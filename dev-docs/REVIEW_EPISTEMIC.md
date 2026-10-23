# The epistemic review loop

A design, built: the model and the reasons behind the review loop that `check_conflicts`, `reflect` and the verdict tools implement.

How Epimemer reviews and reconciles knowledge over time: outdated facts, stale
inferences, contradictions, metacontext-relative truth, trivial knowledge, and
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
   clear-cut calls (dedup, obvious supersession, same-metacontext routing). It
   escalates the epistemically consequential ones to the human (genuine
   contradictions, crossing metacontext boundaries, archival). Human-in-the-loop
   is in-conversation.

---

## 2. The unified review loop

Contradictions, staleness, metacontext coexistence, temporal succession and
triviality are not separate subsystems. They are outcomes of one loop: cheap
candidate generation by embedding similarity, a precise judgment by the
agent, the verdict recorded as edges that are the durable source of truth,
review labels computed from those edges at retrieval, and resolution by the
agent or, where it matters, by the human. Cleanup (§12) is one more arm of
the same loop: nomination plays the candidate-generation role, the agent
judges, the human approves.

---

## 3. Verdict taxonomy

`docs/REFLECTION.md` §2 has the verdicts and the action each takes. The
reasons for the shape:

`supersedes` and `succeeds` keep apart what a single "supersede" would
conflate: *we were wrong* against *the world moved*. `succeeds` is
the temporal sibling of `cross-metacontext`: one says the *metacontext* differs, the
other says the *period* differs. `recurs` exists because without it a
recurrence is forced into `redundant` (which assumes an active twin) or
`succeeds` (which assumes a different claim following). It can only fire
because nomination includes `HISTORICAL` candidates (§5.1).

**`merge_facts` refuses, out loud and with a reason, whenever the graph
cannot vouch for the merge**, and every refusal leaves the agent where it
was: two nodes, one `similarity` edge, corroboration reading the
neighbourhood.

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

Edges are the source of truth; retrieval computes a label per returned node
(`docs/REFLECTION.md` §4 lists them). The node stays `ACTIVE`, and the label
names the contesting or retired node, so the caller can hop to it.

`evidence_merged` is its own label rather than a qualified `evidence_stale`,
and the reason is not tidiness. Archival (§12.3) nominates on
`evidence_stale`, so sharing the label would propose discarding an inference
because its premise got *better* provenance, on every merge, for every
dependent. The two events also want different work from the agent: a
correction says re-derive, a merge says re-read against the new wording. A
merge changes a premise without retiring it (the `derived_from` edge migrates
onto the survivor, which is `ACTIVE`), so the flag edge written at merge is
the only record the event will ever leave.

### 4.2 Edges the loop writes

`supersession_candidate`, `evidence_superseded`, `evidence_merged`,
`assessed`, `retracted_similarity` and the history edges are excluded from default graph traversal
and from edge migration: they are records about claims rather than claims.
`similarity`, `contradiction` and `variant_of` are real edges to follow but
are anchored on every retirement all the same: a
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

**A keep on a `never_retrieved` node is permanent, and `deferred` is the
answer for *not now*.** The empty `covers` that makes the keep sound also
means no later reason can outrank it, so it stands until `reopen` withdraws
it. A `deferral` journal row instead holds the node back for a number of
reflect rounds, two after the first deferral and doubling to a cap of 32,
measured from the `at_round` stamped on the row. A round is a reflect that
completes with at least one store since the previous reflect: a second look
is worth something only once something has been stored, and a clock would age
a deferral on a graph nobody touched. When the wait is over the node comes back
first among the `never_retrieved` nominees, carrying what was said last time.
A later keep, or a one-node `reopen` while the node is still waiting, clears
the deferral, and the tool accepts a deferral only for a node the nominator's
test counts as a never_retrieved nominee. The cap is at most 1024 rounds,
because eventual review is what a deferral promises; the ceiling is enforced on
write and a stored value above it is clamped on read, so a graph holding one
keeps working.

### 4.3 Metacontext model

- **Every ingested node names its metacontext.** `metacontext_id` is required on
  `store_decomposition`, and synthesis and splits inherit their sources'
  metacontexts. `the-real` is the conventional id for the metacontext holding real-world
  claims; nothing reads it specially.
- **Absence names no metacontext.** A node with no `has_metacontext` edge is a node
  nobody said anything about, consistent with every other absence in the
  model (an omitted `confidence` is unrated, an absent `judged_by` is
  unknown). Such a node shares a metacontext with nothing, so it is never nominated
  as contradicting anything, never merged, and never returned by a scoped
  search. It is reachable only on a graph written before the requirement,
  and `epimemer metacontexts declare` is how a person states which metacontext those
  nodes were always in.
- **Contradiction is metacontext-relative.** Same metacontext: a genuine contradiction,
  to resolve. Disjoint metacontexts: not a contradiction; both coexist, each in
  its own.
- **Association, not inheritance.** Metacontexts are linked by association edges;
  facts never flow automatically between metacontexts. Reaching into an associated
  metacontext is an explicit, agentic and often human-gated choice, never an
  inheritance walk, which is what keeps the diamond problem out of the
  knowledge layer.
- **Search takes a list of metacontexts**, returning nodes standing in any of them,
  and no metacontext inherits another. Omitting the list searches every metacontext,
  which is a coherent question rather than an unstated assumption, the reason
  it is optional at search where the metacontext on ingest is not.

---

## 5. Detection

### 5.1 Case A: contradiction, supersession, succession, recurrence

