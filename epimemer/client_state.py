"""Which model is behind a client conversation, as a client hook recorded it.

The MCP server cannot see the model an agent runs on, and a judge is confirmed
in a seat that includes the model (REVIEW_MODE.md §2.6). Claude Code can: its
`SessionStart` and `PostModelSwitch` hooks receive the session id and the model
as JSON on stdin. `epimemer client-state record` runs as that hook and writes
one small file per session, `<dir>/<session_id>.json`, which the server reads
on every claim and every write.

**The live record.** Given `--client-pid $PPID`, the hook also writes the same
content to `<dir>/client-<pid>.json`. A hook's shell is a direct child of the
Claude Code process, so `$PPID` is that process's id, the same for every hook
it runs across `/clear`, resume in place and model switches. The server was
spawned in one conversation and knows only that conversation's id; its session
file names the pid, and the pid file holds whatever that Claude Code process
recorded last, including a conversation started since by `/clear`. The pid is
an opaque key linking one process's hook events, never used to look at
processes.

Reading never raises into a tool: a missing, unreadable or malformed file
reads as *model unknown*, which asks the user rather than binding silently.
"""

import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

# How long a session's file is kept after it was last written. A conversation
# idle this long that resumes records itself again on `SessionStart`.
CLIENT_STATE_KEEP_DAYS = 30


class ClientState(BaseModel):
    """What a hook recorded about one client conversation."""

    session_id: str
    model: str
    recorded_at: datetime
    event: str | None = None
    # The Claude Code process that ran the hook, when the hook was given it.
    client_pid: int | None = None


def client_state_dir(configured: str) -> Path:
    """The configured directory, with a leading `~` expanded."""
    return Path(configured).expanduser()


def _safe_name(session_id: str) -> bool:
    """A session id usable as a file name without leaving the directory."""
    return (
        bool(session_id)
        and all(ch.isalnum() or ch in "-_." for ch in session_id)
        and (session_id not in {".", ".."})
    )


def client_state_path(directory: Path, session_id: str) -> Path | None:
    """Where this session's state lives, or None for an id unsafe as a name."""
    return directory / f"{session_id}.json" if _safe_name(session_id) else None


def client_pid_path(directory: Path, client_pid: int) -> Path | None:
    """Where the live record for one Claude Code process lives."""
    name = f"client-{client_pid}"
    return directory / f"{name}.json" if client_pid > 0 and _safe_name(name) else None


def _read_state(path: Path | None) -> ClientState | None:
    """One state file, validated, or None for any failure at all."""
    if path is None:
        return None
    try:
        state = ClientState.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError, ValueError, ValidationError:
        return None
    if not state.model.strip() or not _safe_name(state.session_id):
        return None
    return state.model_copy(update={"model": state.model.strip()})


def live_client_state(directory: Path, spawn_session_id: str | None) -> ClientState | None:
    """What the client is doing now, found through the conversation the server
    was spawned in. None if there is nothing usable.

    The spawn conversation's file is read first, and must name that
    conversation. Where it names the Claude Code process that wrote it, and that
    process's live record validates, the live record wins: after a `/clear` it
    names the new conversation and whatever model is seated there. Otherwise
    the spawn conversation's own file is the answer. Every failure, no id, no
    file, a file that is not JSON, reads as None, which the caller treats as
    unknown.
    """
    if not spawn_session_id:
        return None
    spawned = _read_state(client_state_path(directory, spawn_session_id))
    if spawned is None or spawned.session_id != spawn_session_id:
        return None
    if spawned.client_pid is not None:
        live = _read_state(client_pid_path(directory, spawned.client_pid))
        if live is not None and live.client_pid == spawned.client_pid:
            return live
    return spawned


def hook_model(payload: dict) -> str | None:
    """The model a hook payload names: `to_model` after a switch, else `model`."""
    for key in ("to_model", "model"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        # Some payloads carry the model as an object with an id.
        if isinstance(value, dict):
            inner = value.get("id")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    return None


def hook_event(payload: dict) -> str | None:
    """Which hook fired, for the record: the event name and any start reason."""
    name = payload.get("hook_event_name")
    reason = payload.get("startup_reason") or payload.get("source")
    parts = [str(p) for p in (name, reason) if isinstance(p, str) and p]
    return ":".join(parts) or None


def _write_atomically(directory: Path, path: Path, text: str) -> None:
    """A temp file in the same directory, made private, then renamed into place."""
    fd, temp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def write_client_state(directory: Path, state: ClientState) -> Path:
    """Write one session's state atomically, and the live record beside it.

    Always `<session_id>.json`; where the state names the Claude Code process,
    `client-<pid>.json` too, with the same content. The directory is created
    private (0700) and each file is private (0600), since they name the
    conversations on this machine.
    """
    path = client_state_path(directory, state.session_id)
    if path is None:
        raise ValueError(f"session id {state.session_id!r} cannot be used as a file name")
    live = None if state.client_pid is None else client_pid_path(directory, state.client_pid)
    if state.client_pid is not None and live is None:
        raise ValueError(f"client pid {state.client_pid!r} cannot be used as a file name")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    text = state.model_dump_json()
    _write_atomically(directory, path, text)
    if live is not None:
        _write_atomically(directory, live, text)
    return path


def parse_client_pid(given: str | None) -> tuple[int | None, str | None]:
    """A `--client-pid` value as a pid, and a note where it was not one.

    Only a positive integer counts. Anything else, such as a literal `$PPID`
    a shell did not expand, reads as absent, and the note says so; recording
    goes on without it.
    """
    if given is None:
        return None, None
    text = given.strip()
    if text.isdigit() and int(text) > 0:
        return int(text), None
    return None, (
        f"epimemer client-state: --client-pid {given!r} is not a positive integer; "
        "recording without it"
    )


def prune_client_state(directory: Path, *, keep_days: int = CLIENT_STATE_KEEP_DAYS) -> int:
    """Delete state files not written for `keep_days`. Returns how many went.

    Both kinds, the per-session files and the per-process live records, since
    they share the directory and the `.json` suffix. Judged by the file's
    modification time, a number, never by text. A file that cannot be removed
    is left for next time.
    """
    cutoff = time.time() - keep_days * 86400
    removed = 0
    try:
        entries = list(directory.glob("*.json"))
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def record_from_hook(
    directory: Path,
    raw: str,
    *,
    client_pid: int | None = None,
    now: datetime | None = None,
) -> str | None:
    """Record the model a hook payload names. Returns why nothing was written.

    None means the file was written. A string is the one-line reason nothing
    was, for the hook to print on stderr: the hook must never fail a session,
    so every problem becomes a reason rather than an exception.
    """
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return "epimemer client-state: the hook input was not JSON; nothing recorded"
    if not isinstance(payload, dict):
        return "epimemer client-state: the hook input was not a JSON object; nothing recorded"
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        return "epimemer client-state: no session_id in the hook input; nothing recorded"
    session_id = session_id.strip()
    if not _safe_name(session_id):
        return "epimemer client-state: the session_id is not a safe file name; nothing recorded"
    model = hook_model(payload)
    if model is None:
        return "epimemer client-state: no model in the hook input; nothing recorded"
    try:
        write_client_state(
            directory,
            ClientState(
                session_id=session_id,
                model=model,
                recorded_at=now or datetime.now(UTC),
                event=hook_event(payload),
                client_pid=client_pid,
            ),
        )
    except OSError as error:
        return f"epimemer client-state: could not write to {directory}: {error}"
    prune_client_state(directory)
    return None
