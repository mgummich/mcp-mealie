"""Environment configuration, validated at startup."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv

TRUTHY = {"1", "true", "yes", "on"}
FALSY = {"0", "false", "no", "off"}


class ConfigError(Exception):
    """Raised when the environment is missing or malformed."""


def _parse_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in TRUTHY:
        return True
    if value in FALSY:
        return False
    # Failing open on a safety flag is the wrong default, so an unrecognized
    # value is an error rather than a silent False.
    allowed = ", ".join(sorted(TRUTHY | FALSY))
    raise ConfigError(f"{name} must be one of: {allowed} (got {raw!r})")


@dataclass(frozen=True)
class Config:
    """Validated runtime configuration, normally built via from_env().

    Attributes:
        url: Base URL of the Mealie instance, no trailing slash.
        token: Long-lived Mealie API token.
        read_only: When True, write tools are not registered at all.
        verify_ssl: Verify TLS certificates; disable for self-signed certs.
        log_level: Python logging level name, e.g. "INFO" or "DEBUG".
    """

    url: str
    token: str
    read_only: bool = False
    verify_ssl: bool = True
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> Config:
        """Build a Config from MEALIE_* environment variables.

        A .env file in the working directory (or any parent) is loaded first,
        but never overrides variables already set in the real environment.

        Returns:
            A validated Config; MEALIE_URL is stripped of trailing slashes.

        Raises:
            ConfigError: If MEALIE_URL or MEALIE_API_TOKEN is missing or
                malformed, or a boolean variable has an unrecognized value.
        """
        # usecwd: without it dotenv searches upward from this module's directory
        # (inside site-packages for an installed copy), not the user's cwd.
        load_dotenv(find_dotenv(usecwd=True))

        url = (os.environ.get("MEALIE_URL") or "").strip().rstrip("/")
        if not url:
            raise ConfigError("MEALIE_URL is required (e.g. https://mealie.example.com)")
        if not url.startswith(("http://", "https://")):
            raise ConfigError(f"MEALIE_URL must start with http:// or https:// (got {url!r})")

        token = (os.environ.get("MEALIE_API_TOKEN") or "").strip()
        if not token:
            raise ConfigError(
                "MEALIE_API_TOKEN is required — create one in Mealie under Settings > API Tokens"
            )

        return cls(
            url=url,
            token=token,
            read_only=_parse_bool("MEALIE_READ_ONLY", False),
            verify_ssl=_parse_bool("MEALIE_VERIFY_SSL", True),
            log_level=(os.environ.get("MEALIE_LOG_LEVEL") or "INFO").strip().upper(),
        )
