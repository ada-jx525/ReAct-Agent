import os

from react_agent.runtime_context import AgentRuntimeContext


def test_context_init() -> None:
    context = AgentRuntimeContext(model="openai/gpt-4o-mini")
    assert context.model == "openai/gpt-4o-mini"


def test_context_init_with_env_vars() -> None:
    os.environ["MODEL"] = "openai/gpt-4o-mini"
    context = AgentRuntimeContext()
    assert context.model == "openai/gpt-4o-mini"


def test_context_init_with_env_vars_and_passed_values() -> None:
    os.environ["MODEL"] = "openai/gpt-4o-mini"
    context = AgentRuntimeContext(model="openai/gpt-5o-mini")
    assert context.model == "openai/gpt-5o-mini"
