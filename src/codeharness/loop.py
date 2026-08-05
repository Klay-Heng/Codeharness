"""Agent main loop — orchestrates LLM, tools, guard, and feedback.

``AgentLoop`` (SPEC 3.1, Task 12) runs one task to completion:

1. Build the initial conversation: a system prompt assembled from the
   memory store's project context plus the effective config, followed by
   the user's task.
2. Until the feedback controller says stop (or rounds run out):
   a. call the LLM for the next turn (tool descriptions included)
   b. parse the response into ``Action`` objects
   c. screen each action with the guard engine: approved actions
      (ALLOW, or ASK_ONCE already session-approved) execute; anything
      else is skipped and reported back to the loop as blocked
   d. collect the ToolResults and evaluate them through the
      FeedbackEngine (classify + strategy + loop decision)
   e. act on the LoopDecision:
        stop_success -> return success
        stop_failure -> return max_rounds (rounds exhausted) / failure
        escalate    -> return interrupted (human escalation needed)
        retry       -> serialize the feedback into the conversation and
                       take another round

All dependencies are injected (llm, tools, guard, feedback, memory,
config, parser), so the loop is fully deterministic under MockBackend
and never performs network I/O, prompts the user, or touches the
filesystem on its own.
"""
from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Literal

from codeharness.feedback import FeedbackEngine
from codeharness.guard import GuardEngine
from codeharness.memory import MemoryStore
from codeharness.models import (
    Action,
    Config,
    CorrectionRecord,
    GuardVerdict,
    LLMBackend,
    Message,
    RunResult,
    ToolResult,
    TurnContext,
)
from codeharness.parser import ResponseParser
from codeharness.tools.registry import ToolRegistry

# Approval callback: (action, verdict) -> bool (True = approved, False = denied)
ApprovalCallback = Callable[[Action, GuardVerdict], Awaitable[bool]]

_RunStatus = Literal["success", "failure", "max_rounds", "interrupted"]


