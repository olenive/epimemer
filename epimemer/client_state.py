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

**Nothing is deleted for being old.** A file's modification time is how long
it has gone unused: a hook event writes it and a server marks it used on every
tool call. The hook gives notice of files long unused, and only
`epimemer client-state prune`, run by the user, deletes them.
"""

import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

# How long a state file goes unused before the hook mentions it. Nothing is
# deleted for being old: a conversation left open has no hook event to refresh
# its file, and deleting it would cost that conversation its seat. The hook
# gives notice, and `epimemer client-state prune` deletes when the user runs it.
CLIENT_STATE_OLD_AFTER_DAYS = 180

# The least time between two notices, so one is not shown on every start.
PRUNE_NOTICE_EVERY_DAYS = 30

# Where the hook remembers when it last gave notice: an empty file whose
# modification time is the answer. Not `.json`, so it is never read as state.
PRUNE_NOTICE_MARKER = ".prune-notice"


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


class ClientRecords(BaseModel):
    """What a server read about the conversation it serves.

    `spawned` is the state of the conversation the server was started in.
    `live` is the live record of the Claude Code process that wrote it, where
    there is a usable one.
    """

    spawned: ClientState
    live: ClientState | None = None


def current_state(records: ClientRecords) -> ClientState:
    """What the client is doing now: the live record, else the spawn conversation's."""
    return records.live if records.live is not None else records.spawned


def read_client_records(directory: Path, spawn_session_id: str | None) -> ClientRecords | None:
    """The records for the conversation the server was spawned in, or None.

    The spawn conversation's file is read first, and must name that
    conversation. Where it names the Claude Code process that wrote it, and that
    process's live record validates, the live record is read too: after a
    `/clear` it names the new conversation and whatever model is seated there.
    Every failure, no id, no file, a file that is not JSON, reads as None,
    which the caller treats as unknown.
    """
    if not spawn_session_id:
        return None
    spawned = _read_state(client_state_path(directory, spawn_session_id))
    if spawned is None or spawned.session_id != spawn_session_id:
        return None
    if spawned.client_pid is not None:
        live = _read_state(client_pid_path(directory, spawned.client_pid))
        if live is not None and live.client_pid == spawned.client_pid:
            return ClientRecords(spawned=spawned, live=live)
    return ClientRecords(spawned=spawned)


def live_client_state(directory: Path, spawn_session_id: str | None) -> ClientState | None:
    """What the client is doing now, found through the conversation the server
    was spawned in. None if there is nothing usable.

    The live record wins where there is one; otherwise the spawn
    conversation's own file is the answer.
    """
    records = read_client_records(directory, spawn_session_id)
    return current_state(records) if records is not None else None


def record_files(directory: Path, records: ClientRecords) -> list[Path]:
    """The files `records` stands for.

    The spawn conversation's file always. With a live record, that process's
    file, and the file of the conversation it names where that is another
    one: a server started after a reconnect is spawned in that conversation
    and reads its file first.
    """
    spawned, live = records.spawned, records.live
    files = [client_state_path(directory, spawned.session_id)]
    if live is not None and live.client_pid is not None:
        files.append(client_pid_path(directory, live.client_pid))
        if live.session_id != spawned.session_id:
            files.append(client_state_path(directory, live.session_id))
    return [path for path in files if path is not None]


def refresh_client_records(directory: Path, records: ClientRecords) -> None:
    """Mark the files `records` stands for as used now.

    A file's modification time is how long it has gone unused, and a hook
    writes one only when a conversation starts, compacts, clears or switches
    model. A server calls this on every tool call, so a conversation that is
    still using Epimemer never looks abandoned. Only the time changes: nothing
    is rewritten and a missing file is not created. Never raises.
    """
    for path in record_files(directory, records):
        try:
            os.utime(path)
        except OSError:
            continue


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


def old_client_state_files(
    directory: Path, *, older_than_days: int = CLIENT_STATE_OLD_AFTER_DAYS
) -> list[Path]:
    """The state files not written or used for `older_than_days`, oldest first.

    Both kinds, the per-session files and the per-process live records, since
    they share the directory and the `.json` suffix. Judged by the file's
    modification time, a number, never by text. Never raises: a directory or
    a file that cannot be read contributes nothing.
    """
    cutoff = time.time() - older_than_days * 86400
    try:
        entries = list(directory.glob("*.json"))
    except OSError:
        return []
    aged = []
    for entry in entries:
        try:
            modified = entry.stat().st_mtime
        except OSError:
            continue
        if modified < cutoff:
            aged.append((modified, entry))
    return [entry for _, entry in sorted(aged)]


def prune_client_state(
    directory: Path, *, older_than_days: int = CLIENT_STATE_OLD_AFTER_DAYS
) -> int:
    """Delete the state files `old_client_state_files` lists. Returns how many went.

    Run only when the user asks, through `epimemer client-state prune`. A file
    that cannot be removed is left for next time.
    """
    removed = 0
    for entry in old_client_state_files(directory, older_than_days=older_than_days):
        try:
            entry.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def prune_notice_text(count: int, directory: Path, days: int) -> str:
    """What the hook tells the user about `count` old state files."""
    records = "1 conversation record" if count == 1 else f"{count} conversation records"
    have = "has" if count == 1 else "have"
    return (
        f"Epimemer: {records} in {directory} {have} not been used for {days} days. "
        "Run `epimemer client-state prune` to delete them. Nothing is deleted until you do."
    )


def prune_notice(directory: Path) -> str | None:
    """The notice of old state files, where one is due. None otherwise.

    Due when there are files older than `CLIENT_STATE_OLD_AFTER_DAYS` and no
    notice was given in the last `PRUNE_NOTICE_EVERY_DAYS`. Giving one is
    remembered by touching the marker file, and a marker that cannot be
    written means no notice, so a read-only directory does not produce one on
    every start. Never raises.
    """
    old = old_client_state_files(directory)
    if not old:
        return None
    marker = directory / PRUNE_NOTICE_MARKER
    try:
        last = marker.stat().st_mtime
    except OSError:
        last = None
    if last is not None and last > time.time() - PRUNE_NOTICE_EVERY_DAYS * 86400:
        return None
    try:
        marker.touch(mode=0o600)
        os.utime(marker)
    except OSError:
        return None
    return prune_notice_text(len(old), directory, CLIENT_STATE_OLD_AFTER_DAYS)


def hook_notice(directory: Path, raw: str) -> str | None:
    """What a hook run should show the user, or None.

    Only a `SessionStart` shows anything: that is the one event whose message
    to the user this was checked for. Any payload that cannot be read shows
    nothing, since the hook must never fail a session.
    """
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "SessionStart":
        return None
    return prune_notice(directory)


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
    return None
