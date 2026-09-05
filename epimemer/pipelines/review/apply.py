"""The two things a reviewer can write (§6.4, §6.5).

`review()` reads and never writes, like `reflect`. These are what acting on
what it found goes through.

**Confirming costs something, or the treadmill moves up a level.** If agent 2
checks a decision and agrees, and nothing records that, agent 3 does the same
work again — the unrecorded-verdict defect one layer higher. So a review is a `DecisionRecord`
pointing back, and `reviewed` stays derived from a row's existence rather than
stored as a flag on a row that claims to be append-only (§3.4).

**Neither writer changes the graph, and that is deliberate for opposite
reasons.**

- A *confirmation* has nothing to change: the decision stands.
- A *dissent* has plenty to change, and does none of it. The undo for a merge is
  `reverse_merge`, for an archival `restore`, for a `one_claim` verdict a
  `distinct` through `apply_reflection` — each with its own refusals, its own
  transaction and its own journal row that sets `supersedes` because it really
  did supersede something. A dispatcher over four such tools is the fan-out scope-blindness
  refused: *a convenience less safe than the sequence it replaces is not a
  convenience.* And the reviewer who most needs to record a dissent is the one
  whose undo was **refused** — a merge whose survivor has since been contradicted
  cannot be reversed at all (§7), and before this there was nowhere to put the
  finding.

So a dissent sets `reviews` and never `supersedes`. A row claiming to supersede
a decision whose effect still stands would put the journal in disagreement with
the graph, and §4.2's whole point is that the two answer different questions
without contradicting each other.

**A retry must not read as a second opinion.** Re-running a batch after a
timeout would otherwise write two confirmations over one decision, and two
confirmations is exactly the evidence a later reviewer weighs — so an identical
judgment by the same judge is refused, naming the row that already says it. A
*different* judge confirming the same decision is not a retry; it is the second
independent check this whole design exists to make possible.

**The gap that leaves, named rather than hidden:** on a graph that does not
require a judge, two blank judges cannot be told apart, so a retry there writes
a second row. That is one more thing `require_judge` buys, and it is why the
identity check is on the id rather than on the pair of subjects alone.
"""

from collections.abc import Sequence

from pydantic import BaseModel

from epimemer.core.types import (
    ClaimKind,
    DecisionKind,
    DecisionRecord,
    Fact,
    JudgeRef,
)
from epimemer.storage.protocol import StorageBackend, judge_aliases

# The kinds whose row records a decision that *created* a node, and therefore
# set the priors `rejudge` revises. A rejudgment points its `reviews` at the
# oldest of these naming the node — the decision itself, not an intervening
# confirmation of it, which is the rule §10.5 already applies to pair verdicts.
ORIGINATING_KINDS: tuple[DecisionKind, ...] = (
    DecisionKind.INGEST,
    DecisionKind.SYNTHESIS,
    DecisionKind.MERGE,
    DecisionKind.SPLIT,
    DecisionKind.ENRICHMENT,
)

# What `rejudge` may revise. Named here because two documents disagree about it
# in passing and this is the declaration: `importance` is **not** in it —
# `judge_importance` is already a judgment with a reason and a clock, and two
# writers for one field is how a value ends up depending on which tool last ran.
REJUDGEABLE_FIELDS: tuple[str, ...] = ("claim_kind", "confidence", "confidence_basis")


class ReviewRefused(BaseModel):
    """Why one review was not recorded.

    Prose rather than a code, matching `SimilarityRefused` and `MergeRefused`:
    the reasons do not form a vocabulary anything branches on.
    """

    decision_id: str
    reason: str


class ReviewRecorded(BaseModel):
    """What one accepted review wrote.

    `reviewed_judge` is who made the decision being reviewed, returned rather
    than left for a second lookup: an agent confirming its own earlier call
    should be able to see that it did. That is allowed — re-reading your own
    work later is a review — but it is a weaker check than an independent one,
    and the difference is only visible if somebody says so.
    """

    decision_id: str
    record_id: str
    kind: str
    subjects: list[str]
    reviewed_judge: str | None = None


class RejudgeRefused(BaseModel):
    node_id: str
    reason: str


class Rejudged(BaseModel):
    """One revised judgment, and what it moved.

    `changed` names only the fields that actually differ, so a caller can tell a
    revision from a restatement without comparing values itself.
    """

    node_id: str
    changed: dict[str, object]
    # The record that made the original judgment, for the caller to point the
    # journal row at. Computed here because this is where the node is in hand.
    reviews: str | None = None


def _covers(existing: DecisionRecord, subjects: Sequence[str]) -> bool:
    """Does an existing review already cover every subject this one names?

    Subject-scoped, because §4.1 settles confirmation granularity that way: one
    `reviews` pointer at an ingest record covering forty-four facts would tell
    the graph a reviewer checked forty-four when it checked six. So a second
    confirmation naming *different* subjects of the same record is new work, and
    only one naming a subset of what is already confirmed is a retry.
    """
    return set(subjects) <= set(existing.subject_ids)


