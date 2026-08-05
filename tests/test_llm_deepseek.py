"""Tests for the DeepSeek backend (src/codeharness/llm/deepseek.py).

DeepSeekBackend wraps the OpenAI SDK (AsyncOpenAI) pointed at
DeepSeek's API.  These tests never touch the network: AsyncOpenAI is
replaced with a recording fake client, and the credential store with a
fake.  Coverage: constructor config, message formatting (including
``tool_call_id`` on tool messages), tool-call parsing (json.loads of
``function.arguments``), OpenAI-style tool formatting, and the
no-tools / no-tool-calls / malformed-arguments edge cases.
"""
from __future__ import annotations

import pytest

import codeharness.llm.deepseek as deepseek_module
from codeharness.llm.deepseek import DeepSeekBackend
from codeharness.models import LLMConfig, Message

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeCredentialStore:
    """Returns a fixed key (or None) without touching the keyring."""

    def __init__(self, key: str | None = "sk-test-123") -> None:
        self._key = key

    def get_key(self) -> str | None:
        return self._key


class _FakeFunction:
    def __init__(self, name: str, arguments: str | None) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, name: str, arguments: str | None = None) -> None:
        self.id = f"fake_{name}"
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content: str | None, tool_calls: list | None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage, finish_reason: str = "stop") -> None:
        self.message = message
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(
        self,
        content: str = "DONE",
        tool_calls: list | None = None,
        finish_reason: str = "stop",
    ) -> None:
        self.choices = [_FakeChoice(_FakeMessage(content, tool_calls), finish_reason)]


class _RecordingCompletions:
    def __init__(self, client: _RecordingClient) -> None:
        self._client = client

    async def create(self, **kwargs):
        self._client.create_kwargs = kwargs
        return self._client.response


class _RecordingChat:
    def __init__(self, client: _RecordingClient) -> None:
        self.completions = _RecordingCompletions(client)


class _RecordingClient:
    """Fake AsyncOpenAI: records init kwargs and the last create() call."""

    def __init__(self, response: _FakeResponse | None = None) -> None:
        self.init_kwargs: dict = {}
        self.create_kwargs: dict | None = None
        self.response = response or _FakeResponse()
        self.chat = _RecordingChat(self)


def _install_fake_client(monkeypatch, response: _FakeResponse | None = None) -> _RecordingClient:
    """Replace AsyncOpenAI with a recording fake; return the fake instance."""
    client = _RecordingClient(response=response)

    def factory(**kwargs):
        client.init_kwargs = dict(kwargs)
        return client

    monkeypatch.setattr(deepseek_module, "AsyncOpenAI", factory)
    return client


def _make_backend(config: LLMConfig | None = None, key: str | None = "sk-test-123"):
    """A DeepSeekBackend with the fake client installed."""
    store = FakeCredentialStore(key)
    backend = DeepSeekBackend(config or LLMConfig(), store)
    return backend


def _config(**overrides) -> LLMConfig:
    config = LLMConfig()
    for name, value in overrides.items():
        setattr(config, name, value)
    return config


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_raises_without_api_key(monkeypatch):
    _install_fake_client(monkeypatch)
    with pytest.raises(ValueError, match="setup"):
        DeepSeekBackend(LLMConfig(), FakeCredentialStore(None))


def test_constructor_configures_openai_client(monkeypatch):
    client = _install_fake_client(monkeypatch)
    config = _config(api_base="https://api.deepseek.example", model="deepseek-reasoner",
                     max_tokens=4096, temperature=0.3)
    backend = DeepSeekBackend(config, FakeCredentialStore("sk-secret"))

    assert client.init_kwargs["api_key"] == "sk-secret"
    assert client.init_kwargs["base_url"] == "https://api.deepseek.example"
    assert backend.model == "deepseek-reasoner"
    assert backend.max_tokens == 4096
    assert backend.temperature == 0.3


# ---------------------------------------------------------------------------
# chat(): message formatting and response mapping
# ---------------------------------------------------------------------------


async def test_chat_formats_messages_and_maps_response(monkeypatch):
    client = _install_fake_client(
        monkeypatch, response=_FakeResponse(content="here is the fix", finish_reason="stop")
    )
    backend = _make_backend()
    messages = [
        Message(role="system", content="You are CodeHarness."),
        Message(role="user", content="fix the bug"),
        Message(role="tool", content="ran tests", tool_call_id="act-1"),
    ]

    result = await backend.chat(messages, tools=[])

    assert client.create_kwargs["messages"] == [
        {"role": "system", "content": "You are CodeHarness."},
        {"role": "user", "content": "fix the bug"},
        {"role": "tool", "content": "ran tests", "tool_call_id": "act-1"},
    ]
    assert result.content == "here is the fix"
    assert result.tool_calls == []
    assert result.finish_reason == "stop"


