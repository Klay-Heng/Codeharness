# CodeHarness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local CLI Coding Agent Harness (CodeHarness) with feedback-loop as the deep dimension, following TDD with mock-LLM deterministic tests.

**Architecture:** Event-driven agent loop with dependency injection. Each module (LLM, Tools, Guard, Feedback, Memory, Config, Credentials) implements a Protocol, injectable into the loop. MockBackend replaces DeepSeek for all unit tests.

**Tech Stack:** Python 3.12+, `openai` SDK (DeepSeek-compatible), `keyring`, `rich`, `tomli`, `pytest` + `pytest-asyncio`

## Global Constraints

- Python >= 3.12; `openai` >= 1.0; `keyring` >= 24.0; `rich` >= 13.0; `tomli` >= 2.0; `pytest` >= 8.0
- TDD strictly enforced: write failing test, run to see it fail, implement, run to see it pass, commit
- All core mechanisms must have mock-LLM deterministic unit tests (no network, no real LLM)
- API key never hardcoded, never in git, never in logs
- 6 harness dimensions all need runnable minimum implementation; feedback loop is the deep dimension
- Package name: `codeharness`; CLI command: `codeharness`

---

## File Structure Map

```
ai4se/                              # project root
├── pyproject.toml                  # package metadata, deps, entry points
├── harness.toml                    # project-level config (example)
├── README.md
├── AGENT_LOG.md
├── Makefile                        # make test, make lint, make clean
├── .gitignore
├── .github/workflows/ci.yml        # GitHub Actions CI
├── src/codeharness/
│   ├── __init__.py
│   ├── main.py                     # CLI entry (typer): setup, status, repl
│   ├── models.py                   # All dataclasses, enums, protocols
│   ├── config.py                   # ConfigStore: TOML loading, merge
│   ├── credentials.py              # CredentialStore: keyring + getpass
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── deepseek.py             # DeepSeekBackend (openai SDK)
│   │   └── mock.py                 # MockBackend (scripted responses)
│   ├── parser.py                   # LLM response -> Action list
│   ├── memory.py                   # MemoryStore: markdown read/write
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── registry.py             # ToolRegistry: register/dispatch
│   │   ├── file_ops.py             # read_file, write_file
│   │   ├── search.py               # search_code, glob_files
│   │   ├── shell.py                # run_shell
│   │   ├── testing_tool.py         # run_tests (pytest)
│   │   ├── git_ops.py              # git_op
│   │   └── package_ops.py          # package_op (pip)
│   ├── guard.py                    # GuardEngine: risk + pattern match
│   ├── feedback.py                 # FeedbackEngine: classify + strategy + loop control
│   ├── loop.py                     # AgentLoop: main while loop
│   └── repl.py                     # REPL: rich-based terminal UI
└── tests/
    ├── __init__.py
    ├── conftest.py                  # pytest fixtures
    ├── test_models.py
    ├── test_config.py
    ├── test_credentials.py
    ├── test_llm_mock.py
    ├── test_parser.py
    ├── test_memory.py
    ├── test_tools.py
    ├── test_guard.py
    ├── test_feedback.py
    ├── test_loop.py
    ├── test_repl.py
    └── demo_mechanisms.py           # A.6 mechanism demonstration script
```## Phase 1: Foundation (Tasks 1-3)

### Task 1: Project Scaffolding

**Dependencies:** None (first task)

**Files to create:** `pyproject.toml`, `Makefile`, `.gitignore`, `src/codeharness/__init__.py`, `tests/__init__.py`, `tests/conftest.py`

**Produces:** `codeharness` package importable, `make test` runs pytest

- [ ] **Step 1: Create pyproject.toml** with project metadata, dependencies (openai, keyring, rich, tomli, typer), dev deps (pytest, pytest-asyncio, ruff), entry point `codeharness = "codeharness.main:app"`, and setuptools config.
- [ ] **Step 2: Create Makefile** with `install`, `test`, `lint`, `clean` targets.
- [ ] **Step 3: Create .gitignore** excluding `__pycache__/`, `.env`, `*.egg-info/`, `.pytest_cache/`, `.harness/`, etc.
- [ ] **Step 4: Create `src/codeharness/__init__.py`** with `__version__ = "0.1.0"`, empty `tests/__init__.py`, and `tests/conftest.py` with `temp_project` and `empty_config_dir` fixtures.
- [ ] **Step 5: Install and verify** — `pip install -e ".[dev]"` then `make test` (expect 0 tests collected, PASS).
- [ ] **Step 6: Commit** — `git add -A && git commit -m "chore: scaffold project"`

---

### Task 2: Data Models and Protocols

**Dependencies:** None

**Files to create:** `src/codeharness/models.py`, `tests/test_models.py`

**Produces:** All dataclasses (Message, Action, ToolResult, LLMResponse, ClassifiedFailure, FeedbackStrategy, FeedbackContext, CorrectionRecord, LoopDecision, RunResult, TurnContext, Config variants), enums (FailureCategory, RiskLevel, GuardVerdict), Protocols (LLMBackend, Tool)