async def review_decision(
    storage: StorageBackend,
    *,
    decision_id: str,
    agreed: bool,
    because: str,
    subject_ids: Sequence[str] | None = None,
    certainty: float | None = None,
    certainty_basis: str | None = None,
    judge: JudgeRef | None = None,
) -> ReviewRefused | ReviewRecorded:
    """Record that somebody checked one journal row, and what they concluded.

    Refusals are ordered permanent-first, on `fact_dedup`'s reasoning: a
    malformed request will never become well-formed, while a duplicate may stop
    being one as soon as a different judge asks.
    """
    kind = DecisionKind.CONFIRMATION if agreed else DecisionKind.DISSENT

    if not because.strip():
        return ReviewRefused(
            decision_id=decision_id,
            reason=(
                "`because` is required. A review with no reason is a rubber "
                "stamp, and it costs more than nothing: it marks the decision "
                "checked, so the next reviewer skips it without being able to "
                "tell whether it was examined or waved through."
            ),
        )
    if certainty is not None and not 0.0 <= certainty <= 1.0:
        return ReviewRefused(
            decision_id=decision_id,
            reason=(
                f"`certainty` is {certainty}; it is a value on the same ladder "
                f"as `confidence` (0.0–1.0). Omit it for the ordinary case — "
                f"omitting stores *unrated*, which is deliberately not a "
                f"rated 0.5."
            ),
        )

    reviewed = await storage.get_decision(decision_id)
    if reviewed is None:
        return ReviewRefused(
            decision_id=decision_id,
            reason=(
                f"no decision '{decision_id}' in graph "
                f"'{storage.current_database}'. The journal is per graph "
                f"— if this id came from another one, `use_graph` first."
            ),
        )

    subjects = list(subject_ids) if subject_ids else list(reviewed.subject_ids)
    stray = [sid for sid in subjects if sid not in reviewed.subject_ids]
    if stray:
        return ReviewRefused(
            decision_id=decision_id,
            reason=(
                f"{', '.join(stray)} is not a subject of this decision. A "
                f"review names a subset of what the decision was about (§4.1); "
                f"reviewing something else is its own decision, about its own "
                f"row."
            ),
        )

    if judge is not None:
        # Only where the judge is known. Two blank judges may be two agents or
        # one retry, and refusing on that guess would block a genuine second
        # opinion on every graph that does not require a judge.
        # Every id this judge's rows may carry, not only the one bound now: a
        # judge that absorbed another record has decisions under both, and a
        # duplicate check that saw only the current id would let the same judge
        # confirm the same decision twice.
        for existing in await storage.query_decisions(
            reviews=decision_id,
            kinds=[kind],
            agent_ids=await judge_aliases(storage, judge.agent_id),
        ):
            if _covers(existing, subjects):
                verb = "confirmed" if agreed else "dissented from"
                return ReviewRefused(
                    decision_id=decision_id,
                    reason=(
                        f"{judge.agent_id} has already {verb} this decision "
                        f"({existing.id}). A second identical row would read as "
                        f"a second opinion, and it is the same one. Naming "
                        f"different subjects of the decision is new work and is "
                        f"accepted; changing your mind is the other verdict, "
                        f"which is also accepted."
                    ),
                )

    record = DecisionRecord(
        kind=kind,
        subject_ids=subjects,
        judged_by=judge,
        certainty=certainty,
        certainty_basis=certainty_basis,
        reviews=decision_id,
    )
    await storage.record_decision(record)
    return ReviewRecorded(
        decision_id=decision_id,
        record_id=record.id,
        kind=kind.value,
        subjects=subjects,
        reviewed_judge=(reviewed.judged_by.agent_id if reviewed.judged_by is not None else None),
    )


