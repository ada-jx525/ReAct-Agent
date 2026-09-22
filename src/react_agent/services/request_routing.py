"""Language-agnostic entity extraction and validated post-classification routing."""

import re
from dataclasses import dataclass

ORDER_PATTERN = re.compile(r"(?i)(?<![a-z0-9])ord[\s-]*([0-9]{1,20})(?![a-z0-9])")
TRACKING_PATTERN = re.compile(
    r"(?i)(?<![a-z0-9])((?:FDX|UPS|DHL)-[A-Z0-9-]{4,58})(?![a-z0-9-])"
)
EMAIL_PATTERN = re.compile(
    r"(?i)(?<![a-z0-9._%+-])([a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,63})(?![a-z0-9.-])"
)


@dataclass(frozen=True)
class RequestEntities:
    order_ids: tuple[str, ...]
    tracking_numbers: tuple[str, ...]
    emails: tuple[str, ...]


@dataclass(frozen=True)
class DeterministicRoute:
    tool_name: str = ""
    tool_args: dict | None = None
    response_code: str = ""
    missing_information: str = "none"
    resolved_intent: str = ""


def extract_entities(text: str) -> RequestEntities:
    """Extract stable identifiers only; never infer the user's intent."""
    return RequestEntities(
        order_ids=tuple(
            sorted({f"ORD-{number}" for number in ORDER_PATTERN.findall(text)})
        ),
        tracking_numbers=tuple(
            sorted({value.upper() for value in TRACKING_PATTERN.findall(text)})
        ),
        emails=tuple(
            sorted(
                {
                    value.rstrip("，。！？,.!?").lower()
                    for value in EMAIL_PATTERN.findall(text)
                }
            )
        ),
    )


def validated_route(
    *,
    intent: str,
    entities: RequestEntities,
    trusted_email: str = "",
    selected_order_id: str = "",
) -> DeterministicRoute:
    """Validate a semantic decision against language-independent invariants."""
    trusted = trusted_email.strip().lower()
    explicit_foreign_email = trusted and any(
        email != trusted for email in entities.emails
    )
    # An email written in chat is untrusted input, never a new identity scope.
    # None of this agent's capabilities accepts an arbitrary customer email, so
    # fail closed before routing. Model-produced identity labels are deliberately
    # not part of authorization; only application identity and explicit input are.
    if explicit_foreign_email:
        return DeterministicRoute(response_code="identity_switch_denied")

    if (
        intent in {"eligibility_check", "return_submission"}
        and len(entities.order_ids) > 1
    ):
        return DeterministicRoute(
            response_code="multiple_orders_require_selection",
            missing_information="order_id",
        )

    if intent == "shipment_lookup":
        if len(entities.tracking_numbers) > 1:
            return DeterministicRoute(
                response_code="multiple_shipments_require_selection",
                missing_information="tracking_number",
            )
        if len(entities.tracking_numbers) == 1:
            return DeterministicRoute(
                tool_name="track_shipment",
                tool_args={"tracking_number": entities.tracking_numbers[0]},
            )
        if not entities.order_ids and not selected_order_id:
            return DeterministicRoute(
                response_code="missing_shipment_reference",
                missing_information="tracking_number",
            )

    # A normalized, unique order reference denotes one record, not the
    # customer's collection.  Resolve this harmless read ambiguity in the host
    # instead of asking a small model to repeat the entity decision perfectly.
    if intent == "order_list" and len(entities.order_ids) == 1:
        return DeterministicRoute(
            tool_name="lookup_order",
            tool_args={"order_id": entities.order_ids[0]},
            resolved_intent="order_lookup",
        )

    if intent == "order_list":
        return DeterministicRoute(tool_name="lookup_my_orders", tool_args={})

    if intent == "order_lookup":
        if len(entities.order_ids) > 1:
            return DeterministicRoute(
                response_code="multiple_orders_require_selection",
                missing_information="order_id",
            )
        if len(entities.order_ids) == 1:
            return DeterministicRoute(
                tool_name="lookup_order",
                tool_args={"order_id": entities.order_ids[0]},
            )
        if not selected_order_id:
            return DeterministicRoute(
                response_code="missing_order_reference",
                missing_information="order_id",
            )
    if intent == "unsupported":
        return DeterministicRoute(response_code="unsupported_operation")
    if intent == "greeting":
        return DeterministicRoute(response_code="greeting")
    return DeterministicRoute()


def response_for(code: str) -> str:
    responses = {
        "identity_switch_denied": (
            "客户身份由当前应用会话提供，不能通过聊天中的邮箱或关系声明切换。"
            "我不会查询或展示其他客户的订单；请使用对应客户已验证的会话。"
        ),
        "multiple_orders_require_selection": (
            "请明确选择一个订单号后再检查；我不会静默选择其中一笔，也不会自动提交申请。"
        ),
        "multiple_shipments_require_selection": (
            "请明确选择一个物流单号后再查询；我不会静默选择其中一条记录。"
        ),
        "missing_shipment_reference": (
            "请提供物流单号，或提供唯一订单号以便先查询关联物流；"
            "我不会猜测或编造物流单号。"
        ),
        "missing_order_reference": (
            "请提供唯一订单号；如果不知道订单号，我可以查询当前客户身份下的订单。"
        ),
        "understanding_unavailable": (
            "暂时无法可靠理解这次请求，本轮没有调用业务工具，也没有提交任何申请。"
            "请稍后重试或更明确地描述需要查询的内容。"
        ),
        "unsupported_operation": (
            "当前系统无法执行这项操作，也不会编造地址、面单、预约、退款或处理结果。"
            "我可以解释已有政策，或查询当前客户可访问的订单和物流记录。"
        ),
        "greeting": (
            "你好，我可以查询当前客户的订单和物流、解释退货政策、检查退货资格，"
            "也可以在你确认后提交整单退货申请。"
        ),
    }
    if code not in responses:
        raise ValueError("Unknown deterministic response")
    return responses[code]
