"""What a tool handed the agent, kept so the dashboard can show it.

The record is **our response**, not the agent's context: what lands in the
model's context is the MCP client's rendering of this, possibly truncated by the
client, inside a tool-result block we never see. The panel is labelled
"Response" for that reason, and this module's names follow
(`RETRIEVAL_PROVENANCE.md` §3.1).

Everything here is a value. Where the records live, who may see them, and when
they are written belongs to `server.py` and the hub.
"""

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from itertools import count

from pydantic import BaseModel, Field, model_validator

from epimemer.pipelines.query.types import SeedProvenance
from epimemer.visualization.ring import RETRIEVAL_RING_CAPACITY, remember

# Uncapped, a ring of these is a copy of the graph held in memory and served to
# any browser that connects. The hub binds 127.0.0.1 by default but
# `EPIMEMER_VIZ_HOST` overrides it, so these are a deliberate decision rather
# than an optimisation (§3.2).
RESPONSE_TEXT_CAP = 20_000

# The id list is as much a sizing problem as the payload: a `reflect` record can
# carry hundreds of nominees (§3, amended).
RETRIEVED_CAP = 200

_RECORD_IDS: Iterator[int] = count(1)


def next_record_id() -> str:
    """Monotonic and assigned here — not the hub's `seq`, which is per browser
    connection and restarts on reconnect."""
    return f"{next(_RECORD_IDS):012d}"


def _now() -> datetime:
    return datetime.now(UTC)


class RetrievedNode(BaseModel):
    """One node the response named, and how it was reached."""

    node_id: str
    provenance: SeedProvenance
    # Similarity or BM25, where the tool has one. `None` where it does not —
    # `find_nodes` and `graph_as_of` rank nothing, and a fabricated 1.0 would read as
    # a perfect match (§9).
    score: float | None = None


class RetrievalRecord(BaseModel):
    """One tool call, as the dashboard reads it.

    `retrieved` is `None` when the tool never declared its ids — a gap made
    visible rather than left as an indistinguishable empty list (§2.1). Read it
    through `retrieved_nodes`, which gives `[]` either way, and ask `declared`
    when the difference matters.
    """

    record_id: str
    at: datetime = Field(default_factory=_now)
    tool: str
    query: str
    graph: str
    retrieved: list[RetrievedNode] | None
    response_text: str
    truncated: bool = False

    @model_validator(mode="after")
    def _apply_caps(self) -> RetrievalRecord:
        """Trim to the caps, and say when the trim bit.

        Done here rather than at the call site so a record cannot be built
        uncapped by a caller who did not know it had to be — the ring, the RPC
        and the hub mirror all construct these.
        """
        text = self.response_text[:RESPONSE_TEXT_CAP]
        nodes = self.retrieved[:RETRIEVED_CAP] if self.retrieved is not None else None
        bit = len(text) < len(self.response_text) or (
            self.retrieved is not None and len(nodes or []) < len(self.retrieved)
        )
        if bit:
            object.__setattr__(self, "truncated", True)
        object.__setattr__(self, "response_text", text)
        object.__setattr__(self, "retrieved", nodes)
        return self

    @property
    def declared(self) -> bool:
        """Whether the tool said anything at all about what it returned."""
        return self.retrieved is not None

    @property
    def retrieved_nodes(self) -> list[RetrievedNode]:
        return self.retrieved or []


def structural_only(record: RetrievalRecord) -> RetrievalRecord:
    """The same record with nothing in it that a stranger should not read.

    The guard is the **bind**, not the process (§3.2 revised): on a non-loopback
    hub the mirror keeps what the selector and focus mode need — which record,
    which tool, which graph, which ids — and drops the query text and the
    payload. Undeclared stays undeclared, because "we do not know what this
    returned" is itself the fact worth keeping.
    """
    return RetrievalRecord(
        record_id=record.record_id,
        at=record.at,
        tool=record.tool,
        query="",
        graph=record.graph,
        retrieved=None if record.retrieved is None else list(record.retrieved),
        response_text="",
        truncated=record.truncated,
    )


def remember_record(
    ring: Sequence[RetrievalRecord], record: RetrievalRecord
) -> tuple[RetrievalRecord, ...]:
    """Append to the records ring, at the capacity §3.2 asks for."""
    return remember(ring, record, capacity=RETRIEVAL_RING_CAPACITY)


# --- The session's own log ---
#
# The ring itself is a value; this is the one mutable cell that holds the
# current one, so the lifespan dict can hand the same log to the tool choke
# point and to the hub client's RPC handler without either owning it.


def new_record_log() -> dict:
    """A holder for this session's records ring."""
    return {"ring": ()}


def append_record(log: dict, record: RetrievalRecord) -> None:
    log["ring"] = remember_record(log["ring"], record)


def records_of(log: dict) -> list[RetrievalRecord]:
    """Oldest first — the order they happened in."""
    return list(log["ring"])
