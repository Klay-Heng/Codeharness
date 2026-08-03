"""Tests for ResponseParser, which turns LLMResponse.tool_calls into Actions.

ResponseParser (src/codeharness/parser.py) maps each tool call dict
``{"name": ..., "params": ...}`` onto an ``Action`` with an auto-generated
ID, dropping calls whose name is empty or missing.  Covers: parsing tool
calls, no tool calls, an empty response, unique action IDs, and filtering
of empty tool names.
"""
from __future__ import annotations

from codeharness.models import Action, LLMResponse
from codeharness.parser import ResponseParser

WRITE_CALL = {"name": "write_file", "params": {"path": "test.py", "content": "print(1)"}}
SHELL_CALL = {"name": "run_shell", "params": {"command": "pytest"}}
READ_CALL = {"name": "read_file", "params": {"path": "src/main.py"}}


def make_parser() -> ResponseParser:
    return ResponseParser()


def test_parse_tool_calls_into_actions():
    response = LLMResponse(content="", tool_calls=[WRITE_CALL, SHELL_CALL])
    actions = make_parser().parse(response)
    assert len(actions) == 2
    assert isinstance(actions[0], Action)
    assert actions[0].tool == "write_file"
    assert actions[0].params == {"path": "test.py", "content": "print(1)"}
    assert actions[1].tool == "run_shell"
    assert actions[1].params == {"command": "pytest"}


def test_parse_no_tool_calls_returns_empty():
    response = LLMResponse(content="just a reply", tool_calls=[])
    assert make_parser().parse(response) == []


def test_parse_empty_response_returns_empty():
    response = LLMResponse()
    assert make_parser().parse(response) == []


def test_actions_have_unique_ids():
    response = LLMResponse(tool_calls=[WRITE_CALL, SHELL_CALL, READ_CALL])
    actions = make_parser().parse(response)
    assert len(actions) == 3
    ids = [action.action_id for action in actions]
    assert all(ids)  # every action has a non-empty auto-generated ID
    assert len(set(ids)) == 3  # and they are all unique


def test_empty_tool_names_filtered():
    response = LLMResponse(
        tool_calls=[
            WRITE_CALL,
            {"name": "", "params": {"path": "x.py"}},
            {"name": None, "params": {"path": "y.py"}},
            SHELL_CALL,
        ]
    )
    actions = make_parser().parse(response)
    assert [action.tool for action in actions] == ["write_file", "run_shell"]


def test_missing_params_default_to_empty_dict():
    response = LLMResponse(tool_calls=[{"name": "no_args"}])
    actions = make_parser().parse(response)
    assert len(actions) == 1
    assert actions[0].tool == "no_args"
    assert actions[0].params == {}
