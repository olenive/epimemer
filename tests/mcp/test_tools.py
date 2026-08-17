"""Tests for MCP tool implementations.

Tests call the pure functions in epimemer.mcp.tools directly
against every storage backend, with mock providers.
"""

import asyncio
import sys

import pytest

from datetime import datetime, timedelta, timezone

from petritype.core.executable_graph_components import (
    ArgumentEdgeToTransition,
    ExecutableGraph,
    ExecutableGraphOperations,
    FunctionTransitionNode,
    ListPlaceNode,
    ReturnedEdgeFromTransition,
)

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    Metacontext,
    NodeEdge,
    NodeStatus,
    NodeType,
    Topic,
    ValueSignal,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.tools import (
    add_timeline_timepoint,
    apply_reflection,
    archive,
    as_of,
    check_conflicts,
    create_metacontext,
    events_in_window,
    find_nodes,
    create_timelink,
    create_timeline,
    get_metacontexts_for_node,
    graph_stats,
    link,
    list_relations,
    list_sources,
    query_changes,
    query_graph,
    query_timeline,
    record_contradiction,
    record_variant,
    reflect,
    judge_importance,
    restore,
    search,
    segment_text,
    set_reference_time,
    store_decomposition,
    supersede_by,
    update,
)
from epimemer.mcp import tools
from epimemer.pipelines.reflection.archival import judgment_is_stale
from epimemer.mcp.tools import _node_to_dict
from epimemer.mcp.server import _resolve_windows
from epimemer.storage.memory import InMemoryStorage
from epimemer.storage.protocol import StorageBackend


# --- Fixtures ---


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def config() -> ServerConfig:
    return ServerConfig(
        storage_backend="memory",
        embedding_provider="mock",
        segmentation_strategy="paragraph",
    )


# --- Helpers ---


async def _two_step_ingest(
    content: str,
    storage: StorageBackend,
    embedding_provider: MockEmbeddingProvider,
    config: ServerConfig,
    *,
    metacontext_id: str | None = None,
) -> tuple[dict, dict]:
    """Run the two-step ingest: segment then store decomposition with dummy extraction."""
    seg_result, seg_meta = await segment_text(
        content, storage, embedding_provider, config,
    )
    segments = [
        {
            "segment_id": s["segment_id"],
            "topics": [f"Topic about: {s['segment_id']}"],
            "facts": [f"Fact from: {s['segment_id']}"],
            "inferences": [f"Inference from: {s['segment_id']}"],
        }
        for s in seg_result["segments"]
    ]
    store_result, store_meta = await store_decomposition(
        document_id=seg_result["document_id"],
        segments=segments,
        storage=storage,
        embedding_provider=embedding_provider,
        metacontext_id=metacontext_id,
    )
    return seg_result, store_result


async def _ingest_facts(
    facts: list[str],
    storage: StorageBackend,
    embedding_provider: MockEmbeddingProvider,
    config: ServerConfig,
    **kwargs,
) -> dict:
    """Ingest one segment whose facts are exactly `facts`.

    Timepoint proposal reads node content, so the tests need to say what that
    content is rather than let the helper invent it.
    """
    seg_result, _ = await segment_text(
        " ".join(facts) or "placeholder", storage, embedding_provider, config
    )
    store_result, _ = await store_decomposition(
        document_id=seg_result["document_id"],
        segments=[{"segment_id": seg_result["segments"][0]["segment_id"], "facts": facts}],
        storage=storage,
        embedding_provider=embedding_provider,
        **kwargs,
    )
    return store_result


async def _only_timeline(storage: StorageBackend):
    timelines = await storage.query_timelines()
    assert len(timelines) == 1, f"expected one timeline, got {timelines}"
    return timelines[0]


# --- Segment tests ---


class TestSegment:

    async def test_segments_text_and_stores_document(
        self, storage, embedding_provider, config
    ):
        result, meta = await segment_text(
            "Paragraph one about ML.\n\nParagraph two about climate.",
            storage, embedding_provider, config,
        )
        assert len(result["segments"]) == 2
        assert result["document_id"]
        # Document should be stored
        doc = await storage.get_document(result["document_id"])
        assert doc is not None

    async def test_segments_stored_in_storage(
        self, storage, embedding_provider, config
    ):
        result, _ = await segment_text(
            "First.\n\nSecond.",
            storage, embedding_provider, config,
        )
        stored = await storage.get_segments_for_document(result["document_id"])
        assert len(stored) == 2

    async def test_single_paragraph(
        self, storage, embedding_provider, config
    ):
        result, _ = await segment_text(
            "Just one paragraph here.",
            storage, embedding_provider, config,
        )
        assert len(result["segments"]) == 1


# --- Timepoint proposal tests ---


class TestTimepointProposal:
    """Ingestion proposes timepoints, so content-time mode is not empty on a
    graph nobody has hand-curated (ISSUES.md #34).

    The proposals ride in the same `write_batch_tx` as everything else: a
    `TIMELINK` naming a timeline that was never stored resolves to an empty row
    rather than an error, so a partial write would fail silently.
    """

    async def test_a_concrete_date_becomes_a_dated_timepoint(
        self, storage, embedding_provider, config
    ):
        await _ingest_facts(
            ["The treaty was signed on 12 March 1997."],
            storage, embedding_provider, config,
        )

        timeline = await _only_timeline(storage)
        assert len(timeline.timepoints) == 1
        assert timeline.timepoints[0].start == datetime(1997, 3, 12, tzinfo=timezone.utc)

    async def test_the_node_is_linked_to_its_timepoint(
        self, storage, embedding_provider, config
    ):
        await _ingest_facts(
            ["The treaty was signed on 12 March 1997."],
            storage, embedding_provider, config,
        )

        timeline = await _only_timeline(storage)
        fact = (await storage.query_nodes(node_type=NodeType.FACT))[0]
        links = await storage.get_edges_from(fact.id, edge_type=EdgeType.TIMELINK)
        assert [e.dst_id for e in links] == [timeline.id]
        assert links[0].metadata["timepoint_id"] == timeline.timepoints[0].id

    async def test_a_vague_expression_stays_undated(
        self, storage, embedding_provider, config
    ):
        """The undated lane exists so this never has to become a date."""
        await _ingest_facts(
            ["Trade grew during the Renaissance."],
            storage, embedding_provider, config,
        )

        point = (await _only_timeline(storage)).timepoints[0]
        assert point.start is None
        assert point.label == "during the Renaissance"

    async def test_text_with_no_temporal_expression_creates_no_timeline(
        self, storage, embedding_provider, config
    ):
        """An empty timeline in the panel's selector is worse than no timeline:
        it looks like data that failed to load."""
        await _ingest_facts(
            ["The room was quiet and the door was shut."],
            storage, embedding_provider, config,
        )

        assert await storage.query_timelines() == []

    async def test_one_date_in_two_nodes_is_one_timepoint(
        self, storage, embedding_provider, config
    ):
        """Two mentions of an instant are one point in time with two things
        said about it, not two coincident marks."""
        await _ingest_facts(
            ["The siege began in 1897.", "The harvest failed in 1897."],
            storage, embedding_provider, config,
        )

        timeline = await _only_timeline(storage)
        assert len(timeline.timepoints) == 1
        links = [
            edge
            for node in await storage.query_nodes(node_type=NodeType.FACT)
            for edge in await storage.get_edges_from(node.id, edge_type=EdgeType.TIMELINK)
        ]
        assert len(links) == 2

    async def test_a_second_document_appends_to_the_same_timeline(
        self, storage, embedding_provider, config
    ):
        """One timeline per graph, not per document: the panel shows one
        timeline at a time, so a timeline per document buries every mark."""
        await _ingest_facts(
            ["The siege began in 1897."], storage, embedding_provider, config
        )
        await _ingest_facts(
            ["The siege was lifted in 1901."], storage, embedding_provider, config
        )

        timeline = await _only_timeline(storage)
        assert [p.start.year for p in timeline.timepoints] == [1897, 1901]

    async def test_a_repeated_date_reuses_the_existing_timepoint(
        self, storage, embedding_provider, config
    ):
        await _ingest_facts(
            ["The siege began in 1897."], storage, embedding_provider, config
        )
        await _ingest_facts(
            ["The harvest failed in 1897."], storage, embedding_provider, config
        )

        assert len((await _only_timeline(storage)).timepoints) == 1

    async def test_proposals_go_to_a_named_timeline_when_asked(
        self, storage, embedding_provider, config
    ):
        created, _ = await create_timeline("Novel", storage)
        await _ingest_facts(
            ["The siege began in 1897."],
            storage, embedding_provider, config,
            timeline_id=created["timeline_id"],
        )

        timeline = await storage.get_timeline(created["timeline_id"])
        assert len(timeline.timepoints) == 1

    async def test_an_unknown_timeline_is_an_error_not_a_new_one(
        self, storage, embedding_provider, config
    ):
        """Silently creating it would put the document on a timeline the caller
        cannot find, under a name they did not choose."""
        with pytest.raises(ValueError, match="nonexistent"):
            await _ingest_facts(
                ["The siege began in 1897."],
                storage, embedding_provider, config,
                timeline_id="nonexistent",
            )

    async def test_proposals_can_be_switched_off(
        self, storage, embedding_provider, config
    ):
        await _ingest_facts(
            ["The siege began in 1897."],
            storage, embedding_provider, config,
            propose_timepoints=False,
        )

        assert await storage.query_timelines() == []

    async def test_the_result_reports_what_was_proposed(
        self, storage, embedding_provider, config
    ):
        result = await _ingest_facts(
            ["The siege began in 1897.", "Trade grew during the Renaissance."],
            storage, embedding_provider, config,
        )
        assert result["timepoints_proposed"] == 2

    async def test_the_surface_form_is_kept_on_a_dated_timepoint(
        self, storage, embedding_provider, config
    ):
        """A resolved date needs no label — it would only repeat the date — but
        what the text actually said is worth keeping, for anyone auditing a
        proposal that looks wrong."""
        await _ingest_facts(
            ["The treaty was signed on 12 March 1997."],
            storage, embedding_provider, config,
        )

        point = (await _only_timeline(storage)).timepoints[0]
        assert point.label is None
        assert point.metadata["detected_from"] == "12 March 1997"

    async def test_topics_and_inferences_are_proposed_from_too(
        self, storage, embedding_provider, config
    ):
        seg_result, _ = await segment_text(
            "Anything.", storage, embedding_provider, config
        )
        await store_decomposition(
            document_id=seg_result["document_id"],
            segments=[{
                "segment_id": seg_result["segments"][0]["segment_id"],
                "topics": ["The siege that began in 1897"],
                "inferences": ["The siege probably ended in 1901."],
            }],
            storage=storage,
            embedding_provider=embedding_provider,
        )

        assert len((await _only_timeline(storage)).timepoints) == 2


# --- Store Decomposition tests ---


class TestStoreDecomposition:

    async def test_creates_nodes_and_edges(
        self, storage, embedding_provider, config
    ):
        _, store_result = await _two_step_ingest(
            "Paragraph one about ML.\n\nParagraph two about climate.",
            storage, embedding_provider, config,
        )
        assert store_result["nodes_created"]["topics"] == 2
        assert store_result["nodes_created"]["facts"] == 2
        assert store_result["nodes_created"]["inferences"] == 2
        assert store_result["edges_created"] > 0

    async def test_embeddings_stored(
        self, storage, embedding_provider, config
    ):
        await _two_step_ingest(
            "Text about embeddings.",
            storage, embedding_provider, config,
        )
        from epimemer.core.types import NodeType
        topics = await storage.query_nodes(node_type=NodeType.TOPIC)
        for topic in topics:
            embs = await storage.get_embeddings_for_item(topic.id)
            assert len(embs) >= 1

    async def test_response_counts_accurate(
        self, storage, embedding_provider, config
    ):
        _, store_result = await _two_step_ingest(
            "A single paragraph.",
            storage, embedding_provider, config,
        )
        total_nodes = sum(store_result["nodes_created"].values())
        assert total_nodes == 3  # 1 topic + 1 fact + 1 inference

    async def test_importance_prior_applied_per_entry(
        self, storage, embedding_provider, config
    ):
        """An entry may carry an importance prior; anything else gets the default.

        A prior only — real judgment happens at reflect time, once the
        neighbourhood exists to judge triviality against.
        """
        seg_result, _ = await segment_text(
            "One paragraph.", storage, embedding_provider, config,
        )
        segment_id = seg_result["segments"][0]["segment_id"]
        await store_decomposition(
            document_id=seg_result["document_id"],
            segments=[{
                "segment_id": segment_id,
                "facts": [
                    {"content": "load-bearing fact", "importance": 0.9},
                    "ordinary fact",
                ],
            }],
            storage=storage,
            embedding_provider=embedding_provider,
        )

        facts = await storage.query_nodes(node_type=NodeType.FACT)
        by_content = {f.content: f.value.importance for f in facts}
        assert by_content["load-bearing fact"] == pytest.approx(0.9)
        assert by_content["ordinary fact"] == pytest.approx(0.5)

    async def test_importance_prior_out_of_range_rejected(
        self, storage, embedding_provider, config
    ):
        seg_result, _ = await segment_text(
            "One paragraph.", storage, embedding_provider, config,
        )
        with pytest.raises(ValueError):
            await store_decomposition(
                document_id=seg_result["document_id"],
                segments=[{
                    "segment_id": seg_result["segments"][0]["segment_id"],
                    "facts": [{"content": "too important", "importance": 1.5}],
                }],
                storage=storage,
                embedding_provider=embedding_provider,
            )

    async def test_with_metacontext(
        self, storage, embedding_provider, config
    ):
        mc = Metacontext(content="Science fiction")
        await storage.store_metacontext(mc)

        await _two_step_ingest(
            "The Culture ships are sentient AIs.",
            storage, embedding_provider, config,
            metacontext_id=mc.id,
        )

        from epimemer.core.types import NodeType
        topics = await storage.query_nodes(node_type=NodeType.TOPIC)
        for topic in topics:
            edges = await storage.get_edges_from(topic.id)
            mc_edges = [e for e in edges if e.type == EdgeType.HAS_METACONTEXT]
            assert len(mc_edges) == 1
            assert mc_edges[0].dst_id == mc.id


