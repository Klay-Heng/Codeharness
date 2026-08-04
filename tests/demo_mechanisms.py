"""Deterministic mechanism demonstration (SPEC A.6 / Task 16).

Three demos, each exercising one core harness mechanism end-to-end with
no network and no real LLM — MockBackend (scripted LLM responses) drives
everything, so the same result is produced on every run:

1. ``demo_guard_intercepts_dangerous_action`` — the guard engine screens
   ``rm -rf /`` as ASK_ALWAYS while allowing a safe ``read_file``.
2. ``demo_feedback_drives_correction`` — a failing round is classified,
   fed back to the agent, and the agent corrects its code in round 2.
3. ``demo_feedback_classifier_precision`` — the deep dimension: precise
   failure classification (SyntaxError at test.py:15, AssertionError,
   all-pass), a strategy for all 9 failure categories, and loop
   controller escalation after 3 consecutive same-category errors.

Run as a script::

    python tests/demo_mechanisms.py

Run under pytest::

    pytest tests/demo_mechanisms.py -v

The demo constructors build their own ``GuardConfig`` / ``ToolsConfig``
explicitly instead of calling ``ConfigLoader().load()`` so the outcome
never depends on any harness.toml that happens to exist in the working
directory — determinism by construction.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from codeharness.feedback import (
    FailureClassifier,
    FeedbackEngine,
    LoopController,
    StrategySelector,
)
from codeharness.guard import GuardEngine
from codeharness.llm.mock import MockBackend
from codeharness.loop import AgentLoop
from codeharness.memory import MemoryStore
from codeharness.models import (
    Action,
    ClassifiedFailure,
    Config,
    CorrectionRecord,
    FailureCategory,
    FeedbackConfig,
    GuardConfig,
    GuardVerdict,
    LLMResponse,
    ToolResult,
    ToolsConfig,
)
from codeharness.parser import ResponseParser
from codeharness.tools.file_ops import ReadFileTool, WriteFileTool
from codeharness.tools.registry import ToolRegistry
from codeharness.tools.shell import RunShellTool


# ---------------------------------------------------------------------------
# Demo 1: guard engine intercepts dangerous actions
# ---------------------------------------------------------------------------


def demo_guard_intercepts_dangerous_action() -> None:
    """Demonstrate: guard engine blocks 'rm -rf /' deterministically."""
    registry = ToolRegistry(ToolsConfig())
    registry.register(RunShellTool())
    registry.register(ReadFileTool())
    guard = GuardEngine(GuardConfig(), registry)

    # Dangerous action: recursive force delete is an ASK_ALWAYS pattern.
    action = Action(tool="run_shell", params={"command": "rm -rf /", "cwd": "."})
    verdict = guard.check(action)
    assert verdict == GuardVerdict.ASK_ALWAYS, f"Expected ASK_ALWAYS, got {verdict}"
    assert verdict.value == "ask_always"

    # Safe action for comparison: read_file is LOW risk, no pattern, no
    # path boundary rule -> ALLOW with no session approval needed.
    safe_action = Action(tool="read_file", params={"path": "src/main.py"})
    safe_verdict = guard.check(safe_action)
    assert safe_verdict == GuardVerdict.ALLOW, f"Expected ALLOW, got {safe_verdict}"
    assert safe_verdict.value == "allow"

    print("PASS: Guard correctly intercepts dangerous 'rm -rf /' (ASK_ALWAYS)")
    print("PASS: Guard allows safe read_file (ALLOW)")


# ---------------------------------------------------------------------------
# Demo 2: feedback loop drives correction
# ---------------------------------------------------------------------------

# Check commands run against the file the agent just wrote.  The broken
# version raises ZeroDivisionError (a RUNTIME_ERROR) deterministically;
# the fixed version exits 0.  ``python -c`` puts the cwd first on
# sys.path, so ``import test`` resolves to the demo's test.py.
_BROKEN_CHECK = 'python -c "import test; test.broken()"'
_FIXED_CHECK = 'python -c "import test; test.fixed()"'

_BROKEN_CODE = "def broken(): return 1/0"
_FIXED_CODE = "def fixed(): return 0"


async def demo_feedback_drives_correction() -> None:
    """Demonstrate: injected failure -> feedback -> agent changes behavior."""
    with tempfile.TemporaryDirectory(prefix="codeharness-demo-") as tmp:
        root = Path(tmp)

        registry = ToolRegistry(ToolsConfig())
        registry.register(ReadFileTool())
        registry.register(WriteFileTool(root=str(root)))
        registry.register(RunShellTool())

        # Passive-ish guard: everything in the temp project is allowed,
        # but ASK_ONCE tools still need session approval (like a user
        # clicking "yes" once per session in the REPL).
        guard = GuardEngine(GuardConfig(project_root=str(root)), registry)
        guard.session.approve("write_file")
        guard.session.approve("run_shell")

        # Script: Round 1 writes code that fails -> Round 2 fixes it ->
        # Round 3 "done" (never reached: the fix succeeds in round 2, so
        # the loop stops early on success).
        script = [
            LLMResponse(
                content="I'll create the file.",
                tool_calls=[
                    {
                        "name": "write_file",
                        "params": {"path": "test.py", "content": _BROKEN_CODE},
                    },
                    {
                        "name": "run_shell",
                        "params": {"command": _BROKEN_CHECK, "cwd": str(root)},
                    },
                ],
            ),
            LLMResponse(
                content="Fixing the division by zero.",
                tool_calls=[
                    {
                        "name": "write_file",
                        "params": {"path": "test.py", "content": _FIXED_CODE},
                    },
                    {
                        "name": "run_shell",
                        "params": {"command": _FIXED_CHECK, "cwd": str(root)},
                    },
                ],
            ),
            LLMResponse(content="Task complete."),
        ]
        mock_llm = MockBackend(script=script)

        config = Config()
        loop = AgentLoop(
            llm=mock_llm,
            tools=registry,
            guard=guard,
            feedback=FeedbackEngine(config.feedback),
            memory=MemoryStore(root),
            config=config,
            parser=ResponseParser(),
        )

        result = await loop.run("Write a working function")

        assert result.status == "success", f"Expected success, got {result.status}"
        assert result.rounds >= 2, f"Expected at least 2 rounds, got {result.rounds}"
        assert mock_llm.call_count >= 2, (
            f"Expected at least 2 LLM calls, got {mock_llm.call_count}"
        )
        # Behavior changed after feedback: the fixed implementation is on
        # disk, not the broken one.
        on_disk = (root / "test.py").read_text(encoding="utf-8")
        assert on_disk == _FIXED_CODE, f"Agent did not fix the code: {on_disk!r}"
        # The classified failure was serialized back into the conversation
        # on retry, which is the mechanism that drives the correction.
        assert result.final_context is not None
        assert any(
            message.content.startswith("[FEEDBACK]")
            for message in result.final_context.messages
        )

        print(f"PASS: Feedback drove correction over {result.rounds} rounds")
        print(f"     LLM was called {mock_llm.call_count} times")
        print("     Agent changed test.py to the fixed implementation after feedback")


# ---------------------------------------------------------------------------
# Demo 3: FeedbackClassifier deep dimension behavior
# ---------------------------------------------------------------------------


def demo_feedback_classifier_precision() -> None:
    """Demonstrate: precise failure classification, strategies, escalation."""
    classifier = FailureClassifier()

    # Test 1: SyntaxError from a pytest traceback — the classifier must
    # extract file + line from the DEEPEST traceback frame.
    syntax_result = ToolResult(
        action_id="a1",
        success=False,
        output="",
        error=(
            "Traceback (most recent call last):\n"
            '  File "/usr/local/lib/python3.12/site-packages/_pytest/runner.py", line 341, in <module>\n'
            '  File "test.py", line 15, in <module>\n'
            "SyntaxError: invalid syntax"
        ),
        exit_code=1,
        duration_ms=100,
    )
    failures = classifier.classify(syntax_result)
    assert len(failures) == 1, f"Expected 1 failure, got {failures}"
    assert failures[0].category == FailureCategory.SYNTAX_ERROR
    assert failures[0].file == "test.py"
    assert failures[0].line == 15
    print("PASS: Correctly classified SyntaxError at test.py:15")

    # Test 2: Assertion failure from pytest's structured FAILED line.
    assert_result = ToolResult(
        action_id="a2",
        success=False,
        output="FAILED tests/test_math.py::test_add - AssertionError: assert 3 == 5",
        error="",
        exit_code=1,
        duration_ms=200,
    )
    failures = classifier.classify(assert_result)
    assert len(failures) >= 1
    assert failures[0].category == FailureCategory.ASSERTION_FAILURE
    # The pytest FAILED line yields a structured failure carrying the
    # failing test file (the nodeid's module path, ::test_add included
    # in the message).
    assert any(f.file == "tests/test_math.py" for f in failures)
    assert any("assert 3 == 5" in f.message for f in failures)
    print("PASS: Correctly classified AssertionError (with failing test file)")

    # Test 3: All-pass result — no failures.
    pass_result = ToolResult(
        action_id="a3",
        success=True,
        output="4 passed in 0.5s",
        error="",
        exit_code=0,
        duration_ms=500,
    )
    failures = classifier.classify(pass_result)
    assert len(failures) == 0
    print("PASS: Correctly identified all-pass (no failures)")

    # Test 4: Strategy selection — all 9 failure categories have guidance.
    selector = StrategySelector()
    assert len(tuple(FailureCategory)) == 9
    for category in FailureCategory:
        strategy = selector.select(ClassifiedFailure(category=category))
        assert strategy.guidance, f"No guidance for {category}"
    print("PASS: All 9 failure categories have a strategy")

    # Test 5: Loop controller — retry below the threshold, escalate at it.
    controller = LoopController()
    cfg = FeedbackConfig(max_correction_rounds=5, max_same_error=3)
    same_failure = ClassifiedFailure(
        category=FailureCategory.SYNTAX_ERROR, file="test.py", message="bad syntax"
    )
    history = [
        CorrectionRecord(
            round_id=round_num,
            failures_before=[same_failure],
            files_touched={"test.py"},
        )
        for round_num in (1, 2, 3)
    ]

    # Two prior rounds of the same error: still retrying.
    decision = controller.decide([same_failure], 3, history[:2], cfg)
    assert decision.action == "retry", f"Expected retry, got {decision.action}"
    # Three consecutive same-category errors: escalate to a human.
    decision = controller.decide([same_failure], 4, history, cfg)
    assert decision.action == "escalate", f"Expected escalate, got {decision.action}"
    print("PASS: Loop controller escalates after 3 consecutive same-category errors")

    print("\n=== All mechanism demonstrations PASSED ===")


# ---------------------------------------------------------------------------
# pytest entry points (wrap the demos as test functions)
# ---------------------------------------------------------------------------


def test_demo_guard_intercepts_dangerous_action() -> None:
    """pytest wrapper: guard demo."""
    demo_guard_intercepts_dangerous_action()


async def test_demo_feedback_drives_correction() -> None:
    """pytest wrapper: feedback loop demo."""
    await demo_feedback_drives_correction()


def test_demo_feedback_classifier_precision() -> None:
    """pytest wrapper: classifier precision demo."""
    demo_feedback_classifier_precision()


# ---------------------------------------------------------------------------
# Direct execution: python tests/demo_mechanisms.py
# ---------------------------------------------------------------------------


def main() -> None:
    """Run all three demos; exit non-zero on the first failure."""
    demo_guard_intercepts_dangerous_action()
    asyncio.run(demo_feedback_drives_correction())
    demo_feedback_classifier_precision()


if __name__ == "__main__":
    main()
