"""The one act no MCP tool may perform (REVIEW_MODE.md §2.3).

A tool the agent can call cannot establish that the *user* called it, so
approving an agent id lives here and in `ctx.elicit`, and nowhere else. The
tests that matter most are the ones about where this command **cannot** reach:
an approval that reports success into a store the server never reads is worse
than a refusal, because the user then believes they have done it.
"""

from datetime import UTC, datetime

import pytest

from epimemer.cli import (
    _confirm,
    _delete,
    _export_bundle,
    _import_bundle,
    _list,
    _reinstate,
    _rename,
    _retire,
    _verify_bundle,
    main,
    unreachable_store,
)
from epimemer.core.types import (
    Agent,
    DecisionKind,
    DecisionRecord,
    Fact,
    JudgeRef,
    Metacontext,
    Topic,
    agent_name,
    is_retired,
    live_agents,
    retired,
    with_description,
)
from epimemer.mcp.config import ServerConfig
from epimemer.pipelines import transfer
from epimemer.pipelines.transfer import unreachable_destination
from epimemer.storage.memory import InMemoryStorage

AT = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)

# The repair re-embeds each node it fixes, and the command builds its provider
# from the config it is handed. A mock one keeps the test off the model
# download without teaching the command about test doubles.
_MOCK_EMBEDDING = ServerConfig(embedding_provider="mock", embedding_dimension=8)


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

    async def test_retired_judges_get_a_heading_of_their_own_with_the_date(self, storage):
        """A marker in the main list would be a distinction the reader has to
        hunt for, and which judges may still be claimed is the reason to read
        the listing at all."""
        await storage.upsert_agent(Agent(id="k1", name="serving", authorised_at=AT))
        await storage.upsert_agent(retired(Agent(id="k2", name="stale", authorised_at=AT), AT))

        out = await _list(storage)

        serving_part, retired_part = out.split("retired judges:")
        assert "serving" in serving_part
        assert "stale" not in serving_part
        assert "stale" in retired_part
        assert "retired 2026-08-22" in retired_part
        assert "epimemer agents reinstate" in retired_part

    async def test_a_graph_with_no_retired_judge_has_no_heading(self, storage):
        await storage.upsert_agent(Agent(id="k1", name="serving", authorised_at=AT))

        assert "retired judges:" not in await _list(storage)


class TestRetiringAndReinstating:
    """Housekeeping a user does, and no MCP tool can: a handle an agent could
    retire is a handle an agent could use to take a rival judge off the roster.
    Neither asks for confirmation — retiring costs nothing that reinstating does
    not give straight back."""

    async def test_retiring_says_what_happens_to_sessions_already_bound(self, storage):
        await storage.upsert_agent(Agent(id="k1", name="Opus 5", authorised_at=AT))

        message = await _retire(storage, "Opus 5")

        assert is_retired(await storage.get_agent("k1"))
        assert "Sessions currently using this judge continue until they reconnect" in message

    async def test_retiring_one_already_retired_is_refused(self, storage):
        await storage.upsert_agent(retired(Agent(id="k1", name="Opus 5", authorised_at=AT), AT))

        message = await _retire(storage, "Opus 5")

        assert "already retired" in message

    async def test_reinstating_puts_it_back(self, storage):
        await storage.upsert_agent(retired(Agent(id="k1", name="Opus 5", authorised_at=AT), AT))

        message = await _reinstate(storage, "Opus 5")

        assert not is_retired(await storage.get_agent("k1"))
        assert "back in use" in message

    async def test_reinstating_a_serving_judge_is_refused(self, storage):
        await storage.upsert_agent(Agent(id="k1", name="Opus 5", authorised_at=AT))

        assert "is not retired" in await _reinstate(storage, "Opus 5")

    async def test_a_handle_naming_nobody_is_refused(self, storage):
        assert "No judge here answers to 'nobody'" in await _retire(storage, "nobody")
        assert "No judge here answers to 'nobody'" in await _reinstate(storage, "nobody")

    def test_the_refusal_off_an_embedded_store_says_what_is_left(self, capsys, monkeypatch):
        """No environment variable stands in for this one, so the message has to
        name the channel that does reach an embedded graph."""
        monkeypatch.setenv("EPIMEMER_STORAGE_BACKEND", "memory")

        code = main(["agents", "retire", "Opus 5"])

        err = capsys.readouterr().err
        assert code == 2
        assert "EPIMEMER_APPROVED_AGENTS" not in err
        assert "A retired judge…" in err


