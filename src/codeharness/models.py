"""Core data models, enums, and protocols for CodeHarness.

Every other module (loop, parser, guard, feedback, memory, config, tools,
llm) depends on the types defined here.  Per SPEC section 6.1 (core
entities), section 3.7 (config), and section 5.3 (protocols):

- Enums:     FailureCategory, RiskLevel, GuardVerdict
- Protocols: LLMBackend (async chat), Tool (execute/dry_run)
- Dataclasses: Message, Action, ToolResult, LLMResponse, ClassifiedFailure,
  FeedbackStrategy, FeedbackContext, CorrectionRecord, LoopDecision,
  RunResult, TurnContext, plus the Config family (LLMConfig, FeedbackConfig,
  GuardConfig, MemoryConfig, ToolsConfig, ProjectConfig, Config).

All enums are ``str`` enums so their values serialize cleanly into text
fed to the LLM and into logs.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class FailureCategory(str, Enum):
    """Deterministic failure categories produced by the feedback classifier.

    Member names follow the SPEC (SYNTAX_ERROR, ...); the string values are
    the short lowercase labels used in serialized output.
    """

    SYNTAX_ERROR = "syntax"
    IMPORT_ERROR = "import"
    ASSERTION_FAILURE = "assert"
    RUNTIME_ERROR = "runtime"
    TIMEOUT = "timeout"
    LINT_WARNING = "lint"
    TYPE_ERROR = "type"
    COMMAND_FAILED = "command"
    UNKNOWN = "unknown"


class RiskLevel(str, Enum):
    """Base risk of a tool; drives the guard verdict mapping."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class GuardVerdict(str, Enum):
    """Guard engine verdict for a single action."""

    ALLOW = "allow"
    ASK_ONCE = "ask_once"
    ASK_ALWAYS = "ask_always"


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMBackend(Protocol):
    """Async LLM chat interface.

    Implementations: DeepSeekBackend (OpenAI SDK) and MockBackend (scripted).
    ``tools`` is a list of OpenAI-style tool definitions (dicts).
    """

    async def chat(
        self, messages: list[Message], tools: list[dict] | None = None
    ) -> LLMResponse: ...


@runtime_checkable
class Tool(Protocol):
    """A tool executable by the agent."""

    name: str
    description: str
    risk_level: RiskLevel

    def execute(self, params: dict) -> ToolResult: ...

    def dry_run(self, params: dict) -> ToolResult: ...


# ---------------------------------------------------------------------------
# Core conversation / execution entities
# ---------------------------------------------------------------------------


