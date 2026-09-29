"""`epimemer client-state record`: the hook that tells the server which model is seated.

It runs as a Claude Code `SessionStart` and `PostModelSwitch` hook, so two
things matter beyond the file it writes: it prints nothing on stdout, because a
`SessionStart` hook's stdout is put into the agent's context, and it always
exits 0, because a failing hook must never break a session.
"""

import io
import json
import os
import stat
import time
from datetime import UTC, datetime

import pytest

from epimemer.cli import main
from epimemer.client_state import (
    ClientState,
    live_client_state,
    write_client_state,
)


def read_client_model(directory, session_id):
    """The model the server would see for a spawn conversation."""
    live = live_client_state(directory, session_id)
    return None if live is None else live.model


def _run(monkeypatch, capsys, payload, *args) -> tuple[int, str, str]:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))
    code = main(["client-state", "record", *args])
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture(autouse=True)
def _no_configured_dir(monkeypatch):
    monkeypatch.delenv("EPIMEMER_CLIENT_STATE_DIR", raising=False)


def test_a_model_switch_is_recorded(monkeypatch, capsys, tmp_path):
    code, out, err = _run(
        monkeypatch,
        capsys,
        {
            "session_id": "conv-1",
            "hook_event_name": "PostModelSwitch",
            "from_model": "claude-fable-5-1",
            "to_model": "claude-opus-5-5",
        },
        "--dir",
        str(tmp_path),
    )

    assert (code, out, err) == (0, "", "")
    stored = json.loads((tmp_path / "conv-1.json").read_text())
    assert stored["session_id"] == "conv-1"
    assert stored["model"] == "claude-opus-5-5"
    assert stored["event"] == "PostModelSwitch"
    assert stored["recorded_at"]
    assert read_client_model(tmp_path, "conv-1") == "claude-opus-5-5"


def test_a_session_start_is_recorded(monkeypatch, capsys, tmp_path):
    code, out, _ = _run(
        monkeypatch,
        capsys,
        {
            "session_id": "conv-2",
            "hook_event_name": "SessionStart",
            "startup_reason": "resume",
            "model": "claude-fable-5-1",
        },
        "--dir",
        str(tmp_path),
    )

    assert (code, out) == (0, "")
    assert read_client_model(tmp_path, "conv-2") == "claude-fable-5-1"
    assert json.loads((tmp_path / "conv-2.json").read_text())["event"] == "SessionStart:resume"


def test_the_directory_and_file_are_private(monkeypatch, capsys, tmp_path):
    directory = tmp_path / "state"
    _run(
        monkeypatch,
        capsys,
        {"session_id": "conv-1", "model": "m"},
        "--dir",
        str(directory),
    )

    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / "conv-1.json").stat().st_mode) == 0o600
    assert [p.name for p in directory.iterdir()] == ["conv-1.json"]


def test_the_configured_directory_is_the_default(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("EPIMEMER_CLIENT_STATE_DIR", str(tmp_path))
    code, out, _ = _run(monkeypatch, capsys, {"session_id": "conv-1", "model": "m"})

    assert (code, out) == (0, "")
    assert read_client_model(tmp_path, "conv-1") == "m"


@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "conv-1", "hook_event_name": "SessionStart", "startup_reason": "clear"},
        {"model": "claude-opus-5-5"},
        {"session_id": "../escape", "model": "m"},
        "this is not json",
        "",
    ],
    ids=["no-model", "no-session", "unsafe-session", "not-json", "empty"],
)
def test_nothing_to_record_writes_nothing_and_exits_zero(monkeypatch, capsys, tmp_path, payload):
    code, out, err = _run(monkeypatch, capsys, payload, "--dir", str(tmp_path))

    assert code == 0
    assert out == ""
    assert err.startswith("epimemer client-state:") and err.count("\n") == 1
    assert list(tmp_path.iterdir()) == []


