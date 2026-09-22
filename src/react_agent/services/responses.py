"""Validate current-turn evidence and render facts without LLM paraphrasing.

This layer validates output contracts, not authentication or policy semantics.
Only registered tool results linked to this turn's actual tool calls are used.
Free-form model drafts are not displayed alongside business facts or policies.
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from react_agent.security import suspicious_instructions

ORDER_STATUS = {
    "confirmed": "已确认",
    "processing": "处理中",
    "shipped": "已发货",
    "delivered": "已送达",
    "cancelled": "已取消",
}
SHIPMENT_STATUS = {
    "label_created": "标签已创建",
    "in_transit": "运输中",
    "out_for_delivery": "正在派送",
    "delivered": "已送达",
    "exception": "物流异常",
    "returned": "已退回",
}
ERRORS = {
    "read_only": "当前 API 只读，不允许提交申请。",
    "return_intent_required": "请明确提出整单申请；查询不会授权提交。",
    "unsupported_scope": "当前 Demo 不支持部分商品退货，未提交整单申请。",
    "unsafe_record": "记录元数据异常，本轮未提供该记录或执行申请。",
    "invalid_argument": "请求参数无效，请核对订单号或查询信息。",
    "order_selection_required": "请明确要检查或申请的唯一订单号；不会自动选择订单。",
    "order_selection_mismatch": "工具订单号与当前选中订单不一致，未执行检查或申请。",
    "identity_required": "缺少应用提供的客户身份；聊天中填写邮箱不代表认证。",
    "not_found": "没有找到当前客户可访问的记录。",
    "forbidden": "当前会话无权访问该记录。",
    "backend_error": "业务服务暂时不可用，请稍后重试。",
    "data_unavailable": "没有可用物流快照，不能推断实时位置或预计送达时间。",
    "approval_stale": "订单或政策已变化，需要重新发起并确认申请。",
    "approval_expired": "确认已过期，需要重新发起并确认申请。",
    "not_eligible": "当前业务规则不允许提交此申请。",
    "eligibility_unknown": "现有信息不足以判断是否可申请，需要进一步核实。",
    "policy_unavailable": "当前执行政策不可用。",
    "index_missing": "政策索引尚未构建。",
    "index_stale": "政策索引已过期，需要重新构建。",
    "retrieval_unavailable": "政策检索暂时不可用。",
    "reranker_unavailable": "本地重排模型不可用，请准备模型后重试；没有静默使用旧检索。",
}


class Contract(BaseModel):
    """Use strict types and allowlist rendering; extra private fields are ignored."""

    model_config = ConfigDict(strict=True, extra="ignore")


class Envelope(Contract):
    ok: bool
    data: Any = None
    error: dict[str, Any] | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.ok and self.error is not None or not self.ok and not self.error:
            raise ValueError("Inconsistent result envelope")
        return self


class Item(Contract):
    product_name: str = Field(min_length=1, max_length=500)
    sku: str = Field(min_length=1, max_length=100)
    quantity: int = Field(ge=1)


class Order(Contract):
    order_id: str = Field(pattern=r"^ORD-[0-9]{1,20}$")
    status: str
    total_cents: int = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    items: list[Item]
    tracking_number: str | None
    delivered_at: str | None
    delivered_at_source: str | None = None


class Eligibility(Contract):
    order_id: str = Field(pattern=r"^ORD-[0-9]{1,20}$")
    eligible: bool | None
    decision: Literal["eligible_to_apply", "ineligible", "insufficient_information"]
    reason_code: str
    policy_id: str
    policy_version: str
    window_days: int = Field(ge=1, le=3650)
    checked_at: str
    deadline: str | None
    is_demo: bool

    @model_validator(mode="after")
    def consistent(self):
        expected = (
            "insufficient_information"
            if self.eligible is None
            else "eligible_to_apply"
            if self.eligible
            else "ineligible"
        )
        if self.decision != expected or self.eligible is True and self.deadline is None:
            raise ValueError("Inconsistent eligibility")
        checked = datetime.fromisoformat(self.checked_at)
        if checked.utcoffset() is None:
            raise ValueError("Missing check timezone")
        if self.deadline is not None:
            deadline = datetime.fromisoformat(self.deadline)
            if (
                deadline.utcoffset() is None
                or self.eligible is True
                and checked > deadline
            ):
                raise ValueError("Invalid eligibility deadline")
        return self


class ReturnRecord(Contract):
    request_id: str = Field(pattern=r"^RET-[A-F0-9]{32}$")
    order_id: str = Field(pattern=r"^ORD-[0-9]{1,20}$")
    reason: str = Field(min_length=1, max_length=500)
    status: Literal["submitted"]
    return_scope: Literal["whole_order"]
    policy_id: str
    policy_version: str
    created_at: str
    is_demo: bool


class ReturnOutcome(Contract):
    created: bool
    already_exists: bool = False
    request: ReturnRecord | None = None
    decision: Literal["cancelled_by_user"] | None = None
    order_id: str | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.decision:
            if (
                self.created
                or self.already_exists
                or self.request is not None
                or self.order_id is None
            ):
                raise ValueError("Inconsistent cancellation")
        elif self.created == self.already_exists or self.request is None:
            raise ValueError("Inconsistent submission")
        return self


class Policy(Contract):
    policy_id: str
    version: str
    window_days: int = Field(ge=1, le=3650)
    required_status: Literal["delivered"]
    is_demo: bool


class Shipment(Contract):
    tracking_number: str
    carrier: str
    status: str
    current_location: str | None
    updated_at: str
    estimated_delivery_at: str | None
    is_demo: bool


class ChunkReference(Contract):
    chunk_index: int = Field(ge=0)
    chunk_id: str = Field(pattern=r"^[a-f0-9]{64}$")


class Passage(Contract):
    policy_id: str
    title: str
    version: str
    effective_date: str
    section: str
    chunk_index: int | None = Field(default=None, ge=0)
    source_chunks: list[ChunkReference] = Field(default_factory=list)
    source_file: str
    chunk_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["active"]
    text: str = Field(min_length=1, max_length=6000)
    is_demo: bool


def safe_text(value: str) -> str:
    """Escape terminal controls, bidi controls and newlines inside scalar fields."""
    return "".join(
        f"\\u{ord(c):04x}" if unicodedata.category(c).startswith("C") else c
        for c in value
    )


def timestamp(value: str | None) -> str:
    if value is None:
        return "未知"
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError("Timezone required")
    except (ValueError, TypeError):
        return "时间数据无效或缺少时区（待核实）"
    return safe_text(value)


def money(cents: int, currency: str) -> str:
    return f"{Decimal(cents) / Decimal(100):.2f} {currency}"


def render_order(data) -> str:
    order = Order.model_validate(data)
    status = ORDER_STATUS.get(order.status, "未知状态，需核实")
    lines = [
        f"订单：{order.order_id}",
        f"订单状态：{status}（{safe_text(order.status)}）",
        f"订单总额：{money(order.total_cents, order.currency)}",
        "商品：",
    ]
    lines.extend(
        f"- {safe_text(i.product_name)} | SKU：{safe_text(i.sku)} | 数量：{i.quantity}"
        for i in order.items
    )
    lines.extend(
        [
            f"签收时间：{timestamp(order.delivered_at)}",
            f"物流单号：{safe_text(order.tracking_number) if order.tracking_number else '暂无'}",
        ]
    )
    if order.delivered_at_source == "local_demo":
        lines.append("签收时间来源：模拟数据")
    return "\n".join(lines)


def render_eligibility(data) -> str:
    result = Eligibility.model_validate(data)
    decisions = {
        True: "满足当前状态和时间规则，可申请；不代表审核通过",
        False: "不满足当前状态或时间规则",
        None: "信息不足，待核实；不是拒绝",
    }
    reasons = {
        "within_return_window": "在申请期限内",
        "return_window_expired": "申请期限已过",
        "order_not_delivered": "订单尚未送达",
        "delivery_time_missing": "缺少签收时间",
        "delivery_time_invalid": "签收时间无效或缺少时区",
        "delivery_time_in_future": "签收时间异常（未来时间）",
    }
    return "\n".join(
        [
            f"退货资格：{result.order_id}",
            f"检查结论：{decisions[result.eligible]}",
            f"原因：{reasons.get(result.reason_code, '未知原因，需核实')}",
            f"执行政策：{safe_text(result.policy_id)} / {safe_text(result.policy_version)}",
            f"申请窗口：{result.window_days}×24 小时（含截止时刻，不延长到午夜）",
            f"截止时间：{timestamp(result.deadline)}",
            f"检查时间：{timestamp(result.checked_at)}",
            "检查范围：状态和时间；商品类别、卫生封条、缺陷、支付状态等未因此获得审核。",
        ]
    )


def render_return(data) -> str:
    result = ReturnOutcome.model_validate(data)
    if result.decision:
        if not re.fullmatch(r"ORD-[0-9]{1,20}", result.order_id):
            raise ValueError("Invalid cancellation order")
        return f"退货申请：{result.order_id}\n已取消本次提交；本次操作没有创建申请。"
    record = result.request
    return "\n".join(
        [
            f"退货申请：{record.order_id}",
            "提交结果：已创建申请"
            if result.created
            else "提交结果：已有申请，本次没有重复创建",
            f"申请编号：{record.request_id}",
            "申请状态：已提交（submitted），等待后续处理",
            "范围：整单",
            f"原因：{safe_text(record.reason)}",
            f"执行政策：{safe_text(record.policy_id)} / {safe_text(record.policy_version)}",
            f"创建时间：{timestamp(record.created_at)}",
            "此结果不是退货审核通过，不代表已生成寄回标签或已退款。",
        ]
    )


def render_policy(data) -> str:
    policy = Policy.model_validate(data)
    return (
        f"执行政策：{safe_text(policy.policy_id)} / {safe_text(policy.version)}\n"
        f"申请窗口：{policy.window_days}×24 小时（含截止时刻）\n"
        "状态要求：已送达；资格结果以业务检查为准，不代表全部商品条件审核通过。"
    )


def render_shipment(data) -> str:
    shipment = Shipment.model_validate(data)
    return "\n".join(
        [
            f"物流单号：{safe_text(shipment.tracking_number)}",
            f"承运商：{safe_text(shipment.carrier)}",
            f"物流快照状态：{SHIPMENT_STATUS.get(shipment.status, '未知状态，需核实')}（{safe_text(shipment.status)}）",
            f"快照位置：{safe_text(shipment.current_location) if shipment.current_location else '未知'}",
            f"快照更新时间：{timestamp(shipment.updated_at)}",
            f"预计送达时间：{timestamp(shipment.estimated_delivery_at)}",
            (
                "以上是本地模拟快照，不是实时承运商位置或退款结果。"
                if shipment.is_demo
                else "以上是业务系统快照，不是实时承运商位置或退款结果。"
            ),
        ]
    )


def render_passages(data) -> str:
    if not isinstance(data, dict) or not isinstance(data.get("matches"), list):
        raise ValueError("Invalid retrieval data")
    if data.get("assessment_error"):
        if data["assessment_error"] == "unsafe_source_instructions":
            return "检索来源包含异常指令样式文本，已隔离本轮证据；未执行申请，请人工检查知识来源。"
        if data.get("assessment_error") == "invalid_source_selection":
            return "模型引用的来源不属于本轮检索结果，无法确认；没有可用的有效引用。请重试。"
        return "证据充分性检查暂时不可用，不能把检查失败解释成知识库没有答案。请重试。"
    if "question_results" in data:
        lines = []
        for item in data["question_results"]:
            lines.append("问题：" + safe_text(item["question"]))
            result = item["evidence"]
            lines.append(
                render_passages(result["data"])
                if result.get("ok")
                else "该问题的政策检索暂时不可用，无法确认。"
            )
        return "\n\n".join(lines)
    assessment = data.get("sufficiency")
    prefix = ""
    if assessment is not None:
        status = assessment.get("status") if isinstance(assessment, dict) else None
        labels = {
            "sufficient": "已选出候选支持条款；来源已核对，适用性仍需确认，不代表业务批准。",
            "partial": "证据覆盖检查：只有部分依据，未覆盖的问题暂时无法确认。",
            "insufficient": "证据覆盖检查：现有片段不足以回答具体问题，暂时无法确认。",
        }
        if status not in labels:
            raise ValueError("Invalid sufficiency status")
        prefix = labels[status] + "\n"
        if data.get("required_clarifications") == ["hygiene_seal_condition"]:
            prefix += "需要补充：卫生封条是否保持完整？拆封本身不能替代封条状态。\n"
        if status == "insufficient":
            if (
                data.get("assessment_reason")
                == "verified_return_destination_unavailable"
            ):
                return "当前没有经过核实的退货仓库地址或收件联系方式，不能从运费、标签或改址政策推断。暂时无法提供。"
            if data.get("coverage_failures"):
                prefix += "检索结果缺少直接支持关键条件的条款，不能用泛相关政策作结论。"
            return prefix.strip()
    if not data["matches"]:
        return "政策依据：没有足够的检索依据，不能据此确认资格或执行操作。"
    passages = [Passage.model_validate(item) for item in data["matches"][:5]]
    if any(suspicious_instructions(p.text) for p in passages):
        raise ValueError("Unsafe source instructions")
    lines = [prefix + "政策依据（来源原文；相关性不代表适用条件已核实）："]
    if data.get("invalid_source_labels"):
        lines.append("部分引用无效，已保留有效来源；不能据此确认全部问题或执行申请。")
    for p, raw in zip(passages, data["matches"][:5]):
        indices = ", ".join(str(ref.chunk_index) for ref in p.source_chunks)
        locator = (
            f"片段索引 {indices}（从 0 开始）"
            if indices
            else f"片段索引 {p.chunk_index}（从 0 开始）"
            if p.chunk_index is not None
            else safe_text(p.section)
        )
        lines.append(f"来源：{safe_text(p.title)} / {safe_text(p.version)} / {locator}")
        lines.append(
            f"文件：{safe_text(p.source_file)} | 政策：{safe_text(p.policy_id)} | 生效日期：{safe_text(p.effective_date)}"
        )
        # Preserve retrieved chunks verbatim; no truncation or free-form translation.
        lines.extend("  " + safe_text(line) for line in p.text.splitlines())
    lines.append(
        "上述内容不能代替业务校验；未核实的商品条件仍待审核，不能承诺接受退货、免费运费或退款。"
    )
    return "\n".join(lines)


RENDERERS = {
    "lookup_order": render_order,
    "check_return_eligibility": render_eligibility,
    "get_return_policy": render_policy,
    "track_shipment": render_shipment,
    "initiate_return": render_return,
    "search_return_knowledge": render_passages,
}
BUSINESS_TOOLS = set(RENDERERS) | {"lookup_my_orders"}


@dataclass(frozen=True)
class RenderedResponse:
    content: str
    deterministic: bool
    validation_errors: tuple[str, ...]
    requires_clarification: bool = False


def render_response(messages, policy_evidence=None) -> RenderedResponse:
    """Replace a draft only with validated evidence belonging to this user turn."""
    start = next(
        (
            i
            for i in range(len(messages) - 1, -1, -1)
            if isinstance(messages[i], HumanMessage)
        ),
        -1,
    )
    turn = messages[start + 1 :]
    calls, blocks, errors, seen = {}, [], [], set()
    handled = False
    for message in turn:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                calls[call["id"]] = call
        elif isinstance(message, ToolMessage) and message.name in BUSINESS_TOOLS:
            handled = True
            name = message.name
            call = calls.get(message.tool_call_id)
            try:
                if not call or call["name"] != name or message.tool_call_id in seen:
                    raise ValueError("Unlinked or duplicate result")
                seen.add(message.tool_call_id)
                if message.status == "error":
                    blocks.append(f"{name}：工具执行失败，无法确认业务结果。")
                    continue
                if not isinstance(message.content, str):
                    raise ValueError("Invalid tool content")
                envelope = Envelope.model_validate(json.loads(message.content))
                if not envelope.ok:
                    code = envelope.error.get("code")
                    blocks.append(
                        f"{name}：{ERRORS.get(code, '操作失败，无法确认业务结果，请核实后重试。')}"
                    )
                    continue
                data = envelope.data
                if name in (
                    "lookup_order",
                    "check_return_eligibility",
                    "initiate_return",
                ):
                    returned_id = (
                        data.get("order_id") if isinstance(data, dict) else None
                    )
                    if (
                        name == "initiate_return"
                        and isinstance(data, dict)
                        and isinstance(data.get("request"), dict)
                    ):
                        returned_id = data["request"].get("order_id")
                    expected_id = str(call["args"].get("order_id", "")).strip().upper()
                    if returned_id != expected_id:
                        raise ValueError("Order mismatch")
                if name == "track_shipment" and (
                    not isinstance(data, dict)
                    or data.get("tracking_number")
                    != str(call["args"].get("tracking_number", "")).strip().upper()
                ):
                    raise ValueError("Shipment mismatch")
                if name == "lookup_my_orders":
                    if not isinstance(data, list):
                        raise ValueError("Invalid order list")
                    block = (
                        "\n\n".join(render_order(order) for order in data)
                        if data
                        else "当前客户没有可查询的订单。"
                    )
                    if len(data) > 1:
                        block += (
                            "\n\n如需针对某笔订单操作，请明确订单号；不会自动选择订单。"
                        )
                else:
                    block = RENDERERS[name](data)
                blocks.append(block)
            except (ValidationError, ValueError, TypeError, KeyError, AttributeError):
                errors.append(f"{name}:invalid_result")
                blocks.append(
                    f"{name}：返回数据未通过校验，无法可靠展示结果；请核实业务记录，不据此判断成功或失败。"
                )
    for call_id, call in calls.items():
        if call["name"] in BUSINESS_TOOLS and call_id not in seen:
            handled = True
            errors.append(f"{call['name']}:missing_result")
            blocks.append(
                f"{call['name']}：没有收到实际工具结果，无法确认是否完成；请核实业务记录。"
            )
    if policy_evidence is not None:
        handled = True
        try:
            envelope = Envelope.model_validate(policy_evidence)
            if envelope.ok:
                blocks.append(render_passages(envelope.data))
            else:
                blocks.append("政策检索失败，不能核实适用规则。")
        except (ValidationError, ValueError, TypeError, KeyError):
            errors.append("policy:invalid_result")
            blocks.append("政策证据未通过校验，不能据此确认规则或资格。")
    if handled:
        unique = list(dict.fromkeys(blocks))
        return RenderedResponse(
            "\n\n".join(unique) or "没有可核实的业务结果。", True, tuple(errors)
        )
    # Unexecuted native/XML tool attempts must never appear as a user answer.
    draft = (
        messages[-1].content if messages and isinstance(messages[-1], AIMessage) else ""
    )
    if not isinstance(draft, str) or re.search(
        r'<tool\b|"(?:name|tool_calls)"\s*:', draft
    ):
        return RenderedResponse(
            "没有获得实际工具结果，无法确认业务信息。请明确查询内容后重试。",
            True,
            ("unexecuted_tool_text",),
        )
    # Without evidence do not display arbitrary model business claims. Preserve
    # only graph-authored failures, otherwise use a safe clarification message.
    return RenderedResponse(
        "请明确希望查询的订单或政策问题；提交整单退货申请还需要订单号和退货原因，并经过终端确认。",
        True,
        (),
        True,
    )
