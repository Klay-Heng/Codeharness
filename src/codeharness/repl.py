"""REPL interface — rich terminal UI over the AgentLoop (SPEC 3.9, Task 13).

The REPL is the interactive front end of CodeHarness:

1. Welcome banner with model / provider / project / API-key status.
2. Task input via ``rich.prompt.Prompt.ask``: single-line, or multi-line
   with a trailing backslash (``\\``) continuation (SPEC 3.9).
3. Each task is handed to the ``AgentLoop``; the run is rendered as
   folded per-round "thought" panels (the loop's per-round decision and
   reason) plus a color-coded summary (green success / red failure /
   yellow max-rounds / blue interrupted; per-category failure colors).
4. Guard approval: :meth:`REPL.request_approval` implements the SPEC
   3.4 HITL prompt (``[y/n/session]``, no-response defaults to deny).
   The current ``AgentLoop`` has no mid-run approval hook (Task 12
   review finding), so it skips unapproved actions and the REPL reports
   the block after the run; this method is the ready-made hook for a
   future loop integration.
5. Exit on ``exit`` / ``quit`` / Ctrl+C / Ctrl+D; on exit the user is
   asked whether to save the session's key decisions to memory.

The REPL never touches the LLM, filesystem, or keyring itself: the
AgentLoop, CredentialStore, and Config are injected, and the console is
injectable for tests.
"""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.text import Text

from codeharness.credentials import CredentialStore
from codeharness.loop import AgentLoop
from codeharness.models import (
    Config,
    CorrectionRecord,
    FailureCategory,
    RunResult,
)

# ---------------------------------------------------------------------------
# Color coding (SPEC 3.9: green pass / red fail / yellow warn / blue info)
# ---------------------------------------------------------------------------

_STATUS_STYLES: dict[str, str] = {
    "success": "green",
    "failure": "red",
    "max_rounds": "yellow",
    "interrupted": "blue",
}

# Failure categories render with the same traffic-light semantics.
_CATEGORY_STYLES: dict[FailureCategory, str] = {
    FailureCategory.SYNTAX_ERROR: "red",
    FailureCategory.IMPORT_ERROR: "red",
    FailureCategory.ASSERTION_FAILURE: "red",
    FailureCategory.RUNTIME_ERROR: "red",
    FailureCategory.TIMEOUT: "red",
    FailureCategory.COMMAND_FAILED: "red",
    FailureCategory.LINT_WARNING: "yellow",
    FailureCategory.TYPE_ERROR: "yellow",
    FailureCategory.UNKNOWN: "blue",
}

_EXIT_COMMANDS = frozenset({"exit", "quit"})

_MAIN_PROMPT = "> "
_CONTINUATION_PROMPT = "... "
_APPROVAL_CHOICES = ("y", "n", "session")

# Guard error prefix produced by AgentLoop._execute_round for blocked actions.
_BLOCKED_MARKER = "blocked by guard"


