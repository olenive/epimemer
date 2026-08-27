"""Withdrawing and moving a node's epistemic frame (`ISSUES.md` #66, part 1).

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

**The withdrawal deletes the edge rather than marking it.** #68's carry-forward
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
    BASE_METACONTEXT_ID,
    EdgeType,
    JudgeRef,
    NodeEdge,
)
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
    the promotion in the response rather than having to ask again. An empty list
    means untagged, which is base reality.
    """

    node_id: str
    withdrew: str
    assigned: str | None = None
    frames_now: list[str] = []
    to_base_reality: bool = False


def _promotes(remaining: Sequence[str], assign: str | None) -> bool:
    """True when this revision would leave the node in no frame at all."""
    return not remaining and assign is None


async def reframe_node(
    storage: StorageBackend,
    *,
    node_id: str,
    withdraw: str,
    because: str,
    assign: str | None = None,
    to_base_reality: bool = False,
    judge: JudgeRef | None = None,
) -> ReframeRefused | Reframed:
    """Withdraw one frame from a node, optionally putting another in its place.

    **`assign` makes the common repair atomic, and that is its whole point.** A
    fact mis-filed under frame A that belongs in frame B could be repaired by
    withdrawing then linking — but that path passes through *untagged*, where the
    claim is asserted in **every** frame, and it strands the node there
    permanently if the second call never happens. Moving in one call never
    reaches that state, and never reaches the last-frame question either.

    **Leaving a node with no frames is a promotion, and has to be said out
    loud.** Untagged is not neutral: base-reality knowledge is inherited by every
    frame, so a fact that was claimed inside one novel becomes a fact claimed in
    all of them. Where the withdrawal would leave nothing, `to_base_reality=True`
    is required — and it is required rather than inferred for `expected_graph`'s
    reason: the check is worth something only because the agent's intent is
    stated independently of the state. Passing it where it does not apply is
    refused too, because a flag that lies about what it authorised is worse than
    no flag.

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
                f"{sorted(held) or 'no frames at all, which is base reality'}. "
                f"An untagged node needs nothing withdrawn — it is already in "
                f"base reality."
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
        if assign != BASE_METACONTEXT_ID and await storage.get_metacontext(
            assign
        ) is None:
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
    promotes = _promotes(remaining, assign)
    if promotes and not to_base_reality:
        return ReframeRefused(
            node_id=node_id,
            reason=(
                f"withdrawing '{withdraw}' would leave {node_id} in no frame at "
                f"all, and untagged is not neutral: base-reality knowledge is "
                f"inherited by every frame, so this claim would go from being "
                f"asserted in one world to being asserted in all of them. If "
                f"that is what you mean, pass to_base_reality=True. If it "
                f"belongs in a different frame, pass assign=<metacontext_id> "
                f"instead and the move happens in one step, never passing "
                f"through base reality."
            ),
        )
    if to_base_reality and not promotes:
        landing = assign if assign is not None else ", ".join(remaining)
        return ReframeRefused(
            node_id=node_id,
            reason=(
                f"to_base_reality=True says this withdrawal promotes the claim "
                f"to base reality, and it does not — {node_id} would still be "
                f"framed by {landing}. Drop the flag."
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
                "to_base_reality": promotes,
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
        to_base_reality=promotes,
    )


__all__ = ["Reframed", "ReframeRefused", "reframe_node"]
