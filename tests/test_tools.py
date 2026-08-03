"""Tests for the tool registry and concrete tool implementations.

Part 1 (SPEC section 3.3, tool dispatch): registration by name, dispatch
of an Action to the matching tool with duration measurement and
exception capture, unknown-tool and disabled-tool error results, and
listing/getting tools.

Part 2 (Task 9, concrete tools): each of the 8 tools from the SPEC tool
table (read_file, write_file, search_code, glob_files, run_shell,
run_tests, git_op, package_op) against temp directories, and for the
subprocess-based tools against real child processes.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from codeharness.models import Action, RiskLevel, ToolResult, ToolsConfig
from codeharness.tools.file_ops import ReadFileTool, WriteFileTool
from codeharness.tools.git_ops import GitOpTool
from codeharness.tools.package_ops import PackageOpTool
from codeharness.tools.registry import ToolRegistry
from codeharness.tools.search import GlobFilesTool, SearchCodeTool
from codeharness.tools.shell import RunShellTool
from codeharness.tools.testing_tool import RunTestsTool


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


# ---------------------------------------------------------------------------
# Task 9: concrete tool implementations (SPEC tool table, section 3.3)
# ---------------------------------------------------------------------------


def test_read_file_reads_content(tmp_path):
    """read_file returns the full file content."""
    p = tmp_path / "notes.txt"
    p.write_text("line1\nline2\nline3\n", encoding="utf-8")

    result = ReadFileTool().execute({"path": str(p)})

    assert result.success is True
    assert result.output == "line1\nline2\nline3\n"


def test_read_file_respects_line_range(tmp_path):
    """start_line/end_line select a 1-based inclusive slice."""
    p = tmp_path / "notes.txt"
    p.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")

    result = ReadFileTool().execute(
        {"path": str(p), "start_line": 2, "end_line": 3}
    )

    assert result.success is True
    assert result.output == "line2\nline3"


def test_read_file_start_line_only(tmp_path):
    """Without end_line the slice runs to the end of the file."""
    p = tmp_path / "notes.txt"
    p.write_text("line1\nline2\nline3\n", encoding="utf-8")

    result = ReadFileTool().execute({"path": str(p), "start_line": 2})

    assert result.success is True
    assert result.output == "line2\nline3"


def test_read_file_missing_file_returns_error(tmp_path):
    """Reading a missing file yields a failed ToolResult."""
    result = ReadFileTool().execute({"path": str(tmp_path / "nope.txt")})

    assert result.success is False
    assert "nope.txt" in (result.error or "")


def test_read_file_requires_path():
    """Missing required param is reported, not raised."""
    result = ReadFileTool().execute({})

    assert result.success is False
    assert "path" in (result.error or "")


def test_read_file_low_risk():
    assert ReadFileTool().risk_level == RiskLevel.LOW


def test_read_file_dry_run_does_not_read(tmp_path):
    """dry_run describes the action without touching the filesystem."""
    result = ReadFileTool().dry_run({"path": str(tmp_path / "ghost.txt")})

    assert result.success is True
    assert "ghost.txt" in (result.output or "")


def test_write_file_creates_file(tmp_path):
    """write_file creates a new file with the given content."""
    p = tmp_path / "out.txt"
    tool = WriteFileTool(root=str(tmp_path))

    result = tool.execute({"path": "out.txt", "content": "hello"})

    assert result.success is True
    assert p.read_text(encoding="utf-8") == "hello"


def test_write_file_overwrites_existing(tmp_path):
    """write_file replaces existing content."""
    p = tmp_path / "out.txt"
    p.write_text("old", encoding="utf-8")
    tool = WriteFileTool(root=str(tmp_path))

    result = tool.execute({"path": "out.txt", "content": "new"})

    assert result.success is True
    assert p.read_text(encoding="utf-8") == "new"


def test_write_file_creates_parent_directories(tmp_path):
    """Missing parent directories are created."""
    tool = WriteFileTool(root=str(tmp_path))
    target = tmp_path / "a" / "b" / "deep.txt"

    result = tool.execute({"path": "a/b/deep.txt", "content": "x"})

    assert result.success is True
    assert target.read_text(encoding="utf-8") == "x"


def test_write_file_rejects_path_outside_root(tmp_path):
    """Paths escaping the project root are refused and never written."""
    outside = tmp_path.parent / "escape.txt"
    tool = WriteFileTool(root=str(tmp_path))

    result = tool.execute({"path": str(outside), "content": "evil"})

    assert result.success is False
    assert "outside project root" in (result.error or "")
    assert not outside.exists()


def test_write_file_directory_target_returns_error(tmp_path):
    """Writing to an existing directory is an error, not a crash."""
    target = tmp_path / "adir"
    target.mkdir()
    tool = WriteFileTool(root=str(tmp_path))

    result = tool.execute({"path": "adir", "content": "x"})

    assert result.success is False
    assert result.error  # PermissionError / IsADirectoryError


def test_write_file_medium_risk():
    assert WriteFileTool().risk_level == RiskLevel.MEDIUM


def test_write_file_dry_run_does_not_write(tmp_path):
    """dry_run validates the path but leaves the file system untouched."""
    tool = WriteFileTool(root=str(tmp_path))

    result = tool.dry_run({"path": "out.txt", "content": "hello"})

    assert result.success is True
    assert "out.txt" in (result.output or "")
    assert not (tmp_path / "out.txt").exists()


def test_search_code_finds_matches(tmp_path):
    """search_code returns path:line:content hits for a regex pattern."""
    (tmp_path / "a.py").write_text("def hello():\n    pass\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("other stuff\n", encoding="utf-8")

    result = SearchCodeTool().execute({"pattern": "hello", "path": str(tmp_path)})

    assert result.success is True
    assert "a.py" in (result.output or "")
    assert "hello" in (result.output or "")
    assert "b.txt" not in (result.output or "")


def test_search_code_no_match_returns_empty(tmp_path):
    """A pattern with no hits succeeds with empty output."""
    (tmp_path / "a.py").write_text("def hello():\n", encoding="utf-8")

    result = SearchCodeTool().execute({"pattern": "zzz", "path": str(tmp_path)})

    assert result.success is True
    assert result.output == ""


def test_search_code_missing_path_returns_error(tmp_path):
    """Searching a nonexistent directory is an error."""
    result = SearchCodeTool().execute({"pattern": "x", "path": str(tmp_path / "nope")})

    assert result.success is False
    assert "nope" in (result.error or "")


def test_search_code_glob_filters_files(tmp_path):
    """The glob param restricts which files are searched."""
    (tmp_path / "a.py").write_text("hello\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")

    result = SearchCodeTool().execute(
        {"pattern": "hello", "path": str(tmp_path), "glob": "*.py"}
    )

    assert result.success is True
    assert "a.py" in (result.output or "")
    assert "a.txt" not in (result.output or "")


def test_search_code_low_risk():
    assert SearchCodeTool().risk_level == RiskLevel.LOW


def test_glob_files_matches_pattern(tmp_path, monkeypatch):
    """glob_files returns sorted matching paths relative to the cwd."""
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "c.txt").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = GlobFilesTool().execute({"pattern": "*.py"})

    assert result.success is True
    assert (result.output or "").splitlines() == ["a.py", "b.py"]


def test_glob_files_recursive(tmp_path, monkeypatch):
    """'**' patterns reach nested directories."""
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "sub.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = GlobFilesTool().execute({"pattern": "**/*.py"})

    assert result.success is True
    assert any("sub.py" in line for line in (result.output or "").splitlines())


def test_glob_files_no_match_returns_empty(tmp_path, monkeypatch):
    """A pattern with no matches succeeds with empty output."""
    monkeypatch.chdir(tmp_path)

    result = GlobFilesTool().execute({"pattern": "*.zzz"})

    assert result.success is True
    assert result.output == ""


def test_glob_files_low_risk():
    assert GlobFilesTool().risk_level == RiskLevel.LOW


def test_run_shell_executes_command():
    """run_shell runs the command and captures combined output."""
    result = RunShellTool().execute({"command": "echo hello"})

    assert result.success is True
    assert "hello" in (result.output or "")


def test_run_shell_captures_stderr():
    """Stderr output is folded into the result output."""
    command = 'python -c "import sys; print(\'boom\', file=sys.stderr)"'
    result = RunShellTool().execute({"command": command})

    assert result.success is True
    assert "boom" in (result.output or "")


def test_run_shell_nonzero_exit():
    """A failing command returns success=False with the exit code."""
    result = RunShellTool().execute({"command": "exit 3"})

    assert result.success is False
    assert result.exit_code == 3


def test_run_shell_respects_timeout():
    """Commands that overrun the timeout fail with a timeout error."""
    command = 'python -c "import time; time.sleep(10)"'
    result = RunShellTool().execute({"command": command, "timeout": 1})

    assert result.success is False
    assert "timed out" in (result.error or "")


def test_run_shell_cwd(tmp_path):
    """The cwd param runs the command in another directory."""
    result = RunShellTool().execute(
        {"command": 'python -c "import os; print(os.getcwd())"', "cwd": str(tmp_path)}
    )

    assert result.success is True
    assert str(tmp_path) in (result.output or "")


def test_run_shell_requires_command():
    result = RunShellTool().execute({})

    assert result.success is False
    assert "command" in (result.error or "")


def test_run_shell_medium_risk():
    assert RunShellTool().risk_level == RiskLevel.MEDIUM


def test_run_tests_passing(tmp_path):
    """run_tests runs pytest and reports a passing suite from JUnit XML."""
    (tmp_path / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )

    result = RunTestsTool().execute({"path": str(tmp_path)})

    assert result.success is True
    assert "passed: 1" in (result.output or "")


def test_run_tests_failing(tmp_path):
    """Failing tests yield success=False with the failure summarized."""
    (tmp_path / "test_bad.py").write_text(
        "def test_bad():\n    assert 1 == 2\n", encoding="utf-8"
    )

    result = RunTestsTool().execute({"path": str(tmp_path)})

    assert result.success is False
    assert "failed: 1" in (result.output or "")
    assert "test_bad" in (result.output or "")


def test_run_tests_accepts_flags(tmp_path):
    """Extra pytest flags are passed through."""
    (tmp_path / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )

    result = RunTestsTool().execute({"path": str(tmp_path), "flags": ["-q"]})

    assert result.success is True
    assert "passed: 1" in (result.output or "")


def test_run_tests_missing_path_returns_error(tmp_path):
    """A nonexistent test path is reported as a failure."""
    result = RunTestsTool().execute({"path": str(tmp_path / "nope")})

    assert result.success is False


def test_run_tests_low_risk():
    assert RunTestsTool().risk_level == RiskLevel.LOW


def _git_execute(
    tool: GitOpTool, cwd: Path, operation: str, args: list[str] | None = None
) -> ToolResult:
    return tool.execute(
        {"operation": operation, "args": args or [], "cwd": str(cwd)}
    )


def _init_git_repo(tool: GitOpTool, cwd: Path) -> None:
    """Initialise a git repo with identity so commits can be made."""
    assert _git_execute(tool, cwd, "init").success
    assert _git_execute(tool, cwd, "config", ["user.email", "test@example.com"]).success
    assert _git_execute(tool, cwd, "config", ["user.name", "Test"]).success


def test_git_op_status_shows_untracked(tmp_path):
    """git status lists untracked files."""
    tool = GitOpTool()
    _init_git_repo(tool, tmp_path)
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")

    result = _git_execute(tool, tmp_path, "status")

    assert result.success is True
    assert "a.txt" in (result.output or "")


def test_git_op_commit_creates_commit(tmp_path):
    """git add + commit creates a commit visible in git log."""
    tool = GitOpTool()
    _init_git_repo(tool, tmp_path)
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    assert _git_execute(tool, tmp_path, "add", ["."]).success
    assert _git_execute(tool, tmp_path, "commit", ["-m", "first commit"]).success

    result = _git_execute(tool, tmp_path, "log")

    assert result.success is True
    assert "first commit" in (result.output or "")


def test_git_op_diff_shows_changes(tmp_path):
    """git diff shows uncommitted modifications."""
    tool = GitOpTool()
    _init_git_repo(tool, tmp_path)
    p = tmp_path / "a.txt"
    p.write_text("hello\n", encoding="utf-8")
    assert _git_execute(tool, tmp_path, "add", ["."]).success
    assert _git_execute(tool, tmp_path, "commit", ["-m", "first"]).success
    p.write_text("world\n", encoding="utf-8")

    result = _git_execute(tool, tmp_path, "diff")

    assert result.success is True
    assert "world" in (result.output or "")
    assert "hello" in (result.output or "")


def test_git_op_unknown_operation_fails(tmp_path):
    """An unknown git operation yields a failed ToolResult."""
    result = _git_execute(GitOpTool(), tmp_path, "frobnicate")

    assert result.success is False


def test_git_op_medium_risk():
    """git_op has a MEDIUM base risk; the guard may escalate per operation."""
    assert GitOpTool().risk_level == RiskLevel.MEDIUM


@pytest.fixture(scope="module")
def venv_python(tmp_path_factory) -> Path:
    """A throwaway virtualenv's python executable, for isolated pip tests."""
    venv_dir = tmp_path_factory.mktemp("venv") / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        check=True,
        capture_output=True,
    )
    exe = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    assert exe.exists(), f"venv python not created at {exe}"
    return exe


