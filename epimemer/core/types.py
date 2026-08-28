"""Core Pydantic models for the epistemic memory system.

These types serve double duty:
1. Storage schema — serialized to/from the database
2. Petri net tokens — flow through processing pipelines
"""

import hashlib
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


class ClaimKind(str, Enum):
    """Whether a fact describes a condition that holds, or an occurrence.

    The distinction fact dedup cannot be built without (#52). Under the validity
    model two ingests of one claim over separate periods are one node whose
    intervals union — correct for a **state**, and fabricated history for an
    **event**. *"Labour is in government"* read from a 1997 document and from a
    2024 one is one condition holding over two spans, and the union is exactly
    right. *"Labour won the election"* read from those same two documents is two
    victories, and the union is a single win spanning twenty-seven years that
    neither source claims.

    Nothing already in the model separates them. The two sentences are
    near-identical as text and therefore near-identical as embeddings, so
    similarity nominates them equally; and the verdict taxonomy asks *what is the
    relationship between these two claims* rather than *what kind of thing is
    being claimed*, which is a different question that happens to decide this one.

    **Judged at ingest, and effectively only there.** The judgment wants the
    document — the tense, the sentences either side, whether "the election" is a
    particular one — and a merge is offered two stripped sentences with none of
    that. Same argument that put `confidence` and `validity` at ingest (#46,
    #53 T1 §9). What it costs is that a claim nobody judged stays unjudged; see
    `Fact.claim_kind`, where an absence refuses rather than guesses.

    Two members, not three. A unique occurrence — *"Napoleon was born in
    1769"* — is an `EVENT` that could in principle be deduped safely, and
    refusing it is a real cost knowingly taken: separating "can happen twice"
    from "happened once" is a third judgment to get wrong, and its error
    direction is the unsafe one.
    """

    STATE = "state"
    EVENT = "event"


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
    # merged fact → dependent inference. Its own type rather than a qualified
    # `evidence_superseded`, because the two events say opposite things about
    # the claim: a correction says it was wrong, a merge says two phrasings of
    # it collapsed and the survivor carries every source (#61). Consumers route
    # on the type — labels, archival, migration all do — so a reader that has
    # never heard of this one sees an edge it does not handle rather than a
    # familiar edge that has quietly grown a second meaning.
    EVIDENCE_MERGED = "evidence_merged"
    # fact ↔ fact: *somebody has judged this pair*, whichever way it went. Its
    # own type rather than a flag on `similarity`, because the two have readers
    # wanting opposite breadth: nomination wants every pair anybody assessed
    # suppressed, corroboration wants only restatements of one claim. One edge
    # serving both makes "these are different claims" corroborate — which is
    # manufactured support, the worst failure this system has (#64 §1.2).
    ASSESSED = "assessed"
    # fact ↔ fact: an earlier `one_claim` verdict about this pair has been
    # **withdrawn** (#68). Written only where one stands, because that is the
    # only place it does anything: `similarity` is not deleted — nothing here
    # deletes — so this is what stops it corroborating, exactly as
    # `contradiction` already does for a pair judged the other way.
    #
    # A retraction is **terminal**, and the asymmetry is deliberate. Nothing
    # re-asserts `one_claim` over one, because the two directions fail
    # differently: a false unification manufactures agreement, which is the
    # worst thing this system can produce (`fact_dedup.py`), while a withdrawn
    # one under-counts. Under-counting is the direction #52 already chose when
    # it left the pre-`claim_kind` corpus unmergeable.
    RETRACTED_SIMILARITY = "retracted_similarity"
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


# Edges that record epistemic review rather than knowledge. The operative
# property is that they are **anchored to a node version** — never migrated on
# supersession or merge, because each records what happened to, or was decided
# about, *that* wording — and never traversed as knowledge.
#
# Three of them are also computed into retrieval labels
# (superseded_candidate / evidence_stale / evidence_merged). `assessed` is the
# first member with no label at all: nothing downstream should treat "a pair was
# looked at" as a flag on either node. It is read by one caller, the nomination
# sweep, and read there as a suppression index (#64 §1.2).
REVIEW_EDGE_TYPES: frozenset[EdgeType] = frozenset(
    {
        EdgeType.SUPERSESSION_CANDIDATE,
        EdgeType.EVIDENCE_SUPERSEDED,
        EdgeType.EVIDENCE_MERGED,
        EdgeType.ASSESSED,
        # Here rather than in `JUDGMENT_EDGE_TYPES` for `assessed`'s reason: it
        # needs the same anchoring *and* exclusion from traversal, being a
        # record about a judgment rather than a claim about the world.
        EdgeType.RETRACTED_SIMILARITY,
    }
)

# Metadata / signal edges (history + review): excluded from edge migration on
# supersession/merge and from default graph traversal. Knowledge relationships
# such as `contradiction` and `variant_of` are NOT in this set — they are real
# edges to follow. They are excluded from migration all the same, by
# `JUDGMENT_EDGE_TYPES` below: traversal and migration are separate questions
# and these two types answer them differently.
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


