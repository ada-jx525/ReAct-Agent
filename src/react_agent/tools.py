"""Customer-scoped business tools and guarded policy retrieval."""

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.func import task
from langgraph.prebuilt import InjectedState
from langgraph.runtime import get_runtime
from langgraph.types import interrupt
from pydantic import ValidationError

from react_agent.runtime_context import AgentRuntimeContext
from react_agent.security import ApprovalDecision, return_intent_error
from react_agent.services.order_selection import selection_error
from react_agent.services.orders import OrderService
from react_agent.services.return_requests import ReturnRequestService
from react_agent.services.returns import ReturnService
from react_agent.services.shipments import ShipmentService


@tool
async def lookup_order(order_id: str) -> dict[str, Any]:
    """Look up the current customer's order by ID (for example ORD-1001).

    Returns status, items, total_cents, currency and tracking number. Never infer
    delivery dates or return eligibility from this result. Check ok/error first.
    Customer identity is supplied by the application, not by this tool's arguments.
    """
    runtime_context = get_runtime(AgentRuntimeContext).context
    service = OrderService(
        runtime_context.customer_email, runtime_context.orders_db_path
    )
    return await asyncio.to_thread(service.lookup_order, order_id)


@tool
async def lookup_my_orders() -> dict[str, Any]:
    """List all orders for the customer selected by the application session.

    Use for 'my orders' or 'what is my order number'. No email argument is needed:
    the application supplies identity privately through runtime context. On
    success, list the returned order IDs. For multiple orders, ask which order
    the user wants to return, rather than guessing. Check ok/error first.
    """
    runtime_context = get_runtime(AgentRuntimeContext).context
    service = OrderService(
        runtime_context.customer_email, runtime_context.orders_db_path
    )
    return await asyncio.to_thread(service.lookup_my_orders)


@tool
async def track_shipment(tracking_number: str) -> dict[str, Any]:
    """Track the current customer's shipment using its tracking number.

    For an order ID, first call lookup_order to obtain tracking_number. If it is
    null, explain that tracking is not yet available instead of guessing a number.
    Returns carrier, status, location, updated_at, ETA and known events. Check
    ok/error first. Null ETA means unknown. is_demo means synthetic, not live.
    """
    runtime_context = get_runtime(AgentRuntimeContext).context
    service = ShipmentService(
        runtime_context.customer_email, runtime_context.orders_db_path
    )
    return await asyncio.to_thread(service.track_shipment, tracking_number)


@tool
async def get_return_policy() -> dict[str, Any]:
    """Read the active return application policy, conditions and version.

    is_demo indicates practice rules, not a real merchant's policy. This tool does
    not determine a specific order's eligibility; use check_return_eligibility.
    """
    runtime_context = get_runtime(AgentRuntimeContext).context
    service = ReturnService(
        runtime_context.customer_email, runtime_context.orders_db_path
    )
    return await asyncio.to_thread(service.get_return_policy)


@tool
async def check_return_eligibility(
    order_id: str, state: Annotated[Any, InjectedState]
) -> dict[str, Any]:
    """Check the current customer's order against stored status and return window.

    Only the user's explicitly selected order is allowed; never pick an order
    from lookup results. The service reads dates and policy from the database.
    eligible=true means eligible to apply, not refund approval. eligible=null means
    insufficient information; explain the reason rather than guessing. Returns a
    reason code, policy version, deadline and checked_at. Report the complete
    deadline timestamp and timezone, not just a date or end-of-day. It never
    creates a return.
    """
    runtime_context = get_runtime(AgentRuntimeContext).context
    error = selection_error(state, order_id, runtime_context.identity_scope())
    if error:
        return error
    service = ReturnService(
        runtime_context.customer_email, runtime_context.orders_db_path
    )
    return await asyncio.to_thread(service.check_return_eligibility, order_id)


@task
async def prepare_return_draft(email: str, db_path: str, order_id: str, reason: str):
    """Checkpoint the original snapshot/expiry; interrupt replay must not renew it."""
    service = ReturnRequestService(email, db_path)
    return await asyncio.to_thread(service.prepare_return, order_id, reason)