def test_an_unwritable_directory_still_exits_zero(monkeypatch, capsys, tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory")

    code, out, err = _run(
        monkeypatch, capsys, {"session_id": "conv-1", "model": "m"}, "--dir", str(blocker / "x")
    )

    assert (code, out) == (0, "")
    assert "could not write" in err


def test_files_older_than_thirty_days_are_pruned(monkeypatch, capsys, tmp_path):
    old = tmp_path / "conv-old.json"
    recent = tmp_path / "conv-recent.json"
    for path in (old, recent):
        path.write_text("{}")
    long_ago = time.time() - 31 * 86400
    os.utime(old, (long_ago, long_ago))
    a_while_ago = time.time() - 29 * 86400
    os.utime(recent, (a_while_ago, a_while_ago))

    _run(monkeypatch, capsys, {"session_id": "conv-1", "model": "m"}, "--dir", str(tmp_path))

    assert sorted(p.name for p in tmp_path.iterdir()) == ["conv-1.json", "conv-recent.json"]


def test_reading_a_malformed_file_is_unknown_not_an_error(tmp_path):
    (tmp_path / "conv-1.json").write_text("{not json")
    assert read_client_model(tmp_path, "conv-1") is None
    assert read_client_model(tmp_path, "conv-missing") is None
    assert read_client_model(tmp_path, None) is None


AT = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)


def test_the_client_pid_writes_the_live_record_beside_the_session_file(
    monkeypatch, capsys, tmp_path
):
    code, out, err = _run(
        monkeypatch,
        capsys,
        {"session_id": "conv-1", "model": "m"},
        "--dir",
        str(tmp_path),
        "--client-pid",
        "4242",
    )

    assert (code, out, err) == (0, "", "")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["client-4242.json", "conv-1.json"]
    session = json.loads((tmp_path / "conv-1.json").read_text())
    assert session == json.loads((tmp_path / "client-4242.json").read_text())
    assert session["client_pid"] == 4242
    assert stat.S_IMODE((tmp_path / "client-4242.json").stat().st_mode) == 0o600


@pytest.mark.parametrize("given", ["$PPID", "0", "-3", "12a", ""])
def test_a_bad_client_pid_is_noted_and_recording_goes_on(monkeypatch, capsys, tmp_path, given):
    code, out, err = _run(
        monkeypatch,
        capsys,
        {"session_id": "conv-1", "model": "m"},
        "--dir",
        str(tmp_path),
        "--client-pid",
        given,
    )

    assert (code, out) == (0, "")
    assert "--client-pid" in err and err.count("\n") == 1
    assert [p.name for p in tmp_path.iterdir()] == ["conv-1.json"]
    assert json.loads((tmp_path / "conv-1.json").read_text())["client_pid"] is None


def _state(session_id: str, model: str, pid: int | None) -> ClientState:
    return ClientState(session_id=session_id, model=model, recorded_at=AT, client_pid=pid)


def test_the_live_record_is_preferred(tmp_path):
    write_client_state(tmp_path, _state("conv-a", "claude-fable-5-1", 77))
    write_client_state(tmp_path, _state("conv-a", "claude-opus-5-5", 77))

    live = live_client_state(tmp_path, "conv-a")
    assert (live.session_id, live.model) == ("conv-a", "claude-opus-5-5")


def test_clear_is_followed_through_the_live_record(tmp_path):
    """The server was spawned in A; /clear started B in the same Claude Code
    process, and a model switch after it is followed too."""
    write_client_state(tmp_path, _state("conv-a", "claude-fable-5-1", 77))
    write_client_state(tmp_path, _state("conv-b", "claude-fable-5-1", 77))
    write_client_state(tmp_path, _state("conv-b", "claude-opus-5-5", 77))

    live = live_client_state(tmp_path, "conv-a")
    assert (live.session_id, live.model) == ("conv-b", "claude-opus-5-5")


def test_no_pid_falls_back_to_the_session_file(tmp_path):
    write_client_state(tmp_path, _state("conv-a", "claude-fable-5-1", None))
    (tmp_path / "client-77.json").write_text(
        _state("conv-b", "claude-opus-5-5", 77).model_dump_json()
    )

    live = live_client_state(tmp_path, "conv-a")
    assert (live.session_id, live.model) == ("conv-a", "claude-fable-5-1")


def test_a_broken_live_record_falls_back_to_the_session_file(tmp_path):
    write_client_state(tmp_path, _state("conv-a", "claude-fable-5-1", 77))
    (tmp_path / "client-77.json").write_text("{broken")

    assert live_client_state(tmp_path, "conv-a").model == "claude-fable-5-1"


def test_both_kinds_of_file_are_pruned(monkeypatch, capsys, tmp_path):
    for name in ("conv-old.json", "client-1.json"):
        path = tmp_path / name
        path.write_text("{}")
        long_ago = time.time() - 31 * 86400
        os.utime(path, (long_ago, long_ago))

    _run(monkeypatch, capsys, {"session_id": "conv-1", "model": "m"}, "--dir", str(tmp_path))

    assert [p.name for p in tmp_path.iterdir()] == ["conv-1.json"]
