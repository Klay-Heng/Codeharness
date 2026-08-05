"""Tests for the guard engine (src/codeharness/guard.py).

Covers SPEC section 3.4: the three-tier risk mapping (LOW -> ALLOW,
MEDIUM -> ASK_ONCE, HIGH -> ASK_ALWAYS), dangerous-pattern matching on
command params (with per-operation synthesis for git_op), path-boundary
checks for write_file/run_shell, unknown-tool handling, and SessionState
ASK_ONCE approval memory (including the session_approval toggle).
"""
from __future__ import annotations

import pytest

from codeharness.guard import GuardEngine, SessionState
from codeharness.models import Action, GuardConfig, GuardVerdict, ToolsConfig
from codeharness.tools.file_ops import ReadFileTool, WriteFileTool
from codeharness.tools.git_ops import GitOpTool
from codeharness.tools.package_ops import PackageOpTool
from codeharness.tools.registry import ToolRegistry
from codeharness.tools.search import GlobFilesTool, SearchCodeTool
from codeharness.tools.shell import RunShellTool
from codeharness.tools.testing_tool import RunTestsTool


def _make_guard(tmp_path, **guard_kwargs) -> tuple[GuardEngine, ToolRegistry]:
    """Build a guard over a registry with all 8 tools, rooted at tmp_path."""
    root = str(tmp_path)
    registry = ToolRegistry(ToolsConfig())
    registry.register(ReadFileTool())
    registry.register(WriteFileTool(root=root))
    registry.register(SearchCodeTool())
    registry.register(GlobFilesTool())
    registry.register(RunShellTool())
    registry.register(RunTestsTool())
    registry.register(GitOpTool())
    registry.register(PackageOpTool())
    config = GuardConfig(project_root=root, **guard_kwargs)
    return GuardEngine(config, registry), registry


def test_blocks_rm_rf(tmp_path):
    """'rm -rf /' matches a dangerous pattern -> ASK_ALWAYS."""
    guard, _ = _make_guard(tmp_path)
    action = Action(tool="run_shell", params={"command": "rm -rf /"})

    assert guard.check(action) == GuardVerdict.ASK_ALWAYS


def test_allows_safe_read(tmp_path):
    """read_file (LOW) inside the project is allowed without asking."""
    guard, _ = _make_guard(tmp_path)
    action = Action(tool="read_file", params={"path": str(tmp_path / "a.py")})

    assert guard.check(action) == GuardVerdict.ALLOW


def test_write_inside_project(tmp_path):
    """write_file (MEDIUM) inside the project -> ASK_ONCE."""
    guard, _ = _make_guard(tmp_path)
    action = Action(
        tool="write_file",
        params={"path": str(tmp_path / "out.txt"), "content": "hello"},
    )

    assert guard.check(action) == GuardVerdict.ASK_ONCE


def test_write_outside_project(tmp_path):
    """write_file escaping the project root -> ASK_ALWAYS."""
    guard, _ = _make_guard(tmp_path)
    outside = tmp_path.parent / "escape.txt"
    action = Action(tool="write_file", params={"path": str(outside), "content": "x"})

    assert guard.check(action) == GuardVerdict.ASK_ALWAYS


def test_git_status_allowed(tmp_path):
    """git_op status (read-only op) is LOW -> ALLOW."""
    guard, _ = _make_guard(tmp_path)
    action = Action(tool="git_op", params={"operation": "status"})

    assert guard.check(action) == GuardVerdict.ALLOW


def test_git_diff_allowed(tmp_path):
    """git_op diff (read-only op) is LOW -> ALLOW."""
    guard, _ = _make_guard(tmp_path)
    action = Action(tool="git_op", params={"operation": "diff"})

    assert guard.check(action) == GuardVerdict.ALLOW


def test_git_push_force_blocked(tmp_path):
    """git push --force matches a dangerous pattern -> ASK_ALWAYS."""
    guard, _ = _make_guard(tmp_path)
    action = Action(tool="git_op", params={"operation": "push", "args": ["--force"]})

    assert guard.check(action) == GuardVerdict.ASK_ALWAYS


def test_git_reset_hard_blocked(tmp_path):
    """git reset --hard matches a dangerous pattern -> ASK_ALWAYS."""
    guard, _ = _make_guard(tmp_path)
    action = Action(tool="git_op", params={"operation": "reset", "args": ["--hard"]})

    assert guard.check(action) == GuardVerdict.ASK_ALWAYS


def test_package_op_blocked(tmp_path):
    """package_op is HIGH -> ASK_ALWAYS."""
    guard, _ = _make_guard(tmp_path)
    action = Action(tool="package_op", params={"operation": "install", "package": "x"})

    assert guard.check(action) == GuardVerdict.ASK_ALWAYS


