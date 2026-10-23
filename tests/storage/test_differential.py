"""A differential test: one random history, applied to the in-memory store and to a candidate.

The parity suite asserts what the protocol promises one property at a time, on
graphs a person thought of. This drives a seeded sequence of operations, chosen
from a menu that covers the whole write side of the protocol, into the
in-memory store and into one persistent backend at once, then compares the two
graphs. The in-memory store is the oracle: it is the reference implementation
every other backend is held to, so where the two disagree the candidate is the
first suspect, though not always the culprit.

**Everything is deterministic.** Every record gets an explicit id and an
explicit timestamp: a fixed base plus one second per step plus a random number
of microseconds, which is sometimes zero on purpose, since an instant on a
whole second is rendered differently from one beside it. The one place a
backend mints a record of its own, the edge a world-change copies onto the
replacement node, draws its id and its clock from `epimemer.core.types`, and the
test replaces both with counters reset before each step on each store, so the
two stores mint the same edge. That is what lets the bundles match byte for
byte.

Each step's outcome is compared as it happens (what it returned, or which
exception it raised), and the graphs are compared at the end: the exported
bundle section by section, then the reads the bundle does not cover. A failure
names the seed, the candidate and the first difference. Three seeds run by
default; `EPIMEMER_DIFFERENTIAL_SEEDS=50` runs fifty.

Postgres is a candidate only when `EPIMEMER_TEST_POSTGRES_DSN` is set, as in
the rest of the suite. Nothing here takes an embedding provider or a model:
vectors are random and eight wide.
"""

import copy
import json
import os
import random
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

import pytest
from pydantic import BaseModel

from epimemer.core import types as core_types
from epimemer.core.advisories import AdvisoryAction, AdvisoryKind
from epimemer.core.seat import JudgeConfirmation
from epimemer.core.temporal import (
    IntervalBasis,
    NamedInstant,
    PreciseInstant,
    UnboundedInstant,
    UnknownInstant,
    ValidityInterval,
)
from epimemer.core.types import (
    Agent,
    ClaimKind,
    DecisionKind,
    DecisionRecord,
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    JudgeRef,
    Metacontext,
    NodeEdge,
    NodeStatus,
    NodeType,
    RawDocument,
    RelationLabel,
    RelationVerdict,
    Segment,
    Timeline,
    Timepoint,
    Topic,
    ValueSignal,
    lineage_edge_type_for,
    migration_disposition,
)
from epimemer.pipelines.transfer import MANIFEST_FILENAME, bundle_bytes, export_graph
from epimemer.storage.memory import InMemoryStorage
from epimemer.storage.protocol import DeferralOverrides, MergeOverrides, WarningOverrides
from epimemer.storage.sqlite_adapter import SQLiteStorage
from epimemer.storage.surrealdb_adapter import SurrealDBStorage
from tests.postgres_store import clean_postgres_store

BASE = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
SEEDS = range(int(os.environ.get("EPIMEMER_DIFFERENTIAL_SEEDS", "3")))
STEPS = 300
DIMENSION = 8
MODELS = ("m8", "m8-alt")
JUDGES = ("k1", "k2", "k3")
CLASSES: dict[NodeType, type] = {
    NodeType.TOPIC: Topic,
    NodeType.FACT: Fact,
    NodeType.INFERENCE: Inference,
}

# Plain words, words the analyzer has to fold, and characters a backend reaching
# its store through query text would have to escape.
WORDS = (
    "harbour",
    "toll",
    "ledger",
    "deployment",
    "rollback",
    "closed",
    "open",
    "the",
    "of",
    "a",
    "Saint",
    "Petersburg",
    "Leningrad",
    "café",
    "naïve",
    "Ångström",
    "it's",
    'said "no"',
    "back`tick",
    "$amount",
    "50%",
    "line\nbreak",
    "日本語",
    "emoji🙂",
)
# Rare identifiers, planted sparsely so a term search has something rare to find.
TICKETS = tuple(f"TKT-{n}" for n in range(1001, 1041))

# The edge types a random edge is drawn from: one or more of every disposition a
# retirement can give an edge (move, copy, keep), and the shapes with a
# non-node end.
EDGE_TYPES = (
    EdgeType.SUPPORTS,
    EdgeType.EXTRACTED_UNDER_TOPIC,
    EdgeType.DERIVED_FROM,
    EdgeType.SIMILARITY,
    EdgeType.CONTRADICTION,
    EdgeType.VARIANT_OF,
    EdgeType.SUBTOPIC_OF,
    EdgeType.HAS_METACONTEXT,
    EdgeType.TAGGED_WITH_TOPIC,
    EdgeType.SOURCED_FROM,
    EdgeType.ASSESSED,
    EdgeType.RELATED,
    EdgeType.TIMELINK,
    EdgeType.ABOUT,
)
LABELS = ("works_for", "employed_by", "reported_in", "published_by")


# --- Deterministic factories ---


def _deterministic_factories(monkeypatch: pytest.MonkeyPatch) -> Callable[[int], None]:
    """Replace the id and clock factories in `epimemer.core.types` with counters.

    Returns `reset(step)`, which the driver calls before applying a step to each
    store, so that whatever a backend mints during the step comes out the same
    on both. Only `_new_id` and `_now` read these names, and every record this
    test writes names its own id and timestamp, so the counters only ever reach
    the edges a backend makes itself.
    """
    state = {"step": 0, "made": 0}

    def next_uuid() -> uuid.UUID:
        state["made"] += 1
        return uuid.UUID(int=(state["step"] << 32) + state["made"])

    class StepClock(datetime):
        @classmethod
        def now(cls, tz=None) -> datetime:  # noqa: ARG003 - the signature it replaces
            return BASE + timedelta(seconds=state["step"], microseconds=state["made"])

    def reset(step: int) -> None:
        state["step"], state["made"] = step, 0

    monkeypatch.setattr(core_types, "uuid4", next_uuid)
    monkeypatch.setattr(core_types, "datetime", StepClock)
    return reset


# --- Random values ---


