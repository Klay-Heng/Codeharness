"""Secure API key storage backed by the OS keyring.

The DeepSeek API key lives in the system keyring (never in a plain
config file or git).  :class:`CredentialStore` is a thin facade over
``keyring`` with a ``DEEPSEEK_API_KEY`` environment-variable fallback
for environments where the keyring is unavailable (e.g. headless CI).

Security invariants:

- The key value is never logged, printed, or included in any status
  string -- :meth:`CredentialStore.check_status` reports presence
  only.
- ``DEEPSEEK_API_KEY`` is read from the environment at call time; it
  is never written to disk or echoed to a log.
"""
from __future__ import annotations

import logging
import os

import keyring

logger = logging.getLogger(__name__)


class CredentialStore:
    """Read, write, and clear the DeepSeek API key.

    Primary store: the system keyring (service ``CodeHarness``,
    account ``deepseek_api_key``).  Fallback: the
    ``DEEPSEEK_API_KEY`` environment variable, used only when the
    keyring backend is unavailable (raises).
    """

    SERVICE_NAME = "CodeHarness"
    ACCOUNT_NAME = "deepseek_api_key"

    @staticmethod
    def get_key() -> str | None:
        """Return the stored API key, or None if no key is available.

        Tries the system keyring first.  If the keyring backend is
        unavailable, falls back to the ``DEEPSEEK_API_KEY``
        environment variable.  Returns None when neither yields a
        value.
        """
        try:
            return keyring.get_password(
                CredentialStore.SERVICE_NAME, CredentialStore.ACCOUNT_NAME
            )
        except Exception:  # noqa: BLE001 - any keyring failure must fall back, never crash
            # No backend / backend failure: never crash, try env var.
            # Log the fallback fact only -- never the key value.
            logger.debug("keyring unavailable; falling back to DEEPSEEK_API_KEY")
            return os.getenv("DEEPSEEK_API_KEY")

    @staticmethod
    def set_key(key: str) -> None:
        """Store ``key`` in the system keyring."""
        keyring.set_password(
            CredentialStore.SERVICE_NAME, CredentialStore.ACCOUNT_NAME, key
        )

    @staticmethod
    def clear_key() -> None:
        """Remove the API key from the system keyring."""
        keyring.delete_password(
            CredentialStore.SERVICE_NAME, CredentialStore.ACCOUNT_NAME
        )

    @staticmethod
    def check_status() -> str:
        """Return ``"set"`` if a key is available, else ``"not_set"``.

        Reports presence only; the key value itself is never
        revealed.
        """
        return "set" if CredentialStore.get_key() is not None else "not_set"
