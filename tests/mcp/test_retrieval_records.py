"""The record of what a tool handed the agent (RETRIEVAL_PROVENANCE.md §3).

The question the feature answers is not "what is in the graph" but *"what did
the agent see, and what did it miss?"* — so the record has to be what **we**
returned, capped, with the ids named rather than guessed at.
"""

import pytest

from epimemer.mcp.retrieval_records import (
    RESPONSE_TEXT_CAP,
    RETRIEVED_CAP,
    RetrievalRecord,
    RetrievedNode,
    remember_record,
    structural_only,
)
from epimemer.pipelines.query.types import SeedProvenance
from epimemer.visualization.ring import RETRIEVAL_RING_CAPACITY


def _record(record_id: str = "000000000001", **over) -> RetrievalRecord:
    fields = {
        "record_id": record_id,
        "tool": "epimemer.search",
        "query": "deployment rollbacks",
        "graph": "default",
        "retrieved": [RetrievedNode(node_id="n1", provenance=SeedProvenance.VECTOR, score=0.82)],
        "response_text": '{"result": {"nodes": []}}',
    }
    return RetrievalRecord(**{**fields, **over})


class TestProvenance:
    def test_a_tool_that_does_not_rank_says_direct(self):
        """§3: `find_nodes`, `graph_as_of`, `query_changes` and `topic_tree` return
        nodes without ranking them. `LEXICAL_SEARCH.md` §6 left room for this
        fifth value rather than pretending they were vector hits."""
        assert SeedProvenance.DIRECT.value == "direct"

    def test_an_unranked_node_carries_no_score(self):
        """§9: showing a blank is honest; showing 1.0 would be a lie."""
        node = RetrievedNode(node_id="n1", provenance=SeedProvenance.DIRECT)
        assert node.score is None

    def test_check_conflicts_candidates_are_vector_hits(self):
        """§3 amended: they genuinely are vector-similarity results, with the
        cosine as the score — no new enum value is needed for them."""
        node = RetrievedNode(node_id="n1", provenance=SeedProvenance.VECTOR, score=0.91)
        assert node.provenance is SeedProvenance.VECTOR


class TestCaps:
    """§3.2: uncapped, this is a copy of the graph held in memory and served to
    any browser that connects."""

    def test_records_ring_is_bounded_and_caps_response_text(self):
        long_text = "x" * (RESPONSE_TEXT_CAP + 500)

        capped = RetrievalRecord(
            record_id="1",
            tool="epimemer.search",
            query="q",
            graph="default",
            retrieved=[],
            response_text=long_text,
        )

        assert len(capped.response_text) == RESPONSE_TEXT_CAP
        assert capped.truncated is True

        ring: tuple[RetrievalRecord, ...] = ()
        for i in range(RETRIEVAL_RING_CAPACITY + 5):
            ring = remember_record(ring, _record(f"{i:03d}"))

        assert len(ring) == RETRIEVAL_RING_CAPACITY
        assert ring[0].record_id == "005"

    def test_a_short_response_is_not_marked_truncated(self):
        assert _record().truncated is False

    def test_the_cap_covers_retrieved_as_well_as_the_text(self):
        """§3 amended: a `reflect` record can carry hundreds of ids, so the id
        list is as much a sizing problem as the payload is."""
        many = [
            RetrievedNode(node_id=f"n{i}", provenance=SeedProvenance.DIRECT)
            for i in range(RETRIEVED_CAP + 50)
        ]

        record = RetrievalRecord(
            record_id="1",
            tool="epimemer.reflect",
            query="",
            graph="default",
            retrieved=many,
            response_text="{}",
        )

        assert len(record.retrieved) == RETRIEVED_CAP
        assert record.truncated is True


class TestUndeclared:
    """§2.1: `None` means the tool never declared; `[]` means it declared and
    returned nothing. A silently-empty record is the gap this distinction
    closes."""

    def test_an_undeclared_record_is_distinguishable_from_an_empty_one(self):
        undeclared = _record(retrieved=None)
        empty = _record(retrieved=[])

        assert undeclared.declared is False
        assert empty.declared is True
        # Readers that only want the ids get `[]` either way, so the
        # distinction never leaks into code that does not care about it.
        assert undeclared.retrieved_nodes == []


class TestStructuralOnly:
    """§3.2's guard, in its pure form: what a non-loopback hub is allowed to
    hold. The ids and counts stay — the selector and focus mode need them —
    and the query text and payload do not."""

    def test_it_keeps_what_the_selector_and_focus_mode_need(self):
        stripped = structural_only(_record())

        assert stripped.record_id == "000000000001"
        assert stripped.tool == "epimemer.search"
        assert stripped.graph == "default"
        assert [n.node_id for n in stripped.retrieved] == ["n1"]

    def test_it_drops_the_query_and_the_payload(self):
        stripped = structural_only(_record())

        assert stripped.query == ""
        assert stripped.response_text == ""

    def test_an_undeclared_record_stays_undeclared_when_stripped(self):
        assert structural_only(_record(retrieved=None)).declared is False


@pytest.mark.parametrize("cap", [RESPONSE_TEXT_CAP, RETRIEVED_CAP])
def test_the_caps_are_real_numbers(cap):
    assert cap > 0