async def test_chat_forwards_model_max_tokens_and_temperature(monkeypatch):
    client = _install_fake_client(monkeypatch)
    backend = _make_backend(_config(max_tokens=2048, temperature=0.5))

    await backend.chat([Message(role="user", content="hi")])

    assert client.create_kwargs["model"] == backend.model
    assert client.create_kwargs["max_tokens"] == 2048
    assert client.create_kwargs["temperature"] == 0.5


async def test_chat_omits_tool_call_id_when_absent(monkeypatch):
    client = _install_fake_client(monkeypatch)
    backend = _make_backend()

    await backend.chat([Message(role="user", content="hi")])

    assert client.create_kwargs["messages"] == [{"role": "user", "content": "hi"}]


async def test_chat_finish_reason_falls_back_to_stop(monkeypatch):
    response = _FakeResponse(content="x", finish_reason=None)
    _install_fake_client(monkeypatch, response=response)
    backend = _make_backend()

    result = await backend.chat([Message(role="user", content="hi")])

    assert result.finish_reason == "stop"


# ---------------------------------------------------------------------------
# chat(): tool calls
# ---------------------------------------------------------------------------


async def test_chat_parses_tool_calls_with_json_arguments(monkeypatch):
    response = _FakeResponse(
        content=None,
        tool_calls=[
            _FakeToolCall("read_file", '{"path": "src/main.py"}'),
            _FakeToolCall("run_shell", '{"command": "pytest", "cwd": "tests"}'),
        ],
    )
    _install_fake_client(monkeypatch, response=response)
    backend = _make_backend()

    result = await backend.chat([Message(role="user", content="inspect")])

    assert result.tool_calls == [
        {"id": "fake_read_file", "name": "read_file", "params": {"path": "src/main.py"}},
        {"id": "fake_run_shell", "name": "run_shell", "params": {"command": "pytest", "cwd": "tests"}},
    ]
    assert result.content == ""
    assert result.finish_reason == "stop"


async def test_chat_malformed_arguments_do_not_crash(monkeypatch):
    response = _FakeResponse(
        content="oops",
        tool_calls=[
            _FakeToolCall("read_file", "not-json-at-all"),
            _FakeToolCall("read_file", None),
            _FakeToolCall("read_file", '["a", "list", "not", "a", "dict"]'),
        ],
    )
    _install_fake_client(monkeypatch, response=response)
    backend = _make_backend()

    result = await backend.chat([Message(role="user", content="x")])

    # Unparseable/absent/non-object arguments degrade to empty params.
    assert result.tool_calls == [
        {"id": "fake_read_file", "name": "read_file", "params": {}},
        {"id": "fake_read_file", "name": "read_file", "params": {}},
        {"id": "fake_read_file", "name": "read_file", "params": {}},
    ]
    assert result.content == "oops"


async def test_chat_no_tool_calls_gives_empty_list(monkeypatch):
    _install_fake_client(monkeypatch, response=_FakeResponse(content="DONE", tool_calls=None))
    backend = _make_backend()

    result = await backend.chat([Message(role="user", content="hi")])

    assert result.tool_calls == []


# ---------------------------------------------------------------------------
# _format_tools(): registry dicts -> OpenAI function-calling shape
# ---------------------------------------------------------------------------


async def test_chat_forwards_tools_in_openai_format(monkeypatch):
    client = _install_fake_client(monkeypatch)
    backend = _make_backend()
    registry_tools = [
        {"name": "read_file", "description": "Read a text file.", "risk_level": "low"},
        {"name": "run_shell", "description": "Run a shell command.", "risk_level": "high"},
    ]

    await backend.chat([Message(role="user", content="go")], tools=registry_tools)

    assert client.create_kwargs["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a text file.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_shell",
                "description": "Run a shell command.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]


async def test_chat_passes_none_when_no_tools(monkeypatch):
    client = _install_fake_client(monkeypatch)
    backend = _make_backend()

    await backend.chat([Message(role="user", content="hi")], tools=None)
    assert client.create_kwargs["tools"] is None

    await backend.chat([Message(role="user", content="hi")], tools=[])
    assert client.create_kwargs["tools"] is None
