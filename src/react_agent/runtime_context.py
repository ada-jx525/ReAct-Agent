"""Define application-supplied identity and configuration for an Agent run."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Annotated

from . import prompts
from .data.db import DEFAULT_DB_PATH


@dataclass(kw_only=True)
class AgentRuntimeContext:
    """Runtime inputs for nodes and tools, not the LLM's message context.

    Fields are not automatically added to the model prompt. Customer identity
    must come from the host application; it is not authenticated by this class.
    """

    # Trusted application configuration, never parameters selected by the LLM.
    # In production populate customer_email from a verified login session.
    customer_email: str = ""
    # Host-enforced capability; never granted by a model or HTTP request.
    read_only: bool = False
    orders_db_path: str = str(DEFAULT_DB_PATH)
    checkpoint_db_path: str = str(DEFAULT_DB_PATH.parent / "agent_checkpoints.db")

    def identity_scope(self) -> str:
        """Bind a conversation/approval to customer and database without exposing PII."""
        value = [
            self.customer_email.strip().lower(),
            str(Path(self.orders_db_path).resolve()),
        ]
        return hashlib.sha256(json.dumps(value).encode()).hexdigest()

    system_prompt: str = field(
        default=prompts.SYSTEM_PROMPT,
        metadata={
            "description": "The system prompt to use for the agent's interactions. "
            "This prompt sets the context and behavior for the agent."
        },
    )

    model: Annotated[str, {"__template_metadata__": {"kind": "llm"}}] = field(
        default="ollama/qwen3:1.7b",
        metadata={
            "description": "The name of the language model to use for the agent's main interactions. "
            "Should be in the form: provider/model-name."
        },
    )

    def __post_init__(self) -> None:
        """Fetch env vars for attributes that were not passed as args."""
        for f in fields(self):
            if not f.init:
                continue

            if f.name == "read_only":
                continue
            if getattr(self, f.name) == f.default:
                setattr(self, f.name, os.environ.get(f.name.upper(), f.default))