class TestDeletingAJudge:
    """The one hard delete, and it is gated on a scan the user is shown. A judge
    that has judged anything is retired instead: the journal is append-only, so
    the record would leave rows naming a key nothing resolves to a name."""

    async def test_the_scan_is_printed_before_anything_is_asked(self, storage, monkeypatch, capsys):
        await storage.upsert_agent(Agent(id="k1", name="Opus 5", authorised_at=AT))
        asked: list[str] = []
        monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or "n")

        message = await _delete(storage, "Opus 5", False)

        assert "has judged nothing in this graph" in capsys.readouterr().out
        assert "Delete the judge 'Opus 5'?" in asked[0]
        assert "Left 'Opus 5' as it was" in message
        assert await storage.get_agent("k1") is not None

    async def test_answering_yes_removes_the_record_and_the_approval(self, storage, monkeypatch):
        await storage.upsert_agent(Agent(id="k1", name="Opus 5", authorised_at=AT))
        await storage.set_approved_agent_ids(["k1"])
        monkeypatch.setattr("builtins.input", lambda prompt: "y")

        message = await _delete(storage, "Opus 5", False)

        assert "is deleted" in message
        assert await storage.get_agent("k1") is None
        assert await storage.get_approved_agent_ids() == []

    async def test_yes_skips_the_prompt(self, storage, monkeypatch):
        await storage.upsert_agent(Agent(id="k1", name="Opus 5", authorised_at=AT))
        monkeypatch.setattr(
            "builtins.input", lambda prompt: pytest.fail("--yes must not ask") or ""
        )

        await _delete(storage, "Opus 5", True)

        assert await storage.get_agent("k1") is None

    async def test_no_terminal_reads_as_no(self, storage, monkeypatch):
        """A shell that hands the command no terminal must not delete a record
        on the strength of an `EOFError`."""

        def _no_terminal(prompt):
            raise EOFError

        await storage.upsert_agent(Agent(id="k1", name="Opus 5", authorised_at=AT))
        monkeypatch.setattr("builtins.input", _no_terminal)

        await _delete(storage, "Opus 5", False)

        assert await storage.get_agent("k1") is not None

    async def test_a_judge_that_has_judged_is_refused_with_the_counts(self, storage, monkeypatch):
        await storage.upsert_agent(Agent(id="k1", name="Opus 5", authorised_at=AT))
        await storage.record_decision(
            DecisionRecord(
                kind=DecisionKind.MERGE,
                subject_ids=["n1"],
                judged_by=JudgeRef(agent_id="k1", digest="d1"),
            )
        )
        monkeypatch.setattr(
            "builtins.input", lambda prompt: pytest.fail("a refusal must not ask") or ""
        )

        message = await _delete(storage, "Opus 5", False)

        assert "1 journal row(s)" in message
        assert "epimemer agents retire Opus 5" in message
        assert await storage.get_agent("k1") is not None

    async def test_a_handle_naming_nobody_is_refused(self, storage):
        assert "No judge here answers to 'nobody'" in await _delete(storage, "nobody", True)


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


