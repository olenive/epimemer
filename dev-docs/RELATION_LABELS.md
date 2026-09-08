# Relation labels: a vocabulary with a record

A user-tier relationship label (`works_for`, `published_by`) used to exist
only as a string repeated on every edge carrying it. This document is the
design that gives each label a record: something to describe, something a
decision can name, and something a decline can be recorded against. Labels
are never rewritten; a vocabulary converges through descriptions and
verdicts, not merges.

---

## 1. The problem

### 1.1 A label existed nowhere

`list_relations` derives the vocabulary by scanning the edges of active nodes
and grouping by `(label, kind)`. Without a record there is nothing to
describe, so an agent choosing a label sees words and counts and no way to
learn what *this graph* means by each; nothing to name in a decision, so a
journal row about a pair of labels has no subject; and nothing to change but
the edges, so any rename is an irreversible bulk rewrite.

### 1.2 What labels affect, and what they do not

**Labels do not affect retrieval.** `traversal_excluded` in `core/types.py`
is the single function deciding whether a search expands through an edge, and
it reads `edge.type` and `edge.kind`, never `edge.label`. Outside
`list_relations` (counting), `link` (writing), and the engine-tier
`published_by` constant in `corroboration.py`, no query pipeline reads a
user-tier label. So consolidating two labels changes a string that gets
printed and nothing about which nodes come back. That is a different class of
operation from a fact merge, which destroys a node and moves corroboration
counts, and the two should not share machinery.

**Relation merges fire approximately never on real graphs.** The largest real
graph held one user-tier label for its first months. A nomination needs two
same-`kind` labels at 0.9 cosine or above. That is either evidence the
problem does not occur, or evidence that nothing encourages `link` with a
user relation so the vocabulary never grows enough to need consolidating; the
two readings point opposite ways and have not been distinguished.

**Near-identical strings can mean opposite things.** The user's example: a
servant *works for* a master in a culture with no employment relation, while
elsewhere in the same universe a corporation formally *employs* an on-call
consultant who does very little work. The nominator sees only strings.

### 1.3 The decline treadmill

`sweep_similar_relation_pairs` re-derives from scratch on every `reflect`.
Without a record of declines, a pair an agent considered and declined is
re-nominated next session, to a different agent, for ever. Getting it right
is what causes the loop: accepting a merge makes one label stop existing, so
accepting is self-suppressing and declining is not, and the graph applies
quiet pressure toward the wrong answer.

The `assessed` edge closes exactly this for fact pairs. It could not serve
here because it runs between two nodes, and `works_for` and `employed_by` are
not nodes. §4 is the equivalent for labels.

---

## 2. The record

### 2.1 The type

```python
class RelationLabel(BaseModel):
    """The vocabulary entry behind a user-tier edge's `label`.

    Not a node, deliberately (§9): a label is vocabulary, not knowledge, and
    a node enters search, embeddings, reflection and merging, every one of
    which would then be answering questions about the *words* the graph uses.
    `Metacontext` is the precedent and the shape: a named, described thing
    that lives beside the graph rather than in it.
    """

    id: str = Field(default_factory=_new_id)
    # What edges actually carry. The join key to `NodeEdge.label`, which keeps
    # its string; edges are not re-pointed at ids (§9).
    name: str
    kind: Literal["relationship", "attribution"] = "relationship"
    # Advisory prose an agent reads before coining (§3). Empty means
    # undescribed, which is a true and useful state.
    description: str = ""
    # The coiner, and never the describer. A later agent may describe this
    # label or judge it against another, and neither restamps this field;
    # those are journalled in their own right. A record created by anything
    # other than `link` (§2.3) carries no judge at all.
    judged_by: JudgeRef | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
```

**`name`, not `content`.** `Metacontext.content` is named for prose. A
relation label's name is a token that edges carry verbatim, and calling it
`content` would invite someone to write a sentence in it. `label` would
stutter against `NodeEdge.label`, which it joins to.

**No `status`.** A status with one reachable value is a constant, not a
state, the same reasoning `DecisionKind` applies to enum members. Nothing
retires a label, so the field would arrive with deprecation if that is ever
built (§5).

**No `ValueSignal`.** A label is not ranked, retrieved, or judged for
importance.