def _at(rng: random.Random, step: int) -> datetime:
    """Step `step`'s instant: on a whole second a quarter of the time."""
    micros = 0 if rng.random() < 0.25 else rng.randrange(1, 1_000_000)
    return BASE + timedelta(seconds=step, microseconds=micros)


def _text(rng: random.Random, *, length: str | None = None) -> str:
    """A few words, a sentence or two, or several kilobytes, now and then with a ticket id."""
    size = length or rng.choices(("short", "medium", "long"), weights=(5, 4, 1))[0]
    low, high = {"short": (1, 3), "medium": (8, 25), "long": (400, 700)}[size]
    words = [rng.choice(WORDS) for _ in range(rng.randint(low, high))]
    if rng.random() < 0.3:
        words.insert(rng.randrange(len(words) + 1), rng.choice(TICKETS))
    return " ".join(words)


def _judge(rng: random.Random) -> JudgeRef | None:
    if rng.random() < 0.3:
        return None
    return JudgeRef(agent_id=rng.choice(JUDGES), digest=f"d{rng.randrange(3)}")


def _metadata(rng: random.Random) -> dict:
    """A free-form bag, sometimes nested, sometimes holding a None to be dropped.

    Every backend drops a None-valued key at any depth of a record, a
    timepoint's `metadata` inside its timeline included, and keeps a None that
    is a list element.
    """
    choice = rng.randrange(4)
    if choice == 0:
        return {}
    if choice == 1:
        return {"lang": rng.choice(("en", "fr")), "note": None}
    if choice == 2:
        return {"nested": {"depth": rng.randrange(5), "gone": None, "kept": [1, None, "x"]}}
    return {"weight": rng.random(), "tags": [rng.choice(WORDS)]}


def _vector(rng: random.Random) -> list[float]:
    if rng.random() < 0.05:
        return [0.0] * DIMENSION
    return [rng.uniform(-1.0, 1.0) for _ in range(DIMENSION)]


def _value(rng: random.Random, step: int) -> ValueSignal:
    return ValueSignal(
        confidence=None if rng.random() < 0.4 else round(rng.random(), 3),
        importance=round(rng.random(), 3),
        retrieved_at=None if rng.random() < 0.6 else _at(rng, step),
        importance_judged_at=None if rng.random() < 0.7 else _at(rng, step),
        importance_judged_by=_judge(rng) if rng.random() < 0.3 else None,
    )


def _validity(rng: random.Random, step: int) -> list[ValidityInterval]:
    if rng.random() < 0.6:
        return []
    return [
        ValidityInterval(
            start=rng.choice((UnknownInstant(), NamedInstant(label="during the Renaissance"))),
            end=rng.choice((UnboundedInstant(), PreciseInstant(at=_at(rng, step + 10_000)))),
            basis=rng.choice(list(IntervalBasis)),
        )
    ]


def _pick(rng: random.Random, items: list) -> Any:
    return rng.choice(items) if items else None


# --- Steps ---
#
# A step is chosen against the oracle's state and returns `(description,
# script)`, or None when nothing it could act on exists yet. The script is run
# against each store in turn with arguments deep-copied for each, so neither
# store can see what the other did to an argument.

type Script = Callable[[Any], Awaitable[Any]]


class Step(NamedTuple):
    rng: random.Random
    oracle: InMemoryStorage
    i: int
    merges: list[dict]


def _call(name: str, *args: Any, **kwargs: Any) -> tuple[str, Script]:
    async def script(store: Any) -> Any:
        return await getattr(store, name)(*copy.deepcopy(args), **copy.deepcopy(kwargs))

    return name, script


def _nodes(step: Step, *, status: NodeStatus | None = NodeStatus.ACTIVE) -> list:
    return [n for n in step.oracle.nodes.values() if status is None or n.status == status]


def _node_type(node: Any) -> NodeType:
    return {Topic: NodeType.TOPIC, Fact: NodeType.FACT, Inference: NodeType.INFERENCE}[type(node)]


def _new_node(step: Step, node_type: NodeType, *, node_id: str | None = None) -> Any:
    rng = step.rng
    segment = _pick(rng, list(step.oracle.segments))
    fields: dict[str, Any] = {
        "id": node_id or f"n{step.i}",
        "content": _text(rng),
        "value": _value(rng, step.i),
        "judged_by": _judge(rng),
        "extraction_method": rng.choice(("unspecified", "agent", "rule")),
        "metadata": _metadata(rng),
        "created_at": _at(rng, step.i),
    }
    if node_type is NodeType.TOPIC:
        fields["description"] = rng.choice(("", _text(rng, length="medium")))
        fields["description_reviewed_at"] = None if rng.random() < 0.7 else _at(rng, step.i)
        fields["source_id"] = None if rng.random() < 0.5 else segment
    else:
        fields["source_id"] = segment or "s-missing"
    if node_type is NodeType.FACT:
        fields["claim_kind"] = _pick(rng, [None, *ClaimKind])
    return CLASSES[node_type](**fields)


def _new_edge(step: Step, edge_id: str, *, src: str | None = None) -> NodeEdge | None:
    rng = step.rng
    nodes = list(step.oracle.nodes)
    if not nodes:
        return None
    edge_type = rng.choice(EDGE_TYPES)
    source = src or rng.choice(nodes)
    destinations = {
        EdgeType.HAS_METACONTEXT: list(step.oracle.metacontexts),
        EdgeType.SOURCED_FROM: list(step.oracle.documents),
        EdgeType.TIMELINK: list(step.oracle.timelines),
    }.get(edge_type, nodes)
    destination = _pick(rng, destinations) or rng.choice(nodes)
    metadata = _metadata(rng)
    if edge_type is EdgeType.TIMELINK:
        timeline = step.oracle.timelines.get(destination)
        point = _pick(rng, [p.id for p in timeline.timepoints]) if timeline else None
        metadata = {"timepoint_id": point or "tp-none"}
    related = edge_type is EdgeType.RELATED
    retired = rng.random() < 0.1
    return NodeEdge(
        id=edge_id,
        src_id=source,
        dst_id=destination,
        type=edge_type,
        label=rng.choice(LABELS) if related else None,
        kind=rng.choice(("relationship", "attribution")) if related else "relationship",
        weight=round(rng.uniform(0.0, 2.0), 4),
        validity=_validity(rng, step.i) if edge_type is EdgeType.SOURCED_FROM else [],
        judged_by=_judge(rng),
        retired_at=_at(rng, step.i) if retired else None,
        retired_by=_judge(rng) if retired else None,
        metadata=metadata,
        created_at=_at(rng, step.i),
    )


