"""Tests for MockBackend, the deterministic scripted LLM backend.

MockBackend (src/codeharness/llm/mock.py) replays a fixed script of
LLMResponse objects so unit tests of the harness loop never hit a real
LLM.  Covers: scripted responses in order, the default DONE response once
the script is exhausted, tool-call responses, call_count tracking,
determinism, and LLMBackend protocol conformance.
"""
from __future__ import annotations

import pytest

from codeharness.llm.mock import MockBackend
from codeharness.models import LLMBackend, LLMResponse, Message

DONE = "DONE"


@pytest.fixture
def messages() -> list[Message]:
    """A minimal conversation to feed into chat()."""
    return [Message(role="user", content="hello")]


@pytest.mark.asyncio
async def test_returns_scripted_responses_in_order(messages):
    script = [
        LLMResponse(content="first"),
        LLMResponse(content="second"),
        LLMResponse(content="third"),
    ]
    backend = MockBackend(script=script)
    assert (await backend.chat(messages)).content == "first"
    assert (await backend.chat(messages)).content == "second"
    assert (await backend.chat(messages)).content == "third"


@pytest.mark.asyncio
async def test_returns_default_done_when_script_exhausted(messages):
    backend = MockBackend(script=[LLMResponse(content="only")])
    assert (await backend.chat(messages)).content == "only"
    response = await backend.chat(messages)
    assert response.content == DONE
    assert response.tool_calls == []


@pytest.mark.asyncio
async def test_returns_default_after_script_exhausted(messages):
    backend = MockBackend()  # no script at all
    for _ in range(3):
        response = await backend.chat(messages)
        assert response.content == DONE
        assert response.tool_calls == []


@pytest.mark.asyncio
async def test_tool_call_response(messages):
    tool_calls = [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "run_tests",
                "arguments": '{"pattern": "test_main.py"}',
            },
        }
    ]
    backend = MockBackend(script=[LLMResponse(content="", tool_calls=tool_calls)])
    response = await backend.chat(messages)
    assert response.content == ""
    assert response.tool_calls == tool_calls


@pytest.mark.asyncio
async def test_call_count_tracking(messages):
    backend = MockBackend(script=[LLMResponse(content="a"), LLMResponse(content="b")])
    assert backend.call_count == 0
    await backend.chat(messages)
    await backend.chat(messages)
    await backend.chat(messages)  # one call past the script end
    assert backend.call_count == 3


@pytest.mark.asyncio
async def test_deterministic_same_input_same_results(messages):
    script = [LLMResponse(content="a"), LLMResponse(content="b")]
    first = MockBackend(script=list(script))
    second = MockBackend(script=list(script))
    for _ in range(2):
        assert await first.chat(messages) == await second.chat(messages)


@pytest.mark.asyncio
async def test_accepts_tools_argument(messages):
    backend = MockBackend()
    tools = [{"type": "function", "function": {"name": "run_tests"}}]
    response = await backend.chat(messages, tools=tools)
    assert response.content == DONE


def test_implements_llmbackend_protocol():
    assert isinstance(MockBackend(), LLMBackend)
