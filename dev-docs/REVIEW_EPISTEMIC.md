# The epistemic review loop

How Epimemer reviews and reconciles knowledge over time: outdated facts, stale
inferences, contradictions, frame-relative truth, trivial knowledge, and
claims true of a period. One loop, several verdicts, and a division of labour
between the agent and the human. `docs/REFLECTION.md` and `docs/VALIDITY.md`
describe the behaviour a caller sees; this document holds the model and the
reasons.

---

## 1. Principles

Epimemer is an append-only, immutable-history epistemic memory. Nodes are
never mutated in content; corrections create new versions linked by history
edges (`superseded_by`, `temporally_followed_by`, `merged_into`). Lifecycle
metadata (`status`, `superseded_at`, `value` signals, lifecycle episodes) is
mutated in place: it is not the knowledge claim, so editing it rewrites no
history. The rhythm is *write fast, organise slow*: ingestion is mechanical
and cheap; organisation (consolidation, review) is deliberate and, where it
matters, agentic.

1. **Detection is cheap recall plus precise judgment.** Embeddings are a good
   candidate generator ("these facts are about the same thing") and a poor
   judge ("do they contradict, supersede, or coexist?"). Similarity
   nominates; an agent decides.
2. **Nothing is destroyed; ambiguity is made visible.** Outdated or contested
   nodes stay `ACTIVE` and retrievable, carrying a computed review label so
   anything reading them knows. Retirement is a deliberate act with history
   preserved; archival is export plus a status flip, never a delete.
3. **Two-tier epistemic responsibility.** The agent handles mechanical,
   clear-cut calls (dedup, obvious supersession, same-frame routing). It
   escalates the epistemically consequential ones to the human (genuine
   contradictions, crossing frame boundaries, archival). Human-in-the-loop
   is in-conversation.

---

## 2. The unified review loop

Contradictions, staleness, frame coexistence, temporal succession and
triviality are not separate subsystems. They are outcomes of one loop:

```
new/changed knowledge
        │
        ▼
  candidate generation   ← cheap: embedding similarity (recall)
        │
        ▼
  agentic judgment       ← precise: the agent classifies the relationship
        │
        ├─ redundant        → merge_facts, or record SIMILARITY and keep both (§3)
        ├─ supersedes       → correction: superseded_by, old → CORRECTED
        ├─ succeeds         → world moved: temporally_followed_by, old → HISTORICAL
        ├─ recurs           → historical twin true again: restore + new source
        ├─ contradicts      → record CONTRADICTION (same frame) → resolve
        ├─ cross-frame      → not a conflict; coexist; (optional) variant_of
        └─ compatible       → nothing
        │
        ▼
  recorded as edges      ← durable source of truth
        │
        ▼
  visible at retrieval   ← computed review labels + provenance
        │
        ▼
  resolution             ← agent, or escalated to human
```

Cleanup (§12) is one more arm of the same loop: nomination plays the
candidate-generation role, the agent judges, the human approves.

---

## 3. Verdict taxonomy

When a newly ingested fact is similar to an existing fact, the agent
classifies the pair:

| Verdict | Meaning | Action |
| --- | --- | --- |
| **redundant** | the same claim restated | `merge_facts(source_ids, content)`: one node keeping a `sourced_from` edge per contributing document, each carrying that document's own periods. Where the merge is refused, record `SIMILARITY` and keep both |
| **supersedes** | the new claim corrects the old; the old was wrong | `superseded_by`, old → `CORRECTED`, terminal |
| **succeeds** | both true, over different periods; the world moved | `temporally_followed_by` (old → new), old → `HISTORICAL`, restorable |
| **recurs** | the same claim, previously retired `HISTORICAL`, is true again | explicit reactivation: `restore` plus a new `sourced_from` edge carrying the new document's interval, in one transaction |
| **contradicts** | conflicting claims, same frame, unclear which holds | record `CONTRADICTION`; resolve (agent or human) |
| **cross-frame** | a "conflict" only because the frames differ | not a conflict; both coexist; optional `variant_of` |
| **compatible** | no conflict | nothing |

`supersedes` and `succeeds` are the two halves of what a single "supersede"
used to conflate: *we were wrong* against *the world moved*. `succeeds` is
the temporal sibling of `cross-frame`: one says the *frame* differs, the
other says the *period* differs. `recurs` exists because without it a
recurrence is forced into `redundant` (which assumes an active twin) or
`succeeds` (which assumes a different claim following). It can only fire
because nomination includes `HISTORICAL` candidates (§5.1).