def _embedding(step: Step, item_id: str, embedding_id: str) -> EmbeddingRecord:
    return EmbeddingRecord(
        id=embedding_id,
        item_id=item_id,
        model_id=step.rng.choice(MODELS),
        vector=_vector(step.rng),
        created_at=_at(step.rng, step.i),
    )


async def store_document(step: Step) -> tuple[str, Script]:
    rng = step.rng
    return _call(
        "store_document",
        RawDocument(
            id=f"d{step.i}",
            content=_text(rng),
            source=rng.choice(("notes.md", "ISSUES.md", None)),
            source_type=rng.choice(("document", "chat", None)),
            published_at=rng.choice((None, PreciseInstant(at=_at(rng, step.i)), UnknownInstant())),
            metadata=_metadata(rng),
            created_at=_at(rng, step.i),
        ),
    )


async def store_segment(step: Step) -> tuple[str, Script] | None:
    document = _pick(step.rng, list(step.oracle.documents))
    if document is None:
        return None
    start = step.rng.randrange(1000)
    return _call(
        "store_segment",
        Segment(
            id=f"s{step.i}",
            source_id=document,
            text=_text(step.rng),
            span_start=start,
            span_end=start + step.rng.randrange(1, 500),
            metadata=_metadata(step.rng),
            created_at=_at(step.rng, step.i),
        ),
    )


async def resegment(step: Step) -> tuple[str, Script] | None:
    """A re-cut, usually of a document nothing cites yet, sometimes of one that is cited."""
    rng = step.rng
    documents = list(step.oracle.documents)
    if not documents:
        return None
    cited = {n.source_id for n in step.oracle.nodes.values()}
    uncited = [
        d
        for d in documents
        if not any(s.id in cited for s in step.oracle.segments.values() if s.source_id == d)
    ]
    document = _pick(rng, uncited) if rng.random() < 0.85 and uncited else rng.choice(documents)
    passages = [
        Segment(
            id=f"s{step.i}-{k}",
            source_id=document,
            text=_text(rng),
            span_start=k * 100,
            span_end=k * 100 + 99,
            created_at=_at(rng, step.i),
        )
        for k in range(rng.randint(1, 3))
    ]
    segmentation = {
        "method": rng.choice(("cuts", "paragraph")),
        "passages": len(passages),
        "history": [{"replaced": rng.randrange(4), "at": _at(rng, step.i).isoformat()}],
        "judged_by": None if rng.random() < 0.5 else {"agent_id": "k1", "digest": "d0"},
    }
    return _call("resegment_tx", document, passages, segmentation=segmentation)


async def store_node(step: Step) -> tuple[str, Script]:
    return _call("store_node", _new_node(step, step.rng.choice(list(NodeType))))


async def restore_node(step: Step) -> tuple[str, Script] | None:
    """Store an existing node again with new content and value: an upsert in place."""
    node = _pick(step.rng, _nodes(step, status=None))
    if node is None:
        return None
    changed = node.model_copy(
        update={"content": _text(step.rng), "value": _value(step.rng, step.i)}, deep=True
    )
    return _call("store_node", changed)


async def store_edge(step: Step) -> tuple[str, Script] | None:
    edge = _new_edge(step, f"e{step.i}")
    return None if edge is None else _call("store_edge", edge)


async def delete_edge(step: Step) -> tuple[str, Script]:
    edges = list(step.oracle.edges)
    target = _pick(step.rng, edges) if step.rng.random() < 0.9 else "e-missing"
    return _call("delete_edge", target or "e-missing")


async def store_embedding(step: Step) -> tuple[str, Script] | None:
    rng = step.rng
    if rng.random() < 0.2 and step.oracle.embeddings:
        existing = rng.choice(list(step.oracle.embeddings.values()))
        return _call("store_embedding", _embedding(step, existing.item_id, existing.id))
    node = _pick(rng, list(step.oracle.nodes))
    if node is None:
        return None
    return _call("store_embedding", _embedding(step, node, f"emb{step.i}"))


# The node every migrated edge is re-pointed at, while checking for collisions.
_REPLACEMENT = object()


def _collapses(step: Step, retiring: set[str], status: NodeStatus) -> bool:
    """Whether retiring `retiring` as `status` would fold two edges into one, or copy two.

    Left out of the menu, because the backends answer it differently and the
    protocol does not say which answer is right. Two edges that re-point to the
    same `(src, dst, type)` collapse to one, and which one survives is the
    first the backend reads: the in-memory, SQLite and Postgres stores read in
    the order the edges were stored, SurrealDB in its own order. On a
    correction SurrealDB does not collapse at all and keeps both. And two
    edges copied by a world-change take their minted ids in the order they
    were read, so each would carry the other's id.
    """
    seen: set[tuple[Any, Any, EdgeType]] = set()
    copies = 0
    for edge in step.oracle.edges.values():
        if edge.src_id not in retiring and edge.dst_id not in retiring:
            continue
        disposition = migration_disposition(edge.type, status)
        if disposition == "keep":
            continue
        src = _REPLACEMENT if edge.src_id in retiring else edge.src_id
        dst = _REPLACEMENT if edge.dst_id in retiring else edge.dst_id
        if src is _REPLACEMENT and dst is _REPLACEMENT:
            continue
        copies += disposition == "copy"
        signature = (src, dst, edge.type)
        if signature in seen:
            return True
        seen.add(signature)
    return copies > 1


