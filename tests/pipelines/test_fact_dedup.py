"""Facts that restate one claim collapse into one node — and only those.

The same claim arriving in two documents used to produce two nodes, because no
decomposition path deduplicates facts. That is wanted up to a point: plural
provenance is what makes corroboration mean anything. What was missing is the
action for the `redundant` verdict, which until now either no-opped or tempted
the agent into a supersession whose required `because` has no honest answer
(REVIEW_EPISTEMIC.md §3).

**The error mode is why almost everything here is a refusal.** A false merge
does not lose information, it manufactures corroboration: two distinct claims
fused into one node with two independent sources read as *better supported* than
either was, which inverts the quantity corroboration measures rather than
degrading it. A missed merge only undercounts. So the tests below spend far more
effort pinning what must **not** merge than what must.

The pair that decides the design is the last of those. Under the validity model
two ingests of one claim over separate periods are one node whose intervals
union — right for *"Labour is in government"* in 1997 and 2024, and a fabricated
twenty-seven-year victory for *"Labour won the election"* in the same two years.
The sentences are near-identical, so nothing computed from them can tell the two
apart; the judgment is made at ingest, where the document is still readable, and
recorded on the fact.
"""

import pytest

from epimemer.core.temporal import IntervalBasis, PreciseInstant, ValidityInterval
from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    ClaimKind,
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    LifecycleEpisode,
    Metacontext,
    NodeEdge,
    NodeStatus,
    RawDocument,
    Topic,
    ValueSignal,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig
from epimemer.pipelines.reflection.fact_dedup import merged_confidence_basis
from epimemer.storage.protocol import MergeOverrides
from epimemer.pipelines.reflection.review import (
    SIMILARITY_NOMINATION_THRESHOLD,
    review_labels,
)

from datetime import datetime, timezone


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


# Vectors are supplied rather than derived from the text, because the similarity
# bar and the claim judgment have to be varied independently: a test about what
# `claim_kind` decides must not also be a test of how alike two sentences happen
# to hash. `_TWIN` is what two phrasings of one claim look like to the gate.
_TWIN = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
_STRANGER = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


async def _fact(
    storage,
    embedding_provider,
    content: str,
    *,
    claim_kind: ClaimKind | None = ClaimKind.STATE,
    vector: list[float] | None = None,
    status: NodeStatus = NodeStatus.ACTIVE,
    value: ValueSignal | None = None,
    metadata: dict | None = None,
    lifecycle: list[LifecycleEpisode] | None = None,
) -> Fact:
    """One stored, embedded fact. Defaults are the mergeable case."""
    fact = Fact(
        content=content,
        source_id="seg-1",
        claim_kind=claim_kind,
        status=status,
        value=value or ValueSignal(),
        metadata=metadata or {},
        lifecycle=list(lifecycle or []),
    )
    await storage.store_node(fact)
    await storage.store_embedding(EmbeddingRecord(
        item_id=fact.id,
        model_id=embedding_provider.model_id,
        vector=vector or _TWIN,
    ))
    return fact


async def _sourced_from(storage, fact: Fact, name: str, *, validity=None) -> str:
    """Attach `fact` to a freshly stored document, with the periods it asserts."""
    document = RawDocument(content=f"contents of {name}", source=name)
    await storage.store_document(document)
    await storage.store_edge(NodeEdge(
        src_id=fact.id,
        dst_id=document.id,
        type=EdgeType.SOURCED_FROM,
        validity=list(validity or []),
    ))
    return document.id


async def _inference(storage, content: str) -> Inference:
    """One stored inference. No embedding: nothing here searches for it."""
    inference = Inference(content=content, source_id="seg-1")
    await storage.store_node(inference)
    return inference


async def _framed(storage, fact: Fact, label: str) -> str:
    frame = Metacontext(content=label)
    await storage.store_metacontext(frame)
    await storage.store_edge(NodeEdge(
        src_id=fact.id, dst_id=frame.id, type=EdgeType.HAS_METACONTEXT,
    ))
    return frame.id


def _year(value: int) -> PreciseInstant:
    return PreciseInstant(at=datetime(value, 1, 1, tzinfo=timezone.utc))


def _span(start: int, end: int) -> ValidityInterval:
    return ValidityInterval(
        start=_year(start), end=_year(end), basis=IntervalBasis.STATED
    )


async def _merge(
    storage, embedding_provider, facts, content="One claim.", **kwargs
) -> dict:
    result, _ = await tools.merge_facts(
        source_ids=[fact.id for fact in facts],
        content=content,
        storage=storage,
        embedding_provider=embedding_provider,
        **kwargs,
    )
    return result


