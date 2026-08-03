"""Shell command tool (SPEC tool table, section 3.3).

``run_shell`` (MEDIUM): execute a shell command via ``subprocess.run``
with a default 60s timeout (SPEC edge case), capturing stdout and
stderr.  The guard engine (Task 10) screens the command *before*
dispatch; this tool only executes.
"""
from __future__ import annotations

import subprocess

from codeharness.models import RiskLevel, ToolResult


class RunShellTool:
    """Execute a shell command with a timeout (default 60s)."""

    name = "run_shell"
    description = (
        "Execute a shell command; params: command, optional cwd, "
        "optional timeout in seconds (default 60)."
    )
    risk_level = RiskLevel.MEDIUM

    def __init__(self, default_timeout: int = 60) -> None:
        self.default_timeout = default_timeout

    def execute(self, params: dict) -> ToolResult:
        command = params.get("command")
        if not command:
            return ToolResult(
                action_id="", success=False, error="missing required param: command"
            )
        cwd = params.get("cwd")
        try:
            timeout = int(params.get("timeout") or self.default_timeout)
        except (TypeError, ValueError):
            return ToolResult(
                action_id="", success=False, error="timeout must be an integer"
            )
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                action_id="",
                success=False,
                error=f"command timed out after {timeout}s",
            )
        combined = (proc.stdout + proc.stderr).strip()
        if proc.returncode == 0:
            return ToolResult(
                action_id="", success=True, output=combined, exit_code=0
            )
        return ToolResult(
            action_id="",
            success=False,
            output=combined,
            error=proc.stderr.strip()
            or proc.stdout.strip()
            or f"command failed (rc={proc.returncode})",
            exit_code=proc.returncode,
        )

    def dry_run(self, params: dict) -> ToolResult:
        command = params.get("command", "?")
        return ToolResult(
            action_id="",
            success=True,
            output=f"[dry-run] run_shell command={command}",
        )
