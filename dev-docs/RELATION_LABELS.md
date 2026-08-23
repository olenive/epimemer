# Relation labels: a vocabulary with a record

**Status: designed, unbuilt (2026-08-24).** Written before any code, at the
user's direction, and written to be implemented from: §7 breaks the build into
four stages with the types, protocol methods, call sites and tests each one
needs. Where a section says "does", read "would".

Raised by `ISSUES.md` #74, which supersedes #69. #69 asked where a relation
merge's journal subjects go and the answer was *nowhere clean*; this document is
why — **the subject has no identity**, and giving it one dissolves #69 rather
than answering it.

**Stage 3 fixes a live defect** (#74's FC1): a relation-label pair an agent has
considered and declined is re-nominated on every `reflect`, for ever. That is
the treadmill #64 closed for fact pairs and never closed here.

§8 records what this deliberately does not do and §9 what was rejected; both
are worth reading before changing any of it.

**Revised 2026-08-24 after an independent review**, in four places, each kept in
§9 or in the section it changed rather than quietly corrected: record creation
moved off the CLI and onto every write path that names a label (§2.3, §9 #8),
because the CLI refuses embedded backends and an agent cannot run it either —
which left stage 3 unable to fix FC1 on the default development configuration;
descriptions journal their own kind rather than `ENRICHMENT` (§7.2, §9 #9);
suppression's permanence is now stated with the dual of §6's rule beside it
(§4.2); and FC2's claimed bound was withdrawn (§6).

---

## 1. What is missing, and how it was found

### 1.1 A label exists nowhere

`list_relations` **derives** the vocabulary: it scans the edges of active nodes
and groups by `(label, kind)`. There is no row, no id, no description anywhere.
A user-tier relationship label is a string repeated on every edge carrying it.

Three consequences, and they are the three open questions about relations:

- **Nothing to describe.** An agent choosing a label sees words and counts, and
  no way to learn what *this graph* means by each.
- **Nothing to name in a decision.** #69's question, unanswerable because the
  subject has no id.
- **Nothing to change but the edges.** "Renaming" means rewriting every edge
  carrying the label, in place, irreversibly.

### 1.2 What was measured first, because it changes the question

**Relation merges fire approximately never.** `memory`, the largest real graph,
holds **one** user-tier label — `published_by`, on 4 edges. A nomination needs
two same-`kind` labels at ≥0.9 cosine. Zero are possible.

**Labels do not affect retrieval.** `traversal_excluded` (`core/types.py`) is
the single function deciding whether a search expands through an edge, and it
reads `edge.type` and `edge.kind` — **never** `edge.label`. Outside
`list_relations` (counting), `link` (writing), and the engine-tier
`published_by` constant in `corroboration.py`, no query pipeline reads a
user-tier label at all.

So merging two labels changes a string that gets printed and nothing about which
nodes come back. **That is a different class of operation from a fact merge**,
which destroys a node and moves corroboration counts — and the two have been
sharing machinery and vocabulary as though they were the same thing.

**The consolidation is a port.** `relation_consolidation.py` arrived in
`4d3526b` (2026-07-23), the same commit that **deleted**
`tag_consolidation.py`, with the same cosine function and the same 0.9
threshold. The tag premise did not survive: a tag *was* the retrieval handle, so
`billing` and `billings` really were two buckets and a search for one missed the
other. A relation label is a handle for nothing.

**And there is no frame check.** `merge_facts` refuses cross-frame pairs;
`find_similar_relation_pairs` groups by `kind` alone, so two fictional universes
in one graph pool their vocabularies and are judged on string similarity. The
worked example, from the user: a servant *works for* a master in a culture with
no employment relation, while elsewhere in the same universe a corporation
formally *employs* an on-call consultant who does very little work.
Near-identical strings, opposite meanings, and the nominator sees only strings.

### 1.3 FC1, the live defect

`find_similar_relation_pairs` re-derives from scratch on every `reflect` and
**records nothing about declines.** Reflection nominates; a merge happens only
if the agent calls `apply_reflection(relation_merges=[…])`. Declining means not
making that call, so there is no record anywhere that the question was asked.

Next session, a different agent scans the same edges, embeds the same strings,
gets the same cosine, and is asked the same question. For ever.

**Getting it right is what causes the loop.** Accepting the merge makes one
label stop existing, so the pair can never be nominated again — accepting is
self-suppressing and declining is not. The graph therefore applies quiet
pressure toward the wrong answer, on a fresh agent each time who cannot see the
previous refusals.

**#64 closed exactly this for fact pairs**, and its measurement is the shape of
the problem: of eighteen pairs nominated on `memory`, five merged and
**thirteen were declined and vanished**. The fix was the `ASSESSED` edge as a
suppression index. Relation labels got no equivalent, and could not: that edge
runs **between two nodes**, and `works_for` and `employed_by` are not nodes.

---

## 2. The record

### 2.1 The type

```python
class RelationLabel(BaseModel):
    """The vocabulary entry behind a user-tier edge's `label`.

    Not a node, deliberately (§9 #1): a label is vocabulary, not knowledge, and
    a node enters search, embeddings, reflection and merging — every one of
    which would then be answering questions about the *words* the graph uses.
    `Metacontext` is the precedent and the shape: a named, described thing that
    lives beside the graph rather than in it.
    """

    id: str = Field(default_factory=_new_id)
    # What edges actually carry. The join key to `NodeEdge.label`, which keeps
    # its string — edges are not re-pointed at ids (§9 #2).
    name: str
    kind: Literal["relationship", "attribution"] = "relationship"
    # Advisory prose an agent reads before coining (§3). Empty means
    # **undescribed**, which is a true and useful state, and is why this field
    # ships in stage 1 though nothing writes it until stage 2.
    description: str = ""
    # **The coiner, and never the describer.** A later agent may describe this
    # label, judge it against another, or deprecate it, and none of that
    # restamps this field — those are journalled in their own right. A record
    # created by anything other than `link` (§2.3) carries no judge at all,
    # because nobody is claiming to have introduced the label.
    judged_by: JudgeRef | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
```

**`name`, not `content`.** `Metacontext.content` is named for prose — *"Real
historical events"*. A relation label's name is a token that edges carry
verbatim, and calling it `content` would invite someone to write a sentence in
it. `label` was rejected as stuttery against `NodeEdge.label`, which it joins to.

**No `status` until stage 4**, and this is the rule rather than an omission. A
status with one reachable value is not a state, it is a constant — the same
reasoning `DecisionKind`'s *"every member has a writer"* applies to enum
members. Nothing retires a label until deprecation exists, so `status` arrives
with the thing that writes it.

**No `ValueSignal`.** `Metacontext` carries one because metacontexts consolidate
during reflection. A label is not ranked, retrieved, or judged for importance.

### 2.2 Where it lives

One table per graph, beside `metacontext`, on **both** backends — the full
protocol, no capability flags, per the standing rule.

```python
# storage/protocol.py, a new section after the metacontext reads

async def store_relation_label(self, label: RelationLabel) -> str:
    """Store a relation label, or update its description and metadata.

    **Those are the only fields an update may move.** `name` cannot change,
    because edges join to it by string (§2.4); `kind` cannot, because the kind
    is in force on the edges and this record only mirrors it (§7.2). Said here
    rather than left to the callers, or the next one discovers the update path
    is wider than the design.
    """

async def get_relation_label(
    self, name: str, kind: str
) -> RelationLabel | None:
    """The record for one label, or None if it has none.

    `None` is the ordinary answer on any graph that predates this, and every
    caller must degrade to today's behaviour rather than refuse (§2.3).
    """

async def query_relation_labels(self) -> list[RelationLabel]:
    """Every relation label record in the active graph."""
```

Uniqueness is `(name, kind)` within a graph, enforced by the writer rather than
by an index on the in-memory backend — `store_relation_label` fetches first.
SurrealDB gets a unique index on `(name, kind)`.

Viz gets `viz_list_relation_labels(database)` in stage 2, following
`viz_list_metacontexts` exactly, including the never-expose rule: viz reads are
**never** registered as MCP tools and never imported in `epimemer/mcp/`.

### 2.3 Creation, and the graphs that already exist

**Every write path that names a label creates-or-fetches its record.** Three of
them: `link` when a label is coined, `describe_relation` when one is described,
and `relation_verdicts` when a pair is judged. Each already writes and already
carries a judge, so the record comes into existence at exactly the moments an
agent touches the vocabulary, and no path can be blocked waiting for one.

**Only `link` records a judge**, because only `link` coins. A record created by
`describe_relation` or by a verdict carries `judged_by=None`: the agent
describing or judging a label is not claiming to have introduced it, and the
description and the verdict are journalled in their own right.

**A CLI backfill is a convenience, not a precondition.**
`epimemer relations backfill` reads the same `related_edges_of_active_nodes`
scan `list_relations` uses and writes a judge-less record per distinct
`(label, kind)`, so a long-lived graph can get its vocabulary in one go. It must
not be the *only* way a pre-existing label gets a record, and the reason is
concrete: **the CLI refuses embedded backends** — `mem://`, `file://`,
`surrealkv://` and the in-memory store all live inside the server process, so a
second connection is a separate store (`cli.py`, `is_embedded_url`). That is the
default development configuration. **And an agent cannot run the CLI at all** —
it is the user's command. A design whose only remedy is a command the agent
cannot issue, on a backend where the command refuses, has no remedy.

**Every read tolerates a missing record.** A label without one is not an error;
it is a graph nobody has touched since this shipped, and every reader falls back
to today's behaviour. This is the fail-safe direction throughout: the worst
outcome is that FC1's suppression does not apply to a pair nobody has judged,
which is exactly what happens now.

### 2.4 Identity is the id; the name does not move

`NodeEdge.label` keeps the string and joins by name, so **renaming a label would
break the join unless every edge were rewritten** — the bulk relabel this whole
design is trying to stop needing. Stage 1 therefore does not support renaming,
and no stage below adds it.

If renaming is ever built, the history belongs **on the record**: one entry per
rename rather than one per edge, and it survives a rename that touched zero
edges, which a per-edge log cannot (#74's FC4).

---

## 3. Descriptions

### 3.1 Advisory prose, not a schema

One description per label per graph, because the same words mean different
things in different graphs — which is the entire content of §1.2's example.

**It will not partition the servant case from the consultant case, and it does
not need to.** It is prose an agent reads, free to say *"in the Court context
this means X; for corporate contracts use Y."* Making it enforceable would make
it a schema; describing individual **edges** rather than the shared label is the
step that would make this a hypergraph. Neither is proposed (§8).

### 3.2 Where an agent meets it

- `list_relations` returns `description` beside `label`, `kind` and `count`.
- `link` returns the description of the label it reused, so an agent coining
  against an existing name is told what that name already means in this graph
  **at the moment it matters**, rather than having to have looked first.
- A write tool, `describe_relation(name, kind, description)`, journalled.

This is the half that pays. It moves the intervention from repair to
prevention: an agent picking from a described vocabulary never coins the fourth
synonym, and no merge is needed to clean up after it.

---

## 4. Suppressing a decline (FC1)

### 4.1 The verdict

Mirrors `apply_reflection(similarities=[…])`, which is the same problem one
layer down:

```
relation_verdicts: [{pair: [label_a, label_b], kind: str,
                     verdict: "distinct" | "synonymous", because: str}]
```

- **`distinct`** — different relationships that look alike. The servant/
  consultant case.
- **`synonymous`** — the same relationship written two ways.

**Both suppress, and `synonymous` acts on nothing until stage 4.** Recording
*"yes, these are synonyms, and I am not merging them"* is a real judgment, and
leaving it unrecordable would be FC1 again for the affirmative answer. When
stage 4 lands it can act on standing verdicts rather than re-asking.

`because` is required on both, for #64's reason: a verdict with no reason marks
the pair judged, so the next agent skips it without knowing whether it was
examined or waved through.

Refused, per entry, in the shape `similarities_refused` already uses: a label
no edge in this graph carries, a pair whose two sides differ in `kind`, and a
pair already carrying **this agent's** identical verdict — a retry is not a
second opinion.

**A label with no record is not refused; it gets one.** The verdict creates it,
judge-less, per §2.3. Refusing here and pointing at the CLI would leave the
defect this stage exists to close unfixable on every embedded backend, which is
the default development configuration.

**A different judge recording a verdict the pair already carries has
confirmed, not decided**, and takes the established shape rather than a new one:
`_journal_pair_judgment(created=False)` writes a confirmation row citing the
oldest decision for that pair. That is what stops a third agent doing the work a
fourth time — §1's defect one layer up, and the same reason it exists for node
pairs.

### 4.2 The suppression read

A small append-only table, **not** a field on the label record:

```python
class RelationVerdict(BaseModel):
    id: str = Field(default_factory=_new_id)
    # Sorted, so (a, b) and (b, a) are one pair rather than two.
    label_ids: list[str]
    verdict: Literal["distinct", "synonymous"]
    because: str
    judged_by: JudgeRef | None = None
    decided_at: datetime = Field(default_factory=_now)
```

```python
async def record_relation_verdict(self, verdict: RelationVerdict) -> str: ...
async def judged_relation_pairs(self) -> set[tuple[str, str]]: ...
```

Storing the pair **on the label record** was rejected: it is mutable state held
twice, once on each side, free to disagree — #54, #55 and #56 for the fifth
time.

`find_similar_relation_pairs` gains the filter. It resolves each label name to a
record, drops any pair whose two ids are already judged, and **suppresses
nothing for a pair either of whose sides has no record** — fail-safe, as §2.3
requires.

This is a **denormalised suppression index and is legitimate as one** for
exactly `similarity_decisions.py`'s stated reason: it is immutable and
append-only, so it cannot drift from the journal row that also records it. The
journal is the audit record; this is what the sweep reads without a journal
query.

**Suppression is permanent, and that is inherited deliberately rather than by
accident.** The fact-pair layer decided it in as many words — *"the `assessed`
edge stays and the pair stays out of every future nomination: the agent has now
judged it twice, and re-offering it would restart the treadmill"* — and #68's
retraction, which fixed the *other* half of that problem, left suppression
explicitly untouched. So a wrong `distinct` here silences a pair for good.

**That is the dual of §6's rule and belongs beside it: a suppression with no
retraction makes every wrong decline permanent by construction**, exactly as a
sweep with no memory makes every right decline futile. Both are stated; neither
is fixed here.

**If a retraction is ever built for labels, #68's one-directional shape must not
be copied across unexamined.** It is one-directional for a reason specific to
facts: a false unification manufactures agreement — the worst failure this
system can produce — while a withdrawal merely under-counts, so #52's direction
settles it. **Neither failure exists here.** Nothing corroborates on a label, so
a wrong `synonymous` invents no support and a wrong `distinct` costs no count.
The asymmetry that justifies the fact layer's terminal retraction is simply
absent, and a symmetric retraction may well be right.

### 4.3 The journal row

`DecisionKind.RELATION_VERDICT`, with `subject_ids = [label_a.id, label_b.id]`.

**This is where #69 resolves.** The subjects are ids of records that exist in
this graph, so `review()` dereferences them like any other row, `node.notes`
surfaces nothing spurious, and no field acquires a second namespace.

Its own kind rather than `SIMILARITY`: review *selects* on kind, and a reviewer
auditing judgments about claims does not want judgments about vocabulary.

`DecisionKind`'s docstring names `relation_merge` as pending on #69 — stage 3
updates that pointer to this document, and stage 4 adds the member if merging
survives.

---

## 5. Deprecation, if merging survives at all — stage 4, undecided

Not settled, and deliberately last. Recorded here so the shape is known.

**Because labels do not affect retrieval (§1.2), deprecation needs no rewrite.**
Marking `employed_by` as an alias of `works_for` sets `status` and an
`alias_of` id on the record. Existing edges keep their own wording;
`list_relations` shows the canonical set with aliases folded underneath; `link`
steers new coinings to the survivor. Nothing is destroyed, so nothing needs
reversing.

That is the whole argument for preferring it to `relation_merges`: **undo you
never need beats undo that works.** A lossy irreversible bulk rewrite becomes a
reversible annotation.

**If `relation_merges` is kept** — for a genuine typo fix across the graph, say
— then it must capture the pre-rewrite partition at the moment it runs, by
`REVIEW_MODE.md` §7.1's rule. Any operation collapsing many into one destroys
the partition, and the partition exists only while the operation is happening.
That rule is not about nodes; it is about collapse.

`reverse_merge`'s three caveats transfer unevenly, which is worth knowing before
reasoning from the parallel:

| `reverse_merge` caveat | Transfers? |
|---|---|
| Capture or lose; retention bounded by `merge_undo_depth` | **Exactly.** Which edges carried the label exists only during the rewrite |
| Refuse when anything accreted onto the survivor | **Weakly.** A label reversal deletes nothing and nothing points at a label. The only accretion is `get_relation_kind`, where a later edge inherited the surviving label's kind |
| The hard delete, and its narrow justification | **Not at all.** Reversing a relabel writes a string back |

---

## 6. Futile cycles

From #74, and the reason stage 2 precedes stage 3.

- **FC1** — the nomination treadmill. §1.3. Fixed by stage 3.
- **FC2 — deprecate ↔ un-deprecate.** Reversible deprecation lets two agents
  alternate. Cheap in the graph, since nothing is rewritten — but **the journal
  is append-only, so a futile cycle permanently inflates the record**, and
  `review`'s difficulty signals keep resurfacing the pair. **Stage 3's
  suppression does not bound this**, and an earlier draft said it did:
  suppression stops the *nomination*, and deprecate/un-deprecate are direct
  calls that need no nomination to happen. The precedent that does apply is
  `merge_cycle_limit` (`REVIEW_MODE.md` §7.8) — a refusal counted from lifecycle
  state that already exists. It costs nothing until stage 4, which is where it
  would be built.
- **FC3 — nudge, comply, re-coin, nudge.** A coin-time nudge plus a later agent
  that genuinely needs the distinction is a loop with no exit. **The description
  is the escape hatch**, which is why stage 2 must land before stage 3 and
  before any nudging: without it, neither the nudge nor the agent being nudged
  can tell a synonym from a distinction.
- **FC4 — rename ping-pong.** With per-edge capture each pass appends to every
  affected edge, O(edges × cycles); on the record it is O(cycles). §2.4.

**The general rule, worth checking against any new nominator: a sweep
recomputed from current state that records no declines is a futile cycle by
construction.** It re-offers what was already refused and cannot know it is
doing so. That is #64's lesson stated once rather than rediscovered per feature.

---

## 7. Build order

Each stage is shippable, each pays on its own, and nothing earlier is thrown
away by a later one.

| # | Stage | Why here |
|---|---|---|
| 1 | `RelationLabel`, the three protocol methods on both backends, `link` create-or-fetch, `epimemer relations backfill` | Additive; changes no behaviour. Every later stage needs identity to exist first, and this is the only stage that can be built without deciding anything else |
| 2 | `description` surfaced in `list_relations` and in `link`'s response; `describe_relation` tool with `DecisionKind.RELATION_DESCRIPTION`; `viz_list_relation_labels` | **The half that pays**, and independent of FC1. Must precede stage 3 (FC3) |
| 3 | `relation_verdicts` in `apply_reflection`, `RelationVerdict` + two protocol methods, the filter in `find_similar_relation_pairs`, `DecisionKind.RELATION_VERDICT` | **Fixes the live defect**, on every backend and without the CLI (§2.3). Needs stage 1 for ids and stage 2 so a verdict is made against a described vocabulary. Resolves #69 |
| 4 | Deprecation / `alias_of` / `status`, replacing `relation_merges` | **Undecided** (§5). Last, because #74 has not settled whether merging survives at all |

**Urgency is low and stated as such.** FC1 causes zero harm today: one label in
the largest real graph means zero nominations are possible. This is a defect
waiting for the vocabulary to grow, which is the argument for building it
properly rather than quickly.

### 7.1 Stage 1

**Types** — `RelationLabel` in `core/types.py`, beside `Metacontext`.

**Protocol** — the three methods of §2.2, plus implementations in
`storage/memory.py` (a `dict[str, RelationLabel]` on `_GraphStore`) and
`storage/surrealdb_adapter.py` (a `relation_label` table in `_setup_schema`,
unique index on `(name, kind)`), plus the passthrough in
`visualization/instrumented_storage.py` — whose guard test compares
**signatures**, not method names.

**Call site** — `tools.link`: after `resolved_kind` is settled, fetch or create
the record with `judged_by=judge`. One extra read on the common path, and the
write only on a label's first use.

**CLI** — `epimemer relations backfill [--graph]`, reusing
`related_edges_of_active_nodes`. A convenience for a long-lived graph, never a
precondition (§2.3): it inherits the CLI's embedded-backend refusal, and no
stage may depend on it.

**Tests** (both backends via the `storage` fixture):

1. Coining a new label creates exactly one record, carrying the coining judge.
2. Coining an existing label creates no second record and does not restamp the
   judge — step 3's re-recorded-edge rule.
3. `(name, kind)` is unique; the same name under a different kind is a
   different record.
4. Backfill over a graph written before this creates one record per distinct
   `(label, kind)` and none for engine-tier edges.
5. Backfill is idempotent.
6. Backfill writes no judge, and a later `link` does not adopt one either.
7. A graph with no records answers `get_relation_label` with `None` and nothing
   raises.
8. Records are per graph: a label in one graph is invisible from another.
9. Every record a label can acquire is reachable without the CLI — a test that
   coins, describes and judges on an in-memory store and finds three records,
   since that backend is exactly where the CLI refuses.

### 7.2 Stage 2

**Call sites** — `tools.list_relations` joins each derived `(label, kind)` to
its record and returns `description` (empty when absent). Counts stay derived
from edges: they are scoped to active nodes for a reason (#14 step 2) and a
stored count would drift. `tools.link` returns `description` when it reused an
existing label.

**New tool** — `describe_relation(name, kind, description)`; creates the record
if the label has none (§2.3); refuses a label no edge carries; refuses a `kind`
change, since the kind is in force on the edges and the record only mirrors it.

**It journals `DecisionKind.RELATION_DESCRIPTION`, not `ENRICHMENT`**, and the
distinction is §4.3's own argument applied one section earlier: `ENRICHMENT` is
reflect's *topic* enrichment, and a reviewer auditing changes to what the graph
**claims** does not want prose about what the graph's **words mean** mixed in.
The first draft wrote `ENRICHMENT` on the grounds that enriching is *what it
is*, which was the right verb and the wrong side of the line. The member ships
in the same commit as this writer, per `DecisionKind`'s drift guard.

**Frontend** — a `relation_label` row, and `EDGE_MEANINGS` untouched: this adds
no `EdgeType`, which is what #55 keeps catching.

**Tests:**

1. `list_relations` returns the description; empty for an undescribed label.
2. `list_relations` still answers correctly on a graph with no records at all.
3. `link` reusing a label returns its description.
4. `describe_relation` on a label with no record creates one, carrying **no**
   judge, and refuses only a label no edge in this graph carries.
5. `describe_relation` refuses a `kind` change.
6. Re-describing replaces the text and journals a second row; the first is not
   edited. The record's `judged_by` is unchanged by any of it.
7. A description journals `RELATION_DESCRIPTION`, and `review(mode="all")`
   over a graph with both shows it does not arrive under `ENRICHMENT`.
8. The viz read never appears in `list_tools()` — the never-expose guard, in
   the shape the existing viz guard test uses.

### 7.3 Stage 3

**Types** — `RelationVerdict`; `DecisionKind.RELATION_VERDICT`, which the
drift guard in `tests/mcp/test_decision_journal.py` requires to have a writer in
the same commit.

**Protocol** — `record_relation_verdict`, `judged_relation_pairs`.

**Call sites** — `apply_reflection` gains `relation_verdicts`, applied **before**
`relation_merges`, mirroring §10.2's ordering rule: a verdict is about the
vocabulary as the agent saw it, and a merge earlier in the same batch would make
one side vanish. `find_similar_relation_pairs` gains the suppression filter.

**Tests:**

1. A declined pair is not nominated again by the next `reflect` — **the
   regression test for FC1**, and it must be shown to fail without the filter.
2. A `synonymous` verdict suppresses too.
3. A verdict on a pair whose labels have no records **creates them**,
   judge-less, and suppresses — run on the in-memory backend, because that is
   where the CLI cannot reach and where an earlier draft of this design left
   FC1 unfixable.
4. `(a, b)` and `(b, a)` are one pair.
5. `because` is required; a blank one is refused per entry, with the rest of the
   batch applied.
6. An identical verdict from the **same** judge is refused as a retry, not
   stored as a second opinion.
6b. An identical verdict from a **different** judge writes a confirmation row
   citing the oldest decision for that pair, per `_journal_pair_judgment` —
   the established shape, not a new one.
7. The journal row names both label ids, and `review()` dereferences them.
8. Verdicts are per graph.
9. A verdict on a label pair does not appear in any node's notes.
10. Suppression survives everything: a pair judged `distinct` is never
    nominated again, including after either label is described or re-used.
    **Permanent by design** (§4.2), and the test says so rather than leaving a
    later reader to decide it is a bug.

---

## 8. What this deliberately does not do

- **It does not describe edges.** A description belongs to the shared label, not
  to edge #4712. Per-edge meaning is the step that would make this a hypergraph.
- **It does not enforce anything.** The description is prose an agent reads. A
  vocabulary the system polices is a schema, and this system's whole shape is
  that agents judge and the graph records.
- **It does not re-point edges at label ids.** `NodeEdge.label` keeps its
  string.
- **It does not add renaming**, and §2.4 says what would be required.
- **It does not settle whether relation merging should exist.** #74 raises the
  question; stage 4 is where it would be answered.
- **It does not add the missing frame check** to the nominator (§1.2). That is
  worth its own scoping: labels are not in a metacontext — the endpoint nodes
  are — so "same frame" means reasoning about the metacontexts of the edges
  carrying each label. It would stop the servant/consultant pair being proposed
  at all, and it is not a substitute for stage 3: two genuinely distinct labels
  in one frame still recur.

---

## 9. Rejected, and why

1. **A label as a node.** It would enter search, embeddings, reflection,
   archival and merging — the whole machine would start answering questions
   about the words the graph uses. `Metacontext` is the precedent for a
   described thing that is deliberately not a node.
2. **Edges pointing at label records by id.** A migration over every user-tier
   edge, for no gain: nothing traverses on the label, so the indirection buys
   nothing and costs the ability to read an edge without a join.
3. **A `subject_labels` field on `DecisionRecord`** — #69's second option. One
   field with two namespaces, which is the tell this codebase names repeatedly.
   Stage 3 makes it unnecessary: the subjects are ids.
4. **The endpoint node ids as a relation merge's subjects** — #69's third
   option. It satisfies *ids only* by making the row surface under nodes the
   decision was not about: `node.notes` would show *"somebody merged two
   relation labels"* against a topic nobody judged. Note that `link` **does**
   journal `[src_id, dst_id]`, and correctly — there the endpoints really are
   what the decision was about.
5. **Suppression stored on the label record.** Mutable state held twice, once
   per side, free to disagree. #54, #55, #56.
6. **A `ValueSignal` on the record.** Nothing ranks or retrieves a label.
7. **`status` in stage 1.** A status with one reachable value is a constant, not
   a state.
8. **Refusing a verdict on an unrecorded label and pointing at the CLI.** The
   first draft did this, and it was a dead end: the CLI refuses embedded
   backends — the default development configuration — and an agent cannot run
   it in any case. *A remedy the agent cannot issue, on a backend where it
   refuses, is not a remedy.* Create-or-fetch at verdict time (§2.3) removes
   the refusal rather than improving its message.
9. **Journalling descriptions as `ENRICHMENT`.** The first draft did this on
   the grounds that enriching is what it is — the right verb, the wrong side of
   §4.3's line, which exists to keep judgments about claims and judgments about
   vocabulary separately selectable.
10. **Fixing FC1 standalone, keyed on the label strings.** It looked like the
   small option and is not: declining is a decision, every decision leaves a
   journal row, and `subject_ids` holds node ids — so it would have forced
   rejection 3 as a side effect. It would have failed safe (a stale string
   suppresses nothing, which is today's behaviour), and that is the only thing
   in its favour.
