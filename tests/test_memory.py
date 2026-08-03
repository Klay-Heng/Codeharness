"""Tests for MemoryStore (src/codeharness/memory.py).

Covers SPEC section 3.6 / US-4: cross-session persistence of project
conventions and past decisions as Markdown files under
``<base_path>/.harness/memory/``.  Conventions live in
``conventions/{name}.md``; decisions in ``decisions/{YYYY-MM-DD}-{slug}.md``.
Checks: a fresh store starts empty, save/load round-trips, convention
overwrite, decision filename/content format, most-recent-first ordering
with a limit, and the ~2000-char cap on ``build_system_context()``.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from codeharness.memory import MemoryStore

TODAY = datetime.now(UTC).date().isoformat()


def _make_store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path)


def test_fresh_store_has_empty_memory(tmp_path):
    """A brand-new store has no conventions, no decisions, and no context."""
    store = _make_store(tmp_path)
    memory_dir = tmp_path / ".harness" / "memory"
    assert (memory_dir / "conventions").is_dir()
    assert (memory_dir / "decisions").is_dir()
    assert store.load_conventions() == []
    assert store.load_recent_decisions(5) == []
    assert store.build_system_context() == ""


def test_save_and_load_convention(tmp_path):
    """A saved convention is written as conventions/{name}.md and round-trips."""
    store = _make_store(tmp_path)
    content = "Use 4-space indentation; keep functions ruff-clean."
    store.save_convention("coding-style", content)

    file = tmp_path / ".harness" / "memory" / "conventions" / "coding-style.md"
    assert file.is_file()
    assert content in file.read_text(encoding="utf-8")

    assert store.load_conventions() == [{"name": "coding-style", "content": content}]


def test_save_convention_overwrites_same_name(tmp_path):
    """Saving a convention twice replaces the old content, not duplicates it."""
    store = _make_store(tmp_path)
    store.save_convention("style", "version one")
    store.save_convention("style", "version two")

    assert store.load_conventions() == [{"name": "style", "content": "version two"}]


def test_save_and_load_decision(tmp_path):
    """A decision is written as decisions/{date}-{slug}.md with full markdown."""
    store = _make_store(tmp_path)
    store.save_decision(
        title="Use pytest instead of unittest",
        context="The test framework was not chosen yet.",
        decision="Standardize on pytest for all unit tests.",
        reasons=["async support", "rich fixtures", "team familiarity"],
        alternatives=["unittest", "hypothesis"],
    )

    files = list((tmp_path / ".harness" / "memory" / "decisions").glob("*.md"))
    assert len(files) == 1
    name = files[0].name
    assert name == f"{TODAY}-use-pytest-instead-of-unittest.md"

    text = files[0].read_text(encoding="utf-8")
    assert text.startswith("# Decision: Use pytest instead of unittest")
    assert "The test framework was not chosen yet." in text
    assert "Standardize on pytest for all unit tests." in text
    assert "- async support" in text
    assert "- rich fixtures" in text
    assert "- team familiarity" in text
    assert "- unittest" in text
    assert "- hypothesis" in text

    decisions = store.load_recent_decisions(5)
    assert len(decisions) == 1
    assert decisions[0]["title"] == "Use pytest instead of unittest"
    assert decisions[0]["date"] == TODAY
    assert "Standardize on pytest for all unit tests." in decisions[0]["content"]


def test_decisions_sorted_most_recent_first_and_limited(tmp_path):
    """Decisions are returned newest first (by date prefix) up to limit."""
    store = _make_store(tmp_path)
    decisions_dir = tmp_path / ".harness" / "memory" / "decisions"
    (decisions_dir / "2026-07-01-use-pytest.md").write_text("old", encoding="utf-8")
    (decisions_dir / "2026-07-02-use-typing.md").write_text("mid", encoding="utf-8")
    (decisions_dir / "2026-07-03-use-ruff.md").write_text("new", encoding="utf-8")

    all_decisions = store.load_recent_decisions(10)
    assert [d["title"] for d in all_decisions] == ["use-ruff", "use-typing", "use-pytest"]
    assert [d["date"] for d in all_decisions] == [
        "2026-07-03",
        "2026-07-02",
        "2026-07-01",
    ]

    recent_two = store.load_recent_decisions(2)
    assert [d["title"] for d in recent_two] == ["use-ruff", "use-typing"]


def test_build_system_context_includes_conventions_and_decisions(tmp_path):
    """The assembled context mentions every stored convention and decision."""
    store = _make_store(tmp_path)
    store.save_convention("coding-style", "Use 4-space indentation.")
    store.save_decision(
        title="Use pytest",
        context="n/a",
        decision="Standardize on pytest.",
        reasons=["async support"],
        alternatives=["unittest"],
    )

    context = store.build_system_context()
    assert "coding-style" in context
    assert "Use 4-space indentation." in context
    assert "Use pytest" in context
    assert "Standardize on pytest." in context
    assert "unittest" in context


def test_build_system_context_capped_at_2000_chars(tmp_path):
    """Large memory stores are truncated to roughly 2000 characters."""
    store = _make_store(tmp_path)
    for i in range(20):
        store.save_convention(f"conv-{i}", "x" * 300)

    context = store.build_system_context()
    assert len(context) <= 2000
    assert context.endswith("[memory context truncated]")