async def supersede(step: Step) -> tuple[str, Script] | None:
    rng = step.rng
    status = rng.choice((NodeStatus.CORRECTED, NodeStatus.HISTORICAL))
    old = _pick(rng, [n for n in _nodes(step) if not _collapses(step, {n.id}, status)])
    if old is None:
        return None
    new = _new_node(step, _node_type(old))
    lineage = NodeEdge(
        id=f"e{step.i}",
        src_id=old.id,
        dst_id=new.id,
        type=lineage_edge_type_for(status),
        judged_by=_judge(rng),
        created_at=_at(rng, step.i),
    )
    others = [n.id for n in _nodes(step) if n.id != old.id]
    evidence = [
        NodeEdge(
            id=f"e{step.i}-ev",
            src_id=old.id,
            dst_id=rng.choice(others),
            type=EdgeType.EVIDENCE_SUPERSEDED,
            created_at=_at(rng, step.i),
        )
        for _ in range(1 if others and rng.random() < 0.5 else 0)
    ]
    clear = (
        [rng.choice(list(step.oracle.edges))] if step.oracle.edges and rng.random() < 0.3 else []
    )
    if rng.random() < 0.25 and others:
        return _call(
            "supersede_by_existing_tx",
            old,
            rng.choice(others),
            lineage,
            status=status,
            superseded_at=_at(rng, step.i),
            evidence_edges=evidence,
            clear_edge_ids=clear,
            judge=_judge(rng),
        )
    return _call(
        "supersede_node_tx",
        old,
        new,
        _embedding(step, new.id, f"emb{step.i}"),
        lineage,
        status=status,
        superseded_at=_at(rng, step.i),
        evidence_edges=evidence,
        clear_edge_ids=clear,
        judge=_judge(rng),
    )


async def set_status(step: Step) -> tuple[str, Script] | None:
    """Archive a few active nodes, or restore a few retired ones, now and then naming
    a node that does not exist, which has to change nothing on either store."""
    rng = step.rng
    returning = rng.random() < 0.4
    pool = (
        [n for n in step.oracle.nodes.values() if n.status is not NodeStatus.ACTIVE]
        if returning
        else _nodes(step)
    )
    if not pool:
        return None
    chosen = rng.sample(pool, min(len(pool), rng.randint(1, 3)))
    if rng.random() < 0.05:
        chosen.append(Topic(id="n-missing", content="absent", created_at=BASE))
    edges = [] if rng.random() < 0.7 else [_new_edge(step, f"e{step.i}", src=chosen[0].id)]
    return _call(
        "set_node_status_tx",
        chosen,
        status=NodeStatus.ACTIVE if returning else NodeStatus.ARCHIVED,
        at=_at(rng, step.i),
        edges=[edge for edge in edges if edge is not None],
        judge=_judge(rng),
    )


async def merge(step: Step) -> tuple[str, Script] | None:
    rng = step.rng
    node_type = rng.choice(list(NodeType))
    pool = [n for n in _nodes(step) if _node_type(n) is node_type]
    if len(pool) < 2:
        return None
    sources = rng.sample(pool, min(len(pool), rng.randint(2, 3)))
    if _collapses(step, {s.id for s in sources}, NodeStatus.MERGED):
        return None
    survivor = _new_node(step, node_type)
    lineage = [
        NodeEdge(
            id=f"e{step.i}-m{k}",
            src_id=source.id,
            dst_id=survivor.id,
            type=EdgeType.MERGED_INTO,
            judged_by=_judge(rng),
            created_at=_at(rng, step.i),
        )
        for k, source in enumerate(sources)
    ]
    others = [n.id for n in _nodes(step) if n.id not in {s.id for s in sources}]
    evidence = [
        NodeEdge(
            id=f"e{step.i}-ev",
            src_id=sources[0].id,
            dst_id=rng.choice(others),
            type=EdgeType.EVIDENCE_MERGED,
            created_at=_at(rng, step.i),
        )
        for _ in range(1 if others and rng.random() < 0.5 else 0)
    ]
    # What a reversal needs, read before the merge moves anything, as
    # `capture_merge_undo` reads it: every edge the merge moves or drops,
    # exactly as it stood. An edge the merge leaves where it is stays out, since
    # a reversal that wrote it again would be writing an edge that never left.
    ids = [s.id for s in sources]
    before = {
        e.id: e
        for direction in ("from", "to")
        for edges in (await step.oracle.get_edges_for(ids, direction=direction)).values()
        for e in edges
        if migration_disposition(e.type, NodeStatus.MERGED) != "keep"
    }
    step.merges.append(
        {
            "survivor": survivor.id,
            "sources": ids,
            "restored": list(before.values()),
            "written": [e.id for e in (*lineage, *evidence)],
        }
    )
    return _call(
        "merge_nodes_tx",
        sources,
        survivor,
        _embedding(step, survivor.id, f"emb{step.i}"),
        lineage,
        merged_at=_at(rng, step.i),
        evidence_edges=evidence,
        judge=_judge(rng),
    )


async def reverse_merge(step: Step) -> tuple[str, Script] | None:
    """Undo one recorded merge, deleting every edge the survivor now has."""
    candidates = [m for m in step.merges if m["survivor"] in step.oracle.nodes]
    if not candidates:
        return None
    plan = step.rng.choice(candidates)
    step.merges.remove(plan)
    survivor = step.oracle.nodes[plan["survivor"]]
    incident = {
        e.id
        for direction in ("from", "to")
        for edges in (await step.oracle.get_edges_for([survivor.id], direction=direction)).values()
        for e in edges
    }
    sources = [
        step.oracle.nodes.get(s) or Topic(id=s, content="gone", created_at=BASE)
        for s in plan["sources"]
    ]
    # Replayed under new ids, as `reverse_merge` replays them: the originals
    # may still be in the graph, moved onto the survivor.
    restored = [
        edge.model_copy(update={"id": f"e{step.i}-r{k}"}) for k, edge in enumerate(plan["restored"])
    ]
    return _call(
        "reverse_merge_tx",
        survivor,
        sources,
        restored,
        restored_at=_at(step.rng, step.i),
        delete_edge_ids=sorted(incident | set(plan["written"])),
        judge=_judge(step.rng),
    )


