"""Graph routing invariants that do not require a running chat model."""

from react_agent.graph import route_after_tools, route_assessment
from react_agent.state import State


def test_direct_read_continues_to_policy_for_mixed_request() -> None:
    state = State(
        direct_tool_call={"name": "lookup_my_orders", "args": {}},
        needs_policy_search=True,
    )

    assert route_after_tools(state) == "retrieve_policy"


def test_direct_read_finishes_without_policy_request() -> None:
    state = State(
        direct_tool_call={"name": "lookup_order", "args": {"order_id": "ORD-1001"}},
        needs_policy_search=False,
    )

    assert route_after_tools(state) == "finalize_response"


def test_sufficient_mixed_evidence_uses_deterministic_renderer() -> None:
    state = State(
        direct_tool_call={"name": "lookup_my_orders", "args": {}},
        policy_evidence={
            "ok": True,
            "data": {"sufficiency": {"status": "sufficient"}},
            "error": None,
        },
    )

    assert route_assessment(state) == "finalize_response"


def test_insufficient_policy_evidence_fails_closed() -> None:
    state = State(
        policy_evidence={
            "ok": True,
            "data": {"sufficiency": {"status": "partial"}},
            "error": None,
        }
    )

    assert route_assessment(state) == "policy_unavailable"