# --- Search tests ---


class TestSearch:

    async def _ingest_content(self, storage, embedding_provider, config):
        await _two_step_ingest(
            "Machine learning models require large datasets for training.",
            storage, embedding_provider, config,
        )

    async def test_returns_relevant_nodes(
        self, storage, embedding_provider, config
    ):
        await self._ingest_content(storage, embedding_provider, config)
        result, meta = await search(
            "Machine learning models require large datasets for training.",
            storage,
            embedding_provider,
            k=5,
            graph_hops=1,
        )
        assert len(result["nodes"]) > 0
        assert meta.nodes_returned > 0

    async def test_respects_node_type_filter(
        self, storage, embedding_provider, config
    ):
        await self._ingest_content(storage, embedding_provider, config)
        result, _ = await search(
            "Machine learning",
            storage,
            embedding_provider,
            k=5,
            node_types=["topic"],
            graph_hops=0,
        )
        for node in result["nodes"]:
            assert node["node_type"] == "topic"

    async def test_meta_has_source_types(
        self, storage, embedding_provider, config
    ):
        await self._ingest_content(storage, embedding_provider, config)
        _, meta = await search(
            "Machine learning",
            storage,
            embedding_provider,
        )
        assert isinstance(meta.source_types, dict)


# --- Retrieval reinforcement ---


async def _value_signals(storage) -> dict[str, tuple[object, object, float]]:
    """Both clocks + importance for every node, keyed by id."""
    nodes = await storage.query_nodes(status=NodeStatus.ACTIVE)
    return {
        n.id: (n.value.retrieved_at, n.value.importance_judged_at, n.value.importance)
        for n in nodes
    }


class TestSearchRecordsRetrieval:
    """`retrieved_at` is the whole record of use, and search is what writes it.

    Without it the only thing known about a node is its age, which cannot tell
    an old load-bearing node from an old dead one — the distinction archival
    nomination is built on.
    """

    async def test_search_stamps_the_nodes_it_returned(
        self, storage, embedding_provider, config
    ):
        await _two_step_ingest(
            "Machine learning models require large datasets.\n\n"
            "Volcanoes erupt when magma reaches the surface.",
            storage, embedding_provider, config,
        )
        before = await _value_signals(storage)
        assert all(retrieved is None for retrieved, _, _ in before.values())

        result, _ = await search(
            "Machine learning models require large datasets.",
            storage,
            embedding_provider,
            k=1,
            graph_hops=0,
            record_retrieval=True,
        )
        returned = {n["id"] for n in result["nodes"]}
        assert returned, "need at least one returned node to stamp"

        after = await _value_signals(storage)
        for node_id, signal in after.items():
            retrieved_at, judged_at, importance = signal
            if node_id in returned:
                assert retrieved_at is not None
            else:
                assert signal == before[node_id], "an unreturned node was touched"

    async def test_being_read_is_not_being_judged(
        self, storage, embedding_provider, config
    ):
        """Retrieval moves the retrieval clock and nothing else.

        The two are deliberately separate: a node returned by every search has
        been *used* a lot, which is not the same as anyone having decided it
        matters. Writing use into importance would let popularity forge a
        judgment nobody made.
        """
        await _two_step_ingest(
            "Machine learning models require large datasets.",
            storage, embedding_provider, config,
        )
        before = await _value_signals(storage)

        for _ in range(5):
            result, _ = await search(
                "Machine learning models require large datasets.",
                storage,
                embedding_provider,
                k=1,
                graph_hops=0,
                record_retrieval=True,
            )

        after = await _value_signals(storage)
        for node_id in {n["id"] for n in result["nodes"]}:
            _, judged_at, importance = after[node_id]
            _, old_judged_at, old_importance = before[node_id]
            assert importance == old_importance
            assert judged_at == old_judged_at is None

    async def test_recording_can_be_switched_off(
        self, storage, embedding_provider, config
    ):
        """It costs a write per returned node, so it has an off switch."""
        await _two_step_ingest(
            "Machine learning models require large datasets.",
            storage, embedding_provider, config,
        )
        before = await _value_signals(storage)

        await search(
            "Machine learning models require large datasets.",
            storage,
            embedding_provider,
            k=5,
            record_retrieval=False,
        )
        assert await _value_signals(storage) == before


# --- Explicit reinforcement (importance) ---


class TestJudgeImportanceDownward:
    """Judgment moves both ways, and the two directions are mirrors.

    `importance` used to have exactly one deliberate mutator and it only
    raised, so a node judged important once left the cheap archival tier
    permanently — cleanup never looked at it again. The step form is the
    interesting part: up and down close the gap to *their own* bound by the
    same fraction, which makes them symmetric in shape but not inverses.
    """

    async def test_a_downward_judgment_lowers_by_the_mirrored_step(self, storage):
        node = Fact(content="mattered once", source_id="s1")
        await storage.store_node(node)

        await judge_importance(
            node.id, direction="down", reason="the bug was fixed",
            storage=storage, importance_step=0.25,
        )

        stored = await storage.get_node(node.id)
        # 0.5 × (1 − 0.25). One step takes an un-judged node under the 0.5
        # nomination ceiling, which is the point of the whole issue.
        assert stored.value.importance == pytest.approx(0.375)

    async def test_repeated_downward_judgments_approach_zero_without_reaching(
        self, storage
    ):
        """Arithmetic must never judge a node out of existence.

        The mirror of the upward asymptote, and the same commitment as
        archive-never-delete expressed on the number line.
        """
        node = Fact(content="fading", source_id="s1")
        await storage.store_node(node)

        for _ in range(50):
            await judge_importance(
                node.id, direction="down", reason="less",
                storage=storage, importance_step=0.25,
            )

        stored = await storage.get_node(node.id)
        assert 0.0 < stored.value.importance < 1e-6

    async def test_up_then_down_does_not_return_home(self, storage):
        """Mirrors, not inverses — and the docstring has to say so."""
        node = Fact(content="contested", source_id="s1")
        await storage.store_node(node)

        await judge_importance(node.id, direction="up", reason="u",
                               storage=storage, importance_step=0.25)
        after_up = (await storage.get_node(node.id)).value.importance
        await judge_importance(node.id, direction="down", reason="d",
                               storage=storage, importance_step=0.25)
        after_down = (await storage.get_node(node.id)).value.importance

        assert after_up == pytest.approx(0.625)
        assert after_down == pytest.approx(0.46875)
        assert after_down < 0.5, "a reversed judgment lands below where it started"

    async def test_alternating_judgments_settle_into_a_two_cycle(self, storage):
        """Sustained disagreement parks the node at the un-judged default.

        Neither side wins: the orbit converges on {0.4286, 0.5714}, straddling
        0.5, so whether the node is nominatable depends on which judgment came
        last. That is the right terminal state — the most recent assessment
        governs and neither agent can lock it.
        """
        node = Fact(content="two agents disagree", source_id="s1")
        await storage.store_node(node)

        for i in range(40):
            await judge_importance(
                node.id, direction="up" if i % 2 == 0 else "down",
                reason="disagreement", storage=storage, importance_step=0.25,
            )

        after_down = (await storage.get_node(node.id)).value.importance
        assert after_down == pytest.approx(3 / 7, abs=1e-6)   # 0.42857…
        await judge_importance(node.id, direction="up", reason="u",
                               storage=storage, importance_step=0.25)
        after_up = (await storage.get_node(node.id)).value.importance
        assert after_up == pytest.approx(4 / 7, abs=1e-6)     # 0.57142…

    async def test_both_directions_share_one_provenance_trail_in_order(self, storage):
        """One chronological story, with each entry's direction on it.

        A reviewer wants to see a bump and its later reversal in sequence, with
        both reasons. Two lists would split exactly what makes the trail useful.
        """
        node = Fact(content="judged twice", source_id="s1")
        await storage.store_node(node)

        await judge_importance(node.id, direction="up", reason="cited",
                               storage=storage)
        await judge_importance(node.id, direction="down", reason="obsolete",
                               storage=storage)

        trail = (await storage.get_node(node.id)).metadata["reinforcements"]
        assert [(e["direction"], e["reason"]) for e in trail] == [
            ("up", "cited"), ("down", "obsolete"),
        ]

    async def test_a_downward_judgment_stamps_the_judgment_clock_only(self, storage):
        node = Fact(content="downgraded", source_id="s1")
        await storage.store_node(node)

        await judge_importance(node.id, direction="down", reason="less",
                               storage=storage)

        stored = await storage.get_node(node.id)
        assert stored.value.importance_judged_at is not None
        assert stored.value.retrieved_at is None

    async def test_an_unknown_direction_is_refused(self, storage):
        node = Fact(content="real", source_id="s1")
        await storage.store_node(node)
        with pytest.raises(ValueError, match="direction"):
            await judge_importance(node.id, direction="sideways", reason="r",
                                   storage=storage)

    async def test_rejects_unknown_node_and_unknown_related_id(self, storage):
        with pytest.raises(ValueError, match="nope"):
            await judge_importance("nope", direction="up", reason="r",
                                   storage=storage)
        node = Fact(content="real", source_id="s1")
        await storage.store_node(node)
        with pytest.raises(ValueError, match="ghost"):
            await judge_importance(node.id, direction="up", reason="r",
                                   storage=storage, related_id="ghost")


class TestJudgeImportanceUpward:
    """`judge_importance` is the only agent-facing path on `importance`.

    It is deliberately not a raw setter: every judgment leaves a trace, so a
    human reviewing a trivial-looking node rated highly can see the
    justification. These cover the upward direction.
    """

    async def test_reinforce_bumps_and_records_provenance(self, storage):
        node = Fact(content="load-bearing fact", source_id="s1")
        trigger = Fact(content="the new information", source_id="s1")
        await storage.store_node(node)
        await storage.store_node(trigger)

        result, meta = await judge_importance(
            node.id,
            direction="up",
            reason="cited by the incident review",
            storage=storage,
            related_id=trigger.id,
            importance_step=0.25,
        )

        stored = await storage.get_node(node.id)
        assert stored.value.importance == pytest.approx(0.5 + 0.25 * 0.5)
        assert result["importance"] == pytest.approx(stored.value.importance)
        assert meta.nodes_returned == 1

        trace = stored.metadata["reinforcements"]
        assert len(trace) == 1
        assert trace[0]["reason"] == "cited by the incident review"
        assert trace[0]["related_id"] == trigger.id
        assert trace[0]["at"]

    async def test_reinforce_appends_rather_than_replaces(self, storage):
        node = Fact(content="reinforced twice", source_id="s1")
        await storage.store_node(node)

        await judge_importance(node.id, direction="up", reason="first",
                               storage=storage, importance_step=0.25)
        await judge_importance(node.id, direction="up", reason="second",
                               storage=storage, importance_step=0.25)

        stored = await storage.get_node(node.id)
        # Asymptotic: 0.5 → 0.625 → 0.71875, approaching 1.0 without reaching it.
        assert stored.value.importance == pytest.approx(0.71875)
        assert [r["reason"] for r in stored.metadata["reinforcements"]] == [
            "first", "second",
        ]

    async def test_reinforce_stamps_the_judgment_clock_and_only_that_one(
        self, storage
    ):
        """The two clocks are independent, which is the point of having two.

        A judgment is not a use. Stamping the retrieval clock here would make an
        agent's assessment look like traffic, and archival nomination reads that
        clock to decide whether anything has ever touched the node.
        """
        node = Fact(content="judged but never read", source_id="s1")
        await storage.store_node(node)

        await judge_importance(node.id, direction="up", reason="matters", storage=storage)

        stored = await storage.get_node(node.id)
        assert stored.value.importance_judged_at is not None
        assert stored.value.retrieved_at is None

    async def test_reinforce_rejects_unknown_node(self, storage):
        with pytest.raises(ValueError, match="nope"):
            await judge_importance("nope", direction="up", reason="r", storage=storage)

    async def test_reinforce_rejects_unknown_related_id(self, storage):
        node = Fact(content="real", source_id="s1")
        await storage.store_node(node)

        with pytest.raises(ValueError, match="ghost"):
            await judge_importance(
                node.id, direction="up", reason="r", storage=storage,
                related_id="ghost",
            )

        # ...and the rejected call left nothing behind.
        stored = await storage.get_node(node.id)
        assert stored.value.importance == pytest.approx(0.5)
        assert "reinforcements" not in stored.metadata


# --- Link tests ---


