"""Config parsing. A safety flag that fails open is worse than one that errors."""

from __future__ import annotations

import pytest

from mealie_mcp.config import Config, ConfigError

REQUIRED = {"MEALIE_URL": "https://mealie.test/", "MEALIE_API_TOKEN": "tok"}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "MEALIE_URL",
        "MEALIE_API_TOKEN",
        "MEALIE_READ_ONLY",
        "MEALIE_VERIFY_SSL",
        "MEALIE_LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)


def set_env(monkeypatch, **extra):
    for key, value in {**REQUIRED, **extra}.items():
        monkeypatch.setenv(key, value)


def test_defaults(monkeypatch):
    set_env(monkeypatch)

    config = Config.from_env()

    assert config.url == "https://mealie.test"  # trailing slash stripped
    assert config.token == "tok"
    assert config.read_only is False
    assert config.verify_ssl is True
    assert config.log_level == "INFO"


def test_missing_url_explains_itself(monkeypatch):
    monkeypatch.setenv("MEALIE_API_TOKEN", "tok")

    with pytest.raises(ConfigError, match="MEALIE_URL is required"):
        Config.from_env()


def test_missing_token_points_at_the_settings_page(monkeypatch):
    monkeypatch.setenv("MEALIE_URL", "https://mealie.test")

    with pytest.raises(ConfigError, match="API Tokens"):
        Config.from_env()


def test_url_without_a_scheme_is_rejected(monkeypatch):
    set_env(monkeypatch, MEALIE_URL="mealie.test")

    with pytest.raises(ConfigError, match="must start with http"):
        Config.from_env()


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_truthy_spellings(monkeypatch, value):
    set_env(monkeypatch, MEALIE_READ_ONLY=value)

    assert Config.from_env().read_only is True


@pytest.mark.parametrize("value", ["0", "false", "No", "off", ""])
def test_falsy_spellings(monkeypatch, value):
    set_env(monkeypatch, MEALIE_READ_ONLY=value)

    assert Config.from_env().read_only is False


def test_a_nonsense_boolean_is_an_error_not_a_silent_false(monkeypatch):
    set_env(monkeypatch, MEALIE_READ_ONLY="maybe")

    with pytest.raises(ConfigError, match="MEALIE_READ_ONLY must be one of"):
        Config.from_env()


def test_verify_ssl_can_be_disabled(monkeypatch):
    set_env(monkeypatch, MEALIE_VERIFY_SSL="false")

    assert Config.from_env().verify_ssl is False
