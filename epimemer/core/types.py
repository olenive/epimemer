"""Core Pydantic models for the epistemic memory system.

These types serve double duty:
1. Storage schema — serialized to/from the database
2. Petri net tokens — flow through processing pipelines
"""

from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from epimemer.core.temporal import ImpreciseInstant, ValidityInterval


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid4())


# --- Enums ---


class NodeType(str, Enum):
    TOPIC = "topic"
    FACT = "fact"
    INFERENCE = "inference"


class NodeStatus(str, Enum):
    ACTIVE = "active"
    # Retired by supersession, reason unrecorded. **Legacy only** — kept so
    # graphs written before #53 still load, since those rows genuinely do not
    # say which of the two below they were and inventing an answer would be a
    # lie. Nothing writes it any more.
    SUPERSEDED = "superseded"
    # We were wrong. The claim should not have been believed, and the node is
    # kept for the audit trail rather than for its content.
    CORRECTED = "corrected"
    # The world moved on. The claim was right and is **still right of its
    # period** — only no longer current. Filing this as an error is how a graph
    # forgets history: Saint Petersburg became Leningrad became Saint
    # Petersburg, each name correct in its turn (#53).
    HISTORICAL = "historical"
    MERGED = "merged"
    # Retired for triviality rather than for being wrong: the node was fine,
    # it just was not worth keeping in the active set. Reversed by `restore`.
    ARCHIVED = "archived"


# What "retired by supersession" means now that it means three things. Readers
# that used `== NodeStatus.SUPERSEDED` must use this instead: the comparison
# still runs, it simply stops seeing two thirds of the cases, which is the
# quiet kind of regression.
SUPERSEDED_STATUSES: frozenset[NodeStatus] = frozenset({
    NodeStatus.SUPERSEDED, NodeStatus.CORRECTED, NodeStatus.HISTORICAL,
})


# Which statuses a claim can come back from, and it is the same question twice:
# what a similarity pass may nominate for review, and what `restore` may
# reactivate. `HISTORICAL` is in both because the world moving on is reversible
# — the claim was right and may be right again. `CORRECTED` is in neither: a
# claim concluded *wrong* has no route back, so nominating it would invite a
# verdict nothing can record, and restoring it would resurrect an error the
# graph already ruled on. That was always `restore`'s stated reason; before the
# status split it could only be enforced as "not superseded", which caught the
# world-change case too (#53 T2).
NOMINATED_STATUSES: frozenset[NodeStatus] = frozenset({
    NodeStatus.ACTIVE, NodeStatus.HISTORICAL,
})

# `ARCHIVED` joins it here and not above: archival retires a node for
# triviality rather than for being wrong, so it is reversible — but a
# similarity pass has no business nominating something the graph deliberately
# set aside as not worth keeping.
RESTORABLE_STATUSES: frozenset[NodeStatus] = frozenset({
    NodeStatus.ARCHIVED, NodeStatus.HISTORICAL,
})


def reachable_statuses(
    *, include_historical: bool = True, include_corrected: bool = False
) -> frozenset[NodeStatus]:
    """Which statuses a search may return, from T3's two reachability switches.

    **The asymmetry in the defaults is the decision.** Knowledge that is not
    current is still knowledge — that is the whole reason `HISTORICAL` exists, so
    it is on. A claim concluded *wrong* is kept for the audit trail rather than
    for reading, so re-offering it should be deliberate — `CORRECTED` is off.

    `CORRECTED` is reachable at all, rather than being walled off, for the
    principle applied everywhere else in this design: report and let the caller
    decide. *"What did we believe about X that turned out wrong?"* is a fair
    question for an epistemic memory, and under an unreachable version it can be
    answered only by already knowing the node id — an audit trail you must know
    the answer to consult.

    **Legacy `SUPERSEDED` rides with `include_corrected`.** Those rows do not
    record which of the two events they were, and `LINEAGE_EDGE_TYPES` already
    reads them as corrections on the grounds that `superseded_by` is what was
    written at the time. Putting them behind the cautious switch keeps the two
    readings of an unrecorded retirement from disagreeing.

    `MERGED` and `ARCHIVED` have no switch and are in neither set. A merged node
    is a husk whose content now lives on the survivor, so returning it duplicates
    a result; an archived node was deliberately set aside as not worth keeping,
    and `restore` is how it comes back.
    """
    statuses = {NodeStatus.ACTIVE}
    if include_historical:
        statuses.add(NodeStatus.HISTORICAL)
    if include_corrected:
        statuses |= {NodeStatus.CORRECTED, NodeStatus.SUPERSEDED}
    return frozenset(statuses)


