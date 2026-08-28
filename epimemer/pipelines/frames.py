"""A node's epistemic frame: withdrawing one, moving one, inheriting one,
declaring one.

Withdrawal and reassignment revise a judgment made at ingest. The rest is the
frame requirement, which
ended in a rule worth stating once here: **absence names no frame.** A node with
no `has_metacontext` edge is not in base reality — it is a node nobody said
anything about, and it shares a frame with nothing. Every function below exists
because of that: `shared_frame_set` and `frame_edges` so reflect re-states a
frame instead of minting a node without one, and `declare_frames` so a graph
written before the rule can stop holding any.

A metacontext assignment used to be **one-way**: `link` writes a
`has_metacontext` edge and nothing removed one, so a fact wrongly framed as
fiction stayed framed for ever. That is not cosmetic. Frames are load-bearing in
three places and all three fail *silently*:

- `merge_refusal` refuses a cross-frame pair, so a mis-framed fact becomes
  permanently unmergeable with its own twin;
- corroboration reads frames, so a mis-framed copy stops corroborating the real
  one and the count is quietly one short;
- a frame-scoped `search` misses it where it belongs and returns it where it
  does not.

**Not a supersession**, which is why `update` had not quietly solved it: the
claim is unchanged and the world has not moved, so `because` has no honest
value. It is `rejudge`'s category — *the judgment about the claim was wrong* —
and its own tool for a structural reason rather than a tidiness one. `rejudge`
is addressed by `node_id` and promises that no status, edge or lineage moves;
a frame revision moves an edge and changes what retrieval does, so the promise
would become false the day `rejudge` grew a frame field.

**The withdrawal deletes the edge rather than marking it.** The retraction rule
is the test: *before designing a mechanism for undo-without-delete, check whether
the read that would honour it is already there.* Here it is not — frames are
derived by scanning `has_metacontext` edges in `frames_for` and in
`get_metacontexts_for_node`, and a `withdrawn` marker would need every such site
to subtract it, with any site missed failing **open** (the frame still applies).
Deleting fails closed: every reader agrees, and the prior value survives in the
node's trail and in the journal row, which is where `rejudge` keeps its own.
"""

from typing import Sequence

from pydantic import BaseModel

from epimemer.core.types import (
    DecisionKind,
    EdgeType,
    JudgeRef,
    NodeEdge,
)
from epimemer.pipelines.reflection.review import frames_for
from epimemer.storage.protocol import StorageBackend


class ReframeRefused(BaseModel):
    """Why one frame revision was not made.

    Prose rather than a code, matching `RejudgeRefused`: the reasons do not form
    a vocabulary anything branches on.
    """

    node_id: str
    reason: str


class Reframed(BaseModel):
    """One frame revision, and what it moved.

    `frames_now` is the node's frame set after the change, so a caller can see
    where the claim landed rather than having to ask again. It is never empty:
    a revision that would leave a node stating no frame at all is refused.
    """

    node_id: str
    withdrew: str
    assigned: str | None = None
    frames_now: list[str] = []


def _strands(remaining: Sequence[str], assign: str | None) -> bool:
    """True when this revision would leave the node stating no frame at all."""
    return not remaining and assign is None


