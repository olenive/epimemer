"""The one act no MCP tool may perform (REVIEW_MODE.md §2.3).

A tool the agent can call cannot establish that the *user* called it, so
approving an agent id lives here and in `ctx.elicit`, and nowhere else. The
tests that matter most are the ones about where this command **cannot** reach:
an approval that reports success into a store the server never reads is worse
than a refusal, because the user then believes they have done it.
"""

from datetime import datetime, timezone

from epimemer.cli import _confirm, _list, main, unreachable_store
from epimemer.core.types import Agent, with_description
from epimemer.mcp.config import ServerConfig


AT = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


class TestWhereThisCommandCannotReach:
    """Approvals live in per-graph settings *inside* the backend, and an
    embedded store lives inside the server process — a second connection to
    `mem://` is a separate store, not a second view of one (ISSUES.md #16)."""

    def test_the_in_memory_backend_is_unreachable(self):
        assert unreachable_store(ServerConfig(storage_backend="memory")) is not None

    def test_an_embedded_surrealdb_url_is_unreachable(self):
        for url in ("mem://", "file:///tmp/graph.db", "surrealkv://data"):
            config = ServerConfig(storage_backend="surrealdb", surrealdb_url=url)
            assert unreachable_store(config) is not None, url

    def test_a_served_surrealdb_is_reachable(self):
        config = ServerConfig(
            storage_backend="surrealdb", surrealdb_url="ws://localhost:8000/rpc"
        )
        assert unreachable_store(config) is None

    def test_confirm_refuses_rather_than_writing_where_nobody_reads(
        self, capsys, monkeypatch
    ):
        monkeypatch.setenv("EPIMEMER_STORAGE_BACKEND", "memory")

        code = main(["agents", "confirm", "critic"])

        err = capsys.readouterr().err
        assert code == 2
        # The refusal has to leave the user somewhere to go, or it just moves
        # the dead end one step later.
        assert "EPIMEMER_APPROVED_AGENTS=critic" in err

    def test_listing_an_unreachable_store_says_so_before_printing_nothing(
        self, capsys, monkeypatch
    ):
        """"(none)" from the wrong store reads exactly like "(none)" from the
        right one."""
        monkeypatch.setenv("EPIMEMER_STORAGE_BACKEND", "memory")

        code = main(["agents", "list"])

        captured = capsys.readouterr()
        assert code == 0
        assert "Note:" in captured.err
        assert "approved ids: (none)" in captured.out


class TestConfirmingAnId:
    async def test_an_unclaimed_id_is_approved_with_nothing_to_confirm(self, storage):
        """The ordinary case: the refusal is what told the user the id exists,
        so the agent has not been able to record anything yet."""
        message = await _confirm(storage, "critic")

        assert await storage.get_approved_agent_ids() == ["critic"]
        assert "has not claimed an identity here yet" in message

    async def test_confirming_stamps_the_description_in_front_of_the_user(
        self, storage
    ):
        await storage.upsert_agent(Agent(
            id="critic",
            descriptions=with_description([], text="a critic", at=AT),
            authorised_at=AT,
        ))

        message = await _confirm(storage, "critic")

        agent = await storage.get_agent("critic")
        assert agent.descriptions[-1].confirmed_at is not None
        assert "a critic" in message

    async def test_only_the_current_version_is_confirmed(self, storage):
        """A user vouches for the wording they were shown, not for every claim
        the agent has ever made about itself."""
        await storage.upsert_agent(Agent(
            id="critic",
            descriptions=with_description(
                with_description([], text="an early critic", at=AT),
                text="a stricter critic", at=AT,
            ),
            authorised_at=AT,
        ))

        await _confirm(storage, "critic")

        agent = await storage.get_agent("critic")
        assert agent.descriptions[0].confirmed_at is None
        assert agent.descriptions[1].confirmed_at is not None

    async def test_confirming_twice_leaves_the_first_confirmation_alone(self, storage):
        await storage.upsert_agent(Agent(
            id="critic",
            descriptions=with_description([], text="a critic", at=AT, confirmed_at=AT),
            authorised_at=AT,
        ))

        await _confirm(storage, "critic")

        agent = await storage.get_agent("critic")
        assert agent.descriptions[-1].confirmed_at == AT


class TestListing:
    async def test_an_unconfirmed_description_is_labelled_on_every_line(self, storage):
        """Said plainly wherever the prose appears: it is the agent's own
        assertion, and the listing is where a human decides what to trust."""
        await storage.set_approved_agent_ids(["critic"])
        await storage.upsert_agent(Agent(
            id="critic",
            descriptions=with_description([], text="a rigorous critic", at=AT),
            authorised_at=AT,
            last_seen_at=AT,
        ))

        out = await _list(storage)

        assert "a rigorous critic" in out
        assert "self-reported, unconfirmed" in out
        assert "approved ids: critic" in out

    async def test_an_empty_graph_says_so(self, storage):
        out = await _list(storage)

        assert "approved ids: (none)" in out
        assert "No agent has claimed an identity" in out