- [ ] **Step 1: Write the failing test `tests/test_models.py`** — test Message creation, Action auto-UUID, ToolResult success/failure, FailureCategory values, RiskLevel/GuardVerdict enum values, LoopDecision, FeedbackContext.serialize(), Config defaults, RunResult, TurnContext add_message/add_result.
- [ ] **Step 2: Run `pytest tests/test_models.py -v`** — expect FAIL (ModuleNotFoundError).
- [ ] **Step 3: Write `src/codeharness/models.py`** — implement all dataclasses, enums, and protocols per SPEC sections 5.3 and 6.1.
- [ ] **Step 4: Run `pytest tests/test_models.py -v`** — expect all PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: add core data models, enums, and protocols"`

---

### Task 3: Config Store

**Dependencies:** Task 2 (models.py)

**Files to create:** `src/codeharness/config.py`, `tests/test_config.py`

**Produces:** `ConfigLoader` class with `load(project_dir: Path | None = None) -> Config`

- [ ] **Step 1: Write the failing test `tests/test_config.py`** — test default config, project-level override of defaults, user-level config, project-overrides-user priority, missing project config fallback, extra_dangerous_patterns merge, tools.disabled.
- [ ] **Step 2: Run `pytest tests/test_config.py -v`** — expect FAIL (ModuleNotFoundError).
- [ ] **Step 3: Write `src/codeharness/config.py`** — implement `ConfigLoader` using `tomli.load()`, constructor takes `user_config_dir` (default `~/.coding-harness`), `_merge()` method for each TOML section, project config overrides user config.
- [ ] **Step 4: Run `pytest tests/test_config.py -v`** — expect all PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: add config store with TOML loading and priority merge"`## Phase 2: Infrastructure (Tasks 4-7)

### Task 4: Credential Store

**Dependencies:** None (pure infrastructure)

**Files to create:** `src/codeharness/credentials.py`, `tests/test_credentials.py`

**Produces:** `CredentialStore` with `get_key() -> str | None`, `set_key(key) -> None`, `clear_key() -> None`, `check_status() -> str`

- [ ] **Step 1: Write the failing test `tests/test_credentials.py`** — test get_key returns password via mock keyring, get_key returns None when not set, set_key calls keyring.set_password, clear_key calls keyring.delete_password, check_status returns "set"/"not_set" (never reveals key value), env var fallback when keyring unavailable, env fallback returns None when var not set.
- [ ] **Step 2: Run `pytest tests/test_credentials.py -v`** — expect FAIL (ModuleNotFoundError).
- [ ] **Step 3: Write `src/codeharness/credentials.py`** — implement `CredentialStore` with SERVICE_NAME="CodeHarness", ACCOUNT_NAME="deepseek_api_key". Primary: `keyring` (get_password/set_password/delete_password). Fallback: `os.getenv("DEEPSEEK_API_KEY")` with debug log. `check_status()` returns "set"/"not_set" only, never the key value.
- [ ] **Step 4: Run `pytest tests/test_credentials.py -v`** — expect all PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: add credential store with keyring and env fallback"`

---

### Task 5: Mock LLM Backend

**Dependencies:** Task 2 (models.py)

**Files to create:** `src/codeharness/llm/__init__.py`, `src/codeharness/llm/mock.py`, `tests/test_llm_mock.py`

**Produces:** `MockBackend(script: list[LLMResponse])` implementing `LLMBackend` Protocol

- [ ] **Step 1: Write the failing test `tests/test_llm_mock.py`** — test returns scripted responses in order, returns default DONE when script exhausted, tool call responses, call_count tracking, determinism (same script = same results).
- [ ] **Step 2: Run `pytest tests/test_llm_mock.py -v`** — expect FAIL (ModuleNotFoundError).
- [ ] **Step 3: Write `src/codeharness/llm/mock.py`** — `MockBackend.__init__` takes `script: list[LLMResponse] | None`, `chat()` returns next script item or default `LLMResponse(content="DONE")` when exhausted, tracks `call_count`.
- [ ] **Step 4: Run `pytest tests/test_llm_mock.py -v`** — expect all PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: add MockBackend for deterministic LLM testing"`

---

### Task 6: Parser

**Dependencies:** Task 2 (models.py)

**Files to create:** `src/codeharness/parser.py`, `tests/test_parser.py`

**Produces:** `ResponseParser.parse(response: LLMResponse) -> list[Action]`

- [ ] **Step 1: Write the failing test `tests/test_parser.py`** — test parse tool_calls into Actions, parse with no tool calls returns empty list, empty response returns empty list, actions have unique UUIDs, empty tool names are filtered out.
- [ ] **Step 2: Run `pytest tests/test_parser.py -v`** — expect FAIL (ModuleNotFoundError).
- [ ] **Step 3: Write `src/codeharness/parser.py`** — `ResponseParser.parse()` iterates `response.tool_calls`, skips empty names, creates `Action` objects with auto-generated IDs.
- [ ] **Step 4: Run `pytest tests/test_parser.py -v`** — expect all PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: add LLM response parser"`

