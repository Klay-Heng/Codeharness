"""Tests for the agent main loop (src/codeharness/loop.py).

AgentLoop (SPEC 3.1 / Task 12) orchestrates the LLM, tool registry,
guard engine, feedback engine, and memory store into the core
task-execution loop.  Every test drives the loop with MockBackend so
LLM turns are fully scripted and the loop behaves deterministically —
no network, no interactive prompts.

Scenarios (from the Task 12 brief):
1. completes a task in one round (LLM returns DONE / no tool calls)
2. uses tools then completes (read_file + write_file)
3. corrects after feedback (write -> failing check -> feedback -> fix)
4. stops at max correction rounds
5. guard blocks a dangerous action (rm -rf is skipped)
6. escalates on repeated failure
"""
from __future__ import annotations

from codeharness.feedback import FeedbackEngine
from codeharness.guard import GuardEngine
from codeharness.llm.mock import MockBackend
from codeharness.loop import AgentLoop
from codeharness.memory import MemoryStore
from codeharness.models import (
    Config,
    FailureCategory,
    FeedbackConfig,
    GuardConfig,
    LLMResponse,
    ToolsConfig,
)
from codeharness.parser import ResponseParser
from codeharness.tools.file_ops import ReadFileTool, WriteFileTool
from codeharness.tools.git_ops import GitOpTool
from codeharness.tools.package_ops import PackageOpTool
from codeharness.tools.registry import ToolRegistry
from codeharness.tools.search import GlobFilesTool, SearchCodeTool
from codeharness.tools.shell import RunShellTool
from codeharness.tools.testing_tool import RunTestsTool

# Command that fails with a SyntaxError traceback when `bad.py` is invalid
# and succeeds (rc=0) when it is valid.  Used to give the feedback engine
# real, deterministic input in a round.
_COMPILE_CMD = (
    "python -c \"compile(open('bad.py').read(), 'bad.py', 'exec')\""
)


def _build_harness(tmp_path, backend, *, max_rounds=5, max_same_error=3,
                   shell=None):
    """Build a fully wired AgentLoop with every dependency injected.

    All 8 tools are registered (matching the guard tests) so the loop
    behaves like the production harness.  ``shell`` overrides the
    default RunShellTool so tests can spy on execution.
    """
    root = str(tmp_path)
    registry = ToolRegistry(ToolsConfig())
    registry.register(ReadFileTool())
    registry.register(WriteFileTool(root=root))
    registry.register(SearchCodeTool())
    registry.register(GlobFilesTool())
    registry.register(shell if shell is not None else RunShellTool())
    registry.register(RunTestsTool())
    registry.register(GitOpTool())
    registry.register(PackageOpTool())

    guard = GuardEngine(GuardConfig(project_root=root), registry)
    config = Config()
    config.feedback = FeedbackConfig(
        max_correction_rounds=max_rounds, max_same_error=max_same_error
    )
    loop = AgentLoop(
        llm=backend,
        tools=registry,
        guard=guard,
        feedback=FeedbackEngine(config.feedback),
        memory=MemoryStore(tmp_path),
        config=config,
        parser=ResponseParser(),
    )
    return loop, guard


def _write_bad_and_compile(tmp_path, bad_content: str) -> LLMResponse:
    """An LLM turn that writes invalid code and then checks it compiles."""
    return LLMResponse(
        content="",
        tool_calls=[
            {"name": "write_file",
             "params": {"path": "bad.py", "content": bad_content}},
            {"name": "run_shell",
             "params": {"command": _COMPILE_CMD, "cwd": str(tmp_path)}},
        ],
    )


# ---------------------------------------------------------------------------
# Test 1: agent completes a task in one round
# ---------------------------------------------------------------------------

async def test_completes_task_in_one_round(tmp_path):
    backend = MockBackend(script=[LLMResponse(content="DONE", tool_calls=[])])
    loop, _ = _build_harness(tmp_path, backend)

    result = await loop.run("Write a hello world script.")

    assert result.status == "success"
    assert result.rounds == 1
    assert backend.call_count == 1
    assert result.final_context is not None
    assert len(result.final_context.correction_history) == 1
    decision = result.final_context.correction_history[0].decision
    assert decision is not None
    assert decision.action == "stop_success"


# ---------------------------------------------------------------------------
# Test 2: agent uses tools, then completes
# ---------------------------------------------------------------------------

async def test_uses_tools_then_completes(temp_project):
    """read_file + write_file execute, and the run succeeds."""
    backend = MockBackend(script=[
        LLMResponse(
            content="",
            tool_calls=[
                {"name": "read_file",
                 "params": {"path": str(temp_project / "src" / "main.py")}},
                {"name": "write_file",
                 "params": {"path": "notes.txt", "content": "read it"}},
            ],
        ),
        LLMResponse(content="DONE", tool_calls=[]),
    ])
    loop, guard = _build_harness(temp_project, backend)
    guard.session.approve("write_file")  # ASK_ONCE tool, session-approved

    result = await loop.run("Read main.py and save notes.")

    assert result.status == "success"
    # Round 1 executes tools; round 2 is the "DONE" completion signal.
    assert result.rounds == 2
    # Both tool calls actually executed and had an effect on disk.
    notes = temp_project / "notes.txt"
    assert notes.exists()
    assert notes.read_text(encoding="utf-8") == "read it"
    results = result.final_context.last_results
    assert len(results) == 2
    assert results[0].success
    assert "hello" in results[0].output  # read_file returned main.py content
    assert results[1].success
    assert result.final_context.correction_history[0].files_touched == {"notes.txt"}


