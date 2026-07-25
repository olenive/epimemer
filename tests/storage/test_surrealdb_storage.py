"""Tests for SurrealDB storage backend.

Uses mem:// (embedded) mode so no external SurrealDB instance is needed.
"""

from datetime import datetime, timezone

import pytest

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    NodeEdge,
    NodeStatus,
    NodeType,
    RawDocument,
    Segment,
    Topic,
)
from epimemer.storage.surrealdb_adapter import SurrealDBStorage


@pytest.fixture
async def store():
    s = SurrealDBStorage(url="mem://")
    await s.connect()
    yield s
    await s.close()


class TestDocumentStorage:

    async def test_store_and_retrieve(self, store):
        doc = RawDocument(content="Hello world")
        await store.store_document(doc)
        got = await store.get_document(doc.id)
        assert got is not None
        assert got.content == "Hello world"

    async def test_get_missing_returns_none(self, store):
        got = await store.get_document("nonexistent")
        assert got is None


class TestSegmentStorage:

    async def test_store_and_retrieve(self, store):
        doc = RawDocument(content="Hello world")
        await store.store_document(doc)
        seg = Segment(source_id=doc.id, text="Hello", span_start=0, span_end=5)
        await store.store_segment(seg)
        segments = await store.get_segments_for_document(doc.id)
        assert len(segments) == 1
        assert segments[0].text == "Hello"

    async def test_multiple_segments_ordered(self, store):
        seg1 = Segment(source_id="d1", text="A", span_start=0, span_end=1)
        seg2 = Segment(source_id="d1", text="B", span_start=2, span_end=3)
        await store.store_segment(seg2)  # Insert out of order
        await store.store_segment(seg1)
        segments = await store.get_segments_for_document("d1")
        assert len(segments) == 2
        assert segments[0].text == "A"  # Should be ordered by span_start


class TestNodeStorage:

    async def test_store_and_retrieve_topic(self, store):
        t = Topic(content="ML topic", source_id="s1")
        await store.store_node(t)
        got = await store.get_node(t.id)
        assert isinstance(got, Topic)
        assert got.content == "ML topic"

    async def test_store_and_retrieve_fact(self, store):
        f = Fact(content="A fact", source_id="s1")
        await store.store_node(f)
        got = await store.get_node(f.id)
        assert isinstance(got, Fact)

    async def test_store_and_retrieve_inference(self, store):
        i = Inference(content="An inference", source_id="s1")
        await store.store_node(i)
        got = await store.get_node(i.id)
        assert isinstance(got, Inference)

    async def test_get_missing_returns_none(self, store):
        got = await store.get_node("nonexistent")
        assert got is None

    async def test_query_by_type(self, store):
        t = Topic(content="topic", source_id="s1")
        f = Fact(content="fact", source_id="s1")
        await store.store_node(t)
        await store.store_node(f)
        topics = await store.query_nodes(node_type=NodeType.TOPIC)
        facts = await store.query_nodes(node_type=NodeType.FACT)
        assert len(topics) == 1
        assert len(facts) == 1

    async def test_query_by_status(self, store):
        t = Topic(content="topic", source_id="s1")
        await store.store_node(t)
        await store.update_node_status(t.id, NodeStatus.SUPERSEDED)
        active = await store.query_nodes(status=NodeStatus.ACTIVE)
        superseded = await store.query_nodes(status=NodeStatus.SUPERSEDED)
        assert len(active) == 0
        assert len(superseded) == 1

    async def test_value_signal_preserved(self, store):
        t = Topic(content="topic", source_id="s1")
        t.value.novelty = 0.3
        t.value.confidence = 0.9
        await store.store_node(t)
        got = await store.get_node(t.id)
        assert got.value.novelty == 0.3
        assert got.value.confidence == 0.9


