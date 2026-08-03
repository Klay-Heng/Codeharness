"""Tests for core data models, enums, and protocols (src/codeharness/models.py).

Covers SPEC section 6.1 entities plus the configuration dataclasses
(SPEC section 3.7) and the LLMBackend / Tool protocols (SPEC 5.3, 6.1).
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from codeharness.models import (
    Action,
    ClassifiedFailure,
    Config,
    CorrectionRecord,
    FailureCategory,
    FeedbackConfig,
    FeedbackContext,
    FeedbackStrategy,
    GuardConfig,
    GuardVerdict,
    LLMBackend,
    LLMConfig,
    LLMResponse,
    LoopDecision,
    MemoryConfig,
    Message,
    ProjectConfig,
    RiskLevel,
    RunResult,
    Tool,
    ToolResult,
    ToolsConfig,
    TurnContext,
)

# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------

class TestMessage:
    def test_create_system_message(self):
        msg = Message(role="system", content="You are a coding agent.")
        assert msg.role == "system"
        assert msg.content == "You are a coding agent."

    def test_default_tool_call_id_is_none(self):
        msg = Message(role="user", content="hello")
        assert msg.tool_call_id is None

    def test_tool_call_id_set(self):
        msg = Message(role="tool", content="ok", tool_call_id="call_1")
        assert msg.tool_call_id == "call_1"

    def test_all_roles_accepted(self):
        for role in ("system", "user", "assistant", "tool"):
            msg = Message(role=role, content="x")
            assert msg.role == role

    def test_has_timestamp(self):
        msg = Message(role="user", content="x")
        assert isinstance(msg.timestamp, datetime)

    def test_timestamps_are_independent(self):
        msg1 = Message(role="user", content="x")
        msg2 = Message(role="user", content="y")
        assert msg1 is not msg2


# ---------------------------------------------------------------------------
# Action
# ---------------------------------------------------------------------------

class TestAction:
    def test_auto_generated_uuid(self):
        action = Action(tool="read_file", params={"path": "src/main.py"})
        assert action.action_id
        # Must be a valid UUID string
        assert uuid.UUID(action.action_id)

    def test_action_ids_are_unique(self):
        a1 = Action(tool="read_file", params={})
        a2 = Action(tool="read_file", params={})
        assert a1.action_id != a2.action_id

    def test_explicit_action_id_respected(self):
        action = Action(tool="write_file", params={}, action_id="custom-id")
        assert action.action_id == "custom-id"

    def test_params_stored(self):
        params = {"path": "a.py", "content": "x = 1"}
        action = Action(tool="write_file", params=params)
        assert action.params == params
        assert action.tool == "write_file"


# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------

class TestToolResult:
    def test_defaults(self):
        result = ToolResult(action_id="a1", success=True)
        assert result.output == ""
        assert result.error is None
        assert result.exit_code is None
        assert result.duration_ms == 0

    def test_success_result(self):
        result = ToolResult(action_id="a1", success=True, output="4 passed in 0.5s", exit_code=0, duration_ms=500)
        assert result.success is True
        assert result.output == "4 passed in 0.5s"
        assert result.exit_code == 0

    def test_failure_result(self):
        result = ToolResult(
            action_id="a2",
            success=False,
            output="FAILED tests/test_math.py",
            error="AssertionError: assert 1 == 2",
            exit_code=1,
            duration_ms=250,
        )
        assert result.success is False
        assert result.error == "AssertionError: assert 1 == 2"
        assert result.exit_code == 1
        assert result.duration_ms == 250

    def test_duration_ms_records_timeout(self):
        result = ToolResult(action_id="a3", success=False, duration_ms=61000)
        assert result.duration_ms >= 60000


# ---------------------------------------------------------------------------
# LLMResponse
# ---------------------------------------------------------------------------

class TestLLMResponse:
    def test_defaults(self):
        response = LLMResponse()
        assert response.content == ""
        assert response.tool_calls == []
        assert response.finish_reason == "stop"

    def test_with_tool_calls(self):
        response = LLMResponse(
            content="I'll read the file.",
            tool_calls=[{"name": "read_file", "params": {"path": "a.py"}}],
            finish_reason="tool_calls",
        )
        assert response.tool_calls[0]["name"] == "read_file"
        assert response.finish_reason == "tool_calls"

    def test_tool_calls_not_shared_between_instances(self):
        r1 = LLMResponse()
        r2 = LLMResponse()
        r1.tool_calls.append({"name": "x", "params": {}})
        assert r2.tool_calls == []


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TestFailureCategory:
    def test_has_all_nine_categories(self):
        expected = {
            "SYNTAX_ERROR",
            "IMPORT_ERROR",
            "ASSERTION_FAILURE",
            "RUNTIME_ERROR",
            "TIMEOUT",
            "LINT_WARNING",
            "TYPE_ERROR",
            "COMMAND_FAILED",
            "UNKNOWN",
        }
        assert {c.name for c in FailureCategory} == expected

    def test_str_enum_values(self):
        assert FailureCategory.SYNTAX_ERROR.value == "syntax"
        assert FailureCategory.IMPORT_ERROR.value == "import"
        assert FailureCategory.ASSERTION_FAILURE.value == "assert"
        assert FailureCategory.RUNTIME_ERROR.value == "runtime"
        assert FailureCategory.TIMEOUT.value == "timeout"
        assert FailureCategory.LINT_WARNING.value == "lint"
        assert FailureCategory.TYPE_ERROR.value == "type"
        assert FailureCategory.COMMAND_FAILED.value == "command"
        assert FailureCategory.UNKNOWN.value == "unknown"

    def test_is_str_enum(self):
        assert isinstance(FailureCategory.UNKNOWN, str)
        assert str(FailureCategory.UNKNOWN) == "FailureCategory.UNKNOWN"


class TestRiskLevel:
    def test_members(self):
        assert {r.name for r in RiskLevel} == {"LOW", "MEDIUM", "HIGH"}

    def test_str_enum_values(self):
        assert RiskLevel.LOW.value == "low"
        assert RiskLevel.MEDIUM.value == "medium"
        assert RiskLevel.HIGH.value == "high"

    def test_ordering(self):
        # HIGH is more dangerous than MEDIUM, which is more than LOW.
        danger = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
        assert danger[RiskLevel.HIGH] > danger[RiskLevel.MEDIUM] > danger[RiskLevel.LOW]


class TestGuardVerdict:
    def test_members(self):
        assert {v.name for v in GuardVerdict} == {"ALLOW", "ASK_ONCE", "ASK_ALWAYS"}

    def test_str_enum_values(self):
        assert GuardVerdict.ALLOW.value == "allow"
        assert GuardVerdict.ASK_ONCE.value == "ask_once"
        assert GuardVerdict.ASK_ALWAYS.value == "ask_always"


# ---------------------------------------------------------------------------
# ClassifiedFailure / FeedbackStrategy
# ---------------------------------------------------------------------------

class TestClassifiedFailure:
    def test_fields(self):
        failure = ClassifiedFailure(
            category=FailureCategory.SYNTAX_ERROR,
            file="test.py",
            line=15,
            message="invalid syntax",
            raw_output="SyntaxError: invalid syntax at test.py:15",
        )
        assert failure.category == FailureCategory.SYNTAX_ERROR
        assert failure.file == "test.py"
        assert failure.line == 15
        assert failure.message == "invalid syntax"
        assert failure.raw_output == "SyntaxError: invalid syntax at test.py:15"

    def test_defaults(self):
        failure = ClassifiedFailure(category=FailureCategory.UNKNOWN)
        assert failure.file is None
        assert failure.line is None
        assert failure.message == ""
        assert failure.raw_output == ""


class TestFeedbackStrategy:
    def test_fields(self):
        strategy = FeedbackStrategy(
            category=FailureCategory.SYNTAX_ERROR,
            context_template="File: {file}:{line}\n{message}",
            guidance="Fix syntax only, do not change logic",
        )
        assert strategy.category == FailureCategory.SYNTAX_ERROR
        assert strategy.context_template == "File: {file}:{line}\n{message}"
        assert strategy.guidance == "Fix syntax only, do not change logic"

    def test_defaults(self):
        strategy = FeedbackStrategy(category=FailureCategory.TIMEOUT)
        assert strategy.context_template == ""
        assert strategy.guidance == ""


# ---------------------------------------------------------------------------
# FeedbackContext
# ---------------------------------------------------------------------------

class TestFeedbackContext:
    def test_defaults(self):
        ctx = FeedbackContext(round_id=1)
        assert ctx.round_id == 1
        assert ctx.failures == []
        assert ctx.strategies == []
        assert ctx.decision is None

    def test_failures_not_shared_between_instances(self):
        ctx1 = FeedbackContext(round_id=1)
        ctx2 = FeedbackContext(round_id=2)
        ctx1.failures.append(ClassifiedFailure(category=FailureCategory.UNKNOWN))
        assert ctx2.failures == []

    def test_serialize_empty(self):
        ctx = FeedbackContext(round_id=1)
        assert ctx.serialize() == "[FEEDBACK] Round 1 | 0 failure(s)"

    def test_serialize_contains_category_file_line_strategy(self):
        failure = ClassifiedFailure(
            category=FailureCategory.ASSERTION_FAILURE,
            file="tests/test_utils.py",
            line=15,
            message="assert 3 == 5",
        )
        strategy = FeedbackStrategy(
            category=FailureCategory.ASSERTION_FAILURE,
            guidance="Analyze the diff between expected and actual, fix logic",
        )
        ctx = FeedbackContext(round_id=2, failures=[failure], strategies=[strategy])
        text = ctx.serialize()
        assert "[FEEDBACK] Round 2 | 1 failure(s)" in text
        assert "[ASSERTION_FAILURE]" in text
        assert "tests/test_utils.py:15" in text
        assert "assert 3 == 5" in text
        assert "Strategy: Analyze the diff between expected and actual, fix logic" in text

    def test_serialize_pairs_strategy_by_category_not_position(self):
        # Strategies are ordered differently from failures; guidance must
        # still attach to the right failure via category lookup.
        syntax_failure = ClassifiedFailure(
            category=FailureCategory.SYNTAX_ERROR,
            file="test.py",
            line=3,
            message="invalid syntax",
        )
        timeout_failure = ClassifiedFailure(
            category=FailureCategory.TIMEOUT,
            file="slow.py",
            message="took too long",
        )
        syntax_strategy = FeedbackStrategy(
            category=FailureCategory.SYNTAX_ERROR,
            guidance="Fix syntax only, do not change logic",
        )
        timeout_strategy = FeedbackStrategy(
            category=FailureCategory.TIMEOUT,
            guidance="Check for infinite loops or add caching",
        )
        # Strategies in reverse order relative to failures.
        ctx = FeedbackContext(
            round_id=3,
            failures=[syntax_failure, timeout_failure],
            strategies=[timeout_strategy, syntax_strategy],
        )
        text = ctx.serialize()
        syntax_block = text.split("test.py:3", 1)[1]
        assert "Fix syntax only, do not change logic" in syntax_block
        timeout_block = text.split("slow.py", 1)[1]
        assert "Check for infinite loops or add caching" in timeout_block

    def test_serialize_without_strategy_omits_guidance(self):
        failure = ClassifiedFailure(category=FailureCategory.UNKNOWN, message="weird")
        ctx = FeedbackContext(round_id=4, failures=[failure], strategies=[])
        text = ctx.serialize()
        assert "[UNKNOWN]" in text
        assert "Strategy:" not in text


# ---------------------------------------------------------------------------
# CorrectionRecord / LoopDecision / RunResult
# ---------------------------------------------------------------------------

class TestCorrectionRecord:
    def test_defaults(self):
        record = CorrectionRecord(round_id=1)
        assert record.round_id == 1
        assert record.action_ids == []
        assert record.failures_before == []
        assert record.failures_after == []
        assert record.files_touched == set()

    def test_files_touched_is_set(self):
        record = CorrectionRecord(round_id=1, files_touched={"a.py", "b.py"})
        assert record.files_touched == {"a.py", "b.py"}
        assert isinstance(record.files_touched, set)

    def test_lists_not_shared(self):
        r1 = CorrectionRecord(round_id=1)
        r2 = CorrectionRecord(round_id=2)
        r1.action_ids.append("a1")
        assert r2.action_ids == []


class TestLoopDecision:
    def test_defaults(self):
        decision = LoopDecision(action="retry")
        assert decision.action == "retry"
        assert decision.reason == ""
        assert decision.round_number == 0

    def test_full(self):
        decision = LoopDecision(action="escalate", reason="same error 3x", round_number=4)
        assert decision.action == "escalate"
        assert decision.reason == "same error 3x"
        assert decision.round_number == 4

    @pytest.mark.parametrize("action", ["retry", "stop_success", "stop_failure", "escalate"])
    def test_all_actions(self, action):
        assert LoopDecision(action=action).action == action


class TestRunResult:
    def test_defaults(self):
        result = RunResult(status="success", rounds=1)
        assert result.status == "success"
        assert result.rounds == 1
        assert result.duration_ms == 0
        assert result.final_context is None

    def test_full(self):
        context = TurnContext()
        result = RunResult(
            status="failure",
            rounds=3,
            duration_ms=1500,
            final_context=context,
        )
        assert result.status == "failure"
        assert result.rounds == 3
        assert result.duration_ms == 1500
        assert result.final_context is context

    @pytest.mark.parametrize("status", ["success", "failure", "max_rounds", "interrupted"])
    def test_all_statuses(self, status):
        assert RunResult(status=status, rounds=0).status == status


# ---------------------------------------------------------------------------
# TurnContext
# ---------------------------------------------------------------------------

class TestTurnContext:
    def test_defaults(self):
        ctx = TurnContext()
        assert ctx.messages == []
        assert ctx.round_count == 0
        assert ctx.last_results == []
        assert ctx.correction_history == []

    def test_add_message(self):
        ctx = TurnContext()
        msg = Message(role="user", content="hi")
        ctx.add_message(msg)
        assert ctx.messages == [msg]

    def test_add_result_increments_round_count(self):
        ctx = TurnContext()
        result = ToolResult(action_id="a1", success=True)
        ctx.add_result(result)
        assert ctx.round_count == 1
        assert ctx.last_results == [result]
        ctx.add_result(ToolResult(action_id="a2", success=False))
        assert ctx.round_count == 2
        assert len(ctx.last_results) == 2

    def test_mutable_defaults_are_independent(self):
        ctx1 = TurnContext()
        ctx2 = TurnContext()
        ctx1.add_message(Message(role="user", content="x"))
        assert ctx2.messages == []


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

class TestLLMConfig:
    def test_defaults(self):
        config = LLMConfig()
        assert config.provider == "deepseek"
        assert config.model == "deepseek-chat"
        assert config.api_base == "https://api.deepseek.com"
        assert config.max_tokens == 8192
        assert config.temperature == 0.0


class TestFeedbackConfig:
    def test_defaults(self):
        config = FeedbackConfig()
        assert config.max_correction_rounds == 5
        assert config.max_same_error == 3
        assert config.signal_sources == ["pytest", "ruff", "mypy"]
        assert config.test_command == "pytest tests/ -v --junitxml=report.xml"
        assert config.lint_command == "ruff check src/"

    def test_lists_not_shared(self):
        c1 = FeedbackConfig()
        c2 = FeedbackConfig()
        c1.signal_sources.append("coverage")
        assert c2.signal_sources == ["pytest", "ruff", "mypy"]


class TestGuardConfig:
    def test_defaults(self):
        config = GuardConfig()
        assert config.extra_dangerous_patterns == []
        assert config.session_approval is True
        assert config.project_root == "."
        assert config.allowed_dirs == ["src", "tests", "docs"]

    def test_dangerous_patterns_cover_spec_list(self):
        config = GuardConfig()
        joined = " ".join(config.dangerous_patterns)
        assert "rm -rf" in joined or "rm\\s+-rf" in joined
        assert "sudo" in joined
        assert "chmod 777" in joined or "chmod\\s+777" in joined
        assert "git push --force" in joined or "git\\s+push\\s+--force" in joined
        assert "git reset --hard" in joined or "git\\s+reset\\s+--hard" in joined
        # SPEC 4.2: at least 6 dangerous patterns
        assert len(config.dangerous_patterns) >= 6

    def test_extra_dangerous_patterns_merge(self):
        config = GuardConfig(extra_dangerous_patterns=[r"del\s+/"])
        assert config.extra_dangerous_patterns == [r"del\s+/"]


class TestMemoryConfig:
    def test_defaults(self):
        config = MemoryConfig()
        assert config.max_decisions_loaded == 10


class TestToolsConfig:
    def test_defaults(self):
        config = ToolsConfig()
        assert config.disabled == []
        assert config.shell_timeout == 60


class TestProjectConfig:
    def test_defaults(self):
        config = ProjectConfig()
        assert config.name == ""
        assert config.language == "python"
        assert config.test_framework == "pytest"
        assert config.lint_tool == "ruff"
        assert config.type_checker == "mypy"


class TestConfig:
    def test_defaults(self):
        config = Config()
        assert isinstance(config.llm, LLMConfig)
        assert isinstance(config.feedback, FeedbackConfig)
        assert isinstance(config.guard, GuardConfig)
        assert isinstance(config.memory, MemoryConfig)
        assert isinstance(config.tools, ToolsConfig)
        assert isinstance(config.project, ProjectConfig)

    def test_nested_defaults_are_consistent(self):
        config = Config()
        assert config.llm.model == "deepseek-chat"
        assert config.feedback.max_correction_rounds == 5
        assert config.guard.session_approval is True
        assert config.memory.max_decisions_loaded == 10
        assert config.tools.shell_timeout == 60
        assert config.project.test_framework == "pytest"

    def test_config_instances_not_shared(self):
        c1 = Config()
        c2 = Config()
        c1.llm.model = "other-model"
        assert c2.llm.model == "deepseek-chat"


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------

class TestLLMBackendProtocol:
    def test_duck_typed_backend_satisfies_protocol(self):
        class FakeBackend:
            def __init__(self):
                self.call_count = 0

            async def chat(self, messages, tools=None):
                self.call_count += 1
                return LLMResponse(content="done")

        assert isinstance(FakeBackend(), LLMBackend)

    def test_protocol_member_signature(self):
        # Verify the protocol declares the async chat method returning LLMResponse.
        assert "chat" in LLMBackend.__protocol_attrs__


class TestToolProtocol:
    def test_duck_typed_tool_satisfies_protocol(self):
        class FakeTool:
            name = "read_file"
            description = "Read a file"
            risk_level = RiskLevel.LOW

            def execute(self, params):
                return ToolResult(action_id="x", success=True)

            def dry_run(self, params):
                return ToolResult(action_id="x", success=True)

        assert isinstance(FakeTool(), Tool)

    def test_tool_without_dry_run_fails_protocol(self):
        class IncompleteTool:
            name = "bad"
            description = "missing dry_run"
            risk_level = RiskLevel.LOW

            def execute(self, params):
                return ToolResult(action_id="x", success=True)

        assert not isinstance(IncompleteTool(), Tool)