# ---------------------------------------------------------------------------
# Test 3: agent corrects after feedback
# ---------------------------------------------------------------------------

async def test_corrects_after_feedback(tmp_path):
    """write bad -> compile fails -> feedback -> fix -> compile passes."""
    backend = MockBackend(script=[
        _write_bad_and_compile(tmp_path, "def broken("),          # round 1: fails
        _write_bad_and_compile(tmp_path, "def fixed():\n    return 1\n"),  # round 2
    ])
    loop, guard = _build_harness(tmp_path, backend)
    guard.session.approve("write_file")
    guard.session.approve("run_shell")

    result = await loop.run("Make bad.py valid Python.")

    assert result.status == "success"
    # Round 1: broken; round 2: fixed; round 3: MockBackend DONE completion.
    assert result.rounds == 3

    history = result.final_context.correction_history
    assert len(history) == 3
    # Round 1: the syntax error was detected, with file/line extracted.
    failures = history[0].failures_before
    assert len(failures) >= 1
    assert failures[0].category == FailureCategory.SYNTAX_ERROR
    assert failures[0].file == "bad.py"
    assert failures[0].line == 1
    # Round 2: the fix passes, no failures remain.
    assert history[1].failures_before == []

    # The feedback was serialized back into the conversation on retry.
    assert any(
        "[FEEDBACK]" in m.content for m in result.final_context.messages
    )
    # The fixed code is what ends up on disk.
    assert (tmp_path / "bad.py").read_text(encoding="utf-8") == (
        "def fixed():\n    return 1\n"
    )


# ---------------------------------------------------------------------------
# Test 4: agent stops at max rounds
# ---------------------------------------------------------------------------

async def test_stops_at_max_rounds(tmp_path):
    """An agent that never converges is stopped by max_correction_rounds."""
    backend = MockBackend(script=[
        _write_bad_and_compile(tmp_path, f"def broken{i}(") for i in range(3)
    ])
    loop, guard = _build_harness(tmp_path, backend, max_rounds=3)
    guard.session.approve("write_file")
    guard.session.approve("run_shell")

    result = await loop.run("Make bad.py valid Python.")

    assert result.status == "max_rounds"
    assert result.rounds == 3
    assert backend.call_count == 3
    decision = result.final_context.correction_history[-1].decision
    assert decision is not None
    assert decision.action == "stop_failure"


# ---------------------------------------------------------------------------
# Test 5: guard blocks a dangerous action
# ---------------------------------------------------------------------------

async def test_guard_blocks_dangerous_action(tmp_path):
    """rm -rf is ASK_ALWAYS: skipped, never executed, reported as blocked."""
    executed: list[dict] = []

    class SpyShell(RunShellTool):
        def execute(self, params):
            executed.append(params)
            return super().execute(params)

    backend = MockBackend(script=[
        LLMResponse(
            content="",
            tool_calls=[
                {"name": "run_shell",
                 "params": {"command": "rm -rf /", "cwd": "."}},
            ],
        ),
    ])
    loop, _ = _build_harness(tmp_path, backend, shell=SpyShell())

    result = await loop.run("Clean up the filesystem.")

    assert executed == []  # the dangerous command never ran
    # Round 1: blocked; round 2: MockBackend DONE (completion signal).
    assert result.status == "success"  # nothing failed; action was blocked
    assert result.rounds == 2
    results = result.final_context.last_results
    assert len(results) == 1
    assert results[0].success is False
    assert "ask_always" in results[0].error


# ---------------------------------------------------------------------------
# Test 6: agent escalates on repeated failure
# ---------------------------------------------------------------------------

async def test_escalates_on_repeated_failure(tmp_path):
    """Same category 3+ consecutive rounds -> escalate (needs human)."""
    backend = MockBackend(script=[
        _write_bad_and_compile(tmp_path, f"def broken{i}(") for i in range(4)
    ])
    loop, guard = _build_harness(
        tmp_path, backend, max_rounds=5, max_same_error=3
    )
    guard.session.approve("write_file")
    guard.session.approve("run_shell")

    result = await loop.run("Make bad.py valid Python.")

    assert result.status == "interrupted"
    assert result.rounds == 4
    history = result.final_context.correction_history
    assert history[-1].decision is not None
    assert history[-1].decision.action == "escalate"
    # Every failed round had the same SYNTAX_ERROR in bad.py.
    for record in history:
        assert {f.category for f in record.failures_before} == {
            FailureCategory.SYNTAX_ERROR
        }