# Why a node was superseded, as the caller states it. There is deliberately no
# default: the whole finding behind #53 is that the two cases are opposite and
# that picking either silently mislabels the other.
SUPERSESSION_REASONS: dict[str, NodeStatus] = {
    "it_was_wrong": NodeStatus.CORRECTED,
    "the_world_changed": NodeStatus.HISTORICAL,
}


def superseded_status_for(because: str) -> NodeStatus:
    """The status a supersession leaves behind, given the caller's reason.

    Spelled as sentences rather than as `corrected`/`historical` because the
    caller is usually a language model choosing between them, and the judgment
    it has to make is exactly the difference between those two sentences.
    """
    status = SUPERSESSION_REASONS.get(because)
    if status is None:
        raise ValueError(
            f"Unknown supersession reason {because!r}. "
            f"Expected one of: {', '.join(sorted(SUPERSESSION_REASONS))}."
        )
    return status


class EdgeType(str, Enum):
    # Segment anchoring
    ABOUT = "about"                  # segment → topic
    CONTAINS = "contains"            # segment → fact
    IMPLIES = "implies"              # segment → inference

    # Semantic hierarchy
    SUPPORTS = "supports"            # fact → topic, fact → inference
    ABSTRACTS = "abstracts"          # inference → topic
    DERIVED_FROM = "derived_from"    # inference → fact

    # Cross-node linking
    SIMILARITY = "similarity"        # topic ↔ topic, fact ↔ fact
    CONTRADICTION = "contradiction"  # fact ↔ fact

    # Topic hierarchy (DAG — multiple parents allowed, cycles forbidden)
    SUBTOPIC_OF = "subtopic_of"      # topic → parent topic

    # History
    SUPERSEDED_BY = "superseded_by"  # node → node (correction)
    # node → node (world-change). States temporal order, not replacement, which
    # is what lets a claim become true *again* without contradicting the edge
    # that recorded it stepping aside (#53). It deliberately does **not** claim
    # adjacency: Saint Petersburg → Petrograd → Leningrad → Saint Petersburg is
    # three separately observed transitions, and discovering a missing step
    # later must not make an existing edge wrong. So the chain is walkable but
    # not gapless, cycles are legal, and two transitions the same way round
    # between one pair are two edges — never dedup these by (src, dst, type).
    TEMPORALLY_FOLLOWED_BY = "temporally_followed_by"
    MERGED_INTO = "merged_into"      # node → node (merge)

    # Temporal
    TIMELINK = "timelink"                        # node → timeline (with timepoint_id in metadata)
    ASSOCIATED_TIMELINE = "associated_timeline"  # topic → timeline

    # Epistemic framing
    HAS_METACONTEXT = "has_metacontext"          # node → metacontext

    # Aboutness & provenance (sources/tags are nodes; these connect to them)
    TAGGED_WITH = "tagged_with"      # node → topic ("about / tagged with this concept")
    SOURCED_FROM = "sourced_from"    # node → RawDocument (originating document)

    # Epistemic review (see REVIEW_EPISTEMIC.md)
    SUPERSESSION_CANDIDATE = "supersession_candidate"  # newer fact → older fact
    EVIDENCE_SUPERSEDED = "evidence_superseded"        # superseded fact → dependent inference
    VARIANT_OF = "variant_of"                          # fact ↔ fact, across frames
    BASED_ON = "based_on"                              # metacontext → metacontext (association)

    # User-defined relationship (open vocabulary): the descriptor lives in
    # NodeEdge.label, behaviour in NodeEdge.kind. The engine routes on the enum;
    # all open relationships share this one sentinel.
    RELATED = "related"


