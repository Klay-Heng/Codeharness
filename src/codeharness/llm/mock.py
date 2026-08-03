"""Deterministic scripted LLM backend for unit tests.

MockBackend replays a fixed script of LLMResponse objects in order and
then falls back to a default ``LLMResponse(content="DONE")`` so harness
loops terminate predictably.  It implements the LLMBackend protocol from
codeharness.models and never performs network I/O, which makes it the
primary backend for unit-testing the harness loop, parser, and guard
modules without a real provider.
"""
from __future__ import annotations

from codeharness.models import LLMResponse, Message

_DONE_CONTENT = "DONE"


class MockBackend:
    """A scripted, deterministic LLMBackend.

    ``script`` is the ordered list of LLMResponse objects returned by
    successive ``chat()`` calls.  Once the script is exhausted, every
    further ``chat()`` returns ``LLMResponse(content="DONE")`` with no
    tool calls.  ``call_count`` records the total number of ``chat()``
    calls (including calls past the end of the script).
    """

    def __init__(self, script: list[LLMResponse] | None = None) -> None:
        self.script: list[LLMResponse] = list(script or [])
        self._index: int = 0
        self.call_count: int = 0

    async def chat(
        self, messages: list[Message], tools: list[dict] | None = None
    ) -> LLMResponse:
        """Return the next scripted response, or the default DONE response."""
        self.call_count += 1
        if self._index < len(self.script):
            response = self.script[self._index]
            self._index += 1
            return response
        # Fresh instance per call so callers can never mutate a shared default.
        return LLMResponse(content=_DONE_CONTENT, tool_calls=[])