### 2.2 Where it lives

One table per graph, beside `metacontext`, on both backends: the full
protocol, no capability flags.

```python
async def store_relation_label(self, label: RelationLabel) -> str:
    """Store a relation label, or update its description and metadata.

    Those are the only fields an update may move. `name` cannot change,
    because edges join to it by string (§2.4); `kind` cannot, because the
    kind is in force on the edges and this record only mirrors it (§7.2).
    """


async def get_relation_label(self, name: str, kind: str) -> RelationLabel | None:
    """The record for one label, or None if it has none. Every caller
    degrades to the recordless behaviour rather than refusing (§2.3)."""


async def query_relation_labels(self) -> list[RelationLabel]:
    """Every relation label record in the active graph."""
```

Uniqueness is `(name, kind)` within a graph. `recorded_relation_label` is the
pure merge both backends write through: `id`, `created_at` and `judged_by`
come from the record already there, and a blank description never
overwrites prose, so the coiner-never-the-describer rule is structural rather
than a convention every caller has to remember. The SurrealDB write is
`UPSERT … CONTENT … WHERE name = $name AND kind = $kind`, keyed on the
natural pair rather than on `uid`, so a create-or-fetch caller that lost a
race writes over the same row instead of hitting the unique index.

Viz reads the table through `viz_list_relation_labels(database)`, following
`viz_list_metacontexts`, including the never-expose rule: viz reads are never
registered as MCP tools and never imported in `epimemer/mcp/`.

### 2.3 Creation, and the graphs that already exist

**Every write path that names a label creates-or-fetches its record.** Three
of them: `link` when a label is coined, `describe_relation` when one is
described, and `relation_verdicts` when a pair is judged. Each already writes
and already carries a judge, so the record comes into existence at exactly
the moments an agent touches the vocabulary, and no path can be blocked
waiting for one. An enumeration of write paths is a claim that ages; the
test in §7.1 that asserts every record is reachable without the CLI is what
catches a fourth.

**Only `link` records a judge**, because only `link` coins.

**The CLI backfill is a convenience, not a precondition.** `epimemer
relations backfill` reads the same `related_edges_of_active_nodes` scan
`list_relations` uses and writes a judge-less record per distinct `(label,
kind)`, so a long-lived graph can get its vocabulary in one go. It must not
be the only way a pre-existing label gets a record: the CLI refuses embedded
backends, which is the default development configuration, and an agent
cannot run it at all (`DEVELOPER_GUIDE.md`, *The CLI is not a remedy an
agent can reach*). Its refusal message says plainly that nothing is lost.

**Every read tolerates a missing record.** A label without one is a graph
nobody has touched since records existed, and every reader falls back to the
recordless behaviour. The worst outcome is that suppression does not apply to
a pair nobody has judged.

### 2.4 Identity is the id; the name does not move

`NodeEdge.label` keeps the string and joins by name, so renaming a label
would break the join unless every edge were rewritten, the bulk relabel this
design exists to stop needing. Renaming is not supported. If it is ever
built, the history belongs on the record, one entry per rename rather than
one per edge, because it must survive a rename that touched zero edges.

---

## 3. Descriptions

### 3.1 Advisory prose, not a schema

One description per label per graph, because the same words mean different
things in different graphs. It will not partition the servant case from the
consultant case, and it does not need to: it is prose an agent reads, free to
say *"in the Court context this means X; for corporate contracts use Y."*
Making it enforceable would make it a schema; describing individual edges
rather than the shared label would make this a hypergraph. Neither is done
(§8).

### 3.2 Where an agent meets it

- `list_relations` returns `description` beside `label`, `kind` and `count`,
  always carrying the field (empty when absent), because the row exists
  either way.
- `link` returns the description of the label it reused, so an agent coining
  against an existing name is told what that name already means at the
  moment it matters. It omits the key rather than sending an empty one: `""`
  reads as *this graph means nothing by the word*, absence as *nobody has
  said*.
- `describe_relation(name, kind, description)` writes one, journalled.

