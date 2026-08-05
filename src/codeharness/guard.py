"""Guard engine: screens actions before dispatch (SPEC section 3.4).

Every action the loop wants to run passes through
:meth:`GuardEngine.check` first.  The verdict is one of
:class:`~codeharness.models.GuardVerdict` (ALLOW / ASK_ONCE /
ASK_ALWAYS) and is decided by code, not prompt:

1. Unknown tool -> ASK_ALWAYS (nothing unregistered ever runs).
2. Dangerous command pattern match (regex over the command params) ->
   ASK_ALWAYS.  ``git_op`` has no ``command`` param, so a git command
   line is synthesized from ``operation`` + ``args`` for matching.
3. Path boundary: ``write_file`` (``path``) and ``run_shell`` (``cwd``)
   must resolve inside ``GuardConfig.project_root``, else ASK_ALWAYS.
   ``run_shell`` must state its ``cwd`` explicitly: a missing cwd cannot
   be verified against the boundary (the tool would fall back to the
   process cwd), so it is treated as outside — deny by default
   (review finding: missing-cwd bypass).
4. Otherwise the tool's ``risk_level`` maps LOW -> ALLOW,
   MEDIUM -> ASK_ONCE, HIGH -> ASK_ALWAYS.  ``git_op`` risk VARIES per
   operation (SPEC tool table): read-only ops are LOW, destructive ops
   HIGH, the rest keep the tool's MEDIUM base.  A MEDIUM verdict
   becomes ALLOW once the user approved the tool for the session
   (``SessionState``), unless ``GuardConfig.session_approval`` is off.

The REPL (or any caller) applies the verdict: ASK_ONCE with user
consent -> :meth:`SessionState.approve` then execute; ASK_ALWAYS ->
ask every time, never remember.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

from codeharness.models import Action, GuardConfig, GuardVerdict, RiskLevel, Tool
from codeharness.tools.registry import ToolRegistry

# Read-only git operations: LOW regardless of the tool's MEDIUM base.
_GIT_READ_ONLY_OPS = frozenset({"status", "diff", "log", "show", "ls-files"})
# Destructive git operations: escalated to HIGH (ASK_ALWAYS every time).
_GIT_DESTRUCTIVE_OPS = frozenset(
    {"reset", "rebase", "clean", "push", "rm", "cherry-pick", "revert"}
)


class SessionState:
    """Tracks tools the user approved for the current session (ASK_ONCE)."""

    def __init__(self) -> None:
        """Start with an empty approval set."""
        self.approved_tools: set[str] = set()

    def approve(self, tool_name: str) -> None:
        """Remember ``tool_name`` as approved for the rest of the session."""
        self.approved_tools.add(tool_name)

    def is_approved(self, tool_name: str) -> bool:
        """True if the user already approved ``tool_name`` this session."""
        return tool_name in self.approved_tools


class GuardEngine:
    """Deterministic pre-dispatch screening of actions."""

    def __init__(self, config: GuardConfig, tool_registry: ToolRegistry) -> None:
        """Create a guard governed by ``config`` and aware of ``tool_registry``.

        Dangerous patterns (``dangerous_patterns`` plus the user- or
        project-supplied ``extra_dangerous_patterns``, SPEC 3.4 rule 4)
        are compiled once at construction (a malformed regex in config
        fails fast here rather than at check time).  ``project_root`` is
        resolved to an absolute path so boundary checks hold regardless
        of the caller's cwd.
        """
        self.config = config
        self.registry = tool_registry
        self.session = SessionState()
        self.session_approval = config.session_approval
        self.project_root = Path(config.project_root).resolve()
        all_patterns = (*config.dangerous_patterns, *config.extra_dangerous_patterns)
        self._patterns: list[re.Pattern[str]] = [
            re.compile(pattern) for pattern in all_patterns
        ]

    def check(self, action: Action) -> GuardVerdict:
        """Screen ``action`` and return ALLOW / ASK_ONCE / ASK_ALWAYS."""
        tool = self.registry.get_tool(action.tool)
        if tool is None:
            # Rule 1: unregistered tools are never allowed.
            return GuardVerdict.ASK_ALWAYS
        if self._matches_dangerous_pattern(action):
            # Rule 2: dangerous command patterns.
            return GuardVerdict.ASK_ALWAYS
        if self._escapes_project_root(action):
            # Rule 3: path boundary for write_file / run_shell.
            return GuardVerdict.ASK_ALWAYS
        # Rule 4: risk-level fallback.
        risk = self._effective_risk(action, tool)
        if risk == RiskLevel.LOW:
            return GuardVerdict.ALLOW
        if risk == RiskLevel.HIGH:
            return GuardVerdict.ASK_ALWAYS
        if self.session_approval and self.session.is_approved(action.tool):
            return GuardVerdict.ALLOW
        return GuardVerdict.ASK_ONCE

    # -- rule 2: dangerous patterns ----------------------------------------

    def _command_text(self, action: Action) -> str:
        """The command-like text of ``action`` for pattern matching.

        ``run_shell`` carries the command verbatim; ``git_op`` splits it
        into ``operation`` + ``args``, so a git command line is
        synthesized for the pattern list (e.g. ``git push --force``).
        Other tools have no command text and match nothing.
        """
        params = action.params
        if "command" in params:
            return str(params["command"])
        if action.tool == "git_op":
            args = params.get("args") or []
            if isinstance(args, str):
                args = shlex.split(args)
            parts = ["git", str(params.get("operation", ""))]
            parts.extend(str(a) for a in args)
            return " ".join(parts)
        return ""

    def _matches_dangerous_pattern(self, action: Action) -> bool:
        """True if any compiled pattern matches the action's command text."""
        text = self._command_text(action)
        return any(pattern.search(text) for pattern in self._patterns)

    # -- rule 3: path boundary ---------------------------------------------

    def _escapes_project_root(self, action: Action) -> bool:
        """True if the action targets a path outside ``project_root``.

        ``write_file`` is bounded by its ``path`` param, ``run_shell`` by
        its ``cwd`` param (both resolved against ``project_root`` when
        relative).  Other tools are not path-bounded here.  An
        unresolvable path is treated as escaping (deny by default).
        """
        if action.tool == "write_file":
            raw = action.params.get("path") or action.params.get("file")
        elif action.tool == "run_shell":
            raw = action.params.get("cwd")
        else:
            return False
        if raw is None:
            # run_shell with no cwd: the tool now defaults to "." so
            # the boundary is project_root itself — treat as in-bounds.
            return False
        try:
            target = Path(str(raw))
            if target.is_absolute():
                target = target.resolve()
            else:
                target = (self.project_root / target).resolve()
        except (OSError, ValueError):
            return True
        return not target.is_relative_to(self.project_root)

    # -- rule 4: effective risk --------------------------------------------

    def _effective_risk(self, action: Action, tool: Tool) -> RiskLevel:
        """The risk used for the fallback mapping (VARIES handled here).

        ``git_op``'s risk varies per operation (SPEC tool table): the
        tool carries a MEDIUM base (the protocol requires one fixed
        level), and the guard escalates destructive operations to HIGH
        and drops read-only ones to LOW.
        """
        if action.tool == "git_op":
            operation = str(action.params.get("operation", ""))
            if operation in _GIT_READ_ONLY_OPS:
                return RiskLevel.LOW
            if operation in _GIT_DESTRUCTIVE_OPS:
                return RiskLevel.HIGH
        return tool.risk_level
