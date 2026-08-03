"""Tests for the tool registry (src/codeharness/tools/registry.py).

Covers SPEC section 3.3 (tool dispatch): registration by name, dispatch
of an Action to the matching tool with duration measurement and
exception capture, unknown-tool and disabled-tool error results, and
listing/getting tools.  Task 9 (concrete tools) will extend this file
with tests for the individual Tool implementations.
"""
from __future__ import annotations

import time

from codeharness.models import Action, RiskLevel, ToolResult, ToolsConfig
from codeharness.tools.registry import ToolRegistry


class _FakeTool:
    """Minimal Tool-protocol implementation for registry tests."""

    def __init__(
        self,
        name: str = "read_file",
        description: str = "Fake tool for tests",
        risk_level: RiskLevel = RiskLevel.LOW,
        output: str = "ok",
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.name = name
        self.description = description
        self.risk_level = risk_level
        self._output = output
        self._error = error
        self._delay = delay
        self.calls: list[dict] = []

    def execute(self, params: dict) -> ToolResult:
        self.calls.append(params)
        if self._delay:
            time.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return ToolResult(action_id="", success=True, output=self._output)

    def dry_run(self, params: dict) -> ToolResult:
        return ToolResult(action_id="", success=True, output="dry-run")


def _make_registry(disabled: list[str] | None = None) -> ToolRegistry:
    return ToolRegistry(ToolsConfig(disabled=disabled or []))


def test_register_and_dispatch(tmp_path):
    """A registered tool is dispatched to with the action's params."""
    tool = _FakeTool()
    registry = _make_registry()
    registry.register(tool)

    action = Action(tool="read_file", params={"path": str(tmp_path / "a.txt")})
    result = registry.dispatch(action)

    assert result.success is True
    assert result.output == "ok"
    assert result.error is None
    # The registry stamps the action id and duration on the result.
    assert result.action_id == action.action_id
    assert result.duration_ms >= 0
    # Params were passed through to the tool untouched.
    assert tool.calls == [{"path": str(tmp_path / "a.txt")}]


def test_dispatch_measures_duration():
    """Wall-clock execution time is recorded in duration_ms."""
    tool = _FakeTool(delay=0.02)
    registry = _make_registry()
    registry.register(tool)

    result = registry.dispatch(Action(tool="read_file", params={}))

    assert result.success is True
    assert result.duration_ms > 0


def test_dispatch_unknown_tool_returns_error():
    """Dispatching an unregistered tool yields a failed ToolResult."""
    registry = _make_registry()
    action = Action(tool="does_not_exist", params={})

    result = registry.dispatch(action)

    assert result.success is False
    assert "does_not_exist" in (result.error or "")
    assert result.action_id == action.action_id  # error result still carries the action id


def test_dispatch_disabled_tool_returns_error():
    """Dispatching a tool in config.tools.disabled fails and never executes it."""
    tool = _FakeTool(name="run_shell")
    registry = _make_registry(disabled=["run_shell"])
    registry.register(tool)

    result = registry.dispatch(Action(tool="run_shell", params={"command": "ls"}))

    assert result.success is False
    assert "run_shell" in (result.error or "")
    assert tool.calls == []  # execute must not be invoked


def test_dispatch_catches_execution_exceptions():
    """An exception raised by tool.execute becomes a failed ToolResult."""
    tool = _FakeTool(error=RuntimeError("boom"))
    registry = _make_registry()
    registry.register(tool)

    result = registry.dispatch(Action(tool="read_file", params={}))

    assert result.success is False
    assert "boom" in (result.error or "")
    assert result.action_id != ""
    assert result.duration_ms >= 0


def test_get_tool_finds_registered_tool():
    """get_tool returns the registered tool instance by name."""
    tool = _FakeTool(name="search_code")
    registry = _make_registry()
    registry.register(tool)

    assert registry.get_tool("search_code") is tool


def test_get_tool_returns_none_for_unknown():
    """get_tool returns None when the name is not registered."""
    registry = _make_registry()

    assert registry.get_tool("missing") is None


def test_register_overwrites_same_name():
    """Registering a second tool under an existing name replaces it."""
    first = _FakeTool(name="read_file", description="first")
    second = _FakeTool(name="read_file", description="second")
    registry = _make_registry()
    registry.register(first)
    registry.register(second)

    assert registry.get_tool("read_file") is second


def test_list_available_returns_tool_info():
    """list_available describes each registered tool as a dict."""
    tool = _FakeTool(name="glob_files", description="Match file patterns")
    registry = _make_registry()
    registry.register(tool)

    listing = registry.list_available()

    assert listing == [
        {
            "name": "glob_files",
            "description": "Match file patterns",
            "risk_level": RiskLevel.LOW,
        }
    ]


def test_list_available_skips_disabled_tools():
    """Tools listed in config.tools.disabled are hidden from list_available."""
    read_tool = _FakeTool(name="read_file")
    shell_tool = _FakeTool(name="run_shell", risk_level=RiskLevel.MEDIUM)
    registry = _make_registry(disabled=["run_shell"])
    registry.register(read_tool)
    registry.register(shell_tool)

    names = [entry["name"] for entry in registry.list_available()]

    assert names == ["read_file"]
