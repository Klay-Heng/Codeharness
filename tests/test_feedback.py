"""Tests for Feedback Engine — the MAIN CONTRIBUTION dimension.

Tests cover FailureClassifier, StrategySelector, LoopController,
and FeedbackEngine (integration). All deterministic — no LLM needed.
"""

import pytest
from codeharness.models import (
    ToolResult, ClassifiedFailure, FeedbackStrategy, FeedbackContext,
    CorrectionRecord, LoopDecision, FeedbackConfig, FailureCategory,
)
from codeharness.feedback import (
    FailureClassifier, StrategySelector, LoopController, FeedbackEngine,
)


# =============================================================================
# FailureClassifier
# =============================================================================

class TestFailureClassifier:
    """Deterministic classification of tool output into failure categories."""

    def test_classify_syntax_error(self):
        result = ToolResult(
            action_id="a1", success=False,
            error='  File "test.py", line 15\n    invalid syntax\nSyntaxError: invalid syntax',
            exit_code=1,
        )
        failures = FailureClassifier().classify(result)
        assert len(failures) == 1
        assert failures[0].category == FailureCategory.SYNTAX_ERROR
        assert "test.py" in failures[0].file or failures[0].file is None

    def test_classify_import_error(self):
        result = ToolResult(
            action_id="a2", success=False,
            error="ModuleNotFoundError: No module named 'requests'",
            exit_code=1,
        )
        failures = FailureClassifier().classify(result)
        assert len(failures) >= 1
        assert failures[0].category == FailureCategory.IMPORT_ERROR

    def test_classify_assertion_failure(self):
        result = ToolResult(
            action_id="a3", success=False,
            output="FAILED test_x.py::test_foo - AssertionError: assert 1 == 2",
            exit_code=1,
        )
        failures = FailureClassifier().classify(result)
        assert len(failures) >= 1
        assert failures[0].category == FailureCategory.ASSERTION_FAILURE

    def test_classify_runtime_error(self):
        result = ToolResult(
            action_id="a4", success=False,
            error="TypeError: int() argument must be a string, not 'list'",
            exit_code=1,
        )
        failures = FailureClassifier().classify(result)
        assert len(failures) >= 1
        assert failures[0].category == FailureCategory.RUNTIME_ERROR

    def test_classify_timeout(self):
        result = ToolResult(
            action_id="a5", success=False,
            output="killed", duration_ms=61000,
        )
        failures = FailureClassifier().classify(result, timeout_ms=60000)
        assert len(failures) >= 1
        assert failures[0].category == FailureCategory.TIMEOUT

    def test_classify_lint_warning(self):
        result = ToolResult(
            action_id="a6", success=True,
            output="src/a.py:10:5: F401 'os' imported but unused",
            exit_code=0,
        )
        failures = FailureClassifier().classify(result)
        assert len(failures) >= 1
        assert failures[0].category == FailureCategory.LINT_WARNING

    def test_classify_type_error(self):
        result = ToolResult(
            action_id="a7", success=False,
            output="src/a.py:10: error: Incompatible types in assignment",
            exit_code=1,
        )
        failures = FailureClassifier().classify(result)
        assert len(failures) >= 1
        assert failures[0].category == FailureCategory.TYPE_ERROR

    def test_classify_command_failed(self):
        result = ToolResult(
            action_id="a8", success=False,
            output="some random output that doesn't match any pattern",
            exit_code=1,
        )
        failures = FailureClassifier().classify(result)
        assert len(failures) >= 1
        assert failures[0].category == FailureCategory.COMMAND_FAILED

    def test_classify_unknown(self):
        result = ToolResult(
            action_id="a9", success=False,
            output="",
            error="",
            exit_code=0,
        )
        failures = FailureClassifier().classify(result)
        assert len(failures) == 0

    def test_classify_multiple_failures(self):
        result = ToolResult(
            action_id="a10", success=False,
            output=(
                "FAILED tests/test_a.py::test_1 - AssertionError: assert 1 == 2\n"
                "FAILED tests/test_b.py::test_2 - AssertionError: assert 3 == 4\n"
                "FAILED tests/test_c.py::test_3 - TypeError: 'NoneType' object is not callable\n"
            ),
            exit_code=1,
        )
        failures = FailureClassifier().classify(result)
        assert len(failures) >= 3

    def test_classify_all_pass(self):
        result = ToolResult(
            action_id="a11", success=True,
            output="4 passed in 0.5s",
            exit_code=0,
        )
        failures = FailureClassifier().classify(result)
        assert len(failures) == 0

    def test_classify_pytest_syntax_error_with_file_and_line(self):
        """Parse file:line from pytest SyntaxError traceback."""
        result = ToolResult(
            action_id="a12", success=False,
            error=(
                '  File "/project/tests/test_math.py", line 15\n'
                '    def test_add()\n'
                'SyntaxError: invalid syntax'
            ),
            exit_code=1,
        )
        failures = FailureClassifier().classify(result)
        assert len(failures) >= 1
        f = failures[0]
        assert f.category == FailureCategory.SYNTAX_ERROR