class TestDeclaringAMetacontext:
    """The user's statement about a graph written before metacontexts were required.

    Here rather than in an MCP tool for the reason approval is here: an agent
    declaring what its own past writes were about is marking its own homework,
    and nothing in the graph could later tell that from a claim somebody made.
    """

    async def test_it_asks_before_writing(self, storage, monkeypatch):
        """The count is the only thing that tells a user how large the claim
        they are about to make is, and the sweep does not come off in one
        step — a wrong metacontext is removed one node at a time with `reassign_metacontext`."""
        from epimemer.cli import _declare_metacontext
        from epimemer.core.types import BASE_METACONTEXT_ID, Topic

        await storage.store_node(Topic(content="Vienna", source_id="s1"))
        asked: list[str] = []
        monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or "n")

        message = await _declare_metacontext(storage, BASE_METACONTEXT_ID, None, False)

        assert "1 node(s) with no metacontext" in asked[0]
        assert "Nothing declared" in message
        assert await storage.count_nodes_without_metacontext() == 1

    async def test_yes_skips_the_prompt_and_declares(self, storage):
        from epimemer.cli import _declare_metacontext
        from epimemer.core.types import BASE_METACONTEXT_ID, Topic

        await storage.store_node(Topic(content="Vienna", source_id="s1"))

        message = await _declare_metacontext(storage, BASE_METACONTEXT_ID, "the-user", True)

        assert "declared 1 node(s)" in message
        assert await storage.count_nodes_without_metacontext() == 0

    async def test_the_command_creates_a_metacontext_the_graph_lacks(self, storage):
        """The sweep refuses a metacontext that does not exist, and this is the only
        place that gap is closed: a person declaring *this graph is about the
        real world* is entitled to say that metacontext exists. No agent reaches it.
        """
        from epimemer.cli import _declare_metacontext
        from epimemer.core.types import Topic

        await storage.switch_database("undeclared")
        await storage.store_node(Topic(content="Vienna", source_id="s1"))

        message = await _declare_metacontext(storage, "the-real", None, True)

        assert "Created metacontext 'the-real'" in message
        assert await storage.get_metacontext("the-real") is not None
        assert await storage.count_nodes_without_metacontext() == 0

    async def test_a_finished_graph_says_so_without_asking(self, storage):
        """What makes the command naturally dead rather than deprecated: once
        no node is left without a metacontext there is nothing for it to do, and it says
        that instead of prompting for a declaration about nothing."""
        from epimemer.cli import _declare_metacontext
        from epimemer.core.types import BASE_METACONTEXT_ID

        message = await _declare_metacontext(storage, BASE_METACONTEXT_ID, None, False)

        assert "Nothing to declare" in message

    def test_it_refuses_a_store_the_server_will_never_read(self, capsys, monkeypatch):
        """And the refusal has to say the graph is not stuck — an embedded
        graph is rebuilt rather than declared."""
        monkeypatch.setenv("EPIMEMER_STORAGE_BACKEND", "memory")

        code = main(["metacontexts", "declare"])

        err = capsys.readouterr().err
        assert code == 2
        assert "rebuilt rather than declared" in err
        assert "EPIMEMER_APPROVED_AGENTS" not in err


class TestRepairingDisplacedTagNames:
    """Also the user's act, for the reason declaring is: only a person who knows
    what the tag was for can say which of two sentences was its name.

    What the repair itself does is asserted in
    `tests/pipelines/test_tag_name_repair.py`; these are about the command
    around it, which is the prompt and what it says afterwards.
    """

    SENTENCE = "Validity intervals: when a claim was true, per source"

    async def _damaged(self, storage):
        from epimemer.core.types import EdgeType, NodeEdge, NodeStatus, Topic
        from epimemer.pipelines.metacontexts import TAG_EXTRACTION_METHOD
        from epimemer.pipelines.reflection.tag_name_repair import (
            ENRICHED_TAG_EXTRACTION_METHOD,
        )

        original = Topic(
            content="issue-53",
            source_id=None,
            extraction_method=TAG_EXTRACTION_METHOD,
            status=NodeStatus.CORRECTED,
        )
        enriched = Topic(
            content=self.SENTENCE,
            source_id=None,
            extraction_method=ENRICHED_TAG_EXTRACTION_METHOD,
            metadata={"enriched_from": original.id},
        )
        await storage.store_node(original)
        await storage.store_node(enriched)
        await storage.store_edge(
            NodeEdge(src_id=original.id, dst_id=enriched.id, type=EdgeType.SUPERSEDED_BY)
        )
        return enriched

    async def test_it_shows_both_wordings_and_does_nothing_on_a_refusal(self, storage, monkeypatch):
        from epimemer.cli import _repair_tag_names

        enriched = await self._damaged(storage)
        asked: list[str] = []
        monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or "n")

        message = await _repair_tag_names(storage, _MOCK_EMBEDDING, None, False)

        assert self.SENTENCE in asked[0]
        assert "issue-53" in asked[0]
        assert "restored 0 name(s)" in message
        assert (await storage.get_node(enriched.id)).content == self.SENTENCE

    async def test_yes_skips_the_prompt_and_restores_the_name(self, storage):
        from epimemer.cli import _repair_tag_names

        enriched = await self._damaged(storage)

        message = await _repair_tag_names(storage, _MOCK_EMBEDDING, "the-user", True)

        assert "restored 1 name(s) by 'the-user'" in message
        after = await storage.get_node(enriched.id)
        assert after.content == "issue-53"
        assert after.description == self.SENTENCE

    async def test_a_graph_with_nothing_to_repair_says_so(self, storage):
        from epimemer.cli import _repair_tag_names

        message = await _repair_tag_names(storage, _MOCK_EMBEDDING, None, True)

        assert "Nothing to repair" in message

    async def test_a_rerun_finds_nothing(self, storage):
        from epimemer.cli import _repair_tag_names

        await self._damaged(storage)
        await _repair_tag_names(storage, _MOCK_EMBEDDING, None, True)

        assert "Nothing to repair" in await _repair_tag_names(storage, _MOCK_EMBEDDING, None, True)

    def test_it_refuses_a_store_the_server_will_never_read(self, capsys, monkeypatch):
        """And says the graph is not stuck: enrichment cannot displace a name any
        more, so an embedded graph has nothing to repair in the first place."""
        monkeypatch.setenv("EPIMEMER_STORAGE_BACKEND", "memory")

        code = main(["tags", "repair"])

        err = capsys.readouterr().err
        assert code == 2
        assert "nothing to repair" in err
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


