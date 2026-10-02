"""A judge confirmation is scoped to a seat, and a write from a changed seat is refused.

The seat is the conversation, the client and the model a claim is made from
(REVIEW_MODE.md §2.6). A confirmation given in one seat is persisted per graph,
so a claim from the same seat after a server restart binds without the picker
and says so, while a claim from any other seat asks and says why. The token and
the session binding both remember the seat of their claim, and a write from a
different seat is refused with what changed named.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.types import ClientCapabilities, Implementation, InitializeRequestParams

from epimemer.client_state import ClientState, write_client_state
from epimemer.core.seat import JudgeConfirmation, Seat
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import server, tools
from epimemer.mcp.config import ServerConfig
from tests.mcp.test_judge_tokens import _claim, _deps, _fact, _judge_of, _update

AT = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)

SEAT = Seat(
    session_id="conv-1",
    client_name="claude-code",
    client_version="2.1.0",
    model="claude-fable-5-1",
)


def _recording(asked: list[tuple[str, str]], chosen: str | None = None):
    """A picker that answers yes and records the proposal and the reason."""

    async def approve(proposed: str, description: str, reason: str) -> tools.ApprovalOutcome:
        asked.append((proposed, reason))
        return tools.ApprovalOutcome(chosen=chosen or proposed)

    return approve


async def _claim_directly(storage, *, seat: Seat | None, asked: list, now=AT, **kwargs) -> dict:
    result, _ = await tools.claim_agent(
        storage,
        agent_id="critic",
        description="a critic",
        approve_id=_recording(asked),
        seat=seat,
        now=now,
        **kwargs,
    )
    return result


class TestAConfirmedSeatCarriesOver:
    async def test_a_confirmed_pick_persists_the_seat(self, storage):
        asked: list = []
        result = await _claim_directly(storage, seat=SEAT, asked=asked)

        assert result["status"] == "claimed"
        assert result["carried_over"] is False
        assert "first claim for this judge in this seat" in result["asked_because"]
        [stored] = await storage.get_judge_confirmations()
        assert stored.agent_id == result["agent_id"]
        assert (stored.session_id, stored.client_name, stored.model) == (
            "conv-1",
            "claude-code",
            "claude-fable-5-1",
        )
        assert stored.confirmed_at == AT

    async def test_a_matching_seat_binds_without_the_picker(self, storage):
        """The restart case: nothing in-process remembers, the backend does."""
        first: list = []
        await _claim_directly(storage, seat=SEAT, asked=first)

        again: list = []
        result = await _claim_directly(storage, seat=SEAT, asked=again, now=LATER)

        assert again == []
        assert result["status"] == "claimed"
        assert result["carried_over"] is True
        assert "asked_because" not in result
        assert result["confirmed_in_seat_at"] == AT.isoformat()
        assert "confirmed in this seat on 2026-09-28, carried over" in result["message"].lower()

    async def test_a_carried_seat_does_not_hold_for_a_revoked_judge(self, storage):
        await _claim_directly(storage, seat=SEAT, asked=[])
        await storage.set_approved_agent_ids([])

        asked: list = []
        result = await _claim_directly(storage, seat=SEAT, asked=asked, now=LATER)

        assert len(asked) == 1
        assert result["carried_over"] is False

    async def test_a_confirmation_is_per_judge(self, storage):
        await _claim_directly(storage, seat=SEAT, asked=[])

        asked: list = []
        result, _ = await tools.claim_agent(
            storage,
            agent_id="editor",
            description="an editor",
            approve_id=_recording(asked),
            seat=SEAT,
            now=LATER,
        )

        assert [p for p, _ in asked] == ["editor"]
        assert result["carried_over"] is False


class TestAClaimThatAsksSaysWhy:
    async def test_an_incomplete_seat_asks_and_names_the_missing_model(self, storage):
        asked: list = []
        unknown_model = SEAT.model_copy(update={"model": None})
        await storage.set_judge_confirmations(
            [
                JudgeConfirmation(
                    session_id="conv-1",
                    agent_id="critic",
                    client_name="claude-code",
                    client_version="2.1.0",
                    model="claude-fable-5-1",
                    confirmed_at=AT,
                )
            ]
        )
        await storage.set_approved_agent_ids(["critic"])

        result = await _claim_directly(
            storage,
            seat=unknown_model,
            asked=asked,
            client_state_file="/state/conv-1.json",
        )

        assert len(asked) == 1
        reason = result["asked_because"]
        assert "model is unknown" in reason and "/state/conv-1.json" in reason
        assert "INTEGRATION.md" in reason
        # The picker is shown the same sentence.
        assert asked[0][1] == reason

    async def test_an_incomplete_seat_persists_nothing(self, storage):
        await _claim_directly(storage, seat=SEAT.model_copy(update={"model": None}), asked=[])
        assert await storage.get_judge_confirmations() == []

    async def test_no_session_id_says_so(self, storage):
        result = await _claim_directly(storage, seat=Seat(client_name="claude-code"), asked=[])
        assert "no client session id" in result["asked_because"]

    async def test_a_model_change_asks_and_names_it(self, storage):
        await _claim_directly(storage, seat=SEAT, asked=[])

        asked: list = []
        switched = SEAT.model_copy(update={"model": "claude-opus-5-5"})
        result = await _claim_directly(storage, seat=switched, asked=asked, now=LATER)

        assert len(asked) == 1
        assert (
            "the model was claude-fable-5-1 and is now claude-opus-5-5" in (result["asked_because"])
        )
        assert result["carried_over"] is False
        # The new seat is now confirmed too, beside the old one.
        assert {c.model for c in await storage.get_judge_confirmations()} == {
            "claude-fable-5-1",
            "claude-opus-5-5",
        }

    async def test_the_in_process_memo_does_not_override_a_complete_seat(self, storage):
        """A complete seat is decided by what was persisted, so a memo from
        another model in this process cannot bind silently."""
        first = await _claim_directly(storage, seat=SEAT, asked=[])

        asked: list = []
        await _claim_directly(
            storage,
            seat=SEAT.model_copy(update={"model": "claude-opus-5-5"}),
            asked=asked,
            confirmed_identity=first["agent_id"],
            now=LATER,
        )

        assert len(asked) == 1

    async def test_the_in_process_memo_still_holds_for_an_incomplete_seat(self, storage):
        incomplete = SEAT.model_copy(update={"model": None})
        first = await _claim_directly(storage, seat=incomplete, asked=[])

        asked: list = []
        result = await _claim_directly(
            storage,
            seat=incomplete,
            asked=asked,
            confirmed_identity=first["agent_id"],
            now=LATER,
        )

        assert asked == []
        assert result["status"] == "claimed"


class TestThePickerShowsTheReason:
    async def test_the_prompt_says_why_it_asks(self, storage):
        messages: list[str] = []

        async def elicit(message, response_type=None):
            messages.append(message)
            return object()  # declined

        ctx = SimpleNamespace(
            lifespan_context={
                "storage": storage,
                "version": "9.9.9",
                "connection_state": server.new_connection_state(),
            },
            elicit=elicit,
        )

        await server._elicit_agent_id(
            ctx, "critic", "a critic", "the model was a and is now b since this judge was"
        )

        assert "Asked because the model was a and is now b" in messages[0]


# --- The write gate, through the MCP boundary ---


@pytest.fixture
def embedder():
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


def _record_model(directory: Path, model: str) -> None:
    write_client_state(
        directory,
        ClientState(session_id="conv-1", model=model, recorded_at=AT, event="SessionStart"),
    )


def _handshake(client_name: str | None):
    """What the SDK hands the server for the client's initialize params.

    The real model rather than a namespace: its field is `client_info`, and
    `clientInfo` is only the wire alias, which a fake carrying the alias hid.
    None is a client that sent no client info, which Claude Code does not on
    the current protocol.
    """
    if client_name is None:
        return None
    return InitializeRequestParams(
        protocol_version="2026-07-28",
        capabilities=ClientCapabilities(),
        client_info=Implementation(name=client_name, version="2.1.0"),
    )


def _seated_ctx(deps: dict, client_name: str = "claude-code"):
    """A context whose client names itself in the handshake and cannot elicit."""

    async def elicit(message, response_type=None):
        raise RuntimeError("this client cannot put a question to the user")

    return SimpleNamespace(
        lifespan_context=deps,
        elicit=elicit,
        session=SimpleNamespace(client_params=_handshake(client_name)),
    )


@pytest.fixture
def seated(storage, embedder, tmp_path):
    config = ServerConfig(
        storage_backend="memory",
        embedding_provider="mock",
        client_session_id="conv-1",
        client_state_dir=str(tmp_path),
    )
    _record_model(tmp_path, "claude-fable-5-1")
    return _seated_ctx(_deps(storage, embedder, config)), tmp_path


class TestAWriteFromAChangedSeatIsRefused:
    async def test_the_claim_records_the_seat_with_the_token_and_binding(
        self, storage, embedder, seated
    ):
        ctx, _ = seated
        await storage.set_approved_agent_ids(["critic"])

        claimed = await _claim(ctx, "critic")

        held = ctx.lifespan_context["connection_state"]
        token_entry = held["judge_tokens"][claimed["judge_token"]]
        assert token_entry["seat"]["model"] == "claude-fable-5-1"
        assert held["judge"]["seat"]["client_name"] == "claude-code"

    async def test_an_unchanged_seat_writes(self, storage, embedder, seated):
        ctx, _ = seated
        await storage.set_approved_agent_ids(["critic"])
        claimed = await _claim(ctx, "critic")
        node = await _fact(storage, embedder, "the bridge opened in 1932")

        written = await _update(
            ctx, node.id, "the bridge opened in March 1932", judge_token=claimed["judge_token"]
        )

        assert "error" not in written
        assert await _judge_of(storage, written) == claimed["agent_id"]

    async def test_a_model_switch_refuses_a_token_write(self, storage, embedder, seated):
        ctx, directory = seated
        await storage.set_approved_agent_ids(["critic"])
        claimed = await _claim(ctx, "critic")
        node = await _fact(storage, embedder, "the bridge opened in 1932")
        _record_model(directory, "claude-opus-5-5")

        refused = await _update(
            ctx, node.id, "the bridge opened in March 1932", judge_token=claimed["judge_token"]
        )

        assert refused["error"].startswith(
            "the model was claude-fable-5-1 and is now claude-opus-5-5 since this judge "
            "was claimed; this write was refused; call claim_agent again and the user "
            "will be asked which judge the new model is"
        )
        assert (await storage.get_node(node.id)).content == "the bridge opened in 1932"

    async def test_a_model_switch_refuses_a_binding_write(self, storage, embedder, seated):
        ctx, directory = seated
        await storage.set_approved_agent_ids(["critic"])
        await _claim(ctx, "critic")
        node = await _fact(storage, embedder, "the bridge opened in 1932")
        _record_model(directory, "claude-opus-5-5")

        refused = await _update(ctx, node.id, "the bridge opened in March 1932")

        assert "the model was claude-fable-5-1 and is now claude-opus-5-5" in refused["error"]

    async def test_known_to_unknown_refuses(self, storage, embedder, seated):
        ctx, directory = seated
        await storage.set_approved_agent_ids(["critic"])
        claimed = await _claim(ctx, "critic")
        node = await _fact(storage, embedder, "the bridge opened in 1932")
        (directory / "conv-1.json").unlink()

        refused = await _update(
            ctx, node.id, "the bridge opened in March 1932", judge_token=claimed["judge_token"]
        )

        assert "the model was claude-fable-5-1 and is now unknown" in refused["error"]

    async def test_unknown_to_known_refuses(self, storage, embedder, seated):
        ctx, directory = seated
        (directory / "conv-1.json").unlink()
        await storage.set_approved_agent_ids(["critic"])
        claimed = await _claim(ctx, "critic")
        node = await _fact(storage, embedder, "the bridge opened in 1932")
        _record_model(directory, "claude-opus-5-5")

        refused = await _update(
            ctx, node.id, "the bridge opened in March 1932", judge_token=claimed["judge_token"]
        )

        assert "the model was unknown and is now claude-opus-5-5" in refused["error"]

    async def test_a_changed_client_refuses(self, storage, embedder, seated):
        ctx, _ = seated
        await storage.set_approved_agent_ids(["critic"])
        claimed = await _claim(ctx, "critic")
        node = await _fact(storage, embedder, "the bridge opened in 1932")
        ctx.session.client_params = _handshake("another-client")

        refused = await _update(
            ctx, node.id, "the bridge opened in March 1932", judge_token=claimed["judge_token"]
        )

        assert "the client was claude-code and is now another-client" in refused["error"]

    async def test_reads_are_not_gated(self, storage, embedder, seated):
        ctx, directory = seated
        await storage.set_approved_agent_ids(["critic"])
        await _claim(ctx, "critic")
        _record_model(directory, "claude-opus-5-5")

        listed = json.loads(await server.epimemer_list_graphs(ctx=ctx))

        assert "error" not in listed


class TestClearIsFollowed:
    """`/clear` keeps the Claude Code process and changes the conversation. The
    server was spawned in the first one; the live record tells it about the
    second."""

    async def test_a_write_after_clear_is_refused_naming_the_conversation(
        self, storage, embedder, seated
    ):
        ctx, directory = seated
        write_client_state(
            directory,
            ClientState(
                session_id="conv-1", model="claude-fable-5-1", recorded_at=AT, client_pid=77
            ),
        )
        await storage.set_approved_agent_ids(["critic"])
        claimed = await _claim(ctx, "critic")
        node = await _fact(storage, embedder, "the bridge opened in 1932")
        write_client_state(
            directory,
            ClientState(
                session_id="conv-2", model="claude-fable-5-1", recorded_at=LATER, client_pid=77
            ),
        )

        refused = await _update(
            ctx, node.id, "the bridge opened in March 1932", judge_token=claimed["judge_token"]
        )

        assert "the conversation was conv-1 and is now conv-2" in refused["error"]

        again = await _claim(ctx, "critic")
        assert "first claim for this judge in this seat" in again["asked_because"]
        assert again["carried_over"] is False


class TestAnUnnamedClientStillHasASeat:
    """Claude Code sends no client info on the current protocol, so a seat
    without a client name must still carry a confirmation over."""

    async def test_a_confirmed_pick_carries_over_without_a_client_name(
        self, storage, embedder, tmp_path
    ):
        config = ServerConfig(
            storage_backend="memory",
            embedding_provider="mock",
            client_session_id="conv-1",
            client_state_dir=str(tmp_path),
        )
        _record_model(tmp_path, "claude-fable-5-1")
        deps = _deps(storage, embedder, config)
        await storage.set_approved_agent_ids(["critic"])
        # The answer the user gave earlier, from a client that sent no name.
        await storage.set_judge_confirmations(
            [
                JudgeConfirmation(
                    session_id="conv-1",
                    agent_id="critic",
                    model="claude-fable-5-1",
                    confirmed_at=datetime.now(UTC),
                )
            ]
        )

        again = await _claim(_seated_ctx(deps, client_name=None), "critic")

        assert again["status"] == "claimed"
        assert again["carried_over"] is True
        assert "carried over" in again["message"]
