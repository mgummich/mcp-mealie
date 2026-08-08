"""Environment configuration, validated at startup."""

from __future__ import annotations

import os
from dataclasses import dataclass

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
    url: str
    token: str
    read_only: bool = False
    verify_ssl: bool = True
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> Config:
        url = (os.environ.get("MEALIE_URL") or "").strip().rstrip("/")
        if not url:
            raise ConfigError("MEALIE_URL is required (e.g. https://mealie.example.com)")
        if not url.startswith(("http://", "https://")):
            raise ConfigError(f"MEALIE_URL must start with http:// or https:// (got {url!r})")

        token = (os.environ.get("MEALIE_API_TOKEN") or "").strip()
        if not token:
            raise ConfigError(
                "MEALIE_API_TOKEN is required — create one in Mealie under "
                "Settings > API Tokens"
            )

        return cls(
            url=url,
            token=token,
            read_only=_parse_bool("MEALIE_READ_ONLY", False),
            verify_ssl=_parse_bool("MEALIE_VERIFY_SSL", True),
            log_level=(os.environ.get("MEALIE_LOG_LEVEL") or "INFO").strip().upper(),
        )