def _new_point(rng: random.Random, step: int) -> Timepoint:
    shape = rng.randrange(3)
    start = _at(rng, step + rng.randrange(-5000, 5000))
    return Timepoint(
        id=f"tp{step}",
        start=None if shape == 2 else start,
        end=start + timedelta(days=rng.randrange(1, 30)) if shape == 1 else None,
        label=rng.choice((None, "the spring", "during the Renaissance")) if shape < 2 else "once",
        metadata=_metadata(rng),
    )


async def create_timeline(step: Step) -> tuple[str, Script]:
    rng = step.rng
    return _call(
        "store_timeline",
        Timeline(
            id=f"tl{step.i}",
            name=_text(rng, length="short"),
            description=rng.choice(("", _text(rng, length="medium"))),
            reference_time=None if rng.random() < 0.7 else _at(rng, step.i),
            metadata=_metadata(rng),
            created_at=_at(rng, step.i),
        ),
    )


async def add_timepoint(step: Step) -> tuple[str, Script] | None:
    """The version flow: read, add a point, store, read again. A tenth of the time
    the same read is stored twice, and the second write must be refused."""
    timeline_id = _pick(step.rng, list(step.oracle.timelines))
    if timeline_id is None:
        return None
    point = _new_point(step.rng, step.i)
    stale = step.rng.random() < 0.1

    async def script(store: Any) -> Any:
        read = await store.get_timeline(timeline_id)
        changed = read.model_copy(update={"timepoints": [*read.timepoints, point]}, deep=True)
        await store.store_timeline(changed)
        if stale:
            await store.store_timeline(changed)
        return await store.get_timeline(timeline_id)

    return "store_timeline (read, add a point, store)", script


async def write_timeline(step: Step) -> tuple[str, Script] | None:
    """A timeline decision: the record and the links it moved, in one write."""
    rng = step.rng
    timeline_id = _pick(rng, list(step.oracle.timelines))
    nodes = list(step.oracle.nodes)
    if timeline_id is None or not nodes:
        return None
    point = _new_point(rng, step.i)
    link = NodeEdge(
        id=f"e{step.i}",
        src_id=rng.choice(nodes),
        dst_id=timeline_id,
        type=EdgeType.TIMELINK,
        metadata={"timepoint_id": point.id},
        judged_by=_judge(rng),
        created_at=_at(rng, step.i),
    )
    links = [
        e
        for e in step.oracle.edges.values()
        if e.type is EdgeType.TIMELINK and e.dst_id == timeline_id and e.retired_at is None
    ]
    judge = _judge(rng)
    retired = [
        e.model_copy(
            update={"retired_at": _at(rng, step.i), "retired_by": judge, "superseded_by": link.id}
        )
        for e in links[:1]
    ]
    reference = _at(rng, step.i)
    kind = rng.choice((DecisionKind.SPLIT, DecisionKind.TIMEPOINT_MERGE))

    async def script(store: Any) -> Any:
        read = await store.get_timeline(timeline_id)
        changed = read.model_copy(
            update={"timepoints": [*read.timepoints, point], "reference_time": reference},
            deep=True,
        )
        await store.write_timeline_tx(
            changed,
            kind=kind,
            retired_edges=copy.deepcopy(retired),
            new_edges=[copy.deepcopy(link)],
            judge=judge,
        )
        return await store.get_timeline(timeline_id)

    return "write_timeline_tx", script


async def write_batch(step: Step) -> tuple[str, Script]:
    """An ingest-shaped batch: new nodes, edges among them and the graph,
    embeddings, and now and then a timeline read at its current version."""
    rng = step.rng
    nodes = [
        _new_node(step, rng.choice(list(NodeType)), node_id=f"n{step.i}-{k}")
        for k in range(rng.randint(1, 3))
    ]
    edges = [
        edge
        for k, node in enumerate(nodes)
        if (edge := _new_edge(step, f"e{step.i}-{k}", src=node.id)) is not None
    ]
    embeddings = [_embedding(step, node.id, f"emb{step.i}-{k}") for k, node in enumerate(nodes)]
    timeline_id = _pick(rng, list(step.oracle.timelines)) if rng.random() < 0.3 else None
    point = _new_point(rng, step.i)

    async def script(store: Any) -> Any:
        timelines = []
        if timeline_id is not None:
            read = await store.get_timeline(timeline_id)
            timelines = [
                read.model_copy(update={"timepoints": [*read.timepoints, point]}, deep=True)
            ]
        await store.write_batch_tx(
            nodes=copy.deepcopy(nodes),
            edges=copy.deepcopy(edges),
            embeddings=copy.deepcopy(embeddings),
            timelines=timelines,
        )

    return "write_batch_tx", script


async def record_decision(step: Step) -> tuple[str, Script]:
    rng = step.rng
    nodes = list(step.oracle.nodes)
    decisions = [d.id for d in await step.oracle.query_decisions()]
    return _call(
        "record_decision",
        DecisionRecord(
            id=f"dec{step.i}",
            kind=rng.choice(list(DecisionKind)),
            subject_ids=rng.sample(nodes, min(len(nodes), rng.randrange(4))),
            covers=rng.sample(("never_retrieved", "stale", "duplicate"), rng.randrange(3)),
            judged_by=_judge(rng),
            decided_at=_at(rng, step.i),
            certainty=None if rng.random() < 0.6 else round(rng.random(), 3),
            certainty_basis=rng.choice((None, "read twice")),
            reviews=_pick(rng, decisions) if rng.random() < 0.3 else None,
            supersedes=_pick(rng, decisions) if rng.random() < 0.1 else None,
            metacontext=_pick(rng, list(step.oracle.metacontexts)) if rng.random() < 0.3 else None,
            at_round=None if rng.random() < 0.6 else rng.randrange(40),
        ),
    )