class TestGraphBundles:
    """Export, import and verify, which are the three acts a backup needs.

    Unlike the rest of this file these are not the user's *exclusive* act —
    `backup_graph` performs the same export from inside the server — so they do
    not sit behind the reachability wall. What they do get is the note, because
    an embedded store opened from out here is an empty one.
    """

    async def _graph_with_something_in_it(self):
        store = InMemoryStorage()
        await store.connect()
        await store.switch_database("source")
        await store.store_metacontext(Metacontext(id="the-real", content="The Real"))
        await store.store_node(Topic(id="topic-1", content="Weather"))
        await store.store_node(Fact(id="fact-1", content="It rained", source_id="seg-1"))
        return store

    async def test_a_directory_gets_the_default_name(self, tmp_path):
        store = await self._graph_with_something_in_it()

        message = await _export_bundle(store, _MOCK_EMBEDDING, "source", str(tmp_path), False)

        written = [path.name for path in tmp_path.iterdir()]
        assert written == [f"source-{datetime.now(UTC).date().isoformat()}.epimemer.tar.gz"]
        assert "Wrote graph 'source'" in message
        assert "nodes 2" in message

    async def test_a_graph_that_does_not_exist_is_refused_before_anything_is_written(
        self, tmp_path
    ):
        store = await self._graph_with_something_in_it()

        with pytest.raises(ValueError, match="'sorce' does not exist"):
            await _export_bundle(store, _MOCK_EMBEDDING, "sorce", str(tmp_path), False)

        assert list(tmp_path.iterdir()) == []
        assert "sorce" not in await store.list_databases()

    async def test_a_named_file_is_taken_literally(self, tmp_path):
        store = await self._graph_with_something_in_it()

        await _export_bundle(store, _MOCK_EMBEDDING, "source", str(tmp_path / "mine.tar.gz"), False)

        assert (tmp_path / "mine.tar.gz").is_file()

    async def test_plain_writes_a_directory_of_files(self, tmp_path):
        store = await self._graph_with_something_in_it()

        await _export_bundle(store, _MOCK_EMBEDDING, "source", str(tmp_path / "plain"), True)

        written = {path.name for path in (tmp_path / "plain").iterdir()}
        assert "manifest.json" in written
        assert "nodes.jsonl" in written

    async def test_import_rebuilds_the_graph_under_the_name_given(self, tmp_path):
        source = await self._graph_with_something_in_it()
        await _export_bundle(source, _MOCK_EMBEDDING, "source", str(tmp_path / "b.tar.gz"), False)
        target = InMemoryStorage()
        await target.connect()

        message = await _import_bundle(
            target, _MOCK_EMBEDDING, str(tmp_path / "b.tar.gz"), "restored"
        )

        assert "as graph 'restored'" in message
        assert "restored" in await target.list_databases()
        await target.switch_database("restored")
        assert (await target.get_node("fact-1")) is not None

    async def test_import_refuses_a_graph_that_exists(self, tmp_path):
        source = await self._graph_with_something_in_it()
        await _export_bundle(source, _MOCK_EMBEDDING, "source", str(tmp_path / "b.tar.gz"), False)

        with pytest.raises(ValueError, match="already exists"):
            await _import_bundle(source, _MOCK_EMBEDDING, str(tmp_path / "b.tar.gz"), "source")

    async def test_verify_reports_a_sound_bundle_and_leaves_no_graph(self, tmp_path):
        source = await self._graph_with_something_in_it()
        await _export_bundle(source, _MOCK_EMBEDDING, "source", str(tmp_path / "b.tar.gz"), False)
        target = InMemoryStorage()
        await target.connect()

        message = await _verify_bundle(target, _MOCK_EMBEDDING, str(tmp_path / "b.tar.gz"))

        assert "byte for byte" in message
        assert [name for name in await target.list_databases() if name.startswith("verify-")] == []

    async def test_verify_says_which_sections_came_back_different(self, tmp_path, monkeypatch):
        """The mismatch branch, which is the only reason to run verify at all.

        Forced by making the restore drop the nodes, which is what a broken
        verbatim write would look like from out here.
        """
        source = await self._graph_with_something_in_it()
        await _export_bundle(source, _MOCK_EMBEDDING, "source", str(tmp_path / "b.tar.gz"), False)
        target = InMemoryStorage()
        await target.connect()

        real = transfer.import_graph

        async def losing_the_nodes(bundle, storage, provider, *, graph):
            return await real(
                bundle.model_copy(
                    update={
                        "nodes": [],
                        "manifest": bundle.manifest.model_copy(
                            update={"counts": {**bundle.manifest.counts, "nodes": 0}}
                        ),
                    }
                ),
                storage,
                provider,
                graph=graph,
            )

        monkeypatch.setattr(transfer, "import_graph", losing_the_nodes)

        message = await _verify_bundle(target, _MOCK_EMBEDDING, str(tmp_path / "b.tar.gz"))

        assert "MISMATCH" in message
        assert "nodes.jsonl" in message