This is the half that pays. It moves the intervention from repair to
prevention: an agent picking from a described vocabulary never coins the
fourth synonym. Descriptions solve comprehension, not convergence: nothing
pulls ten words for one idea together except `list_relations` sorting by
usage count, which is a weak force. That is the strongest argument for
building deprecation (§5).

---

## 4. Suppressing a decline

### 4.1 The verdict

Mirrors `apply_reflection(similarities=[…])`, the same problem one layer down:

```
relation_verdicts: [{pair: [label_a, label_b], kind: str,
                     verdict: "distinct" | "synonymous", because: str}]
```

- **`distinct`**: different relationships that look alike.
- **`synonymous`**: the same relationship written two ways.

Both suppress. Recording *"yes, these are synonyms, and I am not merging
them"* is a real judgment, and leaving it unrecordable would be the
treadmill again for the affirmative answer. `synonymous` acts on nothing (§5).

`because` is required on both: a verdict with no reason marks the pair
judged, so the next agent skips it without knowing whether it was examined or
waved through.

Refused, per entry, in the shape `similarities_refused` uses: a missing
`kind` (copy it from the nomination; defaulting it would make the stale-kind
refusal blame the agent for a value the call invented), a label no edge in
this graph carries, a pair whose two sides differ in `kind`, and a pair
already carrying this agent's identical verdict, since a retry is not a
second opinion.

**A label with no record is not refused; it gets one**, judge-less, per §2.3.

**A different judge recording a verdict the pair already carries has
confirmed, not decided.** `_journal_pair_judgment(created=False)` writes a
confirmation row citing the oldest decision for that pair, which is what
stops a third agent doing the work a fourth time.

**A different judge disagreeing is recorded, not refused.** Both rows survive
with their judges and their reasons, and since both verdicts suppress, the
disagreement changes nothing operationally; it is made visible rather than
resolved. A row is only written when no agreeing row stands, so the table
holds at most one row per (pair, verdict).

**Two unnamed judges compare equal, so an anonymous repeat is refused as a
retry.** Where a graph does not require a judge, a replayed batch and a
genuine second reader are indistinguishable, and they want opposite
treatments. Refusing costs an unnamed agent the ability to confirm, which
the journal's first row already records; accepting would let a retried call
manufacture agreement out of nobody.

### 4.2 The suppression read