def test_sudo_blocked(tmp_path):
    """sudo matches a dangerous pattern -> ASK_ALWAYS."""
    guard, _ = _make_guard(tmp_path)
    action = Action(tool="run_shell", params={"command": "sudo apt-get update"})

    assert guard.check(action) == GuardVerdict.ASK_ALWAYS


def test_chmod_777_blocked(tmp_path):
    """chmod 777 matches a dangerous pattern -> ASK_ALWAYS."""
    guard, _ = _make_guard(tmp_path)
    action = Action(tool="run_shell", params={"command": "chmod 777 /etc/passwd"})

    assert guard.check(action) == GuardVerdict.ASK_ALWAYS


def test_unknown_tool_blocked(tmp_path):
    """An unregistered tool is never allowed -> ASK_ALWAYS."""
    guard, _ = _make_guard(tmp_path)
    action = Action(tool="not_a_tool", params={})

    assert guard.check(action) == GuardVerdict.ASK_ALWAYS


@pytest.mark.parametrize(
    "command",
    [
        "rm -fr /",
        "rm -r -f /",
        "rm -f -r /",
        "rm --recursive --force /",
        "rm --force --recursive /",
    ],
)
def test_rm_flag_variants_blocked(tmp_path, command):
    """rm with reordered, split, or long flags still matches a pattern."""
    guard, _ = _make_guard(tmp_path)
    action = Action(tool="run_shell", params={"command": command})

    assert guard.check(action) == GuardVerdict.ASK_ALWAYS


@pytest.mark.parametrize(
    "command",
    ["chmod -R 777 /etc", "chmod 0777 /etc/passwd", "chmod -R 0777 /tmp/x"],
)
def test_chmod_flag_variants_blocked(tmp_path, command):
    """chmod with a -R flag or leading-zero octal is still blocked."""
    guard, _ = _make_guard(tmp_path)
    action = Action(tool="run_shell", params={"command": command})

    assert guard.check(action) == GuardVerdict.ASK_ALWAYS


def test_run_shell_without_cwd_blocked(tmp_path):
    """run_shell without cwd defaults to project root (tool level) -> ASK_ONCE."""
    guard, _ = _make_guard(tmp_path)
    action = Action(tool="run_shell", params={"command": "ls"})

    assert guard.check(action) == GuardVerdict.ASK_ONCE


def test_run_shell_cwd_outside_project_blocked(tmp_path):
    """run_shell whose cwd escapes the project root -> ASK_ALWAYS."""
    guard, _ = _make_guard(tmp_path)
    action = Action(tool="run_shell", params={"command": "ls", "cwd": str(tmp_path.parent)})

    assert guard.check(action) == GuardVerdict.ASK_ALWAYS


def test_run_shell_cwd_inside_project_ask_once(tmp_path):
    """run_shell with a cwd inside the project keeps MEDIUM -> ASK_ONCE."""
    guard, _ = _make_guard(tmp_path)
    action = Action(
        tool="run_shell", params={"command": "ls", "cwd": str(tmp_path / "sub")}
    )

    assert guard.check(action) == GuardVerdict.ASK_ONCE


def test_session_approval(tmp_path):
    """ASK_ONCE -> approve -> the same tool is ALLOWed for the session."""
    guard, _ = _make_guard(tmp_path)
    action = Action(
        tool="write_file",
        params={"path": str(tmp_path / "out.txt"), "content": "hello"},
    )

    assert guard.check(action) == GuardVerdict.ASK_ONCE
    guard.session.approve("write_file")
    assert guard.check(action) == GuardVerdict.ALLOW


def test_session_approval_disabled(tmp_path):
    """With session_approval=False, ASK_ONCE stays ASK_ONCE after approval."""
    guard, _ = _make_guard(tmp_path, session_approval=False)
    action = Action(
        tool="write_file",
        params={"path": str(tmp_path / "out.txt"), "content": "hello"},
    )

    guard.session.approve("write_file")
    assert guard.check(action) == GuardVerdict.ASK_ONCE


def test_session_approval_does_not_bypass_patterns(tmp_path):
    """A session-approved tool is still blocked by dangerous patterns."""
    guard, _ = _make_guard(tmp_path)
    action = Action(tool="run_shell", params={"command": "rm -rf /"})

    guard.session.approve("run_shell")
    assert guard.check(action) == GuardVerdict.ASK_ALWAYS


def test_extra_dangerous_patterns_are_compiled(tmp_path):
    """Config-supplied extra patterns are compiled and matched."""
    guard, _ = _make_guard(tmp_path, extra_dangerous_patterns=[r"format\s+-y"])
    action = Action(tool="run_shell", params={"command": "format -y /dev/sda"})

    assert guard.check(action) == GuardVerdict.ASK_ALWAYS


def test_session_state_tracks_approvals():
    """SessionState records approved tool names."""
    session = SessionState()

    assert session.is_approved("write_file") is False
    session.approve("write_file")
    assert session.is_approved("write_file") is True
    assert session.is_approved("run_shell") is False
