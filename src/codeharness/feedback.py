"""Feedback Engine — the MAIN CONTRIBUTION dimension of CodeHarness.

Deterministic, testable, code-based mechanisms for:
- FailureClassifier: parse tool output into categorized failures
- StrategySelector: map each failure category to a correction strategy
- LoopController: decide stop/retry/escalate per round
- FeedbackEngine: orchestrate classification + strategy + control

All components are pure functions of their inputs — no LLM, no network,
no randomness.  Every mechanism is verifiable with mock ToolResult data.
"""

from __future__ import annotations

import re
from typing import ClassVar

from codeharness.models import (
    ClassifiedFailure,
    CorrectionRecord,
    FailureCategory,
    FeedbackConfig,
    FeedbackContext,
    FeedbackStrategy,
    LoopDecision,
    ToolResult,
)

# ---------------------------------------------------------------------------
# FailureClassifier — deterministic regex-based classification (SPEC 3.5)
# ---------------------------------------------------------------------------

# Regex patterns per failure category. Each pattern extracts file/line/message
# where available from tool output text.
# fmt: off
_PATTERNS: list[tuple[FailureCategory, str]] = [
    (FailureCategory.SYNTAX_ERROR,  r'SyntaxError:\s*(.+)'),
    (FailureCategory.IMPORT_ERROR,  r'(?:ModuleNotFoundError|ImportError):\s*(.+)'),
    (FailureCategory.ASSERTION_FAILURE, r'AssertionError:\s*(.+)'),
    (FailureCategory.RUNTIME_ERROR, r'(?<!Assertion)(?<!Syntax)Error\b[^:]*:\s*(.+)'),
    (FailureCategory.TYPE_ERROR,    r'(?:Incompatible types|type error).*'),
    (FailureCategory.LINT_WARNING,  r'^(.+?):(\d+):(\d+):\s*(\w+\d+)\s+(.+)'),
]
# fmt: on

# File + line extraction (pytest / traceback style)
_FILE_LINE_PAT = re.compile(r'File\s+"(.+?)",\s*line\s+(\d+)', re.MULTILINE)

# Pytest assertion output
_PYTEST_FAIL_PAT = re.compile(r'FAILED\s+(\S+?)(?:::\S+)?\s+-\s*(.+)')

# mypy error pattern
_MYPY_PAT = re.compile(r'^(.+?):(\d+):\s*error:\s*(.+)', re.MULTILINE)

# ruff lint pattern
_RUFF_PAT = re.compile(r'^(.+?):(\d+):(\d+):\s*(\w+)\s+(.+)', re.MULTILINE)


