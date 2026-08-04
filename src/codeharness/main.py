"""CLI entry point — CodeHarness, a local CLI coding agent (Task 14).

The ``codeharness`` command (installed via the ``codeharness.main:app``
console script) has three forms:

- ``codeharness setup [--reset] [--clear]`` — guided API key entry via
  ``getpass`` (input hidden); ``--reset`` forces re-entry over an
  existing key, ``--clear`` removes the key.
- ``codeharness status`` — reports key presence and the effective
  model/provider from the merged config.
- ``codeharness`` (no subcommand) — wires the full harness
  (CredentialStore -> ConfigLoader -> DeepSeekBackend -> ToolRegistry
  (all 8 tools) -> GuardEngine -> FeedbackEngine -> MemoryStore ->
  AgentLoop -> REPL) and starts the interactive REPL.

All heavy lifting lives in the individual modules; this file only
assembles them and translates CLI conventions (flags, exit codes,
hidden input) into their interfaces.
"""
from __future__ import annotations

import asyncio
from getpass import getpass
from pathlib import Path

import typer

from codeharness.config import ConfigLoader
from codeharness.credentials import CredentialStore
from codeharness.feedback import FeedbackEngine
from codeharness.guard import GuardEngine
from codeharness.llm.deepseek import DeepSeekBackend
from codeharness.loop import AgentLoop
from codeharness.memory import MemoryStore
from codeharness.models import Config
from codeharness.parser import ResponseParser
from codeharness.repl import REPL
from codeharness.tools.file_ops import ReadFileTool, WriteFileTool
from codeharness.tools.git_ops import GitOpTool
from codeharness.tools.package_ops import PackageOpTool
from codeharness.tools.registry import ToolRegistry
from codeharness.tools.search import GlobFilesTool, SearchCodeTool
from codeharness.tools.shell import RunShellTool
from codeharness.tools.testing_tool import RunTestsTool

app = typer.Typer(help="CodeHarness - A local CLI Coding Agent")


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------


@app.command()
def setup(reset: bool = False, clear: bool = False) -> None:
    """Configure or manage the DeepSeek API key.

    Prompts for the key with input hidden (getpass).  Use --reset to
    replace an existing key, or --clear to remove it.
    """
    store = CredentialStore()
    if clear:
        try:
            store.clear_key()
        except Exception:  # noqa: BLE001 - missing key / no keyring backend
            typer.echo("No API key to clear (keyring unavailable or empty).")
        else:
            typer.echo("API key cleared.")
        return
    if reset or store.check_status() == "not_set":
        typer.echo("Enter your DeepSeek API key (input hidden):")
        try:
            key = getpass("> ")
        except KeyboardInterrupt:
            typer.echo("\nSetup cancelled.")
            raise typer.Exit(1)
        if key.strip():
            try:
                store.set_key(key.strip())
            except Exception as exc:  # noqa: BLE001 - keyring backend failure
                typer.echo(f"Could not save API key (keyring unavailable): {exc}")
                raise typer.Exit(1)
            typer.echo("API key saved.")
        else:
            typer.echo("No key entered.")
    else:
        typer.echo(
            "API key already configured. Use --reset to change or --clear to remove."
        )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command()
def status() -> None:
    """Show API key status and effective model configuration."""
    store = CredentialStore()
    typer.echo(f"API key: {store.check_status()}")
    config = ConfigLoader().load()
    typer.echo(f"Model: {config.llm.model}")
    typer.echo(f"Provider: {config.llm.provider}")


# ---------------------------------------------------------------------------
# default command: full harness wiring + REPL
# ---------------------------------------------------------------------------


def _build_registry(config: Config, project_dir: Path) -> ToolRegistry:
    """Register the 8 built-in tools under the merged tool config."""
    registry = ToolRegistry(config.tools)
    registry.register(ReadFileTool())
    registry.register(WriteFileTool(root=str(project_dir)))
    registry.register(SearchCodeTool())
    registry.register(GlobFilesTool())
    registry.register(RunShellTool(default_timeout=config.tools.shell_timeout))
    registry.register(RunTestsTool())
    registry.register(GitOpTool())
    registry.register(PackageOpTool())
    return registry


def build_harness(
    config: Config, store: CredentialStore, project_dir: Path | None = None
) -> tuple[AgentLoop, REPL]:
    """Wire the full harness and return ``(agent_loop, repl)``.

    Assembly order: registry (8 tools) -> DeepSeekBackend -> GuardEngine
    -> FeedbackEngine -> MemoryStore -> AgentLoop (with the parser) ->
    REPL.  The guard's ``project_root`` and the write_file tool are
    pinned to ``project_dir`` so path-boundary checks match where the
    agent actually works.
    """
    if project_dir is None:
        project_dir = Path.cwd()
    project_dir = Path(project_dir).resolve()

    config.guard.project_root = str(project_dir)

    registry = _build_registry(config, project_dir)
    llm = DeepSeekBackend(config.llm, store)
    guard = GuardEngine(config.guard, registry)
    feedback = FeedbackEngine(config.feedback)
    memory = MemoryStore(project_dir)
    loop = AgentLoop(
        llm=llm,
        tools=registry,
        guard=guard,
        feedback=feedback,
        memory=memory,
        config=config,
        parser=ResponseParser(),
    )
    repl = REPL(loop, store, config)
    return loop, repl


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Start the interactive REPL (default command)."""
    if ctx.invoked_subcommand is not None:
        return
    store = CredentialStore()
    if store.check_status() == "not_set":
        typer.echo("No API key configured. Run 'codeharness setup' first.")
        raise typer.Exit(1)
    config = ConfigLoader().load()
    _loop, repl = build_harness(config, store)
    asyncio.run(repl.start())


if __name__ == "__main__":
    app()
