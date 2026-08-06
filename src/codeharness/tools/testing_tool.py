"""Test-running tool (SPEC tool table, section 3.3).

``run_tests`` (LOW): run pytest (optionally restricted to ``path`` with
extra ``flags``) and summarize results from the JUnit XML report the
tool always generates (SPEC edge case: ``run_tests`` 默认生成 JUnit XML
报告).  Failed tests make the ToolResult ``success=False`` so the loop
can feed the summary back to the model.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import ClassVar

from codeharness.models import RiskLevel, ToolResult


class RunTestsTool:
    """Run pytest and summarize pass/fail from a generated JUnit XML report."""

    name = "run_tests"
    description = (
        "Run pytest; params: optional path (file or dir, default cwd), "
        "optional flags (list or space-separated string)."
    )
    risk_level = RiskLevel.LOW
    parameters_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File or directory to run tests on (default: current directory).",
            },
            "flags": {
                "type": "string",
                "description": "Extra pytest flags as a space-separated string (e.g. '-v -x').",
            },
        },
        "required": [],
    }

    def __init__(self, default_timeout: int = 300) -> None:
        self.default_timeout = default_timeout

    def _summarize(self, xml_file: Path, rc: int, raw: str) -> ToolResult:
        """Build the summary ToolResult from the JUnit XML report."""
        try:
            tree = ET.parse(xml_file)
        except (ET.ParseError, FileNotFoundError):
            # pytest crashed before writing a report (e.g. collection error).
            if rc == 0:
                return ToolResult(action_id="", success=True, output=raw, exit_code=rc)
            return ToolResult(
                action_id="",
                success=False,
                error=raw[-2000:] or f"pytest failed (rc={rc})",
                output=raw,
                exit_code=rc,
            )

        root = tree.getroot()
        suites = root.findall("testsuite") if root.tag == "testsuites" else [root]

        tests = failures = errors = skipped = 0
        failed_cases: list[str] = []
        for suite in suites:
            tests += int(suite.get("tests", 0) or 0)
            failures += int(suite.get("failures", 0) or 0)
            errors += int(suite.get("errors", 0) or 0)
            skipped += int(suite.get("skipped", 0) or 0)
            for case in suite.findall("testcase"):
                for fail in case.findall("failure") + case.findall("error"):
                    label = f"{case.get('classname', '')}::{case.get('name', '')}"
                    message = fail.get("message") or ""
                    if not message:
                        message = (fail.text or "").strip()[:200]
                    failed_cases.append(f"FAILED {label}")
                    if message:
                        failed_cases.append(f"  {message}")

        passed = tests - failures - errors - skipped
        summary = (
            f"passed: {passed}, failed: {failures}, "
            f"errors: {errors}, skipped: {skipped}"
        )
        output = "\n".join([summary, *failed_cases])
        # The exit code is authoritative: pytest can exit nonzero with an
        # empty report (e.g. rc=4 usage error for a missing path).
        ok = rc == 0
        result = ToolResult(
            action_id="", success=ok, output=output, exit_code=rc
        )
        if not ok and failures == 0 and errors == 0:
            # pytest itself failed (collection/usage error); surface it.
            result.error = raw[-2000:] or f"pytest failed (rc={rc})"
        return result

    def execute(self, params: dict) -> ToolResult:
        target = params.get("path") or "."
        flags = params.get("flags") or []
        if isinstance(flags, str):
            flags = shlex.split(flags)

        fd, xml_name = tempfile.mkstemp(suffix=".xml", prefix="codeharness_junit_")
        os.close(fd)
        xml_file = Path(xml_name)
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            str(target),
            *(str(f) for f in flags),
            f"--junitxml={xml_name}",
        ]
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
            xml_file.unlink(missing_ok=True)
            return ToolResult(
                action_id="",
                success=False,
                error=f"pytest timed out after {self.default_timeout}s",
            )
        raw = (proc.stdout + proc.stderr).strip()
        try:
            return self._summarize(xml_file, proc.returncode, raw)
        finally:
            xml_file.unlink(missing_ok=True)

    def dry_run(self, params: dict) -> ToolResult:
        target = params.get("path", ".")
        return ToolResult(
            action_id="",
            success=True,
            output=f"[dry-run] run_tests path={target}",
        )