def _completed_cycles(count: int) -> list[LifecycleEpisode]:
    """`count` merge/reverse round trips, as the lifecycle records them."""
    return [
        LifecycleEpisode(
            retired_at=datetime(2026, 1, i + 1, tzinfo=timezone.utc),
            because=NodeStatus.MERGED,
            counterpart=f"survivor-{i}",
            restored_at=datetime(2026, 1, i + 2, tzinfo=timezone.utc),
        )
        for i in range(count)
    ]


class TestOneClaimInTwoDocumentsBecomesOneFact:
    async def test_the_survivor_is_active_and_the_sources_are_retired(
        self, storage, embedding_provider
    ):
        first = await _fact(storage, embedding_provider, "The deploy failed.")
        second = await _fact(
            storage, embedding_provider, "The deployment did not succeed."
        )

        result = await _merge(storage, embedding_provider, [first, second])

        assert result["merged"] is True
        survivor = await storage.get_node(result["fact_id"])
        assert survivor.status is NodeStatus.ACTIVE
        assert survivor.content == "One claim."
        for source in (first, second):
            assert (await storage.get_node(source.id)).status is NodeStatus.MERGED

    async def test_each_source_is_linked_to_the_survivor(
        self, storage, embedding_provider
    ):
        first = await _fact(storage, embedding_provider, "The deploy failed.")
        second = await _fact(storage, embedding_provider, "The deploy broke.")

        result = await _merge(storage, embedding_provider, [first, second])

        for source in (first, second):
            edges = await storage.get_edges_from(
                source.id, edge_type=EdgeType.MERGED_INTO
            )
            assert [edge.dst_id for edge in edges] == [result["fact_id"]]

    async def test_provenance_becomes_plural_rather_than_being_overwritten(
        self, storage, embedding_provider
    ):
        """The whole reason the merge is wanted, and the thing a naive rebuild
        loses: the survivor is supported by both documents, not by one."""
        first = await _fact(storage, embedding_provider, "The deploy failed.")
        second = await _fact(storage, embedding_provider, "The deploy broke.")
        documents = {
            await _sourced_from(storage, first, "runbook.md"),
            await _sourced_from(storage, second, "incident-42.md"),
        }

        result = await _merge(storage, embedding_provider, [first, second])

        edges = await storage.get_edges_from(
            result["fact_id"], edge_type=EdgeType.SOURCED_FROM
        )
        assert {edge.dst_id for edge in edges} == documents

    async def test_each_document_keeps_asserting_its_own_periods(
        self, storage, embedding_provider
    ):
        """Validity rides on the provenance edge precisely so a merge does not
        have to combine it — two sources may disagree and both survive."""
        first = await _fact(storage, embedding_provider, "Labour is in government.")
        second = await _fact(storage, embedding_provider, "Labour governs.")
        await _sourced_from(storage, first, "1997.md", validity=[_span(1997, 2010)])
        await _sourced_from(storage, second, "2024.md", validity=[_span(2024, 2025)])

        result = await _merge(storage, embedding_provider, [first, second])

        edges = await storage.get_edges_from(
            result["fact_id"], edge_type=EdgeType.SOURCED_FROM
        )
        assert sorted(
            interval.start.at.year for edge in edges for interval in edge.validity
        ) == [1997, 2024]

    async def test_the_survivor_is_findable_by_vector_search(
        self, storage, embedding_provider
    ):
        """A merged node that was never embedded is invisible, which is a worse
        outcome than the duplication it replaced."""
        first = await _fact(storage, embedding_provider, "The deploy failed.")
        second = await _fact(storage, embedding_provider, "The deploy broke.")

        result = await _merge(storage, embedding_provider, [first, second])

        stored = await storage.get_embeddings_for_item(
            result["fact_id"], model_id=embedding_provider.model_id
        )
        assert stored, "the survivor was not embedded"


