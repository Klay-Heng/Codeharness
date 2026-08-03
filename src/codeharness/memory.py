"""Cross-session memory for the CodeHarness agent.

MemoryStore persists project conventions and past decisions as Markdown
files under ``<base_path>/.harness/memory/`` so knowledge from one session
survives into the next (SPEC section 3.6 / US-4).  Conventions live in
``conventions/{name}.md``; decisions in ``decisions/{YYYY-MM-DD}-{slug}.md``.
``build_system_context()`` assembles all conventions plus the most recent
decisions into the "project context" block of the system prompt, capped at
roughly 2000 characters so it never dominates the prompt (SPEC 3.6:
all conventions + latest 10 decisions, under 2k tokens).
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

# Filenames: 2026-07-08-use-pytest.md  (date prefix + slug).
_DECISION_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)\.md$")
_SYSTEM_CONTEXT_LIMIT = 2000
_DEFAULT_DECISION_LIMIT = 10
_TRUNCATION_MARKER = "\n[memory context truncated]"


class MemoryStore:
    """File-based, human- and LLM-readable memory store."""

    def __init__(self, base_path: Path) -> None:
        """Create the store rooted at ``base_path/.harness/memory``.

        Both the conventions and decisions directories are created if
        missing, so a fresh store is immediately usable.
        """
        self.root = Path(base_path) / ".harness" / "memory"
        self.conventions_dir = self.root / "conventions"
        self.decisions_dir = self.root / "decisions"
        self.conventions_dir.mkdir(parents=True, exist_ok=True)
        self.decisions_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Conventions
    # ------------------------------------------------------------------

    def save_convention(self, name: str, content: str) -> Path:
        """Persist a project convention as ``conventions/{name}.md``.

        Saving under an existing name overwrites the previous content.
        """
        if not name:
            raise ValueError("convention name must not be empty")
        path = self.conventions_dir / f"{name}.md"
        path.write_text(content, encoding="utf-8")
        return path

    def load_conventions(self) -> list[dict]:
        """Return all conventions as ``[{"name": ..., "content": ...}]``."""
        conventions: list[dict] = []
        for path in sorted(self.conventions_dir.glob("*.md")):
            conventions.append(
                {"name": path.stem, "content": path.read_text(encoding="utf-8")}
            )
        return conventions

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    def save_decision(
        self,
        title: str,
        context: str,
        decision: str,
        reasons: list[str],
        alternatives: list[str],
    ) -> Path:
        """Persist a decision as ``decisions/{date}-{slug}.md`` in Markdown.

        The date prefix makes the files sort chronologically, so newer
        decisions sort first; the slug is derived from the title.
        """
        slug = self._slugify(title)
        path = self.decisions_dir / f"{self._today_iso()}-{slug}.md"
        path.write_text(self._render_decision(title, context, decision, reasons, alternatives), encoding="utf-8")
        return path

    def load_recent_decisions(self, limit: int) -> list[dict]:
        """Return the most recent decisions, newest first, up to ``limit``.

        Each entry is ``{"title": ..., "date": ..., "content": ...}``.
        Ordering follows the filename's date prefix (reverse-sorted), with
        the title parsed from the Markdown heading when present.
        """
        files = sorted(self.decisions_dir.glob("*.md"), reverse=True)
        decisions: list[dict] = []
        for path in files[: max(0, limit)]:
            text = path.read_text(encoding="utf-8")
            decisions.append(self._parse_decision(path.name, text))
        return decisions

    # ------------------------------------------------------------------
    # System context
    # ------------------------------------------------------------------

    def build_system_context(self) -> str:
        """Assemble conventions + recent decisions for the system prompt.

        Returns an empty string when nothing has been stored.  The result
        is capped at roughly 2000 characters; when truncated a marker is
        appended so the LLM knows the context was cut.
        """
        sections: list[str] = []
        for convention in self.load_conventions():
            sections.append(
                f"--- Convention: {convention['name']} ---\n{convention['content']}"
            )
        for decision in self.load_recent_decisions(_DEFAULT_DECISION_LIMIT):
            header = f"--- Decision: {decision['title']}"
            if decision["date"]:
                header += f" ({decision['date']})"
            sections.append(f"{header} ---\n{decision['content']}")
        if not sections:
            return ""
        text = "\n\n".join(sections)
        if len(text) > _SYSTEM_CONTEXT_LIMIT:
            cut = _SYSTEM_CONTEXT_LIMIT - len(_TRUNCATION_MARKER)
            text = text[:cut] + _TRUNCATION_MARKER
        return text

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _today_iso() -> str:
        """Today's date as ``YYYY-MM-DD`` (timezone-aware to satisfy DTZ)."""
        return datetime.now(UTC).date().isoformat()

    @staticmethod
    def _slugify(title: str) -> str:
        """Turn a title into a filename-safe slug: lowercase, dashes.

        Non-alphanumeric runs collapse to a single dash; a title with no
        usable characters (e.g. pure CJK) falls back to ``"decision"``.
        """
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        return slug or "decision"

    def _render_decision(
        self,
        title: str,
        context: str,
        decision: str,
        reasons: list[str],
        alternatives: list[str],
    ) -> str:
        """Render a decision file body in the SPEC Markdown format."""
        lines = [
            f"# Decision: {title}",
            "",
            f"**Date:** {self._today_iso()}",
            "",
            "## Context",
            context,
            "",
            "## Decision",
            decision,
            "",
            "## Reasons",
        ]
        for reason in reasons or []:
            lines.append(f"- {reason}")
        lines += ["", "## Alternatives considered"]
        for alternative in alternatives or []:
            lines.append(f"- {alternative}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _parse_decision(filename: str, text: str) -> dict:
        """Parse a decision file into ``{title, date, content}``.

        The title comes from the ``# Decision:`` heading when present,
        falling back to the filename (date prefix stripped).  The date
        comes from the filename prefix when present.
        """
        match = _DECISION_FILE_RE.match(filename)
        decision_date = match.group(1) if match else ""
        stem = match.group(2) if match else filename[: -len(".md")]
        title_match = re.search(r"^# Decision:\s*(.+?)\s*$", text, flags=re.MULTILINE)
        title = title_match.group(1) if title_match else stem
        return {"title": title, "date": decision_date, "content": text}
