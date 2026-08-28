"""Core tool implementations for the Epimemer MCP server.

Each function is a pure async function with explicit dependencies —
no global state, easily testable. The MCP server layer in server.py
calls these and wraps the results.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Iterable, Iterator, Literal, Mapping, Sequence

from pydantic import BaseModel, Field, ValidationError

from epimemer.core.types import (
    Agent,
    AgentDescription,
    BASE_METACONTEXT_ID,
    ClaimKind,
    DecisionKind,
    DecisionRecord,
    EdgeType,
    EmbeddingRecord,
    EpistemicNode,
    Fact,
    Inference,
    Metacontext,
    NodeChangeEvent,
    NodeEdge,
    NodeStatus,
    NOMINATED_STATUSES,
    RESTORABLE_STATUSES,
    reachable_statuses,
    superseded_status_for,
    NodeType,
    QUARANTINE_METACONTEXT_ID,
    RawDocument,
    RelationLabel,
    Segment,
    Timeline,
    Topic,
    ValueSignal,
    JudgeRef,
    absorbing,
    agent_aliases,
    agent_name,
    current_description,
    description_digest,
    live_agents,
    name_holder,
    new_agent_id,
    recorded_relation_label,
    renamed,
    resolve_agent,
    merged_value_signal,
    supersession_kind,
    with_description,
)
from epimemer.core.temporal import ValidityInterval, ValidityVerdict
from epimemer.core.advisories import (
    Advisory,
    AdvisoryAction,
    AdvisoryKind,
    WarningPolicy,
    notify_user,
    objects_to_the_call,
    resolved_action,
    surfaced,
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.mcp.config import (
    DEFAULT_IMPORTANCE_STEP,
    DEFAULT_RECORD_RETRIEVAL,
    ServerConfig,
)
from epimemer.mcp.retrieval_records import RetrievedNode
from epimemer.mcp.types import ResponseMeta
from epimemer.pipelines.graph_construction.edge_creation import DecomposedSegment
from epimemer.pipelines.query.types import SeedProvenance
from epimemer.pipelines.reflection.review import SIMILARITY_NOMINATION_THRESHOLD
from epimemer.pipelines.review.apply import (
    RejudgeRefused,
    ReviewRefused,
    rejudge_node,
    review_decision,
)
from epimemer.pipelines.review.difficulty import (
    ScoredDecision,
    difficulty_signals,
    review_order,
)
from epimemer.pipelines.review.modes import (
    MODE_KINDS,
    REVIEW_MODES,
    mode_refusal,
    passes_ceiling,
)
from epimemer.storage.protocol import (
    MergeOverrides,
    StorageBackend,
    WarningOverrides,
    resolve_merge_settings,
    resolve_reflect_threshold,
    resolve_require_judge,
    resolve_warning_policy,
    validate_graph_name,
)

from petritype.core.executable_graph_components import ExecutableGraph
from petritype.runtime import RunContext, Runner

from epimemer.visualization.event_bus import InProcessEventBus


async def _run_net(
    graph: ExecutableGraph,
    pipeline_name: str,
    event_bus: InProcessEventBus | None,
) -> tuple[ExecutableGraph, int]:
    """Execute a Petri net to quiescence, optionally emitting visualization events.

    Runs until nothing is enabled. There is deliberately no transition budget:
    a net that has stopped firing is finished, and a number chosen in advance is
    either too small — truncating the pipeline and returning a partial result
    with no error — or large enough never to matter.

    Both paths are the same runner; the event bus only adds an observer, so
    watching a pipeline cannot change what it computes.

    Nothing here may write to stdout: MCP's stdio transport is stdout, so a
    stray print corrupts the protocol. The engine's own progress prints are
    gated behind `verbose`, which the runner leaves off, so no suppression is
    needed — and suppressing it by swapping `sys.stdout` would be worse than the
    problem, since that is process-global state mutated across `await` points.
    """
    if event_bus is not None:
        from epimemer.visualization.instrumented_executor import execute_with_events
        return await execute_with_events(graph, event_bus, pipeline_name)

    steps_before = graph.step_count
    graph = await Runner.run_to_completion(RunContext(graph=graph))
    return graph, graph.step_count - steps_before


# --- Declaring what a response carries ---
#
# Every tool that puts a node id where the agent can read it says so on its
# `ResponseMeta`. The choke point in `server.py` writes the record; it does not
# guess the ids, because walking an arbitrary result dict for id-shaped keys
# would guess differently per tool and break silently when a shape changed
# (`RETRIEVAL_PROVENANCE.md` §2.1).
#
# The rule is semantic rather than a list of tools: **`retrieved` is the set of
# node ids present in the response** — what the agent saw. The enumeration in
# §2 was wrong twice for exactly the reason a list is the wrong shape.


def _declare(
    node_ids: Iterable[str],
    *,
    provenance: SeedProvenance | Mapping[str, SeedProvenance] = SeedProvenance.DIRECT,
    scores: Mapping[str, float] | None = None,
) -> list[RetrievedNode]:
    """The declaration for a response carrying `node_ids`.

    Deduplicated, first appearance winning, so a node reached twice is declared
    once and in the order the response lists it. `DIRECT` is the default because
    most tools return nodes without ranking them at all; a ranked tool passes
    its own map.
    """
    declared: dict[str, RetrievedNode] = {}
    for node_id in node_ids:
        if node_id in declared:
            continue
        declared[node_id] = RetrievedNode(
            node_id=node_id,
            provenance=(
                provenance.get(node_id, SeedProvenance.DIRECT)
                if isinstance(provenance, Mapping)
                else provenance
            ),
            score=None if scores is None else scores.get(node_id),
        )
    return list(declared.values())


_NESTED_ID_KEYS = ("id", "node_id", "topic_id")


def _ids_within(value: object) -> Iterator[str]:
    """Every node id nested anywhere in a result structure this tool just built.

    Used by `reflect` alone, whose nominee lists each have their own shape and
    have outnumbered the seven that existed when this was written — which is the
    argument making itself. Reading them off a hand-written list of key paths is
    how the next one would go undeclared, and §2.1's objection does not apply
    here: it is about the *choke point* guessing across tools it knows nothing
    about, where this is a tool reading the structure it wrote three lines
    earlier.

    `truncated` rides along harmlessly: it is a list of bare key names, and a
    string outside a mapping matches no id key.
    """
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _NESTED_ID_KEYS and isinstance(item, str):
                yield item
            else:
                yield from _ids_within(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _ids_within(item)


# --- Which graph am I writing to? ---


def wrong_graph(
    storage: StorageBackend, expected_graph: str | None
) -> tuple[dict, ResponseMeta] | None:
    """Refuse the call when the agent expected a different graph. None to proceed.

    **The check a machine makes, rather than the hint an agent may read.** The
    active graph is process state, so a client reconnect silently reopens
    whatever the server was configured with — and the call that follows is
    correct in every respect except which graph it ran against. Reporting
    `active_graph` in the response helps an agent that looks; this stops one
    that does not.

    **Every tool that touches graph content, read and write alike**. This
    read three tools at first, on the argument that everything else dereferences
    a node id and so already fails on the wrong graph. That argument was wrong
    twice.

    - **It ignored reads entirely, and reads are the worse half.** A misfiled
      write is *visible*: the material and its journal row sit together in the
      graph that received them, waiting to be found. A wrong-graph `search`
      returns a plausible answer the agent then reasons from and reports, and
      leaves no artifact anywhere. `search` also runs far more often than any
      writer.
    - **An id that fails to resolve is a worse failure than a refusal, not a
      substitute for one.** `merge_facts` raises *node not found*, which does
      not say *wrong graph*, so the agent's next move is a workaround rather
      than a `use_graph`. `apply_reflection` does not even raise — it skips.
      And where two graphs share ids, which is what a restored archive or a
      copied database produces, the ids resolve and the call
      lands.

    Exempt, and the list is short because each is *about* graphs rather than in
    one: `list_graphs` asks which exist, `use_graph` and `delete_graph` take the
    graph as their argument, and `viz_status` is server-level.

    **The gate lives at the MCP boundary and nowhere else** — one home for the
    policy, on `_judge_for_write`'s reasoning: a second check on its own account
    could differ from this one without anybody noticing. A caller that reaches
    `tools.*` directly passes its own storage handle and has no ambient active
    graph to be wrong about, which is the hazard this exists for.

    **Absent refuses, and there is no setting that changes it.** The two shapes
    a setting could take were both rejected, and for one reason: *a guard must
    not be configured by the state it is guarding against.* A per-graph flag
    would be read from whichever graph the call is actually in, so landing
    somewhere with it off would wave the call through. A gate that switched
    itself on once a second graph existed would read a live database list, so
    creating a graph would start refusing calls that worked yesterday and
    deleting it would stop — a requirement that oscillates with unrelated state
    is not a policy. Unlike `require_judge`, which is about *rigour* and
    legitimately varies by use case, this is a correctness check, and there is
    no use case for not minding which graph a call lands in.

    **The refusal for absence says what the active graph is, and warns against
    copying it back.** Naming the graph is worth something only because the
    agent's side and the server's are worked out independently; an agent that
    reads the answer out of the refusal and pastes it in has made the two agree
    by construction. Stated in the message because it cannot be enforced.
    """
    active = storage.current_database
    if expected_graph == active:
        return None
    if expected_graph is None:
        return (
            {
                "refused": (
                    f"This call did not say which graph it means. Pass "
                    f"expected_graph — the server is on '{active}', so "
                    f"expected_graph='{active}' if that is where you meant to "
                    f"be. The active graph is not remembered across a client "
                    f"reconnect, so a session that switched earlier can come "
                    f"back somewhere else, and nothing tells you. Do not copy "
                    f"this name back without checking it is the graph you "
                    f"intend: the value of naming it is that your side and the "
                    f"server's are worked out independently."
                ),
                "expected_graph": None,
                "active_graph": active,
            },
            ResponseMeta(),
        )
    return (
        {
            "refused": (
                f"This call expected graph '{expected_graph}' and the server is "
                f"on '{active}'. Nothing was written. The active graph is not "
                f"remembered across a client reconnect, so a session that "
                f"switched earlier can come back somewhere else — call "
                f"use_graph('{expected_graph}') and retry."
            ),
            "expected_graph": expected_graph,
            "active_graph": active,
        },
        ResponseMeta(),
    )


# --- The decision journal (REVIEW_MODE.md §4) ---

_journal_logger = logging.getLogger("epimemer.mcp.tools")


async def journal(
    storage: StorageBackend,
    kind: DecisionKind,
    subject_ids: Sequence[str],
    *,
    judge: JudgeRef | None,
    reviews: str | None = None,
    supersedes: str | None = None,
    certainty: float | None = None,
    certainty_basis: str | None = None,
    frame: str | None = None,
) -> DecisionRecord | None:
    """Append one judgment to the journal. Returns the row, or None if it failed.

    Called **after** the decision has landed, at every site that makes one. The
    row is not written in the same transaction as the decision, and that is the
    safe direction of the two: a lost row costs the journal an entry, while a
    row for a write that never happened would have review chasing a decision the
    graph never made.

    **And it never raises**, which is the other half of that choice. Raising here
    would fail the tool call *after* the graph write succeeded, and the agent
    would retry: a retried `merge_facts` refuses because its sources are already
    retired, a retried `record_contradiction` writes a row that reads as an
    original decision, and a retried `store_decomposition` ingests the document
    twice. Every one of those is worse than the missing row.

    So a failure is logged rather than returned. The log names the kind and the
    subjects, which is what the row would have held, and it is the operator who
    can act on it — no tool re-journals a decision, so telling the *agent* would
    hand it information it has no move for.

    A blank `judge` is written rather than skipped. The graph may not require
    one (§3.3.1), and the row still carries when the decision was made and
    whether anyone has since checked it — both worth having from an agent that
    did not name itself.
    """
    record = DecisionRecord(
        kind=kind,
        subject_ids=[sid for sid in subject_ids],
        judged_by=judge,
        reviews=reviews,
        supersedes=supersedes,
        # Blank at almost every writer, and that is the design: the review
        # writers are where a declared judgment is the point of the call, so
        # §5's ladder is stated there instead of on twelve tool schemas.
        certainty=certainty,
        certainty_basis=certainty_basis,
        frame=frame,
    )
    try:
        await storage.record_decision(record)
    except Exception:
        _journal_logger.warning(
            "decision journal write failed; the decision stands and is "
            "unrecorded. kind=%s subjects=%s judge=%s",
            kind.value,
            ",".join(record.subject_ids),
            judge.agent_id if judge else "unknown",
            exc_info=True,
        )
        return None
    return record


# --- Advisories (what an operation was told before it made it) ---


async def advisory_policy(
    storage: StorageBackend, default: WarningPolicy
) -> WarningPolicy:
    """The policy in force on the active graph: its overrides over the process default.

    Read per call rather than cached, for the reason nothing here is a
    singleton: the policy is per graph, `use_graph` switches the graph, and a
    cache would answer *what is the policy here* about somewhere else.
    """
    return resolve_warning_policy(await storage.get_warning_overrides(), default)


async def carry_advisories(
    storage: StorageBackend,
    policy: WarningPolicy,
    advisories: list[Advisory],
    subject_ids: Sequence[str],
    *,
    judge: JudgeRef | None,
) -> dict:
    """Record that an operation completed carrying these, and shape the response.

    **Only an advisory that *objects* writes a row.** *Despite* is meaningful
    only where there was something to proceed against, so an advisory that
    merely escalates a correct call — a same-frame contradiction is the one that
    does — keeps its `notify_user` and journals nothing. The first version wrote
    a row for every advisory, which doubled the journal on the commonest path
    and degraded exactly the review the kind exists for. The classification is
    per kind in `ADVISORY_STANCE`, so there is no special case here.

    **Recording is unconditional; surfacing is the setting.** A graph whose
    warnings were switched off for a month should still answer *what was decided
    while nobody was looking*, which is exactly when the question matters most —
    so `surface` gates the response and never the journal row.

    One row per operation rather than per advisory: the agent made one decision.
    The kinds and their messages go in `certainty_basis`, which is the row's own
    prose and is what `review(mode="advisory")` renders — so the reviewer sees
    what the decider was told without a second store to keep in step.

    `certainty` stays blank, and deliberately: nobody rated this. A row invented
    at 0.5 would sort above the genuinely unrated ones and read as a judgment
    the agent never made.

    **`notify_user` is always present and always a boolean**, including when
    there is no advisory at all. Omitting it where nothing escalates leaks which
    branch ran — and it leaked in three shapes before this was made total, since
    *no advisory*, *advisory muted* and *advisory shown but quiet* are the same
    answer to the only question the key asks. A key documented in
    `INTEGRATION.md` that is sometimes absent is worse to read than one that is
    sometimes false.
    """
    if not advisories:
        return {"notify_user": False}
    if objects_to_the_call(advisories):
        await journal(
            storage,
            DecisionKind.PROCEEDED_DESPITE_ADVISORY,
            list(subject_ids),
            judge=judge,
            certainty_basis=" ".join(
                f"[{advisory.kind.value}] {advisory.message}"
                for advisory in advisories
            ),
        )
    shown = surfaced(policy, advisories)
    if not shown:
        return {"notify_user": False}
    return {
        # The user's vocabulary on the wire; only the Python class is `Advisory`.
        "warnings": [advisory.model_dump(mode="json") for advisory in shown],
        # The first advisory's message, kept because it is the key the agent
        # guidance and INTEGRATION.md already document. Breaking it to tidy an
        # internal representation would cost more than it buys.
        "warning": shown[0].message,
        "notify_user": notify_user(policy, shown),
    }


async def prior_decisions(
    storage: StorageBackend,
    kind: DecisionKind,
    subject_ids: Sequence[str],
) -> list[DecisionRecord]:
    """Records of `kind` covering **every** id in `subject_ids`, newest first.

    What lets a second judgment cite the first rather than overwrite it: an
    agent re-recording a pair that already carries the verdict has *confirmed*,
    not decided, and a confirmation is a row pointing back (§3.4).

    Filtered on one subject in the query and the rest in memory, because the
    index answers *contains this id* and not *contains all of these* — and the
    first id already cuts the journal to a handful.
    """
    if not subject_ids:
        return []
    candidates = await storage.query_decisions(
        kinds=[kind], subject_id=subject_ids[0]
    )
    wanted = set(subject_ids)
    return [r for r in candidates if wanted <= set(r.subject_ids)]


# --- Segment (step 1 of agent-driven ingest) ---


async def segment_text(
    content: str,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    config: ServerConfig,
    *,
    source: str | None = None,
    source_type: str | None = None,
    published_by: str | None = None,
    published_at: dict | None = None,
    metadata: dict | None = None,
    segmentation_strategy: str | None = None,
    event_bus: InProcessEventBus | None = None,
    judge: JudgeRef | None = None,
) -> tuple[dict, ResponseMeta]:
    """Segment text and store the document and segments. Returns segments for the agent to decompose.

    This is step 1 of the two-step agent-driven ingest flow. The agent
    receives the segments, extracts topics/facts/inferences itself, then
    calls store_decomposition (step 2).

    source/source_type describe the originating document; every node decomposed
    from it gets a `sourced_from` edge to this document. `published_by` names a
    publishing/authoring entity — resolved-or-created as an entity Topic and linked
    to the document by a `published_by` (attribution) edge. `published_at` is when
    the document was published, which bounds what it could have known; it is left
    absent rather than falling back to the ingest time.
    """
    from epimemer.pipelines.segmentation.paragraph_split import paragraph_split_segmentation_net
    from epimemer.pipelines.segmentation.semantic_similarity import semantic_similarity_segmentation_net

    strategy = segmentation_strategy or config.segmentation_strategy

    doc = RawDocument(
        content=content, source=source, source_type=source_type,
        published_at=published_at, metadata=metadata or {},
    )
    await storage.store_document(doc)

    if published_by:
        # The document and its segments carry no judge: they are the material,
        # not a claim about it, and *who pasted this text* is a different
        # question from *who judged what it says*. The entity topic and the
        # attribution edge are claims — this publisher exists, and this document
        # is theirs — so both name the agent that made them.
        #
        # No journal row either, on the same division: the judgment pass over
        # this document is `store_decomposition`, and that is where the `ingest`
        # row goes (§4.1). Splitting text into paragraphs is not a verdict
        # anybody would review.
        entity = await _upsert_entity_topic(
            published_by, storage, embedding_provider, judge=judge
        )
        await storage.store_edge(NodeEdge(
            src_id=doc.id, dst_id=entity.id, type=EdgeType.RELATED,
            label="published_by", kind="attribution", judged_by=judge,
        ))

    if strategy == "semantic":
        seg_graph = semantic_similarity_segmentation_net(doc, embedding_provider)
        seg_graph, _ = await _run_net(seg_graph, "segmentation:semantic", event_bus)
    else:
        seg_graph = paragraph_split_segmentation_net(doc)
        seg_graph, _ = await _run_net(seg_graph, "segmentation:paragraph", event_bus)

    segments: list[Segment] = list(seg_graph.place_named("Segments").tokens)

    for segment in segments:
        await storage.store_segment(segment)

    # Only return IDs and boundaries — the agent already has the original text.
    result = {
        "document_id": doc.id,
        # Named on the way in, and this is the earliest place it can be. The
        # active graph is process state, so a client reconnect silently reopens
        # whatever the server was configured with — and an ingest into the wrong
        # graph reports success in every other respect. Said here, the agent can
        # notice before it decomposes anything; said only by `list_graphs`, it is
        # noticed after the nodes are written.
        "active_graph": storage.current_database,
        "segments": [
            {"segment_id": s.id, "char_count": len(s.text)}
            for s in segments
        ],
    }
    meta = ResponseMeta(nodes_returned=len(segments))
    return result, meta


# --- Store Decomposition (step 2 of agent-driven ingest) ---


async def _upsert_entity_topic(
    name: str,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    extraction_method: str = "agent:source",
    judge: JudgeRef | None = None,
) -> Topic:
    """Resolve-or-create (by exact name) an entity Topic and persist it directly.

    Used for source/publisher entities at segment time. Exact-name match means a
    repeated name reuses one node; fuzzy duplicates are merged later by reflect.
    """
    existing = await storage.get_node_by_content(name, node_type=NodeType.TOPIC)
    if isinstance(existing, Topic):
        return existing
    # Only a *new* entity names this agent. Reusing an existing one is not a
    # second creation, and restamping it would credit whoever mentioned the name
    # last with a node somebody else introduced.
    topic = Topic(
        content=name, source_id=None, extraction_method=extraction_method,
        judged_by=judge,
    )
    await storage.store_node(topic)
    vec = (await embedding_provider.embed([name]))[0]
    await storage.store_embedding(EmbeddingRecord(
        item_id=topic.id, model_id=embedding_provider.model_id, vector=vec,
    ))
    return topic


class DecompositionEntry(BaseModel):
    """One extracted node as the agent supplied it.

    Both value fields are *priors*, not verdicts, and they differ in what
    omitting one says. `importance` has a real default: triviality is only
    visible once the neighbourhood exists, so the judgment happens at reflect
    time and a node that arrives unrated is simply waiting for it.
    `confidence` cannot be judged later by anything — the material is in front
    of the agent now and nothing downstream will read it again — so an omitted
    value means the question was never put, and stays absent rather than
    landing on the default number.

    No bounds here: `ValueSignal` already holds them, and restating a range in
    two places is how the two come to disagree.
    """
    content: str
    tags: list[str] = Field(default_factory=list)
    importance: float | None = None
    confidence: float | None = None
    # One line, optional, and asked for by guidance rather than enforced. A
    # non-default prior with no reason recorded is the unattributable judgment
    # `judge_importance` refuses outright; here the same argument buys a
    # request, not a refusal, because failing an ingest over it costs more
    # than the missing line.
    confidence_basis: str | None = None
    # Condition or occurrence — the judgment dedup is gated on. Facts
    # only; supplying it on a topic or an inference is refused rather than
    # dropped, since a judgment silently discarded is one the agent believes it
    # made. Omitted is *unjudged*, and unjudged never merges.
    claim_kind: ClaimKind | None = None
    # When this document says the claim was true. Ingest is the only
    # place that can supply it: tense and the dates written in the text are
    # visible here and nowhere later, and reflect has facts and a graph rather
    # than a document. It lands on the node's `sourced_from` edge, so it is
    # always attributable to the document it came from.
    validity: list[ValidityInterval] = Field(default_factory=list)


def _decomposition_entry(entry) -> DecompositionEntry:
    """Unpack a decomposition entry: a bare content string, or a dict of the
    fields above. A bare string is the common case and carries no priors."""
    if isinstance(entry, dict):
        return DecompositionEntry.model_validate(entry)
    return DecompositionEntry(content=entry)


def _entry_value_signal(entry: DecompositionEntry) -> ValueSignal:
    """The priors the agent supplied, and nothing it did not.

    Naming a field at all is what distinguishes "rated 0.5" from "unrated", so
    an omitted one is left out of the call rather than passed as `None` — the
    model's own default is then the single place each field's absence is
    defined.
    """
    supplied = {
        name: value
        for name, value in (
            ("importance", entry.importance),
            ("confidence", entry.confidence),
        )
        if value is not None
    }
    return ValueSignal(**supplied)


def _claim_kind_field(entry: DecompositionEntry, cls: type) -> dict:
    """`claim_kind` as a constructor keyword, refused where it would mean nothing.

    Absence is passed as absence rather than as `None`, on `_entry_value_signal`'s
    grounds: the model's own default is the single place unjudged is defined.

    Supplying it on a topic or an inference **raises**, where a missing
    `confidence_basis` only prompts. The two are different failures. A basis
    nobody wrote is a gap the agent can see in its own output; a judgment written
    into a field that does not exist is one the agent believes it made, and it
    would be discovered — if ever — as a merge that quietly never happens.

    The message says *no field to gate* rather than *inferences never merge*,
    which is why it survived `merge_inferences` shipping: that merge reads its
    premises' validity periods rather than a `claim_kind`, so a refusal phrased
    as a policy about merging would have been overturned by a feature that does
    not change this rule at all.
    """
    if entry.claim_kind is None:
        return {}
    if cls is not Fact:
        raise ValueError(
            f"claim_kind was supplied on a {cls.__name__.lower()} "
            f"({entry.content[:60]!r}). Only facts carry it: a topic is a theme "
            f"rather than a claim, so neither answer is about it, and an "
            f"inference is judged on the periods its premises assert rather "
            f"than on a kind of its own."
        )
    return {"claim_kind": entry.claim_kind}


EXTRACTED_TIMELINE_NAME = "Extracted"


async def _extraction_timeline(
    storage: StorageBackend, timeline_id: str | None
) -> Timeline:
    """The timeline extraction should propose onto.

    One shared timeline per graph rather than one per document. The panel shows
    a single timeline at a time (`dev-docs/TIMELINE_VISUALISATION.md` §12.2), so
    a timeline per document turns every ingest into another near-empty entry in
    the selector and buries the marks. Provenance is not lost by sharing:
    every node carries a `sourced_from` edge to its document.

    A named timeline must already exist. Creating one silently would put the
    document on a timeline the caller cannot find, under a name they never
    chose — `create_timeline` is how a name comes into being.
    """
    if timeline_id is not None:
        timeline = await storage.get_timeline(timeline_id)
        if timeline is None:
            raise ValueError(f"Timeline '{timeline_id}' not found")
        return timeline

    for timeline in await storage.query_timelines():
        if timeline.name == EXTRACTED_TIMELINE_NAME:
            return timeline
    return Timeline(
        name=EXTRACTED_TIMELINE_NAME,
        description="Timepoints proposed from ingested text.",
    )


# How many frames a refusal names before it stops being readable.
_FRAME_LISTING_LIMIT = 10


async def require_metacontext(
    metacontext_id: str, storage: StorageBackend, *, writing: bool = False
) -> None:
    """Raise unless `metacontext_id` names a metacontext in the active graph.

    Metacontext ids are **per graph**, so an id carried over from another graph
    is a string that resolves nowhere here — and an unchecked one leaves the
    node worse off than no frame at all. `frames_for` hands back whatever the
    `has_metacontext` edge points at, so a node in a frame that does not exist
    shares a frame with **nothing**: never nominated as contradicting anything,
    never merged with anything, and absent from every frame-scoped search —
    including a search for the frame the agent meant. It sits in the graph,
    unreachable by every mechanism that would have questioned it.

    **`the-real` is not special.** It is a convention — the string every graph
    should use for the frame holding real-world claims — and it must exist here
    like any other id, created once with `create_metacontext`. It used to be
    accepted with no row, back when an untagged node resolved to it and it
    therefore named something in every graph. Nothing resolves to it now, so
    accepting it rowless would admit an id pointing at nothing: the isolation
    failure this function exists to prevent, waved through by name.

    **The refusal lists what does exist**, because nothing else does: no MCP
    tool enumerates metacontexts, so for an agent holding a stale id this
    message is the only place the right one appears.

    **An empty id is refused here too**, so that the one home for frame
    validation is also the one place the requirement is explained. `search`
    never reaches it — an omitted filter there is a coherent question, not an
    unstated assumption, and searching every frame is the answer to it.

    **`writing=True` additionally refuses the quarantine frame**, which is the
    one rule that differs between reading and writing. Searching *for* what
    nobody has vouched for is a reasonable question; asserting into it is not,
    because a frame an agent can write is a frame that stops meaning *nobody has
    vouched for this*. Only `epimemer frames declare` puts it on anything.
    """
    if not metacontext_id.strip():
        raise ValueError(
            "metacontext_id is required: name the frame this document's claims "
            "are made in. Use 'the-real' for real-world claims — the "
            "conventional id, and the ordinary answer — or another metacontext "
            "from create_metacontext for fiction, a named source, or a "
            "perspective. It is required because a claim has to say which world "
            "it is about: a node with no frame is one nobody spoke for, and "
            "nothing compares it, merges it, or returns it from a scoped "
            "search."
        )
    if writing and metacontext_id == QUARANTINE_METACONTEXT_ID:
        raise ValueError(
            f"'{QUARANTINE_METACONTEXT_ID}' is not a frame anything may be "
            f"written into. It marks nodes nobody has vouched for, stamped by "
            f"`epimemer frames declare` on a graph whose provenance is unknown "
            f"— an agent asserting into it would make it mean nothing. Name "
            f"the frame these claims actually belong to."
        )
    if await storage.get_metacontext(metacontext_id) is not None:
        return

    known = list(await storage.query_metacontexts())
    if known:
        shown = ", ".join(
            f"'{mc.id}' ({mc.content})" for mc in known[:_FRAME_LISTING_LIMIT]
        )
        if len(known) > _FRAME_LISTING_LIMIT:
            shown += f", and {len(known) - _FRAME_LISTING_LIMIT} more"
        have = f"This graph has: {shown}."
    else:
        have = "This graph has no metacontexts yet."
    raise ValueError(
        f"Metacontext '{metacontext_id}' does not exist in graph "
        f"'{storage.current_database}'. Metacontext ids are per graph, so an id "
        f"from another graph names nothing here. {have} Create one with "
        f"create_metacontext — including '{BASE_METACONTEXT_ID}', the "
        f"conventional id for real-world claims, which is an ordinary frame "
        f"and has to exist here like any other. On `search`, leaving the list "
        f"out searches every frame."
    )


async def store_decomposition(
    document_id: str,
    segments: list[dict],
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    metacontext_id: str,
    tags: list[str] | None = None,
    timeline_id: str | None = None,
    propose_timepoints: bool = True,
    event_bus: InProcessEventBus | None = None,
    judge: JudgeRef | None = None,
) -> tuple[dict, ResponseMeta]:
    """Store agent-provided decomposition: topics, facts, inferences per segment.

    Each entry in segments should have:
        segment_id: str
        topics/facts/inferences: each a content string, or a dict of the
            `DecompositionEntry` fields — per-node tags plus the two value
            priors. `importance` defaults to 0.5; `confidence` is left *unrated*
            when omitted rather than defaulting, and an optional
            `confidence_basis` records why a supplied one was chosen. A fact may
            also carry `claim_kind` — condition or occurrence — which is the
            judgment fact dedup is gated on and which only this step can make
            . The ladder an agent calibrates against lives in `server.py`'s
            tool docstring, which is what an agent actually reads before
            ingesting.

    `metacontext_id` is **required** — the frame every claim in this document
    is asserted in, and it must already exist here. `the-real` is the
    conventional id for real-world claims and the ordinary answer; another
    metacontext names fiction, a source, or a perspective. It is required
    because a claim has to say which world it is about: a node with no frame is
    one nobody spoke for, so nothing compares it, merges it, or returns it from
    a scoped search. On 684 real nodes no agent had ever said which world it
    meant. The requirement does not prevent a wrong frame — a reflexive
    `the-real` on a fiction ingest is exactly as wrong as silence was. What it
    buys is that the error is **findable** (the frame is on the ingest journal
    row) and **fixable** (`reframe`), where silence left nothing to find.

    **One frame per call, so a mixed document is two calls.** The id applies to
    every node in the decomposition, so a discussion of a novel that also states
    a fact about its real author is split: the in-world claims in the novel's
    frame, the author's biography in `the-real`. A per-node override belongs in
    the `DecompositionEntry` object beside `importance` and `confidence`, for
    the same reason those are per node — not built, and deliberately not
    foreclosed.

    Every node gets a `sourced_from` edge to the originating document, and a
    `has_metacontext` edge to the frame. `tags` (document-level) and per-node
    tags are resolved-or-created (by exact name) as Topics linked by
    `tagged_with` edges, so a repeated tag reuses one Topic. Everything is
    persisted in one atomic write.

    Temporal expressions in node content become timepoints on a timeline
    (`timeline_id`, or the shared extracted one), linked by `TIMELINK`. Only
    what the text states is resolved: "during the Renaissance" stays undated
    rather than being guessed into a date. Pass `propose_timepoints=False` to
    skip it entirely.
    """
    from epimemer.pipelines.graph_construction.edge_creation import DecomposedSegment, edge_creation_net
    # Imported as a module: the `propose_timepoints` flag above would otherwise
    # shadow the function of the same name.
    from epimemer.pipelines.timeline import functions as timeline_functions

    # A stated frame must resolve *here*, for the same reason a named timeline
    # must already exist: an edge pointing at nothing isolates the node it was
    # meant to frame. Checked before any of the document is built, so a bad id
    # costs nothing and leaves nothing behind.
    await require_metacontext(metacontext_id, storage, writing=True)

    # Accumulate the whole document's writes, then persist them atomically so a
    # mid-document failure cannot leave a partial graph.
    batch_nodes: list[EpistemicNode] = []
    batch_edges: list[NodeEdge] = []
    batch_embeddings: list[EmbeddingRecord] = []

    stored_segments = await storage.get_segments_for_document(document_id)
    segments_by_id = {s.id: s for s in stored_segments}

    total_topics = total_facts = total_inferences = 0
    doc_tag_names = list(tags or [])
    tag_cache: dict[str, Topic] = {}
    # Tag Topics are excluded: a tag is a name, not a statement, and a tag that
    # happens to read as a date would put a mark on the timeline for every node
    # carrying it.
    datable: list[tuple[str, str]] = []

    async def _tag_topic(name: str) -> Topic:
        """Resolve-or-create a tag Topic, adding new ones to the batch."""
        if name in tag_cache:
            return tag_cache[name]
        existing = await storage.get_node_by_content(name, node_type=NodeType.TOPIC)
        if isinstance(existing, Topic):
            tag_cache[name] = existing
            return existing
        topic = Topic(
            content=name, source_id=None, extraction_method="agent:tag",
            judged_by=judge,
        )
        tag_cache[name] = topic
        batch_nodes.append(topic)
        vec = (await embedding_provider.embed([name]))[0]
        batch_embeddings.append(EmbeddingRecord(
            item_id=topic.id, model_id=embedding_provider.model_id, vector=vec,
        ))
        return topic

    for seg_data in segments:
        segment_id = seg_data["segment_id"]
        segment = segments_by_id.get(segment_id)
        if segment is None:
            raise ValueError(f"Segment '{segment_id}' not found for document '{document_id}'")

        topics: list[Topic] = []
        facts: list[Fact] = []
        inferences: list[Inference] = []
        tag_assignments: list[tuple[EpistemicNode, list[str]]] = []
        validity_by_node: dict[str, list[ValidityInterval]] = {}
        for cls, entries, bucket in (
            (Topic, seg_data.get("topics", []), topics),
            (Fact, seg_data.get("facts", []), facts),
            (Inference, seg_data.get("inferences", []), inferences),
        ):
            for entry in entries:
                parsed = _decomposition_entry(entry)
                node = cls(
                    content=parsed.content, source_id=segment_id,
                    value=_entry_value_signal(parsed), extraction_method="agent",
                    # This is the read of the material, and the priors on it —
                    # `claim_kind`, `confidence`, `importance` — are judgments
                    # nothing downstream will re-make, which is why §3.1 calls
                    # ingest the place the unreviewable judgments live.
                    judged_by=judge,
                    # Beside the `reinforcements` trail rather than on the
                    # signal: the basis is prose about one judgment, and
                    # `ValueSignal` is the numbers every ranker reads.
                    metadata=(
                        {"confidence_basis": parsed.confidence_basis}
                        if parsed.confidence_basis else {}
                    ),
                    **_claim_kind_field(parsed, cls),
                )
                bucket.append(node)
                if parsed.validity:
                    validity_by_node[node.id] = parsed.validity
                names = doc_tag_names + parsed.tags
                if names:
                    tag_assignments.append((node, names))

        decomposed = DecomposedSegment(
            segment=segment, topics=topics, facts=facts, inferences=inferences,
        )
        edge_graph = edge_creation_net(decomposed)
        edge_graph, _ = await _run_net(edge_graph, "edge_creation", event_bus)
        edges: list[NodeEdge] = list(edge_graph.place_named("Edges").tokens)

        seg_nodes: list[EpistemicNode] = [*topics, *facts, *inferences]
        batch_nodes.extend(seg_nodes)
        batch_edges.extend(edges)
        datable.extend((node.id, node.content) for node in seg_nodes)

        if seg_nodes:
            vectors = await embedding_provider.embed([n.content for n in seg_nodes])
            for node, vector in zip(seg_nodes, vectors):
                batch_embeddings.append(EmbeddingRecord(
                    item_id=node.id, model_id=embedding_provider.model_id, vector=vector,
                ))

        # Provenance: every node is sourced_from the originating document, and
        # the periods this document asserts the claim held ride on that edge —
        # the only place they are attributable to the source that made them.
        for node in seg_nodes:
            batch_edges.append(NodeEdge(
                src_id=node.id, dst_id=document_id, type=EdgeType.SOURCED_FROM,
                validity=validity_by_node.get(node.id, []),
            ))
        # Tags: each becomes (or reuses) a Topic linked by tagged_with.
        for node, names in tag_assignments:
            for name in names:
                topic = await _tag_topic(name)
                batch_edges.append(NodeEdge(
                    src_id=node.id, dst_id=topic.id, type=EdgeType.TAGGED_WITH,
                ))
        # The frame, written explicitly — including for `the-real`, which is
        # what makes requiring it worth anything. A node carrying no edge is
        # read as base reality anyway, so an unwritten `the-real` would be
        # indistinguishable from an agent that never considered the question,
        # which is the whole defect. `frames_of` reduces both to the same
        # single-frame set, so no consumer sees a difference; a reviewer does.
        for node in seg_nodes:
            batch_edges.append(NodeEdge(
                src_id=node.id, dst_id=metacontext_id,
                type=EdgeType.HAS_METACONTEXT, judged_by=judge,
            ))

        total_topics += len(topics)
        total_facts += len(facts)
        total_inferences += len(inferences)

    # Timepoints ride in the same write: a TIMELINK naming a timeline that was
    # never stored resolves to an empty row rather than an error, so a partial
    # write would fail silently.
    batch_timelines: list[Timeline] = []
    timepoints_proposed = 0
    if propose_timepoints:
        timeline = await _extraction_timeline(storage, timeline_id)
        timeline, timelinks, timepoints_proposed = timeline_functions.propose_timepoints(
            datable, timeline
        )
        batch_edges.extend(timelinks)
        # An unchanged timeline needs no write; a timeline nobody added a point
        # to was never stored in the first place.
        if timepoints_proposed:
            batch_timelines.append(timeline)

    # Every edge in this batch was created by this call, so the judge is stamped
    # once here rather than at each of the five places they are built — three of
    # which are inside a Petritype net that would have to grow an argument to
    # carry it. A single stamp also cannot miss one.
    if judge is not None:
        batch_edges = [
            edge.model_copy(update={"judged_by": judge}) for edge in batch_edges
        ]

    # One atomic write for the entire document.
    await storage.write_batch_tx(
        nodes=batch_nodes,
        edges=batch_edges,
        embeddings=batch_embeddings,
        timelines=batch_timelines,
    )

    # One journal row for the call, never one per fact (§4.1). Forty-four facts
    # out of one document is one reading of one document, and a row each would
    # make ingest the journal's dominant writer by orders of magnitude while
    # still describing a single act. The per-node judgments — `claim_kind`, the
    # two priors — ride inside it, and a reviewer opens them from `subject_ids`.
    # A call that stored nothing journals nothing: there was no judgment to
    # record, and a row with no subjects is a decision about nothing.
    if batch_nodes:
        await journal(
            storage,
            DecisionKind.INGEST,
            [node.id for node in batch_nodes],
            judge=judge,
            # One frame per call, so one value on the row. This is what makes
            # *which claims did this agent file into the real world* a query
            # rather than a walk out to every node's edges.
            frame=metacontext_id,
        )

    nodes_created = {
        "topics": total_topics,
        "facts": total_facts,
        "inferences": total_inferences,
    }
    result = {
        "document_id": document_id,
        # Repeated from `segment` rather than assumed unchanged: the two calls
        # are separate requests and a reconnect can land between them.
        "active_graph": storage.current_database,
        "nodes_created": nodes_created,
        "edges_created": len(batch_edges),
        "timepoints_proposed": timepoints_proposed,
        "historical_twins": await _historical_twins(batch_nodes, storage),
    }
    meta = ResponseMeta(
        nodes_returned=total_topics + total_facts + total_inferences,
        source_types={k: v for k, v in nodes_created.items() if v > 0},
    )
    return result, meta


async def _historical_twins(nodes: Sequence[EpistemicNode], storage) -> list[dict]:
    """Facts just stored that are word-for-word a claim the graph retired.

    The cheap floor under recurrence detection. `check_conflicts` is
    the load-bearing detector — it nominates by similarity, so it sees the
    recurrence two documents phrase differently, which is nearly all of them —
    but it is opt-in, and an agent that never calls it gets no recurrence
    detection at all. An exact-content match is the one case cheap enough to
    check unasked.

    **It reports and never acts.** Reactivation stays explicit: flipping a node
    live behind the caller's back on a string match is too brittle to do
    silently, and the agent has the new document in front of it and can tell a
    recurrence from a coincidence.

    Affordable only because the content lookup was indexed in the same visit: this is one indexed
    lookup per fact, 0.53 ms at 3,000 nodes against a real server, where the
    unhinted query it replaced was a table scan at 4.0 ms and climbing.
    """
    twins: list[dict] = []
    for node in nodes:
        if not isinstance(node, Fact):
            continue
        twin = await storage.get_node_by_content(
            node.content, node_type=NodeType.FACT, status=NodeStatus.HISTORICAL,
        )
        if twin is not None:
            twins.append({
                "fact_id": node.id,
                "content": node.content,
                "historical_id": twin.id,
            })
    return twins



# --- Search ---


# Frame-scoped search over-fetches. Vector top-k is computed before the frame
# filter runs, so a frame whose nodes rank below k would be dropped before the
# filter ever saw them — the query comes back short, or empty. We pull a multiple
# of k candidates and grow the fetch until k in-frame nodes survive or the vector
# store is exhausted. A storage-level frame filter is the eventual answer; this
# bounds the work until then. (Issue 13, REVIEW_EPISTEMIC.md §4.3.)
_FRAME_SCOPE_OVERFETCH = 4
_FRAME_SCOPE_MAX_K = 200


async def _run_retrieval(
    request,
    embedding_provider: EmbeddingProvider,
    storage: StorageBackend,
    event_bus: InProcessEventBus | None,
):
    """Run the hybrid-retrieval net once and return its QueryResult."""
    from epimemer.pipelines.query.hybrid_retrieval import hybrid_retrieval_net
    from epimemer.pipelines.query.types import QueryResult

    graph = hybrid_retrieval_net(request, embedding_provider, storage)
    graph, _ = await _run_net(graph, "retrieval", event_bus)
    result: QueryResult = graph.place_named("QueryResult").tokens[0]
    return result


async def _in_frame_nodes(
    nodes: list[EpistemicNode], metacontexts: Sequence[str], storage: StorageBackend
) -> list[EpistemicNode]:
    """Nodes standing in any of `metacontexts` — a set union, nothing more.

    **No frame inherits another.** This used to return the named frame *plus*
    untagged base reality, on the reasoning that real-world knowledge is the
    shared background every frame is read against. That inheritance was
    hardcoded and invisible: a caller could not see it, turn it off, or ask for
    any other combination. It is now the caller's sentence — a query wanting a
    novel's world read against real history asks for both by name, and one
    wanting only what the novel says asks for one.

    A node stating none of the listed frames does not match, and a node stating
    no frame at all matches nothing scoped — it is only reachable by leaving the
    list out, which is what makes it findable at all while a graph waits to be
    declared.

    One query for the whole set. This was previously an `asyncio.gather` over a
    round-trip per node, which bought concurrency at the cost of issuing
    overlapping reads on the shared SurrealDB connection — the hazard ISSUES.md
    the active-graph guard describes. Batching is faster *and* sequential, so the trade goes away
    rather than being taken.
    """
    from epimemer.pipelines.reflection.review import frames_for

    frames_by_node = await frames_for([node.id for node in nodes], storage)
    wanted = set(metacontexts)
    return [node for node in nodes if frames_by_node[node.id] & wanted]


async def _retrieve_frame_scoped(
    request,
    embedding_provider: EmbeddingProvider,
    storage: StorageBackend,
    metacontexts: Sequence[str],
    event_bus: InProcessEventBus | None,
) -> tuple[list[EpistemicNode], object]:
    """Retrieve in-frame nodes without being capped by the vector top-k.

    Over-fetch candidates and grow the fetch until at least `request.k` in-frame
    nodes survive the filter, or the store returns fewer hits than asked for
    (exhausted), or the cap is hit. Returns the filtered nodes plus the final
    QueryResult, whose edges and metadata describe the run that produced them.
    """
    k = request.k
    fetch_k = min(k * _FRAME_SCOPE_OVERFETCH, _FRAME_SCOPE_MAX_K)
    while True:
        widened = request.model_copy(update={"k": fetch_k})
        result = await _run_retrieval(widened, embedding_provider, storage, event_bus)
        in_frame = await _in_frame_nodes(result.nodes, metacontexts, storage)

        exhausted = result.metadata.nodes_searched < fetch_k
        if len(in_frame) >= k or exhausted or fetch_k >= _FRAME_SCOPE_MAX_K:
            return in_frame, result
        fetch_k = min(fetch_k * 2, _FRAME_SCOPE_MAX_K)


_HIERARCHY_PREVIEW_CHARS = 100


def _content_preview(node: EpistemicNode) -> dict:
    """Reduce a node to id plus truncated content.

    Hierarchy responses carry previews and never full material: the point of
    drill-down is that the caller decides what is worth loading, which a
    response that already inlined everything would defeat.
    """
    content = node.content
    if len(content) > _HIERARCHY_PREVIEW_CHARS:
        content = content[:_HIERARCHY_PREVIEW_CHARS] + "…"
    return {"id": node.id, "content_preview": content}


async def _hierarchy_annotations(
    nodes: Sequence[EpistemicNode], storage: StorageBackend
) -> dict[str, dict]:
    """Map topic id -> {parents?, subtopics?} for the Topics among `nodes`.

    Splitting a broad topic builds a SUBTOPIC_OF DAG; without this, retrieval
    never mentions it and a split buys the caller nothing. Only Topics
    participate, and a topic outside any hierarchy gets no keys at all rather
    than empty ones.

    Both edge lookups are one query for the whole topic set, and neighbour
    bodies are fetched once each across it, reusing nodes the result already
    carries — so a parent and its children coming back together costs no extra
    fetches.
    """
    topics = [n for n in nodes if isinstance(n, Topic)]
    if not topics:
        return {}

    topic_ids = [topic.id for topic in topics]
    parent_edges = await storage.get_edges_for(
        topic_ids, direction="from", edge_type=EdgeType.SUBTOPIC_OF
    )
    child_edges = await storage.get_edges_for(
        topic_ids, direction="to", edge_type=EdgeType.SUBTOPIC_OF
    )

    neighbours_by_topic: dict[str, tuple[list[str], list[str]]] = {}
    needed: set[str] = set()
    for topic_id in topic_ids:
        parent_ids = [e.dst_id for e in parent_edges[topic_id]]
        child_ids = [e.src_id for e in child_edges[topic_id]]
        neighbours_by_topic[topic_id] = (parent_ids, child_ids)
        needed.update(parent_ids)
        needed.update(child_ids)

    known: dict[str, EpistemicNode] = {n.id: n for n in nodes}
    for node_id in needed - known.keys():
        neighbour = await storage.get_node(node_id)
        if neighbour is not None:
            known[node_id] = neighbour

    annotations: dict[str, dict] = {}
    for topic_id, (parent_ids, child_ids) in neighbours_by_topic.items():
        annotation: dict = {}
        parents = [known[i] for i in parent_ids if i in known]
        children = [known[i] for i in child_ids if i in known]
        if parents:
            annotation["parents"] = [_content_preview(p) for p in parents]
        if children:
            annotation["subtopics"] = [_content_preview(c) for c in children]
        if annotation:
            annotations[topic_id] = annotation
    return annotations


async def topic_tree(
    topic_id: str,
    storage: StorageBackend,
    *,
    depth: int = 2,
) -> tuple[dict, ResponseMeta]:
    """Ancestors and a depth-limited subtree for one topic, previews only.

    The drill-down primitive for a split hierarchy: it answers "what is under
    this topic, and what is it part of" with shape and identity rather than
    material, so the caller can pick a branch and fetch only that.

    `depth` counts levels of descendants — 1 is direct subtopics only. A node
    held back by the limit that does have children is flagged ``has_more``, so a
    truncated branch is never mistaken for a leaf.
    """
    from epimemer.pipelines.reflection.topic_hierarchy import (
        get_ancestors,
        get_children,
    )

    if depth < 1:
        raise ValueError("depth must be at least 1")

    node = await storage.get_node(topic_id)
    if node is None:
        raise ValueError(f"Topic {topic_id} not found")
    if not isinstance(node, Topic):
        raise ValueError(f"Node {topic_id} is not a Topic")

    # Shared across the recursion so a DAG with several paths to the same
    # subtopic reports it once, and a malformed cyclic graph still terminates.
    visited: set[str] = {topic_id}

    async def descend(node_id: str, remaining: int) -> list[dict]:
        entries: list[dict] = []
        for child in await get_children(storage, node_id):
            if child.id in visited:
                continue
            visited.add(child.id)
            entry = _content_preview(child)
            if remaining > 1:
                entry["subtopics"] = await descend(child.id, remaining - 1)
            else:
                entry["subtopics"] = []
                if await get_children(storage, child.id):
                    entry["has_more"] = True
            entries.append(entry)
        return entries

    ancestors = await get_ancestors(storage, topic_id)
    subtopics = await descend(topic_id, depth)

    result = {
        "topic": _content_preview(node),
        "ancestors": [_content_preview(a) for a in ancestors],
        "subtopics": subtopics,
        "depth": depth,
    }
    meta = ResponseMeta(
        nodes_returned=len(visited) + len(ancestors),
        source_types={"topic": len(visited) + len(ancestors)},
        # id + preview is still "the agent saw this node".
        retrieved=_declare([node.id, *(a.id for a in ancestors), *visited]),
    )
    return result, meta


def retrieved_signal(value: ValueSignal, at: datetime) -> ValueSignal:
    """The value signal a node carries after being retrieved.

    Stamps `retrieved_at` and nothing else. Every other field is carried
    through unchanged — retrieval records *use*, and must not quietly restate a
    judgment held elsewhere in the signal. `importance_judged_at` is part of
    that: being read is not being judged.

    This used to also raise a `relevance` float asymptotically. That field was
    removed (no reader, and confounded by reflect frequency), which leaves the
    timestamp as the whole of what retrieval records — and makes this the
    complete answer to "when was this last used?".
    """
    return value.model_copy(update={"retrieved_at": at})


async def _record_retrieval(
    nodes: Sequence[EpistemicNode], storage: StorageBackend, enabled: bool
) -> None:
    """Stamp `retrieved_at` on every node search returned.

    Without this the only thing known about a node is its age, which says
    nothing about whether it is load-bearing — exactly the distinction archival
    candidacy needs, and the reason `never_retrieved` can mean what it says.

    It deliberately does **not** feed ranking: results stay ordered by
    similarity. Wiring use back into ranking creates a `retrieved → ranked
    higher → retrieved` loop under which popular nodes crowd out better
    matches. See `dev-docs/REVIEW_EPISTEMIC.md` §12.4.

    Costs one write per returned node, which is why it can be switched off.
    """
    if not enabled:
        return
    at = datetime.now(timezone.utc)
    for node in nodes:
        node.value = retrieved_signal(node.value, at)
        # No backend shares object identity with its callers, so the mutation
        # above is local until it is written back.
        await storage.store_node(node)


async def search(
    query: str,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    k: int = 10,
    node_types: list[str] | None = None,
    graph_hops: int = 1,
    metacontexts: list[str] | None = None,
    terms: list[str] | None = None,
    include_historical: bool = True,
    include_corrected: bool = False,
    valid_as_of: datetime | None = None,
    timeline_id: str | None = None,
    include_corroboration: bool = False,
    record_retrieval: bool = DEFAULT_RECORD_RETRIEVAL,
    event_bus: InProcessEventBus | None = None,
) -> tuple[dict, ResponseMeta]:
    """Search the memory graph: embedding similarity and keyword matching, fused.

    **Pass identifiers, names and exact phrases you care about as `terms`.**
    A ticket id, an error code, a person's name, a filename — anything where the
    exact string matters. Embeddings shred those: `JIRA-4417` becomes word
    pieces mean-pooled with the rest of the sentence, so the query embeds to
    roughly "short alphanumeric string" and every *other* ticket id in the graph
    scores about as well. Keyword matching supplies the term rarity that
    similarity has no notion of, and a declared term's best hit is kept in the
    results even if rank fusion would otherwise have cut it.

    Terms are matched whole and ORed: `terms=["JIRA-4417", "certificate
    rotation"]` finds nodes matching either, and each term matches only
    documents containing all of its words. Omit `terms` and the keyword arm
    falls back to the query's own words — rare ones still fire, common ones
    contribute nothing, and there is no survival guarantee. Declaring is the
    reliable path.

    Each returned node carries `provenance` saying how it was reached:
    `lexical` (an exact term matched its content), `segment` (a term matched the
    source passage it was extracted from), `vector` (embedding similarity), or
    `expanded` (reached by an edge from one of the above). The response also
    carries `segments` — the passages that matched, whether or not anything was
    extracted from them, since *where did I read that?* is a different question
    from *what do I believe?*

    `metacontexts` scopes results to nodes standing in **any** of the frames
    listed — a union the caller states, with no frame inheriting another. A
    question about a novel's world read against real history names both;
    omitting the list searches every frame, which is a coherent question rather
    than an unstated assumption, and is why this side is optional where ingest
    is not. Frame-scoping over-fetches so an in-frame node ranked below
    the vector top-k is still found (see `_retrieve_frame_scoped`). Metacontext labels and computed
    review labels (superseded_candidate / evidence_stale / evidence_merged /
    contested) are always included on returned nodes. Returned Topics that sit in a split hierarchy also
    carry `parents` / `subtopics` as id + preview, so the caller can drill via
    `topic_tree` instead of being handed the whole subtree.

    **Knowledge that is not current is still knowledge**, so a claim retired
    because the world moved on is returned by default and says so in its
    `status`. Its earlier versions do not compete for slots: when a retired node
    and the claim that replaced it both match, the replacement takes the slot and
    the retired one comes back under `earlier_versions` on it. Claims retired for
    being *wrong* are off by default (`include_corrected`), kept for the audit
    trail rather than for reading.

    Nodes whose sources dated them carry `validity` — one entry per source, with
    the periods that source asserts. Nothing is collapsed across sources: union
    takes one careful source and one sloppy one and yields a period neither
    claims, and intersection turns two separate episodes into "never".

    `valid_as_of` asks *what was true then*, and answers with two groups rather
    than a filter: every result carries `valid_at`, either `valid` (some source
    asserts it held then) or `unknown` (nobody says). It never excludes, because
    an interval asserts nothing about the world outside itself — a moment nobody
    dated is unknown, not false, so there is no third group to exclude into. A
    claim provably valid then also keeps its own slot rather than being folded
    into a later version of itself.

    `include_corroboration` adds, per node, how many *independent* sources back
    it — distinct publishers across its similarity neighbourhood, with the
    contributing nodes so the number can be checked. A look-alike whose stated
    periods provably fall clear of this claim's is a claim about *another*
    stretch of time rather than a witness to this one, so it does not count; it
    comes back named under `adjacent_periods`, which is often the only place a
    caller learns the adjacent claim exists. **Off by default, on a
    measurement**: it is the most expensive annotation on this path by a
    wide margin, and it costs more the more similarity edges `reflect` has
    written, so it grows fastest on exactly the graphs where it says most.

    Returned nodes have `retrieved_at` stamped (`record_retrieval=False`
    disables): being retrieved is what tells a used node from a merely old one.
    Ranking is unaffected — see `_record_retrieval`.
    """
    from epimemer.pipelines.query.corroboration import corroboration_for
    from epimemer.pipelines.query.types import QueryRequest, SeedProvenance
    from epimemer.pipelines.query.validity import validity_for, verdict_for
    from epimemer.pipelines.reflection.review import review_labels_for

    # A frame that does not resolve here would narrow the search to nothing and
    # answer as though the graph held nothing about it — the wrong-graph failure
    # one layer in, and on the read side, where there is no artifact left
    # anywhere afterwards. Every named frame is checked, not just the first: a
    # union with one dead id answers a narrower question than the caller asked,
    # silently.
    for frame in (metacontexts or []):
        await require_metacontext(frame, storage)

    # Map string node types to enums
    nt_enums = None
    if node_types:
        nt_enums = [NodeType(t) for t in node_types]

    request = QueryRequest(
        query_text=query,
        k=k,
        node_types=nt_enums,
        graph_hops=graph_hops,
        model_id=embedding_provider.model_id,
        terms=terms,
        statuses=reachable_statuses(
            include_historical=include_historical,
            include_corrected=include_corrected,
        ),
        valid_as_of=valid_as_of,
        timeline_id=timeline_id,
    )

    if metacontexts:
        nodes, query_result = await _retrieve_frame_scoped(
            request, embedding_provider, storage, metacontexts, event_bus
        )
    else:
        query_result = await _run_retrieval(
            request, embedding_provider, storage, event_bus
        )
        nodes = query_result.nodes

    edges_data = [e.model_dump(mode="json") for e in query_result.edges]

    # Reinforce before serializing, so the caller sees the signal the node now
    # holds rather than the one it held a moment ago.
    await _record_retrieval(nodes, storage, record_retrieval)

    # Build node dicts with metacontext labels, computed review labels, and —
    # for topics in a split hierarchy — their neighbours, so the caller can
    # drill rather than be handed the whole subtree.
    hierarchy = await _hierarchy_annotations(nodes, storage)
    labels_by_node = await _metacontext_labels_for([n.id for n in nodes], storage)
    review_by_node = await review_labels_for(nodes, storage)
    # Read over the final set, which expansion has added to since the collapse
    # transition read the seeds. One batched edge query, and the only place the
    # stored intervals become visible to a caller (T1 §3).
    validity_by_node = await validity_for([n.id for n in nodes], storage)
    # Asked for rather than always run. Every other annotation here is a fixed
    # number of batched queries over the result set; this one walks out to each
    # node's similarity neighbourhood, so its cost follows an edge density
    # nothing bounds.
    corroboration_by_node = (
        await corroboration_for([n.id for n in nodes], storage)
        if include_corroboration
        else {}
    )
    verdicts = (
        {
            node.id: verdict_for(
                validity_by_node.get(node.id, []),
                valid_as_of,
                timeline_id=timeline_id,
            ).value
            for node in nodes
        }
        if valid_as_of is not None
        else {}
    )

    nodes_data = []
    for node in nodes:
        node_dict = _node_to_dict(node)
        # How this node was reached. Frame-scoping can hand back a node the
        # final run did not rank, so the label falls back to `expanded` rather
        # than being omitted — every returned node says something about itself.
        node_dict["provenance"] = query_result.provenance.get(
            node.id, SeedProvenance.EXPANDED
        ).value
        if labels_by_node[node.id]:
            node_dict["metacontexts"] = labels_by_node[node.id]
        if node.id in review_by_node:
            node_dict["review"] = review_by_node[node.id]
        if node.id in validity_by_node:
            node_dict["validity"] = [
                source.model_dump(mode="json")
                for source in validity_by_node[node.id]
            ]
        if node.id in corroboration_by_node:
            node_dict["corroboration"] = corroboration_by_node[node.id].model_dump(
                mode="json"
            )
        if node.id in verdicts:
            node_dict["valid_at"] = verdicts[node.id]
        if node.id in query_result.lineage:
            node_dict["earlier_versions"] = [
                _content_preview(earlier) | {"status": earlier.status.value}
                for earlier in query_result.lineage[node.id]
            ]
        node_dict.update(hierarchy.get(node.id, {}))
        nodes_data.append(node_dict)

    result = {
        "nodes": nodes_data,
        "edges": edges_data,
        # Passages the keyword arm matched, in their own right. A segment is not
        # a graph node and must not be pretended into one.
        "segments": [hit.model_dump(mode="json") for hit in query_result.segments],
    }
    if valid_as_of is not None:
        # T3's groups, built from the per-node labels above rather than computed
        # a second time: two places deriving one rule is how they come to
        # disagree, and this response would then contradict itself.
        result["valid_at"] = {
            verdict.value: [
                node_id for node_id, label in verdicts.items() if label == verdict.value
            ]
            for verdict in ValidityVerdict
        }
    meta = ResponseMeta(
        nodes_searched=query_result.metadata.nodes_searched,
        nodes_returned=len(nodes),
        graph_hops=query_result.metadata.graph_hops,
        source_types=query_result.metadata.source_types,
        # The provenance the response already carries, declared for the
        # dashboard. Same fallback as the serialized dict above, so the two
        # cannot disagree about how a node was reached.
        retrieved=_declare(
            (node.id for node in nodes),
            provenance={
                node.id: query_result.provenance.get(node.id, SeedProvenance.EXPANDED)
                for node in nodes
            },
        ),
    )
    return result, meta


# --- Temporal queries ---


def events_in_window(
    node: EpistemicNode, start: datetime, end: datetime,
) -> list[NodeChangeEvent]:
    """Lifecycle events on a node that fall in the half-open window [start, end).

    Emits `created` when the node was born in the window, and one event per
    lifecycle episode boundary that falls inside it: the status the retirement
    gave the node — retiring as `historical` and retiring as `corrected` are
    different things to report — with the counterpart that caused it, and
    `restored` where the node came back.

    The episodes are read rather than `(status, superseded_at)` because that
    pair holds only the *latest* transition: a node that retired, returned and
    retired again has three events and one `superseded_at`.
    """
    events: list[NodeChangeEvent] = []
    if start <= node.created_at < end:
        events.append(NodeChangeEvent(kind="created", at=node.created_at))

    for episode in node.lifecycle:
        if start <= episode.retired_at < end:
            events.append(NodeChangeEvent(
                kind=episode.because.value,
                at=episode.retired_at,
                counterpart=episode.counterpart,
            ))
        if episode.restored_at is not None and start <= episode.restored_at < end:
            events.append(NodeChangeEvent(kind="restored", at=episode.restored_at))

    # A retirement no episode records: a graph written before episodes existed.
    # Reported without a counterpart rather than dropped — old graphs are not
    # repaired, but they are still readable.
    if (
        node.superseded_at is not None
        and node.status is not NodeStatus.ACTIVE
        and all(ep.retired_at != node.superseded_at for ep in node.lifecycle)
        and start <= node.superseded_at < end
    ):
        events.append(
            NodeChangeEvent(kind=node.status.value, at=node.superseded_at)
        )

    return sorted(events, key=lambda event: event.at)


async def graph_as_of(
    at: datetime,
    storage: StorageBackend,
    *,
    node_types: list[str] | None = None,
) -> tuple[dict, ResponseMeta]:
    """Snapshot the active knowledge set as it stood at instant `at`.

    Returns the nodes that had been created by `at` and were not yet retired then
    (the storage `at_time` temporal filter). This is a node-lifecycle snapshot
    only: edges, metacontext, and review labels are *not* time-versioned, so they
    are intentionally omitted — they would reflect the present graph, not the
    graph at `at`.

    **`graph_` is the whole point of the name.** This is *transaction* time —
    what the graph held then — and the other axis, what was *true* then, is
    `search(valid_as_of=…)`. SQL:2011 marks both (`FOR SYSTEM_TIME AS OF`, `FOR
    APPLICATION_TIME AS OF`) because "as of" alone does not say which clock, and
    an unmarked name inherits the default reading: in a knowledge graph, "as of
    1980" reads as *what was true in 1980*, which is the axis this does not
    answer. It was called `as_of` until valid time arrived.
    """
    nt_enums = [NodeType(t) for t in node_types] if node_types else [None]
    nodes: list[EpistemicNode] = []
    for nt in nt_enums:
        nodes.extend(await storage.query_nodes(at_time=at, node_type=nt))

    source_types: dict[str, int] = {}
    for node in nodes:
        key = _node_type_key(node)
        source_types[key] = source_types.get(key, 0) + 1

    result = {
        "at": at.isoformat(),
        "nodes": [_node_to_dict(n) for n in nodes],
    }
    meta = ResponseMeta(
        nodes_returned=len(nodes),
        source_types=source_types,
        retrieved=_declare(n.id for n in nodes),
    )
    return result, meta


async def query_changes(
    windows: list[tuple[datetime, datetime]],
    storage: StorageBackend,
    *,
    node_types: list[str] | None = None,
) -> tuple[dict, ResponseMeta]:
    """What changed (births + retirements) in one or more time windows.

    For each half-open window [start, end), returns the nodes whose creation or
    retirement fell inside it, each tagged with the specific lifecycle event(s)
    and enriched with metacontext + review labels (these are current nodes, so
    present-state labels are accurate). Results are grouped per window; a node
    that changed in several windows appears in each.
    """
    from epimemer.pipelines.reflection.review import review_labels_for

    nt_enums = [NodeType(t) for t in node_types] if node_types else [None]

    windows_data = []
    total = 0
    source_types: dict[str, int] = {}
    # Across every window, since the record is per response and a node that
    # changed twice was still shown once.
    changed_ids: list[str] = []
    for start, end in windows:
        seen: dict[str, EpistemicNode] = {}
        for nt in nt_enums:
            for node in await storage.query_changes(start=start, end=end, node_type=nt):
                seen[node.id] = node

        changed = list(seen.values())
        labels_by_node = await _metacontext_labels_for([n.id for n in changed], storage)
        review_by_node = await review_labels_for(changed, storage)

        changes = []
        for node in changed:
            node_dict = _node_to_dict(node)
            node_dict["events"] = [
                e.model_dump(mode="json") for e in events_in_window(node, start, end)
            ]
            if labels_by_node[node.id]:
                node_dict["metacontexts"] = labels_by_node[node.id]
            if node.id in review_by_node:
                node_dict["review"] = review_by_node[node.id]
            changes.append(node_dict)
            changed_ids.append(node.id)

            key = _node_type_key(node)
            source_types[key] = source_types.get(key, 0) + 1
            total += 1

        windows_data.append({
            "start": start.isoformat(),
            "end": end.isoformat(),
            "changes": changes,
        })

    result = {"windows": windows_data}
    meta = ResponseMeta(
        nodes_returned=total,
        source_types=source_types,
        retrieved=_declare(changed_ids),
    )
    return result, meta


# --- Source / topic / relation queries ---


async def _resolve_hub_id(value: str, storage: StorageBackend) -> str:
    """Resolve a hub reference to an id: a node id, a Topic name, or a document's
    source name (e.g. "ISSUES.md"). Falls back to the raw value if none match.
    """
    if await storage.get_node(value) is not None:
        return value
    topic = await storage.get_node_by_content(value, node_type=NodeType.TOPIC)
    if isinstance(topic, Topic):
        return topic.id
    doc = await storage.get_document_by_source(value)
    if doc is not None:
        return doc.id
    return value


async def find_nodes(
    storage: StorageBackend,
    *,
    sourced_from: str | None = None,
    tagged_with: str | None = None,
    node_types: list[str] | None = None,
    status: str = "active",
    limit: int = 50,
) -> tuple[dict, ResponseMeta]:
    """Find nodes connected to a source or topic hub by graph traversal.

    `sourced_from` (a document/entity id or name) returns the nodes with a
    `sourced_from` edge to it — "which nodes came from X". `tagged_with` (a Topic
    id or name) returns the nodes tagged with that concept. A native graph query,
    replacing the old string-filter listing.
    """
    if tagged_with is not None:
        hub_id = await _resolve_hub_id(tagged_with, storage)
        edge_type = EdgeType.TAGGED_WITH
    elif sourced_from is not None:
        hub_id = await _resolve_hub_id(sourced_from, storage)
        edge_type = EdgeType.SOURCED_FROM
    else:
        raise ValueError("find_nodes requires sourced_from or tagged_with")

    st = NodeStatus(status)
    allowed = set(node_types) if node_types else None

    nodes: list[EpistemicNode] = []
    seen: set[str] = set()
    for edge in await storage.get_edges_to(hub_id, edge_type=edge_type):
        if edge.src_id in seen:
            continue
        seen.add(edge.src_id)
        node = await storage.get_node(edge.src_id)
        if node is None or node.status != st:
            continue
        if allowed and _node_type_key(node) not in allowed:
            continue
        nodes.append(node)
        if len(nodes) >= limit:
            break

    source_types: dict[str, int] = {}
    for node in nodes:
        key = _node_type_key(node)
        source_types[key] = source_types.get(key, 0) + 1
    result = {"nodes": [_node_to_dict(n) for n in nodes]}
    meta = ResponseMeta(
        nodes_returned=len(nodes),
        source_types=source_types,
        retrieved=_declare(n.id for n in nodes),
    )
    return result, meta


async def list_sources(storage: StorageBackend) -> tuple[dict, ResponseMeta]:
    """Distinct source/origin nodes with how many nodes reference each — the
    documents nodes are `sourced_from`, plus entities linked by attribution edges
    (e.g. published_by). Discovery before find_nodes."""
    node_ids = [node.id for node in await storage.query_nodes()]
    sourced_from = await storage.get_edges_for(
        node_ids, direction="from", edge_type=EdgeType.SOURCED_FROM
    )
    attributed = await storage.get_edges_for(
        node_ids, direction="to", edge_type=EdgeType.RELATED
    )

    counts: dict[str, int] = {}
    for node_id in node_ids:
        for e in sourced_from[node_id]:
            counts[e.dst_id] = counts.get(e.dst_id, 0) + 1
        for e in attributed[node_id]:
            if e.kind == "attribution":
                counts[node_id] = counts.get(node_id, 0) + 1

    sources = []
    for dst_id, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        doc = await storage.get_document(dst_id)
        node = await storage.get_node(dst_id)
        name = (doc.source if doc and doc.source else None) or (
            node.content if node else dst_id
        )
        kind = "document" if doc else ("entity" if node else "unknown")
        sources.append({"id": dst_id, "name": name, "kind": kind, "node_count": count})

    result = {"sources": sources}
    # Documents among these are not graph nodes and simply never match one in
    # the dashboard; the entity topics are, and are the reason this declares.
    meta = ResponseMeta(
        nodes_returned=len(sources),
        retrieved=_declare(source["id"] for source in sources),
    )
    return result, meta


async def list_relations(storage: StorageBackend) -> tuple[dict, ResponseMeta]:
    """Distinct user-tier relationship labels (with kind, usage count and
    description) — discovery before coining a new label or consolidating
    synonyms via apply_reflection.

    **Counts stay derived from the edges** rather than stored on the record.
    They are scoped to active nodes, so a stored count would drift
    the moment a node was retired, and the record has nothing to say about
    usage — it holds what the label *means*.

    **Standing verdicts ride on each label they cover.** Requiring `because`
    at the write was justified by the next agent — *who otherwise skips the
    pair without knowing whether it was examined or waved through* — and this
    is where that agent reads it back: each relation carries the verdicts
    naming it, with the other label, the verdict, the reason, who judged, and
    when. Newest first, both rows of a disagreement included.
    """
    from epimemer.pipelines.reflection.relation_consolidation import (
        related_edges_of_active_nodes,
    )

    counts: dict[tuple[str, str], int] = {}
    for e in await related_edges_of_active_nodes(storage):
        counts[(e.label or "", e.kind)] = counts.get((e.label or "", e.kind), 0) + 1

    # The vocabulary in one read rather than one per label, and the join is
    # left-outer on purpose: a label with no record is a graph nobody has
    # described since labels gained records, not an error, and it answers exactly as it
    # did before — with an empty description and no verdicts.
    labels = await storage.query_relation_labels()
    described = {(rl.name, rl.kind): rl.description for rl in labels}

    # Verdicts read whole and grouped onto both sides of their pair in memory:
    # the table is structurally small (at most two rows per pair), and ids
    # resolve to names here because the row stores record ids — the name is
    # what the reader recognises, the id is what suppression is keyed on.
    names_by_id = {rl.id: rl.name for rl in labels}
    ids_by_name = {(rl.name, rl.kind): rl.id for rl in labels}
    verdict_rows = await storage.query_relation_verdicts()
    # The judge as the user knows them: a name where the registry holds
    # one, the recorded id where it does not — which is what an unregistered
    # judge is called.
    judge_names = (
        {
            alias: agent_name(agent)
            for agent in live_agents(await storage.list_agents())
            for alias in agent_aliases(agent)
        }
        if any(v.judged_by for v in verdict_rows)
        else {}
    )
    verdicts_by_label: dict[str, list[dict]] = {}
    for v in sorted(verdict_rows, key=lambda row: row.decided_at, reverse=True):
        if len(v.label_ids) != 2:
            continue
        for this_id, other_id in (v.label_ids, list(reversed(v.label_ids))):
            verdicts_by_label.setdefault(this_id, []).append({
                "with": names_by_id.get(other_id, other_id),
                "verdict": v.verdict,
                "because": v.because,
                "judged_by": (
                    judge_names.get(v.judged_by.agent_id, v.judged_by.agent_id)
                    if v.judged_by
                    else None
                ),
                "decided_at": v.decided_at.isoformat(),
            })

    relations = [
        {
            "label": label,
            "kind": kind,
            "count": c,
            "description": described.get((label, kind), ""),
            "verdicts": verdicts_by_label.get(
                ids_by_name.get((label, kind), ""), []
            ),
        }
        for (label, kind), c in sorted(counts.items(), key=lambda kv: -kv[1])
    ]
    result = {"relations": relations}
    meta = ResponseMeta(nodes_returned=len(relations))
    return result, meta


async def describe_relation(
    name: str,
    storage: StorageBackend,
    *,
    description: str,
    kind: str = "relationship",
    judge: JudgeRef | None = None,
) -> tuple[dict, ResponseMeta]:
    """Say what one of this graph's relationship labels means here.

    Advisory prose, not a schema. It is free to say *"in the Court context this
    means X; for corporate contracts use Y"* — the system never enforces it, and
    making it enforceable is the step that would turn a vocabulary into a
    schema. It describes the shared **label**, never one edge: per-edge meaning
    is what would make this a hypergraph.

    **This is the half that pays**, because it moves the intervention from
    repair to prevention. An agent picking from a described vocabulary never
    coins the fourth synonym, and no merge is needed to clean up after it.

    Refused where the graph cannot back the claim up:

    - **A label no edge carries.** Describing a word this graph has never used
      would put a record in the vocabulary that names nothing, and the next
      agent reading `list_relations` would see a label with a meaning and no
      usage.
    - **A `kind` the edges do not carry.** The kind decides whether retrieval
      follows the edge, so it is in force on the edges and this record only
      mirrors it; changing it here would leave the record disagreeing with every
      edge it describes.

    The record is created if the label has none, carrying **no judge**:
    describing a label is not claiming to have introduced it, and the
    description is journalled in its own right. Re-describing replaces the text
    and journals a second row — the first row is not edited, and the record's
    `judged_by` never moves.
    """
    in_force = await storage.get_relation_kind(name)
    if in_force is None:
        return (
            {
                "described": False,
                "name": name,
                "refused": (
                    f"No edge in this graph carries the relation '{name}'. "
                    f"`list_relations` shows the vocabulary that exists; `link` "
                    f"coins a new label by using it."
                ),
            },
            ResponseMeta(nodes_returned=0),
        )
    if in_force != kind:
        return (
            {
                "described": False,
                "name": name,
                "kind": in_force,
                "refused": (
                    f"'{name}' is a '{in_force}' relation in this graph, not "
                    f"'{kind}'. The kind is in force on the edges and this "
                    f"record only mirrors it, so it cannot be changed from "
                    f"here."
                ),
            },
            ResponseMeta(nodes_returned=0),
        )

    existing = await storage.get_relation_label(name, in_force)
    # `judged_by=None` on a record this call creates, and untouched on one it
    # does not — `recorded_relation_label` keeps the coiner, which is what makes
    # that rule structural rather than a convention every caller remembers.
    #
    # The merge is computed here as well as inside the backend so the response
    # can report **what was stored** rather than what was asked for. The two
    # differ in one case that matters: a blank `description` leaves existing
    # prose alone, so echoing the argument back would tell the agent it had
    # cleared a description it had not.
    stored = recorded_relation_label(
        existing, RelationLabel(name=name, kind=in_force, description=description)
    )
    label_id = await storage.store_relation_label(stored)
    await journal(
        storage, DecisionKind.RELATION_DESCRIPTION, [label_id], judge=judge
    )

    result = {
        "described": True,
        "relation_label_id": label_id,
        "name": name,
        "kind": in_force,
        "description": stored.description,
        "created": existing is None,
    }
    return result, ResponseMeta(nodes_returned=1)


# --- Link ---


async def link(
    src_id: str,
    dst_id: str,
    storage: StorageBackend,
    *,
    edge_type: str | None = None,
    relation: str | None = None,
    kind: str = "relationship",
    weight: float = 1.0,
    metadata: dict | None = None,
    judge: JudgeRef | None = None,
) -> tuple[dict, ResponseMeta]:
    """Create a direct edge between two existing nodes.

    Give either `edge_type` (a known engine EdgeType) or `relation` (a free
    user-defined label → a RELATED edge). For a user relation, `kind` is
    "relationship" (followed in retrieval) or "attribution" (not); a label already
    in use reuses its existing kind (set once per label).
    """
    description = ""
    if relation is not None:
        et = EdgeType.RELATED
        resolved_kind = await storage.get_relation_kind(relation) or kind
        label = relation
        # The label's record, created on first use. One extra read on the
        # common path and a write only when a label is coined — which is the
        # moment, and the only moment, at which somebody is claiming to have
        # introduced this word. A label already recorded is left exactly as it
        # is: re-coining does not restamp the judge, because the second agent
        # did not introduce it.
        record = await storage.get_relation_label(label, resolved_kind)
        if record is None:
            await storage.store_relation_label(
                RelationLabel(name=label, kind=resolved_kind, judged_by=judge)
            )
        else:
            # What this graph already means by the word, told to the agent
            # reusing it **at the moment it matters** rather than only to one
            # that thought to call `list_relations` first. This is information,
            # not a redirect: nothing here steers a coinage (§8), because a
            # nudge that cannot carry the distinction it is overruling is the
            # loop FC3 describes.
            description = record.description
    elif edge_type is not None:
        try:
            et = EdgeType(edge_type)
        except ValueError:
            valid = [e.value for e in EdgeType]
            raise ValueError(f"Invalid edge_type '{edge_type}'. Valid types: {valid}")
        resolved_kind = "relationship"
        label = None
    else:
        raise ValueError("link requires either edge_type or relation")

    # Verify both nodes exist
    if await storage.get_node(src_id) is None:
        raise ValueError(f"Source node '{src_id}' not found")
    if await storage.get_node(dst_id) is None:
        raise ValueError(f"Destination node '{dst_id}' not found")

    edge = NodeEdge(
        src_id=src_id,
        dst_id=dst_id,
        type=et,
        label=label,
        kind=resolved_kind,
        weight=weight,
        judged_by=judge,
        metadata=metadata or {},
    )
    await storage.store_edge(edge)
    await journal(storage, DecisionKind.RELATION, [src_id, dst_id], judge=judge)

    result = {"edge_id": edge.id, "kind": resolved_kind}
    if description:
        result["relation_description"] = description
    meta = ResponseMeta(nodes_returned=2)
    return result, meta


# --- Update ---


async def update(
    node_id: str,
    new_content: str,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    because: str,
    judge: JudgeRef | None = None,
) -> tuple[dict, ResponseMeta]:
    """Update a node by creating a new version (supersession).

    The replacement is embedded so it remains searchable.

    `because` says which of two opposite things happened — `"it_was_wrong"` or
    `"the_world_changed"` — and has no default on purpose. A claim that
    stopped being true was never an error, and recording it as one is how a
    graph forgets its own history.

    It also decides which edges follow the replacement. A correction
    hands over everything but history, review and judgments: the old node is an
    audit husk and the replacement is the same claim, corrected. A world-change
    hands over the frame and the tags only — the retired node keeps its own
    provenance, because it is still true of its period and its sources are what
    say so.

    Judgments — similarity, contradiction, variant_of — stay behind under
    *either* reason. The claim may survive a correction; the wording the
    judgment was made against does not.

    And it decides which lineage edge records the step: `superseded_by` says
    *replaced* and is terminal, `temporally_followed_by` says only *came after*
    and survives the same claim becoming true again.
    """
    from epimemer.pipelines.graph_construction.versioning import supersede_node

    old_node = await storage.get_node(node_id)
    if old_node is None:
        raise ValueError(f"Node '{node_id}' not found")

    # Create new node of the same type, carrying over the value signal so a
    # content correction does not reset reinforcement history. The signal is
    # copied (not shared) so later reinforcement of the new node cannot mutate
    # the superseded original's recorded value.
    #
    # `extraction_method` carries over for the same reason: correcting the
    # wording does not change where the material came from.
    # `judged_by` is deliberately *not* carried over: the replacement is this
    # agent's wording, and inheriting the previous author would credit them with
    # a sentence they never wrote.
    carried_value = old_node.value.model_copy()
    carried_method = old_node.extraction_method
    if isinstance(old_node, Topic):
        new_node: EpistemicNode = Topic(
            content=new_content, source_id=old_node.source_id,
            value=carried_value, extraction_method=carried_method, judged_by=judge,
        )
    elif isinstance(old_node, Fact):
        new_node = Fact(
            content=new_content, source_id=old_node.source_id,
            value=carried_value, extraction_method=carried_method, judged_by=judge,
        )
    elif isinstance(old_node, Inference):
        new_node = Inference(
            content=new_content, source_id=old_node.source_id,
            value=carried_value, extraction_method=carried_method, judged_by=judge,
        )
    else:
        raise ValueError(f"Unknown node type for node '{node_id}'")

    status = superseded_status_for(because)
    edge = await supersede_node(
        old_node, new_node, storage, embedding_provider,
        status=status, judge=judge,
    )
    # The kind carries `because`, rather than a second field repeating it: a
    # correction and a world-change are opposite claims about what happened
    #, and a reviewer asking for one does not want the other.
    await journal(
        storage, supersession_kind(status), [old_node.id, new_node.id], judge=judge
    )

    result = {
        "old_node_id": old_node.id,
        "new_node_id": new_node.id,
        "edge_id": edge.id,
    }
    meta = ResponseMeta(
        nodes_returned=2, retrieved=_declare([new_node.id, old_node.id])
    )
    return result, meta


JudgmentDirection = Literal["up", "down"]


def judged_importance(
    importance: float, direction: JudgmentDirection, step: float
) -> float:
    """`importance` after one judgment in `direction`.

    ::

        up:    importance += step * (1 - importance)     # asymptotic to 1.0
        down:  importance -= step * importance           # asymptotic to 0.0

    Each closes the gap to *its own* bound by the same fraction, so the two are
    mirrors — and deliberately **not** inverses. Up-then-down does not return
    home (0.5 -> 0.625 -> 0.469), and repeated alternation settles into a
    two-cycle straddling 0.5: {3/7, 4/7} at the default step. Neither side
    wins, so a node two agents disagree about parks at the un-judged default,
    with the most recent judgment deciding which side of the nomination ceiling
    it currently sits on.

    An exactly invertible form was considered and rejected twice over.
    ``(i - step)/(1 - step)`` returns home but goes negative below the step size
    and needs a clamp — invertible in the mid-range where nothing needs it,
    lossy near the floor where the nomination ceiling sits. Log-odds is
    genuinely both invertible and asymptotic, but costs the settable knob
    (``EPIMEMER_IMPORTANCE_STEP`` means "close a quarter of the remaining gap";
    a log-odds constant means nothing to anyone) and needs input clamping
    anyway. Both buy invertibility, which nothing here consumes: a later
    downward judgment is a new assessment on new information, not an undo, and
    the provenance trail keeps both entries deliberately.

    Neither direction reaches its bound, so arithmetic can never judge a node
    into certainty or out of existence.
    """
    if direction == "up":
        return importance + step * (1.0 - importance)
    if direction == "down":
        return importance - step * importance
    raise ValueError(f"Unknown direction '{direction}' - expected 'up' or 'down'")


async def judge_importance(
    node_id: str,
    direction: JudgmentDirection,
    reason: str,
    storage: StorageBackend,
    *,
    related_id: str | None = None,
    importance_step: float = DEFAULT_IMPORTANCE_STEP,
    judge: JudgeRef | None = None,
) -> tuple[dict, ResponseMeta]:
    """Move a node's `importance` by one judgment, and record why.

    The explicit path, in both directions: an agent that learns something making
    an existing node matter more — or less — has nowhere else to put it.
    Retrieval writes a timestamp, not a verdict, so being read a lot cannot
    stand in for having been judged.

    Named for the act rather than the outcome, which is what lets one tool carry
    both directions. `direction` is not ceremony wrapped around the judgment; it
    *is* the judgment — "this matters more than the graph currently thinks", or
    less.

    Not a raw setter, deliberately, and for two reasons beyond auditability. An
    agent setting `0.7` has not seen any other node's value and is guessing at a
    scale it cannot see, while "more than the graph thinks" is a judgment it can
    make well. And a setter is last-writer-wins: three judgments that took a
    node to 0.85 would be erased by one agent typing 0.6 six months later on a
    single conversation's context. Steps compose. (The one moment a setter is
    safe already exists — `store_decomposition`'s ingest prior, applied at
    creation before there is anything to overwrite.)

    Every judgment appends `{at, reason, related_id, direction}` to
    `metadata["reinforcements"]` — one chronological trail, because a reviewer
    wants a bump and its later reversal in sequence with both reasons. The key
    keeps its original name: renaming it is a data migration for a cosmetic
    gain. **An entry carrying no `direction` predates this tool and means "up".**

    `related_id` is validated rather than trusted: a dangling reference in a
    provenance trail is worse than no reference, because it reads as evidence.
    """
    node = await storage.get_node(node_id)
    if node is None:
        raise ValueError(f"Node '{node_id}' not found")

    if related_id is not None and await storage.get_node(related_id) is None:
        raise ValueError(f"Related node '{related_id}' not found")

    # Computed before any write, so an unknown direction leaves the node as it
    # was rather than half-judged.
    importance = judged_importance(node.value.importance, direction, importance_step)

    at = datetime.now(timezone.utc)
    node.value = node.value.model_copy(update={
        "importance": importance,
        # The judgment clock, and only that one. `retrieved_at` belongs to
        # retrieval: an assessment is not traffic, and archival nomination
        # reads the two for different reasons.
        "importance_judged_at": at,
        # Written with the clock, and overwritten with it: this pair is the
        # *latest* judgment, not the history. The history is the
        # `reinforcements` trail below, which each entry now names its judge in.
        "importance_judged_by": judge,
    })
    node.metadata = {
        **node.metadata,
        "reinforcements": [
            *node.metadata.get("reinforcements", []),
            {
                "at": at.isoformat(),
                "reason": reason,
                "related_id": related_id,
                "direction": direction,
                # Per entry, because three judgments by three agents compose
                # into one number and the trail is the only place that stays
                # separable. `None` here means unknown, as everywhere.
                "judged_by": judge.model_dump(mode="json") if judge else None,
            },
        ],
    }
    await storage.store_node(node)
    await journal(storage, DecisionKind.IMPORTANCE, [node.id], judge=judge)

    result = {
        "node_id": node.id,
        "importance": node.value.importance,
        "direction": direction,
        "judgments": len(node.metadata["reinforcements"]),
    }
    return result, ResponseMeta(nodes_returned=1, retrieved=_declare([node.id]))


async def supersede_by(
    old_id: str,
    existing_id: str,
    storage: StorageBackend,
    *,
    because: str,
    judge: JudgeRef | None = None,
) -> tuple[dict, ResponseMeta]:
    """Supersede a node by an already-existing node.

    Use this where the current truth already exists in the graph (rather than
    arriving as new content). `because` distinguishes the two reasons that can
    be true of — `"it_was_wrong"` (a correction) or `"the_world_changed"` (the
    old claim still holds of its period). The old
    node is marked accordingly and joined to `existing_id` by the lineage edge
    that matches — `superseded_by` for a correction, `temporally_followed_by`
    for a world-change; inferences that depended on it are flagged
    evidence_stale; the existing node keeps its own edges. Unlike `update`, no
    new node is created.
    """
    from epimemer.pipelines.graph_construction.versioning import supersede_by_existing

    if old_id == existing_id:
        raise ValueError("A node cannot supersede itself")
    old = await storage.get_node(old_id)
    if old is None:
        raise ValueError(f"Node '{old_id}' not found")
    if await storage.get_node(existing_id) is None:
        raise ValueError(f"Node '{existing_id}' not found")

    status = superseded_status_for(because)
    edge = await supersede_by_existing(
        old, existing_id, storage, status=status, judge=judge,
    )
    await journal(storage, supersession_kind(status), [old_id, existing_id], judge=judge)
    result = {"superseded_id": old_id, "by_id": existing_id, "edge_id": edge.id}
    meta = ResponseMeta(
        nodes_returned=2, retrieved=_declare([existing_id, old_id])
    )
    return result, meta


# --- Review loop: detection + verdict recording ---


async def check_conflicts(
    fact_ids: list[str],
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    threshold: float = SIMILARITY_NOMINATION_THRESHOLD,
    k: int = 5,
) -> tuple[dict, ResponseMeta]:
    """Find facts similar to the given facts, for the agent to judge.

    The recall stage of the review loop (REVIEW_EPISTEMIC.md §5.1): for each fact,
    vector-searches above ``threshold`` (excluding the fact itself) and returns the
    candidates with their similarity score, status, metacontext labels, and a
    same_frame flag. Similarity only *nominates* — the agent then classifies each
    candidate (redundant / supersedes / recurs / contradicts / cross-frame /
    compatible) and records the verdict via supersede_by / restore /
    record_contradiction / record_variant. Opt-in and cheap: a single vector
    lookup per fact at a high bar.

    **Candidates include `historical` nodes, and that is what makes `recurs`
    reachable.** A claim retired because the world moved on can become true
    again — Labour out of government in 2010 and back in 2024 — and until this
    nomination included it, nobody was ever asked: ingest saw no twin and wrote
    a second node saying what the first one said. `corrected` nodes stay out,
    because a claim concluded *wrong* has no route back and nominating it would
    invite a verdict that cannot be recorded.

    Each candidate carries its `status` for the same reason. Once retired nodes
    can appear, an agent cannot tell an active twin from a historical one — and
    that distinction is the entire basis for choosing between `redundant` and
    `recurs`.
    """
    from epimemer.pipelines.reflection.review import same_frame

    model_id = embedding_provider.model_id
    conflicts: list[dict] = []
    candidate_count = 0

    for fact_id in fact_ids:
        source = await storage.get_node(fact_id)
        if not isinstance(source, Fact):
            continue
        embeddings = await storage.get_embeddings_for_item(fact_id, model_id=model_id)
        if not embeddings:
            continue
        # k + 1 because the fact is its own nearest neighbour; trim back to k.
        hits = await storage.vector_search(
            embeddings[0].vector, model_id, k=k + 1, node_type=NodeType.FACT,
            statuses=NOMINATED_STATUSES,
        )
        candidates: list[dict] = []
        for item_id, score in hits:
            if item_id == fact_id or score < threshold:
                continue
            cand = await storage.get_node(item_id)
            if not isinstance(cand, Fact):
                continue
            candidates.append({
                "id": cand.id,
                "content": cand.content,
                "score": round(score, 4),
                "status": cand.status.value,
                "metacontexts": await _metacontext_labels(cand.id, storage),
                "same_frame": await same_frame(fact_id, cand.id, storage),
            })
            if len(candidates) >= k:
                break
        if candidates:
            conflicts.append({
                "fact": {"id": source.id, "content": source.content},
                "candidates": candidates,
            })
            candidate_count += len(candidates)

    result = {"conflicts": conflicts, "threshold": threshold}
    # The review loop's front door. Candidates are `vector` with the cosine as
    # the score — they genuinely are similarity results, so no new provenance
    # value is needed for them (§3, amended). The source facts are declared too:
    # the agent read their content here.
    meta = ResponseMeta(
        nodes_returned=candidate_count,
        retrieved=_declare(
            [
                *(c["fact"]["id"] for c in conflicts),
                *(cand["id"] for c in conflicts for cand in c["candidates"]),
            ],
            provenance={
                cand["id"]: SeedProvenance.VECTOR
                for c in conflicts
                for cand in c["candidates"]
            },
            scores={
                cand["id"]: cand["score"]
                for c in conflicts
                for cand in c["candidates"]
            },
        ),
    )
    return result, meta


async def _journal_pair_judgment(
    storage: StorageBackend,
    kind: DecisionKind,
    a_id: str,
    b_id: str,
    *,
    judge: JudgeRef | None,
    created: bool,
) -> DecisionRecord | None:
    """Journal a verdict about a pair, citing the original where there is one.

    A second agent recording a verdict the pair already carries has
    **confirmed**, not decided — the edge is untouched, and the confirmation is
    its own row pointing back (§3.4). That is what stops a third agent doing the
    work a fourth time, which is §1's defect one layer up.

    Where the pair's verdict predates the journal there is nothing to point at,
    and the row is written with `reviews` blank. It reads as a decision because
    the journal cannot cite a row that does not exist, and inventing a target
    would be worse than the ambiguity.
    """
    reviews = None
    if not created:
        # The oldest is the decision; everything after it is already a
        # confirmation, and a confirmation of a confirmation buries the original.
        prior = await prior_decisions(storage, kind, [a_id, b_id])
        reviews = prior[-1].id if prior else None
    return await journal(
        storage, kind, [a_id, b_id], judge=judge, reviews=reviews
    )



async def record_contradiction(
    a_id: str,
    b_id: str,
    storage: StorageBackend,
    *,
    judge: JudgeRef | None = None,
    warning_policy: WarningPolicy | None = None,
) -> tuple[dict, ResponseMeta]:
    """Record a genuine contradiction between two facts (both stay active).

    Creates a single ``contradiction`` edge (idempotent — one per pair, either
    direction). Both facts remain ACTIVE and retrievable; retrieval flags them
    contested so nothing downstream trusts a contested fact blindly.

    **Both outcomes raise an advisory, and they are opposite ones.** A same-frame
    pair is a real conflict and is `flag` by default, which is what sets
    `notify_user` — the trigger `notify_user` has always had, now expressed as a
    policy a graph can change rather than as a hard-wired condition. A
    cross-frame pair is *not* a genuine contradiction, and its advisory says so.
    Either way the call goes through: the graph records what the agent asserted
    and records that it was told.
    """
    from epimemer.pipelines.reflection.review import same_frame

    if a_id == b_id:
        raise ValueError("A node cannot contradict itself")
    if await storage.get_node(a_id) is None:
        raise ValueError(f"Node '{a_id}' not found")
    if await storage.get_node(b_id) is None:
        raise ValueError(f"Node '{b_id}' not found")

    shares_frame = await same_frame(a_id, b_id, storage)
    edge_id, created = await _ensure_symmetric_edge(
        a_id, b_id, EdgeType.CONTRADICTION, storage, judge=judge
    )
    await _journal_pair_judgment(
        storage, DecisionKind.CONTRADICTION, a_id, b_id,
        judge=judge, created=created,
    )

    advisory = Advisory(
        kind=(
            AdvisoryKind.SAME_FRAME_CONTRADICTION if shares_frame
            else AdvisoryKind.CROSS_FRAME
        ),
        message=(
            "These facts stand in the same frame, so the conflict is real and "
            "unresolved — put it to the user and ask how to settle it."
            if shares_frame else
            "These facts are in different metacontext frames, so this is not a "
            "genuine contradiction — consider record_variant instead."
        ),
        subjects=[a_id, b_id],
    )
    policy = await advisory_policy(
        storage, warning_policy if warning_policy is not None else WarningPolicy()
    )
    result = {
        "edge_id": edge_id,
        "created": created,
        "same_frame": shares_frame,
    } | await carry_advisories(
        storage, policy, [advisory], [a_id, b_id], judge=judge
    )
    meta = ResponseMeta(nodes_returned=2)
    return result, meta


async def record_variant(
    a_id: str,
    b_id: str,
    storage: StorageBackend,
    *,
    judge: JudgeRef | None = None,
    warning_policy: WarningPolicy | None = None,
) -> tuple[dict, ResponseMeta]:
    """Record that two facts are one proposition resolved differently per frame.

    Creates a single ``variant_of`` edge (idempotent — one per pair, either
    direction) so a cross-frame divergence (e.g. base reality vs. a fiction frame)
    is a graph traversal rather than a re-derivation (REVIEW_EPISTEMIC.md §8). Both
    facts stay active. variant_of is for facts in *different* frames; if the two
    share a frame, a same_frame note is returned so the agent can reconsider (a
    same-frame conflict is a contradiction, not a variant).
    """
    from epimemer.pipelines.reflection.review import same_frame

    if a_id == b_id:
        raise ValueError("A node cannot be a variant of itself")
    if await storage.get_node(a_id) is None:
        raise ValueError(f"Node '{a_id}' not found")
    if await storage.get_node(b_id) is None:
        raise ValueError(f"Node '{b_id}' not found")

    shares_frame = await same_frame(a_id, b_id, storage)
    edge_id, created = await _ensure_symmetric_edge(
        a_id, b_id, EdgeType.VARIANT_OF, storage, judge=judge
    )
    await _journal_pair_judgment(
        storage, DecisionKind.VARIANT, a_id, b_id, judge=judge, created=created,
    )

    # Only the same-frame case raises one: a cross-frame variant is the correct
    # use of the tool, and an advisory on it would be noise on the happy path.
    # Its own kind rather than the contradiction one, which it shared until the
    # two were found to give opposite advice: here the tool was the wrong one,
    # there the tool was right and the finding wants a person.
    advisories = [
        Advisory(
            kind=AdvisoryKind.SAME_FRAME_VARIANT,
            message=(
                "These facts share a metacontext frame; variant_of is meant for "
                "cross-frame divergence — if they conflict, record_contradiction "
                "fits."
            ),
            subjects=[a_id, b_id],
        )
    ] if shares_frame else []
    policy = await advisory_policy(
        storage, warning_policy if warning_policy is not None else WarningPolicy()
    )
    result = {
        "edge_id": edge_id, "created": created, "same_frame": shares_frame
    } | await carry_advisories(
        storage, policy, advisories, [a_id, b_id], judge=judge
    )
    meta = ResponseMeta(nodes_returned=2)
    return result, meta


async def merge_facts(
    source_ids: list[str],
    content: str,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    similarity_threshold: float = SIMILARITY_NOMINATION_THRESHOLD,
    judge: JudgeRef | None = None,
) -> tuple[dict, ResponseMeta]:
    """Collapse facts that restate one claim into a single node.

    The action the `redundant` verdict never had. Until this existed the verdict
    either no-opped or tempted the agent into a supersession whose required
    `because` has no honest answer — "same claim" is neither *it was wrong* nor
    *the world changed* (REVIEW_EPISTEMIC.md §3).

    **The plurality of provenance is the point, not a side-effect.** The survivor
    keeps one `sourced_from` edge per contributing document, carrying each
    source's own validity periods, because that is what makes per-source
    confidence and corroboration mean anything. Edge migration already collapses
    two edges to the *same* document into one while preserving both sets of
    periods, so nothing has to be reassembled here.

    **Every refusal comes back rather than raising**, with a reason: an agent
    told no has a real alternative available — record `SIMILARITY` and keep both
    — and refusing out loud is how it gets to choose it. See
    `fact_dedup.merge_refusal` for the rules and why they lean towards refusing.
    Ids that name nothing, or name something that is not a fact, do raise: those
    are malformed requests rather than judgments the graph declines.

    **`merge_cycle_limit` refuses an oscillation** (REVIEW_MODE.md §7.8): a fact
    already merged and un-merged this many times is refused with a message
    asking the caller to bring in the user, rather than a warning it would read
    and proceed past. Dormant until merge reversal exists, since nothing writes
    the `restored_at` the count reads — built alongside the episodes it counts,
    because a limit added after an oscillation has run has nothing to look at.

    **Inferences drawn on the sources are flagged `evidence_merged`**, not
    `evidence_stale`: their premise was reworded and better sourced, not
    overturned. The flag asks for a re-read against the survivor's wording, and
    it is where the merge records which phrasing went away — afterwards the
    `derived_from` edge points at the survivor and nothing else remembers.
    """
    from epimemer.pipelines.graph_construction.versioning import merge_nodes
    from epimemer.pipelines.reflection.fact_dedup import (
        merge_refusal,
        merged_confidence_basis,
    )

    sources: list[Fact] = []
    for source_id in source_ids:
        node = await storage.get_node(source_id)
        if node is None:
            raise ValueError(f"Node '{source_id}' not found")
        if not isinstance(node, Fact):
            raise ValueError(
                f"Node '{source_id}' is a {type(node).__name__.lower()}, and "
                f"only facts merge here — topics consolidate through reflect."
            )
        sources.append(node)

    refusal = await merge_refusal(
        sources,
        storage,
        model_id=embedding_provider.model_id,
        similarity_threshold=similarity_threshold,
        cycle_limit=resolve_merge_settings(
            await storage.get_merge_overrides()
        ).cycle_limit,
    )
    if refusal is not None:
        return (
            {"merged": False, "refused": refusal.reason, "source_ids": source_ids},
            # Declared even on a refusal: the ids are readable in the response,
            # and the rule is about what the agent can see rather than about
            # whether the call changed anything (RETRIEVAL_PROVENANCE §2).
            ResponseMeta(
                nodes_returned=len(sources), retrieved=_declare(source_ids)
            ),
        )

    basis = merged_confidence_basis(sources)
    merged = Fact(
        content=content,
        source_id=sources[0].source_id,
        # Every source cleared the gate, so all of them are states — and the
        # survivor has to say so, or the merge would leave behind a node that
        # can never merge again for want of the judgment its own parts carried.
        claim_kind=ClaimKind.STATE,
        value=merged_value_signal([source.value for source in sources]),
        extraction_method="agent:merge",
        judged_by=judge,
        metadata=(
            {"merged_from": source_ids}
            | ({"confidence_basis": basis} if basis else {})
        ),
    )
    await merge_nodes(list(sources), merged, storage, embedding_provider, judge=judge)
    # The survivor first, so a reversal looking for *the merge that made this
    # node* finds it by the id it holds.
    await journal(
        storage, DecisionKind.MERGE, [merged.id, *source_ids], judge=judge
    )

    result = {
        "merged": True,
        "fact_id": merged.id,
        "source_ids": source_ids,
        "sources_retired": len(sources),
    }
    meta = ResponseMeta(
        nodes_returned=1,
        source_types={"facts": 1},
        retrieved=_declare([merged.id, *source_ids]),
    )
    return result, meta


async def merge_inferences(
    source_ids: list[str],
    content: str,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    similarity_threshold: float = SIMILARITY_NOMINATION_THRESHOLD,
    judge: JudgeRef | None = None,
    warning_policy: WarningPolicy | None = None,
) -> tuple[dict, ResponseMeta]:
    """Collapse inferences that state one conclusion into a single node.

    The sibling of `merge_facts`, and the population it exists for is one that
    fact merges create: collapsing four near-identical facts onto one survivor
    migrates the four inferences drawn on them onto that survivor too, each
    carrying an `evidence_merged` flag naming the wording it lost. Four
    inferences hanging off one fact is the clearest case for merging them, and
    it did not exist until facts merged.

    **The survivor rests on the union of the sources' premises**, which is a
    combination neither original had. Usually that is two pieces of evidence for
    one conclusion. Where the premises are dated and provably fall clear of each
    other it is not, and that is reported as an advisory rather than a refusal:
    the honest answer to *these never held together* is often to narrow the
    merged wording or its period, which the agent does by writing content — so
    refusing would block a merge the agent could have fixed. Nomination carries
    the same advisory, so an agent that reached here from `reflect` has already
    seen it.

    **No `claim_kind` gate**, unlike facts, and that is a decision: `claim_kind`
    exists because interval union is mechanically right for a state and
    fabricating for an event. Whether combining premises is legitimate is not
    mechanical — the agent answers it in the text it writes, and a field stored
    at ingest would freeze what the merge itself decides.

    Refusals come back as `merged: false` with a reason, on `merge_facts`'
    grounds: an agent told no has a real alternative — record `SIMILARITY` and
    keep both — and refusing out loud is how it gets to choose it.
    """
    from epimemer.pipelines.graph_construction.versioning import merge_nodes
    from epimemer.pipelines.reflection.fact_dedup import merged_confidence_basis
    from epimemer.pipelines.reflection.inference_dedup import (
        merge_advisories,
        merge_refusal,
    )

    sources: list[Inference] = []
    for source_id in source_ids:
        node = await storage.get_node(source_id)
        if node is None:
            raise ValueError(f"Node '{source_id}' not found")
        if not isinstance(node, Inference):
            raise ValueError(
                f"Node '{source_id}' is a {type(node).__name__.lower()}, and "
                f"only inferences merge here — facts are `merge_facts`, topics "
                f"consolidate through reflect."
            )
        sources.append(node)

    refusal = await merge_refusal(
        sources,
        storage,
        model_id=embedding_provider.model_id,
        similarity_threshold=similarity_threshold,
        cycle_limit=resolve_merge_settings(
            await storage.get_merge_overrides()
        ).cycle_limit,
    )
    if refusal is not None:
        return (
            {"merged": False, "refused": refusal.reason, "source_ids": source_ids},
            ResponseMeta(
                nodes_returned=len(sources), retrieved=_declare(source_ids)
            ),
        )

    # Computed **before** the merge, because afterwards the sources' premises
    # have migrated onto the survivor and the two arguments are indistinguishable
    # from one. This is the same read the nomination made, repeated because a
    # caller can arrive here without one.
    advisories = await merge_advisories(sources, storage)
    policy = await advisory_policy(
        storage, warning_policy if warning_policy is not None else WarningPolicy()
    )

    # The basis travels with the confidence it explains. `merged_value_signal`
    # keeps the highest of the sources', and a rebuild that took the number
    # without its prose would leave a prior nobody can review — which is what
    # `merged_confidence_basis` exists to stop, on this path as on the fact one.
    basis = merged_confidence_basis(sources)
    merged = Inference(
        content=content,
        source_id=sources[0].source_id,
        value=merged_value_signal([source.value for source in sources]),
        extraction_method="agent:merge",
        judged_by=judge,
        metadata=(
            {"merged_from": source_ids}
            | ({"confidence_basis": basis} if basis else {})
        ),
    )
    await merge_nodes(list(sources), merged, storage, embedding_provider, judge=judge)
    # The survivor first, so a reversal looking for *the merge that made this
    # node* finds it by the id it holds.
    await journal(
        storage, DecisionKind.MERGE, [merged.id, *source_ids], judge=judge
    )

    result = {
        "merged": True,
        "inference_id": merged.id,
        "source_ids": source_ids,
        "sources_retired": len(sources),
    } | await carry_advisories(
        storage, policy, advisories, [merged.id, *source_ids], judge=judge
    )
    meta = ResponseMeta(
        nodes_returned=1,
        source_types={"inferences": 1},
        retrieved=_declare([merged.id, *source_ids]),
    )
    return result, meta


async def reverse_merge(
    survivor_id: str,
    storage: StorageBackend,
    *,
    judge: JudgeRef | None = None,
) -> tuple[dict, ResponseMeta]:
    """Undo a merge: restore the sources and destroy the survivor (§7 of
    `dev-docs/REVIEW_MODE.md`).

    **The one action in this system that destroys a node.** Everything else
    retires — a corrected claim, a historical one and a merged source all stay
    in the graph as history. A merge survivor is the exception, and narrowly:
    its content was written by an agent rather than drawn from a document, and
    every claim it carried goes back to the sources it came from, so nothing
    knowable is lost. That reasoning does not generalise, and no tool here
    deletes anything else.

    **Reversing returns the graph to the status it had before the merge.** The
    sources go back to active with their own edges — including one that
    collapsed when two of them cited the same document — and the `merged_into`
    and `evidence_merged` edges the merge wrote are removed. Reversing back and
    forth any number of times leaves the same active graph as doing it once. The
    only trace is in each source's `lifecycle`, which is append-only and records
    that it happened.

    **Refused, with a reason, when:** the fact was not made by a merge; the
    record of which source held which edge has aged past the graph's
    `merge_undo_depth` (permanent — the partition existed only at merge time);
    the survivor has since been merged again or retired; or **anything has been
    added to the survivor since the merge** — a contradiction, a tag, a
    similarity verdict, a relation. That last one matters: reversal deletes the
    node those edges point at, so a refusal is the only thing standing between a
    contested claim and the silent loss of its contest record.

    A later re-merge writes a new survivor from what is known then. The old
    wording is returned here as `survivor_content` and is not resurrected.
    """
    from epimemer.pipelines.graph_construction.versioning import (
        ReverseRefused,
        reverse_merge as _reverse_merge,
    )

    outcome = await _reverse_merge(survivor_id, storage, judge=judge)
    if isinstance(outcome, ReverseRefused):
        return (
            {"reversed": False, "refused": outcome.reason, "survivor_id": survivor_id},
            ResponseMeta(retrieved=_declare([survivor_id])),
        )

    # An overturn: it both **reviews** the merge and **supersedes** it, which is
    # the one case where the two fields are set together (§4). The row names the
    # survivor it destroyed — that id is now the only place the graph says the
    # node existed, and the wording comes back in `survivor_content`.
    merges = await prior_decisions(storage, DecisionKind.MERGE, [survivor_id])
    overturned = merges[0].id if merges else None
    await journal(
        storage,
        DecisionKind.REVERSAL,
        [survivor_id, *outcome["restored_ids"]],
        judge=judge,
        reviews=overturned,
        supersedes=overturned,
    )
    return outcome, ResponseMeta(
        nodes_returned=len(outcome["restored_ids"]),
        source_types={"facts": len(outcome["restored_ids"])},
        retrieved=_declare(outcome["restored_ids"]),
    )


async def configure_merge(
    storage: StorageBackend,
    *,
    undo_depth: int | None = None,
    cycle_limit: int | None = None,
    clear: bool = False,
) -> tuple[dict, ResponseMeta]:
    """Set the active graph's merge settings, or clear them back to the defaults.

    `undo_depth` bounds how far back along a merge lineage the graph keeps what
    a reversal needs. **Lowering it destroys reversal capability on the next
    merge and cannot be undone**, because the payload it drops existed only at
    merge time — which is the same reason the capture had to be built before
    anything read it.

    `cycle_limit` is how many times one fact may be merged and un-merged before
    the next merge refuses and asks for a human. Raising it is the escape hatch
    that refusal points at.

    `clear=True` returns both to the process defaults — deliberately the
    defaults *at the time*, not today's values frozen in, so a default changed
    later still reaches a graph that was once configured and then cleared.
    """
    if undo_depth is not None and undo_depth < 1:
        raise ValueError(
            f"undo_depth must be at least 1, got {undo_depth}: the merge being "
            f"made is level 1, so a lower bound would capture the partition and "
            f"discard it in the same call."
        )
    if cycle_limit is not None and cycle_limit < 1:
        raise ValueError(
            f"cycle_limit must be at least 1, got {cycle_limit}: zero refuses "
            f"every merge of a fact that has ever been un-merged, including the "
            f"first ordinary correction."
        )

    if clear:
        await storage.set_merge_overrides(MergeOverrides())
    elif undo_depth is not None or cycle_limit is not None:
        current = await storage.get_merge_overrides()
        await storage.set_merge_overrides(current.model_copy(update={
            field: value
            for field, value in (
                ("undo_depth", undo_depth), ("cycle_limit", cycle_limit),
            )
            if value is not None
        }))

    overrides = await storage.get_merge_overrides()
    settings = resolve_merge_settings(overrides)
    result = {
        "graph": storage.current_database,
        "merge_undo_depth": settings.undo_depth,
        "merge_cycle_limit": settings.cycle_limit,
        "overridden": overrides.model_dump(exclude_none=True),
    }
    return result, ResponseMeta()


async def configure_warnings(
    storage: StorageBackend,
    *,
    default_warning_policy: WarningPolicy | None = None,
    surface: bool | None = None,
    actions: dict[str, str] | None = None,
    clear: bool = False,
) -> tuple[dict, ResponseMeta]:
    """Read or change what this graph does about advisories.

    Called with nothing but the storage it reports what is in force, which is
    the graph's own answers laid over the process default.

    **`surface` governs surfacing only, never recording.** A graph with it off
    still journals every operation that went ahead against an objecting
    advisory, so `review(mode="advisory")` keeps answering *what was decided
    while nobody was looking* — which is exactly when that question is worth
    asking. Label it for what it does; it is not "turn off warnings".

    **And a kind explicitly named `flag` outranks it.** Naming a kind is the
    more specific statement, so muting a graph does not withdraw an escalation
    somebody asked for by name — which is also what stops `notify_user: true`
    arriving with no text to relay. Setting that kind to `proceed` is how the
    escalation is withdrawn.

    **`actions` is merged, not replaced.** A graph with an opinion about one
    kind has not withdrawn the defaults for the others, and a map override that
    silently dropped unnamed keys is the same class of bug as a field-by-field
    rebuild forgetting a field. `clear=True` is how the whole override goes
    away — back to the process default *at the time*, not today's values frozen
    in, so a default changed later still reaches a graph that was configured
    once and then cleared.
    """
    default = (
        default_warning_policy if default_warning_policy is not None
        else WarningPolicy()
    )
    kinds = sorted(kind.value for kind in AdvisoryKind)
    allowed = sorted(action.value for action in AdvisoryAction)

    parsed: dict[AdvisoryKind, AdvisoryAction] = {}
    for kind, action in (actions or {}).items():
        if kind not in kinds:
            raise ValueError(
                f"'{kind}' is not an advisory kind. Known kinds: "
                f"{', '.join(kinds)}."
            )
        if action not in allowed:
            raise ValueError(
                f"'{action}' is not an action for '{kind}'. Available: "
                f"{', '.join(allowed)}. 'reject' does not exist — an advisory "
                f"reaches the agent before it decides, so there is nothing here "
                f"that refuses on one."
            )
        parsed[AdvisoryKind(kind)] = AdvisoryAction(action)

    if clear:
        await storage.set_warning_overrides(WarningOverrides())
    elif surface is not None or parsed:
        current = await storage.get_warning_overrides()
        await storage.set_warning_overrides(current.model_copy(update={
            **({"surface": surface} if surface is not None else {}),
            **({"by_kind": {**current.by_kind, **parsed}} if parsed else {}),
        }))

    overrides = await storage.get_warning_overrides()
    policy = resolve_warning_policy(overrides, default)
    result = {
        "graph": storage.current_database,
        "surface": policy.surface,
        "actions": {
            kind: resolved_action(policy, AdvisoryKind(kind)).value for kind in kinds
        },
        # Which of those answers this graph gave, as opposed to inherited. A
        # kind set explicitly to the value it would have inherited anyway is not
        # the same as one that is following the default — the first stays put
        # when the default changes, the second tracks it.
        "overridden": {
            key: value
            for key, value in overrides.model_dump(
                mode="json", exclude_none=True
            ).items()
            if value != {}
        },
    }
    return result, ResponseMeta()


# --- Reflect (analysis — no LLM) ---


# The phases `reflect` reports to the visualization strip, in execution order.
# Named here so the topology and the calls below cannot drift apart.
REFLECT_PHASES = (
    "topic_consolidation",
    "split_detection",
    "enrichment_scan",
    "contradiction_detection",
    "recurrence_detection",
    "soundness_check",
    "inference_merge_nomination",
    "boundary_proposals",
    "pending_review",
    "archival_nomination",
    "relation_consolidation",
)


# Every nominee list built out of *pairs*. Pairs grow faster than the node set
# while every other list reflect returns is linear in it, so these are the only
# ones that can run away — and nothing bounded them: no limit parameter, no
# top-k, no size check anywhere on the path. Named here for the same reason as
# the phases above: a pair list added without this line would be unbounded again
# and nothing would say so.
#
# **`inference_merge_candidates` is here despite not being quadratic in the
# graph.** Its grouping bounds it by inferences resting on one premise rather
# than by inferences in total, which is a real and much lower bound — but the
# invariant *every pair-built list is capped* is simpler to hold than *capped
# except where a grouping argument says otherwise*, and the uncapped case grows
# in exactly the graphs this feature targets: a heavily merged graph is one that
# concentrates inferences onto surviving premises, and twenty-one on one premise
# already clears this cap. Capping fails benignly — the weakest candidates wait
# a pass — where not capping fails as the unbounded response the cap was
# measured for.
CAPPED_KEYS = (
    "similar_pairs",
    "contradictions",
    "recurrences",
    "similar_relations",
    "inference_merge_candidates",
)

# The most nominees any one of them returns.
#
# Chosen against the measured distribution rather than picked: real corpora
# yield 4 surviving fact pairs out of 38,226 at the 0.80 threshold, and the
# 0.0105% rate projects ~5,200 at 10,000 facts (`BENCHMARKS.md`). So this sits
# roughly three orders of magnitude above what a real graph returns today —
# insurance, not a working limit — and still well below a response no agent can
# read. `reflect(max_nominations=...)` raises it for a caller who means to.
MAX_NOMINATIONS = 200


def _capped(items: list, limit: int) -> tuple[list, bool]:
    """The first `limit` items, and whether anything was dropped.

    Callers hand this an already score-sorted list, so "first" is "highest
    scoring" — top-k rather than an arbitrary slice, which would offer the agent
    the weakest candidates as readily as the strongest.

    **How many were dropped is deliberately not returned.** A caller told there
    are 40,000 more pairs has no better move available than the one it already
    has: a graph that dense wants a different operation, not a longer list.
    """
    return items[:limit], len(items) > limit


async def reflect(
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    similarity_threshold: float = 0.85,
    relation_similarity_threshold: float = 0.9,
    max_nominations: int = MAX_NOMINATIONS,
    event_bus: InProcessEventBus | None = None,
) -> tuple[dict, ResponseMeta]:
    """Analyse the memory graph and return candidates for the agent to act on.

    Reads only. Returns split candidates, similar topic pairs, enrichment
    candidates, contradiction pairs, recurrences, temporally unsound inferences,
    inference-merge candidates, archival nominations and similar
    relationship-label pairs for the agent to review and act on via
    memory.apply_reflection — nothing here changes the graph.
    """
    from epimemer.pipelines.reflection.contradiction_detection import detect_contradictions
    from epimemer.pipelines.reflection.archival import nominate_archival_candidates
    from epimemer.pipelines.reflection.relation_consolidation import sweep_similar_relation_pairs
    from epimemer.pipelines.reflection.boundaries import propose_boundaries
    from epimemer.pipelines.reflection.inference_dedup import nominate_inference_merges
    from epimemer.pipelines.reflection.soundness import find_unsound_inferences
    from epimemer.pipelines.reflection.topic_consolidation import find_similar_topic_pairs
    from epimemer.pipelines.reflection.topic_enrichment import gather_associated_material_for, _should_enrich
    from epimemer.pipelines.reflection.topic_splitting import should_split
    from epimemer.pipelines.reflection.review import (
        frame_resolver,
        frames_for,
        gather_pending_review,
        same_frame,
    )
    from epimemer.visualization.phase_events import phase_pipeline

    model_id = embedding_provider.model_id

    # 2. Find similar topic pairs for consolidation
    async def _consolidation():
        pairs = await find_similar_topic_pairs(
            storage, embedding_provider,
            similarity_threshold=similarity_threshold,
            model_id=model_id,
        )
        return [
            {
                "topic_a": {"id": a.id, "content": a.content},
                "topic_b": {"id": b.id, "content": b.content},
                "similarity": round(score, 4),
            }
            for a, b, score in pairs
        ]

    # 3. Find split candidates (topics with high internal variance)
    async def _splits():
        candidates = []
        for topic in await _active_topics():
            material = await _material_for(topic)
            if len(material) < 4:
                continue
            material_vectors = await embedding_provider.embed(material)
            if should_split(material_vectors):
                candidates.append({
                    "topic_id": topic.id,
                    "topic_content": topic.content,
                    "material": material,
                })
        return candidates

    # 4. Find enrichment candidates (thin descriptions with rich material)
    async def _enrichment():
        candidates = []
        for topic in await _active_topics():
            material = await _material_for(topic)
            if _should_enrich(topic, material, material_ratio=3.0):
                candidates.append({
                    "topic_id": topic.id,
                    "current_content": topic.content,
                    "associated_material": material,
                })
        return candidates

    # Split detection and the enrichment scan walk the same topic set. Fetched
    # once and reused: a second full scan would add to exactly the N+1 cost
    # that makes reflect the slowest operation here. Lazy rather
    # than hoisted so the fetch stays attributed to the phase that needs it
    # first.
    topic_cache: list[Topic] = []

    async def _active_topics() -> list[Topic]:
        if not topic_cache:
            all_topics = await storage.query_nodes(node_type=NodeType.TOPIC)
            topic_cache.extend(t for t in all_topics if isinstance(t, Topic))
        return topic_cache

    # Both phases want the same material. Gathered for every topic in one go the
    # first time either asks, and scoped to this call so nothing goes stale.
    material_cache: dict[str, list[str]] = {}

    async def _material_for(topic: Topic) -> list[str]:
        if not material_cache:
            material_cache.update(
                await gather_associated_material_for(await _active_topics(), storage)
            )
        return material_cache.get(topic.id, [])

    # 5. Detect contradictions (safety net for anything ingest-time check missed).
    #    Similarity nominates; keep only same-frame pairs — a high-similarity pair
    #    across disjoint metacontext frames is coexistence, not a contradiction.
    async def _same_frame_pairs(raw):
        """Drop cross-frame pairs — coexistence, not conflict — and shape them.

        One resolver for the whole pass, warmed in a single query: candidate
        pairs are quadratic in facts while the facts themselves are not, so the
        set to load is known from `raw` before any pair is checked.
        """
        candidate_ids = list({fact.id for pair in raw for fact in pair[:2]})
        resolve_frames = frame_resolver(
            storage,
            seed=await frames_for(candidate_ids, storage) if candidate_ids else None,
        )
        found = []
        for a, b, score in raw:
            if not await same_frame(a.id, b.id, storage, resolve=resolve_frames):
                continue
            found.append({
                "fact_a": {"id": a.id, "content": a.content, "status": a.status.value},
                "fact_b": {"id": b.id, "content": b.content, "status": b.status.value},
                "similarity": round(score, 4),
            })
        return found

    # Scored once over the nominated set and partitioned twice. This phase is
    # the one that crosses the tool timeout as a graph grows, so widening
    # it to see historical facts must not also mean scoring the matrix twice —
    # the pairs are quadratic and the split is free.
    nominated_pairs: list = []

    async def _contradictions():
        nominated_pairs.extend(await detect_contradictions(
            storage, embedding_provider,
            similarity_threshold=SIMILARITY_NOMINATION_THRESHOLD,
            model_id=model_id,
            statuses=NOMINATED_STATUSES,
        ))
        return await _same_frame_pairs([
            pair for pair in nominated_pairs
            if pair[0].status is NodeStatus.ACTIVE
            and pair[1].status is NodeStatus.ACTIVE
        ])

    # 5b. Recurrence, the safety net's other half: a live claim that says what a
    #     retired-because-the-world-moved-on one said. Reported apart
    #     from the contradictions, because a claim beside its own successor is
    #     not a contradiction and filing it under that word is the misreading
    #     `recurs` exists to prevent. Only mixed pairs qualify: two active facts
    #     are redundancy, two historical ones are both past.
    async def _recurrences():
        return await _same_frame_pairs([
            pair for pair in nominated_pairs
            if {pair[0].status, pair[1].status}
            == {NodeStatus.ACTIVE, NodeStatus.HISTORICAL}
        ])

    # 5c. The temporal soundness check: an inference whose premises
    #     no source puts in the same period. The graph stores inferences it did
    #     not draw, so this is the only place the combination is ever looked at
    #     — and reflect rather than ingest because the motivating case spans two
    #     documents, neither of which is in front of the agent while the other
    #     is being stored. Flags, never blocks; never fires on unknown.
    async def _unsound():
        return [
            flagged.model_dump(mode="json")
            for flagged in await find_unsound_inferences(storage)
        ]

    # 5c-ii. Near-identical active inferences resting on a shared premise, each
    #     carrying its advisory. **Scoped to shared evidence rather than swept
    #     globally**: a fact merge collects duplicate inferences onto one
    #     survivor, which is the population worth reviewing and the only one
    #     that exists — a global sweep over all inference pairs was measured at
    #     zero nominations and is quadratic besides. Capped like every other
    #     pair-built list, though the grouping already bounds it by
    #     inferences-per-premise rather than by the graph; `CAPPED_KEYS` says
    #     why the lower bound was not treated as enough.
    async def _inference_merges():
        return [
            candidate.model_dump(mode="json")
            for candidate in await nominate_inference_merges(
                storage, embedding_provider, model_id=model_id
            )
        ]

    # 5d. Where a succession lets a period close. The other half of
    #     "ingest extracts, reflect proposes": a document cannot know its claim
    #     will ever stop being true, so only something seeing the next document
    #     can close the first interval. Proposes, never writes — the boundary is
    #     `inferred`, and `apply_reflection(boundaries=[...])` is what applies it.
    async def _boundaries():
        return [
            proposal.model_dump(mode="json")
            for proposal in await propose_boundaries(storage)
        ]

    # 6. Surface the pending-review worklist: active nodes already carrying review
    #    state (a candidate to supersede, stale evidence, or an unresolved
    #    contest), with the related ids to act on via apply_reflection /
    #    supersede_by / record_variant.
    async def _pending_review():
        return [
            {
                "node": {"id": n.id, "content": n.content, "node_type": _node_type_key(n)},
                "review": labels,
            }
            for n, labels in await gather_pending_review(storage)
        ]

    # 7. Nominate archival candidates — the hygiene arm of the same loop, and a
    #    worklist in the same shape as pending_review. Mechanical: no LLM, no
    #    embeddings. The agent judges, the human approves, and
    #    apply_reflection(archivals=[...]) applies.
    async def _archival():
        return [
            c.model_dump(mode="json")
            for c in await nominate_archival_candidates(storage)
        ]

    # 8. Find likely-synonymous user relationship labels (open vocabulary captured
    #    fast, organized slow). Judged via apply_reflection relation_verdicts,
    #    which is the whole destination now that `relation_merges` is gone: a
    #    nomination is answered `distinct` or `synonymous` and stops coming back
    #, and nothing rewrites an edge. The sweep also counts what
    #    standing verdicts held back, because suppression is silent: without the
    #    count, an empty list on a well-judged graph reads as *nothing similar
    #    here* rather than *already judged*.
    relation_pairs_suppressed = 0

    async def _relations():
        nonlocal relation_pairs_suppressed
        sweep = await sweep_similar_relation_pairs(
            storage, embedding_provider,
            similarity_threshold=relation_similarity_threshold,
        )
        relation_pairs_suppressed = sweep.suppressed
        return sweep.pairs

    # Reflect is the longest operation in the system and the one users most want
    # to watch, but it is plain functions rather than a Petri net — so it
    # declares a synthetic linear topology and fires it by hand. Without a bus
    # `phase` is a bare await, so watching cannot change what is computed.
    async with phase_pipeline(event_bus, "reflect", REFLECT_PHASES) as phase:
        similar_pairs = await phase("topic_consolidation", _consolidation, tokens=len)
        split_candidates = await phase("split_detection", _splits, tokens=len)
        enrichment_candidates = await phase("enrichment_scan", _enrichment, tokens=len)
        contradictions = await phase(
            "contradiction_detection", _contradictions, tokens=len
        )
        recurrences = await phase("recurrence_detection", _recurrences, tokens=len)
        unsound_inferences = await phase("soundness_check", _unsound, tokens=len)
        inference_merge_candidates = await phase(
            "inference_merge_nomination", _inference_merges, tokens=len
        )
        boundary_proposals = await phase(
            "boundary_proposals", _boundaries, tokens=len
        )
        pending_review = await phase("pending_review", _pending_review, tokens=len)
        archival_candidates = await phase(
            "archival_nomination", _archival, tokens=len
        )
        similar_relations = await phase(
            "relation_consolidation", _relations, tokens=len
        )

    result = {
        "similar_pairs": similar_pairs,
        "split_candidates": split_candidates,
        "enrichment_candidates": enrichment_candidates,
        "contradictions": contradictions,
        "recurrences": recurrences,
        "unsound_inferences": unsound_inferences,
        "inference_merge_candidates": inference_merge_candidates,
        "boundary_proposals": boundary_proposals,
        "pending_review": pending_review,
        "archival_candidates": archival_candidates,
        "similar_relations": similar_relations,
    }

    # Cap the quadratic lists, and say which ones were cut. Applied here,
    # in one place over `CAPPED_KEYS`, rather than inside each phase: the phase
    # events above keep reporting what the pass actually found, which is what
    # makes a truncated response legible in the strip rather than invisible.
    #
    # Each list is capped *after* the contradiction/recurrence partition above,
    # never before — one scored set feeds both, and a cap applied to the set
    # would let the larger half starve the other.
    #
    # **This bounds the response, not the peak allocation.** The scored tuples
    # in `similar_pairs` are still one per surviving pair; what goes away is the
    # response dicts and the unbounded JSON. That is the honest scope: the
    # measurement that demoted the cap from urgent also moved its argument from
    # memory to the response.
    truncated: list[str] = []
    for key in CAPPED_KEYS:
        result[key], was_cut = _capped(result[key], max_nominations)
        if was_cut:
            truncated.append(key)

    # Summed while `result` still holds nothing but nominee lists, so the count
    # cannot be thrown off by a key that is not one — and so a phase added later
    # is counted without anyone remembering to add a term, which the hand-written
    # sum this replaced would have needed.
    nominees_returned = sum(len(value) for value in result.values())
    result["truncated"] = truncated
    # A count, not a nominee list, so it joins `truncated` on this side of the
    # sum. Like `truncated` it is metadata about what the lists above do not
    # show: label pairs standing relation verdicts kept out of
    # `similar_relations` this pass.
    result["relation_pairs_suppressed"] = relation_pairs_suppressed

    meta = ResponseMeta(
        nodes_returned=nominees_returned,
        # Reflect **scans** the whole active graph and the agent sees only the
        # nominees, so a reflect record dims everything except them. That is
        # accurate rather than a special case: `retrieved` is what the response
        # carried, never what the tool looked at (§2, corrected).
        retrieved=_declare(_ids_within(result)),
    )
    return result, meta


# --- Apply Reflection (stores agent decisions) ---


async def apply_reflection(
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    parents: list[dict] | None = None,
    splits: list[dict] | None = None,
    enrichments: list[dict] | None = None,
    merges: list[dict] | None = None,
    supersessions: list[dict] | None = None,
    archivals: list[str] | None = None,
    judgments: list[dict] | None = None,
    relation_verdicts: list[dict] | None = None,
    boundaries: list[dict] | None = None,
    similarities: list[dict] | None = None,
    merge_similarity_threshold: float = 0.92,
    judge: JudgeRef | None = None,
) -> tuple[dict, ResponseMeta]:
    """Apply agent-provided reflection decisions to the graph.

    similarities: [{pair: [a_id, b_id], verdict: "one_claim" | "distinct",
        because: str}] — what you decided about a nominated pair you are not
        otherwise acting on. ``one_claim`` writes ``similarity`` **and**
        ``assessed``; ``distinct`` writes ``assessed`` only, and the split is
        the point: ``similarity`` is what corroboration counts, so recording
        *"these are different claims"* as one would manufacture support. Both
        stop the pair being nominated again. ``because`` is required. Anything
        not recorded comes back in ``similarities_refused`` with a reason.
        ``distinct`` over a pair you earlier called ``one_claim`` **withdraws**
        that verdict: the pair stops corroborating, and the count comes back to
        what it would have been. A withdrawal is final — nothing re-asserts
        ``one_claim`` afterwards, because getting that wrong invents agreement
        rather than losing it.
    parents: [{children_ids: [str], content: str}] — synthesized parent topics
    splits: [{topic_id: str, subtopics: [str]}] — split a broad topic
    enrichments: [{topic_id: str, new_content: str}] — improved descriptions
    merges: [{source_ids: [str], content: str}] — fuse near-duplicate topics
        into one combined topic (sources retained as MERGED history). Each merge
        is applied only if *every* pair of sources clears
        merge_similarity_threshold; otherwise it is rejected. This is the one
        consolidation that retires nodes from the active graph, so the bar is
        high by design — use parents for merely related (not duplicate) topics.
        (Sources, tags, and entities are also Topics, so they consolidate here.)
    supersessions: [{old_id: str, by_id: str, because: str}] — resolve a
        flagged/contested node (from reflect's pending_review) by superseding
        ``old_id`` with an existing node ``by_id``. Atomic: marks old superseded
        (lineage old → by), flags inferences that depended on old as
        evidence_stale, and clears any supersession candidacy on it. The winner
        is unchanged; no new node. ``because`` is required and is a judgment —
        ``"it_was_wrong"`` or ``"the_world_changed"``; if you cannot tell which
        happened, leave the pair contested rather than guessing (see `update`).
    archivals: [node_id] — archive the approved nodes from reflect's
        archival_candidates. Exports each node with its edges (returned as
        ``archive_data`` — keep it; that copy is the archive) and atomically
        flips them to ARCHIVED, which removes them from every active-status
        query. Nothing is deleted, and ``restore`` reverses it. Unknown or
        already-retired ids are skipped, as supersessions are.
    judgments: [{node_id: str, direction: "up"|"down", reason: str,
        related_id: str | None}] — re-judge a node's importance, typically a
        `stale_judgment` nominee from reflect's archival_candidates. The verdict
        that has no other expression: "keep it, and stop treating it as
        important" — or, just as often, "still important, and now recently
        confirmed". Either way the node leaves the stale set, because the clock
        moves whichever direction the judgment goes. Unknown ids are skipped, as
        supersessions and archivals are.
    relation_verdicts: [{pair: [label_a, label_b], kind: str, verdict:
        "distinct" | "synonymous", because: str}] — what you decided about a
        nominated **label** pair. `similarities` one tier down, and the fix for
        the same defect: a declined pair was recorded nowhere, so `reflect`
        re-offered it for ever. ``distinct`` is different relationships that
        look alike; ``synonymous`` is one relationship written two ways.
        **Neither rewrites anything** — a verdict is a record, which is the
        whole of what this tier does since `relation_merges` was removed, and
        what a label means here is settled with ``describe_relation``. **Both
        stop the pair being nominated, and that suppression is permanent by
        design.** ``because`` is required, and
        so is ``kind`` — copy it from the nomination; there is no default, so
        an entry omitting it is refused rather than judged against a kind the
        agent never stated.
        Entries that could not be recorded come back in
        ``relation_verdicts_refused`` with a reason. A label with no record gets
        one, carrying no judge; a pair already carrying **your** identical
        verdict is refused as a retry, and another agent's is recorded as a
        confirmation rather than a second verdict.
    boundaries: [{node_id, source_id, endpoint, at, timeline_id?}] — accept a
        boundary reflect proposed, filling in one open endpoint of
        one source's period. The written interval's basis becomes ``inferred``:
        the date came from another document read against this one, and leaving
        it ``stated`` would have this source appear to assert something it never
        said. Each is re-derived from the graph as it stands and **refused**
        rather than guessed at when the request no longer names exactly one open
        period — refusals come back in ``boundaries_refused`` with a reason,
        since a boundary silently not applied is worse than one rejected out
        loud.

    **A malformed entry refuses the whole call, and nothing is written.** The
    steps below share no transaction and their order is load-bearing, so an
    entry missing a required key used to abort part-way and report a total
    failure over a partial write. Every entry is now checked first, and
    every problem is listed at once. This is structure only — a judgment the
    graph can evaluate is still refused on its own, into the matching
    ``*_refused`` list, so one already-judged pair never costs a batch.
    """
    from epimemer.pipelines.graph_construction.versioning import (
        merge_nodes,
        plan_subtopic_edges,
        supersede_by_existing,
        supersede_node,
    )
    from epimemer.pipelines.frames import frame_edges, shared_frame_set
    from epimemer.pipelines.reflection.batch_validation import (
        malformed_entries,
        refusal_message,
    )
    from epimemer.pipelines.reflection.boundaries import apply_boundary
    from epimemer.pipelines.reflection.relation_verdicts import (
        RelationVerdictRefused,
        apply_relation_verdict,
    )
    from epimemer.pipelines.reflection.similarity_decisions import (
        SimilarityRefused,
        apply_similarity_decision,
    )
    from epimemer.pipelines.reflection.review import frames_of
    from epimemer.pipelines.reflection.topic_consolidation import all_pairs_above_threshold

    # 0. Nothing is applied until the whole batch is known to be applicable.
    #    The nine steps below share no transaction and their order is the
    #    anchoring rule, so a raise part-way down leaves everything above it
    #    committed under an error that cannot say what landed — and a similarity
    #    verdict, being permanently suppressing, then refuses the retry.
    #    Structural only: judgments the graph can evaluate are still refused one
    #    at a time, into the `*_refused` lists.
    malformed = malformed_entries({
        "similarities": similarities,
        "relation_verdicts": relation_verdicts,
        "parents": parents,
        "splits": splits,
        "enrichments": enrichments,
        "merges": merges,
        "supersessions": supersessions,
        "archivals": archivals,
        "judgments": judgments,
        "boundaries": boundaries,
    })
    if malformed:
        raise ValueError(refusal_message(malformed))

    parents_created = 0
    # Refusals rather than counts, because a frame mismatch is something the
    # agent has to act on — `reframe` the odd one out, or synthesise within a
    # frame — and a bare number says neither which group nor why.
    parents_refused: list[dict] = []
    topic_merges_refused: list[dict] = []
    topics_split = 0
    topics_enriched = 0
    topics_merged = 0
    merges_rejected = 0
    supersessions_applied = 0
    model_id = embedding_provider.model_id

    # 1. Record what was decided about nominated pairs. **First**, and that is
    #    the anchoring rule applied to a batch: a judgment is about the
    #    wording it was made against, and steps 4-7 below can retire a node
    #    named here. Recording afterwards would either lose the judgment to a
    #    skip or attach it to a replacement nobody assessed.
    similarities_recorded = 0
    similarity_edges_written = 0
    similarities_retracted = 0
    similarities_refused: list[dict] = []
    for spec in (similarities or []):
        pair = spec["pair"]
        outcome = await apply_similarity_decision(
            storage,
            a_id=pair[0],
            b_id=pair[1],
            verdict=spec["verdict"],
            because=spec.get("because", ""),
            judge=judge,
        )
        if isinstance(outcome, SimilarityRefused):
            similarities_refused.append(outcome.model_dump(mode="json"))
        else:
            similarities_recorded += 1
            similarity_edges_written += outcome.edges_created
            if outcome.retracted:
                similarities_retracted += 1
                # A withdrawal cites what it withdrew, the way a merge reversal
                # does: `reviews` says somebody checked that decision,
                # `supersedes` says it no longer stands. Where the original
                # predates the journal there is nothing to cite, and the row
                # goes in unlinked rather than pointing at an invented target.
                prior = await prior_decisions(
                    storage, DecisionKind.SIMILARITY, [pair[0], pair[1]]
                )
                original = prior[-1].id if prior else None
                await journal(
                    storage, DecisionKind.RETRACTION, [pair[0], pair[1]],
                    judge=judge, reviews=original, supersedes=original,
                )
            else:
                await journal(
                    storage, DecisionKind.SIMILARITY, [pair[0], pair[1]], judge=judge
                )

    # 1b. Record what was decided about nominated **label** pairs, and before
    #     step 9 relabels any of them. A verdict is about the vocabulary as the
    #     agent saw it, so a merge earlier in the same batch would make one side
    #     of a pair vanish and the verdict would land on a label the agent never
    #     judged — step 1's anchoring rule, applied to the tier below it.
    relation_verdicts_recorded = 0
    relation_verdicts_confirmed = 0
    relation_verdicts_refused: list[dict] = []
    for verdict_spec in (relation_verdicts or []):
        verdict_pair = verdict_spec["pair"]
        # Absent `kind` refuses, where absent `because` already did. A default
        # would state 'relationship' on behalf of an agent who stated nothing,
        # and the stale-kind refusal downstream would then blame them for a
        # claim this call invented — an attribution pair refused for naming a
        # kind the agent never named.
        if "kind" not in verdict_spec:
            relation_verdicts_refused.append(RelationVerdictRefused(
                pair=list(verdict_pair),
                reason=(
                    "`kind` is required: copy it from the nomination. There is "
                    "no default — a kind filled in here would be judged on "
                    "behalf of an agent who stated none, and an attribution "
                    "pair would be refused as stale for a claim it never made."
                ),
            ).model_dump(mode="json"))
            continue
        verdict_outcome = await apply_relation_verdict(
            storage,
            label_a=verdict_pair[0],
            label_b=verdict_pair[1],
            kind=verdict_spec["kind"],
            verdict=verdict_spec.get("verdict", ""),
            because=verdict_spec.get("because", ""),
            judge=judge,
        )
        if isinstance(verdict_outcome, RelationVerdictRefused):
            relation_verdicts_refused.append(verdict_outcome.model_dump(mode="json"))
            continue
        if verdict_outcome.created:
            relation_verdicts_recorded += 1
        else:
            relation_verdicts_confirmed += 1
        # The journal row names the two **label record ids**, which is where
        # the unanswerable question resolves: the subject of a decision about
        # a relation finally has an identity that `review()` dereferences like
        # any other. A second agent agreeing writes a confirmation citing the
        # original, exactly as it does for a contradiction or a variant.
        await _journal_pair_judgment(
            storage,
            DecisionKind.RELATION_VERDICT,
            verdict_outcome.label_ids[0],
            verdict_outcome.label_ids[1],
            judge=judge,
            created=verdict_outcome.created,
        )

    # 2. Create parent topics for similar groups
    for parent_spec in (parents or []):
        children_ids: list[str] = parent_spec["children_ids"]
        content: str = parent_spec["content"]

        children: list[EpistemicNode] = []
        for cid in children_ids:
            node = await storage.get_node(cid)
            if node is not None:
                children.append(node)

        if len(children) < 2:
            continue

        # The synthesised parent is a **new assertion**, so it has to say which
        # world it is about like any other write — and the only frame it can
        # honestly claim is the one its children already agree on. Inheriting a
        # union instead would let a topic drawn from a fiction claim and a real
        # one assert in both, which `fact_dedup` calls the worst outcome
        # available; refusing is that gate, one tier up.
        inherited = await shared_frame_set(
            [child.id for child in children], storage
        )
        if inherited is None:
            parents_refused.append({
                "children_ids": children_ids,
                "reason": (
                    "these topics do not stand in exactly the same set of "
                    "frames, and a parent synthesised from them would assert "
                    "in one world what was only ever claimed in another. "
                    "Synthesise within a frame, or `reframe` the odd one out "
                    "if its framing is what is wrong."
                ),
            })
            continue

        parent_topic = Topic(
            content=content,
            source_id=children[0].source_id,
            extraction_method="agent:parent_synthesis",
            judged_by=judge,
            metadata={"synthesized_from": children_ids},
        )
        edges = [
            *await plan_subtopic_edges(children, parent_topic.id, storage),
            *frame_edges(parent_topic.id, inherited, judge=judge),
        ]
        vectors = await embedding_provider.embed([parent_topic.content])
        await storage.write_batch_tx(
            nodes=[parent_topic],
            edges=edges,
            embeddings=[EmbeddingRecord(
                item_id=parent_topic.id, model_id=model_id, vector=vectors[0]
            )],
        )
        await journal(
            storage, DecisionKind.SYNTHESIS,
            [parent_topic.id, *children_ids], judge=judge,
        )
        parents_created += 1

    # 3. Split broad topics into subtopics
    for split_spec in (splits or []):
        topic_id: str = split_spec["topic_id"]
        subtopic_contents: list[str] = split_spec["subtopics"]

        parent = await storage.get_node(topic_id)
        if parent is None or not isinstance(parent, Topic):
            continue

        subtopics = [
            Topic(
                content=sc, source_id=parent.source_id, extraction_method="agent:split",
                judged_by=judge, metadata={"split_from": topic_id},
            )
            for sc in subtopic_contents
        ]
        # Same content, refined — so a subtopic stands exactly where its parent
        # did. A parent that states nothing passes on nothing: inventing a frame
        # here would put words in a nobody's mouth, which is the declaration
        # sweep's job and a person's call.
        inherited = await frames_of(parent.id, storage)
        edges = [
            *await plan_subtopic_edges(subtopics, parent.id, storage),
            *[
                edge
                for st in subtopics
                for edge in frame_edges(st.id, inherited, judge=judge)
            ],
        ]
        vectors = await embedding_provider.embed([st.content for st in subtopics])
        embeddings = [
            EmbeddingRecord(item_id=st.id, model_id=model_id, vector=vec)
            for st, vec in zip(subtopics, vectors)
        ]
        await storage.write_batch_tx(
            nodes=subtopics, edges=edges, embeddings=embeddings,
        )
        await journal(
            storage, DecisionKind.SPLIT,
            [topic_id, *(st.id for st in subtopics)], judge=judge,
        )
        topics_split += 1

    # 4. Enrich topic descriptions
    for enrich_spec in (enrichments or []):
        topic_id = enrich_spec["topic_id"]
        new_content: str = enrich_spec["new_content"]

        old_topic = await storage.get_node(topic_id)
        if old_topic is None or not isinstance(old_topic, Topic):
            continue

        enriched = Topic(
            content=new_content,
            source_id=old_topic.source_id,
            value=old_topic.value,
            extraction_method=f"{old_topic.extraction_method}:enriched",
            judged_by=judge,
            metadata={**old_topic.metadata, "enriched_from": topic_id},
        )
        # supersede_node embeds the replacement and migrates edges.
        # Enrichment rewrites a topic's own description; the earlier wording
        # was never true-of-a-period, so this is a correction.
        await supersede_node(
            old_topic, enriched, storage, embedding_provider,
            status=NodeStatus.CORRECTED, judge=judge,
        )
        await journal(
            storage, DecisionKind.ENRICHMENT, [topic_id, enriched.id], judge=judge
        )
        topics_enriched += 1

    # 5. Merge near-duplicate topics into one (guarded by a high similarity bar)
    for merge_spec in (merges or []):
        source_ids: list[str] = merge_spec["source_ids"]
        content = merge_spec["content"]

        sources: list[EpistemicNode] = []
        for sid in source_ids:
            node = await storage.get_node(sid)
            if isinstance(node, Topic):
                sources.append(node)

        if len(sources) < 2:
            continue

        # Only collapse genuine duplicates: every pair must clear the bar, or
        # the merge is refused (distinct-but-related topics are left untouched).
        if not await all_pairs_above_threshold(
            sources, storage, model_id, merge_similarity_threshold
        ):
            merges_rejected += 1
            continue

        # The gate facts have had since dedup, arriving late here because topic
        # merge grew its own path: `merge_nodes` migrates every source's edges
        # onto the survivor, `has_metacontext` among them, so merging across
        # frames leaves one topic asserted in both worlds. Exact set equality,
        # not overlap — `shared_frame_set` carries the reasoning.
        if await shared_frame_set(source_ids, storage) is None:
            topic_merges_refused.append({
                "source_ids": source_ids,
                "reason": (
                    "these topics do not stand in exactly the same set of "
                    "frames, and the survivor would inherit the union of them "
                    "— asserting in one world what was only ever claimed in "
                    "another. Merge within a frame."
                ),
            })
            continue

        # Combined in one shared place: a field-by-field rebuild here silently
        # reset both value clocks, leaving merged nodes permanently exempt from
        # archival nomination.
        merged_value = merged_value_signal([s.value for s in sources])
        merged_topic = Topic(
            content=content,
            source_id=sources[0].source_id,
            value=merged_value,
            extraction_method="agent:merge",
            judged_by=judge,
            metadata={"merged_from": source_ids},
        )
        await merge_nodes(
            sources, merged_topic, storage, embedding_provider, judge=judge
        )
        await journal(
            storage, DecisionKind.MERGE,
            [merged_topic.id, *source_ids], judge=judge,
        )
        topics_merged += 1

    # 6. Resolve flagged/contested nodes by superseding the loser with an existing
    #    winner (the resolution action of the review loop). Missing or self-pairs
    #    are skipped rather than raised so a batch partially applies cleanly.
    for supersede_spec in (supersessions or []):
        old_id = supersede_spec["old_id"]
        by_id = supersede_spec["by_id"]
        if old_id == by_id:
            continue
        old_node = await storage.get_node(old_id)
        if old_node is None or await storage.get_node(by_id) is None:
            continue
        status = superseded_status_for(supersede_spec["because"])
        await supersede_by_existing(
            old_node, by_id, storage, status=status, judge=judge,
        )
        await journal(
            storage, supersession_kind(status), [old_id, by_id], judge=judge
        )
        supersessions_applied += 1

    # 7. Archive the approved trivial nodes: export first, then one atomic flip.
    #    Ordering matters — the export is the archive, so it must be taken
    #    before anything about the nodes changes.
    to_archive: list[EpistemicNode] = []
    for node_id in (archivals or []):
        node = await storage.get_node(node_id)
        if node is None or node.status is not NodeStatus.ACTIVE:
            continue
        to_archive.append(node)

    archive_data: dict = {"nodes": [], "edges": []}
    if to_archive:
        from epimemer.pipelines.reflection.archival import archive_nodes

        archive_data = await archive_nodes(to_archive, storage)
        await storage.set_node_status_tx(
            to_archive,
            status=NodeStatus.ARCHIVED,
            at=datetime.now(timezone.utc),
            judge=judge,
        )
        # One row for the sweep. Approving a batch of trivial nodes is a single
        # pass over a single nomination list, not twelve independent verdicts.
        await journal(
            storage, DecisionKind.ARCHIVAL,
            [node.id for node in to_archive], judge=judge,
        )

    # 8. Re-judge importance. Separate from archivals on purpose: archiving is a
    #    status verdict wanting human approval, while a change of degree is
    #    something the agent may conclude on its own. `judge_importance` writes
    #    its own journal row, so there is none here — one act, one row, whether
    #    it was reached through this batch or called directly.
    judgments_applied = 0
    for spec in (judgments or []):
        try:
            await judge_importance(
                spec["node_id"],
                direction=spec["direction"],
                reason=spec["reason"],
                storage=storage,
                related_id=spec.get("related_id"),
                judge=judge,
            )
        except ValueError:
            continue        # unknown node or related id — skipped, as above
        judgments_applied += 1

    # 9. Accept boundaries reflect proposed. Last because it is the only step
    #    that edits an existing assertion rather than adding one, so anything
    #    that moves a node's status above has already happened.
    boundaries_applied = 0
    boundaries_refused: list[dict] = []
    for spec in (boundaries or []):
        refusal = await apply_boundary(
            storage,
            node_id=spec["node_id"],
            source_id=spec["source_id"],
            endpoint=spec["endpoint"],
            at=spec["at"] if isinstance(spec["at"], datetime)
            else datetime.fromisoformat(spec["at"]),
            timeline_id=spec.get("timeline_id"),
        )
        if refusal is None:
            boundaries_applied += 1
            # The gap `ATTRIBUTION.md` named: accepting a boundary edits an
            # existing `sourced_from` edge, so stamping it inline would take the
            # edge from whoever ingested it. The journal is where a judgment
            # about someone else's row belongs, and both of its subjects are
            # nodes — the claim, and the source whose period was closed.
            await journal(
                storage, DecisionKind.BOUNDARY,
                [spec["node_id"], spec["source_id"]], judge=judge,
            )
        else:
            boundaries_refused.append(refusal.model_dump(mode="json"))

    result = {
        "similarities_recorded": similarities_recorded,
        "similarity_edges_written": similarity_edges_written,
        "similarities_retracted": similarities_retracted,
        "similarities_refused": similarities_refused,
        "parents_created": parents_created,
        "parents_refused": parents_refused,
        "topics_split": topics_split,
        "topics_enriched": topics_enriched,
        "topics_merged": topics_merged,
        "merges_rejected": merges_rejected,
        "topic_merges_refused": topic_merges_refused,
        "supersessions_applied": supersessions_applied,
        "nodes_archived": len(to_archive),
        "archive_data": archive_data,
        "judgments_applied": judgments_applied,
        "relation_verdicts_recorded": relation_verdicts_recorded,
        "relation_verdicts_confirmed": relation_verdicts_confirmed,
        "relation_verdicts_refused": relation_verdicts_refused,
        "boundaries_applied": boundaries_applied,
        "boundaries_refused": boundaries_refused,
    }
    meta = ResponseMeta(
        nodes_returned=(
            similarities_recorded + parents_created + topics_split + topics_enriched
            + topics_merged + supersessions_applied + len(to_archive)
            + judgments_applied + boundaries_applied
            + relation_verdicts_recorded
        ),
    )
    return result, meta


# --- Review (reading the journal back, REVIEW_MODE.md §6) ---

# One page of decisions, and the cap is the nomination cap applied verbatim: `all`
# over an append-only journal fed by every ingest is precisely the unbounded
# response the reflect nominee lists are capped for, and designing this one
# uncapped afterwards would be perverse. As there — when the response says it was cut,
# act on what came back and review again rather than raising the number.
REVIEW_MAX_RESULTS: int = 200


def _review_subject(
    subject_id: str,
    nodes: dict[str, EpistemicNode],
    labels: dict[str, RelationLabel],
) -> dict:
    """One subject of one journalled decision, from whichever table holds it.

    Two tables and not one because a decision's subject is not always a claim:
    a relation verdict and a relation description are judgments about the
    graph's **words**, and their subjects are `RelationLabel` records
    (`RELATION_LABELS.md` §4.3). That is where the question resolves — it had no
    clean answer while
    a label had no id, and the alternatives were a second namespace inside
    `subject_ids` or the endpoint nodes of edges the decision was not about.

    A label carries no status, so `status` stays null for one — the field
    describes a node's place in the active graph and a vocabulary entry has
    none.
    """
    node = nodes.get(subject_id)
    if node is not None:
        return {
            "id": subject_id,
            "subject_kind": "node",
            "content_preview": _content_preview(node)["content_preview"],
            "status": node.status.value,
        }
    label = labels.get(subject_id)
    if label is not None:
        return {
            "id": subject_id,
            "subject_kind": "relation_label",
            "content_preview": f"{label.name} ({label.kind})",
            "status": None,
        }
    return {
        "id": subject_id,
        "subject_kind": None,
        "content_preview": None,
        "status": None,
    }


async def review(
    storage: StorageBackend,
    *,
    mode: str = "all",
    agent_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    certainty_ceiling: float | None = None,
    max_results: int = REVIEW_MAX_RESULTS,
) -> tuple[dict, ResponseMeta]:
    """Read the decision journal back, shakiest first (§6).

    Read-only, like `reflect`, and for the same reason: it nominates, and every
    change goes through `apply_review`, `rejudge` and the decision tools that
    already exist.

    **Three separate questions, and the first draft ran them together.** *Which*
    decisions (`mode`, `agent_id`, `since`/`until`), *what order* they arrive in
    (always the same — see below), and *whether* the list is narrowed further
    (`certainty_ceiling`). A mode names the selection; every argument narrows
    whatever it selected, which is how §6.1's *"`by_agent` **and** `since`"*
    composes without a second vocabulary.

    **Ordering is two tiers that never mix** (§6.2, `pipelines/review/`). A
    declared `certainty` comes first, ascending; everything unrated follows,
    ordered by how many derived difficulty signals it carries. An unrated
    decision never outranks one an agent flagged: absence is not a claim of
    doubt.

    **Nobody wants only the doubtful ones.** A reviewer checking a session's
    work wants all of it, ordered so the doubtful calls are at the top and they
    can stop reading when it stops repaying the attention. That is why this is
    an ordering rather than a filter, and why the cap is benign: a cut list
    loses the end nobody was going to read.

    **The answer is one graph wide, and says which**. The journal is per
    graph like every other table, because `subject_ids` are node ids and a node
    id resolves only where it lives. Covering more is `list_graphs`,
    `use_graph`, ask again — and that sequence is *safer* than a fan-out would
    be, since each switch is the active graph rather than one borrowed
    mid-call.

    **`elsewhere` says where else to look, and nothing more**. Counts per
    graph, no rows, no ids — the reviewer who needs this is a *later, different*
    agent, which is the reviewer the registry exists for; the one that made the
    decisions switched the graphs itself and never needed telling. It counts
    with `agent_id`, `since` and `until` only, so a count can be **wider** than
    what a review there would list and is never narrower: too high sends someone
    to look and find less, too low leaves them not looking at all.
    """
    refusal = mode_refusal(mode, agent_id=agent_id, since_given=since is not None)
    if refusal is not None:
        return {"refused": refusal, "modes": list(REVIEW_MODES)}, ResponseMeta()

    # One scan, narrowed by whatever the caller supplied. `unreviewed` is the
    # only mode that is not a field filter, so it is applied below against a
    # reviewed-set covering the **whole** selection rather than the page: a
    # reviewed-set built from the page would call every row on page two
    # unreviewed.
    # One handle in, a set of ids out: a judge that has absorbed another
    # record answers under both, and nothing was rewritten to make that so.
    # `judge` is None where no judge was named, which is every mode but
    # `by_agent`.
    judge = (
        resolve_agent(await storage.list_agents(), agent_id)
        if agent_id is not None else None
    )
    judge_ids = (
        None if agent_id is None
        else (agent_aliases(judge) if judge is not None else [agent_id.strip()])
    )
    records = await storage.query_decisions(
        kinds=MODE_KINDS.get(mode), agent_ids=judge_ids, since=since, until=until
    )
    records = [r for r in records if passes_ceiling(r, certainty_ceiling)]

    reviewed = await storage.reviewed_decision_ids([r.id for r in records])
    if mode == "unreviewed":
        records = [r for r in records if r.id not in reviewed]

    subject_ids = list(dict.fromkeys(
        sid for record in records for sid in record.subject_ids
    ))
    subjects = await storage.get_nodes(subject_ids) if subject_ids else {}
    # A vocabulary row's subjects are **label records**, not nodes —
    # which is the whole of what giving labels ids bought, and it is worth
    # nothing if review renders them as two dead strings. Read only where
    # something failed to resolve as a node, so an ordinary page pays nothing
    # and the one extra query lands on the small table.
    unresolved = [sid for sid in subject_ids if sid not in subjects]
    labels = (
        {
            record.id: record
            for record in await storage.query_relation_labels()
            if record.id in set(unresolved)
        }
        if unresolved else {}
    )

    scored = [
        ScoredDecision(record=record, signals=difficulty_signals(record, subjects))
        for record in records
    ]
    ordered = review_order(scored)
    page = ordered[:max_results]

    decisions = [
        {
            "decision_id": item.record.id,
            "kind": item.record.kind.value,
            "decided_at": item.record.decided_at.isoformat(),
            "judged_by": (
                item.record.judged_by.agent_id if item.record.judged_by else None
            ),
            "certainty": item.record.certainty,
            "certainty_basis": item.record.certainty_basis,
            "difficulty_signals": [s.value for s in item.signals],
            # `subject_kind` says which table answered, and a null preview
            # still means nothing did: a merge survivor a reversal destroyed, or
            # a row written elsewhere. That is information rather than an error,
            # so the id stays either way — but *gone* and *not a node in the
            # first place* are different answers, and before labels had ids
            # they were indistinguishable.
            "subjects": [
                _review_subject(sid, subjects, labels)
                for sid in item.record.subject_ids
            ],
            # Derived from a row pointing back, never stored (§3.4), which is
            # what makes `unreviewed` a mode rather than a flag on the row.
            "reviewed": item.record.id in reviewed,
            "reviews": item.record.reviews,
            "supersedes": item.record.supersedes,
            # Null on every kind that does not apply one, which is most of
            # them. Present on ingest and on a declaration sweep, where it is
            # the answer to *which world did this agent say these were about*.
            "frame": item.record.frame,
        }
        for item in page
    ]

    # The locator. Read after the graph's own answer and never merged into
    # it: these counts come from graphs whose node ids do not resolve here, so
    # everything that could be dereferenced stays on the near side of the line.
    here = storage.current_database
    others = [name for name in await storage.list_databases() if name != here]
    counts = (
        await storage.count_decisions_by_graph(
            others, agent_ids=judge_ids, since=since, until=until
        )
        if others else {}
    )

    result = {
        "mode": mode,
        # An answer that does not name its scope reads as the whole story.
        "graph": here,
        "decisions": decisions,
        "decisions_scanned": len(records),
        "truncated": len(ordered) > len(page),
        # Three results out of four hundred unrated rows is not the same answer
        # as three out of four, and only one of them means the graph is in good
        # shape (§6.4). Counted over everything selected, not over the page.
        "unrated_count": sum(1 for r in records if r.certainty is None),
        "unattributed_count": sum(1 for r in records if r.judged_by is None),
        "unreviewed_count": sum(1 for r in records if r.id not in reviewed),
        # The value **this call** used, never what the graph would have done:
        # a caller can pass its own, and a single bar means a refusal
        # message that stated a threshold as the system's and was false for
        # exactly the caller who overrode it.
        "certainty_ceiling": certainty_ceiling,
        "elsewhere": {
            # Every other graph, zeros included: *nothing there* is an answer a
            # reviewer can act on, and omitting it would read as *not checked*.
            "graphs": [
                {"graph": name, "decisions": counts[name]}
                for name in others if name in counts
            ],
            "total": sum(counts.values()),
            # What the counts were narrowed by — and by implication what they
            # were not. `mode` and `certainty_ceiling` are not mirrored here, so
            # a graph counted at 12 can list fewer than 12 when you get there.
            "counted_with": {
                # The ids the handle resolved to *here*, and not the handle:
                # this says what the count **ran with**, and two keys for one
                # filter would read as two filters. The handle is in `judge`.
                # Another graph may know this judge under a different set, and
                # the locator is allowed to be wider than what a review there
                # would list, never narrower — so its own resolution is not
                # consulted.
                "agent_ids": judge_ids,
                "since": since.isoformat() if since else None,
                "until": until.isoformat() if until else None,
            },
            # Listed but not counted: deleted between the two reads. Named
            # rather than dropped, because a graph missing from the counts and a
            # graph holding nothing are different answers.
            "unreadable": [name for name in others if name not in counts],
        },
    }

    if agent_id is not None:
        # What the handle turned out to name. A handle that resolves to
        # nothing used to return an empty page indistinguishable from a judge
        # that has decided nothing, which is the failure a rename or a typo
        # produces — so the two are told apart here, and the judges that do
        # exist are listed, since no tool enumerates them.
        result["judge"] = {
            "asked_for": agent_id,
            "agent_id": judge.id if judge else None,
            "name": agent_name(judge) if judge else None,
            "also_recorded_as": list(judge.former_ids) if judge else [],
        } | (
            {} if judge is not None else {
                "unknown_here": True,
                "judges_here": [
                    agent_name(a) for a in live_agents(await storage.list_agents())
                ],
            }
        )

    # Declared, like every response carrying node ids: `retrieved` is what drives
    # focus in the viewer, so an undeclared subject greys out the moment a
    # reviewer clicks the decision that names it. It is **not** the use signal —
    # only `search` stamps `retrieved_at` — so a graph reviewing itself does not
    # start looking busy to archival.
    #
    # Only the subjects still in this graph. A row can name a node that is gone
    # (a reversal's survivor) and declaring an id nothing resolves would ask the
    # viewer to focus on nothing.
    return result, ResponseMeta(
        nodes_returned=len(decisions),
        retrieved=_declare(
            sid
            for decision in decisions
            for subject in decision["subjects"]
            if (sid := subject["id"]) in subjects
        ),
    )


async def apply_review(
    storage: StorageBackend,
    *,
    confirmations: list[dict] | None = None,
    dissents: list[dict] | None = None,
    judge: JudgeRef | None = None,
) -> tuple[dict, ResponseMeta]:
    """Record that somebody checked decisions in the journal (§6.4).

    The only writer of a review, and `review()` never writes one. Two lists
    rather than a flag, because a reviewer asking *"what has been disputed"*
    does not want the agreements back, and a boolean inside a row cannot be
    selected on.

    **Neither list changes the graph.** A confirmation has nothing to change.
    A dissent has plenty and does none of it: undoing a merge is
    `reverse_merge`, an archival `restore`, a `one_claim` verdict a `distinct`
    through `apply_reflection` — each with its own refusals and its own journal
    row that sets `supersedes` because it really did supersede something. A
    dissent sets only `reviews`, so the journal never claims to have overturned
    a decision whose effect still stands (§4.2). Say in `because` what should
    happen, then make that call.

    **Not one transaction, and the design said it should be.** §10.7 asked for
    one, written when this tool was imagined as performing the reversals. It
    performs nothing, so there is no multi-step change to make atomic — and each
    entry is an independent judgment about an unrelated decision that happens to
    be batched, which is exactly the shape `apply_reflection` refuses per item.

    Each entry: `{decision_id, because, subject_ids?, certainty?,
    certainty_basis?}`. `subject_ids` narrows the review to the subjects
    actually checked — one pointer at an ingest record covering forty-four facts
    otherwise tells the graph a reviewer checked forty-four when it checked six
    (§4.1). Omitted means all of them.
    """
    recorded: list[dict] = []
    refused: list[dict] = []

    for entries, agreed in ((confirmations or [], True), (dissents or [], False)):
        for entry in entries:
            outcome = await review_decision(
                storage,
                decision_id=str(entry.get("decision_id", "")),
                agreed=agreed,
                because=str(entry.get("because", "")),
                subject_ids=entry.get("subject_ids"),
                certainty=entry.get("certainty"),
                certainty_basis=entry.get("certainty_basis"),
                judge=judge,
            )
            if isinstance(outcome, ReviewRefused):
                refused.append(outcome.model_dump())
            else:
                recorded.append(outcome.model_dump())

    result = {
        "recorded": recorded,
        "refused": refused,
        "confirmations": sum(1 for r in recorded if r["kind"] == "confirmation"),
        "dissents": sum(1 for r in recorded if r["kind"] == "dissent"),
        "graph": storage.current_database,
    }
    # The subjects of what was reviewed, so the viewer can follow a reviewer's
    # attention the same way it follows a search.
    return result, ResponseMeta(
        nodes_returned=len(recorded),
        retrieved=_declare(
            sid for entry in recorded for sid in entry["subjects"]
        ),
    )


async def rejudge(
    node_id: str,
    storage: StorageBackend,
    *,
    because: str,
    claim_kind: str | None = None,
    confidence: float | None = None,
    confidence_basis: str | None = None,
    certainty: float | None = None,
    certainty_basis: str | None = None,
    judge: JudgeRef | None = None,
) -> tuple[dict, ResponseMeta]:
    """Revise an ingest-time judgment about a node, without touching the claim (§6.5).

    `claim_kind`, `confidence` and `confidence_basis` are supplied by an agent
    that read the material, and nothing downstream re-makes them — so until this
    existed, review could find every ingest-time mistake and fix none of them,
    which is the verdict-with-no-action shape the `assessed` edge was built for.

    **Never a supersession.** `update` requires `because` to be *it was wrong* or
    *the world changed*, and a mislabelled `claim_kind` is neither: the claim was
    right and the world did not move — the *judgment about* the claim was wrong.
    Filing it as a correction would retire a true node and re-point its edges,
    which is the forgetting the validity model exists to prevent, over a metadata
    mistake. So
    no status moves, no edge moves, no lineage is written, and the node keeps its
    `judged_by`: that field records who wrote the wording, which is unchanged.

    **`confidence` and `certainty` are different numbers on the same ladder, and
    this is the one call that takes both.** `confidence` is a prior about the
    *material* — how well the record would back this claim up. `certainty` is
    about *this act of re-judging* — how sure you are that the original was
    wrong. Omit either for the ordinary case; omitting stores unrated, which is
    deliberately not a rated 0.5.

    **The prior value is kept.** Each revision appends to
    `metadata["rejudgments"]` with what it was, what it became and why, because
    otherwise this would be the one call in the system that destroys a judgment
    rather than superseding it.

    **`importance` is not here.** `judge_importance` is already this tool for
    that one field, and two writers for one value is how it ends up depending on
    which tool ran last.
    """
    parsed_kind = None
    if claim_kind is not None:
        try:
            parsed_kind = ClaimKind(claim_kind)
        except ValueError:
            return {
                "rejudged": False,
                "node_id": node_id,
                "refused": (
                    f"'{claim_kind}' is not a claim kind. Expected "
                    f"{' or '.join(k.value for k in ClaimKind)} — a condition "
                    f"that holds over a period, or something that happened on "
                    f"an occasion."
                ),
            # A refusal still names the id back at the agent, so it still has to
            # declare it — otherwise focus mode greys a node the response just
            # showed. The same rule `merge_facts`' refusal follows.
            }, ResponseMeta(retrieved=_declare([node_id]))

    outcome = await rejudge_node(
        storage,
        node_id=node_id,
        because=because,
        claim_kind=parsed_kind,
        confidence=confidence,
        confidence_basis=confidence_basis,
        certainty=certainty,
        certainty_basis=certainty_basis,
        judge=judge,
    )
    if isinstance(outcome, RejudgeRefused):
        return (
            {"rejudged": False, "node_id": node_id, "refused": outcome.reason},
            ResponseMeta(retrieved=_declare([node_id])),
        )

    record = await journal(
        storage,
        DecisionKind.REJUDGMENT,
        [node_id],
        judge=judge,
        reviews=outcome.reviews,
        certainty=certainty,
        certainty_basis=certainty_basis,
    )
    result = {
        "rejudged": True,
        "node_id": node_id,
        "changed": outcome.changed,
        # The decision this revises, blank where the original predates the
        # journal — which the journal cannot cite, and does not pretend to.
        "reviews": outcome.reviews,
        "decision_id": record.id if record else None,
    }
    return result, ResponseMeta(nodes_returned=1, retrieved=_declare([node_id]))


async def reframe(
    node_id: str,
    storage: StorageBackend,
    *,
    withdraw: str,
    because: str,
    assign: str | None = None,
    judge: JudgeRef | None = None,
) -> tuple[dict, ResponseMeta]:
    """Withdraw a frame from a node, optionally putting another in its place.

    A metacontext assignment used to be one-way, so a fact wrongly framed as
    fiction stayed framed for ever — and that is not cosmetic. It becomes
    permanently unmergeable with its own twin, it stops corroborating the real
    copy, and a frame-scoped search misses it where it belongs while returning
    it where it does not. All three fail silently.

    **Use `assign` whenever the claim belongs in another frame.** Withdrawing
    and then linking passes through a state where the node states no frame at
    all, and strands it there if the second call never happens.

    **A withdrawal that would leave no frames is refused.** A frameless node
    shares a frame with nothing — never compared, never merged, returned by no
    scoped search — so there is nothing to authorise and no flag to pass. Name
    where the claim goes instead.

    Not a supersession: the claim is unchanged and the world has not moved, so
    nothing is retired and no lineage moves.
    """
    from epimemer.pipelines.frames import ReframeRefused, reframe_node

    outcome = await reframe_node(
        storage,
        node_id=node_id,
        withdraw=withdraw,
        because=because,
        assign=assign,
        judge=judge,
    )
    if isinstance(outcome, ReframeRefused):
        return (
            {"reframed": False, "node_id": node_id, "refused": outcome.reason},
            ResponseMeta(retrieved=_declare([node_id])),
        )

    record = await journal(
        storage, DecisionKind.REFRAME, [node_id], judge=judge
    )
    result = {
        "reframed": True,
        "node_id": node_id,
        "withdrew": outcome.withdrew,
        "assigned": outcome.assigned,
        # Never empty: a revision that would strand the node is refused.
        "frames_now": outcome.frames_now,
        "decision_id": record.id if record else None,
    }
    return result, ResponseMeta(nodes_returned=1, retrieved=_declare([node_id]))


async def correct_interval(
    node_id: str,
    storage: StorageBackend,
    *,
    source_id: str,
    intervals: list[dict],
    because: str,
    judge: JudgeRef | None = None,
) -> tuple[dict, ResponseMeta]:
    """Replace what one source is recorded as asserting about when a claim held.

    For an endpoint that is **present and wrong**. `boundary_proposals` fills one
    that is *open*, where a succession implies it; nothing derives that a stated
    date was misread, so this is a separate act on separate evidence.

    Corroboration reads intervals to decide whether a look-alike is a
    witness to the same period or the neighbouring truth, so a wrong interval
    moves a count as well as a date.

    **The whole list for that (node, source) pair is replaced**, because an
    interval is a position in a list on one edge and has no id of its own. An
    empty list is allowed, and is the correction for a period that was invented
    outright.

    Not a supersession.
    """
    from epimemer.pipelines.reflection.boundaries import (
        IntervalCorrectionRefused,
        correct_interval as correct_interval_edge,
    )

    try:
        parsed = [ValidityInterval.model_validate(entry) for entry in intervals]
    except ValidationError as problem:
        return (
            {
                "corrected": False,
                "node_id": node_id,
                "source_id": source_id,
                "refused": (
                    f"an interval did not validate: {problem.error_count()} "
                    f"problem(s). `basis` has no default and must be 'stated' "
                    f"or 'inferred' — the agent is not a source, so every "
                    f"period says which it was."
                ),
            },
            ResponseMeta(retrieved=_declare([node_id])),
        )

    outcome = await correct_interval_edge(
        storage,
        node_id=node_id,
        source_id=source_id,
        intervals=parsed,
        because=because,
        judge=judge,
    )
    if isinstance(outcome, IntervalCorrectionRefused):
        return (
            {
                "corrected": False,
                "node_id": node_id,
                "source_id": source_id,
                "refused": outcome.reason,
            },
            ResponseMeta(retrieved=_declare([node_id])),
        )

    record = await journal(
        storage, DecisionKind.INTERVAL_CORRECTION, [node_id], judge=judge
    )
    result = {
        "corrected": True,
        "node_id": node_id,
        "source_id": source_id,
        "was": [interval.model_dump(mode="json") for interval in outcome.was],
        "now": [interval.model_dump(mode="json") for interval in outcome.now],
        "decision_id": record.id if record else None,
    }
    return result, ResponseMeta(nodes_returned=1, retrieved=_declare([node_id]))


# --- Query Graph ---


async def query_graph(
    node_id: str,
    storage: StorageBackend,
    *,
    hops: int = 1,
    edge_types: list[str] | None = None,
) -> tuple[dict, ResponseMeta]:
    """Traverse the graph from a node, returning the local subgraph."""
    from epimemer.pipelines.query.graph_expansion import expand_via_graph
    from epimemer.pipelines.reflection.review import review_labels_for

    seed_node = await storage.get_node(node_id)
    if seed_node is None:
        raise ValueError(f"Node '{node_id}' not found")

    # Build exclude set if filtering by edge types (include only those listed)
    exclude_edge_types = None
    if edge_types:
        allowed = {EdgeType(t) for t in edge_types}
        all_types = set(EdgeType)
        exclude_edge_types = all_types - allowed

    nodes, edges = await expand_via_graph(
        seed_nodes=[seed_node],
        storage=storage,
        hops=hops,
        exclude_edge_types=exclude_edge_types,
    )

    review_by_node = await review_labels_for(nodes, storage)
    nodes_data = []
    for node in nodes:
        node_dict = _node_to_dict(node)
        if node.id in review_by_node:
            node_dict["review"] = review_by_node[node.id]
        nodes_data.append(node_dict)
    edges_data = [e.model_dump(mode="json") for e in edges]

    source_types: dict[str, int] = {}
    for node in nodes:
        key = _node_type_key(node)
        source_types[key] = source_types.get(key, 0) + 1

    result = {"nodes": nodes_data, "edges": edges_data}
    meta = ResponseMeta(
        nodes_returned=len(nodes),
        graph_hops=hops,
        source_types=source_types,
        # Everything but the seed arrived by walking edges from it, which is
        # what `expanded` means; the seed itself was asked for by id.
        retrieved=_declare(
            (n.id for n in nodes),
            provenance={
                n.id: (
                    SeedProvenance.DIRECT
                    if n.id == seed_node.id
                    else SeedProvenance.EXPANDED
                )
                for n in nodes
            },
        ),
    )
    return result, meta


# --- Archive ---


async def archive(
    storage: StorageBackend,
    *,
    max_age_days: int = 90,
) -> tuple[dict, ResponseMeta]:
    """Find and export archival candidates to a serializable format."""
    from epimemer.pipelines.reflection.archival import archive_nodes, find_archival_candidates

    candidates = await find_archival_candidates(storage, max_age_days=max_age_days)
    archive_data = await archive_nodes(candidates, storage)

    result = {
        "nodes_archived": len(candidates),
        "archive_data": archive_data,
    }
    meta = ResponseMeta(nodes_returned=len(candidates))
    return result, meta


# --- Restore ---


async def restore(
    storage: StorageBackend,
    *,
    archive_data: dict | None = None,
    node_ids: list[str] | None = None,
    sourced_from: str | None = None,
    validity: list[dict] | None = None,
    judge: JudgeRef | None = None,
) -> tuple[dict, ResponseMeta]:
    """Bring nodes back: from an archive blob, or by id when a claim recurs.

    Three shapes reach this, and they need different writes.

    A *cold-storage reimport* brings back records the graph no longer holds:
    those are reconstructed first (so a malformed record fails before anything
    is written) and persisted in a single ``write_batch_tx`` — all of it lands
    or none of it does.

    An *un-archival* is the reversal of the hygiene sweep, and there the rows
    are still present: `archive` never deletes, it flips status. Re-inserting
    them would write nothing, so anything already stored as ARCHIVED is flipped
    back to ACTIVE instead.

    A *reactivation* names `node_ids` directly: a claim retired as HISTORICAL
    because the world moved on, asserted true again by a new source.
    Labour out of government in 2010 and back in 2024 is one claim recurring,
    not two claims, and the alternative — a second node saying what the first
    one said — is the duplication this graph exists to avoid, manufactured by
    its own bookkeeping.

    **What may come back is `RESTORABLE_STATUSES`, and CORRECTED is not in it.**
    That was always this tool's stated reason — *restoring an archive must not
    resurrect a node that was superseded for being wrong* — but before the
    status split it could only be enforced as "not superseded", which refused
    the world-change case too. Now it says what it means.

    **A reactivation must name the source asserting the claim again**, and the
    flip and that edge land in one transaction. A node back to ACTIVE with no
    edge recording why is an assertion the graph makes and cannot attribute.
    The prior intervals and the `temporally_followed_by` record are untouched,
    so the node ends holding several disjoint periods — which is what a list of
    intervals was for.
    """
    archive_data = archive_data or {}
    nodes = [_reconstruct_node(nd) for nd in archive_data.get("nodes", [])]
    edges = [NodeEdge(**ed) for ed in archive_data.get("edges", [])]

    missing: list[EpistemicNode] = []
    archived: list[EpistemicNode] = []
    for node in nodes:
        stored = await storage.get_node(node.id)
        if stored is None:
            missing.append(node)
        elif stored.status is NodeStatus.ARCHIVED:
            archived.append(stored)

    reactivated, new_edges = await _reactivation(
        node_ids or [], sourced_from, validity, storage, judge=judge
    )

    # Only edges reaching a node that was itself missing can be missing: an
    # edge between two nodes still in the graph was never removed.
    missing_ids = {node.id for node in missing}
    missing_edges = [
        edge for edge in edges
        if edge.src_id in missing_ids or edge.dst_id in missing_ids
    ]

    await storage.write_batch_tx(nodes=missing, edges=missing_edges)
    coming_back = archived + reactivated
    if coming_back:
        await storage.set_node_status_tx(
            coming_back, status=NodeStatus.ACTIVE,
            at=datetime.now(timezone.utc), edges=new_edges, judge=judge,
        )

    # One row for the call, ingest's granularity for ingest's reason: bringing
    # a batch back is one act. Both shapes land here — a cold-storage reimport
    # and a claim asserted true again — because both are the same judgment,
    # *this belongs in the active graph*, made about different rows.
    brought_back = [node.id for node in missing] + [node.id for node in coming_back]
    if brought_back:
        await journal(
            storage, DecisionKind.REACTIVATION, brought_back, judge=judge
        )

    result = {
        "nodes_restored": len(missing),
        "nodes_reactivated": len(coming_back),
        "edges_restored": len(missing_edges) + len(new_edges),
    }
    meta = ResponseMeta(nodes_returned=len(missing) + len(coming_back))
    return result, meta


async def _reactivation(
    node_ids: list[str],
    sourced_from: str | None,
    validity: list[dict] | None,
    storage: StorageBackend,
    *,
    judge: JudgeRef | None = None,
) -> tuple[list[EpistemicNode], list[NodeEdge]]:
    """The nodes a `recurs` verdict brings back, and the provenance it brings.

    Every refusal here is checked before anything is written, so a batch naming
    one CORRECTED node changes nothing at all rather than reactivating the rest
    and reporting an error about the one.
    """
    if not node_ids:
        return [], []

    nodes: list[EpistemicNode] = []
    for node_id in node_ids:
        node = await storage.get_node(node_id)
        if node is None:
            raise ValueError(f"Node '{node_id}' not found.")
        if node.status is NodeStatus.ACTIVE:
            continue  # already back; asking twice is not an error
        if node.status not in RESTORABLE_STATUSES:
            raise ValueError(
                f"'{node_id}' is {node.status.value} and cannot be restored. A "
                f"claim retired for being wrong has no route back — supersede "
                f"the correction instead if the graph now says otherwise. "
                f"Restorable: "
                f"{', '.join(sorted(s.value for s in RESTORABLE_STATUSES))}."
            )
        nodes.append(node)

    if not nodes:
        return [], []

    historical = [n for n in nodes if n.status is NodeStatus.HISTORICAL]
    if historical and sourced_from is None:
        raise ValueError(
            "reactivating a historical claim requires `sourced_from`: the "
            "document asserting it is true again. Without it the graph would "
            "state the claim and be unable to say who says so."
        )
    if sourced_from is not None and await storage.get_document(sourced_from) is None:
        raise ValueError(f"Document '{sourced_from}' not found.")

    intervals = [ValidityInterval.model_validate(v) for v in (validity or [])]
    edges = [
        NodeEdge(
            src_id=node.id, dst_id=sourced_from, type=EdgeType.SOURCED_FROM,
            validity=intervals, judged_by=judge,
        )
        for node in nodes
        if sourced_from is not None
    ]
    return nodes, edges


# --- Helpers ---


def _node_to_dict(node: EpistemicNode) -> dict:
    """Serialize a node to dict with its type tag.

    `value.confidence` goes out as `null` when nobody rated the node, and is
    deliberately not substituted with the 0.5 that `rated_confidence` supplies
    elsewhere. This is the surface an agent reads, and it is the audience the
    nullable field exists for: "no one has assessed this" is worth knowing when
    deciding how far to lean on a retrieved claim, and 0.5 cannot say it.
    """
    data = node.model_dump(mode="json")
    # An unknown judge is dropped rather than sent as null, which is the
    # opposite of what `confidence` does directly above — and the difference is
    # what the absence says. A missing confidence is a caveat *about the claim*
    # and worth a line in every result. A missing judge says only that this
    # graph does not record one, which is the default state and true of every
    # node in it (REVIEW_MODE.md §3.3): repeating it per result is noise the
    # agent pays for. A judge that *is* present is information, and is sent.
    if data.get("judged_by") is None:
        data.pop("judged_by", None)
    data["node_type"] = _node_type_key(node)
    return data


def _node_type_key(node: EpistemicNode) -> str:
    """Get the string key for a node's type."""
    if isinstance(node, Topic):
        return "topic"
    elif isinstance(node, Fact):
        return "fact"
    elif isinstance(node, Inference):
        return "inference"
    return "unknown"


def _reconstruct_node(data: dict) -> EpistemicNode:
    """Reconstruct a typed node from a dict.

    Uses the node_type field if present, otherwise tries each type.
    """
    node_type = data.pop("node_type", None)
    if node_type == "topic":
        return Topic(**data)
    elif node_type == "fact":
        return Fact(**data)
    elif node_type == "inference":
        return Inference(**data)

    # Fallback: try each type
    for cls in (Topic, Fact, Inference):
        try:
            return cls(**data)
        except Exception:
            continue
    raise ValueError(f"Cannot reconstruct node from data: {data}")


async def _metacontext_labels_for(
    node_ids: Sequence[str], storage: StorageBackend
) -> dict[str, list[str]]:
    """`_metacontext_labels` for many nodes at once, keyed by node id.

    Each metacontext is read once for the whole set rather than once per node
    that carries it — a shared frame is the normal case, so per node meant
    re-reading the same handful of records for every result.
    """
    tagged = await storage.get_edges_for(
        node_ids, direction="from", edge_type=EdgeType.HAS_METACONTEXT
    )
    contents: dict[str, str] = {}
    for edges in tagged.values():
        for edge in edges:
            if edge.dst_id not in contents:
                mc = await storage.get_metacontext(edge.dst_id)
                if mc:
                    contents[edge.dst_id] = mc.content
    return {
        node_id: [contents[e.dst_id] for e in edges if e.dst_id in contents]
        for node_id, edges in tagged.items()
    }


async def _metacontext_labels(node_id: str, storage: StorageBackend) -> list[str]:
    """Content labels of the metacontexts a node is tagged with."""
    return (await _metacontext_labels_for([node_id], storage))[node_id]


async def _ensure_symmetric_edge(
    a_id: str,
    b_id: str,
    edge_type: EdgeType,
    storage: StorageBackend,
    *,
    judge: JudgeRef | None = None,
) -> tuple[str, bool]:
    """Create a symmetric edge between a and b if absent. Returns (edge_id, created).

    Keeps symmetric relationships (contradiction, variant_of) to one edge per
    pair regardless of direction, so repeated recording does not accumulate
    duplicates.
    """
    from epimemer.pipelines.reflection.similarity_decisions import (
        symmetric_edge_between,
    )

    existing = await symmetric_edge_between(a_id, b_id, edge_type, storage)
    if existing is not None:
        # Deliberately not restamped. The edge records the judgment that made
        # it, and a second agent calling the same tool has confirmed rather than
        # decided — which is a record of its own (§6.4), not an overwrite of
        # somebody else's name.
        return existing.id, False
    edge = NodeEdge(src_id=a_id, dst_id=b_id, type=edge_type, judged_by=judge)
    await storage.store_edge(edge)
    return edge.id, True


# --- Timeline tools ---


def _reference_time_iso(timeline: Timeline) -> str | None:
    """A timeline's reference time as ISO, or None when it follows the clock."""
    return (
        None if timeline.reference_time is None
        else timeline.reference_time.isoformat()
    )


async def create_timeline(
    name: str,
    storage: StorageBackend,
    *,
    description: str = "",
    reference_time: datetime | None = None,
) -> tuple[dict, ResponseMeta]:
    """Create a new timeline.

    `reference_time` is the timeline's own "now" — set it for a fictional or
    historical timeline whose present is not the wall clock. Leaving it unset
    means the timeline tracks real time, which is not the same as pinning it to
    the instant of creation.
    """
    tl = Timeline(name=name, description=description, reference_time=reference_time)
    await storage.store_timeline(tl)
    result = {
        "timeline_id": tl.id,
        "name": tl.name,
        "reference_time": _reference_time_iso(tl),
    }
    meta = ResponseMeta(nodes_returned=1)
    return result, meta


async def set_reference_time(
    timeline_id: str,
    storage: StorageBackend,
    *,
    reference_time: datetime | None = None,
) -> tuple[dict, ResponseMeta]:
    """Set (or clear) a timeline's reference time.

    Separate from creation because a fiction's present is often learned later,
    and read wrong first — the opening chapter dates the story only once you
    have read it. Passing nothing clears the setting, returning the timeline to
    the wall clock.
    """
    tl = await storage.get_timeline(timeline_id)
    if tl is None:
        raise ValueError(f"Timeline '{timeline_id}' not found")

    # Copy-with-update rather than mutate-and-store: `store_timeline` is an
    # upsert of the whole record, so the timepoints have to travel with it.
    updated = tl.model_copy(update={"reference_time": reference_time})
    await storage.store_timeline(updated)

    result = {
        "timeline_id": updated.id,
        "name": updated.name,
        "reference_time": _reference_time_iso(updated),
    }
    meta = ResponseMeta(nodes_returned=1)
    return result, meta


async def add_timeline_timepoint(
    timeline_id: str,
    storage: StorageBackend,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    label: str | None = None,
) -> tuple[dict, ResponseMeta]:
    """Add a timepoint to an existing timeline."""
    from epimemer.pipelines.timeline.functions import add_timepoint

    tl = await storage.get_timeline(timeline_id)
    if tl is None:
        raise ValueError(f"Timeline '{timeline_id}' not found")

    tl, tp = add_timepoint(tl, start=start, end=end, label=label)
    await storage.store_timeline(tl)  # overwrite with updated timeline

    result = {
        "timeline_id": tl.id,
        "timepoint_id": tp.id,
        "timepoints_count": len(tl.timepoints),
    }
    meta = ResponseMeta(nodes_returned=1)
    return result, meta


async def query_timeline(
    timeline_id: str,
    storage: StorageBackend,
    *,
    target: datetime | None = None,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    k: int = 5,
) -> tuple[dict, ResponseMeta]:
    """Query timepoints on a timeline (nearest or range)."""
    from epimemer.pipelines.timeline.functions import find_nearest, get_in_range

    tl = await storage.get_timeline(timeline_id)
    if tl is None:
        raise ValueError(f"Timeline '{timeline_id}' not found")

    timepoints: list = []
    if target is not None:
        timepoints = find_nearest(tl, target, k=k)
    elif range_start is not None and range_end is not None:
        timepoints = get_in_range(tl, range_start, range_end)
    else:
        # Return all timepoints
        timepoints = tl.timepoints

    result = {
        "timeline_id": tl.id,
        "timeline_name": tl.name,
        # Reported on every query so a caller reading timepoints can tell which
        # of them are past and which are future without a second call.
        "reference_time": _reference_time_iso(tl),
        "timepoints": [tp.model_dump(mode="json") for tp in timepoints],
    }
    meta = ResponseMeta(nodes_returned=len(timepoints))
    return result, meta


async def create_timelink(
    node_id: str,
    timeline_id: str,
    timepoint_id: str,
    storage: StorageBackend,
) -> tuple[dict, ResponseMeta]:
    """Link a node to a specific timepoint on a timeline."""
    # Verify node exists
    node = await storage.get_node(node_id)
    if node is None:
        raise ValueError(f"Node '{node_id}' not found")

    # Verify timeline and timepoint exist
    tl = await storage.get_timeline(timeline_id)
    if tl is None:
        raise ValueError(f"Timeline '{timeline_id}' not found")

    from epimemer.pipelines.timeline.functions import get_timepoint
    tp = get_timepoint(tl, timepoint_id)
    if tp is None:
        raise ValueError(f"Timepoint '{timepoint_id}' not found on timeline '{timeline_id}'")

    edge = NodeEdge(
        src_id=node_id,
        dst_id=timeline_id,
        type=EdgeType.TIMELINK,
        metadata={"timepoint_id": timepoint_id},
    )
    await storage.store_edge(edge)

    result = {"edge_id": edge.id, "timepoint_id": timepoint_id}
    meta = ResponseMeta(nodes_returned=1, retrieved=_declare([node_id]))
    return result, meta


# --- Metacontext tools ---


async def create_metacontext(
    content: str,
    storage: StorageBackend,
    *,
    description: str = "",
    metacontext_id: str | None = None,
) -> tuple[dict, ResponseMeta]:
    """Create a new metacontext, optionally under an id you choose.

    **A chosen id is what makes `the-real` an ordinary frame.** It is the
    conventional name for the frame holding real-world claims, and nothing reads
    it specially — so it has to be creatable, like any other, by whoever first
    needs it. Left out, an id is minted, which is what every frame with no
    convention behind it wants.

    Re-creating an id that exists overwrites its prose. That is the same
    behaviour `store_metacontext` has always had, and it is why a graph's frames
    are worth naming deliberately rather than typing twice.
    """
    mc = Metacontext(content=content, description=description)
    if metacontext_id:
        mc = mc.model_copy(update={"id": metacontext_id})
    await storage.store_metacontext(mc)
    result = {"metacontext_id": mc.id, "content": mc.content}
    meta = ResponseMeta(nodes_returned=1)
    return result, meta


async def get_metacontexts_for_node(
    node_id: str,
    storage: StorageBackend,
) -> tuple[dict, ResponseMeta]:
    """Get all metacontexts associated with a node."""
    node = await storage.get_node(node_id)
    if node is None:
        raise ValueError(f"Node '{node_id}' not found")

    edges = await storage.get_edges_from(node_id)
    mc_edges = [e for e in edges if e.type == EdgeType.HAS_METACONTEXT]

    metacontexts = []
    for edge in mc_edges:
        mc = await storage.get_metacontext(edge.dst_id)
        if mc:
            metacontexts.append(mc.model_dump(mode="json"))

    result = {"node_id": node_id, "metacontexts": metacontexts}
    meta = ResponseMeta(
        nodes_returned=len(metacontexts), retrieved=_declare([node_id])
    )
    return result, meta


# --- Graph management ---


def _similar_names(target: str, candidates: list[str], max_results: int = 3) -> list[str]:
    """Find candidate names similar to target using edit distance."""
    from difflib import SequenceMatcher

    scored = [
        (name, SequenceMatcher(None, target.lower(), name.lower()).ratio())
        for name in candidates
        if name != target
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [name for name, score in scored[:max_results] if score > 0.4]


async def effective_reflect_threshold(
    storage: StorageBackend, default: int
) -> int:
    """The threshold in force for the active graph: its override, else `default`.

    For callers that need only the number. `graph_stats` reads the override
    itself — it reports whether one is set — and resolves it with
    `resolve_reflect_threshold` rather than fetching twice.
    """
    return resolve_reflect_threshold(
        await storage.get_reflect_threshold_override(), default
    )


async def configure_reflection(
    storage: StorageBackend,
    *,
    threshold: int | None,
    default_threshold: int,
) -> tuple[dict, ResponseMeta]:
    """Set the active graph's reflect threshold, or clear it back to the default.

    `threshold=None` clears the override — the graph then follows whatever the
    process default is at the time, rather than freezing today's value.

    Deliberately does not touch the counter: raising the threshold means "not
    yet", and zeroing the count would discard the accumulated signal instead of
    deferring it.
    """
    if threshold is not None and threshold < 1:
        raise ValueError(f"threshold must be at least 1, got {threshold}")

    await storage.set_reflect_threshold_override(threshold)

    count = await storage.get_reflect_counter()
    effective = await effective_reflect_threshold(storage, default_threshold)
    result = {
        "graph": storage.current_database,
        "reflect_threshold": effective,
        "overridden": threshold is not None,
        "default_threshold": default_threshold,
        "stores_since_reflect": count,
        "reflect_suggested": count >= effective,
    }
    return result, ResponseMeta()


async def graph_stats(
    storage: StorageBackend, *, default_reflect_threshold: int
) -> tuple[dict, ResponseMeta]:
    """Summarize the active graph: node counts by type, edge counts by type, totals.

    Aggregate-only — does not materialize node or edge bodies.

    Also reports `nodes_without_frame`, which is a migration readout rather than
    an ordinary statistic: it can only be non-zero on a graph written before
    `metacontext_id` was required, and it is how a user checks that
    `epimemer frames declare` has finished its work.

    Also reports reflection pressure: the graph's store counter, the threshold in
    force, whether that threshold is a per-graph override, and whether a reflect
    is due. The counter and any override are stored per graph; the default is
    process config, so it is passed in. These keys are always present — an absent
    key reads the same as `false` to a caller, and this is a readout meant to be
    checked.
    """
    node_counts = await storage.count_nodes_by_type()
    edge_counts = await storage.count_edges_by_type()
    unframed = await storage.count_nodes_without_frame()
    metacontexts = await storage.query_metacontexts()
    timelines = await storage.query_timelines()
    stores_since_reflect = await storage.get_reflect_counter()
    threshold_override = await storage.get_reflect_threshold_override()
    reflect_threshold = resolve_reflect_threshold(
        threshold_override, default_reflect_threshold
    )

    nodes_by_type = {nt.value: node_counts.get(nt, 0) for nt in NodeType}
    edges_by_type = {et.value: edge_counts.get(et, 0) for et in EdgeType}
    total_nodes = sum(nodes_by_type.values())
    total_edges = sum(edges_by_type.values())

    result = {
        "graph": storage.current_database,
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "nodes_by_type": nodes_by_type,
        # Only surface edge types that are actually present, to keep the
        # response readable; the full zero-filled map is available above logic.
        "edges_by_type": {k: v for k, v in edges_by_type.items() if v > 0},
        "metacontexts": len(metacontexts),
        # A node carrying no frame at all is a node nothing compares, merges or
        # returns from a scoped search — absence names no frame, so it shares
        # one with nothing. Only a graph written before the frame was required
        # can hold any, which makes this the completeness check for
        # `epimemer frames declare`: zero is the answer.
        "nodes_without_frame": unframed,
        "timelines": len(timelines),
        "empty": total_nodes == 0 and total_edges == 0,
        "stores_since_reflect": stores_since_reflect,
        "reflect_threshold": reflect_threshold,
        "reflect_threshold_overridden": threshold_override is not None,
        # Inclusive, matching store_decomposition — the two readouts must not
        # disagree about whether a reflect is due.
        "reflect_suggested": stores_since_reflect >= reflect_threshold,
    }
    meta = ResponseMeta(nodes_returned=total_nodes, source_types=nodes_by_type)
    return result, meta


# --- Agents (REVIEW_MODE.md §2) ---

class ApprovalOutcome(BaseModel):
    """What came back from asking the user which judge this agent is.

    **Three states, not two.** `chosen` set is an approval — and may name a
    different id from the one proposed, because the user picks or edits.
    `chosen` empty splits in two, and the split is load-bearing:

    - **declined** — the question reached a person and they said no. Refuses,
      whatever the approved list says. A user declining an id they approved
      last week is withdrawing it for this bind, not being overruled by a list.
    - **unavailable** — no channel to a person exists at all, which is a client
      without the elicitation capability. Here an id the user approved through
      `EPIMEMER_APPROVED_AGENTS` or `epimemer agents confirm` binds, because
      that approval *is* the user's involvement (§2.3) and refusing it would
      leave such a client unable to judge at all.

    The two were one value until 2026-08-25 and the conflation was harmless
    while an approved id skipped the question entirely. Asking on every bind is
    what made them have to be told apart.
    """

    chosen: str | None = None
    channel_available: bool = True


# Asks the user to admit an id. The server owns *how* the question is put; this
# module owns *when* it is worth asking, and reads the answer's three states off
# `ApprovalOutcome`.
ApproveId = Callable[[str, str], Awaitable[ApprovalOutcome]]

# Asks the user to confirm a *new self-description* for an id they have already
# admitted. Separate from `ApproveId` because the two questions have different
# consequences: an unanswered id question refuses the claim, an unanswered
# description question records the version unconfirmed, which is a real
# epistemic object rather than a failure (§2.4).
ConfirmDescription = Callable[[str, str], Awaitable[bool]]


async def approve_agent_ids(
    storage: StorageBackend, ids: Sequence[str]
) -> list[str]:
    """Admit ids to the active graph's approved list. Returns the whole list.

    A **union**, never a replacement. Three writers reach this — the elicitation
    path, the `epimemer agents confirm` CLI, and config seeding at connect — and
    a replacement would have whichever ran last silently revoke the others.
    Order is preserved and duplicates are dropped, so the list reads as the
    order the user admitted them in.
    """
    approved = await storage.get_approved_agent_ids()
    added = [i for i in dict.fromkeys(ids) if i and i not in approved]
    if not added:
        return approved
    updated = [*approved, *added]
    await storage.set_approved_agent_ids(updated)
    return updated


async def seed_approved_judges(
    storage: StorageBackend, handles: Sequence[str]
) -> list[str]:
    """Admit judges named the way a **person** would name them.

    `EPIMEMER_APPROVED_AGENTS` and `epimemer agents confirm` take text a user
    typed, and since the three-layer split the approved list holds opaque keys
     — so a name has to be resolved to the judge it belongs to, or seeding
    an existing judge by name would approve a second, empty identity under the
    name as a key. **Where nothing matches, the handle is admitted as itself**,
    which is exactly the old behaviour and the only sensible reading of seeding
    a judge that does not exist yet: its first claim adopts the key.
    """
    agents = await storage.list_agents()
    return await approve_agent_ids(storage, [
        agent.id if (agent := resolve_agent(agents, handle)) is not None else handle
        for handle in handles
    ])


async def judge_is_approved(storage: StorageBackend, judge: JudgeRef) -> bool:
    """Whether this judge may still write to the **active** graph.

    Approval is per graph, so a session that switches graphs carries a binding
    the new graph never made. One declaration of the rule, called both at
    `use_graph` and again at write time — the second is cheap, and is what keeps
    the first from being a single point of failure (§10.3).
    """
    return judge.agent_id in await storage.get_approved_agent_ids()


async def judge_required(
    storage: StorageBackend, *, process_default: bool
) -> bool:
    """Whether this graph refuses a write that names no judge (§3.3.1).

    The graph's own answer wins, `None` follows the process default. One
    declaration, read at the MCP boundary and nowhere else — a backend that
    refused on its own account would be a second place for the policy to differ,
    and the two could differ silently.
    """
    return resolve_require_judge(
        await storage.get_require_judge(), process_default
    )


def judge_required_reason(approved: Sequence[str]) -> str:
    """Why a write was refused for naming no judge, written for the user.

    It has to name the two ways out, because the agent reading it can do
    neither: claiming an identity needs a judge **the user has approved**, and
    approving one is not something any tool can do (§2.3). `approved` arrives
    as labels rather than keys — see `approved_labels`.
    """
    known = (
        f"Judges approved here: {', '.join(approved)}."
        if approved
        else (
            "**No judge has been approved in this graph**, so no write can "
            "succeed until one is — set EPIMEMER_APPROVED_AGENTS, or run "
            "`epimemer agents confirm <name>` against a served SurrealDB."
        )
    )
    return (
        f"This graph requires every write to name a judge, and this session has "
        f"not claimed one. Call claim_agent first. {known} If this is wrong — "
        f"if the graph should not require a judge — the user can turn it off "
        f"with `epimemer agents require off`; you cannot, and that is "
        f"deliberate."
    )


# One picker line has to fit a terminal, so the parts are budgeted rather than
# concatenated and hoped for.
_ROSTER_LINE_BUDGET = 88
# Choice keys are prefixed so that no agent id, whatever it contains, can
# collide with the "mint a new one" option. Agent ids are user-assigned free
# text and are validated for emptiness and nothing else, so a bare sentinel
# would be a string somebody could legitimately be called.
JUDGE_CHOICE_PREFIX = "use:"
NEW_JUDGE_CHOICE = "new:"
RENAME_JUDGE_CHOICE = "rename:"


class JudgeChoice(BaseModel):
    """One option in the judge picker: what it selects, and the line shown.

    Built here rather than at the MCP boundary because it is a read of graph
    state, and because the picker's whole value is the content of these lines —
    which is testable without an elicitation channel and untestable with one.
    """

    key: str
    agent_id: str
    name: str
    title: str


def _roster_title(name: str, agent: Agent | None) -> str:
    """One picker line: who this judge is, and when it last judged.

    The **name**, never the id — the id is a key and is not shown to anybody
    . A record written before the split has no name and reads as its own
    id, which is what it always was.
    """
    if agent is None:
        return f"{name} · approved, never claimed"
    used = (
        f"last used {agent.last_seen_at.date().isoformat()}"
        if agent.last_seen_at else "never used"
    )
    head = f"{name} · {used}"
    current = current_description(agent)
    if current is None or not current.text:
        return head
    room = _ROSTER_LINE_BUDGET - len(head) - 3
    if room < 12:
        return head
    text = " ".join(current.text.split())
    if len(text) > room:
        text = text[: room - 1].rstrip() + "…"
    return f"{head} · {text}"


async def judge_roster(storage: StorageBackend) -> list[JudgeChoice]:
    """The judges this graph offers to bind to, most recently used first.

    The union of the agent records and the approved ids, because the two answer
    different halves of *who could this be*: a record is a judge that has
    decided something, an approved id may be one the user admitted out of band
    and nothing has claimed yet.

    **Absorbed records are not offered.** A record whose id another has taken as
    a former id is no longer a judge in its own right, and offering it would
    rebuild the split that consolidating it repaired. The same goes for an
    approved id that is any live judge's — current or former — since it is that
    judge, listed once already.

    Ordering is load-bearing rather than cosmetic: the picker goes up on every
    bind, which is affordable only while the answer the user wants is the first
    line offered.
    """
    stored = await storage.list_agents()
    live = live_agents(stored)
    known = {alias for agent in stored for alias in agent_aliases(agent)}
    bare = [
        agent_id for agent_id in await storage.get_approved_agent_ids()
        if agent_id not in known
    ]

    used = [a for a in live if a.last_seen_at]
    unused = [a for a in live if not a.last_seen_at]
    # Two passes rather than one composite key: the date runs descending and the
    # name ascending, and Python's sort is stable, so sorting by the tiebreak
    # first and the primary key second gives both without inventing a key that
    # reverses one and not the other.
    used.sort(key=agent_name)
    used.sort(key=lambda agent: agent.last_seen_at, reverse=True)
    unused.sort(key=agent_name)
    bare.sort()

    return [
        JudgeChoice(
            key=f"{JUDGE_CHOICE_PREFIX}{agent.id}",
            agent_id=agent.id,
            name=agent_name(agent),
            title=_roster_title(agent_name(agent), agent),
        )
        for agent in (*used, *unused)
    ] + [
        JudgeChoice(
            key=f"{JUDGE_CHOICE_PREFIX}{agent_id}",
            agent_id=agent_id,
            name=agent_id,
            title=_roster_title(agent_id, None),
        )
        for agent_id in bare
    ]


def selected_judge_id(key: str) -> str | None:
    """The agent id a picker key selects, or None for *mint a new one*."""
    if key.startswith(JUDGE_CHOICE_PREFIX):
        return key[len(JUDGE_CHOICE_PREFIX):]
    return None


def approved_labels(
    approved: Sequence[str], agents: Sequence[Agent]
) -> list[str]:
    """The approved ids as the user would recognise them — names where known.

    Every refusal that lists what this graph approves goes through here, because
    an opaque id in a message meant for a person is worse than no message: it
    names the right judge in a form nobody can act on. An id belonging to no
    record is shown as itself, which is what it is.
    """
    by_alias = {
        alias: agent_name(agent)
        for agent in live_agents(agents)
        for alias in agent_aliases(agent)
    }
    return list(dict.fromkeys(
        by_alias.get(agent_id, agent_id) for agent_id in approved
    ))


def _unapproved_reason(handle: str, labels: Sequence[str]) -> str:
    """Why a claim was refused, written for the agent to put to the user.

    **The refusal is the prompt** (§2.2): there is no startup handshake, so this
    text is the whole mechanism by which a user ever hears that an agent wants
    an identity. It says what to run, because the user cannot be assumed to know
    the tool exists. `labels` are names rather than ids — an id in a
    message meant for a person names the right judge in a form nobody can act on.
    """
    known = (
        f"Judges already approved here: {', '.join(labels)}."
        if labels
        else "No judge has been approved in this graph yet."
    )
    return (
        f"'{handle}' is not an approved judge for this graph. {known} "
        f"Ask the user which judge you should be — the identity is theirs to "
        f"assign, and it is what lets a later review show that a *different* "
        f"agent made these decisions. They can admit one by answering the "
        f"prompt this call raises, or by running "
        f"`epimemer agents confirm <name>` against a server backend, or by "
        f"setting EPIMEMER_APPROVED_AGENTS before starting the server. "
        f"Do not pick one yourself."
    )


async def rename_judge(
    storage: StorageBackend,
    *,
    handle: str,
    name: str,
    same_judge: bool = False,
) -> dict:
    """Rename a judge, or say why it cannot be renamed yet.

    **The name layer is the only mutable one**, and this is the only thing
    that writes it. Renaming rewrites nothing: `judged_by` records the id, the
    id never changes, and every old row follows the new name because the name is
    resolved at read time. That is the opposite rule from a description, which
    is pinned per decision — *which judge is this* wants the name the user knows
    it by now, and *what did it claim to be then* wants the claim as it stood.

    **A name already taken is not an error, it is a question.** Two records that
    should be one is the commonest reason to be renaming at all — it is how
    `Opus 5 Judge` and `Opus 5` came to exist here — so a collision returns
    `same_judge_needed` and the caller asks. Answering yes consolidates: the
    judge holding the name absorbs the other, gaining its ids and its
    description history, and **nothing is deleted or rewritten**. Answering no
    leaves both alone.

    Not reachable from any MCP tool, for the reason approval is not: a handle an
    agent could rename is a handle an agent could point at another judge's
    history. Its callers are the elicitation prompt and the CLI, which are the
    same two channels that terminate at the user (§2.3).
    """
    name = name.strip()
    if not name:
        return {"status": "refused", "reason": "a judge needs a name."}

    agents = await storage.list_agents()
    agent = resolve_agent(agents, handle)
    if agent is None:
        return {
            "status": "refused",
            "reason": (
                f"No judge here answers to '{handle}'. Known: "
                f"{', '.join(agent_name(a) for a in live_agents(agents)) or 'none'}."
            ),
        }

    holder = name_holder(agents, name, excluding=agent.id)
    if holder is not None and not same_judge:
        return {
            "status": "same_judge_needed",
            "agent_id": agent.id,
            "name": agent_name(agent),
            "holder_id": holder.id,
            "reason": (
                f"'{name}' already names another judge here, with "
                f"{len(holder.descriptions)} description version(s) and "
                f"decisions of its own. If they are the same judge, say so and "
                f"they are consolidated; nothing is deleted and no decision is "
                f"rewritten. If they are not, choose a different name."
            ),
        }

    if holder is not None:
        merged = absorbing(holder, agent)
        await storage.upsert_agent(merged)
        return {
            "status": "consolidated",
            "agent_id": merged.id,
            "name": agent_name(merged),
            "former_ids": merged.former_ids,
            "message": (
                f"'{agent_name(agent)}' and '{name}' are now one judge. Its "
                f"decisions are found under either, and both description "
                f"histories are kept."
            ),
        }

    was = agent_name(agent)
    await storage.upsert_agent(renamed(agent, name))
    return {
        "status": "renamed",
        "agent_id": agent.id,
        "name": name,
        "previous_name": was,
        "message": (
            f"'{was}' is now '{name}'. Every decision it has already made "
            f"reads under the new name — the id it was recorded with has not "
            f"changed and nothing was rewritten."
        ),
    }


async def claim_agent(
    storage: StorageBackend,
    *,
    agent_id: str,
    description: str,
    approve_id: ApproveId | None = None,
    confirm_description: ConfirmDescription | None = None,
    confirmed_identity: str | None = None,
    now: datetime | None = None,
) -> tuple[dict, ResponseMeta]:
    """Bind this session to a judge, or say why it cannot be bound.

    Two gates, and they are deliberately different in strength:

    - **The identity is a hard gate.** A judge the user has not approved is
      refused, because admitting one would hand identity back to the agent and
      *"a different agent reviewed this"* would be self-asserted again (§2.2).
      Where a channel to the user exists, this asks first rather than refusing
      blind.
    - **The description is not.** New wording is recorded either way; it carries
      `confirmed_at` only where a human saw it. *Self-described, unconfirmed* is
      a different epistemic object, never collapsed into the same field (§2.4).

    **`agent_id` is a handle, not the key.** It is resolved
    against this graph's judges by name, by id, and by any id a judge used to be
    recorded under — so an agent may propose whatever the user calls this judge,
    and a returning one may pass back the id it was given. The key is in the
    response as `agent_id` and the handle is in `name`; the key is not for
    showing to anybody.

    **The gate guards assuming an identity, not only minting one** (revised
    2026-08-25). It used to ask only where `agent_id` was not already approved,
    which guarded the wrong act: an approved id then bound with no user
    involvement at all, and the refusal names what this graph approves — so a
    rejected guess returned the list that would have worked. Asking on every
    bind is what closes that, and it is affordable only because the question is
    a pick from a list rather than a name to type.

    **What the user answers with is a handle too**, so choosing an existing
    judge and typing the name of one land in the same place. That matters most
    on the free-text path, which is reached by asking for a *new* judge: typing
    the name of one that exists joins it rather than minting a second record
    with the same name, which is exactly how this graph's own split began.

    **`confirmed_identity` is the caller's cadence memo**, not a permission. It
    names the judge this session has already had confirmed for this graph, and
    the session-scoped state that answers it lives at the MCP boundary beside
    the binding itself. It suppresses the question only while that judge is
    still approved; a different judge, graph or session is a different question.
    A **changed description** is still put to the user, because the memo records
    an identity rather than a wording.

    **Where no channel to the user exists, an approved id still binds.** That is
    the `EPIMEMER_APPROVED_AGENTS` and `epimemer agents confirm` path (§2.3),
    which is user involvement that happened earlier rather than none — and
    refusing it would leave a client that cannot elicit unable to judge at all.

    Nothing here verifies the description. It is self-reported prose, exactly
    like a fact the agent ingests, and it must never be read as a credential.
    """
    at = now or datetime.now(timezone.utc)
    handle = agent_id.strip()
    description = description.strip()

    if not handle:
        return {
            "status": "refused",
            "reason": "an agent id is required; ask the user which one to use.",
        }, ResponseMeta()
    if not description:
        return {
            "status": "refused",
            "agent_id": handle,
            "reason": (
                "a self-description is required: it is what a later review "
                "reads to tell one judge from another."
            ),
        }, ResponseMeta()

    agents = await storage.list_agents()
    approved = await storage.get_approved_agent_ids()
    existing = resolve_agent(agents, handle)
    # An id the user seeded but nothing has claimed has no record to resolve, so
    # the handle stands in as its own key — which is what seeding one means.
    key = existing.id if existing is not None else handle

    confirmed_now = False
    memo_holds = (
        confirmed_identity is not None
        and key == confirmed_identity
        and key in approved
    )
    if memo_holds:
        # Asked and answered, this session, for this graph, for this identity.
        pass
    else:
        outcome = (
            await approve_id(handle, description) if approve_id is not None
            else ApprovalOutcome(channel_available=False)
        )
        if outcome.chosen:
            # The user picks or types, so what comes back is another handle —
            # not necessarily the one proposed, and not necessarily an id.
            # Recording the proposal would record a claim nobody approved.
            agents = await storage.list_agents()
            handle = outcome.chosen.strip()
            existing = resolve_agent(agents, handle)
            if existing is not None:
                key = existing.id
            elif handle in approved:
                # A judge the user seeded out of band and nothing has claimed:
                # the string *is* its key, because nothing else could be, and
                # minting a second one beside it would orphan the approval the
                # user actually gave.
                key = handle
            else:
                key = new_agent_id()
            approved = await approve_agent_ids(storage, [key])
            confirmed_now = True
        elif outcome.channel_available or key not in approved:
            # Declined refuses even a pre-approved judge; unavailable falls
            # through to the approved list, which is the only channel such a
            # client has.
            return {
                "status": "refused",
                "agent_id": handle,
                "approved_agent_ids": approved,
                "approved_judges": approved_labels(approved, agents),
                "reason": _unapproved_reason(
                    handle, approved_labels(approved, agents)
                ),
            }, ResponseMeta()

    agent = existing or Agent(
        id=key, name=handle, authorised_at=at, first_seen_at=at
    )
    current = current_description(agent)
    is_new_text = current is None or current.digest != description_digest(description)

    if is_new_text and not confirmed_now and confirm_description is not None:
        confirmed_now = await confirm_description(agent_name(agent), description)

    updated = agent.model_copy(update={
        # A record written before the split carries no name and reads as its own
        # id; naming it on the next claim makes that explicit rather than
        # leaving every reader to derive it.
        "name": agent_name(agent),
        "descriptions": with_description(
            agent.descriptions,
            text=description,
            at=at,
            confirmed_at=at if confirmed_now else None,
        ),
        "first_seen_at": agent.first_seen_at or at,
        "last_seen_at": at,
    })
    await storage.upsert_agent(updated)

    version: AgentDescription = updated.descriptions[-1]
    name = agent_name(updated)
    return {
        "status": "claimed",
        # The key, for `review(mode="by_agent")` and nothing else. `name` is
        # what to say to the user — an id is not for showing to anybody.
        "agent_id": updated.id,
        "name": name,
        "also_recorded_as": list(updated.former_ids),
        "digest": version.digest,
        # Stated rather than left to be inferred from `description_versions: 1`
        #. *This judge has no history* is what tells an agent it has just
        # created one instead of joining one, and an implication nobody reads
        # is not a signal.
        "new_agent": existing is None,
        "description_versions": len(updated.descriptions),
        "new_description": is_new_text,
        "description_confirmed": version.confirmed_at is not None,
        "approved_agent_ids": approved,
        "message": (
            f"Judging as '{name}'"
            + (
                " — a new judge, with no decisions before this session."
                if existing is None else "."
            )
            + (
                " The user confirmed this description."
                if version.confirmed_at is not None
                else " This description is self-reported and unconfirmed, "
                "which is what a later review will see."
            )
        ),
    }, ResponseMeta(nodes_returned=1)


def _reject_invalid_graph_name(name: str) -> tuple[dict, ResponseMeta] | None:
    """Return an error response for an illegal graph name, else None.

    The storage backends raise on these too (defence in depth); this layer turns
    it into a result the calling agent can read and act on.
    """
    try:
        validate_graph_name(name)
    except ValueError as exc:
        return {"status": "invalid_name", "message": str(exc)}, ResponseMeta()
    return None


async def list_graphs(storage: StorageBackend) -> tuple[dict, ResponseMeta]:
    """List available knowledge graphs."""
    databases = await storage.list_databases()
    current = storage.current_database

    result = {
        "graphs": databases,
        "active_graph": current,
    }
    meta = ResponseMeta(nodes_returned=len(databases))
    return result, meta


async def use_graph(
    name: str,
    storage: StorageBackend,
    *,
    confirm: bool = False,
    judge: JudgeRef | None = None,
    seed_agent_ids: Sequence[str] = (),
) -> tuple[dict, ResponseMeta]:
    """Switch to a different knowledge graph.

    If the graph doesn't exist and confirm is False, returns a confirmation
    prompt with similar graph names. If confirm is True, creates the graph.

    A session binds **one** judge, but approval is per graph, so a switch can
    leave that binding standing over a graph that never made it. Where it does,
    the result says so and the caller drops the binding — silently carrying a
    judge approved for graph A into every write on graph B is how attribution
    starts recording something nobody approved (§10.3).
    """
    invalid = _reject_invalid_graph_name(name)
    if invalid is not None:
        return invalid

    existing = await storage.list_databases()

    if name in existing:
        await storage.switch_database(name)
        return await _switched(
            storage, name, status="switched",
            message=f"Switched to graph '{name}'.", judge=judge,
            seed_agent_ids=seed_agent_ids,
        )

    # Graph doesn't exist
    if not confirm:
        similar = _similar_names(name, existing)
        result: dict = {
            "status": "confirm_create",
            "message": f"Graph '{name}' does not exist.",
            "existing_graphs": existing,
        }
        if similar:
            result["similar_graphs"] = similar
            result["message"] += f" Did you mean one of: {', '.join(similar)}?"
        result["message"] += " Call again with confirm=true to create it."
        return result, ResponseMeta()

    # Create by switching (SurrealDB creates databases on use)
    await storage.switch_database(name)
    return await _switched(
        storage, name, status="created",
        message=f"Created and switched to new graph '{name}'.", judge=judge,
        seed_agent_ids=seed_agent_ids,
    )


async def _switched(
    storage: StorageBackend,
    name: str,
    *,
    status: str,
    message: str,
    judge: JudgeRef | None,
    seed_agent_ids: Sequence[str] = (),
) -> tuple[dict, ResponseMeta]:
    """The result of landing on `name`, including whether the judge survived.

    Config-supplied approvals are applied to whatever graph this server lands
    on, and **before** the judge is re-checked — seeding afterwards would clear
    a judge the configuration was about to admit. On an embedded backend this is
    the only approval channel that reaches the running process,
    so a switch that skipped it would leave the user unable to admit a judge to
    the new graph at all.
    """
    if seed_agent_ids:
        await seed_approved_judges(storage, seed_agent_ids)
    result: dict = {
        "status": status,
        "active_graph": name,
        "message": message,
    }
    if judge is not None and not await judge_is_approved(storage, judge):
        result["judge_cleared"] = judge.agent_id
        result["message"] += (
            f" '{judge.agent_id}' is not approved in this graph, so it is no "
            f"longer bound as the judge — claim_agent again before writing."
        )
    return result, ResponseMeta()


async def delete_graph(
    name: str,
    storage: StorageBackend,
    *,
    confirm: bool = False,
) -> tuple[dict, ResponseMeta]:
    """Delete a knowledge graph permanently.

    Requires confirm=True. Refuses to delete the currently active graph.
    """
    invalid = _reject_invalid_graph_name(name)
    if invalid is not None:
        return invalid

    existing = await storage.list_databases()

    if name not in existing:
        similar = _similar_names(name, existing)
        result: dict = {
            "status": "not_found",
            "message": f"Graph '{name}' does not exist.",
            "existing_graphs": existing,
        }
        if similar:
            result["similar_graphs"] = similar
        return result, ResponseMeta()

    if name == storage.current_database:
        return {
            "status": "refused",
            "message": f"Cannot delete the active graph '{name}'. Switch to a different graph first.",
            "active_graph": name,
        }, ResponseMeta()

    if not confirm:
        return {
            "status": "confirm_delete",
            "message": f"This will permanently delete graph '{name}' and all its data. "
            "Call again with confirm=true to proceed.",
        }, ResponseMeta()

    await storage.delete_database(name)
    return {
        "status": "deleted",
        "message": f"Graph '{name}' has been permanently deleted.",
    }, ResponseMeta()
