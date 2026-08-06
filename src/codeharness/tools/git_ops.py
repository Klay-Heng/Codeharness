"""Git operations tool (SPEC tool table, section 3.3).

``git_op`` (risk VARIES per operation; MEDIUM base): run a git
operation via ``subprocess.run``.  The Tool protocol requires a single
``risk_level``, so the tool carries a MEDIUM base and the guard engine
(Task 10) is expected to escalate per operation (e.g. destructive
commands already covered by ``DEFAULT_DANGEROUS_PATTERNS``).
"""
from __future__ import annotations

import shlex
import subprocess
from typing import ClassVar

from codeharness.models import RiskLevel, ToolResult


class GitOpTool:
    """Run git operations (operation + optional args, optional cwd)."""

    name = "git_op"
    description = (
        "Run git operations; params: operation (e.g. status, diff, log, "
        "commit), optional args (list or space-separated string), "
        "optional cwd."
    )
    # VARIES per operation (SPEC table); MEDIUM is the protocol-fixed base.
    risk_level = RiskLevel.MEDIUM
    parameters_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "description": "Git operation: status, diff, log, commit, add, branch, checkout, etc.",
                "enum": ["status", "diff", "log", "commit", "add", "branch", "checkout", "show", "ls-files"],
            },
            "args": {
                "type": "string",
                "description": "Additional arguments as space-separated string.",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory for the git command.",
            },
        },
        "required": ["operation"],
    }

    def __init__(self, default_timeout: int = 60) -> None:
        self.default_timeout = default_timeout

    def execute(self, params: dict) -> ToolResult:
        operation = params.get("operation")
        if not operation:
            return ToolResult(
                action_id="",
                success=False,
                error="missing required param: operation",
            )
        args = params.get("args") or []
        if isinstance(args, str):
            args = shlex.split(args)
        cmd = ["git", str(operation), *(str(a) for a in args)]
        try:
            proc = subprocess.run(
                cmd,
                cwd=params.get("cwd"),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.default_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                action_id="",
                success=False,
                error=f"git {operation} timed out after {self.default_timeout}s",
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
            or f"git {operation} failed (rc={proc.returncode})",
            exit_code=proc.returncode,
        )

    def dry_run(self, params: dict) -> ToolResult:
        operation = params.get("operation", "?")
        return ToolResult(
            action_id="",
            success=True,
            output=f"[dry-run] git_op operation={operation}",
        )