**`merge_facts` refuses, out loud and with a reason, whenever the graph
cannot vouch for the merge**: a retired twin (that is `recurs`, and
`restore`); a pair not standing in exactly the same frames (that is
`cross-frame`, and `record_variant`); a pair below the nomination bar; or a
claim that is an **event** rather than a state, or one ingested before anyone
judged which it was. Every refusal leaves the agent where it was: two nodes,
one `similarity` edge, corroboration reading the neighbourhood.

**The event/state judgment is recorded at ingest** (`claim_kind` on `Fact`)
because it cannot be recovered later. *"Labour won the election"* from a 1997
document and from a 2024 one is two victories wearing one sentence; under the
interval model a merge unions their periods into a single twenty-seven-year
win. Nothing computable from the two stored sentences separates that from
*"Labour is in government"*, where the union is exactly right; only the
document does, and the document is gone by the time anything asks. A fact
with no `claim_kind` is unmergeable, which is the safe direction.

A separate, non-similarity trigger handles **evidential staleness**: when a
fact is retired or merged, inferences derived from it become suspect (§5.2).

---

## 4. Data model

### 4.1 Review labels (computed, not stored on the node)

Edges are the source of truth; retrieval computes a label per returned node:

| Label | Condition | Case |
| --- | --- | --- |
| `superseded_candidate` | node has an incoming `supersession_candidate` edge | A, temporal |
| `evidence_stale` | inference has an `evidence_superseded` edge, or is `derived_from` a retired fact | B, evidential |
| `evidence_merged` | inference has an `evidence_merged` edge | B, evidential |
| `contested` | node has a `contradiction` edge unresolved in its own frame | contradiction |

The node stays `ACTIVE`. Labels are surfaced on search results alongside the
contesting or retired node id, so the caller can hop to it.

`evidence_merged` is its own label rather than a qualified `evidence_stale`,
and the reason is not tidiness. Archival (§12.3) nominates on
`evidence_stale`, so sharing the label would propose discarding an inference
because its premise got *better* provenance, on every merge, for every
dependent. The two events also want different work from the agent: a
correction says re-derive, a merge says re-read against the new wording. A
merge changes a premise without retiring it (the `derived_from` edge migrates
onto the survivor, which is `ACTIVE`), so the flag edge written in
`merge_nodes` is the only record the event will ever leave.

### 4.2 Edge types the loop writes

| Edge | From → To | Meaning |
| --- | --- | --- |
| `supersession_candidate` | newer fact → older fact | "this may replace that; review" (Case A) |
| `evidence_superseded` | retired fact → dependent inference | "this inference's basis changed" (Case B) |
| `evidence_merged` | absorbed fact → dependent inference | "the premise you were drawn from absorbed another claim". The source is the fact that went away: which wording is gone is the whole content of the flag |
| `contradiction` | fact ↔ fact | genuine same-frame conflict |
| `variant_of` | fact ↔ fact (across frames) | "same proposition, resolved differently per frame"; makes divergence queryable |
| `similarity` | fact ↔ fact | judged one claim, kept as two nodes; read by corroboration |
| `assessed` | fact ↔ fact | a pair was judged, whatever the verdict; the suppression index the nomination sweep reads, and never a flag on either node |
| `temporally_followed_by` | older fact → newer fact | "both true, over different periods": order, not replacement, so it survives recurrence |
| `based_on` / `associated_with` | metacontext → metacontext | frames relate (association, not inheritance) |

`supersession_candidate`, `evidence_superseded`, `evidence_merged`,
`assessed` and the history edges are excluded from default graph traversal
and from edge migration: they are records about claims rather than claims.
`similarity`, `contradiction` and `variant_of` are real edges to follow but
are anchored on every retirement all the same (`JUDGMENT_EDGE_TYPES`): a
judgment is about the wording that was judged, and re-pointing one onto a
replacement asserts it of a claim nobody assessed. The case that makes it
necessary is the substantive correction: "the population is 500,000" →
"5,000,000" is the same claim, so its sources rightly follow it, but a
counterpart judged *one claim* against the old figure was judged against a
number that is no longer there, and since corroboration walks `similarity`,
carrying the edge would count that counterpart's publisher as backing the new
figure.