---

### Task 7: Memory Store

**Dependencies:** Task 2 (models.py)

**Files to create:** `src/codeharness/memory.py`, `tests/test_memory.py`

**Produces:** `MemoryStore` with `load_conventions()`, `load_recent_decisions(limit)`, `save_decision(...)`, `save_convention(name, content)`, `build_system_context() -> str`

- [ ] **Step 1: Write the failing test `tests/test_memory.py`** — test empty conventions/decisions on fresh store, save and load convention, save and load decision (with title/context/decision/reasons/alternatives), decisions respect limit, build_system_context empty vs with content, convention overwrite.
- [ ] **Step 2: Run `pytest tests/test_memory.py -v`** — expect FAIL (ModuleNotFoundError).
- [ ] **Step 3: Write `src/codeharness/memory.py`** — `MemoryStore(base_path: Path)` stores under `.harness/memory/`. Conventions saved as `conventions/{name}.md`. Decisions saved as `decisions/{date}-{slug}.md` with markdown format. `build_system_context()` assembles conventions + recent decisions into a string capped at ~2000 chars.
- [ ] **Step 4: Run `pytest tests/test_memory.py -v`** — expect all PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: add memory store with markdown-based persistence"`## Phase 3: Tools (Tasks 8-9)

### Task 8: Tool Registry

**Dependencies:** Task 2 (models.py)

**Files to create:** `src/codeharness/tools/__init__.py`, `src/codeharness/tools/registry.py`, `tests/test_tools.py`

**Produces:** `ToolRegistry` class with `register(tool)`, `dispatch(action) -> ToolResult`, `list_available() -> list[dict]`, `get_tool(name) -> Tool | None`

- [ ] **Step 1: Write the failing test `tests/test_tools.py` (registry part)** — test register tool and dispatch, dispatch unknown tool returns error, list_available returns tool info, get_tool finds registered tool, get_tool returns None for unknown, disabled tools in config are skipped.
- [ ] **Step 2: Run `pytest tests/test_tools.py -v`** — expect FAIL (ModuleNotFoundError).
- [ ] **Step 3: Write `src/codeharness/tools/registry.py`** — `ToolRegistry.__init__` takes `config: ToolsConfig`. `register(tool: Tool)` adds to internal dict. `dispatch(action) -> ToolResult` looks up tool, checks if disabled, calls `tool.execute(params)`, measures duration. `get_tool(name)` returns tool or None. `list_available()` returns list of {name, description, risk_level} for non-disabled tools.
- [ ] **Step 4: Run tests** — expect registry tests PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: add tool registry with registration and dispatch"`

---

### Task 9: All 8 Tool Implementations

**Dependencies:** Task 8 (registry)

**Files to create:** `src/codeharness/tools/file_ops.py`, `src/codeharness/tools/search.py`, `src/codeharness/tools/shell.py`, `src/codeharness/tools/testing_tool.py`, `src/codeharness/tools/git_ops.py`, `src/codeharness/tools/package_ops.py`

**Produces:** 8 concrete Tool implementations

- [ ] **Step 1: Expand `tests/test_tools.py`** with tests for each tool:
  - `read_file`: reads file content, respects start_line/end_line, returns error for missing file
  - `write_file`: creates file, overwrites existing, returns error for permission issues
  - `search_code`: grep search finds matches, returns empty for no match
  - `glob_files`: matches patterns, returns sorted paths
  - `run_shell`: executes command, captures stdout/stderr, respects timeout
  - `run_tests`: executes pytest, parses JUnit XML
  - `git_op`: status/diff/log return output, commit creates commit
  - `package_op`: pip install/uninstall/list
- [ ] **Step 2: Run tests** — expect FAIL (tools not implemented).
- [ ] **Step 3: Implement each tool module** — `read_file` (RiskLevel.LOW), `write_file` (RiskLevel.MEDIUM, project-root-bounded), `search_code` (LOW, wraps `grep`/`ripgrep`), `glob_files` (LOW, wraps `pathlib.glob`), `run_shell` (MEDIUM, `subprocess.run` with timeout), `run_tests` (LOW, `subprocess.run` pytest + parse XML), `git_op` (VARIES, `subprocess.run git`), `package_op` (HIGH, `subprocess.run pip`).
- [ ] **Step 4: Run `pytest tests/test_tools.py -v`** — expect all PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: add 8 tool implementations (file, search, shell, test, git, package)"`

---

## Phase 4: Guard Engine (Task 10)

### Task 10: Guard Engine

**Dependencies:** Task 3 (config), Task 8 (registry)

**Files to create:** `src/codeharness/guard.py`, `tests/test_guard.py`

**Produces:** `GuardEngine` with `check(action: Action) -> GuardVerdict`, `SessionState` for approval tracking

