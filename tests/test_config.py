"""Tests for the config store (src/codeharness/config.py).

Covers SPEC section 3.7: TOML loading, priority merging
(defaults < user-level < project-level), and the append semantics
of ``guard.extra_dangerous_patterns``.
"""
from __future__ import annotations

from pathlib import Path

from codeharness.config import ConfigLoader
from codeharness.models import DEFAULT_DANGEROUS_PATTERNS, Config


def _write_user_config(dir_: Path, body: str) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    path = dir_ / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def _write_project_config(project_dir: Path, body: str) -> Path:
    path = project_dir / "harness.toml"
    path.write_text(body, encoding="utf-8")
    return path


class TestDefaultConfig:
    def test_load_returns_defaults_when_no_config_files(self, tmp_path):
        """No user or project config -> pure dataclass defaults."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        loader = ConfigLoader(user_config_dir=empty_dir)
        config = loader.load(project_dir=empty_dir)

        assert isinstance(config, Config)
        assert config.llm.provider == "deepseek"
        assert config.llm.model == "deepseek-chat"
        assert config.feedback.max_correction_rounds == 5
        assert config.feedback.signal_sources == ["pytest", "ruff", "mypy"]
        assert config.guard.dangerous_patterns == DEFAULT_DANGEROUS_PATTERNS
        assert config.guard.extra_dangerous_patterns == []
        assert config.guard.session_approval is True
        assert config.memory.max_decisions_loaded == 10
        assert config.tools.disabled == []
        assert config.tools.shell_timeout == 60
        assert config.project.name == ""
        assert config.project.language == "python"

    def test_user_config_dir_defaults_to_home(self):
        loader = ConfigLoader()
        assert loader.user_config_dir == Path.home() / ".coding-harness"


class TestProjectOverridesDefaults:
    def test_project_config_overrides_defaults(self, tmp_path):
        _write_project_config(
            tmp_path,
            """
[feedback]
max_correction_rounds = 8
max_same_error = 2

[project]
name = "demo-project"
language = "python"
""",
        )
        loader = ConfigLoader(user_config_dir=tmp_path / "nouser")
        config = loader.load(project_dir=tmp_path)

        assert config.feedback.max_correction_rounds == 8
        assert config.feedback.max_same_error == 2
        assert config.project.name == "demo-project"
        assert config.project.language == "python"
        # Unrelated fields keep defaults.
        assert config.llm.model == "deepseek-chat"
        assert config.guard.session_approval is True


class TestUserLevelConfig:
    def test_user_config_applied(self, tmp_path):
        user_dir = tmp_path / "user"
        _write_user_config(
            user_dir,
            """
[llm]
model = "deepseek-v3"
temperature = 0.7

[memory]
max_decisions_loaded = 20
""",
        )
        loader = ConfigLoader(user_config_dir=user_dir)
        config = loader.load(project_dir=tmp_path)

        assert config.llm.model == "deepseek-v3"
        assert config.llm.temperature == 0.7
        assert config.memory.max_decisions_loaded == 20


class TestPriority:
    def test_project_overrides_user(self, tmp_path):
        user_dir = tmp_path / "user"
        _write_user_config(
            user_dir,
            """
[llm]
model = "user-model"

[guard]
session_approval = false
""",
        )
        _write_project_config(
            tmp_path,
            """
[llm]
model = "project-model"
""",
        )
        loader = ConfigLoader(user_config_dir=user_dir)
        config = loader.load(project_dir=tmp_path)

        # Project wins where it sets a value...
        assert config.llm.model == "project-model"
        # ...user-level value survives where the project stays silent.
        assert config.guard.session_approval is False

    def test_missing_project_config_falls_back(self, tmp_path):
        user_dir = tmp_path / "user"
        _write_user_config(
            user_dir,
            """
[llm]
model = "user-model"
""",
        )
        # project_dir has no harness.toml
        loader = ConfigLoader(user_config_dir=user_dir)
        config = loader.load(project_dir=tmp_path)

        assert config.llm.model == "user-model"
        assert config.feedback.max_correction_rounds == 5


class TestGuardPatterns:
    def test_extra_dangerous_patterns_append_across_levels(self, tmp_path):
        """User and project extras both accumulate; built-ins untouched."""
        user_dir = tmp_path / "user"
        _write_user_config(
            user_dir,
            """
[guard]
extra_dangerous_patterns = ["rm -rf /"]
""",
        )
        _write_project_config(
            tmp_path,
            """
[guard]
extra_dangerous_patterns = ["git push --force"]
""",
        )
        loader = ConfigLoader(user_config_dir=user_dir)
        config = loader.load(project_dir=tmp_path)

        assert config.guard.extra_dangerous_patterns == [
            "rm -rf /",
            "git push --force",
        ]
        # The built-in default patterns are preserved, not replaced.
        assert config.guard.dangerous_patterns == DEFAULT_DANGEROUS_PATTERNS


class TestTools:
    def test_tools_disabled_and_timeout(self, tmp_path):
        _write_project_config(
            tmp_path,
            """
[tools]
disabled = ["shell", "subprocess"]
shell_timeout = 30
""",
        )
        loader = ConfigLoader(user_config_dir=tmp_path / "nouser")
        config = loader.load(project_dir=tmp_path)

        assert config.tools.disabled == ["shell", "subprocess"]
        assert config.tools.shell_timeout == 30


class TestKeyNormalization:
    def test_kebab_case_toml_keys_accepted(self, tmp_path):
        """Kebab-case TOML keys map onto underscore dataclass fields."""
        _write_user_config(
            tmp_path,
            """
[feedback]
max-correction-rounds = 9
""",
        )
        loader = ConfigLoader(user_config_dir=tmp_path)
        config = loader.load(project_dir=tmp_path)

        assert config.feedback.max_correction_rounds == 9