def _venv_site_packages(venv_python: Path) -> Path:
    venv_dir = venv_python.parent.parent
    if os.name == "nt":
        return venv_dir / "Lib" / "site-packages"
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    return venv_dir / "lib" / version / "site-packages"


def _make_wheel(dir_: Path, name: str = "mypkg", version: str = "0.1.0") -> Path:
    """Build a minimal pure-python wheel offline (no build backend needed)."""
    dist_info = f"{name}-{version}.dist-info"
    files = {
        f"{name}/__init__.py": b"VALUE = 1\n",
        f"{dist_info}/METADATA": (
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        ).encode(),
        f"{dist_info}/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: test-suite\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    rows = []
    for rel, data in files.items():
        digest = base64.urlsafe_b64encode(
            hashlib.sha256(data).digest()
        ).rstrip(b"=").decode()
        rows.append((rel, f"sha256={digest}", str(len(data))))
    rows.append((f"{dist_info}/RECORD", "", ""))
    record = io.StringIO()
    csv.writer(record).writerows(rows)
    files[f"{dist_info}/RECORD"] = record.getvalue().encode()

    wheel = dir_ / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, data in files.items():
            zf.writestr(rel, data)
    return wheel


def test_package_op_list(venv_python):
    """pip list succeeds and lists pip itself."""
    result = PackageOpTool(python=str(venv_python)).execute({"operation": "list"})

    assert result.success is True
    assert "pip" in (result.output or "")


def test_package_op_install_wheel(venv_python, tmp_path):
    """pip install of a local wheel succeeds and lands in site-packages."""
    wheel = _make_wheel(tmp_path)
    site_packages = _venv_site_packages(venv_python)

    result = PackageOpTool(python=str(venv_python)).execute(
        {"operation": "install", "package": str(wheel)}
    )

    assert result.success is True
    assert "Successfully installed" in (result.output or "")
    assert (site_packages / "mypkg").is_dir()
    assert (site_packages / "mypkg-0.1.0.dist-info").is_dir()


def test_package_op_uninstall(venv_python, tmp_path):
    """pip uninstall removes an installed package."""
    wheel = _make_wheel(tmp_path)
    tool = PackageOpTool(python=str(venv_python))
    assert tool.execute({"operation": "install", "package": str(wheel)}).success
    site_packages = _venv_site_packages(venv_python)

    result = tool.execute({"operation": "uninstall", "package": "mypkg"})

    assert result.success is True
    assert "Successfully uninstalled" in (result.output or "")
    assert not (site_packages / "mypkg").exists()
    assert not (site_packages / "mypkg-0.1.0.dist-info").exists()


def test_package_op_install_requires_package(venv_python):
    """install/uninstall without a package name is an error."""
    tool = PackageOpTool(python=str(venv_python))

    result = tool.execute({"operation": "install"})

    assert result.success is False
    assert "package" in (result.error or "")


def test_package_op_unknown_operation(venv_python):
    """Unknown operations are rejected before spawning pip."""
    result = PackageOpTool(python=str(venv_python)).execute({"operation": "frobnicate"})

    assert result.success is False
    assert "frobnicate" in (result.error or "")


def test_package_op_high_risk():
    assert PackageOpTool().risk_level == RiskLevel.HIGH