# Edges that record version history rather than knowledge. They are anchored to
# a specific node version and are excluded from edge migration on supersession /
# merge, and from default graph traversal.
HISTORY_EDGE_TYPES: frozenset[EdgeType] = frozenset(
    {EdgeType.SUPERSEDED_BY, EdgeType.TEMPORALLY_FOLLOWED_BY, EdgeType.MERGED_INTO}
)

# Which lineage edge a retirement writes, given the status it leaves behind.
# The pair to `SUPERSESSION_REASONS`: that decides what the *node* becomes, this
# decides what the *edge* says, and the two must agree. They did not for a
# while — the status split shipped first, so a world-change left a node marked
# "still true of its period" reached by an edge saying it had been replaced.
LINEAGE_EDGE_TYPES: dict[NodeStatus, EdgeType] = {
    NodeStatus.CORRECTED: EdgeType.SUPERSEDED_BY,
    NodeStatus.HISTORICAL: EdgeType.TEMPORALLY_FOLLOWED_BY,
    # Pre-split rows do not record which event they were, and `superseded_by`
    # is what was written at the time. Reading them as the newer edge would
    # claim a distinction nobody drew.
    NodeStatus.SUPERSEDED: EdgeType.SUPERSEDED_BY,
}


def lineage_edge_type_for(status: NodeStatus) -> EdgeType:
    """The edge joining a node retired as `status` to what came after it.

    Refuses anything that is not a supersession, on the same grounds as
    `superseded_status_for`: a merge writes `merged_into` through its own path,
    and an active node has no successor at all. Answering for either would hand
    a caller a plausible edge for an event that did not happen.
    """
    edge_type = LINEAGE_EDGE_TYPES.get(status)
    if edge_type is None:
        raise ValueError(
            f"'{status.value}' is not a supersession, so there is no lineage "
            f"edge for it. Expected one of: "
            f"{', '.join(s.value for s in LINEAGE_EDGE_TYPES)}."
        )
    return edge_type


# Edges that flag a node for epistemic review. They are computed into retrieval
# labels (superseded_candidate / evidence_stale) rather than traversed as
# knowledge, and are anchored to a node version (not migrated on supersession).
REVIEW_EDGE_TYPES: frozenset[EdgeType] = frozenset(
    {EdgeType.SUPERSESSION_CANDIDATE, EdgeType.EVIDENCE_SUPERSEDED}
)

# Metadata / signal edges (history + review): excluded from edge migration on
# supersession/merge and from default graph traversal. Knowledge relationships
# such as `contradiction` and `variant_of` are NOT in this set — they are real
# edges to follow.
NON_KNOWLEDGE_EDGE_TYPES: frozenset[EdgeType] = HISTORY_EDGE_TYPES | REVIEW_EDGE_TYPES

# Edges anchoring a node to the segment it was extracted from. They record
# where a node came from, not that anything in the graph depends on it — every
# extracted node has exactly one, so counting them as structural support would
# make the count constant and meaningless.
SEGMENT_ANCHOR_EDGE_TYPES: frozenset[EdgeType] = frozenset(
    {EdgeType.ABOUT, EdgeType.CONTAINS, EdgeType.IMPLIES}
)

# Built-in edges pointing at a provenance/source hub. Excluded from default
# traversal (a search must not fan out into everything a source produced) but NOT
# from migration (a corrected node keeps its source).
PROVENANCE_EDGE_TYPES: frozenset[EdgeType] = frozenset({EdgeType.SOURCED_FROM})

# Behavioural kinds for user-tier (RELATED) edges. Open vocabulary lives in the
# label; the engine only reads the kind. `attribution` = where it came from / who
# said it (don't fan out from the hub); `relationship` = a real-world relation
# worth following.
RELATIONSHIP_KIND = "relationship"
ATTRIBUTION_KIND = "attribution"