- [ ] **Step 1: Write the failing test `tests/test_guard.py`** — TEST: dangerous pattern "rm -rf /" returns ASK_ALWAYS; safe read_file returns ALLOW; write_file inside project returns ASK_ONCE; write_file outside project returns ASK_ALWAYS; git status returns ALLOW; git push --force returns ASK_ALWAYS; package_op returns ASK_ALWAYS; sudo command returns ASK_ALWAYS; chmod 777 returns ASK_ALWAYS; session_state tracks approvals; ASK_ONCE returns ALLOW after prior approval.
- [ ] **Step 2: Run `pytest tests/test_guard.py -v`** — expect FAIL (ModuleNotFoundError).
- [ ] **Step 3: Write `src/codeharness/guard.py`** — `GuardEngine.__init__` takes `config: GuardConfig` and `tool_registry: ToolRegistry`. `check(action)` logic: (1) if tool not found, return ASK_ALWAYS; (2) check dangerous patterns via regex match on command params; (3) check path boundaries for write_file/run_shell; (4) fall back to tool risk_level mapping. `SessionState` tracks `approved_tools: set[str]` for ASK_ONCE memory.
- [ ] **Step 4: Run `pytest tests/test_guard.py -v`** — expect all PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: add guard engine with risk levels and dangerous pattern matching"`

---

## Phase 5: Feedback Engine (Task 11) - MAIN CONTRIBUTION

### Task 11: Feedback Engine

**Dependencies:** Task 2 (models.py)

**Files to create:** `src/codeharness/feedback.py`, `tests/test_feedback.py`

**Produces:** `FailureClassifier`, `StrategySelector`, `LoopController`, `FeedbackEngine` (orchestrator)

- [ ] **Step 1: Write the failing test `tests/test_feedback.py`** — THREE test classes:

**TestFailureClassifier:**
- `classify_syntax_error` — input with "SyntaxError: invalid syntax at test.py:15", assert category=SYNTAX_ERROR, file="test.py", line=15
- `classify_import_error` — "ModuleNotFoundError: No module named 'requests'", assert IMPORT_ERROR
- `classify_assertion_failure` — "AssertionError: assert 1 == 2", assert ASSERTION_FAILURE
- `classify_runtime_error` — "TypeError: int() argument must be", assert RUNTIME_ERROR
- `classify_timeout` — result with duration_ms=61000, timeout=60000, assert TIMEOUT
- `classify_lint_warning` — ruff output "src/a.py:10:5: F401", assert LINT_WARNING
- `classify_type_error` — mypy output "src/a.py:10: error: Incompatible", assert TYPE_ERROR
- `classify_command_failed` — exit_code=1, no matching pattern, assert COMMAND_FAILED
- `classify_unknown` — empty/unparseable output, assert UNKNOWN
- `classify_multiple_failures` — pytest output with 3 failures
- `classify_all_pass` — exit_code=0, empty stderr, assert empty list

**TestStrategySelector:**
- `select_strategy_for_syntax_error` — assert guidance contains "fix syntax"
- `select_strategy_for_assertion` — assert guidance contains "diff" or "expected"
- `select_strategy_for_timeout` — assert guidance contains "dead loop" or "cache"
- `each_category_has_strategy` — all 9 FailureCategory values have a non-empty strategy

**TestLoopController:**
- `stop_success_no_failures` — empty failures list, assert action="stop_success"
- `stop_failure_max_rounds` — round >= max_rounds, assert action="stop_failure"
- `escalate_same_error_repeated` — 3+ consecutive same-category failures, assert action="escalate"
- `escalate_on_regression` — new file failure not in history, assert action="escalate"
- `retry_normal_case` — failures exist but not max, not repeated, not regression, assert action="retry"
- `regression_detection_new_file` — history has files={"a.py"}, new failure file="b.py", assert regression
- `regression_detection_new_category` — history has categories={SYNTAX_ERROR}, new failure category=RUNTIME_ERROR, assert regression
- `regression_detection_no_regression` — same file and category as before, assert no regression

**TestFeedbackEngine (integration):**
- `evaluate_all_pass` — ToolResult with exit_code=0, assert FeedbackContext with no failures
- `evaluate_with_failure` — ToolResult with SyntaxError, assert FeedbackContext with classified failure and strategy
- `serialize_for_llm` — verify serialized output contains category, file, line, strategy

- [ ] **Step 2: Run `pytest tests/test_feedback.py -v`** — expect FAIL (ModuleNotFoundError).

- [ ] **Step 3: Write `src/codeharness/feedback.py`** implementing:

**FailureClassifier** — `classify(result: ToolResult) -> list[ClassifiedFailure]`:
- Compile regex patterns for each failure category
- Iterate through result.output + result.error lines
- Extract file/line/message via regex groups
- Check timeout: `result.duration_ms > timeout`
- Check exit_code for COMMAND_FAILED fallback
- Return list of ClassifiedFailure objects