async def store_relation_label(step: Step) -> tuple[str, Script]:
    rng = step.rng
    return _call(
        "store_relation_label",
        RelationLabel(
            id=f"rl{step.i}",
            name=rng.choice(LABELS),
            kind=rng.choice(("relationship", "attribution")),
            description=rng.choice(("", _text(rng, length="medium"))),
            judged_by=_judge(rng),
            metadata=_metadata(rng),
            created_at=_at(rng, step.i),
        ),
    )


async def _label_pair(step: Step) -> list[str]:
    labels = [label.id for label in await step.oracle.query_relation_labels()]
    if len(labels) < 2:
        return ["rl-a", "rl-b"]
    return step.rng.sample(labels, 2)


async def record_relation_verdict(step: Step) -> tuple[str, Script]:
    rng = step.rng
    return _call(
        "record_relation_verdict",
        RelationVerdict(
            id=f"rv{step.i}",
            label_ids=await _label_pair(step),
            verdict=rng.choice(("distinct", "synonymous")),
            because=_text(rng, length="short"),
            judged_by=_judge(rng),
            decided_at=_at(rng, step.i),
        ),
    )


async def reopen(step: Step) -> tuple[str, Script]:
    """Withdraw a suppression: retire an `assessed` edge, or reopen a label pair."""
    rng = step.rng
    judge = _judge(rng)
    assessed = [e for e in step.oracle.edges.values() if e.type is EdgeType.ASSESSED]
    retired = [
        e.model_copy(update={"retired_at": _at(rng, step.i), "retired_by": judge})
        for e in assessed[:1]
        if rng.random() < 0.6
    ]
    pair = await _label_pair(step)
    verdicts = (
        []
        if retired
        else [
            RelationVerdict(
                id=f"rv{step.i}",
                label_ids=pair,
                verdict="reopened",
                because="asked again",
                judged_by=judge,
                decided_at=_at(rng, step.i),
            )
        ]
    )
    decision = DecisionRecord(
        id=f"dec{step.i}",
        kind=DecisionKind.REOPENED,
        subject_ids=[e.src_id for e in retired] or pair,
        judged_by=judge,
        decided_at=_at(rng, step.i),
    )
    return _call("reopen_tx", decision, retired_edges=retired, relation_verdicts=verdicts)


async def store_metacontext(step: Step) -> tuple[str, Script]:
    rng = step.rng
    existing = list(step.oracle.metacontexts)
    retired = rng.random() < 0.2
    return _call(
        "store_metacontext",
        Metacontext(
            id=_pick(rng, existing) if existing and rng.random() < 0.2 else f"mc{step.i}",
            content=_text(rng, length="short"),
            description=rng.choice(("", _text(rng, length="medium"))),
            status=NodeStatus.HISTORICAL if retired else NodeStatus.ACTIVE,
            superseded_at=_at(rng, step.i) if retired else None,
            value=_value(rng, step.i),
            metadata=_metadata(rng),
            created_at=_at(rng, step.i),
        ),
    )


async def counters(step: Step) -> tuple[str, Script]:
    rng = step.rng
    name = rng.choice(
        (
            "bump_reflect_counter",
            "bump_reflect_counter",
            "bump_backup_counter",
            "bump_backup_counter",
            "reset_backup_counter",
            "set_reflect_counter",
            "set_backup_counter",
            "set_reflect_round",
            "complete_reflect_round",
        )
    )
    if name.startswith("set_"):
        return _call(name, rng.randrange(50))
    return _call(name)


async def settings(step: Step) -> tuple[str, Script]:
    rng = step.rng

    def maybe(value: Any) -> Any:
        return None if rng.random() < 0.3 else value

    choices: list[tuple[str, Any]] = [
        ("set_reflect_threshold_override", maybe(rng.randint(1, 40))),
        ("set_backup_threshold_override", maybe(rng.randint(1, 40))),
        (
            "set_merge_overrides",
            MergeOverrides(
                undo_depth=maybe(rng.randint(1, 9)), cycle_limit=maybe(rng.randint(1, 4))
            ),
        ),
        (
            "set_deferral_overrides",
            DeferralOverrides(first_wait=maybe(rng.randint(1, 6)), cap=maybe(rng.randint(6, 64))),
        ),
        (
            "set_warning_overrides",
            WarningOverrides(
                surface=maybe(rng.random() < 0.5),
                default_action=maybe(rng.choice(list(AdvisoryAction))),
                by_kind={
                    kind: rng.choice(list(AdvisoryAction))
                    for kind in rng.sample(list(AdvisoryKind), rng.randrange(3))
                },
            ),
        ),
        ("set_require_judge", maybe(rng.random() < 0.5)),
        ("set_approved_agent_ids", rng.sample(JUDGES, rng.randrange(len(JUDGES) + 1))),
        (
            "set_judge_confirmations",
            [
                JudgeConfirmation(
                    session_id=f"conv-{k}",
                    agent_id=rng.choice(JUDGES),
                    client_name=rng.choice((None, "claude-code")),
                    model="claude-opus-5-5",
                    confirmed_at=_at(rng, step.i + k),
                )
                for k in range(rng.randrange(3))
            ],
        ),
    ]
    name, value = rng.choice(choices)
    return _call(name, value)


async def agents(step: Step) -> tuple[str, Script]:
    rng = step.rng
    agent_id = rng.choice(JUDGES)
    if rng.random() < 0.15:
        return _call("delete_agent", agent_id)
    return _call(
        "upsert_agent",
        Agent(
            id=agent_id,
            name=rng.choice(("", "Opus", "Reviewer")),
            former_ids=rng.sample(("old-1", "old-2"), rng.randrange(2)),
            authorised_at=_at(rng, step.i),
            first_seen_at=None if rng.random() < 0.5 else _at(rng, step.i),
            last_seen_at=None if rng.random() < 0.5 else _at(rng, step.i),
        ),
    )


