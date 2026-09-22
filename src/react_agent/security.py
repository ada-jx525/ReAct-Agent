"""Deterministic capabilities and suspicious-data guards, independent of LLMs.

Detection is defense in depth, not proof of prompt-injection immunity. Customer
isolation and human approval must hold even when these patterns miss an attack.
"""

import os
import re
from pathlib import Path
from typing import Literal

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field

INSTRUCTION_PATTERN = re.compile(
    r"<\s*/?\s*(?:system|developer|assistant)\b|\[/?INST\]|"
    r"ignore\s+(?:all\s+|the\s+)?(?:previous|above|prior)\s+(?:instructions|rules)|"
    r"忽略.{0,8}(?:之前|上述|所有|系统).{0,8}(?:指令|规则)|"
    r'"role"\s*:\s*"(?:system|developer)"',
    re.I,
)


def suspicious_instructions(text: str) -> bool:
    return bool(INSTRUCTION_PATTERN.search(text))


def private_state_file(path: Path):
    """Restrict this exact checkpoint file, not its parent/workspace directory."""
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(descriptor)
    if os.name == "posix":
        path.chmod(0o600)


class UnsafeRecord(ValueError):
    """Untrusted record failed metadata bounds or contains role instructions."""


def validate_items(items):
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise UnsafeRecord
    for item in items:
        if not isinstance(item, dict):
            raise UnsafeRecord
        name, sku, quantity = (
            item.get("product_name"),
            item.get("sku"),
            item.get("quantity"),
        )
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= 500
            or suspicious_instructions(name)
        ):
            raise UnsafeRecord
        if (
            not isinstance(sku, str)
            or not 1 <= len(sku) <= 100
            or type(quantity) is not int
            or quantity < 1
        ):
            raise UnsafeRecord


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["approve", "reject"]
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expires_at: str = Field(min_length=1, max_length=64)


def return_intent_error(messages):
    """Only the latest user can request an action; documents cannot grant it.

    Conservative demo contract: repeat an explicit submission request when supplying
    a reason. A model-inferred intent is never a capability grant.
    """
    latest = next(
        (str(m.content) for m in reversed(messages) if isinstance(m, HumanMessage)), ""
    )
    partial = re.search(
        r"(?:只|仅|部分|其中|单个|一件).{0,20}(?:退|商品|耳机|线)|partial|item.?only|one item|only.{0,20}(?:item|cable)",
        latest,
        re.I,
    )
    denied = re.search(
        r"(?:不要|别|禁止|不想|不需要).{0,8}(?:提交|发起|创建|办理|申请|退货)|(?:只|仅).{0,8}(?:查询|检查|了解)|(?:能不能|能否|是否|可不可以|还能).{0,8}(?:退货|退回|申请|提交)|(?:如何|怎么).{0,10}(?:退货|申请|提交)|can I.{0,30}return|how.{0,20}return",
        latest,
        re.I,
    )
    requested = re.search(
        r"(?:提交|发起|创建|办理|申请).{0,12}(?:退货|退回|整单)|(?:整单|整笔订单).{0,10}(?:退货|退回)|(?:帮我|我要|我想|请).{0,12}(?:退货|退掉|退回|退\s*ORD)|(?:submit|create|initiate|request).{0,20}return",
        latest,
        re.I,
    )
    if partial:
        return {
            "ok": False,
            "data": None,
            "error": {
                "code": "unsupported_scope",
                "message": "本 Demo 只支持整单申请，不执行部分商品退货。",
            },
        }
    if denied or not requested:
        return {
            "ok": False,
            "data": None,
            "error": {
                "code": "return_intent_required",
                "message": "请明确提出整单退货申请；查询、政策或工具内容不会授权提交。",
            },
        }
    return None