class TestEdgeStorage:

    async def test_store_and_retrieve_edges(self, store):
        e = NodeEdge(src_id="a", dst_id="b", type=EdgeType.SUPPORTS)
        await store.store_edge(e)
        from_edges = await store.get_edges_from("a")
        to_edges = await store.get_edges_to("b")
        assert len(from_edges) == 1
        assert len(to_edges) == 1
        assert from_edges[0].type == EdgeType.SUPPORTS

    async def test_no_edges_returns_empty(self, store):
        assert len(await store.get_edges_from("x")) == 0
        assert len(await store.get_edges_to("x")) == 0

    async def test_delete_edge(self, store):
        e = NodeEdge(src_id="a", dst_id="b", type=EdgeType.SUPPORTS)
        await store.store_edge(e)
        assert len(await store.get_edges_from("a")) == 1
        await store.delete_edge(e.id)
        assert len(await store.get_edges_from("a")) == 0
        assert len(await store.get_edges_to("b")) == 0

    async def test_delete_missing_edge_is_noop(self, store):
        await store.delete_edge("nonexistent")  # must not raise


class TestCounts:

    async def test_count_nodes_empty(self, store):
        counts = await store.count_nodes_by_type()
        assert counts == {NodeType.TOPIC: 0, NodeType.FACT: 0, NodeType.INFERENCE: 0}

    async def test_count_nodes_by_type(self, store):
        await store.store_node(Topic(content="t", source_id="s1"))
        await store.store_node(Fact(content="f1", source_id="s1"))
        await store.store_node(Fact(content="f2", source_id="s1"))
        await store.store_node(Inference(content="i", source_id="s1"))
        counts = await store.count_nodes_by_type()
        assert counts[NodeType.TOPIC] == 1
        assert counts[NodeType.FACT] == 2
        assert counts[NodeType.INFERENCE] == 1

    async def test_count_nodes_respects_status(self, store):
        topic = Topic(content="t", source_id="s1")
        await store.store_node(topic)
        await store.update_node_status(topic.id, NodeStatus.SUPERSEDED)
        active = await store.count_nodes_by_type(status=NodeStatus.ACTIVE)
        superseded = await store.count_nodes_by_type(status=NodeStatus.SUPERSEDED)
        assert active[NodeType.TOPIC] == 0
        assert superseded[NodeType.TOPIC] == 1

    async def test_count_edges_by_type(self, store):
        await store.store_edge(NodeEdge(src_id="a", dst_id="b", type=EdgeType.SUPPORTS))
        await store.store_edge(NodeEdge(src_id="c", dst_id="b", type=EdgeType.SUPPORTS))
        await store.store_edge(
            NodeEdge(src_id="d", dst_id="a", type=EdgeType.DERIVED_FROM)
        )
        counts = await store.count_edges_by_type()
        assert counts[EdgeType.SUPPORTS] == 2
        assert counts[EdgeType.DERIVED_FROM] == 1
        assert counts[EdgeType.ABOUT] == 0


class TestEmbeddingStorage:

    async def test_store_and_retrieve(self, store):
        emb = EmbeddingRecord(item_id="n1", model_id="model-a", vector=[0.1, 0.2])
        await store.store_embedding(emb)
        results = await store.get_embeddings_for_item("n1")
        assert len(results) == 1
        assert results[0].model_id == "model-a"

    async def test_filter_by_model(self, store):
        emb1 = EmbeddingRecord(item_id="n1", model_id="model-a", vector=[0.1, 0.2])
        emb2 = EmbeddingRecord(item_id="n1", model_id="model-b", vector=[0.3, 0.4])
        await store.store_embedding(emb1)
        await store.store_embedding(emb2)
        results = await store.get_embeddings_for_item("n1", model_id="model-a")
        assert len(results) == 1

    async def test_vector_search(self, store):
        t = Topic(content="ML", source_id="s1")
        await store.store_node(t)
        emb = EmbeddingRecord(item_id=t.id, model_id="test", vector=[1.0, 0.0, 0.0])
        await store.store_embedding(emb)

        results = await store.vector_search([0.9, 0.1, 0.0], "test", k=5)
        assert len(results) == 1
        assert results[0][0] == t.id
        assert results[0][1] > 0.9

    # Excluding inactive nodes is protocol-level and lives in
    # test_storage_parity.py. What stays here is what only this backend can get
    # wrong: over-fetching enough rows that the filter has candidates to keep.


