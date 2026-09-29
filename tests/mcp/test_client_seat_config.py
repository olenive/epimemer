"""The client session id and the client-state directory, read from the environment."""

import pytest

from epimemer.mcp.config import ServerConfig, load_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "EPIMEMER_CLIENT_SESSION_ID",
        "CLAUDE_CODE_SESSION_ID",
        "EPIMEMER_CLIENT_STATE_DIR",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults_know_no_session_and_use_the_home_directory():
    config = load_config()
    assert config.client_session_id is None
    assert config.client_state_dir == "~/.epimemer/client-state"
    assert ServerConfig().client_session_id is None


def test_the_epimemer_variable_names_the_session(monkeypatch):
    monkeypatch.setenv("EPIMEMER_CLIENT_SESSION_ID", "conv-epimemer")
    assert load_config().client_session_id == "conv-epimemer"


def test_claude_code_session_id_is_the_fallback(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "conv-claude")
    assert load_config().client_session_id == "conv-claude"


def test_the_epimemer_variable_wins_over_claude_code(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "conv-claude")
    monkeypatch.setenv("EPIMEMER_CLIENT_SESSION_ID", "conv-epimemer")
    assert load_config().client_session_id == "conv-epimemer"


def test_an_empty_variable_is_unset(monkeypatch):
    monkeypatch.setenv("EPIMEMER_CLIENT_SESSION_ID", "")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "conv-claude")
    assert load_config().client_session_id == "conv-claude"


def test_the_state_directory_is_configurable(monkeypatch):
    monkeypatch.setenv("EPIMEMER_CLIENT_STATE_DIR", "/var/tmp/seats")
    assert load_config().client_state_dir == "/var/tmp/seats"
