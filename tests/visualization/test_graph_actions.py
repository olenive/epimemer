"""The coarse per-transaction event the log reads (EVENT_LOG.md §3.1).

The fine-grained events keep flowing untouched — the graph panel needs them.
What is new here is one event per *act*, emitted where the act happens, so a
reader does not have to reconstruct "superseded 123 → 124, +3 evidence edges"
by grouping a stream that has no correlation id in it.
"""

from datetime import datetime, timezone

import pytest

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    Fact,
    NodeEdge,
    NodeStatus,
    Topic,
)
from epimemer.storage.memory import InMemoryStorage
from epimemer.visualization.event_bus import create_event_bus
from epimemer.visualization.events import ActionVerb, Event, GraphActionRecorded
from epimemer.visualization.graph_actions import summarise, verb_for_status
from epimemer.visualization.instrumented_storage import instrument_storage


@pytest.fixture
def bus():
    return create_event_bus()


def _recorder(bus) -> tuple[list[GraphActionRecorded], list[Event]]:
    actions: list[GraphActionRecorded] = []
    everything: list[Event] = []
    bus.subscribe(GraphActionRecorded, handler=lambda e: actions.append(e))
    bus.subscribe(handler=lambda e: everything.append(e))
    return actions, everything


async def _supersede(bus, *, status: NodeStatus, evidence: int = 0):
    wrapped = instrument_storage(InMemoryStorage(), bus)
    old = Fact(content="Leningrad is the city's name", source_id="s1")
    new = Fact(content="Saint Petersburg is the city's name", source_id="s1")
    await wrapped.store_node(old)
    actions, everything = _recorder(bus)
    await wrapped.supersede_node_tx(
        old, new,
        EmbeddingRecord(item_id=new.id, model_id="test", vector=[1.0, 0.0]),
        NodeEdge(src_id=old.id, dst_id=new.id, type=EdgeType.SUPERSEDED_BY),
        status=status,
        superseded_at=datetime.now(timezone.utc),
        evidence_edges=[
            NodeEdge(src_id=new.id, dst_id=old.id, type=EdgeType.SUPPORTS)
            for _ in range(evidence)
        ],
    )
    return old, new, actions, everything


class TestOneActionPerTransaction:

    async def test_supersede_publishes_one_action_for_four_events(self, bus):
        """§3.1: the coarse event is emitted once per transaction, not once per
        write. `supersede_node_tx` publishes four fine-grained events and one
        act — and it is the act a person reads."""
        old, new, actions, everything = await _supersede(
            bus, status=NodeStatus.CORRECTED, evidence=1
        )

        assert len(everything) >= 4
        assert len(actions) == 1
        assert actions[0].subjects == [old.id, new.id]

    async def test_a_batch_write_is_one_action_whatever_it_holds(self, bus):
        """Same rule from the other end: 25 nodes and their edges are one act."""
        wrapped = instrument_storage(InMemoryStorage(), bus)
        actions, everything = _recorder(bus)
        nodes = [Fact(content=f"claim {i}", source_id="s1") for i in range(25)]

        await wrapped.write_batch_tx(nodes=nodes)

        assert len(everything) > 25
        assert len(actions) == 1
        assert actions[0].verb is ActionVerb.STORED
        assert actions[0].counts["nodes"] == 25


