"""Configuration store: loads TOML files and merges them into a Config.

Per SPEC section 3.7, config sources are merged with priority
(lowest to highest):

1. Dataclass defaults in ``codeharness.models``
2. User-level    ``~/.coding-harness/config.toml``
3. Project-level ``./harness.toml`` (next to the target project)

Every TOML top-level section (``llm``, ``feedback``, ``guard``,
``memory``, ``tools``, ``project``) maps onto the matching dataclass
field of :class:`~codeharness.models.Config`.  TOML keys may be
written in kebab-case (``max-correction-rounds``); they are
normalized to the underscore field names.

``guard.extra_dangerous_patterns`` is special: patterns from each
level APPEND to the accumulated list instead of replacing it, so a
project's extras never wipe out the user's own extras.  All other
list fields (e.g. ``tools.disabled``) are replaced as written.

``tomli`` is used rather than the stdlib ``tomllib`` so the same
code runs on Python < 3.11.
"""
from __future__ import annotations

from pathlib import Path

import tomli

from codeharness.models import Config

# TOML section name (kebab-case) -> Config dataclass field name.
_SECTION_FIELDS = {
    "llm": "llm",
    "feedback": "feedback",
    "guard": "guard",
    "memory": "memory",
    "tools": "tools",
    "project": "project",
}

# Keys whose lists accumulate across merge levels instead of being
# replaced (SPEC 3.7: extra dangerous patterns are appended to the
# built-in/previous patterns).
_APPEND_KEYS = {"extra_dangerous_patterns"}


class ConfigLoader:
    """Loads and merges TOML config files into an effective Config."""

    USER_CONFIG_FILENAME = "config.toml"
    PROJECT_CONFIG_FILENAME = "harness.toml"

    def __init__(self, user_config_dir: Path | None = None) -> None:
        """Create a loader; user config lives in ``user_config_dir``.

        Defaults to ``~/.coding-harness``.  The directory (and its
        ``config.toml``) may not exist yet; missing files are simply
        skipped during :meth:`load`.
        """
        if user_config_dir is None:
            user_config_dir = Path.home() / ".coding-harness"
        self.user_config_dir = Path(user_config_dir)

    def load(self, project_dir: Path | None = None) -> Config:
        """Merge defaults, user-level, then project-level config.

        ``project_dir`` defaults to the current working directory;
        its ``harness.toml`` is applied last and wins on conflicts.
        Returns a fresh :class:`~codeharness.models.Config` on every
        call; callers may mutate it freely.
        """
        config = Config()
        self._merge(config, self.user_config_dir / self.USER_CONFIG_FILENAME)
        if project_dir is None:
            project_dir = Path.cwd()
        self._merge(config, Path(project_dir) / self.PROJECT_CONFIG_FILENAME)
        return config

    def _merge(self, config: Config, path: Path) -> None:
        """Read one TOML file and fold its values into ``config``.

        A missing file is a no-op (fallback to whatever is already
        merged).  Unknown sections and unknown keys are ignored so a
        forward-looking config file does not crash older versions.
        """
        if not path.is_file():
            return
        with path.open("rb") as fh:
            data = tomli.load(fh)
        for section, values in data.items():
            field_name = _SECTION_FIELDS.get(section)
            if field_name is None or not isinstance(values, dict):
                continue
            section_obj = getattr(config, field_name)
            for key, value in values.items():
                attr = key.replace("-", "_")
                if not hasattr(section_obj, attr):
                    continue
                if attr in _APPEND_KEYS and isinstance(value, list):
                    # Append (user extras survive project merge).
                    getattr(section_obj, attr).extend(value)
                else:
                    setattr(section_obj, attr, value)
