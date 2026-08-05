"""Search and glob tools (SPEC tool table, section 3.3).

``search_code`` (LOW): regex search across files, wrapping ripgrep or
grep when available and falling back to a pure-Python walk so the tool
works on machines without either binary.  ``glob_files`` (LOW): match
file paths by glob pattern relative to the current directory.
"""
from __future__ import annotations

import fnmatch
import glob
import re
import shutil
import subprocess
from pathlib import Path

from codeharness.models import RiskLevel, ToolResult

_SEARCH_TIMEOUT = 30


class SearchCodeTool:
    """Regex search across files (pattern, optional path and glob filter)."""

    name = "search_code"
    description = (
        "Regex search across files; params: pattern, optional path "
        "(directory, default cwd), optional glob (filename filter)."
    )
    risk_level = RiskLevel.LOW
    parameters_schema = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The regex pattern to search for.",
            },
            "path": {
                "type": "string",
                "description": "Directory to search in (default: current directory).",
            },
            "glob": {
                "type": "string",
                "description": "Filename filter pattern, e.g. '*.py'.",
            },
        },
        "required": ["pattern"],
    }

    def _search_python(self, pattern: str, root: Path, file_glob: str | None) -> str:
        """Pure-Python fallback producing the same path:line:text format."""
        rx = re.compile(pattern)
        hits: list[str] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if file_glob and not fnmatch.fnmatch(path.name, file_glob):
                continue
            try:
                lines = path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                if rx.search(line):
                    hits.append(f"{path}:{i}:{line}")
        return "\n".join(hits)

    def execute(self, params: dict) -> ToolResult:
        pattern = params.get("pattern")
        if not pattern:
            return ToolResult(
                action_id="", success=False, error="missing required param: pattern"
            )
        root = Path(params.get("path") or ".")
        if not root.exists():
            return ToolResult(
                action_id="", success=False, error=f"path does not exist: {root}"
            )
        file_glob = params.get("glob")

        rg = shutil.which("rg")
        exe = rg or shutil.which("grep")
        # Use the pure-Python fallback when a glob filter is active or no
        # external tool is available.  The Python path handles glob
        # deterministically across platforms, while rg --glob and grep
        # --include have subtle behavioural differences on Windows.
        if exe is None or file_glob is not None:
            return ToolResult(
                action_id="",
                success=True,
                output=self._search_python(str(pattern), root, file_glob),
            )

        if rg:
            cmd = [rg, "--line-number", "--no-heading"]
            if file_glob:
                cmd += ["--glob", str(file_glob)]
            cmd += [str(pattern), str(root)]
        else:
            cmd = [exe, "-rn"]
            if file_glob:
                cmd += ["--include", str(file_glob)]
            cmd += [str(pattern), str(root)]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_SEARCH_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                action_id="",
                success=False,
                error=f"search timed out after {_SEARCH_TIMEOUT}s",
            )
        if proc.returncode == 1:
            # grep/rg exit 1 when nothing matched: not an error.
            return ToolResult(action_id="", success=True, output="")
        if proc.returncode != 0:
            return ToolResult(
                action_id="",
                success=False,
                error=proc.stderr.strip()
                or f"search failed (rc={proc.returncode})",
            )
        return ToolResult(action_id="", success=True, output=proc.stdout.strip())

    def dry_run(self, params: dict) -> ToolResult:
        pattern = params.get("pattern", "?")
        return ToolResult(
            action_id="",
            success=True,
            output=f"[dry-run] search_code pattern={pattern}",
        )


class GlobFilesTool:
    """Match file paths by glob pattern relative to the current directory."""

    name = "glob_files"
    description = "Match file paths by glob pattern, e.g. '**/*.py'."
    risk_level = RiskLevel.LOW
    parameters_schema = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern to match, e.g. '**/*.py' or 'src/**/*.ts'.",
            },
        },
        "required": ["pattern"],
    }

    def execute(self, params: dict) -> ToolResult:
        pattern = params.get("pattern")
        if not pattern:
            return ToolResult(
                action_id="", success=False, error="missing required param: pattern"
            )
        try:
            matches = sorted(glob.glob(str(pattern), recursive=True))
        except (ValueError, OSError) as exc:
            return ToolResult(
                action_id="", success=False, error=f"{type(exc).__name__}: {exc}"
            )
        return ToolResult(action_id="", success=True, output="\n".join(matches))

    def dry_run(self, params: dict) -> ToolResult:
        pattern = params.get("pattern", "?")
        return ToolResult(
            action_id="",
            success=True,
            output=f"[dry-run] glob_files pattern={pattern}",
        )