class TestAnOccurrenceNeverMerges:
    """The pair the whole design turns on (fact dedup, review 2026-08-12)."""

    async def test_two_elections_stay_two_facts(self, storage, embedding_provider):
        first = await _fact(
            storage,
            embedding_provider,
            "Labour won the election.",
            claim_kind=ClaimKind.EVENT,
        )
        second = await _fact(
            storage,
            embedding_provider,
            "Labour won the election.",
            claim_kind=ClaimKind.EVENT,
        )
        await _sourced_from(storage, first, "1997.md", validity=[_span(1997, 1998)])
        await _sourced_from(storage, second, "2024.md", validity=[_span(2024, 2025)])

        result = await _merge(storage, embedding_provider, [first, second])

        assert result["merged"] is False
        assert "occurrence" in result["refused"]
        for source in (first, second):
            assert (await storage.get_node(source.id)).status is NodeStatus.ACTIVE

    async def test_the_same_two_documents_merge_when_the_claim_is_a_condition(
        self, storage, embedding_provider
    ):
        """The contrast that shows the judgment is doing the work. Same periods,
        same similarity, same frames — only the kind of claim differs, and it is
        the difference between one node with two periods and a fabricated one."""
        first = await _fact(
            storage, embedding_provider, "Labour is in government.",
        )
        second = await _fact(storage, embedding_provider, "Labour governs.")
        await _sourced_from(storage, first, "1997.md", validity=[_span(1997, 2010)])
        await _sourced_from(storage, second, "2024.md", validity=[_span(2024, 2025)])

        result = await _merge(storage, embedding_provider, [first, second])

        assert result["merged"] is True

    async def test_one_occurrence_among_states_is_enough_to_refuse(
        self, storage, embedding_provider
    ):
        states = [
            await _fact(storage, embedding_provider, "Labour is in government."),
            await _fact(storage, embedding_provider, "Labour governs."),
        ]
        event = await _fact(
            storage,
            embedding_provider,
            "Labour took office.",
            claim_kind=ClaimKind.EVENT,
        )

        result = await _merge(storage, embedding_provider, [*states, event])

        assert result["merged"] is False
        assert "occurrence" in result["refused"]


class TestAnUnjudgedFactIsNotMerged:
    """The cost of recording the judgment at ingest, taken deliberately.

    Every fact written before `claim_kind` existed is in this state, and no
    later pass can fix it from the graph alone: the document that would settle
    it is gone by then, and guessing from two stripped sentences is how an
    election becomes a condition. Under-merging is the safe direction.
    """

    async def test_an_absent_claim_kind_refuses(self, storage, embedding_provider):
        first = await _fact(
            storage, embedding_provider, "The deploy failed.", claim_kind=None
        )
        second = await _fact(storage, embedding_provider, "The deploy broke.")

        result = await _merge(storage, embedding_provider, [first, second])

        assert result["merged"] is False
        assert "claim_kind" in result["refused"]

    async def test_unjudged_is_not_read_as_a_kind_of_its_own(
        self, storage, embedding_provider
    ):
        """Two unjudged facts are not "both the same kind, therefore mergeable" —
        nothing knows what either of them is."""
        first = await _fact(
            storage, embedding_provider, "The deploy failed.", claim_kind=None
        )
        second = await _fact(
            storage, embedding_provider, "The deploy broke.", claim_kind=None
        )

        result = await _merge(storage, embedding_provider, [first, second])

        assert result["merged"] is False
        assert "claim_kind" in result["refused"]


class TestFramesAreNeverCrossed:
    """Merging a fiction frame into base reality is the single worst outcome
    available, and the naive similarity pass does exactly that."""

    async def test_one_sentence_in_two_frames_stays_two_facts(
        self, storage, embedding_provider
    ):
        first = await _fact(storage, embedding_provider, "Dragons are extinct.")
        second = await _fact(storage, embedding_provider, "Dragons are extinct.")
        await _framed(storage, first, "Base reality")
        await _framed(storage, second, "The novel")

        result = await _merge(storage, embedding_provider, [first, second])

        assert result["merged"] is False
        assert "record_variant" in result["refused"]

    async def test_a_partial_frame_overlap_is_still_a_refusal(
        self, storage, embedding_provider
    ):
        """`same_frame` asks whether two nodes share *at least one* frame, which
        is the right question for a contradiction and the wrong one here: the
        survivor inherits the union, so it would assert in a frame only one
        source ever stood in."""
        first = await _fact(storage, embedding_provider, "Dragons are extinct.")
        second = await _fact(storage, embedding_provider, "Dragons are extinct.")
        shared = await _framed(storage, first, "Base reality")
        await storage.store_edge(NodeEdge(
            src_id=second.id, dst_id=shared, type=EdgeType.HAS_METACONTEXT,
        ))
        await _framed(storage, second, "The novel")

        result = await _merge(storage, embedding_provider, [first, second])

        assert result["merged"] is False
        assert "same set of frames" in result["refused"]

    async def test_two_untagged_facts_share_the_base_frame_and_merge(
        self, storage, embedding_provider
    ):
        first = await _fact(storage, embedding_provider, "The deploy failed.")
        second = await _fact(storage, embedding_provider, "The deploy broke.")

        result = await _merge(storage, embedding_provider, [first, second])

        assert result["merged"] is True