def traversal_excluded(edge: "NodeEdge") -> bool:
    """True when default retrieval should NOT expand through this edge.

    Excludes history + review (graph bookkeeping) and provenance/attribution edges
    (don't fan out from a version/source hub). `tagged_with` and relationship-kind
    edges are followed, like `about`/`supports`.
    """
    if edge.type in NON_KNOWLEDGE_EDGE_TYPES or edge.type in PROVENANCE_EDGE_TYPES:
        return True
    return edge.type == EdgeType.RELATED and edge.kind == ATTRIBUTION_KIND


# Edges a world-change carries onto the replacement rather than leaving behind.
# A frame says *which world* a claim belongs to; a tag says what it is *about*.
# Neither asserts the claim, so both are as true of the replacement as of its
# predecessor — and dropping the frame would move a fiction-frame claim into
# base reality, which is the one thing CLAUDE.md forbids outright.
WORLD_CHANGE_COPIED_EDGE_TYPES: frozenset[EdgeType] = frozenset(
    {EdgeType.HAS_METACONTEXT, EdgeType.TAGGED_WITH}
)

# What happens to an edge when the node it touches is replaced.
EdgeDisposition = Literal["move", "copy", "keep"]


def migration_disposition(edge_type: EdgeType, status: NodeStatus) -> EdgeDisposition:
    """What becomes of a `edge_type` edge when its node is retired as `status`.

    `move` re-points it onto the replacement, `copy` leaves it and adds a second
    edge on the replacement, `keep` leaves it alone.

    **A correction moves everything** except history and review: the old node is
    an audit husk that does not need sources, and the replacement is the *same
    claim*, corrected, so what the claim was drawn from is genuinely its own.

    **A world-change moves nothing** (#54). The historical node is kept because
    it is still true of its period, and what makes it true of a period is its own
    provenance — with, once #53 lands, the validity intervals riding on those
    `sourced_from` edges. Moving them leaves it unable to say who asserted it or
    when it held. Copying them is not the alternative: a `sourced_from` edge on
    the replacement records the old claim's document asserting the *new* claim,
    which is fabricated attribution. The same argument covers knowledge edges —
    a contradiction or a variant is a judgment made *about the old claim*, and
    re-pointing one asserts it of a claim nobody assessed.

    Frames and tags are the exception, and copy: see
    `WORLD_CHANGE_COPIED_EDGE_TYPES`.
    """
    if edge_type in NON_KNOWLEDGE_EDGE_TYPES:
        return "keep"  # anchored to a specific node version
    if status is NodeStatus.HISTORICAL:
        return "copy" if edge_type in WORLD_CHANGE_COPIED_EDGE_TYPES else "keep"
    return "move"


def moved_edge_types(status: NodeStatus) -> frozenset[EdgeType]:
    """The edge types a retirement re-points, for backends that filter by type.

    Derived from `migration_disposition` rather than restated, so a backend
    cannot answer this question differently from the rest of the system.
    """
    return frozenset(
        t for t in EdgeType if migration_disposition(t, status) == "move"
    )

# Reserved id for the canonical base-reality frame ("The Real"). Matched by id,
# never by content, so a fiction frame that internally mentions "reality" is
# never confused with it. Untagged nodes are implicitly in this frame.
BASE_METACONTEXT_ID = "the-real"


# --- Value Signal ---