class FailureClassifier:
    """Deterministic classifier: tool output -> list of ClassifiedFailure.

    Tests: tests/test_feedback.py::TestFailureClassifier (12 tests).
    """

    # Compiled per-category regexes, built once at import time.
    _COMPILED: ClassVar[list[tuple[FailureCategory, re.Pattern[str]]]] = [
        (cat, re.compile(pat, re.MULTILINE | re.IGNORECASE))
        for cat, pat in _PATTERNS
    ]

    def classify(
        self, result: ToolResult, timeout_ms: int | None = None
    ) -> list[ClassifiedFailure]:
        """Classify all failures present in a ToolResult.

        Args:
            result: The tool execution result to analyze.
            timeout_ms: Optional timeout threshold for TIMEOUT classification.

        Returns:
            A (possibly empty) list of classified failures.
        """
        # Combine output + error for analysis
        text = result.output or ""
        if result.error:
            text = (text + "\n" + result.error).strip()

        failures: list[ClassifiedFailure] = []

        # 1. Timeout check (duration-based, not text-based)
        if timeout_ms is not None and result.duration_ms > timeout_ms:
            failures.append(ClassifiedFailure(
                category=FailureCategory.TIMEOUT,
                message=f"Execution exceeded {timeout_ms}ms (took {result.duration_ms}ms)",
                raw_output=text,
            ))

        # 2. Lint warnings (ruff format: file:line:col: CODE message)
        for m in _RUFF_PAT.finditer(text):
            failures.append(ClassifiedFailure(
                category=FailureCategory.LINT_WARNING,
                file=m.group(1),
                line=int(m.group(2)),
                message=f"{m.group(4)} {m.group(5)}",
                raw_output=text,
            ))

        # 3. mypy type errors (file:line: error: message)
        for m in _MYPY_PAT.finditer(text):
            failures.append(ClassifiedFailure(
                category=FailureCategory.TYPE_ERROR,
                file=m.group(1),
                line=int(m.group(2)),
                message=m.group(3).strip(),
                raw_output=text,
            ))

        # 4. SyntaxError (with file/line from traceback)
        for cat, pat in self._COMPILED:
            if cat in (FailureCategory.LINT_WARNING, FailureCategory.TYPE_ERROR):
                continue  # handled above with structured parsing
            match = pat.search(text)
            if match:
                f = ClassifiedFailure(
                    category=cat,
                    message=match.group(1).strip() if match.lastindex else text[:200],
                    raw_output=text,
                )
                # Extract file:line from traceback
                fl_match = _FILE_LINE_PAT.search(text)
                if fl_match:
                    f.file = fl_match.group(1)
                    f.line = int(fl_match.group(2))
                failures.append(f)

        # 5. Pytest assertion failures (structured output)
        for m in _PYTEST_FAIL_PAT.finditer(text):
            filename = m.group(1)
            msg = m.group(2).strip()
            # Only add if not already found via AssertionError pattern
            if not any(
                fe.category == FailureCategory.ASSERTION_FAILURE and fe.file == filename
                for fe in failures
            ):
                # Determine if this is assertion or runtime
                if "AssertionError" in msg:
                    cat = FailureCategory.ASSERTION_FAILURE
                elif "Error" in msg:
                    cat = FailureCategory.RUNTIME_ERROR
                else:
                    cat = FailureCategory.UNKNOWN
                failures.append(ClassifiedFailure(
                    category=cat, file=filename, message=msg, raw_output=text,
                ))

        # 6. Command failed fallback: exit_code != 0 but nothing matched
        if not failures and result.exit_code and result.exit_code != 0:
            failures.append(ClassifiedFailure(
                category=FailureCategory.COMMAND_FAILED,
                message=text[:200] or f"Exit code {result.exit_code}",
                raw_output=text,
            ))

        return failures


# ---------------------------------------------------------------------------
# StrategySelector — category -> strategy lookup (SPEC 3.5)
# ---------------------------------------------------------------------------

_STRATEGY_GUIDANCE: dict[FailureCategory, str] = {
    FailureCategory.SYNTAX_ERROR: (
        "Fix syntax only, do not change logic. Check the exact error "
        "message and line number."
    ),
    FailureCategory.IMPORT_ERROR: (
        "Check import paths or add missing dependency. Verify the module "
        "name is correct and installed."
    ),
    FailureCategory.ASSERTION_FAILURE: (
        "Analyze the diff between expected and actual values. Fix the "
        "logic so the function returns correct output."
    ),
    FailureCategory.RUNTIME_ERROR: (
        "Locate root cause of the runtime error, add defensive checks "
        "(type validation, None guards) to prevent it."
    ),
    FailureCategory.TIMEOUT: (
        "Check for infinite loops, excessive recursion, or missing "
        "caching. Consider adding a timeout or optimizing the algorithm."
    ),
    FailureCategory.LINT_WARNING: (
        "Fix only the lint issue, do not change logic. Follow the "
        "linter's suggested fix."
    ),
    FailureCategory.TYPE_ERROR: (
        "Fix type annotation or usage to match the expected types. "
        "Check the mypy output for the specific mismatch."
    ),
    FailureCategory.COMMAND_FAILED: (
        "Check command syntax and prerequisites. Verify the command "
        "exists and all required arguments are provided."
    ),
    FailureCategory.UNKNOWN: (
        "Investigate the error output and determine root cause. "
        "Read error messages carefully and trace the issue."
    ),
}


class StrategySelector:
    """Maps each failure category to a deterministic guidance strategy.

    Tests: tests/test_feedback.py::TestStrategySelector (4 tests).
    """

    def select(self, failure: ClassifiedFailure) -> FeedbackStrategy:
        """Return the strategy for a given failure, looked up by category."""
        guidance = _STRATEGY_GUIDANCE.get(
            failure.category,
            _STRATEGY_GUIDANCE[FailureCategory.UNKNOWN],
        )
        return FeedbackStrategy(
            category=failure.category,
            context_template=failure.message or "",
            guidance=guidance,
        )


# ---------------------------------------------------------------------------
# LoopController — deterministic loop decision (SPEC 3.5)
# ---------------------------------------------------------------------------

