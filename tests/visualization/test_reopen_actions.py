"""A reopen reaches the live log as one act, carrying the judge who made it.

Withdrawing a suppression is a judgment, so it belongs in the log beside the
verdicts it takes back. None of the existing transaction boundaries fitted:
the three layers write a retired edge, a verdict row and a journal row in
various combinations, and `write_timeline_tx` takes a timeline. So `reopen_tx`
is a boundary of its own, and this is where the act it publishes is pinned.

The verb set stays closed (`EVENT_LOG.md` §11): the act carries
`DecisionKind.REOPENED`, which is the name the journal already records it
under, so the live log and the durable history speak one vocabulary.

Both backends, through the `storage` fixture.
"""

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    ClaimKind,
    DecisionKind,
    EdgeType,
    Fact,
    JudgeRef,
    NodeEdge,
)
from epimemer.mcp import tools
from epimemer.pipelines.reflection.similarity_decisions import apply_similarity_decision
from epimemer.visualization.event_bus import create_event_bus
from epimemer.visualization.events import ActionVerb, EdgeStored, GraphActionRecorded
from epimemer.visualization.instrumented_storage import instrument_storage

EDITOR = JudgeRef(agent_id="editor", digest="d2")


@pytest.fixture
def bus():
    return create_event_bus()


@pytest.fixture
def acts(bus) -> list[GraphActionRecorded]:
    recorded: list[GraphActionRecorded] = []
    bus.subscribe(GraphActionRecorded, handler=lambda e: recorded.append(e))
    return recorded


@pytest.fixture
def watched(storage, bus):
    """The `storage` fixture's backend, instrumented. Runs on both backends."""
    return instrument_storage(storage, bus)


async def _fact(watched, content: str) -> Fact:
    fact = Fact(content=content, source_id="seg-1", claim_kind=ClaimKind.STATE)
    await watched.store_node(fact)
    await watched.store_edge(
        NodeEdge(src_id=fact.id, dst_id=BASE_METACONTEXT_ID, type=EdgeType.HAS_METACONTEXT)
    )
    return fact


async def _declined_pair(watched) -> tuple[Fact, Fact]:
    a = await _fact(watched, "The bridge opened in 1932.")
    b = await _fact(watched, "The bridge was opened in 1932.")
    await apply_similarity_decision(
        watched,
        a_id=a.id,
        b_id=b.id,
        verdict="distinct",
        because="Two bridges, one date.",
        judge=JudgeRef(agent_id="critic", digest="d1"),
    )
    return a, b


async def test_the_act_names_the_decision_and_the_judge(watched, acts):
    a, b = await _declined_pair(watched)
    acts.clear()

    await tools.reopen(watched, node_ids=[a.id, b.id], reason="Look again.", judge=EDITOR)

    assert len(acts) == 1
    assert acts[0].verb is DecisionKind.REOPENED
    assert sorted(acts[0].subjects) == sorted([a.id, b.id])
    assert acts[0].judged_by == "editor"


async def test_the_line_reads_as_a_reopening(watched, acts):
    a, b = await _declined_pair(watched)
    acts.clear()

    await tools.reopen(watched, node_ids=[a.id, b.id], reason="Look again.", judge=EDITOR)

    assert acts[0].summary.startswith("reopened")


async def test_the_retired_edge_reaches_the_viewer(watched, bus):
    edges: list[EdgeStored] = []
    bus.subscribe(EdgeStored, handler=lambda e: edges.append(e))
    a, b = await _declined_pair(watched)
    edges.clear()

    await tools.reopen(watched, node_ids=[a.id, b.id], reason="Look again.", judge=EDITOR)

    assert len(edges) == 1, "a viewer showing the live edge would keep showing a retired one"


async def test_a_refused_reopen_announces_nothing(watched, acts):
    a = await _fact(watched, "The bridge opened in 1932.")
    b = await _fact(watched, "The bridge was opened in 1932.")
    acts.clear()

    await tools.reopen(watched, node_ids=[a.id, b.id], reason="Nothing to undo.", judge=EDITOR)

    assert acts == []


def test_no_reopen_verb_was_added_to_the_closed_set():
    """The verb set stays closed: the act carries its `DecisionKind` instead."""
    assert "reopened" not in {verb.value for verb in ActionVerb}
