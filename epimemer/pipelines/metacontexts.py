"""A node's metacontext: withdrawing one, moving one, inheriting one,
declaring one.

Withdrawal and reassignment revise a judgment made at ingest. The rest is the
metacontext requirement, which ended in a rule worth stating once here:
**absence names no metacontext.** A node with no `has_metacontext` edge is not
in base reality, it is a node nobody said anything about, and it shares a
metacontext with nothing. Every function below exists because of that:
`shared_metacontext_set` and `metacontext_edges` so reflect re-states a
metacontext instead of minting a node without one, `declare_metacontext` so
a graph written before the rule can stop holding any, and
`stamp_tag_metacontexts` so a name stands where it is used rather than nowhere.

A metacontext assignment used to be **one-way**: `link` writes a
`has_metacontext` edge and nothing removed one, so a fact wrongly assigned to a
fiction metacontext stayed there for ever. That is not cosmetic. Metacontexts
are load-bearing in three places and all three fail *silently*:

- `merge_refusal` refuses a cross-metacontext pair, so a misassigned fact
  becomes permanently unmergeable with its own twin;
- corroboration reads metacontexts, so a misassigned copy stops corroborating
  the real one and the count is quietly one short;
- a metacontext-scoped `search` misses it where it belongs and returns it where
  it does not.

**Not a supersession**, which is why `update` had not quietly solved it: the
claim is unchanged and the world has not moved, so `because` has no honest
value. It is `rejudge`'s category — *the judgment about the claim was wrong* —
and its own tool for a structural reason rather than a tidiness one. `rejudge`
is addressed by `node_id` and promises that no status, edge or lineage moves;
a metacontext revision moves an edge and changes what retrieval does, so the promise
would become false the day `rejudge` grew a metacontext field.

**The withdrawal deletes the edge rather than marking it.** The retraction rule
is the test: *before designing a mechanism for undo-without-delete, check whether
the read that would honour it is already there.* Here it is not — metacontexts are
derived by scanning `has_metacontext` edges in `metacontexts_for` and in
`get_metacontexts_for_node`, and a `withdrawn` marker would need every such site
to subtract it, with any site missed failing **open** (the metacontext still applies).
Deleting fails closed: every reader agrees, and the prior value survives in the
node's trail and in the journal row, which is where `rejudge` keeps its own.
"""

from collections.abc import Iterable, Sequence

from pydantic import BaseModel

from epimemer.core.types import (
    DecisionKind,
    EdgeType,
    JudgeRef,
    NodeEdge,
    NodeType,
    Topic,
)
from epimemer.pipelines.reflection.review import metacontexts_for
from epimemer.storage.protocol import StorageBackend


class ReassignmentRefused(BaseModel):
    """Why one metacontext revision was not made.

    Prose rather than a code, matching `RejudgeRefused`: the reasons do not form
    a vocabulary anything branches on.
    """

    node_id: str
    reason: str


class Reassigned(BaseModel):
    """One metacontext revision, and what it moved.

    `metacontexts_now` is the node's metacontext set after the change, so a
    caller can see where the claim landed rather than having to ask again. It is
    never empty: a revision that would leave a node stating no metacontext at
    all is refused.
    """

    node_id: str
    withdrew: str
    assigned: str | None = None
    metacontexts_now: list[str] = []


def _strands(remaining: Sequence[str], assign: str | None) -> bool:
    """True when this revision would leave the node stating no metacontext at all."""
    return not remaining and assign is None


