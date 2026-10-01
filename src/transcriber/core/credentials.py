"""The credential store seam: where the Deepgram API key lives.

The key never goes into the settings document or any file the app writes. The real store is the
operating system's credential manager (Windows Credential Manager via keyring); tests use a fake.
"""

from __future__ import annotations

from typing import Protocol

SERVICE = "Transcriber"
ACCOUNT = "deepgram-api-key"


class CredentialStore(Protocol):
    def get_api_key(self) -> str | None: ...

    def set_api_key(self, key: str) -> None: ...

    def clear_api_key(self) -> None: ...


class KeyringCredentialStore:
    """Backed by the OS credential manager through the keyring library."""

    def get_api_key(self) -> str | None:
        import keyring

        return keyring.get_password(SERVICE, ACCOUNT) or None

    def set_api_key(self, key: str) -> None:
        import keyring

        keyring.set_password(SERVICE, ACCOUNT, key)

    def clear_api_key(self) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(SERVICE, ACCOUNT)
        except PasswordDeleteError:
            pass


class NoCredentialStore:
    """For platforms or sessions without a credential manager: nothing is ever stored."""

    def get_api_key(self) -> str | None:
        return None

    def set_api_key(self, key: str) -> None:
        raise RuntimeError("No credential store is available on this system.")

    def clear_api_key(self) -> None:
        pass
