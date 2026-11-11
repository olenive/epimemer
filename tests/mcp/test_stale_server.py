"""A server running an older Epimemer than the one installed says so.

The server keeps the code it loaded at startup when the package is upgraded
underneath it, so every response it builds compares the version it started on
with the version installed now, and carries a warning in `warnings` while the
two differ. The agent reading it is the one who can ask the user to reconnect.
"""

import json
import os
import shutil
from types import SimpleNamespace

import pytest

from epimemer.core.seat import Seat
from epimemer.core.types import JudgeRef
from epimemer.mcp import server
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.retrieval_records import new_record_log
from epimemer.mcp.staleness import (
    STALE_SERVER,
    read_installed_version,
    stale_server_entry,
    stale_server_warning,
    with_warning,
)
from epimemer.mcp.types import ResponseMeta
from epimemer.storage.memory import InMemoryStorage

RUNNING = "0.4.5"
INSTALLED = "0.4.6"

WARNING = (
    "This server is running Epimemer 0.4.5 and the installed package is now 0.4.6. "
    "Ask the user to reconnect the MCP client, which restarts the server on the "
    "installed version. After the reconnect, load the tool schemas again: the ones "
    "you hold describe the old version."
)


# --- The pure functions -----------------------------------------------------


def test_the_warning_names_both_versions():
    assert stale_server_warning(RUNNING, INSTALLED) == WARNING


def test_equal_versions_warn_of_nothing():
    assert stale_server_warning(RUNNING, RUNNING) is None


@pytest.mark.parametrize(
    ("running", "installed"),
    [(None, INSTALLED), (RUNNING, None), ("unknown", INSTALLED), (RUNNING, "unknown")],
    ids=["running unknown", "installed unreadable", "started unknown", "installed unknown"],
)
def test_an_unknown_version_warns_of_nothing(running, installed):
    assert stale_server_warning(running, installed) is None
    assert stale_server_entry(running, installed) is None


def test_the_entry_has_the_shape_of_every_warning():
    assert stale_server_entry(RUNNING, INSTALLED) == {
        "kind": STALE_SERVER,
        "message": WARNING,
        "subjects": [],
        "detail": {"running": RUNNING, "installed": INSTALLED},
    }


def test_a_response_without_warnings_gets_the_list():
    entry = stale_server_entry(RUNNING, INSTALLED)
    response = {"graphs": ["default"]}

    assert with_warning(response, entry) == {"graphs": ["default"], "warnings": [entry]}
    assert response == {"graphs": ["default"]}


def test_a_response_with_warnings_keeps_them_first():
    entry = stale_server_entry(RUNNING, INSTALLED)
    own = {"kind": "cross_metacontext", "message": "x", "subjects": ["a"], "detail": {}}
    response = {"warnings": [own], "warning": "x", "notify_user": False}

    assert with_warning(response, entry) == {
        "warnings": [own, entry],
        "warning": "x",
        "notify_user": False,
    }
    assert response["warnings"] == [own]


def test_no_entry_leaves_the_response_as_it_was():
    response = {"graphs": ["default"]}

    assert with_warning(response, None) is response


# --- Reading the installed version from disk ---------------------------------


def _install(site, version: str) -> None:
    dist = site / f"stalenessprobe-{version}.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: stalenessprobe\nVersion: {version}\n"
    )


def _touch_later(site) -> None:
    """Move the directory's modified time forward.

    `importlib.metadata` caches a directory's listing for as long as its
    modified time stays the same. Linux stamps that time from a coarse clock,
    so two changes made in one test can share it, where an upgrade and the
    next tool call never do.
    """
    later = os.stat(site).st_mtime + 10
    os.utime(site, (later, later))


def test_the_reader_sees_an_upgrade_made_after_it_first_read(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    _install(tmp_path, "1.0")
    assert read_installed_version("stalenessprobe") == "1.0"

    shutil.rmtree(tmp_path / "stalenessprobe-1.0.dist-info")
    _install(tmp_path, "2.0")
    _touch_later(tmp_path)

    assert read_installed_version("stalenessprobe") == "2.0"


# Python 3.14 warns here that a missing `Version` will raise in a later
# release; the reader answers None either way.
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_a_half_done_upgrade_reads_as_unknown(tmp_path, monkeypatch):
    """The metadata directory is there and its `METADATA` file is not yet."""
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "stalenessprobe-2.0.dist-info").mkdir()

    assert read_installed_version("stalenessprobe") is None


def test_a_package_that_is_not_installed_reads_as_unknown():
    assert read_installed_version("stalenessprobe-not-installed") is None


async def test_a_server_that_has_just_started_is_not_stale(started_deps):
    installed = started_deps["read_installed_version"]()

    assert stale_server_entry(started_deps["version"], installed) is None


# --- The wrapper every tool call goes through --------------------------------


def _ctx(installed: str | None, config: ServerConfig | None = None) -> SimpleNamespace:
    """A call's context on a server started on `RUNNING`, with `installed` on disk."""
    return SimpleNamespace(
        lifespan_context={
            "storage": InMemoryStorage(),
            "config": config or ServerConfig(storage_backend="memory", embedding_provider="mock"),
            "event_bus": None,
            "retrievals": new_record_log(),
            "connection_state": server.new_connection_state(),
            "version": RUNNING,
            "read_installed_version": lambda: installed,
        }
    )