**StrategySelector** — `select(failure: ClassifiedFailure) -> FeedbackStrategy`:
- Lookup table mapping FailureCategory to guidance text
- SYNTAX_ERROR -> "Fix syntax only, do not change logic"
- IMPORT_ERROR -> "Check import paths or add missing dependency"
- ASSERTION_FAILURE -> "Analyze the diff between expected and actual, fix logic"
- RUNTIME_ERROR -> "Locate root cause, add defensive checks"
- TIMEOUT -> "Check for infinite loops or add caching"
- LINT_WARNING -> "Fix only the lint issue, do not change logic"
- TYPE_ERROR -> "Fix type annotation or usage"
- COMMAND_FAILED -> "Check command syntax and prerequisites"
- UNKNOWN -> "Investigate the error output and determine root cause"

**LoopController** — `decide(failures: list[ClassifiedFailure], round_number: int, history: list[CorrectionRecord], config: FeedbackConfig) -> LoopDecision`:
- No failures -> stop_success
- round >= max_correction_rounds -> stop_failure
- Consecutive same-category >= max_same_error -> escalate
- Regression detected (new file or new category vs history) -> escalate
- Otherwise -> retry

**FeedbackEngine** — orchestrates: `evaluate(results: list[ToolResult], round_number: int, history: list[CorrectionRecord]) -> FeedbackContext`:
- Collect results, classify each, select strategies, call LoopController.decide(), build FeedbackContext with decision

- [ ] **Step 4: Run `pytest tests/test_feedback.py -v`** — expect all PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat: add feedback engine with classifier, strategy selector, and loop controller"`## Phase 6: Agent Loop (Task 12)

### Task 12: Agent Loop

**Dependencies:** Tasks 2-11 (all modules)

**Files to create:** `src/codeharness/loop.py`, `tests/test_loop.py`

**Produces:** `AgentLoop` class with `run(task: str) -> RunResult`

- [ ] **Step 1: Write the failing test `tests/test_loop.py`** — Use MockBackend with scripted responses to test deterministically:

```python
# Test 1: Agent completes task in one round
# Script: LLMResponse with no tool calls (DONE)
# Expected: RunResult(status="success", rounds=1)

# Test 2: Agent uses tools then completes
# Script: [read_file response, write_file response, DONE response]
# Expected: actions executed, RunResult(status="success")

# Test 3: Agent corrects after feedback
# Script: 
#   Round 1: write_file action
#   (feedback: test fails with SyntaxError)
#   Round 2: write_file action (fix)
#   (feedback: test passes)
#   Round 3: DONE
# Expected: RunResult(status="success", rounds=3), correction_history has entries

# Test 4: Agent stops at max rounds
# Script: always-returns-fix attempt (never succeeds)
# Expected: RunResult(status="max_rounds")

# Test 5: Guard blocks dangerous action
# Script: run_shell("rm -rf /") action
# Expected: Guard returns ASK_ALWAYS, action skipped (or simulated approval)

# Test 6: Agent escalates on repeated failure
# Script: always produces same failing code
# Expected: feedback loop escalates after max_same_error
```

- [ ] **Step 2: Run `pytest tests/test_loop.py -v`** — expect FAIL.

- [ ] **Step 3: Write `src/codeharness/loop.py`**:

```python
class AgentLoop:
    def __init__(self, llm, tools, guard, feedback, memory, config, parser):
        # All dependencies injected via constructor
    
    async def run(self, task: str) -> RunResult:
        # 1. Build context: system prompt + memory + task
        # 2. Main loop:
        #    a. LLM call -> response
        #    b. Parser -> actions
        #    c. For each action: guard.check -> if ALLOW/ASK_ONCE: execute; if ASK_ALWAYS: request approval
        #    d. Collect results
        #    e. Feedback.evaluate(results, round, history)
        #    f. If stop_success/stop_failure: break
        #    g. If escalate: request human, break
        #    h. If retry: inject feedback into context, continue
        # 3. Return RunResult
```

- [ ] **Step 4: Run `pytest tests/test_loop.py -v`** — expect all PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat: add agent main loop with full harness integration"`

---

## Phase 7: UI & CLI (Tasks 13-15)

### Task 13: REPL Interface

**Dependencies:** Task 12 (loop)

**Files to create:** `src/codeharness/repl.py`, `tests/test_repl.py`

**Produces:** `REPL` class with `start() -> None` using `rich` for terminal rendering

- [ ] **Step 1: Write the failing test `tests/test_repl.py`** — test REPL input parsing (single line, multi-line), exit commands (exit/quit/Ctrl+D), guard approval prompt formatting, feedback display formatting. Mock the AgentLoop to avoid real LLM calls.
- [ ] **Step 2: Run `pytest tests/test_repl.py -v`** — expect FAIL.
- [ ] **Step 3: Write `src/codeharness/repl.py`** — `REPL.__init__` takes `agent_loop`, `credential_store`, `config`. `start()` shows welcome banner with model/project info. Main loop: `rich.prompt.Prompt.ask("> ")` for input. Support multi-line via `\\` continuation. Display agent thoughts in folded `rich.panel.Panel`. Guard prompts via `rich.prompt.Confirm.ask("[y/n/session]")`. Feedback results color-coded via `rich.text.Text`. Exit on "exit"/"quit"/Ctrl+C/Ctrl+D.
- [ ] **Step 4: Run `pytest tests/test_repl.py -v`** — expect all PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: add REPL interface with rich terminal rendering"`

