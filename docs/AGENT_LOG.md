# AGENT_LOG.md

A living log of the key implementation decisions made while building
CodeHarness.  Each entry records the context, the decision, the reasons,
and the alternatives considered, so future sessions can reconstruct
*why* the code looks the way it does without re-deriving it.

Newest entries first.  Copy the template at the bottom for each new
decision.

---

## 2026-07-08 — Task 17/18: CI, distribution, and final polish

**Context:** The implementation was complete; the plan's final tasks
required CI, documentation, and a verification pass.

**Decision:** Added `.github/workflows/ci.yml` (checkout, Python 3.12,
`pip install -e ".[dev]"`, pytest, mechanism demo, ruff on `src/` +
`tests/`), a documented example `harness.toml`, and a full `README.md`.
Created `tests/demo_mechanisms.py` (Task 16) as three deterministic
demos driven entirely by `MockBackend` — no network, no real LLM.

**Reasons:**
- The demo doubles as CI content: the workflow runs the demo script
  directly, so a regression in any demonstrated mechanism fails CI.
- The example config uses only append-safe settings
  (`extra_dangerous_patterns`), never replacing built-in dangerous
  patterns.
- Demos build their own `GuardConfig`/`ToolsConfig` instead of calling
  `ConfigLoader().load()`, so results never depend on a stray
  `harness.toml` in the invocation directory.

**Alternatives considered:** Real-API integration test in CI — rejected,
CI must stay credential-free; a placeholder `DEEPSEEK_API_KEY` is
injected instead.

---

## 2026-07-08 — Task 12/16: the agent loop never prompts; blocked actions are reported

**Context:** The loop orchestrates LLM, guard, tools, and feedback.

**Decision:** `AgentLoop` executes only `ALLOW` verdicts (and
session-approved `ASK_ONCE`).  `ASK_ALWAYS` actions are skipped and
turned into a failed `ToolResult` (`"blocked by guard: ask_always"`)
that flows into the feedback engine and conversation, so the LLM sees
why its action was refused.

**Reasons:** Keeps the loop non-interactive and deterministic; the
blocked result drives the LLM to choose a safe alternative.

**Alternatives considered:** Having the loop prompt the user —
rejected, prompting belongs to the REPL layer.

---

## 2026-07-08 — Task 10: guard verdicts are decided by code, not prompt

**Context:** SPEC 3.4 requires screening every action before dispatch.

**Decision:** `GuardEngine.check` returns `ALLOW`/`ASK_ONCE`/`ASK_ALWAYS`
via deterministic rules in priority order: unknown tool -> ASK_ALWAYS;
dangerous command pattern -> ASK_ALWAYS; path escapes project root
(write_file path / run_shell cwd) -> ASK_ALWAYS; else map risk level
(LOW -> ALLOW, MEDIUM -> ASK_ONCE, HIGH -> ASK_ALWAYS).  Pattern list
covers bypass variants (`rm -fr`, `rm -r -f`, `rm --recursive
--force`, `chmod 0777`, ...).  `run_shell` without an explicit `cwd` is
denied (missing cwd cannot be verified).

**Reasons:** Security decisions must be auditable and untweakable by
prompt injection; deny-by-default on anything unverifiable.

**Alternatives considered:** LLM-based risk assessment — rejected,
non-deterministic and promptable.

---

## 2026-07-08 — Task 11: deterministic feedback mechanisms are the main contribution

**Context:** SPEC 3.5 wants failure classification and correction
guidance.

**Decision:** `FailureClassifier` (regex-based, 9 categories, deepest
traceback frame for file/line), `StrategySelector` (per-category
guidance), `LoopController` (stop/retry/escalate with consecutive-same
and regression rules), all pure functions of their inputs.  `ToolResult`
-> `FeedbackContext` orchestrated by `FeedbackEngine.evaluate`.

**Reasons:** Every mechanism is testable and verifiable without an LLM
— the SPEC's A.6 "deep dimension" demonstration depends on this.

---

## 2026-07-08 — Task 3/4: config merge semantics and credential storage

**Context:** Config sources and API key handling.

**Decision:** Three-level merge (defaults <
`~/.coding-harness/config.toml` < `./harness.toml`); kebab-case keys
normalized to dataclass fields; `guard.extra_dangerous_patterns`
APPENDS across levels so a project can never wipe out user extras or
built-ins.  API key lives in the OS keyring (`CredentialStore`), with
`DEEPSEEK_API_KEY` env fallback; status reports presence only, never
the value.

**Reasons:** Predictable merge priority; secure-by-default credential
handling; CI-friendly env fallback.

---

## 2026-07-08 — Task 5: MockBackend for determinism

**Context:** Tests and demos need a backend with no network.

**Decision:** `MockBackend` replays a script of `LLMResponse`s in
order, then returns a fresh `LLMResponse(content="DONE")` forever after
(never a shared mutable default).

**Reasons:** Scripted determinism plus guaranteed loop termination.

---

## Template

```markdown
## YYYY-MM-DD — Short title

**Context:** (why this decision was needed)

**Decision:** (what was done, in one or two sentences)

**Reasons:** (the concrete costs/benefits that drove the choice)

**Alternatives considered:** (other options and why they lost)
```