class TestLink:

    async def test_creates_edge(self, storage):
        t = Topic(content="topic A", source_id="s1")
        f = Fact(content="fact B", source_id="s1")
        await storage.store_node(t)
        await storage.store_node(f)

        result, _ = await link(t.id, f.id, storage, edge_type="supports")
        assert "edge_id" in result

        edges = await storage.get_edges_from(t.id)
        assert any(e.type == EdgeType.SUPPORTS for e in edges)

    async def test_rejects_invalid_edge_type(self, storage):
        t = Topic(content="topic", source_id="s1")
        await storage.store_node(t)

        with pytest.raises(ValueError, match="Invalid edge_type"):
            await link(t.id, t.id, storage, edge_type="not_a_real_type")

    async def test_rejects_nonexistent_source(self, storage):
        t = Topic(content="topic", source_id="s1")
        await storage.store_node(t)

        with pytest.raises(ValueError, match="Source node"):
            await link("nonexistent", t.id, storage, edge_type="supports")

    async def test_rejects_nonexistent_destination(self, storage):
        t = Topic(content="topic", source_id="s1")
        await storage.store_node(t)

        with pytest.raises(ValueError, match="Destination node"):
            await link(t.id, "nonexistent", storage, edge_type="supports")

    async def test_creates_user_relation_with_kind(self, storage):
        a = Topic(content="article")
        b = Topic(content="BBC")
        await storage.store_node(a)
        await storage.store_node(b)
        result, _ = await link(a.id, b.id, storage, relation="published_by", kind="attribution")
        edge = (await storage.get_edges_from(a.id))[0]
        assert edge.type == EdgeType.RELATED and edge.label == "published_by"
        assert edge.kind == "attribution" and result["kind"] == "attribution"

    async def test_relation_kind_is_reused_per_label(self, storage):
        a, b, c = Topic(content="a"), Topic(content="b"), Topic(content="c")
        for n in (a, b, c):
            await storage.store_node(n)
        await link(a.id, b.id, storage, relation="published_by", kind="attribution")
        # Re-coin the same label with a different kind → the existing kind wins.
        result, _ = await link(a.id, c.id, storage, relation="published_by", kind="relationship")
        assert result["kind"] == "attribution"


# --- Update tests ---


class TestUpdate:

    async def test_supersedes_node(self, storage, embedding_provider):
        t = Topic(content="old content", source_id="s1")
        await storage.store_node(t)

        result, _ = await update(
            t.id,
            "new content",
            storage,
            embedding_provider,
            because="it_was_wrong",
        )
        assert result["old_node_id"] == t.id
        assert result["new_node_id"] != t.id

        old = await storage.get_node(t.id)
        assert old.status == NodeStatus.CORRECTED

        new = await storage.get_node(result["new_node_id"])
        assert new.content == "new content"
        assert isinstance(new, Topic)

    async def test_rejects_nonexistent_node(self, storage, embedding_provider):
        with pytest.raises(ValueError, match="not found"):
            await update(
                "nonexistent",
                "content",
                storage,
                embedding_provider,
                because="it_was_wrong",
            )

    async def test_preserves_node_type(self, storage, embedding_provider):
        f = Fact(content="old fact", source_id="s1")
        await storage.store_node(f)

        result, _ = await update(
            f.id,
            "new fact",
            storage,
            embedding_provider,
            because="it_was_wrong",
        )
        new = await storage.get_node(result["new_node_id"])
        assert isinstance(new, Fact)

    async def test_preserves_value_signal(self, storage, embedding_provider):
        t = Topic(content="old content", source_id="s1")
        t.value.confidence = 0.9
        t.value.importance = 0.8
        await storage.store_node(t)

        result, _ = await update(
            t.id,
            "new content",
            storage,
            embedding_provider,
            because="it_was_wrong",
        )
        new = await storage.get_node(result["new_node_id"])

        # A content correction must not reset accumulated value.
        assert new.value.confidence == 0.9
        assert new.value.importance == 0.8

        # The signal is copied, not shared: reinforcing the correction must not
        # rewrite the superseded original's recorded value.
        new.value.confidence = 0.1
        old = await storage.get_node(t.id)
        assert old.value.confidence == 0.9

    async def test_preserves_extraction_method(self, storage, embedding_provider):
        """Correcting the wording does not change where the material came from.

        Left unset, the replacement silently takes the model default, so every
        corrected node would claim a provenance nobody asserted.
        """
        f = Fact(content="old fact", source_id="s1",
                 extraction_method="agent:import")
        await storage.store_node(f)

        result, _ = await update(
            f.id,
            "new fact",
            storage,
            embedding_provider,
            because="it_was_wrong",
        )
        new = await storage.get_node(result["new_node_id"])

        assert new.extraction_method == "agent:import"


# --- Supersede-by-existing + Case B tests ---


class TestSupersedeBy:

    async def test_supersedes_old_by_existing(self, storage):
        old = Fact(content="CEO is X", source_id="s1")
        new = Fact(content="CEO is Y", source_id="s1")
        await storage.store_node(old)
        await storage.store_node(new)

        result, _ = await supersede_by(old.id, new.id, storage, because="it_was_wrong")

        assert result["superseded_id"] == old.id and result["by_id"] == new.id
        assert (await storage.get_node(old.id)).status == NodeStatus.CORRECTED
        assert (await storage.get_node(new.id)).status == NodeStatus.ACTIVE
        lineage = await storage.get_edges_from(old.id, edge_type=EdgeType.SUPERSEDED_BY)
        assert len(lineage) == 1 and lineage[0].dst_id == new.id

    async def test_does_not_migrate_edges(self, storage):
        # The existing node carries its own evidence — old's support must not move.
        old = Fact(content="old", source_id="s1")
        new = Fact(content="new", source_id="s1")
        support = Fact(content="2020 report", source_id="s1")
        for node in (old, new, support):
            await storage.store_node(node)
        await storage.store_edge(
            NodeEdge(src_id=support.id, dst_id=old.id, type=EdgeType.SUPPORTS)
        )

        await supersede_by(old.id, new.id, storage, because="it_was_wrong")

        assert await storage.get_edges_to(new.id, edge_type=EdgeType.SUPPORTS) == []
        assert len(await storage.get_edges_to(old.id, edge_type=EdgeType.SUPPORTS)) == 1

    async def test_rejects_self_supersede(self, storage):
        t = Topic(content="t", source_id="s1")
        await storage.store_node(t)
        with pytest.raises(ValueError, match="cannot supersede itself"):
            await supersede_by(t.id, t.id, storage, because="it_was_wrong")

    async def test_rejects_missing_nodes(self, storage):
        t = Topic(content="t", source_id="s1")
        await storage.store_node(t)
        with pytest.raises(ValueError, match="not found"):
            await supersede_by("nope", t.id, storage, because="it_was_wrong")
        with pytest.raises(ValueError, match="not found"):
            await supersede_by(t.id, "nope", storage, because="it_was_wrong")


class TestCaseBEvidenceStaleness:

    async def test_supersede_by_flags_inference_via_derived_from(self, storage):
        fact = Fact(content="80% effective", source_id="s1")
        newer = Fact(content="30% effective", source_id="s1")
        inf = Inference(content="drug is highly effective", source_id="s1")
        for node in (fact, newer, inf):
            await storage.store_node(node)
        await storage.store_edge(
            NodeEdge(src_id=inf.id, dst_id=fact.id, type=EdgeType.DERIVED_FROM)
        )

        await supersede_by(fact.id, newer.id, storage, because="it_was_wrong")

        flags = await storage.get_edges_to(inf.id, edge_type=EdgeType.EVIDENCE_SUPERSEDED)
        assert len(flags) == 1 and flags[0].src_id == fact.id

    async def test_update_flags_inference_via_supports(self, storage, embedding_provider):
        fact = Fact(content="fact", source_id="s1")
        inf = Inference(content="inference", source_id="s1")
        await storage.store_node(fact)
        await storage.store_node(inf)
        await storage.store_edge(
            NodeEdge(src_id=fact.id, dst_id=inf.id, type=EdgeType.SUPPORTS)
        )

        await update(
            fact.id,
            "corrected fact",
            storage,
            embedding_provider,
            because="it_was_wrong",
        )

        flags = await storage.get_edges_to(inf.id, edge_type=EdgeType.EVIDENCE_SUPERSEDED)
        assert len(flags) == 1

    async def test_supersede_clears_candidate_edges(self, storage):
        fact = Fact(content="old", source_id="s1")
        newer = Fact(content="new", source_id="s1")
        await storage.store_node(fact)
        await storage.store_node(newer)
        await storage.store_edge(
            NodeEdge(src_id=newer.id, dst_id=fact.id, type=EdgeType.SUPERSESSION_CANDIDATE)
        )

        await supersede_by(fact.id, newer.id, storage, because="it_was_wrong")

        assert await storage.get_edges_to(
            fact.id, edge_type=EdgeType.SUPERSESSION_CANDIDATE
        ) == []


# --- Reflect tests ---


class TestReflect:

    async def test_runs_all_operations(self, storage, embedding_provider):
        t1 = Topic(content="machine learning", source_id="s1")
        t2 = Topic(content="deep learning", source_id="s2")
        await storage.store_node(t1)
        await storage.store_node(t2)

        for t in [t1, t2]:
            vecs = await embedding_provider.embed([t.content])
            await storage.store_embedding(
                EmbeddingRecord(item_id=t.id, model_id=embedding_provider.model_id, vector=vecs[0])
            )

        result, _ = await reflect(storage, embedding_provider)
        assert "similar_pairs" in result
        assert "contradictions" in result
        assert "pending_review" in result

    async def test_surfaces_pending_review(self, storage, embedding_provider):
        old = Fact(content="old", source_id="s1")
        newer = Fact(content="new", source_id="s1")
        await storage.store_node(old)
        await storage.store_node(newer)
        await storage.store_edge(
            NodeEdge(src_id=newer.id, dst_id=old.id, type=EdgeType.SUPERSESSION_CANDIDATE)
        )

        result, _ = await reflect(storage, embedding_provider)
        flagged_ids = {e["node"]["id"] for e in result["pending_review"]}
        assert old.id in flagged_ids
        entry = next(e for e in result["pending_review"] if e["node"]["id"] == old.id)
        assert entry["review"]["superseded_candidate"] == [newer.id]
        assert entry["node"]["node_type"] == "fact"


    async def test_surfaces_archival_candidates(self, storage, embedding_provider):
        """Hygiene is another arm of the same loop: a worklist, like pending_review."""
        born = datetime.now(timezone.utc) - timedelta(days=30)
        trivial = Fact(
            content="a passing detail", source_id="s1", created_at=born,
            value=ValueSignal(importance=0.2),
        )
        await storage.store_node(trivial)

        result, _ = await reflect(storage, embedding_provider)

        entry = next(
            c for c in result["archival_candidates"] if c["node_id"] == trivial.id
        )
        assert entry["reason"] == "never_retrieved"
        assert entry["node_type"] == "fact"


class TestApplyReflectionJudgments:
    """The verdict that had no expression: keep it, stop treating it as important.

    Separate from `archivals` on purpose. Archival is an all-or-nothing status
    flip that wants human approval; a change of degree is something the agent
    may conclude alone, and forcing the first to express the second overstates
    what was concluded.
    """

    async def _judged_long_ago(self, storage):
        node = Fact(
            content="mattered during the incident", source_id="s1",
            value=ValueSignal(
                importance=0.9,
                importance_judged_at=datetime.now(timezone.utc) - timedelta(days=400),
            ),
        )
        await storage.store_node(node)
        return node

    async def test_a_downward_judgment_lowers_importance_and_moves_the_clock(
        self, storage, embedding_provider
    ):
        node = await self._judged_long_ago(storage)
        before = node.value.importance_judged_at

        result, _ = await apply_reflection(
            storage, embedding_provider,
            judgments=[{
                "node_id": node.id,
                "direction": "down",
                "reason": "the incident closed a year ago",
            }],
        )

        stored = await storage.get_node(node.id)
        assert result["judgments_applied"] == 1
        assert stored.value.importance < 0.9
        assert stored.value.importance_judged_at > before

    async def test_judging_back_up_also_clears_the_staleness(
        self, storage, embedding_provider
    ):
        """Re-confirming is a real answer to the nomination, not a no-op.

        Whichever way the judgment goes, the clock moves and the node leaves the
        stale set — which is what stops the same nominee coming back forever.
        """
        node = await self._judged_long_ago(storage)

        await apply_reflection(
            storage, embedding_provider,
            judgments=[{
                "node_id": node.id, "direction": "up",
                "reason": "still load-bearing",
            }],
        )

        stored = await storage.get_node(node.id)
        assert stored.value.importance > 0.9
        assert not judgment_is_stale(
            stored, datetime.now(timezone.utc) - timedelta(days=180)
        )

    async def test_unknown_ids_are_skipped_as_supersessions_are(
        self, storage, embedding_provider
    ):
        node = await self._judged_long_ago(storage)

        result, _ = await apply_reflection(
            storage, embedding_provider,
            judgments=[
                {"node_id": "ghost", "direction": "down", "reason": "r"},
                {"node_id": node.id, "direction": "down", "reason": "r"},
            ],
        )

        assert result["judgments_applied"] == 1