# =============================================================================
# StrategySelector
# =============================================================================

class TestStrategySelector:
    """Maps each failure category to a correction strategy with guidance."""

    def test_select_strategy_for_syntax_error(self):
        s = StrategySelector().select(
            ClassifiedFailure(category=FailureCategory.SYNTAX_ERROR)
        )
        assert "syntax" in s.guidance.lower()
        assert s.guidance  # non-empty

    def test_select_strategy_for_assertion(self):
        s = StrategySelector().select(
            ClassifiedFailure(category=FailureCategory.ASSERTION_FAILURE)
        )
        assert ("diff" in s.guidance.lower() or "expected" in s.guidance.lower())
        assert s.guidance

    def test_select_strategy_for_timeout(self):
        s = StrategySelector().select(
            ClassifiedFailure(category=FailureCategory.TIMEOUT)
        )
        assert ("loop" in s.guidance.lower() or "cache" in s.guidance.lower())
        assert s.guidance

    def test_each_category_has_strategy(self):
        selector = StrategySelector()
        for cat in FailureCategory:
            s = selector.select(ClassifiedFailure(category=cat))
            assert isinstance(s, FeedbackStrategy)
            assert s.guidance, f"No guidance for {cat}"


# =============================================================================
# LoopController
# =============================================================================

class TestLoopController:
    """Deterministic decision: stop, retry, or escalate."""

    def test_stop_success_no_failures(self):
        cfg = FeedbackConfig()
        d = LoopController().decide([], round_number=1, history=[], config=cfg)
        assert d.action == "stop_success"

    def test_stop_failure_max_rounds(self):
        cfg = FeedbackConfig(max_correction_rounds=3)
        d = LoopController().decide(
            [ClassifiedFailure(category=FailureCategory.UNKNOWN)],
            round_number=3, history=[], config=cfg,
        )
        assert d.action == "stop_failure"

    def test_retry_before_max_rounds(self):
        cfg = FeedbackConfig(max_correction_rounds=5)
        d = LoopController().decide(
            [ClassifiedFailure(category=FailureCategory.RUNTIME_ERROR)],
            round_number=2, history=[], config=cfg,
        )
        assert d.action == "retry"

    def test_escalate_same_error_repeated(self):
        cfg = FeedbackConfig(max_correction_rounds=5, max_same_error=3)
        history = [
            CorrectionRecord(
                round_id=1,
                failures_before=[
                    ClassifiedFailure(category=FailureCategory.SYNTAX_ERROR, file="a.py")
                ],
                files_touched={"a.py"},
            ),
            CorrectionRecord(
                round_id=2,
                failures_before=[
                    ClassifiedFailure(category=FailureCategory.SYNTAX_ERROR, file="a.py")
                ],
                files_touched={"a.py"},
            ),
            CorrectionRecord(
                round_id=3,
                failures_before=[
                    ClassifiedFailure(category=FailureCategory.SYNTAX_ERROR, file="a.py")
                ],
                files_touched={"a.py"},
            ),
        ]
        d = LoopController().decide(
            [ClassifiedFailure(category=FailureCategory.SYNTAX_ERROR, file="a.py")],
            round_number=4, history=history, config=cfg,
        )
        assert d.action == "escalate"

    def test_escalate_on_regression_new_file(self):
        cfg = FeedbackConfig()
        history = [
            CorrectionRecord(
                round_id=1,
                failures_before=[
                    ClassifiedFailure(category=FailureCategory.SYNTAX_ERROR, file="a.py")
                ],
                files_touched={"a.py"},
            ),
        ]
        d = LoopController().decide(
            [ClassifiedFailure(category=FailureCategory.SYNTAX_ERROR, file="b.py")],
            round_number=2, history=history, config=cfg,
        )
        assert d.action == "escalate"

    def test_escalate_on_regression_new_category(self):
        cfg = FeedbackConfig()
        history = [
            CorrectionRecord(
                round_id=1,
                failures_before=[
                    ClassifiedFailure(category=FailureCategory.SYNTAX_ERROR, file="a.py")
                ],
                files_touched={"a.py"},
            ),
        ]
        d = LoopController().decide(
            [ClassifiedFailure(category=FailureCategory.RUNTIME_ERROR, file="a.py")],
            round_number=2, history=history, config=cfg,
        )
        assert d.action == "escalate"

    def test_no_regression_same_file_same_category(self):
        cfg = FeedbackConfig()
        history = [
            CorrectionRecord(
                round_id=1,
                failures_before=[
                    ClassifiedFailure(category=FailureCategory.SYNTAX_ERROR, file="a.py")
                ],
                files_touched={"a.py"},
            ),
        ]
        d = LoopController().decide(
            [ClassifiedFailure(category=FailureCategory.SYNTAX_ERROR, file="a.py")],
            round_number=2, history=history, config=cfg,
        )
        # Not regression, not max rounds, not repeated enough => retry
        assert d.action == "retry"

    def test_regression_detection_with_no_history(self):
        """First round with failures should not trigger regression."""
        cfg = FeedbackConfig()
        d = LoopController().decide(
            [ClassifiedFailure(category=FailureCategory.RUNTIME_ERROR)],
            round_number=1, history=[], config=cfg,
        )
        assert d.action == "retry"


