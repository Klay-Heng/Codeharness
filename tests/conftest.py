"""Shared pytest fixtures for CodeHarness tests."""
import pytest
from pathlib import Path


@pytest.fixture
def temp_project(tmp_path):
    """Create a temporary project directory with basic structure."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("def hello(): return 'world'\n")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_main.py").write_text(
        "from src.main import hello\n\n\ndef test_hello():\n    assert hello() == 'world'\n"
    )
    return tmp_path


@pytest.fixture
def empty_config_dir(tmp_path):
    """Empty directory with no harness.toml."""
    return tmp_path
