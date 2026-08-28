"""The gate a fact merge has to clear.

Deduplication's error mode is the worst one the system can produce. Two distinct
claims wrongly unified become one node with two independent sources, which reads
as *better supported* than either was — so a false merge does not merely lose
information, it **manufactures corroboration**. The quantity it corrupts is
exactly the quantity corroboration measures, which means a sloppy merge does not
degrade that number, it inverts it. A missed merge only undercounts.

Everything here follows from that asymmetry: **tune to under-merge.** Each rule
refuses on doubt rather than resolving it, and every refusal says which obstacle
it hit rather than returning a bare boolean. The agent that asked has a real
alternative — record `SIMILARITY` and keep both, which is what corroboration
consumes anyway (REVIEW_EPISTEMIC.md §3) — and can only choose it knowing
whether it hit something permanent or something it can answer.

That is also why the refusals are ordered permanent-first. A cross-frame pair
will never merge however the graph changes; an unjudged one merges as soon as
somebody judges it. Reporting the fixable obstacle while a permanent one also
stands would send an agent to do work that changes nothing.
"""

from collections.abc import Sequence

from pydantic import BaseModel

from epimemer.core.types import (
    DEFAULT_MERGE_CYCLE_LIMIT,
    ClaimKind,
    Fact,
    NodeStatus,
    completed_merge_cycles,
)
from epimemer.pipelines.reflection.review import SIMILARITY_NOMINATION_THRESHOLD, frames_for
from epimemer.pipelines.reflection.topic_consolidation import all_pairs_above_threshold
from epimemer.storage.protocol import StorageBackend


class MergeRefused(BaseModel):
    """Why a merge was declined, in words the agent that asked can act on.

    Prose rather than a code, matching `BoundaryRefused`: the reasons do not
    form a vocabulary anything branches on, and each one is only useful to the
    extent it says which of several near-identical-looking situations occurred.
    """

    reason: str