class TestAtomicOperations:
    """Backend-native atomic supersede/merge via single-query transactions."""

    async def test_supersede_tx_migrates_embeds_and_supersedes(self, store):
        old = Topic(content="old topic", source_id="s1")
        fact = Fact(content="supporting fact", source_id="s1")
        await store.store_node(old)
        await store.store_node(fact)
        await store.store_edge(
            NodeEdge(src_id=fact.id, dst_id=old.id, type=EdgeType.SUPPORTS)
        )

        new = Topic(content="new topic", source_id="s1")
        new_emb = EmbeddingRecord(item_id=new.id, model_id="m", vector=[0.1, 0.2, 0.3])
        lineage = NodeEdge(src_id=old.id, dst_id=new.id, type=EdgeType.SUPERSEDED_BY)
        await store.supersede_node_tx(
            old, new, new_emb, lineage, superseded_at=datetime.now(timezone.utc)
        )

        assert (await store.get_node(old.id)).status == NodeStatus.SUPERSEDED
        assert (await store.get_node(new.id)).content == "new topic"
        assert len(await store.get_embeddings_for_item(new.id)) == 1
        into_new = await store.get_edges_to(new.id, edge_type=EdgeType.SUPPORTS)
        assert len(into_new) == 1 and into_new[0].src_id == fact.id
        assert len(await store.get_edges_to(old.id, edge_type=EdgeType.SUPPORTS)) == 0
        lin = await store.get_edges_from(old.id, edge_type=EdgeType.SUPERSEDED_BY)
        assert len(lin) == 1 and lin[0].dst_id == new.id

    async def test_supersede_tx_rolls_back_on_failure(self, store):
        old = Topic(content="old", source_id="s1")
        await store.store_node(old)

        new = Topic(content="new", source_id="s1")
        # Squat the new node's uid so the in-transaction INSERT collides and
        # aborts the whole transaction.
        await store.store_node(Topic(id=new.id, content="squatter", source_id="s1"))

        new_emb = EmbeddingRecord(item_id=new.id, model_id="m", vector=[0.1, 0.2])
        lineage = NodeEdge(src_id=old.id, dst_id=new.id, type=EdgeType.SUPERSEDED_BY)
        with pytest.raises(Exception):
            await store.supersede_node_tx(
                old, new, new_emb, lineage, superseded_at=datetime.now(timezone.utc)
            )

        # Rolled back: the old node was never marked superseded.
        assert (await store.get_node(old.id)).status == NodeStatus.ACTIVE
        assert len(await store.get_edges_from(old.id, edge_type=EdgeType.SUPERSEDED_BY)) == 0

    async def test_write_batch_tx_inserts_all(self, store):
        t = Topic(content="t", source_id="s1")
        f = Fact(content="f", source_id="s1")
        edge = NodeEdge(src_id=f.id, dst_id=t.id, type=EdgeType.SUPPORTS)
        emb = EmbeddingRecord(item_id=t.id, model_id="m", vector=[1.0, 0.0])

        await store.write_batch_tx(nodes=[t, f], edges=[edge], embeddings=[emb])

        assert (await store.get_node(t.id)).content == "t"
        assert (await store.get_node(f.id)).content == "f"
        assert len(await store.get_edges_to(t.id, edge_type=EdgeType.SUPPORTS)) == 1
        assert len(await store.get_embeddings_for_item(t.id)) == 1

    async def test_write_batch_tx_rolls_back_on_failure(self, store):
        fresh = Topic(content="fresh", source_id="s1")
        collide = Topic(content="collide", source_id="s1")
        # Squat the colliding node's uid so its insert aborts the whole batch.
        await store.store_node(Topic(id=collide.id, content="squatter", source_id="s1"))

        with pytest.raises(Exception):
            await store.write_batch_tx(nodes=[fresh, collide])

        # The fresh node never landed.
        assert await store.get_node(fresh.id) is None

    async def test_supersede_by_existing_tx_flags_and_clears(self, store):
        fact = Fact(content="old", source_id="s1")
        existing = Fact(content="new", source_id="s1")
        inf = Inference(content="inference", source_id="s1")
        for node in (fact, existing, inf):
            await store.store_node(node)
        await store.store_edge(
            NodeEdge(src_id=inf.id, dst_id=fact.id, type=EdgeType.DERIVED_FROM)
        )
        candidate = NodeEdge(
            src_id=existing.id, dst_id=fact.id, type=EdgeType.SUPERSESSION_CANDIDATE
        )
        await store.store_edge(candidate)

        lineage = NodeEdge(
            src_id=fact.id, dst_id=existing.id, type=EdgeType.SUPERSEDED_BY
        )
        evidence = [
            NodeEdge(src_id=fact.id, dst_id=inf.id, type=EdgeType.EVIDENCE_SUPERSEDED)
        ]
        await store.supersede_by_existing_tx(
            fact, existing.id, lineage,
            superseded_at=datetime.now(timezone.utc),
            evidence_edges=evidence,
            clear_edge_ids=[candidate.id],
        )

        assert (await store.get_node(fact.id)).status == NodeStatus.SUPERSEDED
        assert (await store.get_node(existing.id)).status == NodeStatus.ACTIVE
        assert len(
            await store.get_edges_from(fact.id, edge_type=EdgeType.SUPERSEDED_BY)
        ) == 1
        assert len(
            await store.get_edges_to(inf.id, edge_type=EdgeType.EVIDENCE_SUPERSEDED)
        ) == 1
        assert await store.get_edges_to(
            fact.id, edge_type=EdgeType.SUPERSESSION_CANDIDATE
        ) == []

    async def test_merge_tx_migrates_dedupes_and_drops_self_loops(self, store):
        a = Topic(content="a", source_id="s1")
        b = Topic(content="b", source_id="s1")
        fact = Fact(content="shared evidence", source_id="s1")
        for node in (a, b, fact):
            await store.store_node(node)
        await store.store_edge(
            NodeEdge(src_id=fact.id, dst_id=a.id, type=EdgeType.SUPPORTS)
        )
        await store.store_edge(
            NodeEdge(src_id=fact.id, dst_id=b.id, type=EdgeType.SUPPORTS)
        )
        # Edge between the sources → becomes a self-loop on merge, must be dropped.
        await store.store_edge(
            NodeEdge(src_id=a.id, dst_id=b.id, type=EdgeType.SUPPORTS)
        )

        merged = Topic(content="merged", source_id="s1")
        merged_emb = EmbeddingRecord(item_id=merged.id, model_id="m", vector=[0.1, 0.2])
        lineage = [
            NodeEdge(src_id=a.id, dst_id=merged.id, type=EdgeType.MERGED_INTO),
            NodeEdge(src_id=b.id, dst_id=merged.id, type=EdgeType.MERGED_INTO),
        ]
        await store.merge_nodes_tx(
            [a, b], merged, merged_emb, lineage, merged_at=datetime.now(timezone.utc)
        )

        # The two shared supports edges collapse to one; the self-loop is gone.
        into_merged = await store.get_edges_to(merged.id, edge_type=EdgeType.SUPPORTS)
        assert len(into_merged) == 1 and into_merged[0].src_id == fact.id
        self_loops = [
            e for e in await store.get_edges_from(merged.id)
            if e.dst_id == merged.id
        ]
        assert self_loops == []
        assert (await store.get_node(a.id)).status == NodeStatus.MERGED
        assert (await store.get_node(b.id)).status == NodeStatus.MERGED
        assert len(await store.get_embeddings_for_item(merged.id)) == 1
        assert len(await store.get_edges_to(merged.id, edge_type=EdgeType.MERGED_INTO)) == 2