class AgentLoop:
    """The core task-execution loop of the CodeHarness agent.

    Args:
        llm:      Async LLM backend (MockBackend in tests).
        tools:    Tool registry used for dispatch.
        guard:    Guard engine that screens every action pre-dispatch.
        feedback: Feedback engine (classifier + strategy + controller).
        memory:   Cross-session memory store for the system prompt.
        config:   Effective merged configuration.
        parser:   Converts LLMResponse tool calls into Actions.
    """

    def __init__(
        self,
        llm: LLMBackend,
        tools: ToolRegistry,
        guard: GuardEngine,
        feedback: FeedbackEngine,
        memory: MemoryStore,
        config: Config,
        parser: ResponseParser,
        approval_callback: ApprovalCallback | None = None,
    ) -> None:
        """Create an agent loop with all dependencies injected.

        ``approval_callback`` is an async function ``(action, verdict) ->
        bool`` called when the guard blocks an action (ASK_ONCE not yet
        approved, or ASK_ALWAYS).  If the callback returns True the
        action executes; if it returns False (or is None) the action is
        skipped.  In tests the callback is None (MockBackend).
        """
        self.llm = llm
        self.tools = tools
        self.guard = guard
        self.feedback = feedback
        self.memory = memory
        self.config = config
        self.parser = parser
        self.approval_callback = approval_callback

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run(self, task: str) -> RunResult:
        """Execute ``task`` to completion and return the run outcome.

        The run performs up to ``config.feedback.max_correction_rounds``
        rounds (1-indexed).  Each round calls the LLM, screens and
        executes its actions, and evaluates the results through the
        feedback engine; the loop stops on the first stop/escalate
        decision and otherwise injects the feedback and continues.
        """
        start = time.perf_counter()
        messages: list[Message] = [
            Message(role="system", content=self._system_prompt()),
            Message(role="user", content=task),
        ]
        history: list[CorrectionRecord] = []
        last_results: list[ToolResult] = []
        max_rounds = self.config.feedback.max_correction_rounds

        status: _RunStatus = "max_rounds"
        round_number = 1
        while round_number <= max_rounds:
            response = await self.llm.chat(
                messages, tools=self.tools.list_available()
            )
            actions = self.parser.parse(response)

            # Echo the assistant response WITH tool_calls into the
            # conversation so DeepSeek/OpenAI-compatible APIs see the
            # required assistant(tool_calls) -> tool message sequence.
            assistant_content = response.content or ""
            assistant_tool_calls = [
                {
                    "id": action.action_id,
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": json.dumps(tc["params"])},
                }
                for tc, action in zip(response.tool_calls, actions)
            ]
            messages.append(Message(
                role="assistant",
                content=assistant_content,
                tool_calls=assistant_tool_calls if assistant_tool_calls else None,
            ))

            results, action_ids, files_touched = await self._execute_round(
                actions, messages
            )
            last_results = results

            # If the LLM only performed reads (no writes/shell/tests),
            # remind it that reading alone does not complete a modify task.
            # After 2 consecutive read-only rounds, escalate.
            write_tools = {"write_file", "run_shell", "run_tests", "git_op", "package_op"}
            executed_tools = {
                a.tool for a, r in zip(actions, results)
                if r.success and "blocked" not in (r.error or "")
            }
            if executed_tools and executed_tools.isdisjoint(write_tools):
                read_only_streak = getattr(self, "_read_only_streak", 0) + 1
                self._read_only_streak = read_only_streak
                if read_only_streak >= 2:
                    status = "interrupted"
                    break
                messages.append(Message(
                    role="user",
                    content=(
                        "You have only performed read operations so far. "
                        "If the task requires modifying or creating files, "
                        "you MUST use write_file to actually write the "
                        "changes. Reading alone does not complete a "
                        "modify/create task. Please continue."
                    ),
                ))
                round_number += 1
                continue
            self._read_only_streak = 0

            # If every action was blocked, tell the LLM so it can try
            # a different approach instead of silently succeeding.
            blocked = [r for r in results if not r.success and r.error and "blocked by guard" in (r.error or "")]
            if blocked and len(blocked) == len(results):
                blocked_detail = "\n".join(
                    f"  - {r.error}" for r in blocked
                )
                messages.append(Message(
                    role="user",
                    content=(
                        "All of your actions were blocked by the guard:\n"
                        f"{blocked_detail}\n"
                        "Please use different tools or parameters. "
                        "For shell commands, always include a 'cwd' "
                        "inside the project directory."
                    ),
                ))

            # Evaluate the round: classify failures, pick strategies,
            # and let the loop controller decide what happens next.
            ctx = self.feedback.evaluate(results, round_number, history)
            history.append(
                CorrectionRecord(
                    round_id=round_number,
                    action_ids=action_ids,
                    failures_before=ctx.failures,
                    files_touched=files_touched,
                    decision=ctx.decision,
                )
            )

            if ctx.decision is None:
                status = "max_rounds"
                break
            if ctx.decision.action == "stop_success":
                status = "success"
                break
            if ctx.decision.action == "stop_failure":
                # The controller only stops with failure once rounds are
                # exhausted; keep "failure" as a defensive fallback.
                status = (
                    "max_rounds"
                    if round_number >= max_rounds
                    else "failure"
                )
                break
            if ctx.decision.action == "escalate":
                status = "interrupted"
                break

            # retry: hand the serialized feedback back to the LLM and
            # take another correction round.
            messages.append(Message(role="user", content=ctx.serialize()))
            round_number += 1

        final_context = TurnContext(
            messages=messages,
            round_count=len(history),
            last_results=last_results,
            correction_history=history,
        )
        return RunResult(
            status=status,
            rounds=len(history),
            duration_ms=int((time.perf_counter() - start) * 1000),
            final_context=final_context,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _system_prompt(self) -> str:
        """Build the system prompt from the memory context + config."""
        import platform
        parts = [
            "You are CodeHarness, an autonomous coding agent.",
            f"Environment: {platform.system()} ({platform.release()}). "
            "Use Windows commands (dir, type, findstr) not Unix (ls, cat, grep) "
            "when running shell commands.",
            "IMPORTANT: Every run_shell call MUST include 'cwd' parameter. "
            "When asked to create or modify a file, you MUST use "
            "write_file to write the file — reading alone does not complete "
            "the task. After writing, verify your work by reading the file "
            "back or running the tests.",
            f"Project: {self.config.project.name or 'untitled'}",
            (
                "Language: "
                f"{self.config.project.language}; "
                f"test framework: {self.config.project.test_framework}; "
                f"type checker: {self.config.project.type_checker}"
            ),
            f"Test command: {self.config.feedback.test_command}",
        ]
        memory_context = self.memory.build_system_context()
        if memory_context:
            parts.append(memory_context)
        return "\n".join(parts)

    async def _is_approved(
        self, verdict: GuardVerdict, action: Action
    ) -> bool:
        """True if the loop may execute ``action`` under ``verdict``.

        ALLOW always executes.  ASK_ONCE skips the callback when the
        tool was already approved this session.  ASK_ALWAYS and
        ASK_ONCE (first time) call ``self.approval_callback`` when
        one is wired; without a callback they are always denied.
        """
        if verdict is GuardVerdict.ALLOW:
            return True
        if verdict is GuardVerdict.ASK_ONCE and self.guard.session.is_approved(
            action.tool
        ):
            return True
        if self.approval_callback is not None:
            return await self.approval_callback(action, verdict)
        return False

    async def _execute_round(
        self,
        actions: list[Action],
        messages: list[Message],
    ) -> tuple[list[ToolResult], list[str], set[str]]:
        """Screen and execute one round's actions.

        Returns ``(results, action_ids, files_touched)``.  Blocked
        actions produce a failed ToolResult with a ``blocked by
        guard`` error so the feedback engine and conversation still
        see what happened; ``files_touched`` only counts writes that
        were actually executed (used for regression detection).
        """
        results: list[ToolResult] = []
        action_ids: list[str] = []
        files_touched: set[str] = set()

        for action in actions:
            action_ids.append(action.action_id)
            verdict = self.guard.check(action)
            if await self._is_approved(verdict, action):
                result = self.tools.dispatch(action)
                if action.tool == "write_file" and (
                    action.params.get("path") or action.params.get("file")
                ):
                    files_touched.add(
                        str(action.params.get("path") or action.params.get("file"))
                    )
            else:
                result = ToolResult(
                    action_id=action.action_id,
                    success=False,
                    error=(
                        f"blocked by guard: {verdict.value}"
                        f" (tool: {action.tool})"
                    ),
                )
            results.append(result)
            messages.append(
                Message(
                    role="tool",
                    content=result.output or result.error or "",
                    tool_call_id=action.action_id,
                )
            )
        return results, action_ids, files_touched