**The keep verdict is a `retention` journal row, not an edge.** Its `covers`
field lists the reasons it answers (the changed facts named in the
`evidence_stale` label, the absorbed phrasings named in `evidence_merged`).
An empty `covers` means the node was kept for its own sake, the
`never_retrieved` shape. The verdict is anchored, and that is the difference
from the pair case: a judged pair's wording is fixed at the moment of
judgment, so `assessed` suppresses it permanently, while a node's
neighbourhood keeps moving, and a keep that silenced the next change as well
as this one would be a worse defect than the treadmill it replaced. So a
nomination survives a verdict when the node's current reasons are not all
covered. It is not a `judgments` entry because raising `importance` to stop a
nomination writes *do not nominate this* into a field meaning *how
consequential this is*; use `judgments` where the importance was wrong and
`retained` where the importance is right and the node has been re-read.

### 4.3 Metacontext model

- **Every ingested node names its frame.** `metacontext_id` is required on
  `store_decomposition`, and synthesis and splits inherit their sources'
  frames. `the-real` is the conventional id for the frame holding real-world
  claims; nothing reads it specially.
- **Absence names no frame.** A node with no `has_metacontext` edge is a node
  nobody said anything about, consistent with every other absence in the
  model (an omitted `confidence` is unrated, an absent `judged_by` is
  unknown). Such a node shares a frame with nothing, so it is never nominated
  as contradicting anything, never merged, and never returned by a scoped
  search. It is reachable only on a graph written before the requirement,
  and `epimemer frames declare` is how a person states which frame those
  nodes were always in.
- **Contradiction is frame-relative.** Same frame: a genuine contradiction,
  to resolve. Disjoint frames: not a contradiction; both coexist, framed.
- **Association, not inheritance.** Frames are linked by association edges;
  facts never flow automatically between frames. Reaching into an associated
  frame is an explicit, agentic and often human-gated choice, never an
  inheritance walk, which is what keeps the diamond problem out of the
  knowledge layer.
- **Search takes a list of frames**, returning nodes standing in any of them,
  and no frame inherits another. Omitting the list searches every frame,
  which is a coherent question rather than an unstated assumption, the reason
  it is optional at search where the frame on ingest is not.

---

## 5. Detection

### 5.1 Case A: contradiction, supersession, succession, recurrence

1. At ingest, `check_conflicts` runs a vector lookup per new fact over
   `{ACTIVE, HISTORICAL}` facts and returns the candidates above the
   nomination bar (`SIMILARITY_NOMINATION_THRESHOLD`, 0.80), each carrying
   its `status`, because telling `redundant` from `recurs` *is* the
   active/retired distinction and a candidate list that hid it would invite
   the misclassification the verdict exists to prevent. `CORRECTED` is never
   nominated.
2. The agent judges each candidate (§3) and records the appropriate edge.
3. `reflect` keeps a similarity sweep as a safety net. It matters because
   `check_conflicts` is opt-in and a graph whose agent never ran it would
   never be asked. The sweep nominates the same set and reports the mixed
   active/historical pairs under `recurrences`, separately from
   `contradictions`, because a claim beside its own successor is not a
   contradiction and filing it under that word is the misreading `recurs`
   exists to prevent. The wider sweep still scores one matrix; the set is
   partitioned after scoring.

A cheap floor sits under both: `store_decomposition` reports
`historical_twins`, facts just stored that are word-for-word a retired claim.
It reports and never acts.

**Nomination order is ingest order, and ingest order is not validity
order.** A 1970s memoir ingested today is older truth, so a recency-driven
`supersedes` or `succeeds` verdict would point backwards in time. Direction
comes from `published_at` and per-interval witness points where they exist
(`VALIDITY_DESIGN.md`), and for undated pairs the judging agent has to
reason from the documents rather than from arrival.

**A sweep recomputed from current state must record declines.** The
`assessed` edge is written for every judged pair whatever the verdict, and
the sweep skips pairs that carry one. Without it, thirteen of the first
eighteen nominated pairs on a real graph were declined and returned on every
reflect, to an agent who could not know they had been refused.

### 5.2 Case B: evidential staleness

When a fact is retired, the same transaction adds `evidence_superseded` edges
to every inference that is `derived_from` or supported by it. When a fact is
merged, `merge_nodes` adds `evidence_merged` edges from the absorbed fact to
its dependents. No similarity needed: pure graph propagation.

