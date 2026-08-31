"""The one act no MCP tool may perform (REVIEW_MODE.md §2.3).

A tool the agent can call cannot establish that the *user* called it, so
approving an agent id lives here and in `ctx.elicit`, and nowhere else. The
tests that matter most are the ones about where this command **cannot** reach:
an approval that reports success into a store the server never reads is worse
than a refusal, because the user then believes they have done it.
"""

from datetime import UTC, datetime

from epimemer.cli import _confirm, _list, _rename, main, unreachable_store
from epimemer.core.types import (
    Agent,
    agent_name,
    live_agents,
    with_description,
)
from epimemer.mcp.config import ServerConfig

AT = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


class TestWhereThisCommandCannotReach:
    """Approvals live in per-graph settings *inside* the backend, and an
    embedded store lives inside the server process — a second connection to
    `mem://` is a separate store, not a second view of one."""

    def test_the_in_memory_backend_is_unreachable(self):
        assert unreachable_store(ServerConfig(storage_backend="memory")) is not None

    def test_an_embedded_surrealdb_url_is_unreachable(self):
        for url in ("mem://", "file:///tmp/graph.db", "surrealkv://data"):
            config = ServerConfig(storage_backend="surrealdb", surrealdb_url=url)
            assert unreachable_store(config) is not None, url

    def test_a_served_surrealdb_is_reachable(self):
        config = ServerConfig(storage_backend="surrealdb", surrealdb_url="ws://localhost:8000/rpc")
        assert unreachable_store(config) is None

    def test_confirm_refuses_rather_than_writing_where_nobody_reads(self, capsys, monkeypatch):
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
        """ "(none)" from the wrong store reads exactly like "(none)" from the
        right one."""
        monkeypatch.setenv("EPIMEMER_STORAGE_BACKEND", "memory")

        code = main(["agents", "list"])

        captured = capsys.readouterr()
        assert code == 0
        assert "Note:" in captured.err
        assert "approved judges: (none)" in captured.out


class TestRenamingAJudge:
    """The name is the only mutable layer, and this is one of its two
    channels — the other being the elicitation prompt. It is here for the reason
    approval is: a handle an agent could rename is a handle an agent could point
    at another judge's history (§2.2).

    It also has to be here, and not only in the prompt, for a reason the prompt
    cannot cover: `epimemer agents list` reaches a served store, so a user with
    no agent session at all can still repair a name.
    """

    async def test_a_judge_is_renamed_by_name(self, storage):
        await storage.upsert_agent(Agent(id="k1", name="Opus 5", authorised_at=AT))

        message = await _rename(storage, "Opus 5", "reviewer", False)

        assert "'Opus 5' is now 'reviewer'" in message
        assert (await storage.get_agent("k1")).name == "reviewer"

    async def test_a_collision_says_what_the_flag_is_for(self, storage):
        # A command has nowhere to ask, so the question a collision raises is
        # answered up front or not at all.
        await storage.upsert_agent(Agent(id="k1", name="Opus 5 Judge", authorised_at=AT))
        await storage.upsert_agent(Agent(id="k2", name="Opus 5", authorised_at=AT))

        message = await _rename(storage, "Opus 5 Judge", "Opus 5", False)

        assert "--same-judge" in message
        assert len(live_agents(await storage.list_agents())) == 2, "nothing changed"

    async def test_the_flag_consolidates(self, storage):
        await storage.upsert_agent(Agent(id="k1", name="Opus 5 Judge", authorised_at=AT))
        await storage.upsert_agent(Agent(id="k2", name="Opus 5", authorised_at=AT))

        message = await _rename(storage, "Opus 5 Judge", "Opus 5", True)

        assert "one judge" in message
        assert [agent_name(a) for a in live_agents(await storage.list_agents())] == ["Opus 5"]
        assert await storage.get_agent("k1") is not None, "kept, not deleted"

    async def test_a_handle_naming_nobody_is_refused(self, storage):
        message = await _rename(storage, "nobody", "x", False)
        assert "No judge here answers to 'nobody'" in message


