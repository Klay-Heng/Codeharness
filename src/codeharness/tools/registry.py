"""Tool registration and dispatch (SPEC section 3.3).

``ToolRegistry`` holds the set of executable tools implementing the
``Tool`` protocol (models.py) and routes ``Action`` objects to them.  It
is a thin, testable layer: lookup by name, disabled-tool filtering from
``ToolsConfig.disabled``, wall-clock duration measurement, and
exception-to-``ToolResult`` conversion.  Guard screening happens before
dispatch (GuardEngine, Task 10), so the registry itself does not judge
whether an action is allowed.

Interface: ``register``, ``dispatch``, ``get_tool``, ``list_available``.
"""
from __future__ import annotations

import time

from codeharness.models import Action, Tool, ToolResult, ToolsConfig


class ToolRegistry:
    """Register tools and dispatch actions to them."""

    def __init__(self, config: ToolsConfig) -> None:
        """Create a registry governed by ``config`` (tool settings)."""
        self.config = config
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Add ``tool`` under its ``name``; re-registering a name replaces it."""
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Tool | None:
        """Return the registered tool named ``name``, or None if unknown."""
        return self._tools.get(name)

    def list_available(self) -> list[dict]:
        """Describe every non-disabled tool with name, description, risk_level, and parameters_schema."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "risk_level": tool.risk_level,
                "parameters_schema": getattr(tool, "parameters_schema", {"type": "object", "properties": {}}),
            }
            for name, tool in self._tools.items()
            if name not in self.config.disabled
        ]

    def dispatch(self, action: Action) -> ToolResult:
        """Execute ``action`` through its tool and return a ``ToolResult``.

        Unknown tools and tools listed in ``config.disabled`` yield a
        failed result without invoking anything.  Otherwise the tool's
        ``execute(params)`` is called; the wall-clock duration is
        measured and stamped on the result (overwriting the tool's own
        value), and any exception raised by the tool is captured and
        returned as ``ToolResult(success=False)`` instead of propagating.
        """
        tool = self._tools.get(action.tool)
        if tool is None:
            return ToolResult(
                action_id=action.action_id,
                success=False,
                error=f"unknown tool: {action.tool}",
            )
        if action.tool in self.config.disabled:
            return ToolResult(
                action_id=action.action_id,
                success=False,
                error=f"tool disabled by config: {action.tool}",
            )
        start = time.perf_counter()
        try:
            result = tool.execute(action.params)
        except Exception as exc:  # noqa: BLE001 - any tool failure becomes a ToolResult (SPEC 3.3)
            duration_ms = int((time.perf_counter() - start) * 1000)
            return ToolResult(
                action_id=action.action_id,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=duration_ms,
            )
        result.action_id = action.action_id
        result.duration_ms = int((time.perf_counter() - start) * 1000)
        return result