class ValueSignal(BaseModel):
    """Multi-dimensional value signal attached to every node.

    Two questions get asked of a node — "is this being used?" and "does this
    matter?" — and they are answered by different kinds of evidence. Use is a
    fact about events, so it is recorded as *when* they happened. Mattering is a
    judgment, so it is recorded as a number plus when someone last stood behind
    it.

    There used to be a third answer, a decaying `relevance` float, and it was
    removed rather than kept: nothing read it, and `retrieved_at` answers the
    same question better. A decayed number is confounded by how often an
    operator ran `reflect` — 0.3 might be "used once, long ago" or "used often,
    on a graph that reflects a lot" — so it described operator habit as much as
    the node. A timestamp separates *never* from *long ago* without that.

    A `novelty` float was removed for a related reason (#46). It was meant as
    how unlike existing graph state a node is, and it went not because nothing
    read it — though nothing did — but because the number cannot be stored
    honestly: measured at ingest it answers "unexpected relative to what the
    graph held *then*", which is a fact about arrival order, frozen at 1.0 for
    everything ever created. Asked at read time against the graph as it stands
    the question is well-posed, and the nearest-neighbour distance
    `vector_search` returns answers it without a field. `created_at` already
    carries the other sense the name kept collapsing into — how new a node is.

    Each clock's name says which mechanism writes it. One shared
    `last_reinforced` could not: retrieval is passive and automatic while a
    judgment is deliberate and rare, so a single field either conflated them
    or — as it did — silently recorded only the passive one under a name that
    read like the other.
    """
    # How well the record would back this claim up if it were challenged —
    # supplied by the ingesting agent, which is the only party that has read the
    # material, and never computed (#46). `None` is the unrated case and is
    # stored as absence: a deliberate middling 0.5 and a question nobody put
    # are different states, and a default that cannot express "never happened"
    # is the trap both clocks below were pulled out of. Read it through
    # `rated_confidence`, which supplies the 0.5 every consumer expects.
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    # Moved only by judgment — the `judge_importance` tool, or a prior supplied
    # at ingest. Nothing automatic touches it, which is the point. Unlike
    # `confidence` it keeps a real default: triviality is only visible once the
    # neighbourhood exists, so reflect can always go back and judge it, and
    # "unrated" is not a state anything would act on differently.
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    # Both are None until the thing they name actually happens. "Never
    # retrieved" and "never judged" are states worth distinguishing from
    # "happened just now", and a `now` default cannot express either: it made a
    # node nothing had ever touched look freshly used, which is why archival
    # nomination had to compare two clock reads with a tolerance window.
    retrieved_at: datetime | None = None
    importance_judged_at: datetime | None = None


UNRATED_CONFIDENCE = 0.5


def rated_confidence(confidence: float | None) -> float:
    """`confidence` as a number, reading the unrated case as the default.

    For code that must *rank or compare* nodes and has nowhere to put an
    absence: 0.5 is what the ladder in the tool guidance documents as "no
    specific reason to doubt or specially trust it", so an unrated node sorts
    where an unremarkable one would.

    Anything that *displays* or *relays* the number should pass `None` through
    instead — a rendered 0.5 claims an assessment nobody made. So should the
    merge rule, where an absence losing to a real value is the whole point.
    """
    return UNRATED_CONFIDENCE if confidence is None else confidence


def _latest(times: Iterable[datetime | None]) -> datetime | None:
    """The most recent of these, or `None` if none of them happened."""
    happened = [t for t in times if t is not None]
    return max(happened) if happened else None


def _highest(values: Iterable[float | None]) -> float | None:
    """The greatest of these, or `None` if none of them was ever set."""
    rated = [v for v in values if v is not None]
    return max(rated) if rated else None