---

### Task 14: CLI Entry Point

**Dependencies:** Task 4 (credentials), Task 13 (repl)

**Files to create:** `src/codeharness/main.py`

**Produces:** `typer.Typer` app with commands: `setup`, `status`, default (REPL)

- [ ] **Step 1: Write `src/codeharness/main.py`**:

```python
import typer
from getpass import getpass
from codeharness.credentials import CredentialStore
from codeharness.config import ConfigLoader
# ... other imports

app = typer.Typer(help="CodeHarness - A local CLI Coding Agent")

@app.command()
def setup(reset: bool = False, clear: bool = False):
    """Configure or manage API key."""
    store = CredentialStore()
    if clear:
        store.clear_key()
        print("API key cleared.")
        return
    if reset or store.check_status() == "not_set":
        print("Enter your DeepSeek API key (input hidden):")
        key = getpass("> ")
        if key.strip():
            store.set_key(key.strip())
            print("API key saved.")
        else:
            print("No key entered.")
    else:
        print("API key already configured. Use --reset to change or --clear to remove.")

@app.command()
def status():
    """Show configuration status."""
    store = CredentialStore()
    print(f"API key: {store.check_status()}")
    loader = ConfigLoader()
    config = loader.load()
    print(f"Model: {config.llm.model}")
    print(f"Provider: {config.llm.provider}")

@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """Start the REPL (default)."""
    if ctx.invoked_subcommand is None:
        # Initialize all modules and start REPL
        store = CredentialStore()
        if store.check_status() == "not_set":
            print("No API key configured. Run 'codeharness setup' first.")
            raise typer.Exit(1)
        # ... init config, llm, tools, guard, feedback, memory, loop, repl
        # repl.start()

if __name__ == "__main__":
    app()
```

- [ ] **Step 2: Verify CLI** — Run `codeharness --help`, `codeharness setup --help`, `codeharness status`.
- [ ] **Step 3: Commit** — `git commit -m "feat: add CLI entry point with setup, status, and REPL commands"`

---

### Task 15: DeepSeek Backend

**Dependencies:** Task 2 (models), Task 4 (credentials), Task 3 (config)

**Files to create:** `src/codeharness/llm/deepseek.py`

**Produces:** `DeepSeekBackend` implementing `LLMBackend` Protocol

- [ ] **Step 1: Write `src/codeharness/llm/deepseek.py`**:

```python
from openai import AsyncOpenAI
from codeharness.models import Message, LLMResponse, LLMConfig

class DeepSeekBackend:
    def __init__(self, config: LLMConfig, credential_store):
        self.client = AsyncOpenAI(
            api_key=credential_store.get_key(),
            base_url=config.api_base,
        )
        self.model = config.model
        self.max_tokens = config.max_tokens
        self.temperature = config.temperature

    async def chat(self, messages: list[Message], tools: list[dict]) -> LLMResponse:
        formatted = [{"role": m.role, "content": m.content} for m in messages]
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=formatted,
            tools=self._format_tools(tools) if tools else None,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        choice = response.choices[0]
        tool_calls = []
        if choice.message.tool_calls:
            tool_calls = [
                {"name": tc.function.name, "params": tc.function.arguments}
                for tc in choice.message.tool_calls
            ]
        return LLMResponse(
            content=choice.message.content or "",
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
        )

    def _format_tools(self, tools: list[dict]) -> list[dict]:
        # Convert our tool format to OpenAI function-calling format
        ...
```

- [ ] **Step 2: Verify** — Integration test with a real API key (manual, not in CI).
- [ ] **Step 3: Commit** — `git commit -m "feat: add DeepSeek backend via OpenAI SDK"`## Phase 8: Mechanism Demo & Distribution (Tasks 16-18)

### Task 16: Mechanism Demonstration (A.6)

**Dependencies:** Tasks 10 (guard), 11 (feedback), 12 (loop)

**Files to create:** `tests/demo_mechanisms.py`

**Produces:** Deterministic demo script showing 3 required behaviors using MockBackend

- [ ] **Step 1: Write `tests/demo_mechanisms.py`** — Three demo functions, each using MockBackend, all deterministic (no network, no real LLM):