class TestVocabulary:
    """§3.1 revised: the verbs are the status-split ones, so the live log and
    the durable history (`events_in_window`) speak one vocabulary."""

    async def test_a_correction_reads_as_corrected(self, bus):
        _, _, actions, _ = await _supersede(bus, status=NodeStatus.CORRECTED)
        assert actions[0].verb is ActionVerb.CORRECTED

    async def test_a_world_change_reads_as_world_changed(self, bus):
        _, _, actions, _ = await _supersede(bus, status=NodeStatus.HISTORICAL)
        assert actions[0].verb is ActionVerb.WORLD_CHANGED

    def test_there_is_no_superseded_verb(self):
        """The one thing §3.1 rules out by name: "superseded 123 → 124" flattens
        the distinction #53 exists to record. The legacy `SUPERSEDED` status —
        rows that genuinely do not say which act they were — reads as the
        unclassified `undetermined`, not as a guess at one of the two."""
        assert "superseded" not in {verb.value for verb in ActionVerb}

    def test_the_legacy_status_reads_as_undetermined(self):
        assert verb_for_status(NodeStatus.SUPERSEDED) is ActionVerb.UNDETERMINED

    def test_an_unrecognised_status_does_not_claim_the_node_was_retired(self):
        """§11.1 amended (2026-08-19). The fall-through used to answer `retired`,
        on the reasoning that "it left the active set" is the only part that can
        be relied on. That is an assumption, not a fact: `ACTIVE → restored` is
        already a non-retirement flowing through the same transaction, so a
        status added later need not be a retirement either. The default names
        the absence of a determination instead of inventing one."""
        assert verb_for_status("provisional") is ActionVerb.UNDETERMINED
        assert "retired" not in {verb.value for verb in ActionVerb}

    def test_an_undetermined_act_still_reads_as_a_line(self):
        """`undetermined 1 node` is not English. The verb names a state, so the
        summary has to say what happened to the node in it."""
        line = summarise(ActionVerb.UNDETERMINED, ["abcdef1234"], {})

        assert line == "status undetermined: 1 node"

    async def test_recurrence_is_restored_plus_counts_not_a_new_verb(self, bus):
        """#53 T2's `recurs` verdict resolves as restore + a new source edge.
        Recorded as `restored` with the edge in `counts`, so nobody mints a
        `recurs` verb later and splits the vocabulary again."""
        wrapped = instrument_storage(InMemoryStorage(), bus)
        node = Fact(content="Labour is in government", source_id="s1")
        await wrapped.store_node(node)
        await wrapped.set_node_status_tx(
            [node], status=NodeStatus.HISTORICAL, at=datetime.now(timezone.utc)
        )
        actions, _ = _recorder(bus)

        await wrapped.set_node_status_tx(
            [node], status=NodeStatus.ACTIVE, at=datetime.now(timezone.utc)
        )
        await wrapped.write_batch_tx(
            edges=[NodeEdge(src_id=node.id, dst_id="doc1", type=EdgeType.SOURCED_FROM)]
        )

        assert [a.verb for a in actions] == [ActionVerb.RESTORED, ActionVerb.STORED]
        assert actions[1].counts["edges"] == 1
        assert "recurs" not in {verb.value for verb in ActionVerb}


class TestSummary:
    """§3.1: `summary` is pre-rendered on the emitting side deliberately — a
    line the frontend assembles from parts is a second place where the system's
    vocabulary gets decided, and it drifts from the tool responses."""

    async def test_a_correction_renders_both_ids(self, bus):
        old, new, actions, _ = await _supersede(bus, status=NodeStatus.CORRECTED)
        summary = actions[0].summary

        assert summary.startswith("corrected ")
        assert old.id[:8] in summary and new.id[:8] in summary

    async def test_a_world_change_does_not_read_as_a_correction(self, bus):
        _, _, actions, _ = await _supersede(bus, status=NodeStatus.HISTORICAL)
        assert "corrected" not in actions[0].summary

    async def test_the_evidence_it_swept_up_is_in_the_line(self, bus):
        _, _, actions, _ = await _supersede(
            bus, status=NodeStatus.CORRECTED, evidence=3
        )
        assert "4 edges" in actions[0].summary

    async def test_a_merge_names_where_the_content_went(self, bus):
        wrapped = instrument_storage(InMemoryStorage(), bus)
        sources = [Fact(content=f"duplicate {i}", source_id="s1") for i in range(2)]
        for node in sources:
            await wrapped.store_node(node)
        merged = Topic(content="the one kept", source_id="s1")
        actions, _ = _recorder(bus)

        await wrapped.merge_nodes_tx(
            sources, merged,
            EmbeddingRecord(item_id=merged.id, model_id="test", vector=[1.0, 0.0]),
            [NodeEdge(src_id=s.id, dst_id=merged.id, type=EdgeType.MERGED_INTO)
             for s in sources],
            merged_at=datetime.now(timezone.utc),
        )

        assert actions[0].verb is ActionVerb.MERGED
        assert actions[0].subjects[0] == merged.id
        assert merged.id[:8] in actions[0].summary


class TestActionIds:

    async def test_action_ids_rise_within_a_session(self, bus):
        """§4.1: the id is assigned by the process that emits the act, so it is
        a position in a stream. The hub's `seq` is neither — it restarts at 0
        per browser socket."""
        wrapped = instrument_storage(InMemoryStorage(), bus)
        actions, _ = _recorder(bus)

        for i in range(3):
            await wrapped.write_batch_tx(
                nodes=[Fact(content=f"claim {i}", source_id="s1")]
            )

        ids = [a.action_id for a in actions]
        assert ids == sorted(ids)
        assert len(set(ids)) == 3
