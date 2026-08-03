"""Tests for the credential store (src/codeharness/credentials.py).

Covers secure API key storage via the OS keyring with a
``DEEPSEEK_API_KEY`` environment-variable fallback when the keyring
is unavailable.  Every test mocks the keyring backend with
``unittest.mock``; no real keyring is touched, and no key value may
ever leak into status strings or logs.
"""
from __future__ import annotations

from unittest import mock

from codeharness.credentials import CredentialStore

SECRET = "sk-secret"


class TestGetKey:
    def test_returns_password_from_keyring(self):
        with mock.patch("keyring.get_password", return_value=SECRET) as get_password:
            assert CredentialStore.get_key() == SECRET
        get_password.assert_called_once_with(
            CredentialStore.SERVICE_NAME, CredentialStore.ACCOUNT_NAME
        )

    def test_returns_none_when_not_set(self):
        with mock.patch("keyring.get_password", return_value=None):
            assert CredentialStore.get_key() is None

    def test_env_fallback_when_keyring_raises(self, monkeypatch):
        """Keyring unavailable -> read DEEPSEEK_API_KEY instead."""
        with mock.patch(
            "keyring.get_password", side_effect=RuntimeError("no keyring backend")
        ):
            monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
            assert CredentialStore.get_key() == SECRET

    def test_env_fallback_returns_none_when_var_not_set(self, monkeypatch):
        """Keyring unavailable and no env var -> None, never an exception."""
        with mock.patch(
            "keyring.get_password", side_effect=RuntimeError("no keyring backend")
        ):
            monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
            assert CredentialStore.get_key() is None


class TestSetKey:
    def test_set_key_calls_keyring_set_password(self):
        with mock.patch("keyring.set_password") as set_password:
            CredentialStore.set_key(SECRET)
        set_password.assert_called_once_with(
            CredentialStore.SERVICE_NAME, CredentialStore.ACCOUNT_NAME, SECRET
        )


class TestClearKey:
    def test_clear_key_calls_keyring_delete_password(self):
        with mock.patch("keyring.delete_password") as delete_password:
            CredentialStore.clear_key()
        delete_password.assert_called_once_with(
            CredentialStore.SERVICE_NAME, CredentialStore.ACCOUNT_NAME
        )


class TestCheckStatus:
    def test_returns_set_when_key_present(self):
        with mock.patch("keyring.get_password", return_value=SECRET):
            assert CredentialStore.check_status() == "set"

    def test_returns_not_set_when_absent(self):
        with mock.patch("keyring.get_password", return_value=None):
            assert CredentialStore.check_status() == "not_set"

    def test_never_reveals_key_value(self):
        """Status is exactly "set"/"not_set"; the key itself never appears."""
        with mock.patch("keyring.get_password", return_value=SECRET):
            status = CredentialStore.check_status()
        assert status == "set"
        assert SECRET not in status
        assert status in ("set", "not_set")
