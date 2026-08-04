"""Tests for the CLI entry point (src/codeharness/main.py).

The typer app exposes three commands: ``setup`` (guided API key entry
via getpass), ``status`` (key + model info), and the default REPL
command (no subcommand).  All tests go through typer.testing.CliRunner
with every external dependency faked: the keyring-backed
CredentialStore, the ConfigLoader, getpass, the DeepSeekBackend, and
the REPL.  Nothing touches the network, keyring, or real stdin.

The default-command wiring test verifies the full harness assembly:
all 8 tools registered, guard/feedback/memory/parser/loop/REPL
constructed with the injected credentials and config.
"""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from typer.testing import CliRunner

import codeharness.main as main_module
from codeharness.feedback import FeedbackEngine
from codeharness.guard import GuardEngine
from codeharness.loop import AgentLoop
from codeharness.memory import MemoryStore
from codeharness.models import Config
from codeharness.parser import ResponseParser
from codeharness.tools.registry import ToolRegistry

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeCredentialStore:
    """Records key operations; reports a fixed status."""

    def __init__(self, status: str = "not_set") -> None:
        self.status = status
        self.set_key_calls: list[str] = []
        self.clear_calls: int = 0

    def get_key(self) -> str | None:
        return "sk-test" if self.status == "set" else None

    def check_status(self) -> str:
        return self.status

    def set_key(self, key: str) -> None:
        self.set_key_calls.append(key)

    def clear_key(self) -> None:
        self.clear_calls += 1


