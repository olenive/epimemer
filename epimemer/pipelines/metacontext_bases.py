"""One metacontext building on another: the `based_on` edge and its divergence.

A world can share its history with another up to a point and part from it
there: a novel set in the real 1940s, an alternate history, a role-playing
setting that follows real events until a chosen year. The `based_on` edge
records that, from the derived metacontext to its base, with `diverges` in its
metadata saying where the two part.

**Association, not inheritance.** No claim flows along the edge. A search
scoped to the derived metacontext returns what stands in it and nothing from
the base; a reader who wants the shared history names both metacontexts. The
edge is reported where a reader is deciding what to search, so the
relationship is learned from the graph rather than guessed.

**One base per metacontext.** Setting a base replaces the previous one, and
the previous edge is deleted rather than marked, on the reasoning
`reassign_metacontext` gives for a withdrawn metacontext: a marker every
reader would have to subtract fails open wherever one forgets.

No judge and no journal row: a base is structure, like creating the
metacontext itself.
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import BaseModel, TypeAdapter, ValidationError

from epimemer.core.temporal import ImpreciseInstant, NamedInstant, PreciseInstant
from epimemer.core.types import EdgeType, EndpointKind, NodeEdge, live_edges
from epimemer.storage.protocol import StorageBackend

# What a divergence can be: a date, or a moment the source named in words. An
# unknown instant would record that the worlds part somewhere nobody can say,
# which tells a reader nothing about how far to trust the base, and an
# unbounded one would say they never shared a history, which is no base at all.
# Classes rather than the discriminator, so a kind added to `temporal.py` is
# refused here until somebody decides it can be a divergence.
DIVERGENCE_INSTANTS = (PreciseInstant, NamedInstant)


class MetacontextBase(BaseModel):
    """The metacontext one builds on, as a reader is shown it."""

    metacontext_id: str
    content: str
    diverges: dict | None = None


class BaseSet(BaseModel):
    """What one `set_metacontext_base` changed."""

    metacontext_id: str
    based_on: MetacontextBase | None = None
    replaced: str | None = None


def _divergence_shapes() -> str:
    """The two shapes a divergence takes, written out from the models themselves."""
    date = PreciseInstant(at=datetime(1990, 1, 1, tzinfo=UTC))
    named = NamedInstant(label="the late 1980s")
    return (
        f"a date, {json.dumps(date.model_dump(mode='json', exclude_none=True))}, or a "
        f"named moment, {json.dumps(named.model_dump(mode='json'))}"
    )


def parse_divergence(diverges: dict | None) -> dict | None:
    """`diverges` as it is stored, or raise saying why it is refused.

    Parsed with the instant model validity intervals use, so a divergence and a
    period read the same way, and stored in its JSON form.
    """
    if diverges is None:
        return None
    try:
        instant = TypeAdapter(ImpreciseInstant).validate_python(diverges)
    except ValidationError as problem:
        raise ValueError(
            f"`diverges` is not an instant ({problem.error_count()} problem(s)). "
            f"Give {_divergence_shapes()}."
        ) from None
    if not isinstance(instant, DIVERGENCE_INSTANTS):
        raise ValueError(
            f"`diverges` {json.dumps(diverges, default=str)} is neither a date nor a named moment. "
            f"It tells a reader how far to trust the base's claims, so it has to "
            f"say where the two part: give {_divergence_shapes()}, or leave "
            f"`diverges` out if that is not known."
        )
    return instant.model_dump(mode="json")


def _current(edges: Sequence[NodeEdge]) -> NodeEdge | None:
    """The base edge in force: the newest live one, where the setter keeps one."""
    live = live_edges(edges)
    return max(live, key=lambda edge: (edge.created_at, edge.id)) if live else None


async def metacontext_bases(
    storage: StorageBackend, metacontext_ids: Sequence[str]
) -> dict[str, MetacontextBase]:
    """The base of each of these metacontexts that has one, keyed by its id.

    One batched edge read. A metacontext with no base is absent from the map,
    so an empty map means none of them builds on anything.
    """
    found = await storage.get_edges_for(
        list(metacontext_ids), direction="from", edge_type=EdgeType.BASED_ON
    )
    bases: dict[str, MetacontextBase] = {}
    for metacontext_id, edges in found.items():
        edge = _current(edges)
        if edge is None:
            continue
        base = await storage.get_metacontext(edge.dst_id)
        bases[metacontext_id] = MetacontextBase(
            metacontext_id=edge.dst_id,
            content=base.content if base is not None else edge.dst_id,
            diverges=edge.metadata.get("diverges"),
        )
    return bases


async def _chain_reaches(storage: StorageBackend, start: str, target: str) -> list[str] | None:
    """The chain of bases from `start` that reaches `target`, or `None`.

    Visited ids are remembered, so a cycle already in the graph ends the walk
    rather than looping.
    """
    chain = [start]
    seen = {start}
    current = start
    while current != target:
        edge = _current(await storage.get_edges_from(current, edge_type=EdgeType.BASED_ON))
        if edge is None or edge.dst_id in seen:
            return None
        current = edge.dst_id
        seen.add(current)
        chain.append(current)
    return chain


async def check_base(
    storage: StorageBackend,
    *,
    metacontext_id: str,
    base_id: str | None,
    diverges: dict | None,
) -> dict | None:
    """Raise unless `metacontext_id` may be based on `base_id`; the parsed divergence.

    Asks nothing of `metacontext_id` itself, so `create_metacontext` can check
    before it writes the metacontext and leave nothing behind on a refusal.
    """
    from epimemer.mcp.tools import require_edge_shape, require_metacontext

    if diverges is not None and base_id is None:
        raise ValueError(
            "`diverges` was given without a base. It says where a metacontext "
            "parts from the one it builds on, so name that one as well."
        )
    divergence = parse_divergence(diverges)
    if base_id is None:
        return None
    require_edge_shape(EdgeType.BASED_ON, EndpointKind.METACONTEXT, EndpointKind.METACONTEXT)
    if base_id == metacontext_id:
        raise ValueError(
            f"'{metacontext_id}' cannot be based on itself. A base is another "
            f"metacontext whose history this one shares up to its divergence."
        )
    await require_metacontext(base_id, storage)
    chain = await _chain_reaches(storage, base_id, metacontext_id)
    if chain is not None:
        raise ValueError(
            f"'{base_id}' already builds on '{metacontext_id}' "
            f"({' -> '.join(chain)}), so basing '{metacontext_id}' on it would "
            f"make a cycle. Each metacontext has one base, and following the "
            f"bases has to end."
        )
    return divergence


async def write_base(
    storage: StorageBackend,
    *,
    metacontext_id: str,
    base_id: str | None,
    divergence: dict | None,
) -> str | None:
    """Replace `metacontext_id`'s base with `base_id`, or withdraw it; the base replaced.

    Unchecked: `check_base` is what makes the arguments safe, and every caller
    runs it first. The new edge is written before the old ones are deleted, so
    a failure between the two leaves the newest edge in force rather than none.
    """
    previous = await storage.get_edges_from(metacontext_id, edge_type=EdgeType.BASED_ON)
    current = _current(previous)
    if base_id is not None:
        await storage.store_edge(
            NodeEdge(
                src_id=metacontext_id,
                dst_id=base_id,
                type=EdgeType.BASED_ON,
                metadata={"diverges": divergence},
            )
        )
    for edge in previous:
        await storage.delete_edge(edge.id)
    return current.dst_id if current is not None else None


async def set_metacontext_base(
    storage: StorageBackend,
    *,
    metacontext_id: str,
    base_id: str | None = None,
    diverges: dict | None = None,
) -> BaseSet:
    """Set, replace or withdraw the base of an existing metacontext."""
    from epimemer.mcp.tools import require_metacontext

    await require_metacontext(metacontext_id, storage)
    divergence = await check_base(
        storage, metacontext_id=metacontext_id, base_id=base_id, diverges=diverges
    )
    replaced = await write_base(
        storage, metacontext_id=metacontext_id, base_id=base_id, divergence=divergence
    )
    bases = await metacontext_bases(storage, [metacontext_id])
    return BaseSet(
        metacontext_id=metacontext_id,
        based_on=bases.get(metacontext_id),
        replaced=replaced,
    )