@dataclass
class Message:
    """One message in the LLM conversation."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Action:
    """A parsed tool invocation emitted by the LLM."""

    tool: str
    params: dict[str, Any]
    action_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ToolResult:
    """Outcome of executing a single Action."""

    action_id: str
    success: bool
    output: str = ""
    error: str | None = None
    exit_code: int | None = None
    duration_ms: int = 0


@dataclass
class LLMResponse:
    """Parsed LLM chat response: free text plus optional tool calls."""

    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = "stop"


# ---------------------------------------------------------------------------
# Feedback entities
# ---------------------------------------------------------------------------


@dataclass
class ClassifiedFailure:
    """A single classified failure extracted from tool output."""

    category: FailureCategory
    file: str | None = None
    line: int | None = None
    message: str = ""
    raw_output: str = ""


@dataclass
class FeedbackStrategy:
    """Correction strategy for one failure category."""

    category: FailureCategory
    context_template: str = ""
    guidance: str = ""


@dataclass
class LoopDecision:
    """Loop controller verdict for one round."""

    action: Literal["retry", "stop_success", "stop_failure", "escalate"]
    reason: str = ""
    round_number: int = 0


@dataclass
class CorrectionRecord:
    """Record of one correction round, used for regression detection."""

    round_id: int
    action_ids: list[str] = field(default_factory=list)
    failures_before: list[ClassifiedFailure] = field(default_factory=list)
    failures_after: list[ClassifiedFailure] = field(default_factory=list)
    files_touched: set[str] = field(default_factory=set)
    decision: LoopDecision | None = None


@dataclass
class FeedbackContext:
    """Everything fed back into the LLM after a round of tool execution."""

    round_id: int
    failures: list[ClassifiedFailure] = field(default_factory=list)
    strategies: list[FeedbackStrategy] = field(default_factory=list)
    decision: LoopDecision | None = None

    def serialize(self) -> str:
        """Render the feedback as structured text for LLM injection.

        Format follows SPEC section 3.5:

            [FEEDBACK] Round 2 | 1 failure(s)
              [ASSERTION_FAILURE] tests/test_utils.py:15
                assert 3 == 5
                Strategy: Analyze the diff between expected and actual...
        """
        lines = [f"[FEEDBACK] Round {self.round_id} | {len(self.failures)} failure(s)"]
        for failure in self.failures:
            location = f"{failure.file}:{failure.line}" if failure.file else ""
            header = f"  [{failure.category.name}] {location}".rstrip()
            lines.append(header)
            if failure.message:
                lines.append(f"    {failure.message}")
            # Pair each failure with its strategy by category, not by position,
            # so filtering/reordering of strategies never misattaches guidance.
            strategy = next(
                (s for s in self.strategies if s.category == failure.category),
                None,
            )
            if strategy is not None and strategy.guidance:
                lines.append(f"    Strategy: {strategy.guidance}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Run / session entities
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    """Final result of one agent run."""

    status: Literal["success", "failure", "max_rounds", "interrupted"]
    rounds: int
    duration_ms: int = 0
    final_context: TurnContext | None = None


@dataclass
class TurnContext:
    """Accumulates the state of one conversation turn."""

    messages: list[Message] = field(default_factory=list)
    round_count: int = 0
    last_results: list[ToolResult] = field(default_factory=list)
    correction_history: list[CorrectionRecord] = field(default_factory=list)

    def add_message(self, message: Message) -> None:
        """Append a message to the conversation."""
        self.messages.append(message)

    def add_result(self, result: ToolResult) -> None:
        """Record a tool result and advance the round counter."""
        self.last_results.append(result)
        self.round_count += 1


# ---------------------------------------------------------------------------
# Config dataclasses (SPEC section 3.7)
# ---------------------------------------------------------------------------


@dataclass
class LLMConfig:
    """LLM provider settings (user-level config)."""

    provider: str = "deepseek"
    model: str = "deepseek-chat"
    api_base: str = "https://api.deepseek.com"
    max_tokens: int = 8192
    temperature: float = 0.0


@dataclass
class FeedbackConfig:
    """Feedback loop settings (user- and project-level)."""

    max_correction_rounds: int = 5
    max_same_error: int = 3
    timeout_ms: int = 60000
    signal_sources: list[str] = field(default_factory=lambda: ["pytest", "ruff", "mypy"])
    test_command: str = "pytest tests/ -v --junitxml=report.xml"
    lint_command: str = "ruff check src/"


# Dangerous command patterns matched by the guard engine (SPEC 3.4 / 10.2).
# The first entries are the canonical SPEC forms; the following variants
# close flag-order/bypass gaps: `rm -fr`, `rm -r -f`, `rm --recursive
# --force`, `chmod -R 777`, `chmod 0777` (review finding: pattern-variant
# bypass).  Entries must stay literal substrings of the canonical forms
# where tests/SPEC reference them, so variants are appended, not merged.
DEFAULT_DANGEROUS_PATTERNS: list[str] = [
    r"rm\s+-rf",  # recursive force delete
    r"rm\s+-(?:[A-Za-z]*[rf][A-Za-z]*[rf][A-Za-z]*)",  # -rf / -fr / -rF (any order)
    r"rm\s+-[A-Za-z]*r[A-Za-z]*\s+-[A-Za-z]*f[A-Za-z]*",  # rm -r -f
    r"rm\s+-[A-Za-z]*f[A-Za-z]*\s+-[A-Za-z]*r[A-Za-z]*",  # rm -f -r
    r"rm\s+--recursive\b[\s\S]*?\b--force\b",  # rm --recursive --force
    r"rm\s+--force\b[\s\S]*?\b--recursive\b",  # rm --force --recursive
    r"sudo",  # privilege escalation
    r"chmod\s+777",  # world-writable permissions
    r"chmod\s+(?:-[A-Za-z]*\s+)?0?777\b",  # variants: -R 777, 0777
    r">\s*/dev/",  # writing to device files
    r"git\s+push\s+--force",  # force-push
    r"git\s+reset\s+--hard",  # destructive reset
    r"mkfs\b",  # filesystem formatting
    r":\(\)\s*\{[^}]*\|[^}]*\}",  # fork bomb
]


@dataclass
class GuardConfig:
    """Guard engine settings (user- and project-level)."""

    extra_dangerous_patterns: list[str] = field(default_factory=list)
    dangerous_patterns: list[str] = field(
        default_factory=lambda: list(DEFAULT_DANGEROUS_PATTERNS)
    )
    session_approval: bool = True
    project_root: str = "."
    allowed_dirs: list[str] = field(default_factory=lambda: ["src", "tests", "docs"])


@dataclass
class MemoryConfig:
    """Memory store settings (user-level)."""

    max_decisions_loaded: int = 10


@dataclass
class ToolsConfig:
    """Tool registry settings (user-level)."""

    disabled: list[str] = field(default_factory=list)
    shell_timeout: int = 60


@dataclass
class ProjectConfig:
    """Project-level metadata."""

    name: str = ""
    language: str = "python"
    test_framework: str = "pytest"
    lint_tool: str = "ruff"
    type_checker: str = "mypy"


@dataclass
class Config:
    """Merged, effective configuration used by the whole harness."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)
    guard: GuardConfig = field(default_factory=GuardConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    project: ProjectConfig = field(default_factory=ProjectConfig)
