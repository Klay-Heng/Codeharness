"""Tests for the REPL interface (src/codeharness/repl.py).

The REPL (SPEC 3.9 / Task 13) is a rich terminal UI over the AgentLoop:
welcome banner, backslash (`\\`)-continuation multi-line task input, per-round
"thought" panels, `[y/n/session]` guard approval prompts, color-coded
feedback output, and exit via exit/quit/Ctrl+C/Ctrl+D.

Every test drives the REPL with a FakeAgentLoop returning preset
RunResults and monkeypatched rich prompts, so nothing blocks on real
stdin and no LLM or network is involved.  The console is captured
(Console(record=True)) so rendering is asserted on exported text.
"""
from __future__ import annotations

import pytest
from rich.console import Console

import codeharness.repl as repl_module
from codeharness.memory import MemoryStore
from codeharness.models import (
    ClassifiedFailure,
    Config,
    CorrectionRecord,
    FailureCategory,
    LoopDecision,
    RunResult,
    ToolResult,
    TurnContext,
)
from codeharness.repl import REPL


class FakeAgentLoop:
    """Duck-typed AgentLoop that replays preset RunResults and records tasks."""

    def __init__(self, results: list[RunResult], memory=None) -> None:
        self.results = list(results)
        self.calls: list[str] = []
        self.memory = memory

    async def run(self, task: str) -> RunResult:
        self.calls.append(task)
        if self.results:
            return self.results.pop(0)
        return RunResult(status="success", rounds=0, duration_ms=1)


class FakeCredentialStore:
    """Reports a fixed key status without touching the real keyring."""

    def __init__(self, status: str = "not_set") -> None:
        self._status = status

    def check_status(self) -> str:
        return self._status


def _make_repl(
    tmp_path, results: list[RunResult] | None = None, memory=None, key_status="not_set"
) -> tuple[REPL, FakeAgentLoop, Console]:
    loop = FakeAgentLoop(results or [], memory=memory)
    console = Console(record=True, width=100)
    config = Config()
    config.project.name = "demo"
    repl = REPL(loop, FakeCredentialStore(key_status), config, console=console)
    return repl, loop, console


def _success_result(rounds: int = 1, ms: int = 123) -> RunResult:
    """A clean run whose history ends in stop_success."""
    history = [
        CorrectionRecord(
            round_id=i,
            action_ids=[f"a{i}"],
            decision=LoopDecision(
                action="retry",
                reason=f"Retrying correction ({i}/2)",
                round_number=i,
            ),
        )
        for i in range(1, rounds + 1)
    ]
    history[-1].decision = LoopDecision(
        action="stop_success", reason="All checks passed", round_number=rounds
    )
    return RunResult(
        status="success",
        rounds=rounds,
        duration_ms=ms,
        final_context=TurnContext(messages=[], round_count=rounds, correction_history=history),
    )


def _script_prompts(monkeypatch, script: list[str]) -> list[tuple[str, dict]]:
    """Replace rich Prompt with a stub replaying `script`; record prompts.

    An empty script raises EOFError (simulating Ctrl+D) so a test that
    forgets to script an exit still terminates.
    """
    calls: list[tuple[str, dict]] = []

    class FakePrompt:
        @staticmethod
        def ask(prompt_text, **kwargs):
            calls.append((str(prompt_text), dict(kwargs)))
            if not script:
                raise EOFError
            return script.pop(0)

    monkeypatch.setattr(repl_module, "Prompt", FakePrompt)
    return calls


def _script_confirms(monkeypatch, answers: list[bool]) -> list[tuple[str, dict]]:
    """Replace rich Confirm with a stub replaying `answers`; record prompts."""
    calls: list[tuple[str, dict]] = []

    class FakeConfirm:
        @staticmethod
        def ask(prompt_text, **kwargs):
            calls.append((str(prompt_text), dict(kwargs)))
            if not answers:
                return False
            return answers.pop(0)

    monkeypatch.setattr(repl_module, "Confirm", FakeConfirm)
    return calls


# ---------------------------------------------------------------------------
# Input loop
# ---------------------------------------------------------------------------


async def test_accepts_input_and_calls_agent_loop_run(tmp_path, monkeypatch):
    repl, loop, console = _make_repl(tmp_path, results=[_success_result()])
    prompt_calls = _script_prompts(monkeypatch, ["write tests for utils.py", "exit"])
    _script_confirms(monkeypatch, [False])  # decline exit-save prompt

    await repl.start()

    assert loop.calls == ["write tests for utils.py"]
    assert len(prompt_calls) >= 2
    assert prompt_calls[0][0] == "> "  # main prompt
    text = console.export_text()
    assert "success" in text
    assert "123 ms" in text


@pytest.mark.parametrize("command", ["exit", "quit"])
async def test_exit_commands_stop_the_loop(tmp_path, monkeypatch, command):
    repl, loop, console = _make_repl(tmp_path)
    _script_prompts(monkeypatch, [command])

    await repl.start()

    assert loop.calls == []
    assert "Goodbye" in console.export_text()