class TestConfirmingAnId:
    async def test_an_unclaimed_id_is_approved_with_nothing_to_confirm(self, storage):
        """The ordinary case: the refusal is what told the user the id exists,
        so the agent has not been able to record anything yet."""
        message = await _confirm(storage, "critic")

        assert await storage.get_approved_agent_ids() == ["critic"]
        assert "No judge here answers to it yet" in message

    async def test_confirming_stamps_the_description_in_front_of_the_user(self, storage):
        await storage.upsert_agent(
            Agent(
                id="critic",
                descriptions=with_description([], text="a critic", at=AT),
                authorised_at=AT,
            )
        )

        message = await _confirm(storage, "critic")

        agent = await storage.get_agent("critic")
        assert agent.descriptions[-1].confirmed_at is not None
        assert "a critic" in message

    async def test_only_the_current_version_is_confirmed(self, storage):
        """A user vouches for the wording they were shown, not for every claim
        the agent has ever made about itself."""
        await storage.upsert_agent(
            Agent(
                id="critic",
                descriptions=with_description(
                    with_description([], text="an early critic", at=AT),
                    text="a stricter critic",
                    at=AT,
                ),
                authorised_at=AT,
            )
        )

        await _confirm(storage, "critic")

        agent = await storage.get_agent("critic")
        assert agent.descriptions[0].confirmed_at is None
        assert agent.descriptions[1].confirmed_at is not None

    async def test_confirming_twice_leaves_the_first_confirmation_alone(self, storage):
        await storage.upsert_agent(
            Agent(
                id="critic",
                descriptions=with_description([], text="a critic", at=AT, confirmed_at=AT),
                authorised_at=AT,
            )
        )

        await _confirm(storage, "critic")

        agent = await storage.get_agent("critic")
        assert agent.descriptions[-1].confirmed_at == AT


class TestListing:
    async def test_an_unconfirmed_description_is_labelled_on_every_line(self, storage):
        """Said plainly wherever the prose appears: it is the agent's own
        assertion, and the listing is where a human decides what to trust."""
        await storage.set_approved_agent_ids(["critic"])
        await storage.upsert_agent(
            Agent(
                id="critic",
                descriptions=with_description([], text="a rigorous critic", at=AT),
                authorised_at=AT,
                last_seen_at=AT,
            )
        )

        out = await _list(storage)

        assert "a rigorous critic" in out
        assert "self-reported, unconfirmed" in out
        assert "approved judges: critic" in out

    async def test_an_empty_graph_says_so(self, storage):
        out = await _list(storage)

        assert "approved judges: (none)" in out
        assert "No agent has claimed an identity" in out


class TestRequiringAJudge:
    """The other thing only a user may do. Same wall, different setting — and
    the message behind it names a different environment variable, because one
    generic refusal would send half its readers to the wrong one."""

    async def test_turning_it_on_warns_when_nobody_is_approved(self, storage):
        from epimemer.cli import _require

        message = await _require(storage, "on", False)

        assert await storage.get_require_judge() is True
        # Said now rather than discovered by the next write failing: this is the
        # one setting that can make a working graph refuse everything.
        assert "no id is approved here" in message.lower()

    async def test_turning_it_on_with_an_approved_id_says_so(self, storage):
        from epimemer.cli import _require

        await storage.set_approved_agent_ids(["critic"])

        message = await _require(storage, "on", False)

        assert "critic" in message
        assert "no id is approved" not in message.lower()

    async def test_off_is_recorded_as_the_graphs_own_answer(self, storage):
        from epimemer.cli import _require

        await _require(storage, "off", True)

        assert await storage.get_require_judge() is False, (
            "an explicit off must outrank a server default of on"
        )

    async def test_default_clears_rather_than_freezing_todays_value(self, storage):
        from epimemer.cli import _require

        await _require(storage, "on", False)

        message = await _require(storage, "default", True)

        assert await storage.get_require_judge() is None
        assert "follows the server setting" in message

    async def test_listing_reports_the_policy(self, storage):
        from epimemer.cli import _list

        await storage.set_require_judge(True)

        assert "requires a judge: yes" in await _list(storage)

    def test_the_refusal_names_the_right_environment_variable(self, capsys, monkeypatch):
        monkeypatch.setenv("EPIMEMER_STORAGE_BACKEND", "memory")

        code = main(["agents", "require", "on"])

        err = capsys.readouterr().err
        assert code == 2
        assert "EPIMEMER_REQUIRE_JUDGE=true" in err
        assert "EPIMEMER_APPROVED_AGENTS" not in err