async def _call(ctx, result: dict, tool: str = "epimemer.list_graphs", **kwargs) -> dict:
    async def _tool() -> tuple[dict, ResponseMeta]:
        return result, ResponseMeta()

    text = await server._run_with_timeout(tool, _tool, ctx, "", lambda r, m: "", **kwargs)
    return json.loads(text)


async def test_a_response_carries_the_warning_while_the_versions_differ():
    payload = await _call(_ctx(INSTALLED), {"graphs": ["default"]})

    assert payload["result"]["graphs"] == ["default"]
    assert payload["result"]["warnings"] == [stale_server_entry(RUNNING, INSTALLED)]
    assert payload["result"]["warnings"][0]["message"] == WARNING


async def test_the_warning_follows_the_tools_own_and_leaves_notify_user_alone():
    own = {"kind": "cross_metacontext", "message": "x", "subjects": ["a"], "detail": {}}

    payload = await _call(_ctx(INSTALLED), {"warnings": [own], "notify_user": False})

    assert payload["result"]["warnings"] == [own, stale_server_entry(RUNNING, INSTALLED)]
    assert payload["result"]["notify_user"] is False


async def test_a_failed_call_carries_the_warning():
    """A failure is often how a stale server first shows."""

    async def _fails() -> tuple[dict, ResponseMeta]:
        raise ImportError("cannot import name 'DeferralSettings'")

    text = await server._run_with_timeout(
        "epimemer.list_graphs", _fails, _ctx(INSTALLED), "", lambda r, m: ""
    )
    payload = json.loads(text)

    assert "DeferralSettings" in payload["error"]
    assert payload["warnings"] == [stale_server_entry(RUNNING, INSTALLED)]


async def test_a_call_refused_for_the_wrong_graph_carries_the_warning():
    payload = await _call(
        _ctx(INSTALLED), {"never": "reached"}, tool="epimemer.search", expected_graph="elsewhere"
    )

    assert "never" not in payload["result"]
    assert payload["result"]["warnings"] == [stale_server_entry(RUNNING, INSTALLED)]


async def test_nothing_is_added_while_the_versions_match():
    payload = await _call(_ctx(RUNNING), {"graphs": ["default"]})

    assert payload["result"] == {"graphs": ["default"]}


async def test_nothing_is_added_while_the_installed_version_cannot_be_read():
    payload = await _call(_ctx(None), {"graphs": ["default"]})

    assert payload["result"] == {"graphs": ["default"]}


# --- Write refusals made before the wrapper runs ------------------------------
#
# `_judge_for_write` runs in a write tool's body, and a refusal from it is
# returned as the whole response, so the wrapper never sees it.

STRICT = ServerConfig(storage_backend="memory", embedding_provider="mock", require_judge=True)


def _judge_claimed_elsewhere() -> dict:
    """A binding claimed in a seat with a model this call's seat does not have."""
    return server._with_seat(
        JudgeRef(agent_id="critic", digest="d1"), Seat(model="claude-fable-5-0")
    )


async def _refusal(ctx, expected_graph: str = "default", **kwargs) -> dict:
    judge, refused = await server._judge_for_write(ctx, expected_graph, **kwargs)
    assert judge is None and refused is not None
    return json.loads(refused)


async def test_a_write_refused_for_the_wrong_graph_carries_the_warning():
    payload = await _refusal(_ctx(INSTALLED), expected_graph="elsewhere")

    assert payload["result"]["expected_graph"] == "elsewhere"
    assert payload["result"]["warnings"] == [stale_server_entry(RUNNING, INSTALLED)]


async def test_a_write_with_an_unknown_token_carries_the_warning():
    payload = await _refusal(_ctx(INSTALLED), judge_token="not-a-token")

    assert "claim_agent" in payload["error"]
    assert payload["warnings"] == [stale_server_entry(RUNNING, INSTALLED)]


async def test_a_write_from_a_changed_seat_carries_the_warning():
    ctx = _ctx(INSTALLED)
    ctx.lifespan_context["connection_state"]["judge"] = _judge_claimed_elsewhere()

    payload = await _refusal(ctx)

    assert "since this judge was claimed" in payload["error"]
    assert payload["warnings"] == [stale_server_entry(RUNNING, INSTALLED)]


async def test_a_write_refused_for_want_of_a_judge_carries_the_warning():
    payload = await _refusal(_ctx(INSTALLED, STRICT))

    assert "claim_agent" in payload["error"]
    assert payload["warnings"] == [stale_server_entry(RUNNING, INSTALLED)]


async def test_a_write_tool_hands_the_refusal_back_with_the_warning():
    text = await server.memory_segment(
        content="The siege began in 1897.", ctx=_ctx(INSTALLED, STRICT), expected_graph="default"
    )
    payload = json.loads(text)

    assert "claim_agent" in payload["error"]
    assert payload["warnings"] == [stale_server_entry(RUNNING, INSTALLED)]


async def test_a_write_refusal_adds_nothing_while_the_versions_match():
    payload = await _refusal(_ctx(RUNNING, STRICT))

    assert "warnings" not in payload
