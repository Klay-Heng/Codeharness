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

import time
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
    ) -> None:
        """Create an agent loop with all dependencies injected."""
        self.llm = llm
        self.tools = tools
        self.guard = guard
        self.feedback = feedback
        self.memory = memory
        self.config = config
        self.parser = parser

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

            results, action_ids, files_touched = self._execute_round(
                actions, messages
            )
            last_results = results

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
        parts = [
            "You are CodeHarness, an autonomous coding agent.",
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

    def _is_approved(self, verdict: GuardVerdict, action: Action) -> bool:
        """True if the loop may execute ``action`` under ``verdict``.

        ALLOW always executes.  ASK_ONCE executes only when the user
        already approved the tool for this session.  ASK_ALWAYS is
        rejected outright — an interactive caller (the REPL) would
        prompt, but the loop itself never asks.
        """
        if verdict is GuardVerdict.ALLOW:
            return True
        if verdict is GuardVerdict.ASK_ONCE:
            return self.guard.session.is_approved(action.tool)
        return False

    def _execute_round(
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
            if self._is_approved(verdict, action):
                result = self.tools.dispatch(action)
                if action.tool == "write_file" and action.params.get("path"):
                    files_touched.add(str(action.params["path"]))
            else:
                result = ToolResult(
                    action_id=action.action_id,
                    success=False,
                    error=f"blocked by guard: {verdict.value}",
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