class TestRetiredFactsAreNotMerged:
    async def test_a_historical_twin_is_a_recurrence_rather_than_a_duplicate(
        self, storage, embedding_provider
    ):
        """Merging here would destroy the history that made the recurrence
        visible. The verdict is `recurs` and the action is `restore`."""
        active = await _fact(storage, embedding_provider, "Labour is in government.")
        retired = await _fact(
            storage,
            embedding_provider,
            "Labour governs.",
            status=NodeStatus.HISTORICAL,
        )

        result = await _merge(storage, embedding_provider, [active, retired])

        assert result["merged"] is False
        assert "restore" in result["refused"]

    async def test_a_corrected_fact_is_not_folded_back_in(
        self, storage, embedding_provider
    ):
        active = await _fact(storage, embedding_provider, "The capital is Berlin.")
        wrong = await _fact(
            storage,
            embedding_provider,
            "The capital is Bonn.",
            status=NodeStatus.CORRECTED,
        )

        result = await _merge(storage, embedding_provider, [active, wrong])

        assert result["merged"] is False
        assert "corrected" in result["refused"]


class TestAFutileMergeCycleIsRefused:
    """Merged, reversed, merged again is an agent burning tokens on an
    oscillation nobody wants (REVIEW_MODE.md §7.8).

    Not expected — but hard to catch after the fact and nearly free to catch
    here, which is the whole case for building it before it happens. The signal
    needs no new storage: every merge appends a `merged` lifecycle episode and
    every reversal closes it with `restored_at`, in a list that is append-only
    and never trimmed.

    **Dormant until reversal exists.** Nothing writes `restored_at` today, so
    the count is zero on every real fact and this gate cannot fire. It is built
    now because the episodes it reads are being written now, and a limit added
    after an oscillation has run has nothing to look at.
    """

    async def test_a_fact_at_the_limit_refuses_the_next_merge(
        self, storage, embedding_provider
    ):
        oscillated = await _fact(
            storage, embedding_provider, "The capital is Bonn.",
            lifecycle=_completed_cycles(2),
        )
        other = await _fact(storage, embedding_provider, "Bonn is the capital.")

        result = await _merge(storage, embedding_provider, [oscillated, other])

        assert result["merged"] is False
        assert "merge_cycle_limit of 2" in result["refused"]
        assert "Ask the user" in result["refused"]

    async def test_one_cycle_is_an_ordinary_correction_and_still_merges(
        self, storage, embedding_provider
    ):
        """The limit is not "never oscillate". One merge-then-reverse is a
        judgment revised, which is the system working."""
        once = await _fact(
            storage, embedding_provider, "The capital is Bonn.",
            lifecycle=_completed_cycles(1),
        )
        other = await _fact(storage, embedding_provider, "Bonn is the capital.")

        result = await _merge(storage, embedding_provider, [once, other])

        assert result["merged"] is True

    async def test_an_unfinished_merge_does_not_count_as_a_cycle(
        self, storage, embedding_provider
    ):
        """An open episode is a node that is still merged, not one that came
        back. Counting it would refuse on the strength of the merge that is
        being reversed rather than on a completed round trip."""
        open_episode = [LifecycleEpisode(
            retired_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            because=NodeStatus.MERGED,
            counterpart="survivor-0",
        )]
        never_returned = await _fact(
            storage, embedding_provider, "The capital is Bonn.",
            lifecycle=_completed_cycles(1) + open_episode,
        )
        other = await _fact(storage, embedding_provider, "Bonn is the capital.")

        result = await _merge(storage, embedding_provider, [never_returned, other])

        assert result["merged"] is True

    async def test_a_retirement_that_was_not_a_merge_does_not_count(
        self, storage, embedding_provider
    ):
        """A claim that stepped aside for its period and came back is the validity model's
        recurrence, not an oscillation — the case the lifecycle exists for."""
        recurred = await _fact(
            storage, embedding_provider, "Labour is in government.",
            lifecycle=[LifecycleEpisode(
                retired_at=datetime(2010, 5, 11, tzinfo=timezone.utc),
                because=NodeStatus.HISTORICAL,
                counterpart="successor",
                restored_at=datetime(2024, 7, 5, tzinfo=timezone.utc),
            )] * 3,
        )
        other = await _fact(storage, embedding_provider, "Labour governs.")

        result = await _merge(storage, embedding_provider, [recurred, other])

        assert result["merged"] is True

    async def test_the_count_is_per_node_rather_than_per_pair(
        self, storage, embedding_provider
    ):
        """Pair matching would miss `A+B`, then `A+C`, then `A+D` — one node
        oscillating against a fresh partner each time, which is the same waste
        wearing a disguise."""
        oscillated = await _fact(
            storage, embedding_provider, "The capital is Bonn.",
            lifecycle=_completed_cycles(2),
        )
        stranger = await _fact(
            storage, embedding_provider, "Bonn is the capital.",
        )

        result = await _merge(storage, embedding_provider, [oscillated, stranger])

        assert result["merged"] is False
        assert "merge_cycle_limit" in result["refused"]

    async def test_raising_the_limit_lets_the_merge_through(
        self, storage, embedding_provider
    ):
        """The refusal tells the agent to ask the user, so the escape hatch has
        to be real — otherwise a legitimate third merge is blocked with no
        recourse, which is worse than the oscillation.

        Raised through the *graph's* setting rather than a call argument,
        because that is the path the message points at: `merge_facts` resolves
        the limit from the active graph, so a caller cannot merge past it by
        passing a number of its own."""
        oscillated = await _fact(
            storage, embedding_provider, "The capital is Bonn.",
            lifecycle=_completed_cycles(2),
        )
        other = await _fact(storage, embedding_provider, "Bonn is the capital.")
        await storage.set_merge_overrides(MergeOverrides(cycle_limit=3))

        result = await _merge(storage, embedding_provider, [oscillated, other])

        assert result["merged"] is True

    async def test_a_permanent_obstacle_is_reported_ahead_of_this_one(
        self, storage, embedding_provider
    ):
        """Refusals are ordered permanent-first, and this one is fixable: a
        person can settle it or raise the limit. Reporting it while a
        cross-frame pair also stands would send an agent to do work that
        changes nothing."""
        oscillated = await _fact(
            storage, embedding_provider, "The capital is Bonn.",
            lifecycle=_completed_cycles(5),
        )
        other = await _fact(storage, embedding_provider, "Bonn is the capital.")
        await _framed(storage, other, "fiction")

        result = await _merge(storage, embedding_provider, [oscillated, other])

        assert result["merged"] is False
        assert "frames" in result["refused"]
        assert "merge_cycle_limit" not in result["refused"]