class TestWhereABundleCanBeWritten:
    """`--to` takes a local path or a cloud URL, through one `fsspec` call.

    A local path needs no extra installed, and a cloud URL whose driver is
    missing has to say which extra installs it: the alternative is an
    `ImportError` naming a package the user never asked for.
    """

    def test_a_local_path_needs_nothing(self, tmp_path):
        assert unreachable_destination(str(tmp_path / "b.tar.gz")) is None

    def test_a_cloud_url_without_its_driver_names_the_extra(self, monkeypatch):
        import fsspec

        def missing(protocol):
            raise ImportError(f"no driver for {protocol}")

        monkeypatch.setattr(fsspec, "get_filesystem_class", missing)

        for url, extra in (("gs://bucket/b.tar.gz", "gcs"), ("s3://bucket/b.tar.gz", "s3")):
            message = unreachable_destination(url)
            assert message is not None and f"epimemer[{extra}]" in message

    def test_an_unknown_scheme_says_so_rather_than_naming_an_extra(self, monkeypatch):
        import fsspec

        def unknown(protocol):
            raise ValueError(protocol)

        monkeypatch.setattr(fsspec, "get_filesystem_class", unknown)

        message = unreachable_destination("wat://bucket/b.tar.gz")
        assert message is not None and "does not know" in message

    async def test_export_refuses_before_reading_the_graph(self, monkeypatch):
        import fsspec

        monkeypatch.setattr(
            fsspec,
            "get_filesystem_class",
            lambda protocol: (_ for _ in ()).throw(ImportError(protocol)),
        )
        store = InMemoryStorage()
        await store.connect()

        with pytest.raises(ValueError, match=r"epimemer\[gcs\]"):
            await _export_bundle(store, _MOCK_EMBEDDING, "default", "gs://bucket/b.tar.gz", False)

    def test_the_command_exits_two_and_says_which_extra(self, monkeypatch, capsys, tmp_path):
        import fsspec

        monkeypatch.setenv("EPIMEMER_STORAGE_BACKEND", "memory")
        monkeypatch.setenv("EPIMEMER_EMBEDDING_PROVIDER", "mock")
        monkeypatch.setattr(
            fsspec,
            "get_filesystem_class",
            lambda protocol: (_ for _ in ()).throw(ImportError(protocol)),
        )

        code = main(["graphs", "export", "default", "--to", "s3://bucket/b.tar.gz"])

        assert code == 2
        assert "epimemer[s3]" in capsys.readouterr().err

    def test_the_command_writes_a_bundle_to_a_local_path(self, monkeypatch, capsys, tmp_path):
        """The wiring, end to end: argv in, a file on disk out."""
        monkeypatch.setenv("EPIMEMER_STORAGE_BACKEND", "memory")
        monkeypatch.setenv("EPIMEMER_EMBEDDING_PROVIDER", "mock")

        code = main(["graphs", "export", "default", "--to", str(tmp_path / "b.tar.gz")])

        assert code == 0
        assert (tmp_path / "b.tar.gz").is_file()
        assert "Wrote graph 'default'" in capsys.readouterr().out
