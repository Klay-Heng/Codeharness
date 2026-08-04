# CodeHarness

A local CLI coding agent harness with a deterministic, testable
feedback-loop engine — the main contribution of the project.  CodeHarness
runs an LLM-driven agent loop over your project, screens every tool
action through a guard engine, classifies failures with a code-based
classifier, and drives corrections with per-category strategies and loop
control — all inspectable, verifiable, and LLM-free.

## Project Overview

CodeHarness is a coding agent that operates on your local filesystem and
terminal:

- **Agent loop** — the LLM proposes tool calls, the harness executes
  them, and the outcome is fed back as structured correction guidance.
- **Guard engine** — every action is screened before dispatch:
  dangerous command patterns (`rm -rf /`, `sudo`, fork bombs, ...),
  path-boundary escapes, and per-tool risk levels produce deterministic
  `ALLOW` / `ASK_ONCE` / `ASK_ALWAYS` verdicts.
- **Feedback engine** (main contribution) — a deterministic,
  regex-based `FailureClassifier` parses tool output into 9 failure
  categories, a `StrategySelector` maps each category to correction
  guidance, and a `LoopController` decides stop/retry/escalate with
  regression detection.  No LLM is involved in the mechanism itself.
- **8 built-in tools** — `read_file`, `write_file`, `search_code`,
  `glob_files`, `run_shell`, `run_tests`, `git_op`, `package_op`.
- **Cross-session memory** — project conventions and past decisions are
  persisted as Markdown and injected into the system prompt.
- **Interactive REPL** — a `codeharness` terminal session that asks
  before risky actions and shows the loop's reasoning.
- **Deterministic demos** — `tests/demo_mechanisms.py` demonstrates the
  guard, the feedback loop, and classifier precision with a scripted
  `MockBackend` (no network, no real LLM).

## Installation

Requires Python 3.12+.

```bash
pip install codeharness          # published distribution
```

For development (editable install with pytest/ruff):

```bash
pip install -e ".[dev]"
```

## Quick Start

```bash
codeharness setup                # configure your DeepSeek API key (first time)
codeharness status               # confirm the key is set
codeharness                      # start the interactive REPL
```

Inside the REPL, give the agent a task, e.g.:

```
> Write a hello world function with a test
```

The agent will read/write files, run tests, and correct its own failures
through the feedback loop.  Risky actions are flagged by the guard and
require your approval.

## API Key Security

- The DeepSeek API key is stored in your **OS keyring** (service
  `CodeHarness`) — never in a config file and never in git.
- In environments without a keyring backend (e.g. headless CI), the
  `DEEPSEEK_API_KEY` environment variable is used as a fallback.
- The key value is **never logged, printed, or reported**.
  `codeharness status` reports presence only (`set` / `not_set`).
- `codeharness setup --reset` replaces an existing key;
  `codeharness setup --clear` removes it.

## Commands Reference

| Command | Description |
|---|---|
| `codeharness setup` | Configure the DeepSeek API key (first time) |
| `codeharness setup --reset` | Replace the existing API key |
| `codeharness setup --clear` | Remove the API key |
| `codeharness status` | Show key status and effective model/provider |
| `codeharness` | Start the interactive REPL |
| `make install` | Install package + dev deps in editable mode |
| `make test` | Run all unit tests (`pytest tests/ -v`) |
| `make lint` | Run ruff linter (`ruff check src/ tests/`) |
| `make clean` | Remove build artifacts |
| `pytest tests/demo_mechanisms.py -v` | Run the mechanism demonstrations |
| `python tests/demo_mechanisms.py` | Same demos as a plain script |

## Configuration

Configuration is merged from three sources, lowest priority first:

1. Built-in defaults (`src/codeharness/models.py`)
2. User level — `~/.coding-harness/config.toml`
3. Project level — `./harness.toml` (wins on conflicts)

See [`harness.toml`](harness.toml) for a fully commented example.  Each
top-level section (`llm`, `feedback`, `guard`, `memory`, `tools`,
`project`) maps to a config dataclass.  TOML keys may be kebab-case or
snake_case.  `guard.extra_dangerous_patterns` is **appended** across
levels (a project's extras never wipe out user extras or built-ins);
all other list fields are replaced as written.

## Distribution

- **Packaging** — `src/` layout with `pyproject.toml`; the
  `codeharness` console script is defined as `codeharness.main:app`.
- **CI** — `.github/workflows/ci.yml` runs on push/PR to `main`:
  checkout, Python 3.12, `pip install -e ".[dev]"`, full pytest suite,
  the mechanism demo script, and `ruff check src/ tests/`.  A
  placeholder `DEEPSEEK_API_KEY` is injected so CI never touches real
  credentials.
- **Publishing** — `python -m build` then `twine upload dist/*` (see
  `make clean` first for a fresh artifact set).

## Known Limitations

- **Classifier is regex-based** — failure classification targets
  pytest/ruff/mypy-style output; other test frameworks or exotic
  tracebacks may fall back to `UNKNOWN`.
- **`run_shell` uses `shell=True`** — the guard screens commands but
  does not sandbox them; always run the harness on projects you trust.
- **DeepSeek-only provider** — the LLM backend speaks the OpenAI SDK to
  the DeepSeek API; other providers require a new backend.
- **Memory is plain Markdown** — no dedup or semantic indexing beyond
  filename sorting.
- **Keyring dependency** — without a keyring backend the key falls back
  to `DEEPSEEK_API_KEY` (see API Key Security).

## License

MIT — the LICENSE file is included in the published distribution.
