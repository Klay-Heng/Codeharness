"""File read/write tools (SPEC tool table, section 3.3).

``read_file`` (LOW): read a text file, optionally a 1-based inclusive
line range.  ``write_file`` (MEDIUM): create or overwrite a file,
bounded to the project root so the agent cannot escape the workspace.
Both implement the ``Tool`` protocol from models.py (synchronous
``execute``/``dry_run`` returning ``ToolResult``).
"""
from __future__ import annotations

from pathlib import Path

from codeharness.models import RiskLevel, ToolResult


class ReadFileTool:
    """Read a text file, optionally a 1-based inclusive line range."""

    name = "read_file"
    description = (
        "Read a text file. Parameters: path (string, required) — "
        "the file path to read; start_line (int, optional) — first "
        "line to read (1-based); end_line (int, optional) — last "
        "line to read (1-based, inclusive)."
    )
    risk_level = RiskLevel.LOW

    def execute(self, params: dict) -> ToolResult:
        path = params.get("path") or params.get("file")
        if not path:
            return ToolResult(
                action_id="", success=False, error="missing required param: path"
            )
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            return ToolResult(
                action_id="", success=False, error=f"{type(exc).__name__}: {exc}"
            )

        start_line = params.get("start_line")
        end_line = params.get("end_line")
        if start_line is None and end_line is None:
            return ToolResult(action_id="", success=True, output=text)

        lines = text.splitlines()
        try:
            start = int(start_line) if start_line is not None else 1
            end = int(end_line) if end_line is not None else len(lines)
        except (TypeError, ValueError):
            return ToolResult(
                action_id="",
                success=False,
                error="start_line/end_line must be integers",
            )
        if start < 1 or end < 1 or start > end:
            return ToolResult(
                action_id="",
                success=False,
                error=f"invalid line range: start_line={start}, end_line={end}",
            )
        return ToolResult(
            action_id="", success=True, output="\n".join(lines[start - 1 : end])
        )

    def dry_run(self, params: dict) -> ToolResult:
        path = params.get("path", "?")
        return ToolResult(
            action_id="", success=True, output=f"[dry-run] read_file path={path}"
        )


class WriteFileTool:
    """Create or overwrite a file inside the project root.

    ``root`` bounds every write: paths that resolve outside it are
    refused with a failed ToolResult (SPEC section 3.3 / 3.4: path
    boundary check).  Missing parent directories are created.
    """

    name = "write_file"
    description = (
        "Create or overwrite a file. Parameters: path (string, "
        "required) — the file path to write to; content (string, "
        "required) — the full text content to write into the file."
    )
    risk_level = RiskLevel.MEDIUM

    def __init__(self, root: str | Path = ".") -> None:
        self.root = str(root)

    def _resolve(self, path: str | Path) -> Path | None:
        """Resolve ``path`` against the project root; None if it escapes.

        Relative paths are resolved against the root itself (the harness
        runs with cwd == project root, so root-relative is the natural
        coordinate space for the agent).
        """
        root = Path(self.root).resolve()
        target = Path(path)
        if target.is_absolute():
            target = target.resolve()
        else:
            target = (root / target).resolve()
        if not target.is_relative_to(root):
            return None
        return target

    def execute(self, params: dict) -> ToolResult:
        path = params.get("path") or params.get("file")
        if not path:
            return ToolResult(
                action_id="", success=False, error="missing required param: path"
            )
        content = params.get("content")
        if content is None:
            return ToolResult(
                action_id="", success=False, error="missing required param: content"
            )
        target = self._resolve(path)
        if target is None:
            return ToolResult(
                action_id="",
                success=False,
                error=f"path outside project root: {path}",
            )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8")
        except OSError as exc:
            return ToolResult(
                action_id="", success=False, error=f"{type(exc).__name__}: {exc}"
            )
        return ToolResult(
            action_id="",
            success=True,
            output=f"wrote {len(str(content))} chars to {target}",
        )

    def dry_run(self, params: dict) -> ToolResult:
        path = params.get("path")
        if not path:
            return ToolResult(
                action_id="", success=False, error="missing required param: path"
            )
        target = self._resolve(path)
        if target is None:
            return ToolResult(
                action_id="",
                success=False,
                error=f"path outside project root: {path}",
            )
        size = len(str(params.get("content", "")))
        return ToolResult(
            action_id="",
            success=True,
            output=f"[dry-run] would write {size} chars to {target}",
        )
