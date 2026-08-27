"""Ingest attribution, and the graph's answer to *must a write name a judge?*

Two halves of step 4 (REVIEW_MODE.md §3.3.1, §10.4). The first makes a fact's own
wording carry its author — where the unreviewable priors live, since nothing
downstream re-reads the material. The second lets a user insist on it.

**The setting is a gate on the agent, so the agent cannot touch it.** That is the
same rule the approved-id list follows, and the tests here treat any route from a
tool to this value as a bug rather than a convenience.

**Off is the default**, and a graph that never names anyone is not misconfigured:
blank means unknown, and for many graphs it does not matter who judged.
"""

import pytest

from epimemer.core.types import EdgeType, JudgeRef, NodeType
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig
from epimemer.storage.protocol import resolve_require_judge


CRITIC = JudgeRef(agent_id="critic", digest="d1")


@pytest.fixture
def embedder():
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def config():
    return ServerConfig(storage_backend="memory", embedding_provider="mock")


async def _ingest(storage, embedder, config, *, judge=None, **kwargs):
    """One document through both ingest steps, returning both results."""
    seg, _ = await tools.segment_text(
        "The treaty was signed in Vienna in 1815.",
        storage, embedder, config, judge=judge, **kwargs,
    )
    store, _ = await tools.store_decomposition(
        document_id=seg["document_id"],
        segments=[{
            "segment_id": seg["segments"][0]["segment_id"],
            "topics": ["the Congress of Vienna"],
            "facts": [{
                "content": "the treaty was signed in Vienna",
                "claim_kind": "event",
                "tags": ["diplomacy"],
            }],
            "inferences": ["the powers wanted a settled border"],
        }],
        storage=storage,
        embedding_provider=embedder,
        judge=judge,
    )
    return seg, store


class TestIngestRecordsWhoReadTheMaterial:
    """The priors supplied here — `claim_kind`, `confidence`, `importance` — are
    judgments nothing downstream will re-make, which is why §3.1 calls ingest the
    place the unreviewable judgments live."""

    async def test_every_stored_node_names_its_judge(
        self, storage, embedder, config
    ):
        await _ingest(storage, embedder, config, judge=CRITIC)

        nodes = await storage.query_nodes()
        assert nodes, "the ingest stored nothing"
        assert all(n.judged_by == CRITIC for n in nodes), [
            (n.content, n.judged_by) for n in nodes if n.judged_by != CRITIC
        ]

    async def test_every_edge_the_ingest_wrote_names_its_judge(
        self, storage, embedder, config
    ):
        """One stamp covers all five kinds this call builds, three of which come
        out of a Petritype net that would otherwise have to carry the judge."""
        _, _ = await _ingest(storage, embedder, config, judge=CRITIC)

        facts = [n for n in await storage.query_nodes(node_type=NodeType.FACT)]
        edges = [e for f in facts for e in await storage.get_edges_from(f.id)]
        assert {e.type for e in edges} >= {
            EdgeType.SOURCED_FROM, EdgeType.TAGGED_WITH,
        }
        assert all(e.judged_by == CRITIC for e in edges)

    async def test_a_tag_topic_names_the_agent_that_introduced_it(
        self, storage, embedder, config
    ):
        await _ingest(storage, embedder, config, judge=CRITIC)

        tag = await storage.get_node_by_content("diplomacy", node_type=NodeType.TOPIC)
        assert tag is not None and tag.judged_by == CRITIC

    async def test_a_publisher_entity_names_its_judge(
        self, storage, embedder, config
    ):
        await tools.segment_text(
            "A report.", storage, embedder, config,
            published_by="The Gazette", judge=CRITIC,
        )

        entity = await storage.get_node_by_content(
            "The Gazette", node_type=NodeType.TOPIC
        )
        assert entity.judged_by == CRITIC

    async def test_reusing_an_entity_does_not_restamp_it(
        self, storage, embedder, config
    ):
        """Mentioning a name again is not introducing it, and crediting the
        second agent would take the node from whoever created it."""
        await tools.segment_text(
            "A report.", storage, embedder, config,
            published_by="The Gazette", judge=CRITIC,
        )

        await tools.segment_text(
            "Another report.", storage, embedder, config,
            published_by="The Gazette",
            judge=JudgeRef(agent_id="editor", digest="d2"),
        )

        entity = await storage.get_node_by_content(
            "The Gazette", node_type=NodeType.TOPIC
        )
        assert entity.judged_by == CRITIC

    async def test_ingesting_without_a_judge_records_unknown(
        self, storage, embedder, config
    ):
        """The default, and not a degraded path."""
        await _ingest(storage, embedder, config)

        nodes = await storage.query_nodes()
        assert nodes and all(n.judged_by is None for n in nodes)

    async def test_the_document_itself_carries_no_judge(
        self, storage, embedder, config
    ):
        """*Who pasted this text* is a different question from *who judged what
        it says*, and only the second is a claim about the world."""
        seg, _ = await _ingest(storage, embedder, config, judge=CRITIC)

        doc = await storage.get_document(seg["document_id"])
        assert not hasattr(doc, "judged_by")