@pytest.mark.parametrize("exc", [KeyboardInterrupt, EOFError])
async def test_ctrl_c_and_ctrl_d_exit_gracefully(tmp_path, monkeypatch, exc):
    repl, loop, console = _make_repl(tmp_path)

    class RaisingPrompt:
        @staticmethod
        def ask(prompt_text, **kwargs):
            raise exc()

    monkeypatch.setattr(repl_module, "Prompt", RaisingPrompt)

    await repl.start()

    assert loop.calls == []
    assert "Goodbye" in console.export_text()


async def test_multiline_backslash_continuation_joins_lines(tmp_path, monkeypatch):
    repl, loop, _console = _make_repl(tmp_path, results=[_success_result()])
    _script_prompts(monkeypatch, ["write a file with\\", "a second line", "exit"])
    _script_confirms(monkeypatch, [False])  # decline exit-save prompt

    await repl.start()

    assert loop.calls == ["write a file with\na second line"]


# ---------------------------------------------------------------------------
# Guard approval prompts (SPEC 3.4 HITL: [y/n/session])
# ---------------------------------------------------------------------------


async def test_guard_approval_prompt_formatting(tmp_path, monkeypatch):
    repl, _loop, _console = _make_repl(tmp_path)
    prompts: list[tuple[str, dict]] = []

    class FakePrompt:
        @staticmethod
        def ask(prompt_text, **kwargs):
            prompts.append((str(prompt_text), dict(kwargs)))
            return "session"

    monkeypatch.setattr(repl_module, "Prompt", FakePrompt)

    choice = repl.request_approval("run_shell", "rm -rf /")

    assert choice == "session"
    text, kwargs = prompts[0]
    assert "run_shell" in text
    assert "rm -rf /" in text
    assert "[y/n/session]" in text
    assert kwargs["choices"] == ["y", "n", "session"]
    assert kwargs["default"] == "n"  # no-response defaults to deny (SPEC 3.4)


# ---------------------------------------------------------------------------
# Feedback display: color-coded status + per-round thought panels
# ---------------------------------------------------------------------------


async def test_feedback_status_colors(tmp_path):
    repl, _loop, _console = _make_repl(tmp_path)
    assert repl._status_style("success") == "green"
    assert repl._status_style("failure") == "red"
    assert repl._status_style("max_rounds") == "yellow"
    assert repl._status_style("interrupted") == "blue"
    assert repl._category_style(FailureCategory.SYNTAX_ERROR) == "red"
    assert repl._category_style(FailureCategory.ASSERTION_FAILURE) == "red"
    assert repl._category_style(FailureCategory.LINT_WARNING) == "yellow"
    assert repl._category_style(FailureCategory.TYPE_ERROR) == "yellow"


async def test_run_rendering_shows_rounds_failures_and_blocked_notice(
    tmp_path, monkeypatch
):
    history = [
        CorrectionRecord(
            round_id=1,
            action_ids=["a1"],
            failures_before=[
                ClassifiedFailure(
                    category=FailureCategory.SYNTAX_ERROR,
                    file="bad.py",
                    line=1,
                    message="invalid syntax",
                )
            ],
            decision=LoopDecision(
                action="retry", reason="Retrying correction (1/2)", round_number=1
            ),
        ),
        CorrectionRecord(
            round_id=2,
            action_ids=["a2"],
            decision=LoopDecision(
                action="stop_success", reason="All checks passed", round_number=2
            ),
        ),
    ]
    result = RunResult(
        status="success",
        rounds=2,
        duration_ms=321,
        final_context=TurnContext(
            messages=[],
            round_count=2,
            correction_history=history,
            last_results=[
                ToolResult(action_id="x", success=False, error="blocked by guard: ask_always")
            ],
        ),
    )
    repl, _loop, console = _make_repl(tmp_path, results=[result])
    _script_prompts(monkeypatch, ["task", "exit"])
    _script_confirms(monkeypatch, [False])  # decline exit-save prompt

    await repl.start()

    text = console.export_text()
    assert "Round 1" in text
    assert "SYNTAX_ERROR" in text
    assert "bad.py:1" in text
    assert "Round 2" in text
    assert "All checks passed" in text
    assert "blocked 1 action(s)" in text
    assert "2 round(s), 321 ms" in text


# ---------------------------------------------------------------------------
# Banner and exit-save (SPEC 3.9: prompt to save key decisions on exit)
# ---------------------------------------------------------------------------


async def test_welcome_banner_shows_model_and_project(tmp_path, monkeypatch):
    repl, _loop, console = _make_repl(tmp_path, key_status="not_set")
    _script_prompts(monkeypatch, ["exit"])

    await repl.start()

    text = console.export_text()
    assert "CodeHarness" in text
    assert "deepseek-chat" in text  # default LLMConfig.model
    assert "demo" in text  # project name
    assert "not_set" in text  # credential status


async def test_exit_prompt_saves_key_decisions_to_memory(tmp_path, monkeypatch):
    memory = MemoryStore(tmp_path)
    repl, _loop, _console = _make_repl(
        tmp_path, results=[_success_result()], memory=memory
    )
    _script_prompts(monkeypatch, ["write tests", "quit"])
    confirms = _script_confirms(monkeypatch, [True])

    await repl.start()

    assert "Save" in confirms[0][0]
    decisions = memory.load_recent_decisions(5)
    assert len(decisions) == 1
    assert "write tests" in decisions[0]["content"]
    assert "success" in decisions[0]["content"]