---

## 6. Resolution

A flagged or contested node is resolved by one of:

1. **Retire the loser**: `supersede_by(old_id, by=existing_id, because=…)`
   marks an existing node as replacing another, with the status and edge
   chosen by `because`; `apply_reflection(supersessions=[…])` does it in
   batch from the `pending_review` worklist.
2. **Coexist via metacontexts**: re-classify as cross-frame; ensure both
   facts are framed; optionally add a `variant_of` link. Often the right
   answer.
3. **Escalate to the human**: surfaced in conversation; the user decides;
   the agent applies the outcome.
4. **Leave contested**: until resolved, retrieval flags both facts so nothing
   downstream trusts a contested fact blindly.

A flagged inference is resolved by re-deriving it, retiring it, or keeping it
with `apply_reflection(retained=[…])` naming the reasons it was re-read
against (§4.2).

---

## 7. Human-in-the-loop

- **In conversation**, not a separate UI.
- **Notify on genuine same-frame contradictions**: "new fact conflicts with
  existing fact X in the same context; how should I resolve it?" Cross-frame
  "conflicts" do not interrupt the user; at most a quiet "framed as Y". The
  advisory policy (`WARNINGS_AND_SETTINGS.md`) is what makes this a setting a
  graph can change rather than a hard-coding.
- **Frame-crossing consultation**: when a frame-scoped answer looks thin or
  an associated frame may be relevant, the agent proposes consulting it and
  the user approves, with borrowed knowledge always labelled with its frame.
  Ask when the crossing is significant (fiction against real, or genuine
  uncertainty); just do it with provenance for expected pulls.
- **Archival approval** (§12.3) reuses the same channel: `reflect` surfaces
  `archival_candidates` the way it surfaces `pending_review`, the user
  approves in conversation, `apply_reflection(archivals=[…])` applies.

Most of this is agent guidance (`epimemer_prompts/DEFAULT.md`) plus the
visibility the data model provides: provenance on every node, frames on
search, association edges.

---

## 8. Worked example: frame coexistence

- Base reality (`the-real`): *"Napoleon lost at Waterloo"*.
- Novel-X frame: *"Napoleon won at Waterloo"*.

These do not contradict: different frames, both kept. An optional
`variant_of` edge between them records "same proposition, diverges here", so
*"where does Novel-X depart from reality?"* is a graph traversal rather than
a re-derivation. `Novel-X --based_on--> the-real` records the frame
relationship without inheriting any facts.

---

## 9. Foundations the loop rests on

- `status` ∈ {ACTIVE, CORRECTED, HISTORICAL, MERGED, ARCHIVED}, plus the
  legacy SUPERSEDED that nothing writes; every reader uses
  `SUPERSEDED_STATUSES` rather than an equality against one member.
- `supersede_by`, `merge_nodes`, `write_batch_tx` and `set_node_status_tx`
  are atomic on both backends; edge migration on retirement is per edge type
  (`migration_disposition`).
- `vector_search` and `text_search` take a `statuses` set, defaulting to
  active only.
- Every decision leaves a journal row (`REVIEW_MODE.md`).

---

## 10. Decisions

- Contradiction handling is unified into the review loop, not separate.
- Fact merging exists, gated on `claim_kind`, frames and the nomination bar
  (§3); topic merging remains the default consolidation.
- `variant_of` is in the vocabulary, so divergences are queryable.
- Association, not inheritance, for metacontexts.
- Human-in-the-loop is in conversation, with notification on genuine
  same-frame contradictions and on frame-crossing.
- Ingest-time detection (`check_conflicts`) is opt-in, with `reflect` as the
  safety net; one nomination bar (0.80) serves contradiction, recurrence and
  merge gating.
- A dashboard panel for human resolution is possible later; in conversation
  first.

---

## 11. Rejected

- **Merge for facts on similarity alone.** Similarity cannot distinguish a
  duplicate from a contradiction or a succession; the merge is gated on an
  agent's verdict and on `claim_kind`.
- **A single "supersede" for corrections and world-changes.** It files
  historical truth as error (`VALIDITY_DESIGN.md`).
- **Untagged nodes reading as base reality.** Silence became an assertion
  about the real world; the frame is required at ingest instead.
- **Inheritance between frames.** The diamond problem, imported into
  knowledge.