class FakeConfigLoader:
    """Returns a preset Config; records loads."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        self.load_calls: int = 0

    def load(self, project_dir: Path | None = None) -> Config:
        self.load_calls += 1
        return self.config


class StubBackend:
    """Records construction; never talks to a network."""

    instances: ClassVar[list[StubBackend]] = []

    def __init__(self, config, store) -> None:
        self.config = config
        self.store = store
        StubBackend.instances.append(self)


class StubREPL:
    """Records construction and that start() was awaited."""

    instances: ClassVar[list[StubREPL]] = []

    def __init__(self, loop, store, config, console=None) -> None:
        self.loop = loop
        self.store = store
        self.config = config
        self.started = False
        StubREPL.instances.append(self)

    async def start(self) -> None:
        self.started = True


def _record_getpass(monkeypatch, value: str = "sk-abc"):
    """Replace getpass with a recording stub returning ``value``."""
    calls: list[str] = []

    def fake_getpass(prompt: str = "") -> str:
        calls.append(prompt)
        return value

    monkeypatch.setattr(main_module, "getpass", fake_getpass)
    return calls


def _install_config_loader(monkeypatch, config: Config | None = None) -> FakeConfigLoader:
    loader = FakeConfigLoader(config)
    monkeypatch.setattr(main_module, "ConfigLoader", lambda: loader)
    return loader


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------


def test_setup_prompts_and_saves_key_when_not_set(monkeypatch):
    store = FakeCredentialStore("not_set")
    monkeypatch.setattr(main_module, "CredentialStore", lambda: store)
    prompts = _record_getpass(monkeypatch, "sk-abc")

    result = runner.invoke(main_module.app, ["setup"])

    assert result.exit_code == 0
    assert prompts == ["> "]  # hidden input prompt
    assert store.set_key_calls == ["sk-abc"]
    assert "API key saved." in result.output


def test_setup_blank_key_is_not_saved(monkeypatch):
    store = FakeCredentialStore("not_set")
    monkeypatch.setattr(main_module, "CredentialStore", lambda: store)
    _record_getpass(monkeypatch, "   ")

    result = runner.invoke(main_module.app, ["setup"])

    assert result.exit_code == 0
    assert store.set_key_calls == []
    assert "No key entered." in result.output


def test_setup_refuses_when_already_configured(monkeypatch):
    store = FakeCredentialStore("set")
    monkeypatch.setattr(main_module, "CredentialStore", lambda: store)
    prompts = _record_getpass(monkeypatch, "sk-extra")

    result = runner.invoke(main_module.app, ["setup"])

    assert result.exit_code == 0
    assert "already configured" in result.output
    assert prompts == []  # no key entry was requested
    assert store.set_key_calls == []


def test_setup_reset_forces_prompt_even_when_set(monkeypatch):
    store = FakeCredentialStore("set")
    monkeypatch.setattr(main_module, "CredentialStore", lambda: store)
    _record_getpass(monkeypatch, "sk-new")

    result = runner.invoke(main_module.app, ["setup", "--reset"])

    assert result.exit_code == 0
    assert store.set_key_calls == ["sk-new"]
    assert "API key saved." in result.output


def test_setup_clear_removes_key_without_prompting(monkeypatch):
    store = FakeCredentialStore("set")
    monkeypatch.setattr(main_module, "CredentialStore", lambda: store)
    prompts = _record_getpass(monkeypatch, "sk-extra")

    result = runner.invoke(main_module.app, ["setup", "--clear"])

    assert result.exit_code == 0
    assert store.clear_calls == 1
    assert store.set_key_calls == []
    assert prompts == []
    assert "API key cleared." in result.output


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_shows_key_status_and_model(monkeypatch):
    store = FakeCredentialStore("set")
    monkeypatch.setattr(main_module, "CredentialStore", lambda: store)
    config = Config()
    config.llm.model = "deepseek-chat"
    config.llm.provider = "deepseek"
    loader = _install_config_loader(monkeypatch, config)

    result = runner.invoke(main_module.app, ["status"])

    assert result.exit_code == 0
    assert "API key: set" in result.output
    assert "Model: deepseek-chat" in result.output
    assert "Provider: deepseek" in result.output
    assert loader.load_calls == 1


def test_status_shows_not_set_when_no_key(monkeypatch):
    store = FakeCredentialStore("not_set")
    monkeypatch.setattr(main_module, "CredentialStore", lambda: store)
    _install_config_loader(monkeypatch)

    result = runner.invoke(main_module.app, ["status"])

    assert result.exit_code == 0
    assert "API key: not_set" in result.output


# ---------------------------------------------------------------------------
# default command: full harness wiring + REPL
# ---------------------------------------------------------------------------


def test_default_command_requires_key_first(monkeypatch):
    store = FakeCredentialStore("not_set")
    monkeypatch.setattr(main_module, "CredentialStore", lambda: store)

    result = runner.invoke(main_module.app, [])

    assert result.exit_code == 1
    assert "No API key configured" in result.output
    assert "setup" in result.output


def test_default_command_wires_full_harness_and_starts_repl(monkeypatch, tmp_path):
    monkeypatch.setattr(
        main_module.Path, "cwd", classmethod(lambda cls: tmp_path)
    )
    store = FakeCredentialStore("set")
    monkeypatch.setattr(main_module, "CredentialStore", lambda: store)
    config = Config()
    config.project.name = "demo"
    loader = _install_config_loader(monkeypatch, config)
    monkeypatch.setattr(main_module, "DeepSeekBackend", StubBackend)
    monkeypatch.setattr(main_module, "REPL", StubREPL)

    result = runner.invoke(main_module.app, [])

    assert result.exit_code == 0
    assert len(StubREPL.instances) == 1
    repl = StubREPL.instances[0]
    assert repl.started is True  # REPL.start() was awaited

    # Harness wiring: loop over all injected modules.
    loop = repl.loop
    assert isinstance(loop, AgentLoop)
    assert loop.config is config
    assert loop.parser is not None and isinstance(loop.parser, ResponseParser)
    assert isinstance(loop.guard, GuardEngine)
    assert isinstance(loop.feedback, FeedbackEngine)
    assert isinstance(loop.memory, MemoryStore)
    assert loop.memory.root == tmp_path / ".harness" / "memory"

    # All 8 tools registered and available.
    assert isinstance(loop.tools, ToolRegistry)
    names = {t["name"] for t in loop.tools.list_available()}
    assert names == {
        "read_file", "write_file", "search_code", "glob_files",
        "run_shell", "run_tests", "git_op", "package_op",
    }

    # LLM backend built from the loaded config + credentials.
    assert len(StubBackend.instances) == 1
    llm = StubBackend.instances[0]
    assert llm.config is config.llm
    assert llm.store is store

    # REPL got the loop, the store, and the effective config.
    assert repl.store is store
    assert repl.config is config
    assert loader.load_calls == 1