class TestQueryChanges:
    # Fixed half-open window [W_START, W_END) with deterministic timestamps.
    W_START = datetime(2026, 6, 10, tzinfo=timezone.utc)
    W_END = datetime(2026, 6, 20, tzinfo=timezone.utc)

    async def test_returns_node_born_in_window(self, store):
        f = Fact(
            content="born inside", source_id="s1",
            created_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )
        await store.store_node(f)
        changed = await store.query_changes(start=self.W_START, end=self.W_END)
        assert [n.id for n in changed] == [f.id]

    async def test_returns_node_retired_in_window(self, store):
        # Born before the window, retired inside it.
        t = Topic(
            content="retired inside", source_id="s1",
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            status=NodeStatus.SUPERSEDED,
            superseded_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )
        await store.store_node(t)
        changed = await store.query_changes(start=self.W_START, end=self.W_END)
        assert [n.id for n in changed] == [t.id]

    async def test_excludes_node_fully_outside(self, store):
        before = Topic(
            content="before", source_id="s1",
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        after = Inference(
            content="after", source_id="s1",
            created_at=datetime(2026, 6, 25, tzinfo=timezone.utc),
        )
        await store.store_node(before)
        await store.store_node(after)
        changed = await store.query_changes(start=self.W_START, end=self.W_END)
        assert changed == []

    async def test_respects_node_type_filter(self, store):
        f = Fact(
            content="fact in", source_id="s1",
            created_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )
        t = Topic(
            content="topic in", source_id="s1",
            created_at=datetime(2026, 6, 16, tzinfo=timezone.utc),
        )
        await store.store_node(f)
        await store.store_node(t)
        facts = await store.query_changes(
            start=self.W_START, end=self.W_END, node_type=NodeType.FACT
        )
        assert [n.id for n in facts] == [f.id]

    async def test_half_open_boundaries(self, store):
        # start is inclusive, end is exclusive.
        at_start = Fact(content="at start", source_id="s1", created_at=self.W_START)
        at_end = Fact(content="at end", source_id="s1", created_at=self.W_END)
        await store.store_node(at_start)
        await store.store_node(at_end)
        changed = await store.query_changes(start=self.W_START, end=self.W_END)
        assert [n.id for n in changed] == [at_start.id]


class TestSourceTopicAndRelationHelpers:

    async def test_get_node_by_content(self, store):
        t = Topic(content="BBC")
        await store.store_node(t)
        got = await store.get_node_by_content("BBC", node_type=NodeType.TOPIC)
        assert got is not None and got.id == t.id
        assert await store.get_node_by_content("missing") is None

    async def test_relabel_edges(self, store):
        a, b = Topic(content="a"), Topic(content="b")
        await store.store_node(a)
        await store.store_node(b)
        await store.store_edge(NodeEdge(
            src_id=a.id, dst_id=b.id, type=EdgeType.RELATED,
            label="written_by", kind="attribution",
        ))
        assert await store.relabel_edges("written_by", "authored_by") == 1
        assert (await store.get_edges_from(a.id))[0].label == "authored_by"

    async def test_get_relation_kind(self, store):
        a, b = Topic(content="a"), Topic(content="b")
        await store.store_node(a)
        await store.store_node(b)
        await store.store_edge(NodeEdge(
            src_id=a.id, dst_id=b.id, type=EdgeType.RELATED,
            label="funded_by", kind="attribution",
        ))
        assert await store.get_relation_kind("funded_by") == "attribution"
        assert await store.get_relation_kind("unknown") is None


class TestGraphNameInjection:
    """Graph names reach ``REMOVE DATABASE IF EXISTS `{name}` `` by f-string
    interpolation, and `use_graph`/`delete_graph` are agent-facing tools taking
    arbitrary strings.

    The exploit takes two steps, because `delete_graph` returns `not_found` for
    a name it cannot see: create a graph whose name breaks out of the backtick
    quoting, then delete it. The injected statement then runs.
    """

    async def test_hostile_graph_names_rejected(self, store):
        hostile = "pwn`; REMOVE DATABASE `victim"
        with pytest.raises(ValueError):
            await store.delete_database(hostile)
        with pytest.raises(ValueError):
            await store.switch_database(hostile)
        with pytest.raises(ValueError):
            await store.switch_database("a;b")

    async def test_bystander_database_survives_injection_attempt(self, store):
        await store.switch_database("victim")
        await store.switch_database("default")
        assert "victim" in await store.list_databases()

        hostile = "pwn`; REMOVE DATABASE `victim"
        with pytest.raises(ValueError):
            await store.switch_database(hostile)
        with pytest.raises(ValueError):
            await store.delete_database(hostile)

        remaining = await store.list_databases()
        assert "victim" in remaining
        assert hostile not in remaining


class TestVectorSearchOverFetch:
    """Ranking happens before the status filter, so enough rows must be ranked.

    The exclusion invariant itself is protocol-level and lives in
    `test_storage_parity.py`. What is specific to this backend is *how* it gets
    there: a status filter written into the ranking query costs embeddings ×
    nodes, because SurrealDB re-runs the subquery per row, so this adapter ranks
    first and filters a small candidate set afterwards. That trade only holds if
    the over-fetch reaches deep enough to still find `k` survivors, and if it
    escalates when it does not.
    """

    async def _corpus(self, store, *, live: int, retired: int, model_id="test"):
        """Retired nodes that all out-score every live one.

        The adversarial ordering is the point: any retired node that ranks below
        a live one is harmless, so only this arrangement can starve the filter.
        """
        made = {"live": [], "retired": []}
        for i in range(retired):
            fact = Fact(content=f"retired {i}", source_id="s1")
            await store.store_node(fact)
            await store.store_embedding(
                EmbeddingRecord(
                    item_id=fact.id, model_id=model_id, vector=[1.0, 0.001 * i, 0.0]
                )
            )
            await store.update_node_status(fact.id, NodeStatus.SUPERSEDED)
            made["retired"].append(fact)
        for i in range(live):
            fact = Fact(content=f"live {i}", source_id="s1")
            await store.store_node(fact)
            await store.store_embedding(
                EmbeddingRecord(
                    item_id=fact.id,
                    model_id=model_id,
                    vector=[0.5, 0.85 - 0.001 * i, 0.0],
                )
            )
            made["live"].append(fact)
        return made

    async def test_a_few_retired_nodes_do_not_reach_for_the_exact_query(
        self, store, monkeypatch
    ):
        """Over-fetching has to reach *past* `k`, not just to it.

        Three retired nodes sit above the live ones, so ranking exactly `k` rows
        comes back short and the exact query gets used on an ordinary graph —
        correct, but it is the quadratic-ish path this whole design exists to
        avoid, and nothing about the returned results would show it.
        """
        from epimemer.storage import surrealdb_adapter

        await self._corpus(store, live=10, retired=3)

        async def refuse(*args, **kwargs):
            raise AssertionError(
                "the exact query is the fallback, not the path for a healthy graph"
            )

        monkeypatch.setattr(surrealdb_adapter, "_ranked_active_items", refuse)

        results = await store.vector_search([1.0, 0.0, 0.0], "test", k=5)

        assert len(results) == 5

    async def test_escalation_is_tried_before_the_exact_query(
        self, store, monkeypatch
    ):
        """Reaching further is cheap; the exact query is not. Try it first."""
        from epimemer.storage import surrealdb_adapter

        made = await self._corpus(store, live=5, retired=25)

        async def refuse(*args, **kwargs):
            raise AssertionError("escalation should have filled k without falling back")

        monkeypatch.setattr(surrealdb_adapter, "_ranked_active_items", refuse)

        results = await store.vector_search([1.0, 0.0, 0.0], "test", k=5)

        assert sorted(i for i, _ in results) == sorted(n.id for n in made["live"])

    async def test_returns_k_when_most_top_hits_are_retired(self, store):
        """More than two thirds retired: the first over-fetch cannot fill `k`."""
        made = await self._corpus(store, live=5, retired=25)

        results = await store.vector_search([1.0, 0.0, 0.0], "test", k=5)

        assert len(results) == 5
        assert set(i for i, _ in results) <= {n.id for n in made["live"]}

    async def test_returns_k_when_the_escalation_also_falls_short(self, store):
        """Past every over-fetch factor, the exact query has to take over."""
        made = await self._corpus(store, live=3, retired=150)

        results = await store.vector_search([1.0, 0.0, 0.0], "test", k=3)

        assert sorted(i for i, _ in results) == sorted(n.id for n in made["live"])

    async def test_returns_what_exists_when_fewer_than_k_are_active(
        self, store, monkeypatch
    ):
        """Asking for more than the graph holds returns everything it holds —
        without reaching again for rows that provably are not there.

        The scan came back short of its own limit, which means it hit the end of
        the embeddings. Escalating or falling back cannot find an eleventh node
        in a graph of ten, so doing either is wasted work on every small graph.
        """
        from epimemer.storage import surrealdb_adapter

        made = await self._corpus(store, live=2, retired=8)

        async def refuse(*args, **kwargs):
            raise AssertionError("the scan already reached the end of the embeddings")

        monkeypatch.setattr(surrealdb_adapter, "_ranked_active_items", refuse)

        results = await store.vector_search([1.0, 0.0, 0.0], "test", k=10)

        assert sorted(i for i, _ in results) == sorted(n.id for n in made["live"])

    async def test_an_all_retired_graph_returns_nothing(self, store):
        await self._corpus(store, live=0, retired=12)

        assert await store.vector_search([1.0, 0.0, 0.0], "test", k=5) == []

    async def test_top_k_matches_a_brute_force_reference(self, store):
        """Over-fetching must not change *which* nodes come back, or in what
        order — it is a way of reaching the same answer, not a different one."""
        made = await self._corpus(store, live=12, retired=9)
        query = [0.7, 0.7, 0.0]

        results = await store.vector_search(query, "test", k=5)

        def cosine(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            na = sum(x * x for x in a) ** 0.5
            nb = sum(x * x for x in b) ** 0.5
            return dot / (na * nb)

        scored = []
        for fact in made["live"]:
            vector = (await store.get_embeddings_for_item(fact.id))[0].vector
            scored.append((fact.id, cosine(query, vector)))
        expected = sorted(scored, key=lambda pair: -pair[1])[:5]

        assert [i for i, _ in results] == [i for i, _ in expected]
        for (_, got), (_, want) in zip(results, expected):
            assert got == pytest.approx(want, rel=1e-6)

    async def test_the_typed_path_over_fetches_too(self, store):
        """A typed search reaches past embeddings of other node types, which the
        unfiltered scan cannot exclude up front."""
        for i in range(20):
            fact = Fact(content=f"fact {i}", source_id="s1")
            await store.store_node(fact)
            await store.store_embedding(
                EmbeddingRecord(
                    item_id=fact.id, model_id="test", vector=[1.0, 0.001 * i, 0.0]
                )
            )
        topic = Topic(content="the one topic", source_id="s1")
        await store.store_node(topic)
        await store.store_embedding(
            EmbeddingRecord(item_id=topic.id, model_id="test", vector=[0.5, 0.85, 0.0])
        )

        results = await store.vector_search(
            [1.0, 0.0, 0.0], "test", k=1, node_type=NodeType.TOPIC
        )

        assert [i for i, _ in results] == [topic.id]