async def rejudge_node(
    storage: StorageBackend,
    *,
    node_id: str,
    because: str,
    claim_kind: ClaimKind | None = None,
    confidence: float | None = None,
    confidence_basis: str | None = None,
    certainty: float | None = None,
    certainty_basis: str | None = None,
    judge: JudgeRef | None = None,
) -> RejudgeRefused | Rejudged:
    """Revise an ingest-time judgment about a node, without touching the claim.

    **Never a supersession**, and that is the point of having it at all. `update`
    requires `because` to be *it was wrong* or *the world changed*, and a
    mislabelled `claim_kind` is neither: the claim was right and the world did
    not move — **the judgment about the claim was wrong**. Filing it as a
    correction would retire a true node and re-point its edges, which is the
    forgetting the validity model exists to prevent, for a metadata mistake.

    So nothing here moves a status, an edge or a lineage. The node keeps its
    `judged_by`: that field records who wrote the *wording*, which is unchanged.

    **The prior value is kept, not overwritten.** A trail entry goes in
    `metadata["rejudgments"]` alongside the change — without it this call is the
    one place in the system where a judgment is destroyed rather than
    superseded, and *"nothing is destroyed"* would stop being true.
    """
    if not because.strip():
        return RejudgeRefused(
            node_id=node_id,
            reason=(
                "`because` is required: this overwrites a prior another agent "
                "supplied after reading the material, and the graph has to "
                "carry why."
            ),
        )
    supplied = {
        name: value
        for name, value in (
            ("claim_kind", claim_kind),
            ("confidence", confidence),
            ("confidence_basis", confidence_basis),
        )
        if value is not None
    }
    if not supplied:
        return RejudgeRefused(
            node_id=node_id,
            reason=(
                f"nothing to revise. Supply at least one of: "
                f"{', '.join(REJUDGEABLE_FIELDS)}. Three ingest judgments are "
                f"revised elsewhere, each because of how it is addressed: "
                f"`importance` by `judge_importance`; a **frame** by "
                f"`reframe`, since withdrawing a metacontext moves an edge and "
                f"changes what merges, corroborates and searches; a **validity "
                f"interval** by `correct_interval`, since an interval belongs "
                f"to a (node, source) pair rather than to a node. None of the "
                f"three is a job for `supersede_by`, which would file a true "
                f"claim as an error."
            ),
        )
    if confidence is not None and not 0.0 <= confidence <= 1.0:
        return RejudgeRefused(
            node_id=node_id,
            reason=f"`confidence` is {confidence}; the ladder runs 0.0–1.0.",
        )
    if certainty is not None and not 0.0 <= certainty <= 1.0:
        return RejudgeRefused(
            node_id=node_id,
            reason=f"`certainty` is {certainty}; the ladder runs 0.0–1.0.",
        )

    node = await storage.get_node(node_id)
    if node is None:
        return RejudgeRefused(node_id=node_id, reason=f"no such node: {node_id}.")

    if claim_kind is not None and not isinstance(node, Fact):
        # The refusal `_claim_kind_field` already makes at ingest, for the same
        # reason: a judgment written into a field that does not exist is one the
        # agent believes it made, and it would surface — if ever — as a merge
        # that quietly never happens.
        return RejudgeRefused(
            node_id=node_id,
            reason=(
                f"{node_id} is a {type(node).__name__.lower()} and has no "
                f"`claim_kind` to gate — the field is on facts alone. A topic is "
                f"a theme rather than a claim, and inferences are meant to "
                f"coexist."
            ),
        )

    changed: dict[str, object] = {}
    was: dict[str, object] = {}
    if claim_kind is not None and node.claim_kind != claim_kind:
        was["claim_kind"] = node.claim_kind.value if node.claim_kind else None
        changed["claim_kind"] = claim_kind.value
    if confidence is not None and node.value.confidence != confidence:
        was["confidence"] = node.value.confidence
        changed["confidence"] = confidence
    existing_basis = node.metadata.get("confidence_basis")
    if confidence_basis is not None and existing_basis != confidence_basis:
        was["confidence_basis"] = existing_basis
        changed["confidence_basis"] = confidence_basis

    if not changed:
        return RejudgeRefused(
            node_id=node_id,
            reason=(
                "every value supplied is what the node already carries, so "
                "there is nothing to revise. Restating a judgment you agree "
                "with is a confirmation — `apply_review` is the call that "
                "records one, against the decision that made it."
            ),
        )

    # `reviews` names the decision that made the original judgment, which is the
    # oldest originating row rather than the newest: a rejudgment revises the
    # decision, not a later confirmation of it (§10.5's rule for pair verdicts).
    originating = await storage.query_decisions(subject_id=node_id, kinds=list(ORIGINATING_KINDS))
    reviews = originating[-1].id if originating else None

    if "claim_kind" in changed:
        node.claim_kind = claim_kind
    value_update = {name: value for name, value in changed.items() if name == "confidence"}
    if value_update:
        node.value = node.value.model_copy(update=value_update)
    node.metadata = {
        **node.metadata,
        **({"confidence_basis": confidence_basis} if "confidence_basis" in changed else {}),
        # Append-only, and the only place the prior value survives. One
        # chronological trail rather than a field per revision, on
        # `judge_importance`'s reasoning: a reviewer wants a judgment and its
        # later reversal in sequence, with both reasons.
        "rejudgments": [
            *node.metadata.get("rejudgments", []),
            {
                "because": because,
                "was": was,
                "now": changed,
                "judged_by": judge.model_dump(mode="json") if judge else None,
            },
        ],
    }
    await storage.store_node(node)
    return Rejudged(node_id=node_id, changed=changed, reviews=reviews)