class LoopController:
    """Decides stop/retry/escalate based on failures, history, and config.

    Tests: tests/test_feedback.py::TestLoopController (9 tests).
    """

    def decide(
        self,
        failures: list[ClassifiedFailure],
        round_number: int,
        history: list[CorrectionRecord],
        config: FeedbackConfig,
    ) -> LoopDecision:
        """Determine the next action for the feedback loop.

        Decision priority (from SPEC 3.5):
        1. No failures -> stop_success
        2. round >= max_correction_rounds -> stop_failure
        3. Consecutive same-category >= max_same_error -> escalate
        4. Regression (new file or new category) -> escalate
        5. Otherwise -> retry
        """
        # Rule 1: no failures = success
        if not failures:
            return LoopDecision(
                action="stop_success",
                reason="All checks passed",
                round_number=round_number,
            )

        # Rule 2: hit max rounds
        if round_number >= config.max_correction_rounds:
            return LoopDecision(
                action="stop_failure",
                reason=f"Reached max correction rounds ({config.max_correction_rounds})",
                round_number=round_number,
            )

        # Rule 3: consecutive same-category failures
        if self._consecutive_same_category(failures, history, config.max_same_error):
            cat = failures[0].category.value
            return LoopDecision(
                action="escalate",
                reason=f"Same category '{cat}' failed {config.max_same_error}+ consecutive times",
                round_number=round_number,
            )

        # Rule 4: regression detection
        if self._detected_regression(failures, history):
            return LoopDecision(
                action="escalate",
                reason="Regression detected: new file or new failure category",
                round_number=round_number,
            )

        # Rule 5: default — keep trying
        return LoopDecision(
            action="retry",
            reason=f"Retrying correction ({round_number}/{config.max_correction_rounds})",
            round_number=round_number,
        )

    def _consecutive_same_category(
        self,
        failures: list[ClassifiedFailure],
        history: list[CorrectionRecord],
        max_same: int,
    ) -> bool:
        """Check if the last N rounds all had the same failure category."""
        if len(history) < max_same:
            return False

        current_categories = {f.category for f in failures}
        # Look at the last max_same records
        recent = history[-max_same:]
        for record in recent:
            record_categories = {f.category for f in record.failures_before}
            # Each recent round must have had the same single category
            # as one of the current failure categories
            if not (record_categories & current_categories):
                return False
        return True

    def _detected_regression(
        self,
        failures: list[ClassifiedFailure],
        history: list[CorrectionRecord],
    ) -> bool:
        """Detect if new failures appear in previously untouched files or categories."""
        if not history:
            return False

        # Collect all files and categories seen in previous rounds
        seen_files: set[str] = set()
        seen_categories: set[FailureCategory] = set()
        for record in history:
            seen_files |= record.files_touched
            for f in record.failures_before:
                seen_categories.add(f.category)

        # Check if any current failure is in a new file or new category
        for failure in failures:
            if failure.file and failure.file not in seen_files:
                return True
            if failure.category not in seen_categories:
                return True

        return False


# ---------------------------------------------------------------------------
# FeedbackEngine — orchestrator (SPEC 3.5)
# ---------------------------------------------------------------------------

class FeedbackEngine:
    """Orchestrates classification, strategy selection, and loop control.

    This is the single entry-point the AgentLoop calls after each round
    of tool execution.

    Tests: tests/test_feedback.py::TestFeedbackEngine (5 tests).
    """

    def __init__(self, config: FeedbackConfig | None = None):
        self.config = config or FeedbackConfig()
        self.classifier = FailureClassifier()
        self.selector = StrategySelector()
        self.controller = LoopController()

    def evaluate(
        self,
        results: list[ToolResult],
        round_number: int,
        history: list[CorrectionRecord],
    ) -> FeedbackContext:
        """Classify results, select strategies, decide next action.

        Args:
            results: ToolResults from the most recent round.
            round_number: Current correction round (1-indexed).
            history: Prior CorrectionRecords for regression tracking.

        Returns:
            A FeedbackContext with classified failures, strategies, and decision.
        """
        all_failures: list[ClassifiedFailure] = []
        for result in results:
            all_failures.extend(self.classifier.classify(result))

        strategies = [self.selector.select(f) for f in all_failures]
        decision = self.controller.decide(
            all_failures, round_number, history, self.config,
        )

        return FeedbackContext(
            round_id=round_number,
            failures=all_failures,
            strategies=strategies,
            decision=decision,
        )