def merged_value_signal(signals: Sequence[ValueSignal]) -> ValueSignal:
    """Combine the signals of nodes being collapsed into one replacement.

    Lives here, shared by both merge sites, because a merge builds a *fresh*
    node: a field-by-field rebuild silently resets every field it forgets to
    name, and the two sites forgot different things at different times. One
    function means a field added to `ValueSignal` has exactly one place to be
    considered rather than two places to be missed.

    Each field is combined by what it means:

    - `importance` takes the max — a judgment made about either source still
      applies to what replaces them, so collapsing topics must not discard one.
    - **Both clocks take the latest**, with `None` meaning "never" and so losing
      to any real timestamp. Carrying `importance` across without the date it
      was judged is not a lost timestamp but a false pair: the merged node claims
      a judgment nobody ever made, and `judgment_is_stale` reads the pair, so an
      unjudged node is never stale and the merged node stays exempt from every
      archival class permanently (#45). The same argument holds for retrieval —
      knowledge that has been retrieved does not become unretrieved by being
      merged.
    - `confidence` takes the max — which looks wrong for a caller-supplied
      prior, since the more credulous assessment wins and the disagreement
      disappears. It is right because of what it pairs with: `merge_similar_topics`
      makes the **higher-confidence description the primary content**, so the
      merged node's confidence describes the content it actually leads with.
      The two rules are one rule read from either end, and changing either
      alone makes the merged node claim a strength for text it no longer
      leads with. An unrated signal takes no part: `None` means nobody put the
      question, so it loses to any real value the way the clocks do, and a
      merge of unrated nodes stays unrated rather than inventing a judgment.

    Requires at least one signal; merging nothing has no meaning.
    """
    if not signals:
        raise ValueError("merged_value_signal requires at least one signal")
    return ValueSignal(
        confidence=_highest(s.confidence for s in signals),
        importance=max(s.importance for s in signals),
        retrieved_at=_latest(s.retrieved_at for s in signals),
        importance_judged_at=_latest(s.importance_judged_at for s in signals),
    )


# --- Documents and Segments ---


class RawDocument(BaseModel):
    """Input text before any processing."""
    id: str = Field(default_factory=_new_id)
    content: str
    source: str | None = None         # human-meaningful origin, e.g. "ISSUES.md"
    source_type: str | None = None    # free string; suggested: document|api|chat
    # When the document was published, as against `created_at` below, which is
    # when it was ingested — a 1970 memoir read today carries `created_at =
    # 2026`. It bounds what this source could have known, and it is what anyone
    # would sort or weigh sources by (#53 T1 §7).
    #
    # **Never fall back to `created_at`.** The fallback is the bug it exists to
    # fix, with an extra step: every undated document would claim its facts were
    # witnessed on the day it happened to be ingested, and a graph rebuilt next
    # year would say something else. No publication date means no witness point.
    published_at: ImpreciseInstant | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


class Segment(BaseModel):
    """A non-overlapping section of a document aligned to semantic boundaries."""
    id: str = Field(default_factory=_new_id)
    source_id: str                    # RawDocument.id
    text: str
    span_start: int                   # character offset in source
    span_end: int                     # character offset in source
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


# --- Epistemic Nodes ---


class LifecycleEpisode(BaseModel):
    """One spell a node spent out of the active set, and the return that ended it.

    `(status, superseded_at)` is a single slot, and a node can leave the active
    set more than once — #53 T2 legalised that, and Saint Petersburg is the
    standing example. The pair cannot say *retired, then came back*: clear
    `superseded_at` on the return and the retirement disappears from every time
    window; keep it and the retirement reports the node's *current* status, which
    by then is `active`. A scalar `restored_at` only defers the same overwrite to
    the second retirement.

    So the history is a list and the list is append-only. A retirement appends an
    episode; a return closes the open one. Nothing is ever cleared or
    overwritten, and `(status, superseded_at)` stay on the node as the
    current-state snapshot the fast paths read.

    `because` is the status the node took, reusing `NodeStatus` rather than
    minting a second vocabulary for the same thing (`ACTIVE` is not a legal
    value — a node does not retire into being active). `counterpart` is the node
    that replaced, followed or absorbed this one, and is `None` where nothing
    did: archival retires a node without anything superseding it.
    """
    retired_at: datetime
    because: NodeStatus
    counterpart: str | None = None
    restored_at: datetime | None = None


def with_retirement(
    episodes: Sequence[LifecycleEpisode],
    *,
    at: datetime,
    because: NodeStatus,
    counterpart: str | None = None,
) -> list[LifecycleEpisode]:
    """`episodes` plus the retirement that just happened. Never mutates its input."""
    return [
        *episodes,
        LifecycleEpisode(retired_at=at, because=because, counterpart=counterpart),
    ]