# Judgments one node carries about another. A judgment is made against the
# wording it was shown, so re-pointing one onto a replacement asserts it of a
# claim nobody assessed — the argument `migration_disposition` already makes for
# a world-change, and just as true of a correction and of a merge. Anchored to
# the node version that was judged, on **every** retirement.
#
# The costs are asymmetric, which is what settles it. Anchoring costs one
# re-nomination: the replacement against the same counterpart is a pair nobody
# has judged, and saying so is correct. Migrating can manufacture corroboration
# in silence — a false unification does not lose information, it inverts the
# quantity corroboration measures (`fact_dedup.py`).
#
# Not in `NON_KNOWLEDGE_EDGE_TYPES`, deliberately: these stay traversable. This
# set answers what a *retirement* does to them, not what a *search* does.
#
# `assessed` (#64 step 1) is a judgment too, but belongs in `REVIEW_EDGE_TYPES`
# rather than here: it needs the same anchoring *and* exclusion from traversal,
# being a suppression index rather than knowledge. Listing it in both would be
# redundant — `NON_KNOWLEDGE_EDGE_TYPES` is consulted first.
JUDGMENT_EDGE_TYPES: frozenset[EdgeType] = frozenset(
    {EdgeType.SIMILARITY, EdgeType.CONTRADICTION, EdgeType.VARIANT_OF}
)

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

    **A correction moves everything** except history, review and judgments: the
    old node is an audit husk that does not need sources, and the replacement is
    the *same claim*, corrected, so what the claim was drawn from is genuinely
    its own.

    **No retirement moves a judgment** (#65) — the one rule here that does not
    vary by status, see `JUDGMENT_EDGE_TYPES`. A correction is the case that
    makes it necessary rather than merely tidy: "the population is 500,000"
    corrected to "5,000,000" is the same claim by this module's own reckoning,
    so the sources follow it, but a counterpart judged *one claim* against the
    old figure was judged against a number that is no longer there. Carrying the
    edge would count that counterpart's publisher as backing the new figure. A
    merge is the same shape reached differently: the survivor's content is
    *synthesised*, so it is not the wording any judgment was made against
    either.

    **A world-change moves nothing** (#54). The historical node is kept because
    it is still true of its period, and what makes it true of a period is its own
    provenance — with, once #53 lands, the validity intervals riding on those
    `sourced_from` edges. Moving them leaves it unable to say who asserted it or
    when it held. Copying them is not the alternative: a `sourced_from` edge on
    the replacement records the old claim's document asserting the *new* claim,
    which is fabricated attribution. The same argument covers knowledge edges —
    a contradiction or a variant is a judgment made *about the old claim*, and
    re-pointing one asserts it of a claim nobody assessed. That half of it is
    not confined to this status; see above.

    Frames and tags are the exception, and copy: see
    `WORLD_CHANGE_COPIED_EDGE_TYPES`.

    **A merge does not move the frame** (#76). Every other edge on a survivor is
    something its sources genuinely brought with them, but a frame is a claim
    about which world this is — and the survivor's content is *synthesised*, so
    nobody has yet said which world the synthesised wording is about. Moving the
    edge would answer for them, attributed to whoever framed a source. The
    merging agent re-states it instead, under its own judge: merging is not
    coining, one layer up from `describe_relation`'s version of the same rule.
    A correction still moves it — there the replacement is the same claim.
    """
    if edge_type in NON_KNOWLEDGE_EDGE_TYPES:
        return "keep"  # anchored to a specific node version
    if edge_type in JUDGMENT_EDGE_TYPES:
        return "keep"  # anchored to the wording that was judged (#65)
    if status is NodeStatus.HISTORICAL:
        return "copy" if edge_type in WORLD_CHANGE_COPIED_EDGE_TYPES else "keep"
    if status is NodeStatus.MERGED and edge_type is EdgeType.HAS_METACONTEXT:
        return "keep"  # re-stated by the merger, never inherited (#76)
    return "move"


def moved_edge_types(status: NodeStatus) -> frozenset[EdgeType]:
    """The edge types a retirement re-points, for backends that filter by type.

    Derived from `migration_disposition` rather than restated, so a backend
    cannot answer this question differently from the rest of the system.
    """
    return frozenset(
        t for t in EdgeType if migration_disposition(t, status) == "move"
    )

# The conventional id for the frame holding claims about the real world. A
# **convention, not a mechanism**: it is an ordinary metacontext that must exist
# like any other, and nothing in the system reads it specially. Named here so
# that every graph uses the same string for the same frame rather than half of
# them saying "reality" and half "real-world", which would leave two frames
# nothing ever compares.
BASE_METACONTEXT_ID = "the-real"

# The frame a declaration sweep stamps on a graph nobody is prepared to vouch
# for. **No agent may write it**: `store_decomposition` refuses it by name, and
# only `epimemer frames declare` puts it on anything. That asymmetry is the
# whole point — a frame an agent could assert into is a frame that stops meaning
# *nobody has vouched for this*, and becomes untagged again under a new name.
QUARANTINE_METACONTEXT_ID = "unvouched"


# --- Who decided (REVIEW_MODE.md §3) ---


class JudgeRef(BaseModel):
    """Who is deciding, resolved once at the MCP boundary (§3.2, §10.4).

    Passed explicitly from there on and never read from a module global, so a
    second graph or a second session cannot inherit the first one's judge. The
    digest pins the description version current at the call, which is what makes
    a decision readable years later without an as-of query.

    **Absent means unknown, and nothing more** (§3.3, revised 2026-08-23). It
    carries no date and asserts nothing about why nobody is named — a graph may
    require a judge or not, and that is a per-graph setting rather than a
    property of this type.

    Two fields rather than the design's separate `judged_by` / `judge_desc`
    columns, because the pair is never meaningful apart: an agent id without the
    description version says *who* but not *what they claimed to be at the
    time*, which is the half that makes an old decision readable. Kept nested
    wherever it is recorded, so no carrier has to remember two field names.

    It sits here, above every type that carries one, rather than beside `Agent`
    at the end of this module: the registry is one subsystem, and this is a
    field on nodes, edges, episodes and value signals.
    """

    agent_id: str
    digest: str


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
    # Who last stood behind `importance`, paired with the clock above rather
    # than standing alone: the two answer *when* and *who* about one judgment
    # and are written together or not at all. Named for `importance`
    # specifically because `confidence` is a different judgment, made at a
    # different moment by possibly a different agent, and will want its own.
    importance_judged_by: JudgeRef | None = None


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
      disappears. **The two callers reach it by different routes, and both are
      recorded here because one of them used to be**: `merge_similar_topics`
      makes the **higher-confidence description the primary content**, so the
      merged topic's confidence describes the text it actually leads with —
      one rule read from either end, and changing either alone makes the node
      claim a strength for wording it no longer leads with. `merge_facts` (#52)
      does *not* work that way: the survivor's content is written fresh by the
      agent, so nothing is inherited to lead with. Max is still right there, for
      the field's own definition (#46) — confidence asks *how well would the
      record back this claim up*, and a survivor keeping one `sourced_from` edge
      per contributing document is backed by every one of them, so the
      best-supported rating is a floor rather than a ceiling. `merged_confidence_basis`
      carries that rating's stated reason across with it, or the prior arrives
      stripped of the reason #46 asks for. An unrated signal takes no part:
      `None` means nobody put the question, so it loses to any real value the
      way the clocks do, and a merge of unrated nodes stays unrated rather than
      inventing a judgment.

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
    # Retiring and returning are two decisions, often months and sometimes two
    # agents apart, so they carry two judges rather than one. `restored_by` is
    # written by the same single edit that writes `restored_at` — the only edit
    # an episode ever receives.
    retired_by: JudgeRef | None = None
    restored_by: JudgeRef | None = None


def with_retirement(
    episodes: Sequence[LifecycleEpisode],
    *,
    at: datetime,
    because: NodeStatus,
    counterpart: str | None = None,
    judge: "JudgeRef | None" = None,
) -> list[LifecycleEpisode]:
    """`episodes` plus the retirement that just happened. Never mutates its input."""
    return [
        *episodes,
        LifecycleEpisode(
            retired_at=at, because=because, counterpart=counterpart,
            retired_by=judge,
        ),
    ]


def with_return(
    episodes: Sequence[LifecycleEpisode],
    *,
    at: datetime,
    judge: "JudgeRef | None" = None,
) -> list[LifecycleEpisode]:
    """`episodes` with the open one closed at `at`.

    A no-op when there is nothing open: a node retired before episodes existed
    has no episode to close, and old graphs are not repaired retroactively
    (ISSUES.md → *Older carry-overs*). Closing is the only edit an episode ever
    receives, and it happens once.
    """
    if not episodes or episodes[-1].restored_at is not None:
        return list(episodes)
    return [
        *episodes[:-1],
        episodes[-1].model_copy(update={"restored_at": at, "restored_by": judge}),
    ]


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
    # Who wrote this wording — the agent that read the material at ingest, or
    # the one that synthesised it during reflect. Fixed at creation and never
    # edited: a new version is a new node, which gets its own (§3.4).
    judged_by: JudgeRef | None = None
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
    # Condition or occurrence, judged by the ingesting agent (#52). `None` is
    # *unjudged* — the state every fact written before this field existed is in
    # — and never a third kind of claim. Dedup refuses on it rather than picking
    # a side: the two answers have opposite consequences, and the safe direction
    # is to under-merge, since a missed merge only undercounts while a false one
    # manufactures corroboration out of two distinct claims.
    #
    # On facts alone. A topic is a theme rather than a claim, so neither answer
    # is about it. Inferences are provisional by design and competing ones are
    # meant to coexist, so nothing here gates them today — and when inference
    # merge lands (`dev-docs/WARNINGS_AND_SETTINGS.md` §6) it will not want this
    # field either: it is nominated on *shared evidence* and warned by
    # `assertions_are_disjoint` over its premises' periods, which is the same
    # hazard answered where the dates actually are.
    claim_kind: ClaimKind | None = None
    status: NodeStatus = NodeStatus.ACTIVE
    superseded_at: datetime | None = None
    # Every spell this node has spent out of the active set. Append-only; the
    # two fields above are the current-state snapshot of the last entry.
    lifecycle: list[LifecycleEpisode] = Field(default_factory=list)
    value: ValueSignal = Field(default_factory=ValueSignal)
    # Who wrote this wording — the agent that read the material at ingest, or
    # the one that synthesised it during reflect. Fixed at creation and never
    # edited: a new version is a new node, which gets its own (§3.4).
    judged_by: JudgeRef | None = None
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
    # Who wrote this wording — the agent that read the material at ingest, or
    # the one that synthesised it during reflect. Fixed at creation and never
    # edited: a new version is a new node, which gets its own (§3.4).
    judged_by: JudgeRef | None = None
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
    # Who asserted this edge. A first-class field rather than a `metadata` key,
    # which is where the design put it: metadata is a free-form bag, and a
    # reader asking *who decided this* would have to know a string. Every edge
    # can carry one — a judgment edge because somebody judged, a provenance edge
    # because somebody read the document (step 4).
    judged_by: JudgeRef | None = None
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


# --- Merge undo (REVIEW_MODE.md §7) ---

# `metadata` key the payload lives under, on the merge survivor. Not a typed
# field: `Topic`, `Fact` and `Inference` share no base class, so a field would
# have to be declared three times for a payload only merge survivors ever carry
# — and `metadata` already holds `merged_from` beside it.
MERGE_UNDO_KEY = "merge_undo"

# How far back along a `merged_into` chain the partitions are retained. Ten
# levels of one lineage stay reversible; the eleventh and beyond become
# permanent. Not aimed at storage — a merge retires its sources rather than
# deleting them, so what it already keeps and never reclaims (~3.4 KB of husk
# and vector) dwarfs the payload it adds (~190 B). It bounds the one claim that
# keeps absorbing restatements, whose history otherwise grows without limit.
DEFAULT_MERGE_UNDO_DEPTH = 10

# How many completed merge/reverse cycles a fact may have behind it before the
# next merge refuses. One is an ordinary correction; two can be two judges
# disagreeing; the third attempt is an oscillation nobody wants, and an agent
# can run it indefinitely without noticing.
DEFAULT_MERGE_CYCLE_LIMIT = 2


def completed_merge_cycles(node: "EpistemicNode") -> int:
    """How many times this node has been merged and then brought back.

    **The signal needs no new storage**, which is most of the case for having
    it: every merge appends a `LifecycleEpisode` with `because: MERGED`, every
    reversal closes that episode with `restored_at`, and the list is append-only
    and never trimmed. So one completed cycle leaves one closed `merged`
    episode, permanently.

    Counted per node rather than per pair. Pair matching would miss `A+B`, then
    `A+C`, then `A+D` — one node oscillating against different partners — on
    data that is there either way.
    """
    return sum(
        1 for episode in node.lifecycle
        if episode.because is NodeStatus.MERGED and episode.restored_at is not None
    )


class MergedEdge(BaseModel):
    """One edge exactly as it stood before a merge moved it.

    **The whole edge, field by field, and never a hand-listed subset.** Built
    with `edge.model_dump(exclude={"id"})`, so a field added to `NodeEdge` later
    is carried without anyone remembering to come back here. A hand-listed
    subset was the first design and it omitted `metadata` and `created_at` —
    which is where a judgment's `judged_by` will live, so a merge/reverse cycle
    would have replayed every edge with its attribution stripped.

    Values rather than references, because migration collapses duplicates by
    `(src, dst, type)`: the row this was taken from may not exist afterwards.
    """
    owner_id: str            # the merging source this edge belonged to
    edge: dict               # NodeEdge.model_dump(exclude={"id"})
    # True when *both* endpoints were merging, which makes the edge a self-loop
    # the migration drops outright rather than re-points. It is the only class
    # that is gone immediately rather than moved, so it is the only one a
    # reversal must recreate from nothing.
    intra_set: bool = False


class MergeUndo(BaseModel):
    """Everything needed to replay one merge backwards.

    Carried on the survivor rather than in a graph-wide log, which is what makes
    it self-maintaining: archive the survivor and the payload goes with it, no
    dangling promise and no buffer pinning nodes against the graph's own
    cleanup. A session of N merges touches N *distinct* survivors, one entry
    each, so a per-node bound evicts nothing a global ring of the same size
    would have kept.
    """
    source_ids: list[str]
    edges: list[MergedEdge] = Field(default_factory=list)
    merged_at: datetime
    decision_id: str | None = None      # the DecisionRecord, once §4 exists
    # The survivor's wording, kept because reversal *deletes* the node holding
    # it. Without this a reversal cannot say what it withdrew, and the contested
    # text stops being quotable the moment the reversal lands.
    survivor_content: str = ""


def read_merge_undo(node: "EpistemicNode") -> MergeUndo | None:
    """The merge payload on `node`, or None if it carries none.

    None is genuinely ambiguous here and the caller has to disambiguate it:
    the node was never a merge survivor, or its payload aged past the depth
    bound. One is permanent and the other is a mistake, and a reversal refusing
    must say which — `metadata["merged_from"]` is what tells them apart, since
    eviction clears the payload and never the lineage.
    """
    raw = node.metadata.get(MERGE_UNDO_KEY)
    return None if raw is None else MergeUndo.model_validate(raw)


def with_merge_undo(metadata: dict, undo: MergeUndo | None) -> dict:
    """`metadata` with the merge payload set, or cleared when `undo` is None.

    Never mutates its input, matching `with_retirement` / `with_return`.
    """
    rest = {k: v for k, v in metadata.items() if k != MERGE_UNDO_KEY}
    return rest if undo is None else {**rest, MERGE_UNDO_KEY: undo.model_dump(mode="json")}


# --- Embeddings ---


class EmbeddingRecord(BaseModel):
    """An embedding vector associated with a specific item and model."""
    id: str = Field(default_factory=_new_id)
    # A node id, in practice always. This was written as "node or segment id",
    # but no path writes a segment: all 624 records across the real graphs point
    # at nodes, and `vector_search` resolves every hit through `get_node`, so a
    # segment record would be fetched and dropped (#59, measured 2026-08-21).
    # Segments reach retrieval through BM25 instead, which indexes the whole
    # field — that is why they answer *where did I read that?* well.
    #
    # **That absence is what keeps the 256 word-piece window off them.** 11.1%
    # of real segment text crosses it and the worst loses 48%, so embedding
    # segments would make a silent truncation real on the day it was added. It
    # is a precondition of doing so, not a detail — ISSUES.md #59 carries it.
    item_id: str
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


class RelationLabel(BaseModel):
    """The vocabulary entry behind a user-tier edge's `label` (#74).

    A relationship label used to exist **nowhere**: `list_relations` derived the
    vocabulary by scanning edges and grouping by `(label, kind)`, so there was no
    row, no id and no description — just a string repeated on every edge
    carrying it. Three things follow from that, and they are the three open
    questions about relations: nothing to describe, so an agent choosing a label
    sees words with counts and no way to learn what this graph means by each;
    nothing to name in a decision, so a judgment about a label had no
    `subject_ids` to put it under; and nothing to change but the edges, so
    "renaming" meant an irreversible bulk rewrite.

    **Not a node, deliberately.** A label is vocabulary, not knowledge, and a
    node enters search, embeddings, reflection and merging — every one of which
    would then be answering questions about the *words* the graph uses.
    `Metacontext` is the precedent and the shape: a named, described thing that
    lives beside the graph rather than in it.

    **The name is the join key and does not move.** `NodeEdge.label` keeps its
    string, so renaming would break the join unless every edge were rewritten —
    which is the bulk relabel this design exists to stop needing. Renaming is
    therefore not supported, and if it is ever built its history belongs here,
    one entry per rename rather than one per edge, since that survives a rename
    that touched zero edges.
    """

    id: str = Field(default_factory=_new_id)
    # What edges actually carry. The join key to `NodeEdge.label`, which keeps
    # its string — edges are not re-pointed at ids.
    name: str
    kind: Literal["relationship", "attribution"] = "relationship"
    # Advisory prose an agent reads before coining. Empty means **undescribed**,
    # which is a true and useful state, and is why this field exists before
    # anything writes it.
    description: str = ""
    # **The coiner, and never the describer.** A later agent may describe this
    # label, judge it against another, or deprecate it, and none of that
    # restamps this field — those are journalled in their own right. A record
    # created by anything other than `link` carries no judge at all, because
    # nobody is claiming to have introduced the label.
    judged_by: JudgeRef | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


def recorded_relation_label(
    existing: RelationLabel | None, incoming: RelationLabel
) -> RelationLabel:
    """What to store for `incoming`, given whatever `(name, kind)` already holds.

    **The identity survives the write, and that is the whole point of the
    record.** A caller that constructs a fresh `RelationLabel` for a label that
    already has one would otherwise mint a new id on top of the old, and a
    journal row naming the label would then point at an id nothing resolves —
    which is the defect #74 exists to remove, rebuilt one layer down. So `id`,
    `created_at` and `judged_by` come from the record that is already there.

    **`judged_by` is the coiner and never the describer.** Preserving it here is
    what makes that rule structural rather than a convention every caller has to
    remember: describing a label, judging it against another, or backfilling it
    are not claims to have introduced the word.

    Only `description` and `metadata` move, exactly as
    `store_relation_label` promises — and **a blank description never
    overwrites prose**, so a path that writes a record without one (a coin, a
    backfill) cannot quietly erase what somebody wrote.
    """
    if existing is None:
        return incoming
    return existing.model_copy(update={
        "description": incoming.description or existing.description,
        "metadata": {**existing.metadata, **incoming.metadata},
    })


RELATION_VERDICTS: tuple[str, ...] = ("distinct", "synonymous")


def relation_pair_key(a_id: str, b_id: str) -> tuple[str, str]:
    """The one key for a label pair, whichever order it was judged in.

    Sorted, so `(a, b)` and `(b, a)` are one pair rather than two. It lives here
    rather than in the sweep or the backend because three places have to agree
    on it — the row that is written, the set the sweep reads, and the lookup
    that refuses a retry — and a pair keyed two ways is a suppression index that
    silently suppresses half of what it should.
    """
    return tuple(sorted((a_id, b_id)))  # type: ignore[return-value]


class RelationVerdict(BaseModel):
    """What an agent decided about a nominated pair of relation labels (#74).

    **The suppression index for FC1.** `find_similar_relation_pairs` re-derives
    from scratch on every `reflect` and recorded nothing about declines, so a
    pair an agent considered and rejected came back on every pass, for ever, to
    a fresh agent who could not see the previous refusals. Worse, the graph
    applied quiet pressure toward the wrong answer: accepting a merge makes one
    label stop existing, so **accepting is self-suppressing and declining is
    not**.

    `#64` closed exactly this for fact pairs with the `assessed` edge, and
    relation labels could not have one: that edge runs between two **nodes**,
    and `works_for` and `employed_by` are not nodes. They are now records, which
    is what makes this row addressable.

    **A small append-only table, not a field on the label record.** Storing the
    pair on each side would be mutable state held twice, free to disagree —
    #54, #55 and #56 for the fifth time. It is a denormalised suppression index
    and is legitimate as one for `similarity_decisions.py`'s stated reason: it
    is immutable and append-only, so it cannot drift from the journal row that
    also records it. The journal is the audit record; this is what the sweep
    reads without a journal query.

    **Both verdicts suppress, and `synonymous` acts on nothing yet.** Recording
    *"yes, these are synonyms, and I am not merging them"* is a real judgment,
    and leaving it unrecordable would be FC1 again for the affirmative answer.
    Whatever consolidates labels can then act on standing verdicts rather than
    re-asking.

    **Suppression is permanent**, inherited from the fact-pair layer
    deliberately rather than by accident, so a wrong `distinct` silences a pair
    for good. That is the dual of the futile-cycle rule and both are stated in
    `RELATION_LABELS.md` §4.2; the retraction question for both layers lives in
    `ISSUES.md` #80.
    """

    id: str = Field(default_factory=_new_id)
    # Two `RelationLabel` ids, sorted by `relation_pair_key`. Ids and not names:
    # a name is what edges join on, and keying suppression on a string would
    # have made the journal row's subjects strings too — the second namespace
    # #74 exists to avoid.
    label_ids: list[str]
    verdict: Literal["distinct", "synonymous"]
    # Required by every writer, for #64's reason: a verdict with no reason marks
    # the pair judged, so the next agent skips it without knowing whether it was
    # examined or waved through.
    because: str
    judged_by: JudgeRef | None = None
    decided_at: datetime = Field(default_factory=_now)


# --- Agents: who judged this (REVIEW_MODE.md §2) ---


def description_digest(text: str) -> str:
    """Identify a self-description *version*.

    Truncated sha256 of the exact text. It identifies the version and never the
    agent: hashing the description to get an id fails in both directions —
    reword and you become a different judge, paste and you become the same one
    (§2.1). The id is the user's to assign; this is only what the agent claimed
    to be at the moment it decided, pinned so a decision that records the digest
    needs no as-of query.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class AgentDescription(BaseModel):
    """One thing an agent said about itself, and when it said it.

    Append-only. A re-description appends; nothing is ever edited, because a
    decision made last week was made by whatever this agent claimed to be last
    week, and that claim has to stay readable after the agent changes its mind.

    **Self-description is a claim, not a credential** (§2.4). Nothing verifies
    the text — it is self-reported prose, exactly like a fact the agent ingests.
    `confirmed_at` is the only part carrying human weight, and it is set only
    through a channel that terminates at the user: `ctx.elicit`, or the
    `epimemer agents confirm` CLI where the client cannot elicit (§2.3). `None`
    is *self-described, unconfirmed* — a different epistemic object, never
    collapsed into the same field.
    """

    digest: str
    text: str
    recorded_at: datetime = Field(default_factory=_now)
    confirmed_at: datetime | None = None


class Agent(BaseModel):
    """A judge: something that made decisions in this graph.

    Not a user account and not a credential. The identity is **assigned by the
    user**, which is what makes review provable — an agent that mints its own
    cannot establish that it is a different agent from the one that decided
    yesterday, and self-review becomes indistinguishable from independent
    review (§2.2).

    **Three layers, because one field was doing three jobs** (#78, 2026-08-26).
    `id` is the join key: opaque, frozen into every `judged_by` at write time,
    and never shown to anybody. `name` is the handle — what the picker lists,
    what `review(mode="by_agent")` accepts, what a frontend labels a row with —
    and it is **freely renamable**, resolved at read time so a rename carries
    old rows with it. `descriptions` is what this judge *claimed to be*, pinned
    per decision by digest and never resolved at read time. The two resolution
    rules are opposite on purpose: *which judge is this* wants the name the user
    knows it by now, and *what did it claim to be when it decided this* wants
    the claim as it stood, or an old decision stops being readable.

    Before the split, the id was all three at once, so naming a judge badly on
    first contact was permanent and splitting one judge's history in two was a
    typo away — which is how `Opus 5 Judge` and `Opus 5` both came to exist on
    this repository's own graph.

    **`name` empty means *use the id*.** That is what a row written before the
    split looks like, and `agent_name` is the one place the fallback lives, so
    nothing else has to know. Every write since fills it in.

    **`former_ids` is the whole of aliasing.** It carries the ids this judge's
    rows may already record: the keys of records absorbed into this one. Nothing
    is ever rewritten — old rows keep the id they recorded and lookup resolves
    through this list, the same shape as `rejudge` keeping the value it replaces
    (#78). An agent record whose id appears here has been absorbed and is no
    longer a judge in its own right; `live_agents` is where that is decided.

    The append-only-list-with-dates shape is deliberately `LifecycleEpisode`'s.
    Same problem, same answer: a scalar plus a timestamp cannot express
    *changed, and here is what it was before*.
    """

    id: str
    name: str = ""
    former_ids: list[str] = Field(default_factory=list)
    descriptions: list[AgentDescription] = Field(default_factory=list)
    authorised_at: datetime = Field(default_factory=_now)
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None


def new_agent_id() -> str:
    """A fresh opaque key for a judge.

    Opaque rather than the name the user typed, so the name stays free to
    change. #77 rejected this and was overturned the same week: both its
    objections — *approving an id you cannot identify* and *the name-to-identity
    problem only moves* — were premised on a free-text prompt. With a picker the
    user never sees the key, and a **human** resolves name to identity on every
    bind rather than a machine guessing. The picker is the precondition for the
    opaque id, not an alternative to it.
    """
    return _new_id()


def with_description(
    descriptions: Sequence[AgentDescription],
    *,
    text: str,
    at: datetime,
    confirmed_at: datetime | None = None,
) -> list[AgentDescription]:
    """`descriptions` plus `text`, unless the current one already says it.

    Re-recording identical text is not a new version (§2.1), so a session that
    re-claims with unchanged prose leaves the list alone — otherwise the history
    would fill with versions that differ only in their timestamps, and *which
    description was current when this was decided* would stop being answerable
    from the digest alone.
    """
    digest = description_digest(text)
    if descriptions and descriptions[-1].digest == digest:
        return list(descriptions)
    return [
        *descriptions,
        AgentDescription(
            digest=digest, text=text, recorded_at=at, confirmed_at=confirmed_at
        ),
    ]


def current_description(agent: Agent) -> AgentDescription | None:
    """The version in force now — the last appended, never the newest by date."""
    return agent.descriptions[-1] if agent.descriptions else None


def agent_name(agent: Agent) -> str:
    """What to call this judge. The id where no name was ever set.

    One place for the fallback, because a record written before the three-layer
    split (#78) has no name and every display path would otherwise carry the
    same `or`. A legacy id reads as a name because it *was* one.
    """
    return agent.name or agent.id


def agent_aliases(agent: Agent) -> list[str]:
    """Every id this judge's journal rows may record — current first.

    Nothing is rewritten when judges are consolidated, so *this judge's
    decisions* is a query over a set of ids rather than one. Callers pass the
    whole list to `query_decisions`; a judge that has never been consolidated
    yields a list of one and the query is what it always was.
    """
    return [agent.id, *(fid for fid in agent.former_ids if fid != agent.id)]


def absorbed_agent_ids(agents: Sequence[Agent]) -> set[str]:
    """Ids that some *other* agent record has claimed as a former id.

    A record so named is no longer a judge in its own right: its rows resolve to
    the judge that absorbed it. It is kept rather than deleted — nothing here
    hard-deletes, and its description history is what makes its old decisions
    readable — so *absorbed* has to be derived, and this is the one place that
    derivation lives.
    """
    return {
        former
        for agent in agents
        for former in agent.former_ids
        if former != agent.id
    }


def live_agents(agents: Sequence[Agent]) -> list[Agent]:
    """The judges this graph actually has, absorbed records dropped."""
    absorbed = absorbed_agent_ids(agents)
    return [agent for agent in agents if agent.id not in absorbed]


def resolve_agent(agents: Sequence[Agent], handle: str) -> Agent | None:
    """The live judge a handle names — an id, a name, or a former id.

    **Precedence is id, then name, then former id**, and the order is not
    arbitrary. An id is exact and unambiguous, so it wins. A name is what the
    user sees and types, so it beats a historical alias: where a judge was once
    called *Opus 5* and a different one is called that **now**, the one it names
    today is the one meant. Names match case-insensitively, because a picker
    offering `Opus 5` and `opus 5` as separate judges is the split this exists
    to stop.

    Absorbed records are invisible here, so a handle that named one resolves to
    whatever absorbed it — which is the whole point of consolidating.
    """
    handle = handle.strip()
    if not handle:
        return None
    live = live_agents(agents)
    for agent in live:
        if agent.id == handle:
            return agent
    folded = handle.casefold()
    for agent in live:
        if agent_name(agent).casefold() == folded:
            return agent
    for agent in live:
        if handle in agent.former_ids:
            return agent
    return None


def name_holder(
    agents: Sequence[Agent], name: str, *, excluding: str = ""
) -> Agent | None:
    """The live judge already called `name`, ignoring the one being renamed.

    Names must be unique per graph or `by_agent` stops being answerable after a
    rename and the picker shows two identical lines. Enforced where a name is
    *set*, which is the only place it can be enforced at all.
    """
    folded = name.strip().casefold()
    return next(
        (
            agent for agent in live_agents(agents)
            if agent.id != excluding and agent_name(agent).casefold() == folded
        ),
        None,
    )


def renamed(agent: Agent, name: str) -> Agent:
    """`agent` under a new handle. The id, the history and the rows are untouched."""
    return agent.model_copy(update={"name": name.strip()})


def absorbing(survivor: Agent, absorbed: Agent) -> Agent:
    """`survivor`, now answering for `absorbed`'s ids and description history.

    The repair for one judge recorded twice. **Nothing is rewritten and nothing
    is deleted**: the absorbed record stays where it is, its id becomes a former
    id here, and its journal rows keep the id they were written with. What
    changes is only where a lookup lands.

    **The descriptions are merged, not discarded**, and that is what makes it
    safe. A decision records `(agent_id, digest)`, and reading *what did this
    judge claim to be then* resolves the id to an agent and the digest to a
    version — so dropping the absorbed history would leave its own old decisions
    unreadable through the very record that now answers for them. Ordered by
    when they were recorded, deduplicated by digest, so `current_description`
    still returns the latest claim either judge made.
    """
    merged: dict[str, AgentDescription] = {}
    for version in sorted(
        [*survivor.descriptions, *absorbed.descriptions],
        key=lambda v: v.recorded_at,
    ):
        merged.setdefault(version.digest, version)
    seen = [survivor.first_seen_at, absorbed.first_seen_at]
    last = [survivor.last_seen_at, absorbed.last_seen_at]
    return survivor.model_copy(update={
        "former_ids": list(dict.fromkeys([
            *survivor.former_ids,
            *agent_aliases(absorbed),
        ])),
        "descriptions": list(merged.values()),
        "authorised_at": min(survivor.authorised_at, absorbed.authorised_at),
        "first_seen_at": min((s for s in seen if s), default=None),
        "last_seen_at": max((s for s in last if s), default=None),
    })



# --- The decision journal (REVIEW_MODE.md §4) ---


class DecisionKind(str, Enum):
    """What sort of judgment a journal row records.

    One value per writer, and the list is deliberately fine-grained where two
    outcomes look alike but mean opposite things — `CORRECTION` and
    `WORLD_CHANGE` are the two halves of `because`, and #53 exists because
    collapsing them is how a graph forgets its own history.

    It is not a free string: review selects on it, and a vocabulary that grows
    by typing is one that cannot be selected on reliably.

    **Every member has a writer.** A value nothing can produce is worse than no
    value at all — a caller writes a branch for it and the branch is dead, and
    here it is worse still, because review *selects* on this: a kind with no
    writer is a filter that silently returns nothing and looks like a clean
    graph. `WARNINGS_AND_SETTINGS.md` §8.1 settled this for `AdvisoryAction` and
    the same rule holds here, enforced by a test that reads both lists.

    Two kinds are therefore **not** here yet, and are named so nobody re-derives
    them: `relation_merge`, whose subjects `RELATION_LABELS.md` settled — they
    are the two labels' record ids, which #69 could not name because a label had
    no id — and which waits only on whether label merging survives at all
    (`RELATION_LABELS.md` §5); and `proceeded_despite_advisory`, once advisories
    exist — that one is `WARNINGS_AND_SETTINGS.md` §9's node note, folded in
    here so there is one review machine rather than two (REVIEW_MODE.md §9).
    """

    # Ingest. One row per `store_decomposition` call rather than per fact (§4.1)
    # — forty-four facts out of one document is one reading of one document.
    INGEST = "ingest"

    # Supersession, in its two opposite readings.
    CORRECTION = "correction"
    WORLD_CHANGE = "world_change"

    # Judgments about pairs.
    CONTRADICTION = "contradiction"
    VARIANT = "variant"
    SIMILARITY = "similarity"
    # A `similarity` verdict withdrawn (#68). Its own kind rather than
    # `REVERSAL`, though both undo an earlier decision, because the two differ
    # in what they did: a merge reversal **deletes** the survivor — the system's
    # only hard delete — and a reviewer selecting `REVERSAL` to audit that would
    # otherwise get rows where nothing was destroyed.
    RETRACTION = "retraction"

    # Consolidation, and its undo.
    MERGE = "merge"
    REVERSAL = "reversal"

    # Reflect's other applications.
    SYNTHESIS = "synthesis"
    SPLIT = "split"
    ENRICHMENT = "enrichment"
    ARCHIVAL = "archival"
    REACTIVATION = "reactivation"
    BOUNDARY = "boundary"

    # Revisions of an ingest-time judgment that are *not* supersessions — the
    # claim is unchanged and the world has not moved, so `because` has no honest
    # value (#66). `rejudge` covers the node-scoped fields; these two are
    # separate because they are addressed differently, which is the tell that
    # they are different tools: a frame is an edge onto a metacontext, and an
    # interval belongs to a (node, source) pair rather than to a node.
    REFRAME = "reframe"
    INTERVAL_CORRECTION = "interval_correction"

    # Everything else an agent asserts about the graph.
    RELATION = "relation"
    # Prose about what one of this graph's relationship labels *means* (#74).
    # Its own kind rather than `ENRICHMENT`, which is reflect's enrichment of a
    # **topic**: a reviewer auditing changes to what the graph claims does not
    # want prose about what the graph's words mean mixed in. The first draft
    # wrote `ENRICHMENT` because enriching is what it is — the right verb, and
    # the wrong side of the line. `RELATION_LABELS.md` §7.2.
    RELATION_DESCRIPTION = "relation_description"
    # A pair of relation labels judged `distinct` or `synonymous` (#74 FC1).
    # Its own kind rather than `SIMILARITY`: review *selects* on kind, and a
    # reviewer auditing judgments about claims does not want judgments about
    # vocabulary mixed in. Its subjects are the two labels' record ids, which is
    # where #69 resolves — the subject finally has an identity to name.
    RELATION_VERDICT = "relation_verdict"
    IMPORTANCE = "importance"

    # One declaration sweep: a user stating, through the CLI, which frame the
    # nodes of a graph that predate the requirement were always in. One row per
    # sweep rather than per node — the archival-sweep granularity rule, and for
    # the same reason: it is one act of judgment applied to whatever it found,
    # not N independent verdicts. Its subjects are the nodes it stamped.
    FRAME_DECLARATION = "frame_declaration"

    # Review of a decision already in the journal (§6.4, step 7). All three
    # carry a `reviews` pointer, and none of them is a graph change: the point
    # of review is that somebody looked, and looking is worth recording even
    # where nothing needed doing.
    #
    # `CONFIRMATION` and `DISSENT` are two kinds rather than one with a flag,
    # for the reason this enum is fine-grained everywhere else: a reviewer
    # asking *"what has been disputed"* does not want the agreements, and a
    # boolean inside a row cannot be selected on.
    CONFIRMATION = "confirmation"
    # Checked, and wrong — but **nothing was undone**, which is what separates
    # this from `REVERSAL`. The tools that undo a decision journal their own row
    # and set `supersedes`; a dissent sets only `reviews`, because a row
    # claiming to supersede a decision whose effect still stands would make the
    # journal disagree with the graph (§4.2).
    DISSENT = "dissent"
    # An ingest-time judgment revised without touching the claim (§6.5) —
    # `claim_kind`, `confidence`, `confidence_basis`. Never a supersession: the
    # wording is unchanged, so nothing was corrected and nothing moved on.
    REJUDGMENT = "rejudgment"


class DecisionRecord(BaseModel):
    """One judgment, as an append-only row (§4).

    Attribution on the rows answers *who judged this node*. This answers the
    inverse — *what did this agent judge* — which over fields scattered across
    facts, edges, lifecycle episodes and value signals would be five scans and a
    reassembly. The inline fields stay: they are the immutable denormalised
    copy, and §4.2 records which of the two is primary for which question.

    **Never edited, with no exceptions** — including for review state, which is
    why there is no `reviewed_at` here. A review is another record pointing back
    (`reviews`), so *reviewed* is derived from existence rather than stored as a
    mutable flag on a row that claims to be append-only. The first draft had
    both, and the contradiction was load-bearing: a mutable field on this row
    also has to stay in sync with a copy on the node, across two backends
    (#54, #55, #56).

    There is no update path on the protocol either, which is what makes the
    claim structural rather than a convention.
    """

    id: str = Field(default_factory=_new_id)
    kind: DecisionKind
    # What the judgment was about. Ids rather than edges: edges cannot originate
    # from edges, so similarity, contradiction and variant decisions would need
    # the inline form regardless, and two rules for one relation is worse than a
    # scan (§3.5).
    subject_ids: list[str] = Field(default_factory=list)
    # Absent means **unknown**, and nothing more (§3.3). A graph that does not
    # require a judge still journals: the row carries when it was decided and
    # whether anyone has since checked it, and both are worth having from an
    # agent that did not name itself.
    judged_by: JudgeRef | None = None
    decided_at: datetime = Field(default_factory=_now)
    # §5. The same ladder as `confidence` (#46) rather than a second
    # near-identical one, and absent means **unrated** — deliberately not a
    # rated 0.5. Supplied by the review writers (`apply_review`, `rejudge`),
    # where a declared judgment is the whole point of the call; every other
    # writer leaves it blank rather than adding a rung to twelve tool schemas,
    # so `review()`'s derived tier still carries most of the corpus (§6.2).
    # Bounded because the ordering sorts on it: an out-of-range value would not
    # be rejected by the sort, it would silently rank first or last.
    certainty: float | None = Field(default=None, ge=0.0, le=1.0)
    certainty_basis: str | None = None
    # The record this one is *about*. A confirmation reviews without
    # superseding, so the two are separate fields — collapsing them is what
    # breaks a derived-only scheme, since a confirmation supersedes nothing.
    reviews: str | None = None
    supersedes: str | None = None
    # The frame this decision was made in, where the decision names one. Ingest
    # and a declaration sweep both apply exactly one frame to everything they
    # touch, so one value per row is the whole of it — and it is what turns the
    # recoverability argument for requiring a frame into a supported read: *what
    # did this agent file into the real world* is one query here rather than a
    # walk from ingest rows out to the edges of the nodes they name (#76).
    frame: str | None = None


SUPERSESSION_KINDS: dict[NodeStatus, DecisionKind] = {
    NodeStatus.CORRECTED: DecisionKind.CORRECTION,
    NodeStatus.HISTORICAL: DecisionKind.WORLD_CHANGE,
}


def supersession_kind(status: NodeStatus) -> DecisionKind:
    """Which journal kind a retirement at `status` is.

    One declaration, read by `update`, `supersede_by` and reflect's
    supersessions and enrichments, so the three cannot drift into disagreeing
    about what the same status means.
    """
    return SUPERSESSION_KINDS.get(status, DecisionKind.CORRECTION)