class TestTheGraphDecidesWhetherAJudgeIsRequired:
    async def test_a_graph_starts_with_no_answer_of_its_own(self, storage):
        assert await storage.get_require_judge() is None

    async def test_a_graphs_answer_beats_the_process_default(self, storage):
        await storage.set_require_judge(True)
        assert await tools.judge_required(storage, process_default=False) is True

        await storage.set_require_judge(False)
        assert await tools.judge_required(storage, process_default=True) is False

    async def test_no_answer_follows_the_process_default(self, storage):
        assert await tools.judge_required(storage, process_default=True) is True
        assert await tools.judge_required(storage, process_default=False) is False

    async def test_clearing_follows_the_default_rather_than_freezing_it(
        self, storage
    ):
        """`None` means *follow the default*, deliberately not *today's value of
        the default* — the same rule the reflect threshold states, so changing
        the server's setting later still reaches a graph that was once set."""
        await storage.set_require_judge(True)
        await storage.set_require_judge(None)

        assert await storage.get_require_judge() is None
        assert await tools.judge_required(storage, process_default=True) is True

    async def test_the_setting_is_per_graph(self, storage):
        await storage.set_require_judge(True)

        await storage.switch_database("elsewhere")

        assert await storage.get_require_judge() is None

    def test_the_resolver_has_one_rule(self):
        assert resolve_require_judge(None, True) is True
        assert resolve_require_judge(None, False) is False
        assert resolve_require_judge(True, False) is True
        assert resolve_require_judge(False, True) is False

    async def test_no_tool_can_change_it(self, storage):
        """A gate the agent can open is decoration.

        Checked against the tool module's whole surface rather than against a
        list, because the failure mode is somebody adding a convenient setter
        later and nobody noticing it undoes the point of the setting.
        """
        setters = [
            name for name in dir(tools)
            if "require" in name.lower() and name.startswith(("set_", "configure"))
        ]
        assert setters == []


class TestWhatARefusalSays:
    """The agent reading it can do neither of the two things that would fix it,
    so the message has to be aimed past it at the user."""

    def test_it_names_claim_agent_and_the_approved_ids(self):
        reason = tools.judge_required_reason(["critic", "editor"])

        assert "claim_agent" in reason
        assert "critic, editor" in reason

    def test_a_graph_with_no_approved_id_is_told_so_plainly(self):
        """Otherwise the advice is *call claim_agent*, which will also fail."""
        reason = tools.judge_required_reason([])

        assert "No judge has been approved" in reason
        assert "EPIMEMER_APPROVED_AGENTS" in reason
        assert "epimemer agents confirm" in reason

    def test_it_says_who_can_turn_the_requirement_off(self):
        reason = tools.judge_required_reason(["critic"])

        assert "epimemer agents require off" in reason
        assert "you cannot" in reason