**Demo 1: Guard Intercepts Dangerous Action**
```python
def demo_guard_intercepts_dangerous_action():
    """Demonstrate: guard engine blocks 'rm -rf /' deterministically."""
    from codeharness.guard import GuardEngine
    from codeharness.models import Action, GuardConfig
    from codeharness.tools.registry import ToolRegistry
    from codeharness.tools.shell import RunShellTool
    from codeharness.config import ConfigLoader
    
    config = ConfigLoader().load()
    registry = ToolRegistry(config.tools)
    registry.register(RunShellTool())
    guard = GuardEngine(config.guard, registry)
    
    # Dangerous action
    action = Action(tool="run_shell", params={"command": "rm -rf /"})
    verdict = guard.check(action)
    assert verdict.value == "ask_always", f"Expected ASK_ALWAYS, got {verdict}"
    
    # Safe action for comparison
    safe_action = Action(tool="read_file", params={"path": "src/main.py"})
    safe_verdict = guard.check(safe_action)
    assert safe_verdict.value == "allow", f"Expected ALLOW, got {safe_verdict}"
    
    print("PASS: Guard correctly intercepts dangerous 'rm -rf /'")
    print("PASS: Guard allows safe read_file")
```

**Demo 2: Feedback Loop Drives Correction**
```python
async def demo_feedback_drives_correction():
    """Demonstrate: injected failure -> feedback classification -> agent changes behavior."""
    from codeharness.loop import AgentLoop
    from codeharness.llm.mock import MockBackend
    from codeharness.models import LLMResponse, Action, ToolResult
    from codeharness.feedback import FeedbackEngine
    from codeharness.config import ConfigLoader
    
    config = ConfigLoader().load()
    
    # Script: Round 1 writes code that fails -> Round 2 fixes it -> Round 3 done
    script = [
        # Round 1: Agent writes code
        LLMResponse(
            content="I'll create the file.",
            tool_calls=[{"name": "write_file", "params": {"path": "test.py", "content": "def broken(): return 1/0"}}]
        ),
        # After feedback: Agent fixes the bug
        LLMResponse(
            content="Fixing the division by zero.",
            tool_calls=[{"name": "write_file", "params": {"path": "test.py", "content": "def fixed(): return 0"}}]
        ),
        # Round 3: Done
        LLMResponse(content="Task complete."),
    ]
    
    mock_llm = MockBackend(script=script)
    
    # Create a loop with mock LLM and simple tools
    loop = AgentLoop(
        llm=mock_llm,
        tools=create_test_tools(),  # minimal tools that simulate test output
        guard=create_passive_guard(),  # allows everything
        feedback=FeedbackEngine(config.feedback),
        memory=create_empty_memory(),
        config=config,
        parser=ResponseParser(),
    )
    
    result = await loop.run("Write a working function")
    
    assert result.rounds >= 2, f"Expected at least 2 rounds (write + fix), got {result.rounds}"
    assert mock_llm.call_count >= 2, f"Expected at least 2 LLM calls"
    
    print(f"PASS: Feedback drove correction over {result.rounds} rounds")
    print(f"     LLM was called {mock_llm.call_count} times")
```

**Demo 3: FeedbackClassifier Deep Dimension Behavior**
```python
def demo_feedback_classifier_precision():
    """Demonstrate: deep dimension - precise failure classification."""
    from codeharness.feedback import FailureClassifier, FeedbackEngine
    from codeharness.models import ToolResult, FailureCategory
    
    classifier = FailureClassifier()
    
    # Test 1: Syntax error from pytest output
    syntax_result = ToolResult(
        action_id="a1", success=False,
        output="", error='SyntaxError: invalid syntax at test.py:15',
        exit_code=1, duration_ms=100
    )
    failures = classifier.classify(syntax_result)
    assert len(failures) == 1
    assert failures[0].category == FailureCategory.SYNTAX_ERROR
    assert failures[0].file == "test.py"
    assert failures[0].line == 15
    print("PASS: Correctly classified SyntaxError at test.py:15")
    
    # Test 2: Assertion failure from pytest
    assert_result = ToolResult(
        action_id="a2", success=False,
        output="FAILED tests/test_math.py::test_add - AssertionError: assert 3 == 5",
        error="", exit_code=1, duration_ms=200
    )
    failures = classifier.classify(assert_result)
    assert len(failures) >= 1
    assert failures[0].category == FailureCategory.ASSERTION_FAILURE
    print(f"PASS: Correctly classified AssertionError")
    
    # Test 3: All-pass result
    pass_result = ToolResult(
        action_id="a3", success=True,
        output="4 passed in 0.5s", error="", exit_code=0, duration_ms=500
    )
    failures = classifier.classify(pass_result)
    assert len(failures) == 0
    print("PASS: Correctly identified all-pass (no failures)")
    
    # Test 4: Strategy selection
    from codeharness.feedback import StrategySelector
    selector = StrategySelector()
    for category in FailureCategory:
        strategy = selector.select(category)
        assert strategy.guidance, f"No guidance for {category}"
    print("PASS: All 9 failure categories have a strategy")
    
    # Test 5: Loop controller - escalation on repeated same error
    from codeharness.feedback import LoopController
    from codeharness.models import FeedbackConfig, CorrectionRecord
    controller = LoopController()
    cfg = FeedbackConfig(max_correction_rounds=5, max_same_error=3)
    
    # Simulate 3 rounds of the same syntax error
    history = []
    for round_num in range(3):
        history.append(CorrectionRecord(
            round_id=round_num + 1,
            failures_before=[ClassifiedFailure(category=FailureCategory.SYNTAX_ERROR, file="test.py", message="bad syntax")],
            files_touched={"test.py"}
        ))
    
    same_failure = [ClassifiedFailure(category=FailureCategory.SYNTAX_ERROR, file="test.py", message="bad syntax")]
    decision = controller.decide(same_failure, 4, history, cfg)
    assert decision.action == "escalate", f"Expected escalate, got {decision.action}"
    print("PASS: Loop controller escalates after 3 consecutive same-category errors")
    
    print("\n=== All mechanism demonstrations PASSED ===")
```