class TestApplyReflectionArchivals:
    """Approval is the human's; this is only what happens after it.

    Archive, never delete: the export comes back with the response so the
    caller keeps a copy, and `restore` puts the node back.
    """

    async def _trivial_fact(self, storage, content="a passing detail"):
        born = datetime.now(timezone.utc) - timedelta(days=30)
        node = Fact(
            content=content, source_id="s1", created_at=born,
            value=ValueSignal(importance=0.2),
        )
        await storage.store_node(node)
        return node

    async def test_archives_exactly_the_approved_ids(
        self, storage, embedding_provider
    ):
        approved = await self._trivial_fact(storage, "approved for archival")
        spared = await self._trivial_fact(storage, "not approved")

        result, _ = await apply_reflection(
            storage, embedding_provider, archivals=[approved.id],
        )

        assert result["nodes_archived"] == 1
        assert (await storage.get_node(approved.id)).status == NodeStatus.ARCHIVED
        assert (await storage.get_node(spared.id)).status == NodeStatus.ACTIVE

        exported = {n["id"] for n in result["archive_data"]["nodes"]}
        assert exported == {approved.id}

    async def test_archived_node_can_be_restored(self, storage, embedding_provider):
        node = await self._trivial_fact(storage)
        result, _ = await apply_reflection(
            storage, embedding_provider, archivals=[node.id],
        )

        await restore(result["archive_data"], storage)

        assert (await storage.get_node(node.id)).status == NodeStatus.ACTIVE

    async def test_archived_node_leaves_search(
        self, storage, embedding_provider, config
    ):
        """The point of the status flip: retrieval stops returning it."""
        seg_result, _ = await segment_text(
            "One paragraph about kestrels.", storage, embedding_provider, config,
        )
        await store_decomposition(
            document_id=seg_result["document_id"],
            segments=[{
                "segment_id": seg_result["segments"][0]["segment_id"],
                "facts": ["Kestrels hover while hunting."],
            }],
            storage=storage,
            embedding_provider=embedding_provider,
        )
        fact = (await storage.query_nodes(node_type=NodeType.FACT))[0]

        found, _ = await search(
            "Kestrels hover while hunting.", storage, embedding_provider,
            k=5, graph_hops=0, record_retrieval=False,
        )
        assert fact.id in {n["id"] for n in found["nodes"]}

        await apply_reflection(storage, embedding_provider, archivals=[fact.id])

        after, _ = await search(
            "Kestrels hover while hunting.", storage, embedding_provider,
            k=5, graph_hops=0, record_retrieval=False,
        )
        assert fact.id not in {n["id"] for n in after["nodes"]}

    async def test_inference_with_archived_evidence_returns_to_the_worklist(
        self, storage, embedding_provider
    ):
        """Part of the same loop: archiving evidence re-nominates what rests on it."""
        born = datetime.now(timezone.utc) - timedelta(days=30)
        evidence = Fact(
            content="a passing detail", source_id="s1", created_at=born,
            value=ValueSignal(importance=0.2),
        )
        inference = Inference(
            content="rests entirely on that detail", source_id="s1",
            created_at=born,
            # High importance, so only the follow-on rule can nominate it.
            value=ValueSignal(importance=0.9),
        )
        await storage.store_node(evidence)
        await storage.store_node(inference)
        await storage.store_edge(NodeEdge(
            src_id=inference.id, dst_id=evidence.id, type=EdgeType.DERIVED_FROM,
        ))

        await apply_reflection(storage, embedding_provider, archivals=[evidence.id])

        result, _ = await reflect(storage, embedding_provider)
        entry = next(
            c for c in result["archival_candidates"] if c["node_id"] == inference.id
        )
        assert entry["reason"] == "evidence_stale"

    async def test_skips_missing_and_already_archived_ids(
        self, storage, embedding_provider
    ):
        """Same forgiveness as supersessions: a stale worklist partially applies."""
        node = await self._trivial_fact(storage)
        await apply_reflection(storage, embedding_provider, archivals=[node.id])

        result, _ = await apply_reflection(
            storage, embedding_provider, archivals=[node.id, "no-such-node"],
        )

        assert result["nodes_archived"] == 0
        assert (await storage.get_node(node.id)).status == NodeStatus.ARCHIVED


# --- Apply Reflection (merge) tests ---


class TestApplyReflectionMerge:

    async def _store_topic(self, storage, embedding_provider, content, vector):
        t = Topic(content=content, source_id="s1")
        await storage.store_node(t)
        await storage.store_embedding(
            EmbeddingRecord(
                item_id=t.id, model_id=embedding_provider.model_id, vector=vector
            )
        )
        return t

    async def test_merge_collapses_near_duplicates(self, storage, embedding_provider):
        a = await self._store_topic(storage, embedding_provider, "ML basics", [1.0, 0.0])
        b = await self._store_topic(
            storage, embedding_provider, "Machine learning basics", [1.0, 0.0]
        )

        result, _ = await apply_reflection(
            storage, embedding_provider,
            merges=[{"source_ids": [a.id, b.id], "content": "Machine learning basics"}],
            merge_similarity_threshold=0.9,
        )

        assert result["topics_merged"] == 1
        assert result["merges_rejected"] == 0
        assert (await storage.get_node(a.id)).status == NodeStatus.MERGED
        assert (await storage.get_node(b.id)).status == NodeStatus.MERGED
        # Exactly one active topic remains — the merged node — and it is embedded.
        actives = await storage.query_nodes(node_type=NodeType.TOPIC)
        assert len(actives) == 1
        assert actives[0].content == "Machine learning basics"
        assert len(await storage.get_embeddings_for_item(actives[0].id)) == 1

    async def test_merge_rejected_below_threshold(self, storage, embedding_provider):
        a = await self._store_topic(storage, embedding_provider, "ML", [1.0, 0.0])
        b = await self._store_topic(storage, embedding_provider, "Cooking", [0.0, 1.0])

        result, _ = await apply_reflection(
            storage, embedding_provider,
            merges=[{"source_ids": [a.id, b.id], "content": "X"}],
            merge_similarity_threshold=0.9,
        )

        assert result["topics_merged"] == 0
        assert result["merges_rejected"] == 1
        # Distinct topics are left untouched and active.
        assert (await storage.get_node(a.id)).status == NodeStatus.ACTIVE
        assert (await storage.get_node(b.id)).status == NodeStatus.ACTIVE

    async def test_the_wired_merge_carries_the_value_clocks(
        self, storage, embedding_provider
    ):
        """The agent-driven path has the same obligation as the helper (#45).

        This is the merge that actually runs in production — `merge_similar_topics`
        is the pipeline helper. Carrying `importance` forward without the date it
        was judged leaves the merged node exempt from `stale_judgment` forever.
        """
        judged_at = datetime.now(timezone.utc) - timedelta(days=400)
        retrieved_at = datetime.now(timezone.utc) - timedelta(days=3)
        a = await self._store_topic(storage, embedding_provider, "ML basics", [1.0, 0.0])
        a.value = ValueSignal(
            importance=0.9, importance_judged_at=judged_at, retrieved_at=retrieved_at
        )
        await storage.store_node(a)
        b = await self._store_topic(
            storage, embedding_provider, "Machine learning basics", [1.0, 0.0]
        )

        result, _ = await apply_reflection(
            storage, embedding_provider,
            merges=[{"source_ids": [a.id, b.id], "content": "Machine learning basics"}],
            merge_similarity_threshold=0.9,
        )

        assert result["topics_merged"] == 1
        merged = (await storage.query_nodes(node_type=NodeType.TOPIC))[0]
        assert merged.value.importance == pytest.approx(0.9)
        assert merged.value.importance_judged_at == judged_at
        assert merged.value.retrieved_at == retrieved_at

    async def test_merge_refused_without_embeddings(self, storage, embedding_provider):
        # Similarity cannot be verified without embeddings → refuse.
        a = Topic(content="A", source_id="s1")
        b = Topic(content="B", source_id="s2")
        await storage.store_node(a)
        await storage.store_node(b)

        result, _ = await apply_reflection(
            storage, embedding_provider,
            merges=[{"source_ids": [a.id, b.id], "content": "AB"}],
        )

        assert result["topics_merged"] == 0
        assert result["merges_rejected"] == 1
        assert (await storage.get_node(a.id)).status == NodeStatus.ACTIVE


class TestApplyReflectionSupersessions:

    async def test_resolves_flagged_node(self, storage, embedding_provider):
        old = Fact(content="CEO is X", source_id="s1")
        winner = Fact(content="CEO is Y", source_id="s1")
        inf = Inference(content="X leads strategy", source_id="s1")
        for node in (old, winner, inf):
            await storage.store_node(node)
        # old is flagged as a supersession candidate by winner, and supports inf.
        await storage.store_edge(
            NodeEdge(src_id=winner.id, dst_id=old.id, type=EdgeType.SUPERSESSION_CANDIDATE)
        )
        await storage.store_edge(
            NodeEdge(src_id=old.id, dst_id=inf.id, type=EdgeType.SUPPORTS)
        )

        result, _ = await apply_reflection(
            storage, embedding_provider,
            supersessions=[
                {"old_id": old.id, "by_id": winner.id, "because": "it_was_wrong"},
            ],
        )

        assert result["supersessions_applied"] == 1
        assert (await storage.get_node(old.id)).status == NodeStatus.CORRECTED
        assert (await storage.get_node(winner.id)).status == NodeStatus.ACTIVE
        lineage = await storage.get_edges_from(old.id, edge_type=EdgeType.SUPERSEDED_BY)
        assert len(lineage) == 1 and lineage[0].dst_id == winner.id
        # Candidacy is cleared and the dependent inference is flagged evidence_stale.
        assert await storage.get_edges_to(
            old.id, edge_type=EdgeType.SUPERSESSION_CANDIDATE
        ) == []
        assert len(await storage.get_edges_to(
            inf.id, edge_type=EdgeType.EVIDENCE_SUPERSEDED
        )) == 1

    async def test_skips_self_and_missing(self, storage, embedding_provider):
        a = Fact(content="a", source_id="s1")
        await storage.store_node(a)

        result, _ = await apply_reflection(
            storage, embedding_provider,
            supersessions=[
                # self-supersede
                {"old_id": a.id, "by_id": a.id, "because": "it_was_wrong"},
                # missing winner
                {"old_id": a.id, "by_id": "missing", "because": "it_was_wrong"},
                # missing loser
                {"old_id": "missing", "by_id": a.id, "because": "it_was_wrong"},
            ],
        )
        assert result["supersessions_applied"] == 0
        assert (await storage.get_node(a.id)).status == NodeStatus.ACTIVE


# --- Query Graph tests ---


class TestQueryGraph:

    async def test_returns_neighbor_subgraph(self, storage):
        t = Topic(content="topic", source_id="s1")
        f = Fact(content="fact", source_id="s1")
        await storage.store_node(t)
        await storage.store_node(f)
        await storage.store_edge(
            NodeEdge(src_id=f.id, dst_id=t.id, type=EdgeType.SUPPORTS)
        )

        result, meta = await query_graph(t.id, storage, hops=1)
        node_ids = {n["id"] for n in result["nodes"]}
        assert t.id in node_ids
        assert f.id in node_ids
        assert meta.nodes_returned == 2
        assert meta.graph_hops == 1

    async def test_respects_hop_limit(self, storage):
        t1 = Topic(content="t1", source_id="s1")
        t2 = Topic(content="t2", source_id="s2")
        f = Fact(content="fact", source_id="s1")
        await storage.store_node(t1)
        await storage.store_node(t2)
        await storage.store_node(f)
        await storage.store_edge(
            NodeEdge(src_id=f.id, dst_id=t1.id, type=EdgeType.SUPPORTS)
        )
        await storage.store_edge(
            NodeEdge(src_id=f.id, dst_id=t2.id, type=EdgeType.SUPPORTS)
        )

        result, _ = await query_graph(t1.id, storage, hops=0)
        assert len(result["nodes"]) == 1

    async def test_rejects_nonexistent_node(self, storage):
        with pytest.raises(ValueError, match="not found"):
            await query_graph("nonexistent", storage)


# --- Archive tests ---


class TestArchive:

    async def test_finds_old_superseded_nodes(self, storage):
        from datetime import timedelta

        t = Topic(content="old topic", source_id="s1")
        await storage.store_node(t)
        old_time = datetime.now(timezone.utc) - timedelta(days=200)
        await storage.update_node_status(t.id, NodeStatus.SUPERSEDED, superseded_at=old_time)

        result, meta = await archive(storage, max_age_days=90)
        assert result["nodes_archived"] == 1
        assert len(result["archive_data"]["nodes"]) == 1

    async def test_excludes_active_nodes(self, storage):
        t = Topic(content="active topic", source_id="s1")
        await storage.store_node(t)

        result, _ = await archive(storage, max_age_days=0)
        assert result["nodes_archived"] == 0


# --- Restore tests ---