class TestTheGateAtTheBoundary:
    """`_judge_for_write` is the only place the policy is read.

    It also answers **which graph before who** (#71). Everything it reads is
    graph state — the approved-agent list and the `require_judge` setting — so
    on a wrong-graph call it would otherwise refuse with *claim an agent* rather
    than *wrong graph*, and warn an operator that a judge is unapproved in a
    graph nobody meant to be in.
    """

    class _Ctx:
        def __init__(self, storage, config, stored=None, raises=False):
            self.lifespan_context = {"storage": storage, "config": config}
            self._stored = stored
            self._raises = raises
            self.set_calls: list = []

        async def get_state(self, key):
            if self._raises:
                raise RuntimeError("no session exists")
            return self._stored

        async def set_state(self, key, value):
            if self._raises:
                raise RuntimeError("no session exists")
            self.set_calls.append(value)

    async def test_a_permissive_graph_lets_an_unnamed_write_through(
        self, storage, config
    ):
        from epimemer.mcp.server import _judge_for_write

        judge, refused = await _judge_for_write(
            self._Ctx(storage, config), storage.current_database
        )

        assert judge is None and refused is None

    async def test_a_strict_graph_refuses_an_unnamed_write(self, storage, config):
        from epimemer.mcp.server import _judge_for_write

        await storage.set_require_judge(True)

        judge, refused = await _judge_for_write(
            self._Ctx(storage, config), storage.current_database
        )

        assert judge is None
        assert refused is not None and "claim_agent" in refused

    async def test_a_strict_graph_accepts_a_claimed_judge(self, storage, config):
        from epimemer.mcp.server import _judge_for_write

        await storage.set_require_judge(True)
        await storage.set_approved_agent_ids(["critic"])
        ctx = self._Ctx(storage, config, stored=CRITIC.model_dump(mode="json"))

        judge, refused = await _judge_for_write(ctx, storage.current_database)

        assert judge == CRITIC and refused is None

    async def test_a_wrong_graph_is_answered_before_the_judge_is(
        self, storage, config
    ):
        """The refusal names the graph, not the judge.

        Without this the agent is sent to `claim_agent` — which is itself gated
        — over a graph it never meant to be in, and it is the *wrong graph's*
        policy that decided to send it there.
        """
        from epimemer.mcp.server import _judge_for_write

        await storage.set_require_judge(True)

        judge, refused = await _judge_for_write(
            self._Ctx(storage, config), "somewhere-else"
        )

        assert judge is None
        assert refused is not None
        assert "somewhere-else" in refused
        assert "claim_agent" not in refused

    async def test_it_reads_no_graph_state_before_that(self, storage, config, caplog):
        """The operator warning is the other half. A bound judge is checked
        against the **active** graph's approved list, so a wrong-graph call
        would report a revocation that never happened."""
        import logging

        from epimemer.mcp.server import _judge_for_write

        # Approved somewhere the call did not mean to be. Without the graph
        # check `_bound_judge` reads *this* list, misses the judge, and reports
        # a revocation that never happened.
        await storage.set_approved_agent_ids(["someone-else"])
        ctx = self._Ctx(storage, config, stored=CRITIC.model_dump(mode="json"))

        with caplog.at_level(logging.WARNING):
            await _judge_for_write(ctx, "somewhere-else")

        assert "not approved" not in caplog.text

    async def test_a_revoked_id_is_refused_where_a_judge_is_required(
        self, storage, config
    ):
        """The two rules compose: approval is re-checked on every write, and a
        graph that requires a judge has no unknown to fall back to."""
        from epimemer.mcp.server import _judge_for_write

        await storage.set_require_judge(True)
        await storage.set_approved_agent_ids(["someone-else"])
        ctx = self._Ctx(storage, config, stored=CRITIC.model_dump(mode="json"))

        judge, refused = await _judge_for_write(ctx, storage.current_database)

        assert judge is None and refused is not None

    async def test_the_process_default_applies_where_the_graph_has_no_answer(
        self, storage
    ):
        from epimemer.mcp.server import _judge_for_write

        strict = ServerConfig(storage_backend="memory", require_judge=True)

        _, refused = await _judge_for_write(self._Ctx(storage, strict))

        assert refused is not None


class TestASessionlessClientCanStillClaim:
    """Session state needs a session. Without one, a claim that bound nothing
    would leave a strict graph refusing every write from a client that had
    correctly claimed an identity — the approval gap of §10.3, one layer over."""

    async def test_a_claim_without_a_session_still_binds_the_process(
        self, storage, config
    ):
        from epimemer.mcp.server import _bind_judge, _judge_for_write

        ctx = TestTheGateAtTheBoundary._Ctx(storage, config, raises=True)
        await storage.set_require_judge(True)
        await storage.set_approved_agent_ids(["critic"])

        bound = await _bind_judge(ctx, CRITIC)

        assert bound is False, "reported, so the caller can see what happened"
        judge, refused = await _judge_for_write(ctx, storage.current_database)
        assert judge == CRITIC and refused is None

    async def test_a_session_binding_clears_the_fallback(self, storage, config):
        """Otherwise a stale process-wide judge would outlive the session that
        replaced it."""
        from epimemer.mcp.server import _bind_judge

        ctx = TestTheGateAtTheBoundary._Ctx(storage, config)
        ctx.lifespan_context["fallback_judge"] = CRITIC.model_dump(mode="json")

        assert await _bind_judge(ctx, CRITIC) is True
        assert ctx.lifespan_context["fallback_judge"] is None
