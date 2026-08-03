"""Package operations tool (SPEC tool table, section 3.3).

``package_op`` (HIGH): run pip install/uninstall/list via
``subprocess.run``.  ``python`` is configurable so tests (and users)
can target a virtualenv instead of the harness's own interpreter; the
uninstall path always passes ``--yes`` so it cannot hang on a prompt.
"""
from __future__ import annotations

import shlex
import subprocess
import sys

from codeharness.models import RiskLevel, ToolResult

_SUPPORTED_OPERATIONS = ("install", "uninstall", "list")


class PackageOpTool:
    """Install, uninstall, or list Python packages via pip."""

    name = "package_op"
    description = (
        "Run pip operations; params: operation (install, uninstall, "
        "list), package (required for install/uninstall), optional args."
    )
    risk_level = RiskLevel.HIGH

    def __init__(
        self, python: str | None = None, default_timeout: int = 180
    ) -> None:
        self.python = python or sys.executable
        self.default_timeout = default_timeout

    def execute(self, params: dict) -> ToolResult:
        operation = params.get("operation")
        if operation not in _SUPPORTED_OPERATIONS:
            return ToolResult(
                action_id="",
                success=False,
                error=(
                    f"unsupported operation: {operation!r} "
                    f"(expected {'|'.join(_SUPPORTED_OPERATIONS)})"
                ),
            )
        package = params.get("package")
        if operation in ("install", "uninstall") and not package:
            return ToolResult(
                action_id="",
                success=False,
                error=f"missing required param: package (for {operation})",
            )

        cmd = [self.python, "-m", "pip", "--disable-pip-version-check", operation]
        if operation == "uninstall":
            cmd.append("--yes")
        args = params.get("args") or []
        if isinstance(args, str):
            args = shlex.split(args)
        cmd.extend(str(a) for a in args)
        if operation in ("install", "uninstall"):
            cmd.append(str(package))

        try:
            proc = subprocess.run(
                cmd,
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
                error=f"pip {operation} timed out after {self.default_timeout}s",
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
            or f"pip {operation} failed (rc={proc.returncode})",
            exit_code=proc.returncode,
        )

    def dry_run(self, params: dict) -> ToolResult:
        operation = params.get("operation", "?")
        package = params.get("package", "")
        return ToolResult(
            action_id="",
            success=True,
            output=f"[dry-run] package_op operation={operation} package={package}",
        )