# The menu, with the weight each step is drawn at.
MENU: tuple[tuple[Callable[[Step], Awaitable[tuple[str, Script] | None]], int], ...] = (
    (store_document, 4),
    (store_segment, 6),
    (resegment, 2),
    (store_node, 14),
    (restore_node, 3),
    (store_edge, 12),
    (delete_edge, 2),
    (store_embedding, 6),
    (supersede, 5),
    (set_status, 4),
    (merge, 4),
    (reverse_merge, 2),
    (create_timeline, 2),
    (add_timepoint, 3),
    (write_timeline, 2),
    (write_batch, 4),
    (record_decision, 6),
    (store_relation_label, 3),
    (record_relation_verdict, 3),
    (reopen, 2),
    (store_metacontext, 3),
    (counters, 3),
    (settings, 3),
    (agents, 2),
)


# --- Comparing ---


def _comparable(value: Any) -> Any:
    """A return value as plain data, so two backends' answers compare by content."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _comparable(v) for k, v in value.items()}
    if isinstance(value, set | frozenset):
        return sorted(_comparable(v) for v in value)
    if isinstance(value, list | tuple):
        return [_comparable(v) for v in value]
    if isinstance(value, float):
        return round(value, 9)
    return value


async def _outcome(store: Any, script: Script) -> tuple[tuple[str, Any], str]:
    """What a step did on one store, and, apart from it, the message of what it raised.

    Only the first half is compared: which exception, not its wording, is the
    protocol's business. The message is for the report when the two differ.
    """
    try:
        return ("returned", _comparable(await script(store))), ""
    except Exception as exc:  # noqa: BLE001 - which one is what gets compared
        return ("raised", type(exc).__name__), str(exc)


def _first_difference(expected: bytes, found: bytes) -> str:
    want, got = expected.decode().splitlines(), found.decode().splitlines()
    for line, (a, b) in enumerate(zip(want, got, strict=False), start=1):
        if a != b:
            return f"line {line}:\n  oracle:    {a}\n  candidate: {b}"
    return (
        f"line {min(len(want), len(got)) + 1}: oracle has {len(want)} lines, candidate {len(got)}"
    )


async def _compare_bundles(oracle: Any, candidate: Any) -> list[str]:
    exported = {}
    for name, store in (("oracle", oracle), ("candidate", candidate)):
        bundle = await export_graph(
            store, embedding_provider="none", embedding_model_id="none", exported_at=BASE
        )
        exported[name] = bundle_bytes(bundle)
    return [
        f"bundle section {section}, {_first_difference(want, exported['candidate'][section])}"
        for section, want in exported["oracle"].items()
        if section != MANIFEST_FILENAME and want != exported["candidate"][section]
    ]


async def _reads(
    store: Any, rng: random.Random, oracle: InMemoryStorage, *, engine_scores: bool
) -> dict[str, Any]:
    """Everything the bundle leaves out or answers differently, read the same way twice.

    `rng` is a fresh generator per store seeded alike, and `oracle` supplies the
    ids and terms to ask about, so both stores are asked identical questions.

    `engine_scores` is for a candidate that scores text in its own engine,
    which is SurrealDB: its analyzer stems and its statistics are its own, so
    its BM25 scores are not the in-memory store's, and the protocol promises
    the same hits only for rare terms. For such a candidate the lexical reads
    are rare terms alone, compared as sets of ids; every other candidate scores
    through `storage/bm25.py` and is compared hit for hit, score and order.
    """
    reads: dict[str, Any] = {}

    def by_id(models: Any) -> list[dict]:
        return sorted(_comparable(list(models)), key=lambda model: model["id"])

    for status in NodeStatus:
        reads[f"query_nodes({status.value})"] = by_id(await store.query_nodes(status=status))
        reads[f"count_nodes_by_type({status.value})"] = _comparable(
            await store.count_nodes_by_type(status=status)
        )
    reads["count_edges_by_type"] = _comparable(await store.count_edges_by_type())
    reads["count_nodes_without_metacontext"] = await store.count_nodes_without_metacontext()

    node_ids = sorted(oracle.nodes)
    sample = [*rng.sample(node_ids, min(len(node_ids), 12)), "n-missing"]
    for direction in ("from", "to"):
        for edge_type in (None, rng.choice(EDGE_TYPES)):
            found = await store.get_edges_for(sample, direction=direction, edge_type=edge_type)
            reads[f"get_edges_for({direction}, {edge_type})"] = {
                node_id: by_id(edges) for node_id, edges in found.items()
            }
    embeddings = await store.get_embeddings_for_items(node_ids)
    reads["get_embeddings_for_items"] = {k: by_id(v) for k, v in embeddings.items()}

    # Equal scores have no order the protocol settles: the in-memory store keeps
    # insertion order among them and SurrealDB keeps none. A zero vector, stored
    # or asked with, scores 0.0 against everything and makes such ties. So the
    # top ten are compared by score alone, which checks the cut, and the whole
    # ranking by score and then id, which checks every hit.
    for k in range(5):
        query = [0.0] * DIMENSION if k == 0 else _vector(rng)
        statuses = rng.choice(
            (frozenset({NodeStatus.ACTIVE}), frozenset({NodeStatus.ACTIVE, NodeStatus.HISTORICAL}))
        )
        node_type = rng.choice((None, *NodeType))
        model = rng.choice(MODELS)
        name = f"vector_search #{k} ({model}, {node_type}, {sorted(statuses)})"
        top = await store.vector_search(query, model, k=10, node_type=node_type, statuses=statuses)
        reads[f"{name}, top ten scores"] = [round(score, 6) for _, score in top]
        every = await store.vector_search(
            query, model, k=10_000, node_type=node_type, statuses=statuses
        )
        reads[f"{name}, every hit"] = sorted(
            ((round(score, 6), item) for item, score in every), key=lambda h: (-h[0], h[1])
        )

    contents = [n.content for n in oracle.nodes.values()] + [
        s.text for s in oracle.segments.values()
    ]
    words = sorted({w for text in contents for w in text.split()})
    tickets = sorted({w for w in words if w.startswith("TKT-")})
    for k in range(6):
        rare = k % 2 == 0 and bool(tickets)
        pool = tickets if rare else words
        terms = rng.sample(pool, min(len(pool), rng.randint(1, 2)))
        corpus = rng.choice(("nodes", "segments"))
        node_type = rng.choice(list(NodeType)) if corpus == "nodes" else None
        if engine_scores and not rare:
            continue
        hits = await store.text_search(terms, corpus=corpus, node_type=node_type, k=50)
        reads[f"text_search #{k} {terms} ({corpus}, {node_type})"] = (
            sorted(item for item, _ in hits)
            if engine_scores
            else [(item, round(score, 6)) for item, score in hits]
        )

    decision_ids = sorted(d.id for d in await oracle.query_decisions())
    for k in range(8):
        filters: dict[str, Any] = {}
        if rng.random() < 0.4:
            filters["agent_ids"] = rng.sample(JUDGES, rng.randrange(3))
        if rng.random() < 0.4:
            filters["kinds"] = rng.sample(list(DecisionKind), rng.randint(1, 6))
        if rng.random() < 0.3 and node_ids:
            filters["subject_ids"] = rng.sample(node_ids, min(len(node_ids), 4))
        if rng.random() < 0.2 and node_ids:
            filters["subject_id"] = rng.choice(node_ids)
        if rng.random() < 0.2 and decision_ids:
            filters["reviews"] = rng.choice(decision_ids)
        if rng.random() < 0.5:
            filters["since"] = _at(rng, rng.randrange(STEPS))
        if rng.random() < 0.5:
            filters["until"] = _at(rng, rng.randrange(STEPS))
        if rng.random() < 0.3:
            filters["limit"] = rng.randint(1, 10)
        found = await store.query_decisions(**filters)
        reads[f"query_decisions #{k} {sorted(filters)}"] = [d.id for d in found]
    reads["reviewed_decision_ids"] = sorted(await store.reviewed_decision_ids(decision_ids))

    for k in range(5):
        start = _at(rng, rng.randrange(STEPS))
        end = start + timedelta(seconds=rng.randint(1, 120))
        node_type = rng.choice((None, *NodeType))
        changed = await store.query_changes(start=start, end=end, node_type=node_type)
        reads[f"query_changes #{k} ({node_type})"] = sorted(n.id for n in changed)

    reads["judged_relation_pairs"] = sorted(await store.judged_relation_pairs())
    labels = sorted(label.id for label in await oracle.query_relation_labels())
    for k in range(3):
        pair = rng.sample(labels, 2) if len(labels) >= 2 else ["rl-a", "rl-b"]
        verdicts = await store.relation_verdicts_for(pair)
        reads[f"relation_verdicts_for #{k}"] = [v.id for v in verdicts]

    reads["get_reflect_counter"] = await store.get_reflect_counter()
    reads["get_reflect_round"] = await store.get_reflect_round()
    reads["get_deferral_overrides"] = _comparable(await store.get_deferral_overrides())
    reads["get_backup_counter"] = await store.get_backup_counter()
    reads["get_reflect_threshold_override"] = await store.get_reflect_threshold_override()
    reads["get_backup_threshold_override"] = await store.get_backup_threshold_override()
    reads["get_merge_overrides"] = _comparable(await store.get_merge_overrides())
    reads["get_warning_overrides"] = _comparable(await store.get_warning_overrides())
    reads["get_require_judge"] = await store.get_require_judge()
    reads["get_approved_agent_ids"] = await store.get_approved_agent_ids()
    reads["get_judge_confirmations"] = _comparable(await store.get_judge_confirmations())
    reads["list_agents"] = by_id(await store.list_agents())
    return reads


# --- The test ---


@pytest.fixture(params=["sqlite", "surrealdb", "postgres"])
async def candidate(request, tmp_path):
    """Each persistent backend, empty. Postgres skips without a DSN."""
    if request.param == "sqlite":
        store = SQLiteStorage(tmp_path / "graphs")
        await store.connect()
        yield store
        await store.close()
    elif request.param == "surrealdb":
        store = SurrealDBStorage(url="mem://")
        await store.connect()
        yield store
        await store.close()
    else:
        async with clean_postgres_store() as store:
            yield store


@pytest.mark.parametrize("seed", SEEDS)
async def test_a_random_history_leaves_the_same_graph(candidate, seed, monkeypatch):
    """Every step does the same on both stores, and the two graphs end the same.

    A step is chosen against the oracle and applied to the oracle first, so a
    choice never depends on the candidate. The first step whose outcome differs
    fails the test there, which names the operation; a graph that drifted
    without any step noticing fails at the bundle or at the reads.
    """
    reset = _deterministic_factories(monkeypatch)
    oracle = InMemoryStorage()
    rng = random.Random(seed)
    merges: list[dict] = []
    operations, weights = zip(*MENU, strict=True)
    context = f"seed {seed}, candidate {candidate.backend_name}"

    for i in range(STEPS):
        chosen = None
        while chosen is None:
            operation = rng.choices(operations, weights=weights)[0]
            chosen = await operation(Step(rng, oracle, i, merges))
        description, script = chosen
        reset(i)
        expected, expected_detail = await _outcome(oracle, script)
        reset(i)
        found, found_detail = await _outcome(candidate, script)
        assert found == expected, (
            f"{context}: step {i} ({description}) diverged\n"
            f"  oracle:    {json.dumps(expected, default=str)[:2000]} {expected_detail}\n"
            f"  candidate: {json.dumps(found, default=str)[:2000]} {found_detail}"
        )

    differences = await _compare_bundles(oracle, candidate)
    assert not differences, f"{context}: {differences[0]}"

    engine_scores = candidate.backend_name == "surrealdb"
    expected_reads = await _reads(
        oracle, random.Random(seed + 1_000_003), oracle, engine_scores=engine_scores
    )
    found_reads = await _reads(
        candidate, random.Random(seed + 1_000_003), oracle, engine_scores=engine_scores
    )
    for name, want in expected_reads.items():
        assert found_reads[name] == want, (
            f"{context}: {name} differs\n"
            f"  oracle:    {json.dumps(want, default=str)[:2000]}\n"
            f"  candidate: {json.dumps(found_reads[name], default=str)[:2000]}"
        )