At ingest, `check_conflicts` nominates candidates for each new fact from
`ACTIVE` and `HISTORICAL` facts above the one nomination bar (0.80), each
carrying its `status`, because telling `redundant` from `recurs` *is* the
active/retired distinction and a candidate list that hid it would invite the
misclassification the verdict exists to prevent. `CORRECTED` is never
nominated. `reflect` keeps a similarity sweep as a safety net, because
`check_conflicts` is opt-in and a graph whose agent never ran it would never
be asked. The sweep reports the mixed active/historical pairs under
`recurrences`, separately from `contradictions`, because a claim beside its
own successor is not a contradiction and filing it under that word is the
misreading `recurs` exists to prevent. A cheap floor sits under both:
`store_decomposition` reports facts just stored that are word-for-word a
retired claim, and never acts on them.

**Nomination order is ingest order, and ingest order is not validity
order.** A 1970s memoir ingested today is older truth, so a recency-driven
`supersedes` or `succeeds` verdict would point backwards in time. Direction
comes from `published_at` and per-interval witness points where they exist
(`VALIDITY_DESIGN.md`), and for undated pairs the judging agent has to
reason from the documents rather than from arrival.

**A sweep recomputed from current state must record declines**, or a
declined pair returns on every reflect to an agent who cannot know it was
refused (`REVIEW_MODE.md` §1).

### 5.2 Case B: evidential staleness

When a fact is retired, the same transaction adds `evidence_superseded` edges
to every inference that is `derived_from` or supported by it. When a fact is
merged, the merge adds `evidence_merged` edges from the absorbed fact to
its dependents. No similarity needed: pure graph propagation.

---

## 6. Resolution

A flagged or contested node is resolved by retiring the loser, by coexisting
in separate metacontexts (often the right answer), by escalating to the
human, or by leaving it contested, in which case retrieval flags both facts so
nothing downstream trusts a contested fact blindly. A flagged inference is
re-derived, retired, or kept with the reasons it was re-read against (§4.2).

---

## 7. Human-in-the-loop

In conversation, not a separate UI. A genuine same-metacontext contradiction
is raised with the user; a cross-metacontext "conflict" does not interrupt
them, and the warning policy (`WARNINGS_AND_SETTINGS.md`) is what makes this a
setting a graph can change rather than a hard-coding. Consulting an
associated metacontext and approving an archival go through the same channel.
How an agent applies this is guidance, in `epimemer_prompts/DEFAULT.md`.

---

## 8. Worked example: metacontext coexistence

- Base reality (`the-real`): *"Napoleon lost at Waterloo"*.
- Novel-X metacontext: *"Napoleon won at Waterloo"*.

These do not contradict: different metacontexts, both kept. An optional
`variant_of` edge between them records "same proposition, diverges here", so
*"where does Novel-X depart from reality?"* is a graph traversal rather than
a re-derivation. `Novel-X --based_on--> the-real` records the metacontext
relationship without inheriting any facts.

---

## 11. Rejected

- **Merge for facts on similarity alone.** Similarity cannot distinguish a
  duplicate from a contradiction or a succession; the merge is gated on an
  agent's verdict and on `claim_kind`.
- **A single "supersede" for corrections and world-changes.** It files
  historical truth as error (`VALIDITY_DESIGN.md`).
- **Untagged nodes reading as base reality.** Silence became an assertion
  about the real world; the metacontext is required at ingest instead.
- **Inheritance between metacontexts.** The diamond problem, imported into
  knowledge.

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

The same rule keeps a decayed relevance score and a stored novelty out of the
model: each would mix a judgment with a fact about the graph that changes as
the graph does. Ask which half a stored form serves, and derive the other at
read time.

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
`docs/REFLECTION.md` §5 lists the nomination classes.

- **Nominate (mechanical, no LLM).** `HISTORICAL` nodes are excluded: a node
  retired because the world changed is still true of its period, so ageing it
  out would be the same defect one level down. `evidence_merged` is never
  nominated on (§4.1).
- **Judge (agent):** the agent reviews the nominated set with graph context.
  Importance is judged at reflect time, not ingest time, because triviality
  is only visible once the neighbourhood exists ("error message X" matters
  until the bug is fixed, then does not). The agent may judge a nominee up,
  keep it with `retained`, or put a `never_retrieved` one off with
  `deferred` (§4.2), instead of letting it go.
- **Approve (human, in conversation).** Archive, never delete: export, then a
  status flip, and `restore` reverses.

**Inference follow-on.** Archiving a fact walks `derived_from`: an inference
whose entire evidence set is now archived or retired joins the next candidate
list, flagged for the same review, never auto-archived. Inferences are the
expensive-to-recreate layer.

### 12.4 Decisions

- Structural importance is computed, blended at candidacy time, never cached.
- **Value signals do not feed search ranking.** Recording use creates a
  feedback loop (retrieved → ranked higher → retrieved). At archival
  granularity that loop is benign: it only protects used nodes from cleanup.
  Wired into ranking it would compound: popular nodes crowd out better
  matches, and are then protected from cleanup for it. If ranking ever wants
  a value term, that is a deliberate future decision with its own analysis.

---

## 13. Temporal validity, as it lands on this loop

The model is `VALIDITY_DESIGN.md`; `docs/VALIDITY.md` and `docs/RETRIEVAL.md`
§6 have the soundness check, boundary proposals and the lineage fold as a
caller sees them. What it changes here:

- **Two verdicts, not one.** `supersedes` means correction alone; `succeeds`
  is the world moving; `recurs` is a historical twin true again (§3). The
  node and the edge cannot disagree about which act happened, because one
  declaration maps the reason to the status and the status to the edge.
- **Recall includes `HISTORICAL`** (§5.1), and `reflect` reports
  `recurrences` beside `contradictions`.
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
  survive merges for free" would quietly stop being true. Every backend hands
  the loser's intervals to the survivor. That is not the union the model
  forbids: that union is across sources, while these came from the same
  document about what is now the same claim.
