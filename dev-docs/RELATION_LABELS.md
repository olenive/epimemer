# Relation labels: a vocabulary with a record

**Status: stages 1–3 built (2026-08-26, 2026-08-27, 2026-08-27); stage 4
designed (2026-08-24) and undecided (§5).** Written
before any code, at the user's direction, and written to be implemented from:
§7 breaks the build into four stages with the types, protocol methods, call
sites and tests each one needs. Where an unbuilt section says "does", read
"would".

**Stage 1 is `RelationLabel`, the three protocol methods on both backends,
`link` create-or-fetch, and `epimemer relations backfill`** — additive, and it
changes no behaviour. §7.1 records what departed from the design and why.

**Stage 2 is the half that pays**: `description` surfaced in `list_relations`
and in `link`'s response, the `describe_relation` tool journalling
`RELATION_DESCRIPTION`, and `viz_list_relation_labels`. It moves the
intervention from repair to prevention — an agent picking from a described
vocabulary never coins the fourth synonym. §7.2 records its departures.

Raised by the label record, which supersedes the label-merge attribution question. That question asked where a relation
merge's journal subjects go and the answer was *nowhere clean*; this document is
why — **the subject has no identity**, and giving it one dissolves the question rather
than answering it.

**Stage 3 fixed a live defect** (the label record's FC1): a relation-label pair an agent had
considered and declined was re-nominated on every `reflect`, for ever. That is
the treadmill the `assessed` edge closed for fact pairs and never closed here.
`apply_reflection(relation_verdicts=[…])` records the decline, the `RelationVerdict`
table is the suppression index, and the journal row naming both label records is
**where label-merge attribution finally resolves** — the question was unanswerable only because the
subject had no identity. §7.3 records what departed from the design and why.

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
- **Nothing to name in a decision.** The label-merge attribution question, unanswerable because the
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

**And there is no frame check.** `merge_facts` refuses cross-frame
pairs; `sweep_similar_relation_pairs` groups by `kind` alone, so two fictional
universes in one graph pool their vocabularies and are judged on string
similarity. **The asymmetry is smaller than it looks**, and §8 has the working:
a merged *fact* inherits the union of its sources' frames, which is why that
refusal exists, and nothing here inherits anything — so the harm is a
vocabulary that has lost a distinction, not a claim asserted in a world nobody
made it in. The
worked example, from the user: a servant *works for* a master in a culture with
no employment relation, while elsewhere in the same universe a corporation
formally *employs* an on-call consultant who does very little work.
Near-identical strings, opposite meanings, and the nominator sees only strings.

### 1.3 FC1, the live defect

`sweep_similar_relation_pairs` re-derives from scratch on every `reflect` and
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

**The `assessed` edge closed exactly this for fact pairs**, and its measurement is the shape of
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


async def get_relation_label(self, name: str, kind: str) -> RelationLabel | None:
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

> **Three is right again, and the route back is worth keeping.** On 2026-08-27
> this list was corrected to *four*: `apply_reflection(relation_merges=…)` named
> labels, existed, and was reached from `reflect`'s `similar_relations`, and it
> was missing here only because §7.3 had stage 4 *replacing* it — the list had
> been written as if the path were already gone. On 2026-08-28 merging was
> removed (§5), so the fourth path stopped existing and the count is three by
> deletion rather than by the correction being wrong. **The carry-forward
> survives both moves intact: an enumeration of write paths is a claim that
> ages, and the guard that catches it is §7.1's test 9.**

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
edges, which a per-edge log cannot (the label record's FC4).

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

`because` is required on both, for the `assessed` edge's reason: a verdict with no reason marks
the pair judged, so the next agent skips it without knowing whether it was
examined or waved through.

Refused, per entry, in the shape `similarities_refused` already uses: a missing
`kind` (copy it from the nomination — defaulting it would make the stale-kind
refusal blame the agent for a value the call invented), a label no edge in this
graph carries, a pair whose two sides differ in `kind`, and a pair already
carrying **this agent's** identical verdict — a retry is not a second opinion.

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

**A different judge disagreeing is recorded, not refused.** It is neither a
retry nor a confirmation; both rows survive with their judges and their
reasons, and since both verdicts suppress, the disagreement changes nothing
operationally — it is made visible rather than resolved. Resolving it is the missing suppression retraction's
question, and answering it here would be building the missing suppression retraction by accident. One
structural consequence: a row is only written when no agreeing row stands, so
the table holds at most one row per (pair, verdict) — two rows per pair, ever.

**Two unnamed judges compare equal, so an anonymous repeat is refused as a
retry.** Where a graph does not require a judge, a replayed batch and a genuine
second reader are indistinguishable, and they want opposite treatments.
Refusing costs an unnamed agent the ability to confirm — which the journal's
first row already records — while accepting would let a retried call
manufacture agreement out of nobody. Fact dedup's direction, applied to attribution
rather than to corroboration.

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
async def judged_relation_pairs(self) -> set[tuple[str, str]]: ...  # the sweep's read
async def relation_verdicts_for(self, label_ids) -> Sequence[...]: ...  # the writer's read
async def query_relation_verdicts(self) -> Sequence[...]: ...  # the reader's read
```

Three reads because three questions: the sweep asks *has this pair been
judged* (a cheap set); the write path asks *by whom, and to what* — a retry
and a confirmation are told apart by the verdict matching while the judge does
not, and a `DecisionRecord` carries the subjects and the judge but **not the
verdict**, so the journal cannot answer it; and the whole-table read serves the
agent, below.

Storing the pair **on the label record** was rejected: it is mutable state held
twice, once on each side, free to disagree — per-edge-type migration, the drifted lookup tables and the drifted lookup tables for the fifth
time.

The sweep gains the filter. It resolves each label name to a record, drops any
pair whose two ids are already judged, and **suppresses nothing for a pair
either of whose sides has no record** — fail-safe, as §2.3 requires.

**What was written must be readable where the next agent looks, and it is read
back in two places.** `because` is required on the grounds of what the next
agent needs, which is a promise about a *read*: each `list_relations` row
carries the standing verdicts naming its label — the other label, the verdict,
the reason, the judge and the date, newest first, both rows of a disagreement.
And `reflect` reports `relation_pairs_suppressed`, counted where the skip
happens, because the suppression is silent by design and without the count an
empty nomination list on a well-judged graph is indistinguishable from a graph
with nothing similar in it — *settled* and *unexamined* must never read the
same.

This is a **denormalised suppression index and is legitimate as one** for
exactly `similarity_decisions.py`'s stated reason: it is immutable and
append-only, so it cannot drift from the journal row that also records it. The
journal is the audit record; the sweep and the row reads above are what the
runtime consults without a journal query.

**Suppression is permanent, and that is inherited deliberately rather than by
accident.** The fact-pair layer decided it in as many words — *"the `assessed`
edge stays and the pair stays out of every future nomination: the agent has now
judged it twice, and re-offering it would restart the treadmill"* — and the `one_claim` retraction's
retraction, which fixed the *other* half of that problem, left suppression
explicitly untouched. So a wrong `distinct` here silences a pair for good.

**That is the dual of §6's rule and belongs beside it: a suppression with no
retraction makes every wrong decline permanent by construction**, exactly as a
sweep with no memory makes every right decline futile. Both are stated; neither
is fixed here.

**If a retraction is ever built for labels, the `one_claim` retraction's one-directional shape must not
be copied across unexamined.** It is one-directional for a reason specific to
facts: a false unification manufactures agreement — the worst failure this
system can produce — while a withdrawal merely under-counts, so fact dedup's direction
settles it. **Neither failure exists here.** Nothing corroborates on a label, so
a wrong `synonymous` invents no support and a wrong `distinct` costs no count.
The asymmetry that justifies the fact layer's terminal retraction is simply
absent, and a symmetric retraction may well be right.

**That conclusion is stage-dated, and the dependency is named here rather than
left to be rediscovered.** *"Nothing acts on `synonymous`"* is true only until
stage 4, whose deprecation would act on it. The asymmetry still does not
reappear — but only because deprecation is **reversible** by design (§5, and
FC2's whole shape assumes it), so acting on a wrong `synonymous` invents nothing
permanent. **Ship an irreversible deprecation and this argument needs re-deriving
from scratch.** Filed as the missing suppression retraction, which is where the retraction question
now lives for both layers.

### 4.3 The journal row

`DecisionKind.RELATION_VERDICT`, with `subject_ids = [label_a.id, label_b.id]`.

**This is where label-merge attribution resolves.** The subjects are ids of records that exist in
this graph, and `review()` dereferences them — through `subject_kind` (`node`,
`relation_label`, or null), because a decision's subject is not always a claim
and `get_nodes` alone rendered a label id as *not in this graph*. The label
read happens only where an id failed to resolve as a node, so an ordinary page
pays nothing; a label is never declared in `retrieved`, which drives focus in a
**node** viewer. `node.notes` surfaces nothing spurious, and no field acquires
a second namespace. *Giving the subject an identity is worth nothing until the
reader dereferences it* — the same lesson the label record is built on, one layer up.

Its own kind rather than `SIMILARITY`: review *selects* on kind, and a reviewer
auditing judgments about claims does not want judgments about vocabulary.

`DecisionKind`'s docstring names `relation_merge` as pending on that question — stage 3
updates that pointer to this document, and stage 4 adds the member if merging
survives.

---

## 5. Merging is removed; deprecation is unbuilt — stage 4

**Decided 2026-08-28: `relation_merges` is removed, and stage 4 is not built.**
A relation label is never rewritten. `reflect` still nominates likely synonyms,
`relation_verdicts` records what was decided about a pair, and
`describe_relation` is what makes a vocabulary converge — a described word is
what stops the third synonym being coined.

**What removal costs, said plainly.** A `synonymous` verdict now acts on
nothing, permanently rather than provisionally: an agent who concludes that two
labels are one relationship can record that and describe both, and no mechanism
folds them. That is the gap deprecation would fill, and the decision is that an
unfilled gap beats an irreversible fill.

**What it buys.** The system had exactly one operation with no undo, and it
spent that on the least valuable thing in the graph. Edges are not versioned, so
a bulk relabel destroyed the pre-rewrite partition at the moment it ran, with no
journal row naming what it had done — the one decision `ATTRIBUTION.md` had to
list as unattributed. Removing it makes *every* retained operation reversible or
recorded, which is a property worth having whole.

**Nothing about this blocks deprecation later**, and that asymmetry is why the
call went this way: dropping is reversible and building is not. The design below
stands unchanged as the shape stage 4 would take, and the deadline inside it is
still binding — on **how** it is built whenever it is built, never on when.

> **2026-08-27: put to the user, and with a reviewer as this is written.** The
> recommendation was **remove `relation_merges` and do not build stage 4 yet**;
> the user's instinct was that removal is right and that deprecation still looks
> like a tool worth having. Neither is decided. What the discussion produced that
> should survive it:
>
> - **Dropping is reversible; building is not.** Deprecation can be added at any
>   later point and nothing about descriptions blocks it, whereas once
>   `list_relations` and `link` both carry a folding rule plus a status model and
>   a cycle limit, removing that is a migration.
> - **§5's deadline is conditional on building, not on timing.** *"Deprecation
>   must record its own state changes from the first version"* constrains **how**
>   it is built whenever it is built; it creates no pressure to build it now.
>   That reading is the load-bearing one for deferring and is with the reviewer.
> - **Descriptions solve comprehension, not convergence.** Stage 2 tells an agent
>   what a word means here; nothing pulls ten words for one idea together except
>   `list_relations` sorting by usage count, which is a weak force. This is the
>   strongest argument for building deprecation and it was under-weighted.
> - **The ordering constraint, which binds whatever is decided: stage 3 must land
>   before or with any removal.** `reflect` nominates `similar_relations` today
>   and those nominations exist to feed `relation_merges`. Remove merging first
>   and they feed nothing — FC1's treadmill still running, now to no destination.
>   Verdicts have to be there to inherit them.
> - **The corpus evidence may be survivorship.** One relation label in the
>   largest real graph either means the problem does not occur, or means nothing
>   here encourages `link` with a user relation so the vocabulary never grows
>   enough to need consolidating. The two readings point opposite ways and were
>   not distinguished.

**Because labels do not affect retrieval (§1.2), deprecation needs no rewrite.**
Marking `employed_by` as an alias of `works_for` sets `status` and an
`alias_of` id on the record. Existing edges keep their own wording;
`list_relations` shows the canonical set with aliases folded underneath; `link`
steers new coinings to the survivor. Nothing is destroyed, so nothing needs
reversing.

That is the whole argument for preferring it to `relation_merges`: **undo you
never need beats undo that works.** A lossy irreversible bulk rewrite becomes a
reversible annotation.

**Deprecation must record its own state changes from the first version**, and
this is a deadline rather than a preference. Two agents can deprecate and
un-deprecate the same label alternately (FC2): nothing is rewritten, so the
graph is unharmed, but the journal is append-only and `review`'s difficulty
signals keep resurfacing the pair. The bound is a **cycle limit** in the shape
of `merge_cycle_limit` (`REVIEW_MODE.md` §7.8) — a refusal counted from state
that already exists — and it is *nearly free* on exactly that condition: a
label that has been deprecated and restored has to say so anyway, and the limit
reads what deprecation was going to record regardless. Ship deprecation without
that history and the early oscillations are unreconstructible, which is
`REVIEW_MODE.md` §10's 0a/0b argument in a third place: **the only steps with a
deadline are the ones recording something that exists once.**

**Terminality is the wrong bound here, and it is worth saying why**, since the `one_claim` retraction
is the obvious model and is right next door. A retraction there is one-way
because a false unification manufactures agreement while a withdrawal only
under-counts. Neither failure exists for a label (§4.2), so a deprecation
nobody may reverse would make a wrong deprecation permanent and buy nothing for
it. Count the cycles; do not forbid the second one.

**`relation_merges` was not kept**, and the condition it would have had to meet
is why. It would have needed to capture the pre-rewrite partition at the moment
it ran, by `REVIEW_MODE.md` §7.1's rule — any operation collapsing many into one
destroys the partition, and the partition exists only while the operation is
happening. That rule is not about nodes; it is about collapse. Meeting it is
most of the cost of building deprecation, on an operation deprecation replaces.

`reverse_merge`'s three caveats transfer unevenly, which is worth knowing before
reasoning from the parallel:

| `reverse_merge` caveat | Transfers? |
|---|---|
| Capture or lose; retention bounded by `merge_undo_depth` | **Exactly.** Which edges carried the label exists only during the rewrite |
| Refuse when anything accreted onto the survivor | **Weakly.** A label reversal deletes nothing and nothing points at a label. The only accretion is `get_relation_kind`, where a later edge inherited the surviving label's kind |
| The hard delete, and its narrow justification | **Not at all.** Reversing a relabel writes a string back |

---

## 6. Futile cycles

From the label record. **Only one of the four is reachable today**, and the other three are
cycles in features nobody has built — which is the useful thing to say about
them, because a shared list reads like a defect register when three of its
entries are *preconditions attached to features that would create them*.

| | Reachable? | Carried by |
|---|---|---|
| FC1 | **Yes — live** | Fixed by stage 3 |
| FC2 | Only if deprecation exists | Stage 4, **from day one** (§5) |
| FC3 | Only if coin-time nudging exists — **nothing proposes it** (§8) | Whatever proposes it |
| FC4 | Only if renaming exists — **§2.4 does not build it** | Whatever builds it |

The entries below are therefore written as constraints on the thing that would
make each one possible, not as work outstanding.

- **FC1** — the nomination treadmill. §1.3. Fixed by stage 3, and the only
  entry here describing something the system does now.
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
- **FC3 — nudge, comply, re-coin, nudge.** An agent coins a name, is steered to
  an existing one, complies; a later agent that genuinely needs the distinction
  coins it again and is steered again. **This is not reachable, and an earlier
  draft implied stage 2 fixes it.** Stage 2 does no steering: `link` returns the
  description of a label it *reused*, which is information and not a redirect
  (§3.2). Nothing in this design proposes a nudge, and §8 now says so. The
  constraint stands for whatever does propose one: **a nudge must carry the
  description**, or neither it nor the agent it is nudging can tell a synonym
  from a distinction, and the loop has no exit.
- **FC4 — rename ping-pong.** Also not reachable: §2.4 does not build renaming,
  because edges join to `name` by string. The constraint for whatever builds it:
  history goes **on the record**, since per-edge capture costs O(edges × cycles)
  against O(cycles), and it survives a rename that touched zero edges.

**The general rule, worth checking against any new nominator: a sweep
recomputed from current state that records no declines is a futile cycle by
construction.** It re-offers what was already refused and cannot know it is
doing so. That is the `assessed` edge's lesson stated once rather than rediscovered per feature.

---

## 7. Build order

Each stage is shippable, each pays on its own, and nothing earlier is thrown
away by a later one.

| # | Stage | Why here |
|---|---|---|
| 1 | `RelationLabel`, the three protocol methods on both backends, `link` create-or-fetch, `epimemer relations backfill` | Additive; changes no behaviour. Every later stage needs identity to exist first, and this is the only stage that can be built without deciding anything else |
| 2 | `description` surfaced in `list_relations` and in `link`'s response; `describe_relation` tool with `DecisionKind.RELATION_DESCRIPTION`; `viz_list_relation_labels` | **The half that pays**, and independent of FC1. Must precede stage 3 (FC3) |
| 3 | `relation_verdicts` in `apply_reflection`, `RelationVerdict` + two protocol methods, the filter in `sweep_similar_relation_pairs`, `DecisionKind.RELATION_VERDICT` | **Fixes the live defect**, on every backend and without the CLI (§2.3). Needs stage 1 for ids and stage 2 so a verdict is made against a described vocabulary. Resolves label-merge attribution |
| 4 | Deprecation / `alias_of` / `status`, **its own state-change history and a cycle limit over it** (§5) | **Not built, and not scheduled.** `relation_merges` was removed on 2026-08-28 rather than replaced, so this stage no longer has anything to displace: it is a new capability whenever somebody wants it. If it is built, the history is day-one work rather than a follow-on — it is what FC2's bound counts, and it exists only while the changes happen |

**Urgency is low and stated as such.** FC1 causes zero harm today: one label in
the largest real graph means zero nominations are possible. This is a defect
waiting for the vocabulary to grow, which is the argument for building it
properly rather than quickly.

### 7.1 Stage 1

> **Built 2026-08-26.** As designed, with two departures worth recording.
>
> **`store_relation_label` preserves the record's identity**, rather than
> leaving that to the callers. The design said *"those are the only fields an
> update may move"* and left the enforcement unstated; passing a freshly
> constructed `RelationLabel` for a label that already had one therefore minted
> a new id over the old, silently. That is the label record's own defect one layer down — a
> journal row naming the label would point at an id nothing resolves — and it
> reached a passing test, which checked the description and not the id.
> `recorded_relation_label` is now the pure merge both backends write through:
> `id`, `created_at` and `judged_by` come from the record already there, and a
> blank description never overwrites prose. **The coiner-never-the-describer
> rule is structural rather than a convention every caller has to remember.**
>
> **Test 9 was scoped to what stage 1 has.** As written it coins, describes and
> judges, and finds three records; describing and judging arrive in stages 2 and
> 3. What it can assert now — and does — is that coining alone records a label
> with no CLI involved, which is the half of the claim that stage 1 owns. The
> rest belongs with the paths that create it.
>
> The SurrealDB write is `UPSERT … CONTENT … WHERE name = $name AND kind =
> $kind`, keyed on the natural pair rather than on `uid`: a create-or-fetch
> caller that lost a race would otherwise write a second record under a fresh
> id and hit the unique index, turning a benign concurrent coin into an error.
> The read-merge-write above is what makes `CONTENT` safe there.
>
> `epimemer relations backfill` inherits the CLI's embedded-backend refusal, and
> its refusal message says plainly that **nothing is lost** — every write path
> creates the record, so the vocabulary fills in as it is used. A refusal that
> read as *this graph cannot be fixed* would be worse than no command.

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
   coins, describes and judges on an in-memory store, since that backend is
   exactly where the CLI refuses. Three paths: this test is what found the
   fourth (`relation_merges`, §2.3's correction of 2026-08-27), and it is also
   what shows the count back down after §5 removed it on 2026-08-28.

### 7.2 Stage 2

Built 2026-08-27. The rules that decide behaviour:

**The kind is resolved from the edges by `get_relation_kind`** — the method
`link` already trusts for a reused label, so the two agree by construction. It
reads every edge while `list_relations` is scoped to *active* nodes, so a label whose only remaining edges hang off retired nodes is describable
but not listed. Right way round: the vocabulary outlives the claims that used
it, and the alternative would make a word undescribable exactly when the graph
had begun to forget what it meant.

**Every write path creates a missing record** (§2.3's enumeration, guarded by
one test that ages with it): coining via `link`, describing, and judging.

**The response reports what was stored, not what was asked for.** A blank
`description` leaves existing prose alone (`recorded_relation_label`'s rule),
so the stored text is what comes back — echoing the argument would tell an
agent it had cleared a description it had not.

**`link` omits the key rather than sending an empty one**: `""` reads as *this
graph means nothing by the word*, absence as *nobody has said* — §3.1's
distinction. `list_relations` does the opposite and always carries the field,
because there the row exists either way and an absent key would be a missing
column rather than an unstated meaning.


**Call sites** — `tools.list_relations` joins each derived `(label, kind)` to
its record and returns `description` (empty when absent). Counts stay derived
from edges: they are scoped to active nodes for a reason and a
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

**Frontend** — `assemble_snapshot` carries `relation_labels` and
`RelationLabelView` exists on both sides of the wire; nothing renders them
until an edge inspector exists, which is a UI feature rather than a row.
`EDGE_MEANINGS` untouched: this adds no `EdgeType`, which is what the drifted lookup tables keeps
catching.

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

Built 2026-08-27; the read surface (verdicts on `list_relations`,
`relation_pairs_suppressed` on `reflect`) followed one commit later, out of
review — the rules all live in §4 now.

**Types** — `RelationVerdict`; `DecisionKind.RELATION_VERDICT`, which the
drift guard in `tests/mcp/test_decision_journal.py` requires to have a writer in
the same commit.

**Protocol** — the four methods in §4.2: `record_relation_verdict`,
`judged_relation_pairs`, `relation_verdicts_for`, `query_relation_verdicts`.

**Call sites** — `apply_reflection` gains `relation_verdicts`, applied at
**step 1b**, immediately after `similarities`: both are judgments about pairs
as the agent saw them, and the anchoring rule covers them jointly — a merge
earlier in the same batch would make one side of a pair vanish. The sweep
gains the suppression filter and the `suppressed` count; `list_relations`
carries each label's standing verdicts (§4.2). `apply_relation_verdict` takes
a `judge` it deliberately does not write onto a label record it creates — the
argument is accepted and dropped at the one call site most likely to reach for
it, which is where the coiner-never-the-judger rule needed to be visible.

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
11. `list_relations` carries the verdict, its direction, its `because` and its
    judge — mirrored onto both labels of the pair, disagreements as two rows,
    an unattributed judge as null.
12. `reflect` reports `relation_pairs_suppressed`, distinguishing a settled
    graph from an unexamined one.
13. An entry omitting `kind` is refused per entry, names the missing field,
    and suppresses nothing.

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
- **It does not nudge.** `link` reports the description of a label it reused; it
  does not steer an agent away from a name it was about to coin. Steering is a
  plausible next idea and it carries FC3, so whatever proposes it owes the
  answer there: a nudge that cannot show *why* two names differ produces a loop
  with no exit.
- **It does not merge labels.** That question is settled: merging was removed
  on 2026-08-28 rather than replaced (§5), so a label is never rewritten.
- **It does not check frames when nominating a pair**, and that is now a closed
  question rather than an open one. The check was proposed, argued down twice,
  and dropped on 2026-08-28 when its last reason went with merging.

  The argument is worth keeping because it corrects an overclaim made here
  first. A frame check would **not** stop the servant/consultant pair being
  proposed: both usages sit in the same fictional universe, so their derived
  frame sets are identical and nothing fires. It catches a different case
  entirely — two universes, or fiction beside base reality — and the
  corroboration harm that justifies `merge_facts`' cross-frame refusal does not
  transfer, because nothing corroborates on a label. So it was only ever a
  nomination-quality improvement, worth a wasted look rather than a wrong
  belief, and no substitute for verdicts: two genuinely distinct labels in one
  frame recur either way. What finally closed it is that its strongest remaining
  reason was deprecation folding a fiction label under a real one, and
  deprecation is not being built. The answer to a cross-frame label is its
  **description** — *"in the Court frame this means X"* — the distinction stated
  rather than the pair refused.

  **If it is ever built, the bar is disjointness, not equality.** Do not copy
  `fact_dedup`'s *exactly the same set* rule: that bar is right there because a
  merge inherits a union, and here nothing inherits, so a label legitimately
  used in two frames would become unpairable with anything. The right question
  is `same_frame`'s — **share at least one** — and the check is its negation:
  do not nominate a pair whose derived frame sets are **disjoint**. A label has
  no frame of its own; its frames are the union of `frames_for` over the
  endpoint nodes of every edge carrying it. Those are two different questions
  and `review.py` already distinguishes them.

---

## 9. Rejected, and why

1. **A label as a node.** It would enter search, embeddings, reflection,
   archival and merging — the whole machine would start answering questions
   about the words the graph uses. `Metacontext` is the precedent for a
   described thing that is deliberately not a node.
2. **Edges pointing at label records by id.** A migration over every user-tier
   edge, for no gain: nothing traverses on the label, so the indirection buys
   nothing and costs the ability to read an edge without a join.
3. **A `subject_labels` field on `DecisionRecord`** — the second option considered. One
   field with two namespaces, which is the tell this codebase names repeatedly.
   Stage 3 makes it unnecessary: the subjects are ids.
4. **The endpoint node ids as a relation merge's subjects** — the third
   option. It satisfies *ids only* by making the row surface under nodes the
   decision was not about: `node.notes` would show *"somebody merged two
   relation labels"* against a topic nobody judged. Note that `link` **does**
   journal `[src_id, dst_id]`, and correctly — there the endpoints really are
   what the decision was about.
5. **Suppression stored on the label record.** Mutable state held twice, once
   per side, free to disagree. Per-edge-type migration, the drifted lookup tables, the drifted lookup tables.
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