- **A qualified `evidence_stale` for merges** (§4.1).
- **Importance as the way to keep a nominated node** (§4.2).

---

## 12. Value model and graph hygiene

Wrong knowledge is handled by the verdicts above. Trivial knowledge (small
decisions, transient error records, one-off details worth writing but not
worth keeping) would otherwise accumulate forever, active and retrievable,
diluting every similarity search. This is the hygiene arm of the loop.

### 12.1 The value model

`ValueSignal` on every node holds two stored signals with different
dynamics, and a third is computed:

| Dimension | Moves down | Moves up | Answers |
| --- | --- | --- | --- |
| `retrieved_at` | never; a timestamp does not decay | stamped on retrieval | "is this being used?" |
| `importance`, with `importance_judged_at` | judgment only, via `judge_importance` | explicit judgment: `judge_importance`, or human review | "does this matter?" |
| structural importance (computed) | | | knowledge-edge in-degree, read live at candidacy; edges in `NON_KNOWLEDGE_EDGE_TYPES` do not count |

Nothing automatic may erode a judgment: an agent that marks a node important
is recording an assessment, not starting a timer. Nothing in the system
lowers `importance` except another judgment; what ages instead is confidence
in the judgment's currency, expressed by `importance_judged_at` and read by
the `stale_judgment` nomination class.

`confidence` is a **caller-supplied prior** with a four-value ladder, stored
absent when unrated rather than as a middling number, with a
`confidence_basis` in `node.metadata`. *How well-supported by evidence* is a
judgment about material only the ingesting agent has read, so it is stored;
*multiple independent sources increase confidence* is a fact about the graph
that changes as the graph does, so it is derived at read time as
**corroboration** (distinct source documents, better distinct `published_by`
entities) and never writes the field.

Two fields were removed rather than fixed, and the reasons are the rule
worth keeping. A decayed `relevance` float could not answer *is this used?*
because its value depended on how often an operator ran `reflect`; a nullable
timestamp separates *never* from *long ago* without that confound, and
removing decay made `reflect` a pure read. A stored `novelty` could not be
stored honestly at all: measured at ingest it answers "unexpected relative to
what the graph held then", frozen for the life of the node, while the
question anyone wants is against the graph as it stands, which the
nearest-neighbour distance already answers. In each of `relevance`,
`novelty` and `confidence` the stored form had mixed two meanings, and the
fix was to ask which half the stored form serves and derive the other at
read time.

Merges combine value signals through one shared `merged_value_signal`: a
field-by-field rebuild silently resets what it forgets to name, and one
function means the next field added to `ValueSignal` has one place to be
considered rather than two places to be missed.

### 12.2 Upward paths

1. **Usage recording (automatic).** A node returned by `search` gets
   `retrieved_at = now`. System-driven, no judgment.
2. **Agent judgment (explicit).** `judge_importance(node_id, direction,
   reason, related_id=None)` moves `importance` up or down and records why,
   and optionally which new node triggered the re-assessment. Deliberately
   not a raw setter: every bump leaves an auditable trace.
3. **Structure (derived).** Knowledge-edge in-degree, computed at read time.

### 12.3 Cleanup: the archival arm of the review loop

Same three-tier shape as detection: cheap nomination, agent judgment, human
approval. Cost stays proportional to the junk, not the graph.

- **Nominate (mechanical, no LLM):** retired nodes with low importance;
  `evidence_stale` inferences; active facts never retrieved since creation
  (`retrieved_at is None`) with low importance and zero knowledge in-degree;
  nodes whose importance judgment is stale. `HISTORICAL` nodes are excluded:
  a node retired because the world changed is still true of its period, so
  ageing it out would be the same defect one level down. `evidence_merged` is
  never nominated on (§4.1).
- **Judge (agent):** the agent reviews the nominated set with graph context.
  Importance is judged at reflect time, not ingest time, because triviality
  is only visible once the neighbourhood exists ("error message X" matters
  until the bug is fixed, then does not). The agent may judge a nominee up,
  or keep it with `retained` (§4.2), instead of letting it go.
- **Approve (human, in conversation):** `reflect` surfaces
  `archival_candidates`; `apply_reflection(archivals=[…])` applies the
  approved set. Archive, never delete: export via the archive path, and
  `restore` reverses.