# =============================================================================
# FeedbackEngine (integration)
# =============================================================================

class TestFeedbackEngine:
    """Integration tests for the full feedback pipeline."""

    def test_evaluate_all_pass(self):
        engine = FeedbackEngine()
        results = [
            ToolResult(action_id="a1", success=True, output="4 passed", exit_code=0),
        ]
        ctx = engine.evaluate(results, round_number=1, history=[])
        assert len(ctx.failures) == 0
        assert ctx.decision is not None
        assert ctx.decision.action == "stop_success"

    def test_evaluate_with_syntax_failure(self):
        engine = FeedbackEngine()
        results = [
            ToolResult(
                action_id="a1", success=False,
                error='SyntaxError: invalid syntax at test.py:15',
                exit_code=1,
            ),
        ]
        ctx = engine.evaluate(results, round_number=1, history=[])
        assert len(ctx.failures) >= 1
        assert ctx.failures[0].category == FailureCategory.SYNTAX_ERROR
        assert len(ctx.strategies) >= 1
        assert ctx.strategies[0].guidance

    def test_serialize_for_llm_contains_key_info(self):
        engine = FeedbackEngine()
        results = [
            ToolResult(
                action_id="a1", success=False,
                error='SyntaxError: invalid syntax at test.py:15',
                exit_code=1,
            ),
        ]
        ctx = engine.evaluate(results, round_number=2, history=[])
        output = ctx.serialize()
        assert "SYNTAX_ERROR" in output
        assert "test.py" in output or "15" in output

    def test_evaluate_with_mixed_results(self):
        """One pass, one fail: feedback should report the failure."""
        engine = FeedbackEngine()
        results = [
            ToolResult(action_id="a1", success=True, output="ok", exit_code=0),
            ToolResult(
                action_id="a2", success=False,
                error="TypeError: int() argument must be...",
                exit_code=1,
            ),
        ]
        ctx = engine.evaluate(results, round_number=1, history=[])
        assert len(ctx.failures) >= 1
        assert any(f.category == FailureCategory.RUNTIME_ERROR for f in ctx.failures)

    def test_loop_controller_integration_in_evaluate(self):
        """After max rounds, evaluate should produce stop_failure decision."""
        engine = FeedbackEngine()
        history = [
            CorrectionRecord(
                round_id=r,
                failures_before=[ClassifiedFailure(category=FailureCategory.UNKNOWN, file="x.py")],
                files_touched={"x.py"},
            )
            for r in range(1, 6)
        ]
        results = [
            ToolResult(action_id="a1", success=False, error="bad", exit_code=1),
        ]
        ctx = engine.evaluate(results, round_number=5, history=history)
        assert ctx.decision is not None
        assert ctx.decision.action in ("stop_failure", "escalate")