class TestRestore:

    async def test_reimports_nodes(self, storage):
        archive_data = {
            "nodes": [
                {
                    "id": "restored-1",
                    "content": "restored topic",
                    "source_id": "s1",
                    "node_type": "topic",
                    "status": "active",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
            "edges": [
                {
                    "id": "edge-r1",
                    "src_id": "restored-1",
                    "dst_id": "other",
                    "type": "supports",
                }
            ],
        }
        result, meta = await restore(archive_data, storage)
        assert result["nodes_restored"] == 1
        assert result["edges_restored"] == 1

        node = await storage.get_node("restored-1")
        assert node is not None
        assert node.content == "restored topic"

    async def test_restore_reactivates_an_archived_node(self, storage):
        """Un-archival, not reimport: the row is still there, so a re-insert
        would write nothing and the node would stay out of the active set."""
        node = Fact(content="archived but wanted back", source_id="s1")
        await storage.store_node(node)
        await storage.set_node_status_tx(
            [node], status=NodeStatus.ARCHIVED, at=datetime.now(timezone.utc)
        )

        result, _ = await restore({"nodes": [_node_to_dict(node)]}, storage)

        assert result["nodes_reactivated"] == 1
        assert result["nodes_restored"] == 0
        restored = await storage.get_node(node.id)
        assert restored.status == NodeStatus.ACTIVE
        assert restored.superseded_at is None

    async def test_restore_leaves_a_superseded_node_retired(self, storage):
        """An archive may hold nodes retired for being *wrong*. Those stay
        retired — restoring an archive is not a blanket resurrection."""
        node = Fact(content="wrong and superseded", source_id="s1")
        await storage.store_node(node)
        await storage.update_node_status(node.id, NodeStatus.SUPERSEDED)

        result, _ = await restore({"nodes": [_node_to_dict(node)]}, storage)

        assert result["nodes_reactivated"] == 0
        assert (await storage.get_node(node.id)).status == NodeStatus.SUPERSEDED

    async def test_restore_is_atomic_on_bad_record(self, storage):
        # A malformed edge (missing dst_id) must abort the whole restore so the
        # otherwise-valid node never lands — all-or-nothing.
        archive_data = {
            "nodes": [
                {
                    "id": "restore-atomic-1",
                    "content": "should not persist",
                    "source_id": "s1",
                    "node_type": "topic",
                    "status": "active",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
            "edges": [{"id": "edge-bad", "src_id": "restore-atomic-1", "type": "supports"}],
        }
        with pytest.raises(Exception):
            await restore(archive_data, storage)

        assert await storage.get_node("restore-atomic-1") is None


# --- Timeline tool tests ---


class TestTimelineTools:

    async def test_create_timeline(self, storage):
        result, meta = await create_timeline("AI History", storage, description="Key AI events")
        assert result["name"] == "AI History"
        assert result["timeline_id"]

        tl = await storage.get_timeline(result["timeline_id"])
        assert tl is not None
        assert tl.name == "AI History"

    async def test_create_timeline_with_reference_time(self, storage):
        """A fictional timeline's present is knowable at creation."""
        when = datetime(1897, 5, 26, tzinfo=timezone.utc)
        result, _ = await create_timeline("Dracula", storage, reference_time=when)

        assert result["reference_time"] == when.isoformat()
        assert (await storage.get_timeline(result["timeline_id"])).reference_time == when

    async def test_reference_time_unset_by_default(self, storage):
        result, _ = await create_timeline("real world", storage)

        assert result["reference_time"] is None
        assert (await storage.get_timeline(result["timeline_id"])).reference_time is None

    async def test_set_reference_time_updates_and_clears(self, storage):
        """Learned later, and revisable — a fiction's 'now' can be read wrong first."""
        created, _ = await create_timeline("Dracula", storage)
        tl_id = created["timeline_id"]
        when = datetime(1897, 5, 26, tzinfo=timezone.utc)

        result, meta = await set_reference_time(tl_id, storage, reference_time=when)
        assert result["reference_time"] == when.isoformat()
        assert meta.nodes_returned == 1
        assert (await storage.get_timeline(tl_id)).reference_time == when

        cleared, _ = await set_reference_time(tl_id, storage)
        assert cleared["reference_time"] is None
        assert (await storage.get_timeline(tl_id)).reference_time is None

    async def test_set_reference_time_preserves_timepoints(self, storage):
        """The whole timeline is re-stored, so its points must survive the write."""
        created, _ = await create_timeline("Dracula", storage)
        tl_id = created["timeline_id"]
        await add_timeline_timepoint(
            tl_id, storage, start=datetime(1897, 5, 26, tzinfo=timezone.utc),
        )

        await set_reference_time(
            tl_id, storage, reference_time=datetime(1897, 6, 1, tzinfo=timezone.utc),
        )

        assert len((await storage.get_timeline(tl_id)).timepoints) == 1

    async def test_set_reference_time_rejects_unknown_timeline(self, storage):
        with pytest.raises(ValueError, match="no-such-timeline"):
            await set_reference_time("no-such-timeline", storage)

    async def test_query_timeline_reports_reference_time(self, storage):
        when = datetime(1897, 5, 26, tzinfo=timezone.utc)
        created, _ = await create_timeline("Dracula", storage, reference_time=when)

        result, _ = await query_timeline(created["timeline_id"], storage)

        assert result["reference_time"] == when.isoformat()

    async def test_add_timepoint(self, storage):
        result, _ = await create_timeline("test", storage)
        tl_id = result["timeline_id"]

        result, _ = await add_timeline_timepoint(
            tl_id, storage,
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            label="New Year 2024",
        )
        assert result["timepoint_id"]
        assert result["timepoints_count"] == 1

    async def test_add_multiple_timepoints_sorted(self, storage):
        result, _ = await create_timeline("test", storage)
        tl_id = result["timeline_id"]

        await add_timeline_timepoint(tl_id, storage, start=datetime(2024, 6, 1, tzinfo=timezone.utc), label="June")
        await add_timeline_timepoint(tl_id, storage, start=datetime(2024, 1, 1, tzinfo=timezone.utc), label="Jan")

        tl = await storage.get_timeline(tl_id)
        assert tl.timepoints[0].label == "Jan"
        assert tl.timepoints[1].label == "June"

    async def test_add_timepoint_nonexistent_timeline(self, storage):
        with pytest.raises(ValueError, match="not found"):
            await add_timeline_timepoint("nonexistent", storage, label="X")

    async def test_query_nearest(self, storage):
        result, _ = await create_timeline("test", storage)
        tl_id = result["timeline_id"]

        await add_timeline_timepoint(tl_id, storage, start=datetime(2020, 1, 1, tzinfo=timezone.utc), label="2020")
        await add_timeline_timepoint(tl_id, storage, start=datetime(2024, 1, 1, tzinfo=timezone.utc), label="2024")

        result, meta = await query_timeline(
            tl_id, storage,
            target=datetime(2023, 1, 1, tzinfo=timezone.utc),
            k=1,
        )
        assert len(result["timepoints"]) == 1

    async def test_query_range(self, storage):
        result, _ = await create_timeline("test", storage)
        tl_id = result["timeline_id"]

        await add_timeline_timepoint(tl_id, storage, start=datetime(2020, 1, 1, tzinfo=timezone.utc), label="2020")
        await add_timeline_timepoint(tl_id, storage, start=datetime(2022, 1, 1, tzinfo=timezone.utc), label="2022")
        await add_timeline_timepoint(tl_id, storage, start=datetime(2024, 1, 1, tzinfo=timezone.utc), label="2024")

        result, _ = await query_timeline(
            tl_id, storage,
            range_start=datetime(2021, 1, 1, tzinfo=timezone.utc),
            range_end=datetime(2023, 1, 1, tzinfo=timezone.utc),
        )
        assert len(result["timepoints"]) == 1  # Only 2022

    async def test_create_timelink(self, storage):
        t = Topic(content="AI topic", source_id="s1")
        await storage.store_node(t)

        tl_result, _ = await create_timeline("AI Timeline", storage)
        tl_id = tl_result["timeline_id"]
        tp_result, _ = await add_timeline_timepoint(
            tl_id, storage,
            start=datetime(2023, 3, 1, tzinfo=timezone.utc),
            label="GPT-4 release",
        )
        tp_id = tp_result["timepoint_id"]

        result, _ = await create_timelink(t.id, tl_id, tp_id, storage)
        assert result["edge_id"]
        assert result["timepoint_id"] == tp_id

        edges = await storage.get_edges_from(t.id)
        tl_edges = [e for e in edges if e.type == EdgeType.TIMELINK]
        assert len(tl_edges) == 1
        assert tl_edges[0].metadata["timepoint_id"] == tp_id

    async def test_create_timelink_nonexistent_node(self, storage):
        tl_result, _ = await create_timeline("test", storage)
        with pytest.raises(ValueError, match="Node"):
            await create_timelink("nonexistent", tl_result["timeline_id"], "tp-1", storage)

    async def test_create_timelink_nonexistent_timepoint(self, storage):
        t = Topic(content="topic", source_id="s1")
        await storage.store_node(t)
        tl_result, _ = await create_timeline("test", storage)
        with pytest.raises(ValueError, match="Timepoint"):
            await create_timelink(t.id, tl_result["timeline_id"], "nonexistent", storage)


# --- Metacontext tool tests ---


class TestMetacontextTools:

    async def test_create_metacontext(self, storage):
        result, _ = await create_metacontext("Real historical events", storage)
        assert result["content"] == "Real historical events"
        assert result["metacontext_id"]

        mc = await storage.get_metacontext(result["metacontext_id"])
        assert mc is not None

    async def test_get_metacontexts_for_node(self, storage):
        mc = Metacontext(content="Fictional")
        await storage.store_metacontext(mc)

        t = Topic(content="Vampires", source_id="s1")
        await storage.store_node(t)

        await storage.store_edge(NodeEdge(
            src_id=t.id, dst_id=mc.id, type=EdgeType.HAS_METACONTEXT,
        ))

        result, meta = await get_metacontexts_for_node(t.id, storage)
        assert len(result["metacontexts"]) == 1
        assert result["metacontexts"][0]["content"] == "Fictional"

    async def test_get_metacontexts_empty(self, storage):
        t = Topic(content="topic", source_id="s1")
        await storage.store_node(t)

        result, _ = await get_metacontexts_for_node(t.id, storage)
        assert result["metacontexts"] == []

    async def test_ensure_base_metacontext_reserved_and_idempotent(self, storage):
        from epimemer.core.types import BASE_METACONTEXT_ID
        from epimemer.mcp.tools import ensure_base_metacontext

        mc1 = await ensure_base_metacontext(storage)
        assert mc1.id == BASE_METACONTEXT_ID
        assert mc1.content == "The Real"

        mc2 = await ensure_base_metacontext(storage)
        assert mc2.id == mc1.id
        all_mcs = await storage.query_metacontexts()
        assert sum(1 for m in all_mcs if m.id == BASE_METACONTEXT_ID) == 1


class TestReviewEdgeTraversal:

    async def test_review_edges_hidden_from_default_traversal(self, storage):
        a = Fact(content="a", source_id="s1")
        b = Fact(content="b", source_id="s1")
        await storage.store_node(a)
        await storage.store_node(b)
        await storage.store_edge(
            NodeEdge(src_id=b.id, dst_id=a.id, type=EdgeType.SUPERSESSION_CANDIDATE)
        )

        # Default traversal treats the review edge as metadata — not followed.
        result, _ = await query_graph(a.id, storage, hops=1)
        assert b.id not in {n["id"] for n in result["nodes"]}

        # ...but it is reachable with an explicit edge_types filter.
        result2, _ = await query_graph(
            a.id, storage, hops=1, edge_types=["supersession_candidate"]
        )
        assert b.id in {n["id"] for n in result2["nodes"]}


# --- Review loop: detection + verdict recording ---


async def _store_fact_with_embedding(
    storage, model_id, content, vector, *, metacontext_id=None
):
    f = Fact(content=content, source_id="s1")
    await storage.store_node(f)
    await storage.store_embedding(
        EmbeddingRecord(item_id=f.id, model_id=model_id, vector=vector)
    )
    if metacontext_id is not None:
        await storage.store_edge(
            NodeEdge(src_id=f.id, dst_id=metacontext_id, type=EdgeType.HAS_METACONTEXT)
        )
    return f


class TestCheckConflicts:

    async def test_surfaces_similar_active_facts(self, storage, embedding_provider):
        model_id = embedding_provider.model_id
        query = await _store_fact_with_embedding(storage, model_id, "CEO is Alice", [1.0, 0.0])
        similar = await _store_fact_with_embedding(
            storage, model_id, "Alice leads the company", [1.0, 0.0]
        )
        await _store_fact_with_embedding(storage, model_id, "unrelated", [0.0, 1.0])

        result, meta = await check_conflicts(
            [query.id], storage, embedding_provider, threshold=0.5
        )

        assert len(result["conflicts"]) == 1
        entry = result["conflicts"][0]
        assert entry["fact"]["id"] == query.id
        cand_ids = {c["id"] for c in entry["candidates"]}
        assert similar.id in cand_ids
        # The fact never appears as its own candidate.
        assert query.id not in cand_ids
        assert meta.nodes_returned == len(entry["candidates"])

    async def test_excludes_self_and_below_threshold(self, storage, embedding_provider):
        model_id = embedding_provider.model_id
        query = await _store_fact_with_embedding(storage, model_id, "a", [1.0, 0.0])
        await _store_fact_with_embedding(storage, model_id, "b", [0.0, 1.0])

        result, _ = await check_conflicts(
            [query.id], storage, embedding_provider, threshold=0.9
        )
        # Only self (excluded) and an orthogonal fact (below 0.9) → nothing.
        assert result["conflicts"] == []

    async def test_flags_cross_frame_candidate(self, storage, embedding_provider):
        model_id = embedding_provider.model_id
        fiction = Metacontext(content="Fiction")
        await storage.store_metacontext(fiction)
        query = await _store_fact_with_embedding(
            storage, model_id, "Napoleon lost at Waterloo", [1.0, 0.0]
        )
        variant = await _store_fact_with_embedding(
            storage, model_id, "Napoleon won at Waterloo", [1.0, 0.0],
            metacontext_id=fiction.id,
        )

        result, _ = await check_conflicts(
            [query.id], storage, embedding_provider, threshold=0.5
        )
        cand = next(
            c for c in result["conflicts"][0]["candidates"] if c["id"] == variant.id
        )
        assert cand["same_frame"] is False
        assert "Fiction" in cand["metacontexts"]

    async def test_skips_facts_without_embeddings(self, storage, embedding_provider):
        f = Fact(content="no embedding", source_id="s1")
        await storage.store_node(f)
        result, _ = await check_conflicts([f.id], storage, embedding_provider)
        assert result["conflicts"] == []


class TestRecordContradiction:

    async def test_records_same_frame_and_signals_notify(self, storage):
        a = Fact(content="X is true", source_id="s1")
        b = Fact(content="X is false", source_id="s1")
        await storage.store_node(a)
        await storage.store_node(b)

        result, _ = await record_contradiction(a.id, b.id, storage)

        assert result["created"] is True
        assert result["same_frame"] is True
        assert result["notify_user"] is True
        edges = await storage.get_edges_from(a.id, edge_type=EdgeType.CONTRADICTION)
        assert len(edges) == 1 and edges[0].dst_id == b.id

    async def test_idempotent_either_direction(self, storage):
        a = Fact(content="a", source_id="s1")
        b = Fact(content="b", source_id="s1")
        await storage.store_node(a)
        await storage.store_node(b)

        first, _ = await record_contradiction(a.id, b.id, storage)
        second, _ = await record_contradiction(b.id, a.id, storage)  # reversed

        assert first["created"] is True
        assert second["created"] is False
        assert second["edge_id"] == first["edge_id"]
        from_a = await storage.get_edges_from(a.id, edge_type=EdgeType.CONTRADICTION)
        from_b = await storage.get_edges_from(b.id, edge_type=EdgeType.CONTRADICTION)
        assert len(from_a) + len(from_b) == 1

    async def test_cross_frame_does_not_notify(self, storage):
        fiction = Metacontext(content="Fiction")
        await storage.store_metacontext(fiction)
        a = Fact(content="real", source_id="s1")
        b = Fact(content="fictional", source_id="s1")
        await storage.store_node(a)
        await storage.store_node(b)
        await storage.store_edge(
            NodeEdge(src_id=b.id, dst_id=fiction.id, type=EdgeType.HAS_METACONTEXT)
        )

        result, _ = await record_contradiction(a.id, b.id, storage)
        assert result["same_frame"] is False
        assert result["notify_user"] is False
        assert "warning" in result

    async def test_rejects_self_and_missing(self, storage):
        a = Fact(content="a", source_id="s1")
        await storage.store_node(a)
        with pytest.raises(ValueError, match="cannot contradict itself"):
            await record_contradiction(a.id, a.id, storage)
        with pytest.raises(ValueError, match="not found"):
            await record_contradiction(a.id, "nope", storage)


class TestRecordVariant:

    async def test_records_cross_frame_variant(self, storage):
        novel = Metacontext(content="Novel-X")
        await storage.store_metacontext(novel)
        real = Fact(content="Napoleon lost at Waterloo", source_id="s1")
        fic = Fact(content="Napoleon won at Waterloo", source_id="s1")
        await storage.store_node(real)
        await storage.store_node(fic)
        await storage.store_edge(
            NodeEdge(src_id=fic.id, dst_id=novel.id, type=EdgeType.HAS_METACONTEXT)
        )

        result, _ = await record_variant(real.id, fic.id, storage)
        assert result["created"] is True
        assert result["same_frame"] is False
        assert "warning" not in result
        edges = await storage.get_edges_from(real.id, edge_type=EdgeType.VARIANT_OF)
        assert len(edges) == 1 and edges[0].dst_id == fic.id

    async def test_same_frame_warns(self, storage):
        a = Fact(content="a", source_id="s1")
        b = Fact(content="b", source_id="s1")
        await storage.store_node(a)
        await storage.store_node(b)
        result, _ = await record_variant(a.id, b.id, storage)
        assert result["same_frame"] is True
        assert "warning" in result

    async def test_idempotent_either_direction(self, storage):
        a = Fact(content="a", source_id="s1")
        b = Fact(content="b", source_id="s1")
        await storage.store_node(a)
        await storage.store_node(b)
        first, _ = await record_variant(a.id, b.id, storage)
        second, _ = await record_variant(b.id, a.id, storage)
        assert second["created"] is False and second["edge_id"] == first["edge_id"]


class TestReflectFrameAware:

    async def _fact(self, storage, model_id, content, vector, *, mc=None):
        f = Fact(content=content, source_id="s1")
        await storage.store_node(f)
        await storage.store_embedding(
            EmbeddingRecord(item_id=f.id, model_id=model_id, vector=vector)
        )
        if mc is not None:
            await storage.store_edge(
                NodeEdge(src_id=f.id, dst_id=mc, type=EdgeType.HAS_METACONTEXT)
            )
        return f

    async def test_cross_frame_pairs_dropped_from_contradictions(
        self, storage, embedding_provider
    ):
        model_id = embedding_provider.model_id
        fiction = Metacontext(content="Fiction")
        await storage.store_metacontext(fiction)
        # Same-frame near-identical pair (both untagged → base reality).
        await self._fact(storage, model_id, "real A", [1.0, 0.0])
        await self._fact(storage, model_id, "real B", [1.0, 0.0])
        # Cross-frame near-identical pair (one tagged fiction).
        await self._fact(storage, model_id, "story A", [0.0, 1.0])
        await self._fact(storage, model_id, "story B", [0.0, 1.0], mc=fiction.id)

        result, _ = await reflect(storage, embedding_provider)
        contents = {
            frozenset({p["fact_a"]["content"], p["fact_b"]["content"]})
            for p in result["contradictions"]
        }
        # The same-frame pair is surfaced; the cross-frame pair is filtered out.
        assert frozenset({"real A", "real B"}) in contents
        assert frozenset({"story A", "story B"}) not in contents


# --- Retrieval visibility: frame-scoping + review labels (Phase 2c) ---


class TestSearchFrameScoping:

    async def test_includes_base_excludes_sibling_frames(
        self, storage, embedding_provider
    ):
        mc_real = Metacontext(content="Real world")
        mc_fiction = Metacontext(content="Fiction")
        await storage.store_metacontext(mc_real)
        await storage.store_metacontext(mc_fiction)

        # All facts share the query's embedding so vector search returns them all;
        # only the frame filter decides what comes back.
        query = "anything"
        qvec = (await embedding_provider.embed([query]))[0]
        real = await _store_fact_with_embedding(
            storage, embedding_provider.model_id, "real fact", qvec,
            metacontext_id=mc_real.id,
        )
        fic = await _store_fact_with_embedding(
            storage, embedding_provider.model_id, "fiction fact", qvec,
            metacontext_id=mc_fiction.id,
        )
        base = await _store_fact_with_embedding(
            storage, embedding_provider.model_id, "base fact", qvec,
        )

        result, _ = await search(
            query, storage, embedding_provider,
            k=20, graph_hops=0, metacontext_id=mc_real.id,
        )
        ids = {n["id"] for n in result["nodes"]}
        assert real.id in ids       # in-frame
        assert base.id in ids       # untagged base reality is always in scope
        assert fic.id not in ids    # sibling frame excluded

    async def test_cross_frame_returns_all_frames(self, storage, embedding_provider):
        mc_real = Metacontext(content="Real world")
        mc_fiction = Metacontext(content="Fiction")
        await storage.store_metacontext(mc_real)
        await storage.store_metacontext(mc_fiction)

        query = "anything"
        qvec = (await embedding_provider.embed([query]))[0]
        real = await _store_fact_with_embedding(
            storage, embedding_provider.model_id, "real fact", qvec,
            metacontext_id=mc_real.id,
        )
        fic = await _store_fact_with_embedding(
            storage, embedding_provider.model_id, "fiction fact", qvec,
            metacontext_id=mc_fiction.id,
        )

        result, _ = await search(
            query, storage, embedding_provider,
            k=20, graph_hops=0, metacontext_id=mc_real.id, cross_frame=True,
        )
        ids = {n["id"] for n in result["nodes"]}
        assert {real.id, fic.id} <= ids


class TestSearchFrameScopingBeyondTopK:
    """Frame-scoping must not be capped by the vector top-k.

    Vector search ranks first and returns k hits; the frame filter runs after.
    So a frame whose relevant nodes rank below k is dropped before the filter
    ever sees it — the query comes back short, or empty. These pin that an
    in-frame node still surfaces when out-of-frame nodes outrank it.
    """

    async def test_frame_scoped_search_reaches_beyond_top_k(
        self, storage, embedding_provider
    ):
        mc = Metacontext(content="Frame")
        sibling = Metacontext(content="Sibling")
        await storage.store_metacontext(mc)
        await storage.store_metacontext(sibling)

        model_id = embedding_provider.model_id
        k = 3
        qvec = (await embedding_provider.embed(["anything"]))[0]

        # k sibling-frame facts, each maximally similar to the query, fill the
        # top-k. They are excluded by the filter (not base reality), so pre-fix
        # the frame comes back empty.
        for i in range(k):
            await _store_fact_with_embedding(
                storage, model_id, f"sibling {i}", qvec, metacontext_id=sibling.id
            )
        # One in-frame fact, strictly less similar, so it ranks below the top-k.
        weaker = [qvec[0] * 0.5, *qvec[1:]]
        in_frame = await _store_fact_with_embedding(
            storage, model_id, "in-frame fact", weaker, metacontext_id=mc.id
        )

        result, _ = await search(
            "anything", storage, embedding_provider,
            k=k, graph_hops=0, metacontext_id=mc.id,
        )
        ids = {n["id"] for n in result["nodes"]}
        assert in_frame.id in ids  # missed pre-fix: filtered out of the top-k

    async def test_frame_scoped_search_iterates_past_initial_overfetch(
        self, storage, embedding_provider
    ):
        """Enough distractors that one over-fetch still misses the in-frame node;
        the fetch has to grow until the store is exhausted and it surfaces."""
        mc = Metacontext(content="Frame")
        sibling = Metacontext(content="Sibling")
        await storage.store_metacontext(mc)
        await storage.store_metacontext(sibling)

        model_id = embedding_provider.model_id
        k = 3
        qvec = (await embedding_provider.embed(["anything"]))[0]

        for i in range(15):  # well past the initial k*4 over-fetch of 12
            await _store_fact_with_embedding(
                storage, model_id, f"sibling {i}", qvec, metacontext_id=sibling.id
            )
        weaker = [qvec[0] * 0.5, *qvec[1:]]
        in_frame = await _store_fact_with_embedding(
            storage, model_id, "in-frame fact", weaker, metacontext_id=mc.id
        )

        result, _ = await search(
            "anything", storage, embedding_provider,
            k=k, graph_hops=0, metacontext_id=mc.id,
        )
        ids = {n["id"] for n in result["nodes"]}
        assert in_frame.id in ids
        # Nothing out-of-frame leaks in on the way to finding it.
        assert ids == {in_frame.id}


class TestReviewLabelsInRetrieval:

    async def test_query_graph_flags_superseded_candidate(self, storage):
        old = Fact(content="old", source_id="s1")
        newer = Fact(content="new", source_id="s1")
        await storage.store_node(old)
        await storage.store_node(newer)
        await storage.store_edge(
            NodeEdge(src_id=newer.id, dst_id=old.id, type=EdgeType.SUPERSESSION_CANDIDATE)
        )

        result, _ = await query_graph(old.id, storage, hops=0)
        node = result["nodes"][0]
        assert node["id"] == old.id
        assert node["review"]["superseded_candidate"] == [newer.id]

    async def test_query_graph_flags_contested(self, storage):
        a = Fact(content="X true", source_id="s1")
        b = Fact(content="X false", source_id="s1")
        await storage.store_node(a)
        await storage.store_node(b)
        await storage.store_edge(
            NodeEdge(src_id=a.id, dst_id=b.id, type=EdgeType.CONTRADICTION)
        )

        result, _ = await query_graph(a.id, storage, hops=0)
        assert result["nodes"][0]["review"]["contested"] == [b.id]

    async def test_clean_node_has_no_review_field(self, storage):
        t = Topic(content="fine", source_id="s1")
        await storage.store_node(t)
        result, _ = await query_graph(t.id, storage, hops=0)
        assert "review" not in result["nodes"][0]

    async def test_search_surfaces_review_labels(self, storage, embedding_provider):
        query = "anything"
        qvec = (await embedding_provider.embed([query]))[0]
        old = await _store_fact_with_embedding(
            storage, embedding_provider.model_id, "old fact", qvec
        )
        newer = Fact(content="new fact", source_id="s1")
        await storage.store_node(newer)
        await storage.store_edge(
            NodeEdge(src_id=newer.id, dst_id=old.id, type=EdgeType.SUPERSESSION_CANDIDATE)
        )

        result, _ = await search(query, storage, embedding_provider, k=20, graph_hops=0)
        flagged = next(n for n in result["nodes"] if n["id"] == old.id)
        assert flagged["review"]["superseded_candidate"] == [newer.id]


# --- Search with metacontext ---


class TestSearchWithMetacontext:

    async def test_search_filtered_by_metacontext(
        self, storage, embedding_provider, config
    ):
        mc_real = Metacontext(content="Real world")
        mc_fiction = Metacontext(content="Fiction")
        await storage.store_metacontext(mc_real)
        await storage.store_metacontext(mc_fiction)

        await _two_step_ingest(
            "Neural networks are used in image recognition.",
            storage, embedding_provider, config,
            metacontext_id=mc_real.id,
        )
        await _two_step_ingest(
            "The neural lace allows direct brain-computer interface.",
            storage, embedding_provider, config,
            metacontext_id=mc_fiction.id,
        )

        result, meta = await search(
            "Neural networks",
            storage, embedding_provider,
            k=10, graph_hops=0,
            metacontext_id=mc_real.id,
        )

        for node in result["nodes"]:
            assert "metacontexts" in node
            assert "Real world" in node["metacontexts"]


class TestGraphStats:

    async def test_empty_graph(self, storage):
        result, meta = await graph_stats(storage, default_reflect_threshold=10)
        assert result["total_nodes"] == 0
        assert result["total_edges"] == 0
        assert result["empty"] is True
        assert result["nodes_by_type"] == {"topic": 0, "fact": 0, "inference": 0}
        assert result["edges_by_type"] == {}
        assert result["metacontexts"] == 0
        assert meta.nodes_returned == 0

    async def test_counts_nodes_and_edges_by_type(self, storage):
        topic = Topic(content="t", source_id="s1")
        fact_a = Fact(content="f1", source_id="s1")
        fact_b = Fact(content="f2", source_id="s1")
        inference = Inference(content="i", source_id="s1")
        for node in (topic, fact_a, fact_b, inference):
            await storage.store_node(node)
        await storage.store_edge(
            NodeEdge(src_id=fact_a.id, dst_id=topic.id, type=EdgeType.SUPPORTS)
        )
        await storage.store_edge(
            NodeEdge(src_id=fact_b.id, dst_id=topic.id, type=EdgeType.SUPPORTS)
        )
        await storage.store_edge(
            NodeEdge(src_id=inference.id, dst_id=fact_a.id, type=EdgeType.DERIVED_FROM)
        )

        result, meta = await graph_stats(storage, default_reflect_threshold=10)
        assert result["total_nodes"] == 4
        assert result["nodes_by_type"] == {"topic": 1, "fact": 2, "inference": 1}
        assert result["total_edges"] == 3
        assert result["edges_by_type"] == {"supports": 2, "derived_from": 1}
        assert result["empty"] is False
        assert meta.nodes_returned == 4

    async def test_excludes_superseded_nodes(self, storage):
        topic = Topic(content="t", source_id="s1")
        await storage.store_node(topic)
        await storage.update_node_status(topic.id, NodeStatus.SUPERSEDED)

        result, _ = await graph_stats(storage, default_reflect_threshold=10)
        assert result["nodes_by_type"]["topic"] == 0
        assert result["total_nodes"] == 0

    async def test_counts_metacontexts(self, storage):
        await storage.store_metacontext(Metacontext(content="Real world"))
        await storage.store_metacontext(Metacontext(content="Fiction"))

        result, _ = await graph_stats(storage, default_reflect_threshold=10)
        assert result["metacontexts"] == 2


# --- Temporal queries: as_of + query_changes ---

_W_START = datetime(2026, 6, 10, tzinfo=timezone.utc)
_W_END = datetime(2026, 6, 20, tzinfo=timezone.utc)


def _fact_at(content, created, *, status=NodeStatus.ACTIVE, retired=None):
    return Fact(
        content=content, source_id="s1", created_at=created,
        status=status, superseded_at=retired,
    )


async def _supersede(storage, old, new, *, status, at):
    """A real supersession at a chosen instant, so windows can be exact."""
    await storage.supersede_node_tx(
        old, new,
        EmbeddingRecord(item_id=new.id, model_id="test", vector=[1.0, 0.0, 0.0]),
        NodeEdge(src_id=old.id, dst_id=new.id, type=EdgeType.SUPERSEDED_BY),
        status=status, superseded_at=at,
    )


class TestEventsInWindow:

    def test_created_in_window(self):
        f = _fact_at("x", datetime(2026, 6, 15, tzinfo=timezone.utc))
        evs = events_in_window(f, _W_START, _W_END)
        assert [e.kind for e in evs] == ["created"]
        assert evs[0].at == f.created_at

    def test_superseded_in_window(self):
        f = _fact_at(
            "x", datetime(2026, 6, 1, tzinfo=timezone.utc),
            status=NodeStatus.SUPERSEDED,
            retired=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )
        assert [e.kind for e in events_in_window(f, _W_START, _W_END)] == ["superseded"]

    def test_merged_in_window(self):
        f = _fact_at(
            "x", datetime(2026, 6, 1, tzinfo=timezone.utc),
            status=NodeStatus.MERGED,
            retired=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )
        assert [e.kind for e in events_in_window(f, _W_START, _W_END)] == ["merged"]

    def test_created_and_retired_same_window_yields_two_events(self):
        f = _fact_at(
            "x", datetime(2026, 6, 12, tzinfo=timezone.utc),
            status=NodeStatus.SUPERSEDED,
            retired=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )
        assert [e.kind for e in events_in_window(f, _W_START, _W_END)] == [
            "created", "superseded",
        ]

    def test_outside_window_yields_nothing(self):
        f = _fact_at("x", datetime(2026, 6, 1, tzinfo=timezone.utc))
        assert events_in_window(f, _W_START, _W_END) == []


class TestAsOf:

    async def test_snapshot_returns_active_set_at_instant(self, storage):
        old = _fact_at(
            "old", datetime(2026, 6, 1, tzinfo=timezone.utc),
            status=NodeStatus.SUPERSEDED,
            retired=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )
        new = _fact_at("new", datetime(2026, 6, 15, tzinfo=timezone.utc))
        await storage.store_node(old)
        await storage.store_node(new)

        # Before new is born and before old is retired: only old is live.
        early, _ = await as_of(datetime(2026, 6, 10, tzinfo=timezone.utc), storage)
        assert [n["id"] for n in early["nodes"]] == [old.id]

        # After old retired and new born: only new is live.
        late, meta = await as_of(datetime(2026, 6, 20, tzinfo=timezone.utc), storage)
        assert [n["id"] for n in late["nodes"]] == [new.id]
        assert meta.nodes_returned == 1

    async def test_omits_review_labels(self, storage):
        # A node with an incoming supersession_candidate edge would be labelled
        # `superseded_candidate` by review_labels — as_of must not surface that.
        old = _fact_at("old", datetime(2026, 6, 1, tzinfo=timezone.utc))
        new = _fact_at("new", datetime(2026, 6, 2, tzinfo=timezone.utc))
        await storage.store_node(old)
        await storage.store_node(new)
        await storage.store_edge(
            NodeEdge(src_id=new.id, dst_id=old.id, type=EdgeType.SUPERSESSION_CANDIDATE)
        )
        result, _ = await as_of(datetime(2026, 6, 10, tzinfo=timezone.utc), storage)
        assert all("review" not in n for n in result["nodes"])

    async def test_node_type_filter(self, storage):
        f = _fact_at("f", datetime(2026, 6, 1, tzinfo=timezone.utc))
        t = Topic(content="t", source_id="s1",
                  created_at=datetime(2026, 6, 1, tzinfo=timezone.utc))
        await storage.store_node(f)
        await storage.store_node(t)
        result, _ = await as_of(
            datetime(2026, 6, 10, tzinfo=timezone.utc), storage, node_types=["fact"]
        )
        assert [n["id"] for n in result["nodes"]] == [f.id]


class TestQueryChangesTool:

    async def test_groups_by_window_with_event_tags(self, storage):
        a = _fact_at("a", datetime(2026, 6, 15, tzinfo=timezone.utc))   # window 1
        b = _fact_at("b", datetime(2026, 6, 25, tzinfo=timezone.utc))   # window 2
        await storage.store_node(a)
        await storage.store_node(b)

        w1 = (_W_START, _W_END)
        w2 = (datetime(2026, 6, 20, tzinfo=timezone.utc),
              datetime(2026, 6, 30, tzinfo=timezone.utc))
        result, meta = await query_changes([w1, w2], storage)

        win1, win2 = result["windows"]
        assert [c["id"] for c in win1["changes"]] == [a.id]
        assert [e["kind"] for e in win1["changes"][0]["events"]] == ["created"]
        assert [c["id"] for c in win2["changes"]] == [b.id]
        assert meta.nodes_returned == 2

    async def test_two_events_for_create_and_retire_in_window(self, storage):
        f = _fact_at(
            "f", datetime(2026, 6, 12, tzinfo=timezone.utc),
            status=NodeStatus.SUPERSEDED,
            retired=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )
        await storage.store_node(f)
        result, _ = await query_changes([(_W_START, _W_END)], storage)
        changes = result["windows"][0]["changes"]
        # The node appears exactly once, carrying both lifecycle events.
        assert [c["id"] for c in changes] == [f.id]
        assert [e["kind"] for e in changes[0]["events"]] == ["created", "superseded"]

    async def test_includes_metacontext_and_review_labels(self, storage):
        fiction = Metacontext(content="Fiction")
        await storage.store_metacontext(fiction)
        a = _fact_at("a", datetime(2026, 6, 15, tzinfo=timezone.utc))
        newer = _fact_at("newer", datetime(2026, 6, 16, tzinfo=timezone.utc))
        await storage.store_node(a)
        await storage.store_node(newer)
        await storage.store_edge(
            NodeEdge(src_id=a.id, dst_id=fiction.id, type=EdgeType.HAS_METACONTEXT)
        )
        # newer nominates a as superseded → a gets a `superseded_candidate` label.
        await storage.store_edge(
            NodeEdge(src_id=newer.id, dst_id=a.id, type=EdgeType.SUPERSESSION_CANDIDATE)
        )
        result, _ = await query_changes([(_W_START, _W_END)], storage)
        by_id = {c["id"]: c for c in result["windows"][0]["changes"]}
        assert by_id[a.id]["metacontexts"] == ["Fiction"]
        assert "superseded_candidate" in by_id[a.id]["review"]

    async def test_query_changes_names_the_superseding_node(self, storage):
        """#57, durable surface. Before this, the history reported *that* a node
        retired and never *by whom* — the relation existed only as an edge."""
        old = _fact_at("Leningrad", datetime(2026, 6, 12, tzinfo=timezone.utc))
        new = _fact_at("Saint Petersburg", datetime(2026, 6, 14, tzinfo=timezone.utc))
        await storage.store_node(old)
        await _supersede(storage, old, new,
                         status=NodeStatus.HISTORICAL,
                         at=datetime(2026, 6, 15, tzinfo=timezone.utc))

        result, _ = await query_changes([(_W_START, _W_END)], storage)
        by_id = {c["id"]: c for c in result["windows"][0]["changes"]}
        retirements = [
            e for e in by_id[old.id]["events"] if e["kind"] == "historical"
        ]
        assert [e["counterpart"] for e in retirements] == [new.id]

    async def test_query_changes_reports_every_episode_of_a_recurring_node(
        self, storage,
    ):
        """Retired, brought back, retired again — three transitions, three reports.

        `(status, superseded_at)` is one slot, so it can hold only the last of
        them: clear it on the return and the first retirement vanishes from
        every window; keep it and the retirement reports the node's *current*
        status. A scalar `restored_at` defers the same overwrite to the second
        retirement rather than fixing it.
        """
        node = _fact_at("the claim", datetime(2026, 6, 1, tzinfo=timezone.utc))
        first = _fact_at("what replaced it",
                         datetime(2026, 6, 11, tzinfo=timezone.utc))
        second = _fact_at("what replaced it next",
                          datetime(2026, 6, 15, tzinfo=timezone.utc))
        await storage.store_node(node)

        retired_first = datetime(2026, 6, 11, tzinfo=timezone.utc)
        came_back = datetime(2026, 6, 13, tzinfo=timezone.utc)
        retired_again = datetime(2026, 6, 15, tzinfo=timezone.utc)

        await _supersede(storage, node, first,
                         status=NodeStatus.HISTORICAL, at=retired_first)
        # The return, driven at the storage layer: the `recurs` verdict that will
        # call this is #53 T2 work, and the contract it needs holds here already.
        await storage.set_node_status_tx(
            [await storage.get_node(node.id)], status=NodeStatus.ACTIVE, at=came_back,
        )
        await _supersede(storage, await storage.get_node(node.id), second,
                         status=NodeStatus.HISTORICAL, at=retired_again)

        def window(start_day, end_day):
            return (datetime(2026, 6, start_day, tzinfo=timezone.utc),
                    datetime(2026, 6, end_day, tzinfo=timezone.utc))

        result, _ = await query_changes(
            [window(10, 12), window(12, 14), window(14, 16)], storage,
        )
        reported = [
            [e for c in win["changes"] if c["id"] == node.id for e in c["events"]]
            for win in result["windows"]
        ]
        assert [[e["kind"] for e in evs] for evs in reported] == [
            ["historical"], ["restored"], ["historical"],
        ]
        assert [e["counterpart"] for e in reported[0]] == [first.id]
        assert [e["counterpart"] for e in reported[2]] == [second.id]


class TestResolveWindows:
    NOW = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)

    def test_defaults_to_last_24h(self):
        windows = _resolve_windows(self.NOW)
        assert windows == [(self.NOW - timedelta(hours=24), self.NOW)]

    def test_last_hours_trailing_window(self):
        windows = _resolve_windows(self.NOW, last_hours=6)
        assert windows == [(self.NOW - timedelta(hours=6), self.NOW)]

    def test_explicit_windows_with_open_end_uses_now(self):
        windows = _resolve_windows(
            self.NOW, windows=[["2026-06-20T00:00:00+00:00", ""]]
        )
        assert windows == [(datetime(2026, 6, 20, tzinfo=timezone.utc), self.NOW)]

    def test_windows_take_precedence_over_relative(self):
        windows = _resolve_windows(
            self.NOW, last_hours=6,
            windows=[["2026-06-01T00:00:00+00:00", "2026-06-02T00:00:00+00:00"]],
        )
        assert windows == [(
            datetime(2026, 6, 1, tzinfo=timezone.utc),
            datetime(2026, 6, 2, tzinfo=timezone.utc),
        )]

    def test_rejects_inverted_window(self):
        with pytest.raises(ValueError):
            _resolve_windows(
                self.NOW,
                windows=[["2026-06-20T00:00:00+00:00", "2026-06-10T00:00:00+00:00"]],
            )

# --- Sources, tags-as-topics, relations ---


class _FixedEmbed:
    """Embedding provider returning a fixed vector per exact string (for tests)."""
    model_id = "fixed"

    def __init__(self, mapping):
        self.mapping = mapping

    async def embed(self, texts):
        return [self.mapping[t] for t in texts]


async def _ingest(storage, ep, config, content, *, source, tags=None, facts):
    """Segment + decompose one document into the given facts."""
    seg, _ = await segment_text(content, storage, ep, config, source=source)
    sid = seg["segments"][0]["segment_id"]
    await store_decomposition(
        document_id=seg["document_id"],
        segments=[{"segment_id": sid, "topics": [], "facts": facts, "inferences": []}],
        storage=storage, embedding_provider=ep, tags=tags,
    )
    return seg["document_id"]


class TestIngestSourcesAndTags:

    async def test_sourced_from_edge_per_node(self, storage, embedding_provider, config):
        doc_id = await _ingest(storage, embedding_provider, config, "One para.",
                               source="ISSUES.md", facts=["a fact"])
        facts = await storage.query_nodes(node_type=NodeType.FACT)
        assert facts
        for f in facts:
            srcs = await storage.get_edges_from(f.id, edge_type=EdgeType.SOURCED_FROM)
            assert [e.dst_id for e in srcs] == [doc_id]

    async def test_tagged_with_creates_and_reuses_one_topic(self, storage, embedding_provider, config):
        await _ingest(storage, embedding_provider, config, "Alpha.",
                      source="A.md", tags=["billing"], facts=["af"])
        await _ingest(storage, embedding_provider, config, "Beta.",
                      source="B.md", tags=["billing"], facts=["bf"])
        billing = await storage.get_node_by_content("billing", node_type=NodeType.TOPIC)
        assert billing is not None
        # Exactly one billing Topic, with a tagged_with edge from each fact.
        topics = [t for t in await storage.query_nodes(node_type=NodeType.TOPIC)
                  if t.content == "billing"]
        assert len(topics) == 1
        taggers = await storage.get_edges_to(billing.id, edge_type=EdgeType.TAGGED_WITH)
        assert len(taggers) == 2

    async def test_published_by_entity_edge(self, storage, embedding_provider, config):
        seg, _ = await segment_text(
            "An article.", storage, embedding_provider, config,
            source="BBC article", published_by="BBC",
        )
        bbc = await storage.get_node_by_content("BBC", node_type=NodeType.TOPIC)
        assert bbc is not None
        edges = await storage.get_edges_to(bbc.id)
        assert any(
            e.type == EdgeType.RELATED and e.label == "published_by"
            and e.kind == "attribution" for e in edges
        )


class TestFindNodesTraversal:

    async def _two(self, storage, ep, config):
        await _ingest(storage, ep, config, "Alpha.", source="ISSUES.md",
                      tags=["billing"], facts=["af"])
        await _ingest(storage, ep, config, "Beta.", source="README.md",
                      tags=["weather"], facts=["bf"])

    async def test_find_by_sourced_from(self, storage, embedding_provider, config):
        doc_id = await _ingest(storage, embedding_provider, config, "Alpha.",
                               source="ISSUES.md", facts=["af"])
        result, _ = await find_nodes(storage, sourced_from=doc_id)
        assert {n["content"] for n in result["nodes"]} == {"af"}

    async def test_find_by_sourced_from_document_name(self, storage, embedding_provider, config):
        await _ingest(storage, embedding_provider, config, "Alpha.",
                      source="ISSUES.md", facts=["af"])
        # Resolves the document by its human source name, not just its id.
        result, _ = await find_nodes(storage, sourced_from="ISSUES.md")
        assert {n["content"] for n in result["nodes"]} == {"af"}

    async def test_find_by_tagged_with_name(self, storage, embedding_provider, config):
        await self._two(storage, embedding_provider, config)
        result, _ = await find_nodes(storage, tagged_with="billing")
        assert {n["content"] for n in result["nodes"]} == {"af"}

    async def test_requires_a_hub(self, storage):
        with pytest.raises(ValueError):
            await find_nodes(storage)


class TestListSourcesAndRelations:

    async def test_list_sources(self, storage, embedding_provider, config):
        await _ingest(storage, embedding_provider, config, "Alpha.",
                      source="ISSUES.md", facts=["af"])
        await segment_text("Article.", storage, embedding_provider, config,
                           source="BBC article", published_by="BBC")
        result, _ = await list_sources(storage)
        names = {s["name"] for s in result["sources"]}
        assert "BBC" in names  # the publishing entity is a source

    async def test_list_relations(self, storage, embedding_provider, config):
        await segment_text("Article.", storage, embedding_provider, config,
                           source="BBC article", published_by="BBC")
        result, _ = await list_relations(storage)
        labels = {r["label"]: r["kind"] for r in result["relations"]}
        assert labels.get("published_by") == "attribution"


class TestTraversalVsMigration:

    async def test_sourced_from_migrates_but_search_does_not_expand(
        self, storage, embedding_provider, config
    ):
        from epimemer.pipelines.graph_construction.versioning import supersede_node
        from epimemer.pipelines.query.graph_expansion import expand_via_graph
        doc_id = await _ingest(storage, embedding_provider, config, "Para.",
                               source="ISSUES.md", facts=["the fact"])
        fact = (await storage.query_nodes(node_type=NodeType.FACT))[0]

        # Default expansion from the fact must NOT cross sourced_from to the doc.
        nodes, _ = await expand_via_graph([fact], storage, hops=2)
        assert doc_id not in {n.id for n in nodes}

        # Supersession migrates the sourced_from edge onto the replacement.
        new = Fact(content="the corrected fact", source_id=fact.source_id)
        await supersede_node(
            fact,
            new,
            storage,
            embedding_provider,
            status=NodeStatus.CORRECTED,
        )
        migrated = await storage.get_edges_from(new.id, edge_type=EdgeType.SOURCED_FROM)
        assert [e.dst_id for e in migrated] == [doc_id]

    async def test_tagged_with_is_traversed(self, storage, embedding_provider, config):
        from epimemer.pipelines.query.graph_expansion import expand_via_graph
        await _ingest(storage, embedding_provider, config, "Para.",
                      source="A.md", tags=["billing"], facts=["the fact"])
        fact = (await storage.query_nodes(node_type=NodeType.FACT))[0]
        billing = await storage.get_node_by_content("billing", node_type=NodeType.TOPIC)
        nodes, _ = await expand_via_graph([fact], storage, hops=1)
        assert billing.id in {n.id for n in nodes}


class TestRelationConsolidation:

    async def test_find_similar_relation_pairs(self, storage):
        from epimemer.pipelines.reflection.relation_consolidation import (
            find_similar_relation_pairs,
        )
        emb = _FixedEmbed({
            "authored_by": [1.0, 0.0], "written_by": [1.0, 0.0], "funded_by": [0.0, 1.0],
        })
        a, b, c, d = (Topic(content=x) for x in ("a", "b", "c", "d"))
        for n in (a, b, c, d):
            await storage.store_node(n)
        await storage.store_edge(NodeEdge(src_id=a.id, dst_id=b.id, type=EdgeType.RELATED,
                                          label="authored_by", kind="relationship"))
        await storage.store_edge(NodeEdge(src_id=a.id, dst_id=c.id, type=EdgeType.RELATED,
                                          label="written_by", kind="relationship"))
        await storage.store_edge(NodeEdge(src_id=a.id, dst_id=d.id, type=EdgeType.RELATED,
                                          label="funded_by", kind="relationship"))
        pairs = await find_similar_relation_pairs(storage, emb, similarity_threshold=0.9)
        got = {frozenset((p["label_a"], p["label_b"])) for p in pairs}
        assert got == {frozenset(("authored_by", "written_by"))}

    async def test_apply_relation_merges(self, storage, embedding_provider):
        a, b, c = (Topic(content=x) for x in ("a", "b", "c"))
        for n in (a, b, c):
            await storage.store_node(n)
        await storage.store_edge(NodeEdge(src_id=a.id, dst_id=b.id, type=EdgeType.RELATED,
                                          label="written_by", kind="relationship"))
        await storage.store_edge(NodeEdge(src_id=a.id, dst_id=c.id, type=EdgeType.RELATED,
                                          label="authored_by", kind="relationship"))
        result, _ = await apply_reflection(
            storage, embedding_provider,
            relation_merges=[{"labels": ["written_by"], "into": "authored_by"}],
        )
        assert result["relations_consolidated"] == 1
        assert result["edges_relabeled"] == 1
        labels = {e.label for e in await storage.get_edges_from(a.id)}
        assert labels == {"authored_by"}

    async def test_reflect_surfaces_similar_relations_key(self, storage, embedding_provider):
        result, _ = await reflect(storage, embedding_provider)
        assert "similar_relations" in result and isinstance(result["similar_relations"], list)


class TestGraphNameValidationTools:
    """`use_graph` / `delete_graph` take arbitrary agent-supplied strings and
    reach SurrealQL that interpolates the database name."""

    async def test_use_graph_rejects_hostile_name(self, storage):
        result, _ = await tools.use_graph(
            "pwn`; REMOVE DATABASE `victim", storage, confirm=True
        )
        assert result["status"] == "invalid_name"
        assert "pwn`; REMOVE DATABASE `victim" not in await storage.list_databases()

    async def test_delete_graph_rejects_hostile_name(self, storage):
        before, _ = await tools.list_graphs(storage)
        result, _ = await tools.delete_graph("a;b", storage, confirm=True)
        assert result["status"] == "invalid_name"
        after, _ = await tools.list_graphs(storage)
        assert after["graphs"] == before["graphs"]

    async def test_use_graph_still_accepts_legal_name(self, storage):
        result, _ = await tools.use_graph("my-graph_2", storage, confirm=True)
        assert result["status"] in {"created", "switched"}
        assert result["active_graph"] == "my-graph_2"


class TestRunNetStdout:
    """`_run_net` used to swap `sys.stdout` for `sys.stderr` around execution to
    keep engine debug prints off MCP's stdio transport.

    Two problems: the swap is process-global across `await` points, so with
    overlapping tool calls one call saves another's redirected stdout as its
    "original" and the swap never unwinds; and the engine's prints are gated
    behind `verbose`, which defaults off, so there is nothing to suppress.
    """

    async def test_run_net_does_not_touch_stdout(self, capsys):
        original_stdout = sys.stdout

        async def _slow_double(x: int) -> int:
            # Yield control so the two runs genuinely interleave.
            await asyncio.sleep(0)
            return x * 2

        def _build() -> ExecutableGraph:
            return ExecutableGraphOperations.construct_graph([
                ListPlaceNode("Input", int, [5]),
                ListPlaceNode("Output", int),
                FunctionTransitionNode("double", _slow_double),
                ArgumentEdgeToTransition("Input", "double", "x"),
                ReturnedEdgeFromTransition("double", "Output"),
            ])

        results = await asyncio.gather(
            tools._run_net(_build(), "pipeline-a", None),
            tools._run_net(_build(), "pipeline-b", None),
        )

        for graph, fired in results:
            assert fired == 1
            output = next(p for p in graph.places if p.name == "Output")
            assert output.tokens == [10]

        assert sys.stdout is original_stdout
        assert capsys.readouterr().out == ""


def test_tool_count_matches_integration_doc():
    """INTEGRATION.md's stated tool count must track the actual registrations.

    A cheap guard against the doc drift catalogued in ISSUES.md — the count can
    only be stated in one canonical place, and this fails the moment a tool is
    added or removed without updating it.
    """
    import re
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    registered = (repo_root / "epimemer" / "mcp" / "server.py").read_text().count("@mcp.tool(")

    integration = (repo_root / "INTEGRATION.md").read_text()
    stated = re.search(r"listed with (\d+) tools", integration)
    assert stated is not None, "INTEGRATION.md no longer states a tool count to check"
    assert int(stated.group(1)) == registered, (
        f"INTEGRATION.md says {stated.group(1)} tools but server.py registers {registered}"
    )
