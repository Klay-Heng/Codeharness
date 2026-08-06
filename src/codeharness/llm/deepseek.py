"""DeepSeek LLM backend via the OpenAI SDK (SPEC 3.2, Task 15).

DeepSeek serves an OpenAI-compatible chat-completions API, so
:class:`DeepSeekBackend` wraps ``openai.AsyncOpenAI`` pointed at
``LLMConfig.api_base``.  It implements the ``LLMBackend`` protocol from
codeharness.models (async ``chat``), which the AgentLoop calls every
round:

- Conversation messages are re-serialized into OpenAI API dicts
  (``role``/``content``, plus ``tool_call_id`` on tool results).
- Tool definitions from the registry (``{name, description,
  risk_level}``) are converted into OpenAI function-calling shape with
  an open ``parameters`` object schema.
- Response tool calls are decoded into the codebase convention
  ``{"name": ..., "params": {json-decoded arguments}}`` (the
  ``ResponseParser`` consumes exactly this shape).  Malformed or
  non-object arguments degrade to empty params so a bad tool call
  never crashes the loop.

The client is created once at construction with the key from the
credential store; a missing key raises immediately with a setup hint
rather than failing on the first network call.
"""
from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from codeharness.models import LLMConfig, LLMResponse, Message


class DeepSeekBackend:
    """Async LLM backend speaking DeepSeek's OpenAI-compatible API."""

    def __init__(self, config: LLMConfig, credential_store) -> None:
        """Create the OpenAI client for ``config`` using the stored key.

        Args:
            config: LLM settings (provider, model, api_base, limits).
            credential_store: Anything exposing ``get_key()`` (the
                CredentialStore or a test double).
        """
        api_key = credential_store.get_key()
        if not api_key:
            raise ValueError(
                "No DeepSeek API key configured. Run 'codeharness setup' first."
            )
        self.client = AsyncOpenAI(api_key=api_key, base_url=config.api_base)
        self.model = config.model
        self.max_tokens = config.max_tokens
        self.temperature = config.temperature

    async def chat(
        self, messages: list[Message], tools: list[dict] | None = None
    ) -> LLMResponse:
        """Send the conversation and return the model's response."""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[self._format_message(m) for m in messages],
                tools=self._format_tools(tools) if tools else None,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
        except Exception as exc:
            detail_parts = [f"Error: {exc}"]
            body = getattr(exc, "body", None)
            if body is not None:
                detail_parts.append(f"Response: {body}")
            message = getattr(exc, "message", None)
            if message and message != str(exc):
                detail_parts.append(f"Message: {message}")
            # Include the first formatted tool to help debug schema issues.
            if tools:
                formatted = self._format_tools(tools)
                detail_parts.append(
                    "First tool sent: "
                    + json.dumps(formatted[0], indent=2, ensure_ascii=False)
                )
            raise RuntimeError(
                "\n".join(detail_parts)
                + f"\nModel: {self.model}, Messages: {len(messages)}, "
                f"Tools: {len(tools) if tools else 0}"
            ) from exc
        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            tool_calls=self._decode_tool_calls(choice.message.tool_calls),
            finish_reason=choice.finish_reason or "stop",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_message(message: Message) -> dict[str, Any]:
        """Serialize one harness Message into an OpenAI API message dict.

        Tool results must carry the id of the assistant tool call they
        answer (``tool_call_id``); assistant messages that called tools
        must carry ``tool_calls`` so the API accepts subsequent tool
        results.
        """
        formatted: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_call_id:
            formatted["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            formatted["tool_calls"] = message.tool_calls
        return formatted

    @staticmethod
    def _format_tools(tools: list[dict]) -> list[dict[str, Any]]:
        """Convert registry tool descriptions to OpenAI function-calling format.

        Returns a deep copy of each tool's ``parameters_schema`` with
        empty ``required`` lists stripped — some providers reject them.
        """
        formatted: list[dict[str, Any]] = []
        for tool in tools:
            schema = tool.get(
                "parameters_schema",
                {"type": "object", "properties": {}},
            )
            # Deep-copy so we never mutate the shared ClassVar.
            params: dict[str, Any] = {
                "type": schema.get("type", "object"),
                "properties": dict(schema.get("properties", {})),
            }
            required = schema.get("required")
            if required:  # omit empty [] — some APIs reject it
                params["required"] = list(required)
            formatted.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": params,
                },
            })
        return formatted

    @staticmethod
    def _decode_tool_calls(tool_calls: list | None) -> list[dict[str, Any]]:
        """Decode OpenAI tool calls into ``{name, params}`` dicts.

        ``function.arguments`` is a JSON string; it is parsed with
        ``json.loads`` into the params dict.  Unparseable, absent, or
        non-object arguments degrade to ``{}`` so the loop never sees a
        malformed tool call.
        """
        if not tool_calls:
            return []
        decoded: list[dict[str, Any]] = []
        for call in tool_calls:
            params: dict[str, Any] = {}
            arguments = call.function.arguments
            if arguments:
                try:
                    parsed = json.loads(arguments)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    params = parsed
            decoded.append({
                "id": getattr(call, "id", f"call_{len(decoded)}"),
                "name": call.function.name,
                "params": params,
            })
        return decoded