class REPL:
    """Interactive terminal loop over an :class:`~codeharness.loop.AgentLoop`.

    Args:
        agent_loop:       The fully wired agent loop (Task 12).
        credential_store: Key presence reporting for the banner.
        config:           Effective configuration (model, project, rounds).
        console:          Rich console for output; injectable for tests.
    """

    def __init__(
        self,
        agent_loop: AgentLoop,
        credential_store: CredentialStore,
        config: Config,
        console: Console | None = None,
    ) -> None:
        """Create a REPL with all dependencies injected."""
        self.agent_loop = agent_loop
        self.credential_store = credential_store
        self.config = config
        self.console = console if console is not None else Console()
        self._last_task: str | None = None
        self._last_result: RunResult | None = None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Run the REPL until the user exits (exit/quit/Ctrl+C/Ctrl+D)."""
        self._show_banner()
        self.console.print(
            "Type 'exit' or 'quit' to leave; end a line with '\\' to continue."
        )
        while True:
            try:
                first = Prompt.ask(_MAIN_PROMPT)
            except (KeyboardInterrupt, EOFError):
                break
            if self._is_exit(first):
                break
            task = self._collect_input(first)
            if task is None:  # interrupted during continuation
                break
            if not task.strip():
                continue
            self._last_task = task
            result = await self.agent_loop.run(task)
            self._last_result = result
            self._render_run(result)
        self._on_exit()

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------

    def _collect_input(self, first: str) -> str | None:
        """Join continuation lines into one task; None means interrupted.

        A line ending in a backslash (``\\``) continues on the next
        line; the backslash itself is consumed and lines are joined
        with newlines (SPEC 3.9 multi-line input).
        """
        lines = [first.rstrip()]
        while lines[-1].endswith("\\"):
            lines[-1] = lines[-1][:-1]
            try:
                nxt = Prompt.ask(_CONTINUATION_PROMPT)
            except (KeyboardInterrupt, EOFError):
                return None
            lines.append(nxt.rstrip())
        return "\n".join(lines)

    @staticmethod
    def _is_exit(line: str) -> bool:
        """True for the exit commands ``exit`` / ``quit`` (case-insensitive)."""
        return line.strip().lower() in _EXIT_COMMANDS

    # ------------------------------------------------------------------
    # Guard approval (SPEC 3.4 HITL: Guard -> ASK_* -> [y/n/session])
    # ------------------------------------------------------------------

    def request_approval(self, tool: str, detail: str = "") -> str:
        """Ask whether a guard-screened action may run.

        Returns one of ``"y"`` (allow once), ``"n"`` (deny), or
        ``"session"`` (allow for the session).  The default is ``"n"`` —
        the SPEC's no-response default is to deny.  The AgentLoop
        currently has no mid-run hook to call this (Task 12 review
        finding); it is the REPL's ready-made approval primitive for a
        future loop integration.
        """
        message = f"Guard: allow '{tool}'"
        if detail:
            message += f" ({detail})"
        message += "? [y/n/session]"
        choice = Prompt.ask(
            message, choices=list(_APPROVAL_CHOICES), default="n"
        )
        return choice.strip().lower()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _show_banner(self) -> None:
        """Print the welcome banner: model, provider, project, key status."""
        lines = [
            f"Model:       {self.config.llm.model} ({self.config.llm.provider})",
            f"Project:     {self.config.project.name or 'untitled'}",
            f"API key:     {self.credential_store.check_status()}",
            f"Max rounds:  {self.config.feedback.max_correction_rounds}",
        ]
        self.console.print(
            Panel("\n".join(lines), title="CodeHarness", border_style="cyan")
        )

    def _render_run(self, result: RunResult) -> None:
        """Render one completed run: thought panels + color-coded summary."""
        if result.final_context is not None:
            for i, record in enumerate(
                result.final_context.correction_history, start=1
            ):
                self._render_round(record, i)
        blocked = self._blocked_count(result)
        if blocked:
            self.console.print(
                Text(
                    f"Guard blocked {blocked} action(s) during this run:",
                    style="yellow",
                )
            )
            for detail in self._blocked_details(result):
                self.console.print(Text(f"  - {detail}", style="dim yellow"))
        style = self._status_style(result.status)
        summary = Text()
        summary.append("[", style=style)
        summary.append(result.status, style=style)
        summary.append(f"] {result.rounds} round(s), {result.duration_ms} ms")
        self.console.print(summary)

    def _render_round(self, record: CorrectionRecord, round_number: int) -> None:
        """Render one round as a folded panel ("thoughts", collapsed)."""
        body = Text()
        decision = record.decision
        if decision is not None:
            body.append(decision.reason, style="bold")
            body.append("\n")
        if record.failures_before:
            for failure in record.failures_before:
                location = (
                    f"{failure.file}:{failure.line}" if failure.file else "?"
                )
                style = self._category_style(failure.category)
                body.append(f"  [{failure.category.name}] {location}", style=style)
                if failure.message:
                    body.append(f" — {failure.message}", style=style)
                body.append("\n")
        else:
            body.append("  No failures.", style="green")
        title = f"Round {round_number} - {decision.action if decision else 'n/a'}"
        self.console.print(
            Panel(body, title=title, expand=False, border_style="cyan")
        )

    @staticmethod
    def _status_style(status: str) -> str:
        """Rich style name for a RunResult status (green/red/yellow/blue)."""
        return _STATUS_STYLES.get(status, "blue")

    @staticmethod
    def _category_style(category: FailureCategory) -> str:
        """Rich style name for a failure category (red/yellow/blue)."""
        return _CATEGORY_STYLES.get(category, "blue")

    def _blocked_count(self, result: RunResult) -> int:
        """Count guard-blocked actions in the run's last results."""
        if result.final_context is None:
            return 0
        return sum(
            1
            for r in result.final_context.last_results
            if not r.success and r.error and _BLOCKED_MARKER in r.error
        )

    def _blocked_details(self, result: RunResult) -> list[str]:
        """Return descriptions of each guard-blocked action."""
        if result.final_context is None:
            return []
        return [
            r.error or "unknown"
            for r in result.final_context.last_results
            if not r.success and r.error and _BLOCKED_MARKER in r.error
        ]

    # ------------------------------------------------------------------
    # Exit
    # ------------------------------------------------------------------

    def _on_exit(self) -> None:
        """Exit sequence: optionally save key decisions, then goodbye."""
        if self._last_result is not None:
            try:
                if Confirm.ask(
                    "Save this session's key decisions to memory?",
                    default=False,
                ):
                    self._save_decisions()
            except (KeyboardInterrupt, EOFError):
                pass  # interrupted while asking -> skip saving
        self.console.print("[dim]Goodbye.[/dim]")

    def _save_decisions(self) -> None:
        """Persist the last run's outcome as a memory decision (SPEC 3.6)."""
        memory = getattr(self.agent_loop, "memory", None)
        if memory is None or self._last_task is None or self._last_result is None:
            return
        history = []
        if self._last_result.final_context is not None:
            history = self._last_result.final_context.correction_history
        reasons = [
            f"Round {record.round_id}: "
            f"{record.decision.action} - {record.decision.reason}"
            for record in history
            if record.decision is not None
        ]
        title = self._last_task.strip() or "Session task"
        memory.save_decision(
            title=title[:60],
            context=f"Task: {self._last_task}",
            decision=(
                f"Run finished with status '{self._last_result.status}' "
                f"after {self._last_result.rounds} round(s)"
            ),
            reasons=reasons,
            alternatives=[],
        )