async def reassign_metacontext(
    storage: StorageBackend,
    *,
    node_id: str,
    withdraw: str,
    because: str,
    assign: str | None = None,
    judge: JudgeRef | None = None,
) -> ReassignmentRefused | Reassigned:
    """Withdraw one metacontext from a node, optionally putting another in its place.

    **`assign` makes the common repair atomic, and that is its whole point.** A
    fact mis-filed under metacontext A that belongs in metacontext B could be
    repaired by withdrawing then linking, but that path passes through
    *untagged*, where the claim is asserted in **every** metacontext, and it
    strands the node there permanently if the second call never happens. Moving
    in one call never reaches that state, and never reaches the
    last-metacontext question either.

    **Leaving a node stating no metacontext at all is refused outright.** It
    used to be allowed behind a flag, back when absence meant base reality and
    the withdrawal was a *promotion* worth authorising deliberately. Absence
    means nothing now: a node without a metacontext shares a metacontext with
    nothing, so it is never compared, never merged, and returned by no scoped
    search. There is no longer any reason to want one, so the flag is gone
    rather than renamed: a claim goes somewhere, or it stays where it is.

    Nothing here moves a status or a lineage, and the node keeps its
    `judged_by` — that field records who wrote the wording, which is unchanged.
    """
    if not because.strip():
        return ReassignmentRefused(
            node_id=node_id,
            reason=(
                "`because` is required: this withdraws a metacontext another "
                "agent assigned after reading the material, and it changes what "
                "merges, what corroborates and what a metacontext-scoped search "
                "returns. The graph has to carry why."
            ),
        )

    node = await storage.get_node(node_id)
    if node is None:
        return ReassignmentRefused(node_id=node_id, reason=f"no such node: {node_id}.")

    edges = [
        edge for edge in await storage.get_edges_from(node_id, edge_type=EdgeType.HAS_METACONTEXT)
    ]
    held = {edge.dst_id for edge in edges}
    if withdraw not in held:
        return ReassignmentRefused(
            node_id=node_id,
            reason=(
                f"{node_id} does not stand in '{withdraw}'. It holds "
                f"{sorted(held) or 'no metacontexts at all'}. A node stating no "
                f"metacontext has nothing to withdraw; `link` it into the "
                f"metacontext it belongs in, or declare the graph."
            ),
        )

    if assign is not None:
        if assign == withdraw:
            return ReassignmentRefused(
                node_id=node_id,
                reason=(
                    f"`assign` and `withdraw` are both '{withdraw}', so there is nothing to revise."
                ),
            )
        if await storage.get_metacontext(assign) is None:
            return ReassignmentRefused(
                node_id=node_id,
                reason=(
                    f"no metacontext '{assign}' in this graph. Metacontext ids "
                    f"are per graph, so one carried over from another names "
                    f"nothing here, and a node standing in nothing shares a "
                    f"metacontext with no other node. Create it with "
                    f"`create_metacontext` first."
                ),
            )

    remaining = sorted(held - {withdraw})
    if _strands(remaining, assign):
        return ReassignmentRefused(
            node_id=node_id,
            reason=(
                f"withdrawing '{withdraw}' would leave {node_id} stating no "
                f"metacontext at all, and a node without a metacontext shares a "
                f"metacontext with nothing: never compared, never merged, and "
                f"returned by no scoped search. Pass assign=<metacontext_id> to "
                f"say where the claim belongs instead, and the move happens in "
                f"one step. If it belongs in the real world, that metacontext "
                f"has an id like any other."
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

    # Append-only, and the only place the withdrawn metacontext survives on the node.
    # It matters more here than it does for a rejudgment: every search and
    # corroboration answer given while the metacontext was wrong was wrong, and this
    # entry's position in the trail — with the journal row's timestamp beside it
    # — is what lets a reviewer bound which answers those were.
    node.metadata = {
        **node.metadata,
        "metacontext_reassignments": [
            *node.metadata.get("metacontext_reassignments", []),
            {
                "because": because,
                "withdrew": withdraw,
                "assigned": assign,
                "judged_by": judge.model_dump(mode="json") if judge else None,
            },
        ],
    }
    await storage.store_node(node)

    metacontexts_now = sorted([*remaining, *([assign] if assign is not None else [])])
    return Reassigned(
        node_id=node_id,
        withdrew=withdraw,
        assigned=assign,
        metacontexts_now=metacontexts_now,
    )


async def shared_metacontext_set(
    node_ids: Sequence[str], storage: StorageBackend
) -> set[str] | None:
    """The one metacontext set all of these nodes stand in, or `None` if they differ.

    **Exact set equality, not overlap**, and `fact_dedup` states the reason for
    the fact layer: a node derived from several sources inherits the *union* of
    their metacontexts, so deriving one node from a base-reality claim and a
    fiction one leaves it asserting both, the worst outcome available.
    `same_metacontext` asks whether two nodes share *at least one* metacontext,
    which is the right question for a contradiction and the wrong one here.

    A node stating no metacontext has an empty set, which is equal only to
    another empty one. So two undeclared nodes may still be combined, since
    neither says anything a merge could contradict, while an undeclared node and
    a declared one are refused: combining them would put a claim nobody placed
    into a metacontext somebody named. `epimemer metacontexts declare` is what
    ends that state; `same_metacontext` answers the *overlap* question
    differently for the same pair, and says why.
    """
    metacontexts = await metacontexts_for(list(node_ids), storage)
    distinct = {frozenset(metacontexts[node_id]) for node_id in node_ids}
    return set(next(iter(distinct))) if len(distinct) == 1 else None


async def combined_metacontext_set(
    topics: Sequence[Topic], storage: StorageBackend
) -> set[str] | None:
    """The metacontexts a node combining these topics may stand in, or `None`.

    Two answers, and which one applies is decided by what the topics are. Every
    one a topic node created from a tag: the **union**, because a name stands in
    every metacontext it is used from and a node gathering names is used from
    all of them. That is the answer the all-tag merge exemption gives, and it is
    the same answer stated once for parent synthesis. Any topic a statement: the
    **one set they all share**, from `shared_metacontext_set`, and `None` where
    they differ, because a claim combining a fiction claim and a real one would
    assert in both worlds.
    """
    ids = [topic.id for topic in topics]
    if topics and all(created_from_tag(topic) for topic in topics):
        return set().union(*(await metacontexts_for(ids, storage)).values())
    return await shared_metacontext_set(ids, storage)


TAG_EXTRACTION_METHOD = "agent:tag"
"""What `store_decomposition` stamps on a Topic it creates to carry a tag."""


def created_from_tag(node: Topic) -> bool:
    """Whether this Topic is a tag rather than a statement about a world.

    A tag is a name, so it asserts nothing, and `_tag_topic` resolves it by
    content: one name is one topic node, whatever world it is used from. What
    that node stands in is derived from use rather than judged: it is **the
    union of the metacontexts of the nodes tagged with it**, so `store_decomposition`
    adds this call's metacontext to it, and the set only grows. The union is the
    worst answer available for a claim, which is what `shared_metacontext_set`
    says, and the right one for a name: a name in two worlds records that it was
    used from both. That is why the topic-merge gate exempts an all-tag merge.
    """
    return node.extraction_method == TAG_EXTRACTION_METHOD


def metacontext_edges(
    node_id: str, metacontexts: Sequence[str] | set[str], *, judge: JudgeRef | None = None
) -> list[NodeEdge]:
    """`has_metacontext` edges putting one node in each of `metacontexts`.

    `the-real` is written like any other id: it is a conventional name for the
    metacontext holding real-world claims, not a mechanism, and nothing reads it
    specially since absence stopped meaning it.
    """
    return [
        NodeEdge(
            src_id=node_id,
            dst_id=metacontext,
            type=EdgeType.HAS_METACONTEXT,
            judged_by=judge,
        )
        for metacontext in sorted(metacontexts)
    ]


def metacontexts_to_stamp(held: set[str], used_from: Iterable[set[str]]) -> set[str]:
    """The metacontexts a tag is used from and does not yet stand in.

    Add-only, and that is the whole rule: a tag stamped by a declaration keeps
    what it was given, and a use withdrawn from a node here leaves the name
    standing where it stood. Nothing derives a removal, so this never returns
    one, and running it twice returns nothing the second time.
    """
    return set().union(*used_from) - held


class TagMetacontextStamping(BaseModel):
    """What one stamping pass over a graph's tags found and what it wrote.

    `topics_seen` beside `edges_written` is the idempotence check: a rerun sees
    the same topic nodes and writes nothing.
    """

    topics_seen: int
    edges_written: int
    topic_ids: list[str] = []


async def stamp_tag_metacontexts(storage: StorageBackend) -> TagMetacontextStamping:
    """Put every topic node created from a tag in the metacontexts it is used from.

    The union of the metacontexts of the nodes that point at it with a
    `tagged_with_topic` edge, minus what it already holds. This brings a graph
    written before ingest wrote the edge up to the rule the ingest now keeps, so
    a scoped read returns the tags used from that scope instead of dropping them
    and every edge that reached them.

    **No judge and no journal row.** This derives edges from edges already in
    the graph, which anybody can re-derive from the same rows; a declaration is a
    person stating that claims nobody spoke for were about one world, and this is
    not that.

    A tag that tags nothing is left alone: it is used from nowhere, so the union
    is empty and there is nothing to say about it until it is next used. Uses are
    counted whatever status the tagged node now has, matching ingest: the name
    was used from that world at the time, and archiving the claim does not
    unsay it.
    """
    topics = [
        node
        for node in await storage.query_nodes(node_type=NodeType.TOPIC)
        if isinstance(node, Topic) and created_from_tag(node)
    ]
    if not topics:
        return TagMetacontextStamping(topics_seen=0, edges_written=0)

    topic_ids = [topic.id for topic in topics]
    uses = await storage.get_edges_for(
        topic_ids, direction="to", edge_type=EdgeType.TAGGED_WITH_TOPIC
    )
    held = await storage.get_edges_for(
        topic_ids, direction="from", edge_type=EdgeType.HAS_METACONTEXT
    )
    tagged_ids = sorted({edge.src_id for edges in uses.values() for edge in edges})
    tagged_metacontexts = await metacontexts_for(tagged_ids, storage)

    written = 0
    stamped: list[str] = []
    for topic_id in topic_ids:
        missing = metacontexts_to_stamp(
            {edge.dst_id for edge in held[topic_id]},
            (tagged_metacontexts[edge.src_id] for edge in uses[topic_id]),
        )
        if not missing:
            continue
        for edge in metacontext_edges(topic_id, missing):
            await storage.store_edge(edge)
        written += len(missing)
        stamped.append(topic_id)

    return TagMetacontextStamping(topics_seen=len(topics), edges_written=written, topic_ids=stamped)


class MetacontextDeclaration(BaseModel):
    """What one declaration sweep found and what it stamped.

    `already_in_metacontext` is reported beside `declared` because the two
    together are the migration's completeness check: a graph is done when the
    count of nodes without a metacontext reaches zero, and a rerun that declares
    nothing is how you find that out.
    """

    metacontext: str
    declared: int
    already_in_metacontext: int
    node_ids: list[str] = []


async def declare_metacontext(
    storage: StorageBackend,
    *,
    metacontext: str,
    judge: JudgeRef | None = None,
) -> MetacontextDeclaration:
    """Stamp `metacontext` on every active node that carries no metacontext at all.

    **A user's declaration, not a migration.** Nothing derives this from the
    content: somebody is stating that the claims in this graph were always about
    one world, and taking responsibility for having said so. That is why it
    lives behind the CLI — the same reasoning that keeps judge approval out of
    agent reach — and why the edges carry a judge.

    **Idempotent, and it never touches a node that already has a metacontext.**
    A node standing in a fiction metacontext must not acquire a second one from
    a sweep aimed at the nodes nobody spoke for; a node already declared must
    not be declared twice. So the predicate is *no metacontexts at all*, which
    is also the state that stops existing as the sweep runs.

    One journal row for the whole sweep, naming the nodes it stamped — the
    granularity an archival sweep uses, and for the same reason: this is one act
    of judgment applied to whatever it found, not one verdict per node.

    **The metacontext has to exist first, and this refuses rather than creating
    it.** A sweep that minted the metacontext it was about to stamp would be the
    one thing every other path here refuses, an edge pointing at a metacontext
    nobody described, done in bulk on the nodes least able to survive it.
    Creating it is the caller's separate act, which is what makes it a
    declaration rather than a side effect.
    """
    from epimemer.mcp.tools import journal

    if await storage.get_metacontext(metacontext) is None:
        raise ValueError(
            f"no metacontext '{metacontext}' in graph "
            f"'{storage.current_database}'. Metacontexts are per graph, and a "
            f"declaration cannot point at one that does not exist: the nodes "
            f"would end up in a metacontext they share with nothing, which is "
            f"worse than the state this is fixing. Create it first."
        )

    nodes = await storage.query_nodes()
    node_ids = [node.id for node in nodes]
    if not node_ids:
        return MetacontextDeclaration(metacontext=metacontext, declared=0, already_in_metacontext=0)

    held = await storage.get_edges_for(
        node_ids, direction="from", edge_type=EdgeType.HAS_METACONTEXT
    )
    without_metacontext = [node_id for node_id in node_ids if not held[node_id]]

    for node_id in without_metacontext:
        for edge in metacontext_edges(node_id, [metacontext], judge=judge):
            await storage.store_edge(edge)

    if without_metacontext:
        await journal(
            storage,
            DecisionKind.METACONTEXT_DECLARATION,
            without_metacontext,
            judge=judge,
            metacontext=metacontext,
        )

    return MetacontextDeclaration(
        metacontext=metacontext,
        declared=len(without_metacontext),
        already_in_metacontext=len(node_ids) - len(without_metacontext),
        node_ids=without_metacontext,
    )