A small append-only table, not a field on the label record:

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
async def judged_relation_pairs(self) -> set[tuple[str, str]]: ...  # the sweep's read
async def relation_verdicts_for(self, label_ids) -> Sequence[...]: ...  # the writer's read
async def query_relation_verdicts(self) -> Sequence[...]: ...  # the reader's read
```

Three reads because three questions: the sweep asks *has this pair been
judged* (a cheap set); the write path asks *by whom, and to what*, since a
retry and a confirmation are told apart by the verdict matching while the
judge does not, and a `DecisionRecord` carries the subjects and the judge but
not the verdict; and the whole-table read serves the agent.

Storing the pair on the label record was rejected: mutable state held twice,
once on each side, free to disagree.

The sweep resolves each label name to a record, drops any pair whose two ids
are already judged, and suppresses nothing for a pair either of whose sides
has no record.

**What was written is readable where the next agent looks.** Each
`list_relations` row carries the standing verdicts naming its label: the
other label, the verdict, the reason, the judge and the date, newest first,
both rows of a disagreement. And `reflect` reports
`relation_pairs_suppressed`, counted where the skip happens, because the
suppression is silent by design and without the count an empty nomination
list on a well-judged graph is indistinguishable from a graph with nothing
similar in it. *Settled* and *unexamined* must never read the same.

This is a denormalised suppression index, and legitimate as one for
`similarity_decisions.py`'s reason: it is immutable and append-only, so it
cannot drift from the journal row that also records the act.

**Suppression is permanent**, inherited deliberately from the fact-pair
layer, so a wrong `distinct` silences a pair for good. That is the dual of
§6's rule: a suppression with no retraction makes every wrong decline
permanent by construction, exactly as a sweep with no memory makes every
right decline futile. Both are stated; neither is fixed here, and
`ISSUES.md` holds the open question. If a retraction is ever built for
labels, the fact layer's one-directional shape must not be copied across
unexamined. It is one-directional there because a false unification
manufactures agreement while a withdrawal merely under-counts. Neither
failure exists here: nothing corroborates on a label, so a wrong
`synonymous` invents no support and a wrong `distinct` costs no count, and a
symmetric retraction may well be right. That conclusion holds only while
nothing acts irreversibly on `synonymous`; ship an irreversible deprecation
and it needs re-deriving.

### 4.3 The journal row

`DecisionKind.RELATION_VERDICT`, with `subject_ids = [label_a.id,
label_b.id]`. The subjects are ids of records that exist in this graph, and
`review()` dereferences them through `subject_kind` (`node`,
`relation_label`, or null), because a decision's subject is not always a
claim. The label read happens only where an id failed to resolve as a node,
so an ordinary page pays nothing, and a label is never declared in
`retrieved`, which drives focus in a node viewer. Giving the subject an
identity is worth nothing until the reader dereferences it.

Its own kind rather than `SIMILARITY`: review selects on kind, and a reviewer
auditing judgments about claims does not want judgments about vocabulary.

---

## 5. Labels are never rewritten; deprecation is unbuilt

`relation_merges` was removed and nothing replaced it. `reflect` still
nominates likely synonyms, `relation_verdicts` records what was decided about
a pair, and `describe_relation` is what makes a vocabulary converge.
`tests/mcp/test_no_relation_merging.py` guards the absence.

**What that costs.** A `synonymous` verdict acts on nothing: an agent who
concludes that two labels are one relationship can record that and describe
both, and no mechanism folds them. That is the gap deprecation would fill,
and the decision is that an unfilled gap beats an irreversible fill.

**What it buys.** Merging was the system's one operation with no undo, spent
on the least valuable thing in the graph. Edges are not versioned, so a bulk
relabel destroyed the pre-rewrite partition at the moment it ran, with no
journal row naming what it had done. Removing it makes every retained
operation reversible or recorded, which is a property worth having whole.
Dropping is reversible and building is not, so the call went this way.

**The shape deprecation would take, if it is ever wanted.** Because labels do
not affect retrieval (§1.2), deprecation needs no rewrite: marking
`employed_by` as an alias of `works_for` sets `status` and an `alias_of` id
on the record. Existing edges keep their own wording; `list_relations` shows
the canonical set with aliases folded underneath; `link` steers new coinings
to the survivor. Nothing is destroyed, so nothing needs reversing. Undo you
never need beats undo that works.

Two constraints bind it from the first version:

- **It must record its own state changes**, and this is a deadline rather
  than a preference. Two agents can deprecate and un-deprecate the same label
  alternately (§6, FC2); nothing is rewritten, so the graph is unharmed, but
  the journal is append-only and `review`'s difficulty signals keep
  resurfacing the pair. The bound is a cycle limit in the shape of
  `merge_cycle_limit` (`REVIEW_MODE.md` §7.8), a refusal counted from state
  that already exists, and it is nearly free on exactly the condition that
  the history is recorded. Ship deprecation without that history and the
  early oscillations are unreconstructible: the only steps with a deadline
  are the ones recording something that exists once.
- **Terminality is the wrong bound.** A fact-pair retraction is one-way
  because a false unification manufactures agreement while a withdrawal only
  under-counts. Neither failure exists for a label (§4.2), so a deprecation
  nobody may reverse would make a wrong deprecation permanent and buy nothing
  for it. Count the cycles; do not forbid the second one.

---

## 6. Futile cycles

A sweep recomputed from current state that records no declines is a futile
cycle by construction: it re-offers what was already refused and cannot know
it is doing so. Four instances were identified for labels. One was live and
is fixed; the other three are constraints on features nobody has built, and
are written as such rather than as work outstanding.

| | Reachable? | Bound by |
|---|---|---|
| FC1, the nomination treadmill (§1.3) | Was live | Verdicts (§4) |
| FC2, deprecate ↔ un-deprecate | Only if deprecation exists | A cycle limit over deprecation's own history (§5) |
| FC3, nudge, comply, re-coin, nudge | Only if coin-time steering exists; nothing proposes it (§8) | A nudge must carry the description, or neither it nor the agent can tell a synonym from a distinction and the loop has no exit |
| FC4, rename ping-pong | Only if renaming exists; §2.4 does not build it | History on the record, since per-edge capture costs O(edges × cycles) against O(cycles) and must survive a rename that touched zero edges |

Suppression does not bound FC2: it stops the *nomination*, and
deprecate/un-deprecate would be direct calls that need no nomination.

---

## 7. How it is built

| Stage | Holds |
|---|---|
| 1 | `RelationLabel`, the three protocol methods on both backends, `link` create-or-fetch, `epimemer relations backfill` |
| 2 | `description` on `list_relations` and on `link`'s response; `describe_relation` with `DecisionKind.RELATION_DESCRIPTION`; `viz_list_relation_labels` |
| 3 | `relation_verdicts` in `apply_reflection`, `RelationVerdict` and its protocol methods, the suppression filter in `sweep_similar_relation_pairs`, `DecisionKind.RELATION_VERDICT` |
| 4 | Deprecation: not built, not scheduled (§5) |

### 7.1 The record

**Types**: `RelationLabel` in `core/types.py`, beside `Metacontext`.
**Protocol**: the three methods of §2.2, implemented in `storage/memory.py` (a
`dict[str, RelationLabel]` on `_GraphStore`) and `storage/surrealdb_adapter.py`
(a `relation_label` table in `_setup_schema`, unique index on `(name,
kind)`), plus the passthrough in `visualization/instrumented_storage.py`,
whose guard test compares signatures, not method names. **Call site**:
`tools.link`, after `resolved_kind` is settled, fetches or creates the record
with `judged_by=judge`: one extra read on the common path, and a write only
on a label's first use. **CLI**: `epimemer relations backfill [--graph]`.

**Tests** (both backends via the `storage` fixture): coining creates exactly
one record carrying the coining judge and never restamps it; `(name, kind)`
is unique; re-storing an existing label keeps its id; backfill is
idempotent, writes no judge, and skips engine-tier edges; records are per
graph; and every record a label can acquire is reachable without the CLI,
asserted on the in-memory store, where the CLI refuses.

### 7.2 Descriptions

**The kind is resolved from the edges by `get_relation_kind`**, the method
`link` already trusts for a reused label, so the two agree by construction.
It reads every edge while `list_relations` is scoped to active nodes, so a
label whose only remaining edges hang off retired nodes is describable but
not listed. Right way round: the vocabulary outlives the claims that used it,
and the alternative would make a word undescribable exactly when the graph
had begun to forget what it meant.

**The response reports what was stored, not what was asked for.** A blank
`description` leaves existing prose alone (`recorded_relation_label`'s
rule), so the stored text is what comes back; echoing the argument would
tell an agent it had cleared a description it had not.

**Call sites**: `tools.list_relations` joins each derived `(label, kind)` to
its record. Counts stay derived from edges: they are scoped to active nodes
for a reason and a stored count would drift. `describe_relation(name, kind,
description)` creates the record if the label has none, refuses a label no
edge carries, and refuses a `kind` change. Re-describing replaces the text
and journals a second row; the first row is not edited.

**It journals `DecisionKind.RELATION_DESCRIPTION`, not `ENRICHMENT`.**
`ENRICHMENT` is reflect's *topic* enrichment, and a reviewer auditing changes
to what the graph claims does not want prose about what the graph's words
mean mixed in.

**Frontend**: `assemble_snapshot` carries `relation_labels` and
`RelationLabelView` exists on both sides of the wire; nothing renders them
until an edge inspector exists.

**Tests**: descriptions surface on `list_relations` and on `link` reuse; a
description on a recordless label creates the record judge-less; a `kind`
change is refused; re-describing journals a second row without editing the
first; the journal kind is `RELATION_DESCRIPTION`; the viz read never appears
in `list_tools()`.

### 7.3 Verdicts

**Types**: `RelationVerdict`; `DecisionKind.RELATION_VERDICT`, which the
drift guard in `tests/mcp/test_decision_journal.py` requires to have a
writer. **Protocol**: the four methods in §4.2. **Call sites**:
`apply_reflection` applies `relation_verdicts` immediately after
`similarities`, because both are judgments about pairs as the agent saw them
and the anchoring rule covers them jointly: a merge earlier in the same
batch would make one side of a pair vanish. The sweep gains the suppression
filter and the `suppressed` count; `list_relations` carries each label's
standing verdicts. `apply_relation_verdict` takes a `judge` it deliberately
does not write onto a label record it creates.

**Tests**: a declined pair is never re-nominated (shown failing without the
filter); both verdicts suppress; `(a, b)` and `(b, a)` are one pair;
`because` and `kind` are required per entry; a same-judge retry is refused
where a different judge writes a confirmation row; verdicts are per graph
and journal both label ids; suppression survives describing or re-using
either label; `reflect` reports `relation_pairs_suppressed`.

---

## 8. What this deliberately does not do

- **It does not describe edges.** A description belongs to the shared label,
  not to one edge. Per-edge meaning is the step that would make this a
  hypergraph.
- **It does not enforce anything.** A vocabulary the system polices is a
  schema, and this system's whole shape is that agents judge and the graph
  records.
- **It does not re-point edges at label ids.** `NodeEdge.label` keeps its
  string.
- **It does not add renaming** (§2.4).
- **It does not nudge.** `link` reports the description of a label it reused;
  it does not steer an agent away from a name it was about to coin. Steering
  carries FC3, so whatever proposes it owes the answer there.
- **It does not merge labels** (§5).
- **It does not check metacontexts when nominating a pair.** A metacontext check would
  not stop the servant/consultant pair being proposed: both usages sit in the
  same fictional universe, so their derived metacontext sets are identical and
  nothing fires. It catches a different case, two universes or fiction beside
  base reality, and the corroboration harm that justifies `merge_facts`'
  cross-metacontext refusal does not transfer, because nothing corroborates on a
  label. So it was only ever a nomination-quality improvement, and its last
  real reason (deprecation folding a fiction label under a real one) went
  with merging. The answer to a cross-metacontext label is its description.

  If it is ever built, the bar is disjointness, not equality. Do not copy
  `fact_dedup`'s *exactly the same set* rule: that bar is right there because
  a merge inherits a union, and here nothing inherits, so a label
  legitimately used in two metacontexts would become unpairable with anything. The
  right check is the negation of `same_metacontext`'s *share at least one*: do not
  nominate a pair whose derived metacontext sets are disjoint, where a label's
  metacontexts are the union of `metacontexts_for` over the endpoint nodes of every edge
  carrying it.

---

## 9. Rejected

1. **A label as a node.** It would enter search, embeddings, reflection,
   archival and merging; the whole machine would start answering questions
   about the words the graph uses.
2. **Edges pointing at label records by id.** A migration over every
   user-tier edge for no gain: nothing traverses on the label, so the
   indirection buys nothing and costs the ability to read an edge without a
   join.
3. **A `subject_labels` field on `DecisionRecord`.** One field with two
   namespaces. Label ids as subjects make it unnecessary.
4. **The endpoint node ids as a label decision's subjects.** It satisfies
   *ids only* by making the row surface under nodes the decision was not
   about. (`link` does journal `[src_id, dst_id]`, correctly: there the
   endpoints really are what the decision was about.)
5. **Suppression stored on the label record.** Mutable state held twice,
   once per side, free to disagree.
6. **A `ValueSignal` on the record.** Nothing ranks or retrieves a label.
7. **`status` before anything writes it.** A status with one reachable value
   is a constant.
8. **Refusing a verdict on an unrecorded label and pointing at the CLI.** The
   CLI refuses embedded backends, the default development configuration, and
   an agent cannot run it in any case. A remedy the agent cannot issue, on a
   backend where it refuses, is not a remedy. Create-or-fetch at verdict time
   removes the refusal rather than improving its message.
9. **Journalling descriptions as `ENRICHMENT`.** The right verb, the wrong
   side of §4.3's line.
10. **Suppressing declines keyed on the label strings, with no record.** It
    looked like the small option and is not: declining is a decision, every
    decision leaves a journal row, and `subject_ids` holds ids, so it would
    have forced rejection 3 as a side effect.
11. **Merging labels with a captured undo.** Meeting `REVIEW_MODE.md` §7.1's
    rule (any operation collapsing many into one must capture the partition
    while it exists) is most of the cost of building deprecation, on an
    operation deprecation replaces.