class TestTheSimilarityFloorIsTheNominationBar:
    async def test_facts_the_graph_would_never_have_paired_are_refused(
        self, storage, embedding_provider
    ):
        """Not a second opinion on the agent's judgment — a check that these are
        facts the loop could have offered each other in the first place."""
        first = await _fact(storage, embedding_provider, "The deploy failed.")
        second = await _fact(
            storage, embedding_provider, "Napoleon was born in 1769.",
            vector=_STRANGER,
        )

        result = await _merge(storage, embedding_provider, [first, second])

        assert result["merged"] is False
        assert str(SIMILARITY_NOMINATION_THRESHOLD) in result["refused"]

    async def test_a_fact_with_no_stored_embedding_is_refused(
        self, storage, embedding_provider
    ):
        """Similarity that cannot be checked is not similarity that was."""
        first = await _fact(storage, embedding_provider, "The deploy failed.")
        second = Fact(
            content="The deploy broke.", source_id="seg-1", claim_kind=ClaimKind.STATE,
        )
        await storage.store_node(second)

        result = await _merge(storage, embedding_provider, [first, second])

        assert result["merged"] is False
        assert "no stored embedding" in result["refused"]

    async def test_every_nomination_path_is_gated_at_the_merge_floor(self):
        """One home for the bar, and *every* path reads it.

        The invariant is **merge floor ≤ every nomination bar**: a merge that
        refuses what a sweep routinely nominates sends the agent to act on a
        candidate the graph will not accept. It was broken until 2026-08-21 —
        `check_conflicts` and `merge_facts` at 0.83, reflect's contradiction and
        recurrence sweeps at a literal 0.80 — so pairs in [0.80, 0.83) were
        nominated and then refused. Checked by signature rather than by
        behaviour because a drifted default is invisible to any call that passes
        the argument explicitly.

        The MCP boundary is included deliberately: `server.py` re-declared the
        number as its own literal, which is the one copy nothing would have
        caught when the constant moved.
        """
        import inspect

        from epimemer.mcp import server
        from epimemer.pipelines.reflection.contradiction_detection import (
            detect_contradictions,
        )

        bars = {
            "tools.check_conflicts": inspect.signature(tools.check_conflicts)
            .parameters["threshold"].default,
            "server.check_conflicts": inspect.signature(server.memory_check_conflicts)
            .parameters["threshold"].default,
            "detect_contradictions": inspect.signature(detect_contradictions)
            .parameters["similarity_threshold"].default,
        }
        floor = inspect.signature(tools.merge_facts).parameters[
            "similarity_threshold"
        ].default

        assert floor == SIMILARITY_NOMINATION_THRESHOLD
        assert all(bar >= floor for bar in bars.values()), bars

    async def test_a_pair_reflect_nominates_can_be_merged(
        self, storage, embedding_provider
    ):
        """The band that used to exist, checked from the outside: a pair scoring
        just over the sweep's bar is nominated *and* mergeable, rather than
        offered and then refused."""
        from epimemer.pipelines.reflection.contradiction_detection import (
            detect_contradictions,
        )

        first = await _fact(storage, embedding_provider, "The deploy failed.")
        second = await _fact(
            storage, embedding_provider, "The deployment did not succeed.",
            # cos(_TWIN, this) = 0.81 — inside the old [0.80, 0.83) band, where
            # reflect nominated and the merge gate refused.
            vector=[0.81, 0.5865, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        )

        nominated = await detect_contradictions(
            storage, embedding_provider, model_id=embedding_provider.model_id,
        )
        assert {first.id, second.id} in [{a.id, b.id} for a, b, _ in nominated]

        result = await _merge(storage, embedding_provider, [first, second])
        assert result["merged"] is True, result.get("refused")


class TestTheSurvivorInheritsWhatItsSourcesCarried:
    async def test_the_value_signal_is_combined_rather_than_rebuilt(
        self, storage, embedding_provider
    ):
        """The shared value rebuild, whose second caller this is. A field-by-field
        reconstruction silently resets whatever it forgets to name."""
        judged_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        first = await _fact(
            storage, embedding_provider, "The deploy failed.",
            value=ValueSignal(importance=0.9, importance_judged_at=judged_at),
        )
        second = await _fact(
            storage, embedding_provider, "The deploy broke.",
            value=ValueSignal(importance=0.2),
        )

        result = await _merge(storage, embedding_provider, [first, second])

        survivor = await storage.get_node(result["fact_id"])
        assert survivor.value.importance == 0.9
        assert survivor.value.importance_judged_at == judged_at

    async def test_the_survivor_is_a_state_so_it_can_merge_again(
        self, storage, embedding_provider
    ):
        """Every source cleared the gate as a state. A survivor left unjudged
        could never merge again, for want of a judgment its own parts carried."""
        first = await _fact(storage, embedding_provider, "The deploy failed.")
        second = await _fact(storage, embedding_provider, "The deploy broke.")

        result = await _merge(storage, embedding_provider, [first, second])

        survivor = await storage.get_node(result["fact_id"])
        assert survivor.claim_kind is ClaimKind.STATE

    async def test_the_reason_behind_the_kept_confidence_comes_with_it(
        self, storage, embedding_provider
    ):
        """The confidence a merge keeps is the highest of its sources, and the
        prose explaining it lives in metadata — so rebuilding one without the
        other leaves a prior nobody can review."""
        first = await _fact(
            storage, embedding_provider, "The deploy failed.",
            value=ValueSignal(confidence=0.9),
            metadata={"confidence_basis": "the deploy log itself"},
        )
        second = await _fact(
            storage, embedding_provider, "The deploy broke.",
            value=ValueSignal(confidence=0.3),
            metadata={"confidence_basis": "a colleague's recollection"},
        )

        result = await _merge(storage, embedding_provider, [first, second])

        survivor = await storage.get_node(result["fact_id"])
        assert survivor.value.confidence == 0.9
        assert survivor.metadata["confidence_basis"] == "the deploy log itself"

    async def test_unrated_sources_leave_the_survivor_owing_no_reason(self):
        signals = [
            Fact(content="a", source_id="s", value=ValueSignal()),
            Fact(content="b", source_id="s", value=ValueSignal()),
        ]

        assert merged_confidence_basis(signals) is None

    async def test_the_survivor_names_what_it_was_made_from(
        self, storage, embedding_provider
    ):
        first = await _fact(storage, embedding_provider, "The deploy failed.")
        second = await _fact(storage, embedding_provider, "The deploy broke.")

        result = await _merge(storage, embedding_provider, [first, second])

        survivor = await storage.get_node(result["fact_id"])
        assert survivor.metadata["merged_from"] == [first.id, second.id]


class TestADependentInferenceIsToldItsPremiseChanged:
    """A merge rewords what an inference rests on, so it flags dependents.

    Every other event that changes a premise already does: both supersession
    paths call `plan_evidence_stale_edges`, and `review_labels_for` derives
    `evidence_stale` from a `derived_from` edge into a retired fact. A merge
    reached neither, and could not — the `derived_from` edge migrates onto the
    survivor in the same transaction, the survivor is ACTIVE, and MERGED is not
    in SUPERSEDED_STATUSES — so the inference was left resting on agent-written
    text it was never drawn from, with `review_labels` empty.

    **The flag is its own rather than supersession's.** Nothing was overturned:
    the claim under the inference is the claim it was drawn from, and what
    changed is the wording that states it and the documents behind it. Told
    `evidence_stale`, an agent would read an overturning that did not happen —
    and archival, which nominates on that label, would propose discarding the
    inference on every merge.
    """

    async def test_both_dependency_directions_are_flagged(
        self, storage, embedding_provider
    ):
        """`derived_from` and `supports` are the two ways an inference depends
        on a fact, and the planner has always covered both."""
        first = await _fact(storage, embedding_provider, "The deploy failed.")
        second = await _fact(
            storage, embedding_provider, "The deployment did not succeed."
        )
        inference = await _inference(storage, "Deployments have been failing.")
        await storage.store_edge(NodeEdge(
            src_id=inference.id, dst_id=first.id, type=EdgeType.DERIVED_FROM,
        ))
        await storage.store_edge(NodeEdge(
            src_id=second.id, dst_id=inference.id, type=EdgeType.SUPPORTS,
        ))

        await _merge(storage, embedding_provider, [first, second])

        labels = await review_labels(await storage.get_node(inference.id), storage)
        assert set(labels["evidence_merged"]) == {first.id, second.id}

    async def test_the_flag_says_absorbed_rather_than_overturned(
        self, storage, embedding_provider
    ):
        first = await _fact(storage, embedding_provider, "The deploy failed.")
        second = await _fact(
            storage, embedding_provider, "The deployment did not succeed."
        )
        inference = await _inference(storage, "Deployments have been failing.")
        await storage.store_edge(NodeEdge(
            src_id=inference.id, dst_id=first.id, type=EdgeType.DERIVED_FROM,
        ))

        await _merge(storage, embedding_provider, [first, second])

        labels = await review_labels(await storage.get_node(inference.id), storage)
        assert "evidence_stale" not in labels
        assert list(await storage.get_edges_to(
            inference.id, edge_type=EdgeType.EVIDENCE_SUPERSEDED
        )) == []

    async def test_the_flag_stays_on_the_fact_that_was_absorbed(
        self, storage, embedding_provider
    ):
        """A review edge is anchored to the node version it was written about,
        so it is not migrated onto the survivor — which is what lets the agent
        see *which wording* went away."""
        first = await _fact(storage, embedding_provider, "The deploy failed.")
        second = await _fact(
            storage, embedding_provider, "The deployment did not succeed."
        )
        inference = await _inference(storage, "Deployments have been failing.")
        await storage.store_edge(NodeEdge(
            src_id=inference.id, dst_id=first.id, type=EdgeType.DERIVED_FROM,
        ))

        result = await _merge(storage, embedding_provider, [first, second])

        flags = await storage.get_edges_to(
            inference.id, edge_type=EdgeType.EVIDENCE_MERGED
        )
        assert [edge.src_id for edge in flags] == [first.id]
        assert result["fact_id"] not in {edge.src_id for edge in flags}

    async def test_an_inference_resting_on_neither_fact_is_untouched(
        self, storage, embedding_provider
    ):
        first = await _fact(storage, embedding_provider, "The deploy failed.")
        second = await _fact(
            storage, embedding_provider, "The deployment did not succeed."
        )
        bystander = await _inference(storage, "The release notes were late.")

        await _merge(storage, embedding_provider, [first, second])

        assert await review_labels(
            await storage.get_node(bystander.id), storage
        ) == {}


class TestMalformedRequests:
    async def test_a_single_fact_is_already_itself(
        self, storage, embedding_provider
    ):
        only = await _fact(storage, embedding_provider, "The deploy failed.")

        result = await _merge(storage, embedding_provider, [only])

        assert result["merged"] is False
        assert "already itself" in result["refused"]

    async def test_the_same_fact_named_twice_is_not_two_facts(
        self, storage, embedding_provider
    ):
        only = await _fact(storage, embedding_provider, "The deploy failed.")

        result = await _merge(storage, embedding_provider, [only, only])

        assert result["merged"] is False
        assert "already itself" in result["refused"]

    async def test_an_unknown_id_raises_rather_than_refusing(
        self, storage, embedding_provider
    ):
        """A refusal is a judgment the graph made; an id naming nothing is a
        request that was never well-formed."""
        real = await _fact(storage, embedding_provider, "The deploy failed.")

        with pytest.raises(ValueError, match="not found"):
            await _merge(storage, embedding_provider, [real, Fact(
                content="x", source_id="s",
            )])

    async def test_a_topic_id_raises(self, storage, embedding_provider):
        real = await _fact(storage, embedding_provider, "The deploy failed.")
        topic = Topic(content="Deployments", source_id=None)
        await storage.store_node(topic)

        with pytest.raises(ValueError, match="only facts merge"):
            await tools.merge_facts(
                source_ids=[real.id, topic.id],
                content="One claim.",
                storage=storage,
                embedding_provider=embedding_provider,
            )


class TestTheJudgmentIsRecordedAtIngest:
    """It can only be made here. Everything downstream sees stripped sentences."""

    async def test_a_supplied_claim_kind_reaches_the_stored_fact(
        self, storage, embedding_provider, config
    ):
        seg_result, _ = await tools.segment_text(
            "Labour won the election.", storage, embedding_provider, config,
        )
        await tools.store_decomposition(
            document_id=seg_result["document_id"],
            segments=[{
                "segment_id": seg_result["segments"][0]["segment_id"],
                "facts": [
                    {"content": "Labour won the election.", "claim_kind": "event"},
                ],
            }],
            storage=storage,
            embedding_provider=embedding_provider,
            metacontext_id=BASE_METACONTEXT_ID,
        )

        facts = await storage.query_nodes(node_type=tools.NodeType.FACT)
        assert [fact.claim_kind for fact in facts] == [ClaimKind.EVENT]

    async def test_omitting_it_stores_unjudged_rather_than_a_default(
        self, storage, embedding_provider, config
    ):
        """A guessed default would be a judgment nobody made, on the field a
        merge is gated by."""
        seg_result, _ = await tools.segment_text(
            "Labour is in government.", storage, embedding_provider, config,
        )
        await tools.store_decomposition(
            document_id=seg_result["document_id"],
            segments=[{
                "segment_id": seg_result["segments"][0]["segment_id"],
                "facts": ["Labour is in government."],
            }],
            storage=storage,
            embedding_provider=embedding_provider,
            metacontext_id=BASE_METACONTEXT_ID,
        )

        facts = await storage.query_nodes(node_type=tools.NodeType.FACT)
        assert [fact.claim_kind for fact in facts] == [None]

    async def test_supplying_it_on_a_topic_raises_rather_than_dropping_it(
        self, storage, embedding_provider, config
    ):
        """A judgment written into a field that does not exist is one the agent
        believes it made, and it would surface — if ever — as a merge that
        quietly never happens."""
        seg_result, _ = await tools.segment_text(
            "Labour is in government.", storage, embedding_provider, config,
        )

        with pytest.raises(ValueError, match="Only facts carry it"):
            await tools.store_decomposition(
                document_id=seg_result["document_id"],
                segments=[{
                    "segment_id": seg_result["segments"][0]["segment_id"],
                    "topics": [
                        {"content": "British politics", "claim_kind": "state"},
                    ],
                }],
                storage=storage,
                embedding_provider=embedding_provider,
                metacontext_id=BASE_METACONTEXT_ID,
            )

    async def test_a_kind_outside_the_vocabulary_is_rejected(
        self, storage, embedding_provider, config
    ):
        seg_result, _ = await tools.segment_text(
            "Labour is in government.", storage, embedding_provider, config,
        )

        with pytest.raises(ValueError):
            await tools.store_decomposition(
                document_id=seg_result["document_id"],
                segments=[{
                    "segment_id": seg_result["segments"][0]["segment_id"],
                    "facts": [
                        {"content": "Labour governs.", "claim_kind": "condition"},
                    ],
                }],
                storage=storage,
                embedding_provider=embedding_provider,
                metacontext_id=BASE_METACONTEXT_ID,
            )

    async def test_the_kind_survives_a_round_trip_through_the_backend(
        self, storage, embedding_provider
    ):
        """Both backends, because a judgment that does not persist is one the
        merge can never read."""
        stored = await _fact(
            storage, embedding_provider, "Labour won.", claim_kind=ClaimKind.EVENT,
        )

        assert (await storage.get_node(stored.id)).claim_kind is ClaimKind.EVENT