- [ ] **Step 2: Run `python tests/demo_mechanisms.py`** — expect all demos PASS deterministically.

- [ ] **Step 3: Run `pytest tests/demo_mechanisms.py -v`** (wrap demos as pytest test functions).

- [ ] **Step 4: Commit** — `git commit -m "feat: add mechanism demonstration script for A.6 requirements"`

---

### Task 17: CI/CD and Distribution

**Dependencies:** All tasks above

**Files to create:** `.github/workflows/ci.yml`, `harness.toml` (example), `README.md`

- [ ] **Step 1: Create `.github/workflows/ci.yml`**:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  unit-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install dependencies
        run: pip install -e ".[dev]"
      - name: Run tests
        env:
          DEEPSEEK_API_KEY: "sk-mock-for-ci"
        run: pytest tests/ -v
      - name: Run mechanism demo
        run: python tests/demo_mechanisms.py
      - name: Lint
        run: ruff check src/ tests/
```

- [ ] **Step 2: Create example `harness.toml`** at project root for documentation purposes.

- [ ] **Step 3: Write `README.md`** with sections: Project Overview, Installation (`pip install codeharness`), Quick Start (`codeharness setup` then `codeharness`), API Key Security, Commands Reference, Configuration, Distribution, Known Limitations, License.

- [ ] **Step 4: Verify CI** — Push to GitHub, verify the Actions workflow runs and passes.

- [ ] **Step 5: Commit** — `git commit -m "chore: add CI config, example config, and README"`

---

### Task 18: Final Integration and Polish

**Dependencies:** All tasks

- [ ] **Step 1: Integration test** — Run `codeharness` with real DeepSeek API key, execute a simple task ("write a hello world function with a test"), verify full loop works end-to-end.
- [ ] **Step 2: Final lint** — `make lint` passes with zero issues.
- [ ] **Step 3: Final test** — `make test` passes all tests (unit + demo).
- [ ] **Step 4: Update `AGENT_LOG.md`** with key implementation decisions.
- [ ] **Step 5: Write `REFLECTION.md`** framework.
- [ ] **Step 6: Final commit and push** — `git commit -m "chore: final integration polish and documentation"`

---

## Dependency Graph

```
Task 1 (scaffolding)
  |
Task 2 (models) ─────────────────────────────┐
  |                                           │
Task 3 (config) ──┐                          │
  |               |                          │
Task 4 (credentials)                        │
  |               |                          │
Task 5 (mock llm) |                          │
  |               |                          │
Task 6 (parser)   |                          │
  |               |                          │
Task 7 (memory)   |                          │
  |               |                          │
Task 8 (tool registry) ──┐                   │
  |                      |                   │
Task 9 (all 8 tools) ────┤                   │
  |                      |                   │
Task 10 (guard) ─────────┤                   │
  |                      |                   │
Task 11 (feedback) ★ ────┤                   │
  |                      |                   │
  └──────────────────────┼───────────────────┘
                         │
                  Task 12 (agent loop)
                         │
                  Task 13 (repl)
                         │
                  Task 14 (cli entry) ── Task 15 (deepseek backend)
                         │
                  Task 16 (mechanism demo)
                         │
                  Task 17 (ci/cd, readme)
                         │
                  Task 18 (final polish)
```

**Parallelizable:** Tasks 4, 5, 6, 7 can all run in parallel after Task 2. Tasks 10 and 11 can run in parallel after Tasks 3 and 8. Tasks 13 and 15 can run in parallel after Task 12.

---

## Quick Reference: Key Commands

| Command | Description |
|---|---|
| `make install` | Install package + dev deps in editable mode |
| `make test` | Run all unit tests |
| `make lint` | Run ruff linter |
| `make clean` | Remove build artifacts |
| `pytest tests/test_feedback.py -v` | Run feedback engine tests only |
| `python tests/demo_mechanisms.py` | Run A.6 mechanism demonstration |
| `codeharness setup` | Configure API key (first-time) |
| `codeharness status` | Show configuration status |
| `codeharness` | Start REPL |

---

*Plan version: v1.0 | Date: 2026-07-08 | Based on SPEC v1.0*