async def reframe_node(
    storage: StorageBackend,
    *,
    node_id: str,
    withdraw: str,
    because: str,
    assign: str | None = None,
    judge: JudgeRef | None = None,
) -> ReframeRefused | Reframed:
    """Withdraw one frame from a node, optionally putting another in its place.

    **`assign` makes the common repair atomic, and that is its whole point.** A
    fact mis-filed under frame A that belongs in frame B could be repaired by
    withdrawing then linking — but that path passes through *untagged*, where the
    claim is asserted in **every** frame, and it strands the node there
    permanently if the second call never happens. Moving in one call never
    reaches that state, and never reaches the last-frame question either.

    **Leaving a node stating no frame at all is refused outright.** It used to
    be allowed behind a flag, back when absence meant base reality and the
    withdrawal was a *promotion* worth authorising deliberately. Absence means
    nothing now: a frameless node shares a frame with nothing, so it is
    never compared, never merged, and returned by no scoped search. There is no
    longer any reason to want one, so the flag is gone rather than renamed —
    a claim goes somewhere, or it stays where it is.

    Nothing here moves a status or a lineage, and the node keeps its
    `judged_by` — that field records who wrote the wording, which is unchanged.
    """
    if not because.strip():
        return ReframeRefused(
            node_id=node_id,
            reason=(
                "`because` is required: this withdraws a framing another agent "
                "supplied after reading the material, and it changes what "
                "merges, what corroborates and what a frame-scoped search "
                "returns. The graph has to carry why."
            ),
        )

    node = await storage.get_node(node_id)
    if node is None:
        return ReframeRefused(node_id=node_id, reason=f"no such node: {node_id}.")

    edges = [
        edge
        for edge in await storage.get_edges_from(
            node_id, edge_type=EdgeType.HAS_METACONTEXT
        )
    ]
    held = {edge.dst_id for edge in edges}
    if withdraw not in held:
        return ReframeRefused(
            node_id=node_id,
            reason=(
                f"{node_id} is not framed by '{withdraw}'. It holds "
                f"{sorted(held) or 'no frames at all'}. A node stating no frame "
                f"has nothing to withdraw; `link` it into the frame it belongs "
                f"in, or declare the graph."
            ),
        )

    if assign is not None:
        if assign == withdraw:
            return ReframeRefused(
                node_id=node_id,
                reason=(
                    f"`assign` and `withdraw` are both '{withdraw}', so there "
                    f"is nothing to revise."
                ),
            )
        if await storage.get_metacontext(assign) is None:
            return ReframeRefused(
                node_id=node_id,
                reason=(
                    f"no metacontext '{assign}' in this graph. Frame ids are "
                    f"per graph, so one carried over from another names nothing "
                    f"here — and a node framed by nothing shares a frame with "
                    f"no other node. Create it with `create_metacontext` first."
                ),
            )

    remaining = sorted(held - {withdraw})
    if _strands(remaining, assign):
        return ReframeRefused(
            node_id=node_id,
            reason=(
                f"withdrawing '{withdraw}' would leave {node_id} stating no "
                f"frame at all, and a frameless node shares a frame with "
                f"nothing: never compared, never merged, and returned by no "
                f"scoped search. Pass assign=<metacontext_id> to say where the "
                f"claim belongs instead — the move happens in one step. If it "
                f"belongs in the real world, that frame has an id like any "
                f"other."
            ),
        )

    for edge in edges:
        if edge.dst_id == withdraw:
            await storage.delete_edge(edge.id)

    if assign is not None:
        await storage.store_edge(
            NodeEdge(
                src_id=node_id,
                dst_id=assign,
                type=EdgeType.HAS_METACONTEXT,
                judged_by=judge,
            )
        )

    # Append-only, and the only place the withdrawn frame survives on the node.
    # It matters more here than it does for a rejudgment: every search and
    # corroboration answer given while the frame was wrong was wrong, and this
    # entry's position in the trail — with the journal row's timestamp beside it
    # — is what lets a reviewer bound which answers those were.
    node.metadata = {
        **node.metadata,
        "reframings": [
            *node.metadata.get("reframings", []),
            {
                "because": because,
                "withdrew": withdraw,
                "assigned": assign,
                "judged_by": judge.model_dump(mode="json") if judge else None,
            },
        ],
    }
    await storage.store_node(node)

    frames_now = sorted([*remaining, *([assign] if assign is not None else [])])
    return Reframed(
        node_id=node_id,
        withdrew=withdraw,
        assigned=assign,
        frames_now=frames_now,
    )


async def shared_frame_set(
    node_ids: Sequence[str], storage: StorageBackend
) -> set[str] | None:
    """The one frame set all of these nodes stand in, or `None` if they differ.

    **Exact set equality, not overlap**, and `fact_dedup` states the reason for
    the fact layer: a node derived from several sources inherits the *union* of
    their frames, so deriving one node from a base-reality claim and a fiction
    one leaves it asserting both — the worst outcome available. `same_frame`
    asks whether two nodes share *at least one* frame, which is the right
    question for a contradiction and the wrong one here.

    A node stating no frame has an empty set, which is equal only to another
    empty one. So two undeclared nodes may still be combined — neither says
    anything a merge could contradict — while an undeclared node and a declared
    one are refused, because combining them would put a claim nobody framed into
    a frame somebody named. `epimemer frames declare` is what ends that state;
    `same_frame` answers the *overlap* question differently for the same pair,
    and says why.
    """
    frames = await frames_for(list(node_ids), storage)
    distinct = {frozenset(frames[node_id]) for node_id in node_ids}
    return set(next(iter(distinct))) if len(distinct) == 1 else None


