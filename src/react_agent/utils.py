"""Utility & helper functions."""

import os

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage


def get_message_text(msg: BaseMessage) -> str:
    """Get the text content of a message."""
    content = msg.content
    if isinstance(content, str):
        return content
    elif isinstance(content, dict):
        return content.get("text", "")
    else:
        txts = [c if isinstance(c, str) else (c.get("text") or "") for c in content]
        return "".join(txts).strip()


def load_chat_model(fully_specified_name: str) -> BaseChatModel:
    """Load a chat model from a fully specified name.

    Args:
        fully_specified_name (str): String in the format 'provider/model'.
    """
    provider, model = fully_specified_name.split("/", maxsplit=1)
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model,
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            temperature=0,
            reasoning=False,
            num_ctx=int(os.environ.get("OLLAMA_NUM_CTX", "4096")),
            num_predict=1024,
            keep_alive="2m",
            client_kwargs={"timeout": 180.0},
        )
    try:
        from langchain.chat_models import init_chat_model
    except ImportError as exc:
        raise ValueError(
            "Cloud providers require the corresponding optional package extra."
        ) from exc
    return init_chat_model(model, model_provider=provider)