@tool
async def initiate_return(
    order_id: str, reason: str, state: Annotated[Any, InjectedState]
) -> dict[str, Any]:
    """Prepare a whole-order return application and pause for human confirmation.

    Use only when the user asks to submit a return and supplies a reason. For
    partial/item-only returns explain that this demo supports whole orders only.
    Identity and approval are application-controlled, not arguments for the LLM.
    The application displays the draft and collects confirmation before any write.
    created=true means an application was submitted, never a refund approved.
    If cancelled, do not retry unless the user explicitly asks again.
    """
    context = get_runtime(AgentRuntimeContext).context
    if context.read_only:
        return {
            "ok": False,
            "data": None,
            "error": {"code": "read_only", "message": "当前服务不允许提交申请。"},
        }
    error = selection_error(state, order_id, context.identity_scope())
    if error:
        return error
    history = (
        state.get("messages", [])
        if isinstance(state, dict)
        else getattr(state, "messages", [])
    )
    error = return_intent_error(history)
    if error:
        return error
    saved_scope = (
        state.get("customer_scope", "")
        if isinstance(state, dict)
        else getattr(state, "customer_scope", "")
    )
    if saved_scope != context.identity_scope():
        return {
            "ok": False,
            "data": None,
            "error": {
                "code": "forbidden",
                "message": "Conversation identity does not match.",
            },
        }
    evidence = (
        state.get("policy_evidence")
        if isinstance(state, dict)
        else getattr(state, "policy_evidence", None)
    )
    if evidence is not None:
        data = evidence.get("data") or {}
        if (
            evidence.get("ok") is not True
            or (data.get("sufficiency") or {}).get("status") != "sufficient"
        ):
            return {
                "ok": False,
                "data": None,
                "error": {
                    "code": "policy_unavailable",
                    "message": "本轮政策证据未充分核实，未准备或提交申请。",
                },
            }
    service = ReturnRequestService(context.customer_email, context.orders_db_path)
    prepared = await prepare_return_draft(
        context.customer_email, str(context.orders_db_path), order_id, reason
    )
    if not prepared["ok"] or prepared["data"].get("already_exists"):
        return prepared
    draft = prepared["data"]
    payload = {
        key: draft[key]
        for key in (
            "action",
            "order_id",
            "reason",
            "return_scope",
            "items",
            "policy_id",
            "policy_version",
            "deadline",
            "fingerprint",
            "expires_at",
            "is_demo",
        )
    }
    # GraphInterrupt must propagate to LangGraph, not be swallowed by an error handler.
    approval = interrupt(payload)
    try:
        approval = ApprovalDecision.model_validate(approval).model_dump()
    except ValidationError:
        return {
            "ok": False,
            "data": None,
            "error": {
                "code": "invalid_argument",
                "message": "无效的确认参数，未提交申请。",
            },
        }
    if approval["decision"] != "approve":
        return {
            "ok": True,
            "data": {
                "decision": "cancelled_by_user",
                "created": False,
                "order_id": draft["order_id"],
                "is_demo": draft["is_demo"],
            },
            "error": None,
        }
    if (
        approval["fingerprint"] != draft["fingerprint"]
        or approval["expires_at"] != draft["expires_at"]
    ):
        return {
            "ok": False,
            "data": None,
            "error": {
                "code": "approval_stale",
                "message": "Application details changed; confirm again.",
            },
        }
    try:
        expiry = datetime.fromisoformat(draft["expires_at"])
        valid = expiry.tzinfo is not None and datetime.now(UTC) <= expiry
    except (ValueError, TypeError, KeyError):
        valid = False
    if not valid:
        return {
            "ok": False,
            "data": None,
            "error": {
                "code": "approval_expired",
                "message": "Confirmation expired; prepare a new application.",
            },
        }
    return await asyncio.to_thread(
        service.submit_return, order_id, reason, approval["fingerprint"]
    )


TOOLS = [
    lookup_order,
    lookup_my_orders,
    track_shipment,
    get_return_policy,
    check_return_eligibility,
    initiate_return,
]