def frame_edges(
    node_id: str, frames: Sequence[str] | set[str], *, judge: JudgeRef | None = None
) -> list[NodeEdge]:
    """`has_metacontext` edges putting one node in each of `frames`.

    `the-real` is written like any other id: it is a conventional name for the
    frame holding real-world claims, not a mechanism, and nothing reads it
    specially since absence stopped meaning it.
    """
    return [
        NodeEdge(
            src_id=node_id,
            dst_id=frame,
            type=EdgeType.HAS_METACONTEXT,
            judged_by=judge,
        )
        for frame in sorted(frames)
    ]


class FrameDeclaration(BaseModel):
    """What one declaration sweep found and what it stamped.

    `already_framed` is reported beside `declared` because the two together are
    the migration's completeness check: a graph is done when `unframed` reaches
    zero, and a rerun that declares nothing is how you find that out.
    """

    frame: str
    declared: int
    already_framed: int
    node_ids: list[str] = []


async def declare_frames(
    storage: StorageBackend,
    *,
    frame: str,
    judge: JudgeRef | None = None,
) -> FrameDeclaration:
    """Stamp `frame` on every active node that carries no frame at all.

    **A user's declaration, not a migration.** Nothing derives this from the
    content: somebody is stating that the claims in this graph were always about
    one world, and taking responsibility for having said so. That is why it
    lives behind the CLI — the same reasoning that keeps judge approval out of
    agent reach — and why the edges carry a judge.

    **Idempotent, and it never touches a node that already has a frame.** A node
    framed as fiction must not acquire a second frame from a sweep aimed at the
    ones nobody spoke for; a node already declared must not be declared twice.
    So the predicate is *no frames at all*, which is also the state that stops
    existing as the sweep runs.

    One journal row for the whole sweep, naming the nodes it stamped — the
    granularity an archival sweep uses, and for the same reason: this is one act
    of judgment applied to whatever it found, not one verdict per node.

    **The frame has to exist first, and this refuses rather than creating it.**
    A sweep that minted the frame it was about to stamp would be the one thing
    every other path here refuses — an edge pointing at a metacontext nobody
    described — done in bulk, on the nodes least able to survive it. Creating it
    is the caller's separate act, which is what makes it a declaration rather
    than a side effect.
    """
    from epimemer.mcp.tools import journal

    if await storage.get_metacontext(frame) is None:
        raise ValueError(
            f"no metacontext '{frame}' in graph "
            f"'{storage.current_database}'. Frames are per graph, and a "
            f"declaration cannot point at one that does not exist — the nodes "
            f"would end up in a frame they share with nothing, which is worse "
            f"than the state this is fixing. Create it first."
        )

    nodes = await storage.query_nodes()
    node_ids = [node.id for node in nodes]
    if not node_ids:
        return FrameDeclaration(frame=frame, declared=0, already_framed=0)

    framed = await storage.get_edges_for(
        node_ids, direction="from", edge_type=EdgeType.HAS_METACONTEXT
    )
    unframed = [node_id for node_id in node_ids if not framed[node_id]]

    for node_id in unframed:
        for edge in frame_edges(node_id, [frame], judge=judge):
            await storage.store_edge(edge)

    if unframed:
        await journal(
            storage, DecisionKind.FRAME_DECLARATION, unframed,
            judge=judge, frame=frame,
        )

    return FrameDeclaration(
        frame=frame,
        declared=len(unframed),
        already_framed=len(node_ids) - len(unframed),
        node_ids=unframed,
    )


__all__ = [
    "FrameDeclaration",
    "Reframed",
    "ReframeRefused",
    "declare_frames",
    "frame_edges",
    "reframe_node",
    "shared_frame_set",
]
