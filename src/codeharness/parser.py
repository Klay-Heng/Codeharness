"""Parse LLM responses into executable Action objects.

ResponseParser converts the ``tool_calls`` of an ``LLMResponse`` into
``Action`` instances for the harness loop.  Each tool call is a dict in
the codebase convention ``{"id": ..., "name": ..., "params": ...}`` (as
produced by the DeepSeek/Mock backends).  Calls whose name is empty or
missing are dropped.

The original ``id`` from the LLM tool call is passed through as the
Action's ``action_id`` so that the assistant ``tool_calls[].id`` and
the tool-result ``tool_call_id`` match exactly (required by
OpenAI-compatible APIs).
"""
from __future__ import annotations

import uuid

from codeharness.models import Action, LLMResponse


class ResponseParser:
    """Convert LLMResponse tool calls into a list of Action objects."""

    def parse(self, response: LLMResponse) -> list[Action]:
        """Map each tool call to an Action, skipping empty tool names.

        The LLM's tool-call ``id`` is preserved as the Action id so the
        assistant(tool_calls) → tool result sequence shares the same id.
        """
        actions: list[Action] = []
        for call in response.tool_calls:
            name = call.get("name")
            if not name:
                continue
            params = call.get("params") or {}
            actions.append(Action(
                tool=name,
                params=params,
                action_id=call.get("id", str(uuid.uuid4())),
            ))
        return actions