class TestDeclaringAFrame:
    """The user's statement about a graph written before frames were required.

    Here rather than in an MCP tool for the reason approval is here: an agent
    declaring what its own past writes were about is marking its own homework,
    and nothing in the graph could later tell that from a claim somebody made.
    """

    async def test_it_asks_before_writing(self, storage, monkeypatch):
        """The count is the only thing that tells a user how large the claim
        they are about to make is, and the sweep does not come off in one
        step — a wrong frame is removed one node at a time with `reframe`."""
        from epimemer.cli import _declare_frames
        from epimemer.core.types import BASE_METACONTEXT_ID, Topic

        await storage.store_node(Topic(content="Vienna", source_id="s1"))
        asked: list[str] = []
        monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or "n")

        message = await _declare_frames(storage, BASE_METACONTEXT_ID, None, False)

        assert "1 unframed node(s)" in asked[0]
        assert "Nothing declared" in message
        assert await storage.count_nodes_without_frame() == 1

    async def test_yes_skips_the_prompt_and_declares(self, storage):
        from epimemer.cli import _declare_frames
        from epimemer.core.types import BASE_METACONTEXT_ID, Topic

        await storage.store_node(Topic(content="Vienna", source_id="s1"))

        message = await _declare_frames(storage, BASE_METACONTEXT_ID, "the-user", True)

        assert "declared 1 node(s)" in message
        assert await storage.count_nodes_without_frame() == 0

    async def test_the_command_creates_a_frame_the_graph_lacks(self, storage):
        """The sweep refuses a frame that does not exist, and this is the only
        place that gap is closed: a person declaring *this graph is about the
        real world* is entitled to say that frame exists. No agent reaches it.
        """
        from epimemer.cli import _declare_frames
        from epimemer.core.types import Topic

        await storage.switch_database("undeclared")
        await storage.store_node(Topic(content="Vienna", source_id="s1"))

        message = await _declare_frames(storage, "the-real", None, True)

        assert "Created metacontext 'the-real'" in message
        assert await storage.get_metacontext("the-real") is not None
        assert await storage.count_nodes_without_frame() == 0

    async def test_a_finished_graph_says_so_without_asking(self, storage):
        """What makes the command naturally dead rather than deprecated: once
        no unframed node is left there is nothing for it to do, and it says
        that instead of prompting for a declaration about nothing."""
        from epimemer.cli import _declare_frames
        from epimemer.core.types import BASE_METACONTEXT_ID

        message = await _declare_frames(storage, BASE_METACONTEXT_ID, None, False)

        assert "Nothing to declare" in message

    def test_it_refuses_a_store_the_server_will_never_read(self, capsys, monkeypatch):
        """And the refusal has to say the graph is not stuck — an embedded
        graph is rebuilt rather than declared."""
        monkeypatch.setenv("EPIMEMER_STORAGE_BACKEND", "memory")

        code = main(["frames", "declare"])

        err = capsys.readouterr().err
        assert code == 2
        assert "rebuilt rather than declared" in err
        assert "EPIMEMER_APPROVED_AGENTS" not in err


class TestServe:
    """`epimemer serve` is the launch command an MCP client is given. It is
    not administration: it opens no store of its own, so the reachability
    check that guards every other command must not apply to it."""

    def test_serve_runs_the_mcp_server_and_nothing_else(self, monkeypatch):
        import epimemer.mcp.server as server

        calls: list[tuple] = []
        monkeypatch.setattr(server.mcp, "run", lambda *a, **k: calls.append((a, k)))
        monkeypatch.setattr(
            "epimemer.cli.load_config",
            lambda: (_ for _ in ()).throw(AssertionError("serve must not load a config")),
        )

        assert main(["serve"]) == 0
        assert calls == [((), {})]