async def merge_refusal(
    sources: Sequence[Fact],
    storage: StorageBackend,
    *,
    model_id: str,
    similarity_threshold: float = SIMILARITY_NOMINATION_THRESHOLD,
    cycle_limit: int = DEFAULT_MERGE_CYCLE_LIMIT,
) -> MergeRefused | None:
    """Why these facts must not be collapsed into one, or `None` if nothing objects.

    `None` is not an endorsement — the *judgment* that two sentences are one
    claim is the agent's, made with the candidates in front of it, and no cosine
    stands in for it. What this answers is the narrower question: given what the
    graph knows, is there a reason this merge would be wrong regardless of how
    good that judgment is.
    """
    distinct = {fact.id for fact in sources}
    if len(distinct) < 2:
        return MergeRefused(
            reason=(
                f"a merge collapses several facts into one and this names "
                f"{len(distinct)}; a single fact is already itself."
            )
        )

    retired = [fact for fact in sources if fact.status is not NodeStatus.ACTIVE]
    if retired:
        # A historical twin is the `recurs` verdict, not `redundant`, and it has
        # its own resolution: `restore` reactivates the twin and records the new
        # source's period on it. Merging instead would destroy the very history
        # that made the recurrence visible — and merging a corrected claim into
        # its correction would fold an error the graph already ruled on back
        # into the active set.
        statuses = ", ".join(sorted({fact.status.value for fact in retired}))
        return MergeRefused(
            reason=(
                f"only active facts merge, and {len(retired)} of these are "
                f"{statuses}. A historical twin recurring is `restore`, not a "
                f"merge; a corrected one has been ruled on already."
            )
        )

    frames = await frames_for([fact.id for fact in sources], storage)
    if len({frozenset(frames[fact.id]) for fact in sources}) > 1:
        # Not `same_frame`, which asks whether two nodes share *at least one*
        # frame. That is the right question for a contradiction — an overlap
        # makes the conflict real — and the wrong one here, because a merge
        # inherits the union of its sources' frames. Collapsing a base-reality
        # claim into one also framed as fiction would leave a node asserting
        # both, which is the single worst outcome available.
        return MergeRefused(
            reason=(
                "these facts do not stand in exactly the same set of frames, "
                "and a merged node would inherit the union of them — asserting "
                "in one world what was only ever claimed in another. A "
                "cross-frame twin is `record_variant`."
            )
        )

    events = [fact for fact in sources if fact.claim_kind is ClaimKind.EVENT]
    if events:
        return MergeRefused(
            reason=(
                "an occurrence never merges. Two documents describing separate "
                "elections yield near-identical sentences, and collapsing them "
                "unions their periods into one victory spanning both — history "
                "neither source claims. Record `SIMILARITY` and keep both."
            )
        )

    unjudged = [fact for fact in sources if fact.claim_kind is None]
    if unjudged:
        # The whole of the legacy corpus is in this state, and deliberately so:
        # the judgment wants the document, which is long gone by the time
        # anything asks. Refusing is the cost of recording it at ingest, taken
        # knowingly — the alternative is guessing at merge time from two
        # stripped sentences, which is how an election becomes a condition.
        return MergeRefused(
            reason=(
                f"{len(unjudged)} of these facts were stored without a "
                f"claim_kind, so nothing knows whether they describe conditions "
                f"or occurrences — and the two answers merge in opposite "
                f"directions. Facts ingested with `claim_kind` set are eligible."
            )
        )

    oscillating = [
        fact for fact in sources
        if completed_merge_cycles(fact) >= cycle_limit
    ]
    if oscillating:
        # Merged, reversed, merged again is an agent burning tokens on an
        # oscillation nobody wants. Not expected — but hard to catch after the
        # fact and nearly free to catch here, since `merge_facts` has already
        # loaded every source, so `lifecycle` is in hand and the check costs no
        # round trip.
        #
        # **A refusal rather than a warning, deliberately.** A warning is
        # something an agent reads and proceeds past, which is the exact failure
        # this exists for; `docs/REFLECTION.md` §1 puts consequential calls in
        # front of the human. Placed among the fixable refusals rather than the
        # permanent ones: a person can settle it, or raise the limit.
        #
        # **Accepted gap:** an agent could evade this by merging a different
        # source set. Recorded rather than closed — the simple version is worth
        # having, and detecting deliberate evasion here would be solving a
        # problem nobody has.
        counts = ", ".join(
            str(completed_merge_cycles(fact)) for fact in oscillating
        )
        return MergeRefused(
            reason=(
                f"{len(oscillating)} of these facts have already been merged and "
                f"un-merged before ({counts} times), reaching the "
                f"merge_cycle_limit of {cycle_limit} this merge was gated at. "
                f"Merging again is likely to be reversed again. Ask the user "
                f"before proceeding — and if the merge is right, the limit is "
                f"configurable per graph."
            )
        )

    if not await all_pairs_above_threshold(
        list(sources), storage, model_id, similarity_threshold
    ):
        # The floor is the *nomination* bar rather than a second, higher one, and
        # it is not a second opinion on the agent's judgment. It says only: a
        # merge may collapse facts that could have been offered to each other as
        # candidates, which is what catches a pairing named by mistake. A fact
        # with no stored embedding fails it, since similarity that cannot be
        # checked is not similarity that was.
        #
        # **The message names the bar this call applied rather than asserting
        # what the graph would have done.** `similarity_threshold` is an
        # argument, so a caller can pass one the sweeps do not use — and the old
        # wording ("not facts the graph would have offered each other") then
        # stated something false about a pair reflect had just nominated. That
        # was live until 2026-08-21, when the sweeps and this gate still ran at
        # different numbers; the claim about the graph is now the constant's to
        # make, not this string's.
        return MergeRefused(
            reason=(
                f"not every pair reaches the {similarity_threshold} similarity "
                f"bar this merge was gated at (or one of them has no stored "
                f"embedding). Facts below the bar are not nominated as "
                f"candidates, so `SIMILARITY` and keeping both is the action "
                f"available here."
            )
        )

    return None


def merged_confidence_basis(sources: Sequence[Fact]) -> str | None:
    """The recorded reason belonging to the confidence a merge keeps.

    `merged_value_signal` takes the highest confidence of its sources, and the
    prose explaining that number lives in `metadata` rather than beside it, so
    that `ValueSignal` stays the numbers every ranker reads. The
    consequence is that a merge which rebuilds the signal and not the metadata
    leaves a prior with its reason stripped off — the exact state the guidance
    exists to prevent, reached by a path nobody chose.

    Unrated sources take no part, as they take none in the signal itself: a
    merge of unrated facts stays unrated and so owes no reason. Ties keep the
    first, which is arbitrary but deterministic — and by then two sources have
    said the same thing about the same claim.
    """
    rated = [fact for fact in sources if fact.value.confidence is not None]
    if not rated:
        return None
    return max(rated, key=lambda fact: fact.value.confidence).metadata.get(
        "confidence_basis"
    )