def with_return(
    episodes: Sequence[LifecycleEpisode], *, at: datetime,
) -> list[LifecycleEpisode]:
    """`episodes` with the open one closed at `at`.

    A no-op when there is nothing open: a node retired before episodes existed
    has no episode to close, and old graphs are not repaired retroactively
    (ISSUES.md → *Older carry-overs*). Closing is the only edit an episode ever
    receives, and it happens once.
    """
    if not episodes or episodes[-1].restored_at is not None:
        return list(episodes)
    return [*episodes[:-1], episodes[-1].model_copy(update={"restored_at": at})]


class Topic(BaseModel):
    """Paragraph-length semantic summary of a theme.

    Acts as a soft ontological node — embeds well, supports clustering,
    and can evolve over time.
    """
    id: str = Field(default_factory=_new_id)
    content: str                      # paragraph-level description
    source_id: str | None = None      # Segment.id, if extracted from text (entity/tag topics have none)
    status: NodeStatus = NodeStatus.ACTIVE
    superseded_at: datetime | None = None
    # Every spell this node has spent out of the active set. Append-only; the
    # two fields above are the current-state snapshot of the last entry.
    lifecycle: list[LifecycleEpisode] = Field(default_factory=list)
    value: ValueSignal = Field(default_factory=ValueSignal)
    extraction_method: str = "unspecified"
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


class Fact(BaseModel):
    """Atomic, verifiable, grounded statement.

    Tied to source material with minimal ambiguity.
    """
    id: str = Field(default_factory=_new_id)
    content: str
    source_id: str                    # Segment.id that generated this
    status: NodeStatus = NodeStatus.ACTIVE
    superseded_at: datetime | None = None
    # Every spell this node has spent out of the active set. Append-only; the
    # two fields above are the current-state snapshot of the last entry.
    lifecycle: list[LifecycleEpisode] = Field(default_factory=list)
    value: ValueSignal = Field(default_factory=ValueSignal)
    extraction_method: str = "unspecified"
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


class Inference(BaseModel):
    """Higher-level interpretive derivation from facts and context.

    Explicitly provisional and revisable. Multiple competing inferences
    from the same evidence are permitted to coexist.
    """
    id: str = Field(default_factory=_new_id)
    content: str
    source_id: str                    # Segment.id that generated this
    status: NodeStatus = NodeStatus.ACTIVE
    superseded_at: datetime | None = None
    # Every spell this node has spent out of the active set. Append-only; the
    # two fields above are the current-state snapshot of the last entry.
    lifecycle: list[LifecycleEpisode] = Field(default_factory=list)
    value: ValueSignal = Field(default_factory=ValueSignal)
    extraction_method: str = "unspecified"
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


# Union of all epistemic node types
EpistemicNode = Topic | Fact | Inference


class NodeChangeEvent(BaseModel):
    """A lifecycle event on a node that falls inside a queried time window.

    Emitted by temporal change queries: `created` when the node was born in the
    window, `restored` when it came back, and otherwise the status the
    retirement gave it, since that is exactly what the retirement was.
    `corrected` and `historical` are the two halves of the old `superseded`,
    which survives only on rows written before #53. A node both born and retired
    inside one window yields two events.

    `counterpart` is the node that replaced, followed or absorbed this one — the
    "by whom" of a supersession (#57). It is `None` for births, returns, and
    retirements that had no counterpart (archival) or predate episodes.
    """
    kind: Literal[
        "created", "superseded", "corrected", "historical", "merged",
        "archived", "restored",
    ]
    at: datetime
    counterpart: str | None = None


# --- Edges ---