**Status `ARCHIVED`.** Archiving an active trivial fact removes it from the
active set: approved archival is export plus an atomic status flip through
`set_node_status_tx`, which is a generic status-flip transaction rather than
an archival-specific one. Existing `status = 'active'` filters then exclude
it with no further changes; `restore` flips it back and appends a lifecycle
episode rather than re-inserting.

**Inference follow-on.** Archiving a fact walks `derived_from`: an inference
whose entire evidence set is now archived or retired joins the next candidate
list, flagged for the same review, never auto-archived. Inferences are the
expensive-to-recreate layer.

### 12.4 Decisions

- `importance` is a stored field; structural importance is computed, blended
  at candidacy time, never cached.
- Retrieval reinforcement is on by default (k writes per search).
- Importance is judged at reflect time; `store_decomposition` accepts an
  optional per-node importance as a prior.
- Cleanup is archive-only. Deletion stays out of the system.
- `judge_importance` records provenance for every judgment, in both
  directions; there is no raw setter.
- **Value signals do not feed search ranking.** Recording use creates a
  feedback loop (retrieved → ranked higher → retrieved). At archival
  granularity that loop is benign: it only protects used nodes from cleanup.
  Wired into ranking it would compound: popular nodes crowd out better
  matches, and are then protected from cleanup for it. If ranking ever wants
  a value term, that is a deliberate future decision with its own analysis.

---

## 13. Temporal validity, as it lands on this loop

The model is `VALIDITY_DESIGN.md`. What it changes here:

- **Two verdicts, not one.** `supersedes` means correction alone; `succeeds`
  is the world moving; `recurs` is a historical twin true again (§3). The
  node and the edge cannot disagree about which act happened:
  `superseded_status_for(because)` and `lineage_edge_type_for(status)` in
  `core/types.py` are read together, and `RESTORABLE_STATUSES` and
  `NOMINATED_STATUSES` say once which statuses come back and which are
  nominated.
- **Recall includes `HISTORICAL`** (§5.1), and `reflect` reports
  `recurrences` beside `contradictions`.
- **The soundness check is a reflect phase.** `find_unsound_inferences`
  flags an inference whose premises' asserted intervals do not intersect,
  reporting the offending premise pairs with their periods, because the
  agent's move is a judgment and a verdict with its evidence hidden cannot be
  argued with. `assertions_are_disjoint` holds only when both sides carry
  periods and every cross pair compares `before` or `after`; a pair that
  cannot be placed blocks the finding rather than counting as disjoint. Reflect
  rather than ingest, because an inference joining a 1970 document to a 2000
  one is invisible while either is stored alone.
- **Boundary proposals are a reflect phase.** `propose_boundaries` moves a
  date one document gave onto a fact from another, licensed by the
  `temporally_followed_by` edge the agent already wrote, and never
  manufactures a date from publication dates.
- **Retrieval folds lineage.** When a historical node and its successor both
  match, the successor takes the slot and the historical node attaches to
  it. The fold reads the *status*, not the edge: two `ACTIVE` nodes joined by
  a lineage edge are two current claims, which is the shape `restore` leaves
  behind. The walk is cycle-safe, since recurrence closes a cycle on ordinary
  data, and a cycle has no last version, so its best-ranked member hosts the
  rest. The top-k cut is taken after the fold.
- **`ValidityVerdict` has two members**, *valid* and *unknown*. Under
  open-world semantics nothing can prove a claim was not true at a moment
  without a closed-world marking, so an *excluded* bucket would be
  unreachable rather than empty, and a value nothing can produce earns a
  dead branch in every caller. A valid-time filter is therefore not merely
  dishonest but unimplementable: there is no negative to filter on.
  `valid_as_of` and `timeline_id` both come from the caller and neither
  defaults, so an unasked question gets no verdict and there is no clock for
  a first implementation to reach for.
- **Intervals are half-open**, `[start, end)`. Under closed intervals the
  exact instant of the 1991 renaming is one at which the city is provably
  called both names, and every adjacent pair of periods overlaps by a point,
  which would fire the soundness check on ordinary succession.
- **Merging two nodes from the same document keeps both edges' intervals.**
  Edge migration collapses duplicates by `(src, dst, type)`, which would drop
  a provenance edge and everything it asserted, exactly where "intervals
  survive merges for free" would quietly stop being true. Both backends hand
  the loser's intervals to the survivor. That is not the union the model
  forbids: that union is across sources, while these came from the same
  document about what is now the same claim.
