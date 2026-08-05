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
      else is skipped
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
    LoopDecision,
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
    """The core task-execution loop of the CodeHarness agent."""

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
        """Execute ``task`` to completion.

        The loop follows a simple protocol:

        1. Call the LLM with the current conversation and tool list.
        2. If the LLM returns no tool calls, it is signalling completion
           — return success (or push back once if the response is empty).
        3. Otherwise execute every tool call, feed results back into the
           conversation, and evaluate through the feedback engine.
        4. If the feedback engine escalates (repeated errors / regression),
           stop.  Otherwise **always give the LLM another turn** so it can
           read → think → write → test across multiple rounds.
        5. ``max_correction_rounds`` is a hard safety limit.
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

            # Build the assistant message with tool_calls for API compliance.
            assistant_content = response.content or ""
            assistant_tool_calls = [
                {
                    "id": action.action_id,
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["params"]),
                    },
                }
                for tc, action in zip(response.tool_calls, actions)
            ]
            messages.append(Message(
                role="assistant",
                content=assistant_content,
                tool_calls=assistant_tool_calls if assistant_tool_calls else None,
            ))

            # ---- No tool calls: the LLM considers the task done ----------
            if not actions:
                if not response.content.strip() and round_number == 1:
                    # Completely empty first response — push back once.
                    messages.append(Message(
                        role="user",
                        content=(
                            "You produced no output and no tool calls. "
                            "Please use the available tools to work on "
                            "the task."
                        ),
                    ))
                    round_number += 1
                    continue
                # Non-empty text or later round: genuine completion.
                status = "success"
                history.append(
                    CorrectionRecord(
                        round_id=round_number,
                        action_ids=[],
                        failures_before=[],
                        files_touched=set(),
                        decision=LoopDecision(
                            action="stop_success",
                            reason="LLM signalled completion (no tool calls)",
                            round_number=round_number,
                        ),
                    )
                )
                break

            # ---- Execute the round's tool calls --------------------------
            results, action_ids, files_touched = await self._execute_round(
                actions, messages
            )
            last_results = results

            # If every action was blocked, tell the LLM so it can try
            # a different approach.
            blocked = [
                r for r in results
                if not r.success and r.error
                and "blocked by guard" in (r.error or "")
            ]
            if blocked and len(blocked) == len(results):
                blocked_detail = "\n".join(f"  - {r.error}" for r in blocked)
                messages.append(Message(
                    role="user",
                    content=(
                        "All of your actions were blocked by the guard:\n"
                        f"{blocked_detail}\n"
                        "Please use different tools or parameters. "
                        "For shell commands, include a 'cwd' parameter."
                    ),
                ))

            # ---- Feedback: classify failures, check escalation -----------
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

            if ctx.decision is not None and ctx.decision.action == "escalate":
                status = "interrupted"
                break

            # Hard stop at max rounds (only if the LLM truly cannot fix).
            if round_number >= max_rounds:
                status = "max_rounds"
                break

            # ---- Always give the LLM another turn ------------------------
            # Inject the serialized feedback so the LLM sees tool results
            # and can decide the next step (read → write → test → ...).
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
        platform_name = platform.system()
        if platform_name == "Windows":
            shell_hint = "Use Windows commands (dir, type, findstr) not Unix (ls, cat, grep)."
        else:
            shell_hint = "Use Unix commands (ls, cat, grep) appropriate for your platform."

        parts = [
            "You are CodeHarness, an autonomous coding agent.",
            f"Environment: {platform_name} ({platform.release()}). {shell_hint}",
            "",
            "LANGUAGE: Respond in the same language as the user's request. "
            "If the user writes in Chinese (中文), respond in Chinese. "
            "If the user writes in English, respond in English.",
            "",
            "IMPORTANT:",
            "- When asked to create/modify a file, call write_file to write it.",
            "- When asked a question, read relevant files and then give a "
            "natural-language answer summarizing your findings.",
            "- Every run_shell call MUST include a 'cwd' parameter.",
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