class NodeEdge(BaseModel):
    """A typed, weighted, directed edge between two nodes.

    For engine-tier edges, `type` is a known EdgeType and `label`/`kind` are unused.
    For user-tier relationships, `type` is `RELATED`, `label` holds the open
    descriptor (e.g. "published_by"), and `kind` selects behaviour
    (`relationship` follows in retrieval; `attribution` does not).
    """
    id: str = Field(default_factory=_new_id)
    src_id: str
    dst_id: str
    type: EdgeType
    label: str | None = None
    kind: Literal["relationship", "attribution"] = "relationship"
    weight: float = Field(default=1.0, ge=0.0)
    # When *this source* asserts the claim was true (#53 T1 §2). A list, because
    # one source can assert several disjoint periods — a party in government
    # over five separate spans is one claim, not five.
    #
    # Per source rather than per node, and that is the whole decision. A
    # node-level set has to union what its sources assert, and union takes one
    # careful source and one sloppy one and produces a period **neither
    # claims** — the same failure as a false dedup manufacturing corroboration.
    # Living here also means intervals survive a merge for free, since merging
    # migrates edges and there is no combination rule to invent.
    validity: list[ValidityInterval] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _validity_needs_a_source(self) -> "NodeEdge":
        """Only a provenance edge can carry validity.

        An interval is what a source asserts, so it has to hang off the edge
        naming that source. On a `similarity` or `tagged_with` edge it would be
        a period attributed to nobody — unfalsifiable, unmergeable, and exactly
        the node-level set this design rejected, reached by accident.
        """
        if self.validity and self.type not in PROVENANCE_EDGE_TYPES:
            raise ValueError(
                f"a '{self.type.value}' edge cannot carry validity intervals: an "
                f"interval is one source's assertion and belongs on the edge "
                f"naming that source. Expected one of: "
                f"{', '.join(sorted(t.value for t in PROVENANCE_EDGE_TYPES))}."
            )
        return self


# --- Embeddings ---


class EmbeddingRecord(BaseModel):
    """An embedding vector associated with a specific item and model."""
    id: str = Field(default_factory=_new_id)
    item_id: str                      # node or segment id
    model_id: str                     # e.g. "all-mpnet-base-v2"
    vector: list[float]
    created_at: datetime = Field(default_factory=_now)


# --- Timelines ---


class Timepoint(BaseModel):
    """A point or interval on a timeline.

    Can be concrete (with start/end datetimes), vague (label only),
    or a mix (concrete start with descriptive label).
    """
    id: str = Field(default_factory=_new_id)
    start: datetime | None = None     # concrete start (optional)
    end: datetime | None = None       # concrete end (optional, for intervals)
    label: str | None = None          # free-text (e.g., "during the Renaissance")
    metadata: dict = Field(default_factory=dict)


class Timeline(BaseModel):
    """An ordered container of timepoints.

    Timepoints are embedded within the timeline (not separate graph nodes).
    Other nodes link to specific timepoints via TIMELINK edges that carry
    a timepoint_id in metadata.
    """
    id: str = Field(default_factory=_new_id)
    name: str
    description: str = ""
    timepoints: list[Timepoint] = Field(default_factory=list)
    # This timeline's "now" — the instant a viewer should be centred on and
    # measure "past" and "future" against. A fictional timeline's present is a
    # fact about that world ("the novel opens in May 1897"), not a viewer
    # preference, so it lives here rather than in a browser.
    #
    # `None` means *follow the wall clock*, and is deliberately distinct from
    # storing the current instant at creation: a real-world timeline whose
    # present was frozen at the moment it was first written would drift further
    # out of date every day it was used.
    reference_time: datetime | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


# --- Metacontext ---


class Metacontext(BaseModel):
    """Epistemic frame for disambiguation.

    Metacontexts distinguish different takes, sources, or interpretations
    of the same information. For example, "Real historical events" vs.
    "World of Darkness fictional universe" vs. "Reporting by the BBC".

    Has value signals and status like epistemic nodes — supports
    consolidation/merge during reflection.
    """
    id: str = Field(default_factory=_new_id)
    content: str                      # e.g., "Real historical events"
    description: str = ""             # longer explanation
    status: NodeStatus = NodeStatus.ACTIVE
    superseded_at: datetime | None = None
    value: ValueSignal = Field(default_factory=ValueSignal)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
