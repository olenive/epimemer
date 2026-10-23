# The epistemic review loop

The model behind the review loop that `check_conflicts`, `reflect` and the
verdict tools implement: outdated facts, stale inferences, contradictions,
metacontext-relative truth, trivial knowledge and claims true of a period.
`docs/REFLECTION.md` and `docs/VALIDITY.md` describe what a caller sees; this
page holds the decisions and their reasons.

---

## 1. Principles

The principles (similarity nominates and an agent decides, nothing is
destroyed, consequential calls go to the user) are `docs/REFLECTION.md` §1.
Claims are never mutated in content; corrections create new versions linked
by history edges (`superseded_by`, `temporally_followed_by`, `merged_into`).
Lifecycle metadata (`status`, `superseded_at`, `value` signals, lifecycle
episodes) is mutated in place, because it is not the claim.

## 2. One loop

Contradiction, staleness, metacontext coexistence, succession, recurrence and
triviality are outcomes of one loop: similarity nominates, the agent judges,
the verdict is recorded as edges or journal rows, and retrieval computes
review labels from them. Cleanup (§12) is the same loop with another
nominator.

---

## 3. Verdict taxonomy

`docs/REFLECTION.md` §2 has the verdicts and the action each takes.

- **`supersedes` and `succeeds` are separate**, because *we were wrong* and
  *the world moved* are opposite claims about history. `succeeds` is to the
  period what a cross-metacontext verdict is to the metacontext.
- **`recurs` exists** because otherwise a recurrence is forced into
  `redundant` (which assumes an active twin) or `succeeds` (which assumes a
  different claim). It needs nomination to include `HISTORICAL` (§5.1).
- **`merge_facts` refuses, with a reason, whenever the graph cannot vouch for
  the merge**, leaving two nodes, one `similarity` edge, and corroboration
  reading the neighbourhood.
- **The event/state judgment is recorded at ingest** (`claim_kind` on `Fact`),
  because only the document can tell *"Labour won the election"* in 1997 from
  the same sentence in 2024, and merging two events would union their periods
  into one long win. A fact with no `claim_kind` is unmergeable, which is the
  safe direction.

Evidential staleness has its own trigger, without similarity (§5.2).

---

## 4. Data model

### 4.1 Review labels (computed, not stored on the node)

Retrieval computes a label per returned node from its edges
(`docs/REFLECTION.md` §4); the node stays `ACTIVE` and the label names the
contesting or retired node.

`evidence_merged` is its own label rather than a kind of `evidence_stale`,
because archival nominates on `evidence_stale` and would otherwise propose
discarding every dependent of every merge. The merge flag edge is the only
record a merge leaves on a dependent, since its `derived_from` edge migrates
onto an `ACTIVE` survivor.

### 4.2 Edges the loop writes

`supersession_candidate`, `evidence_superseded`, `evidence_merged`,
`assessed`, `retracted_similarity` and the history edges are excluded from
default traversal and from edge migration (`NON_KNOWLEDGE_EDGE_TYPES`),
because they are records about claims. `similarity`, `contradiction` and
`variant_of` are followed in traversal but stay on the retired node all the
same, because a judgment is about the wording judged: after a correction from
500,000 to 5,000,000, a counterpart judged *one claim* against the old figure
would otherwise corroborate the new one.

**A keep is a `retention` journal row, not an edge.** Its `covers` lists the
reasons it answers, and a nomination survives the keep when the node's current
reasons are not all covered, because a node's neighbourhood keeps moving and a
keep must not silence the next change. It is not an importance judgment,
because raising `importance` to stop a nomination would make one field mean
two things.

**A keep on a `never_retrieved` node is permanent** (its empty `covers` can be
outranked by nothing) until `reopen`, so *not now* is `deferred`
(`docs/REFLECTION.md` §6 has the waits and settings). A deferral counts reflect
rounds, a reflect that completes with at least one store since the previous
one, because a clock would age a deferral on a graph nobody touched. The wait
has a ceiling (`MAX_DEFERRAL_CAP`, 1024 rounds), enforced on write and clamped
on read, because a deferral promises eventual review.

### 4.3 Metacontext model

- **Every ingested node names its metacontext**: `metacontext_id` is required
  on `store_decomposition`, and synthesis and splits inherit their sources'.
  `the-real` is the conventional id for real-world claims; nothing reads it
  specially.
- **Absence names no metacontext**, as an omitted `confidence` is unrated. A
  node with no `has_metacontext` edge shares a metacontext with nothing, so it
  is never nominated, merged or returned by a scoped search. It exists only on
  graphs written before the requirement, and `epimemer metacontexts declare`
  is how a person states which metacontext those nodes were in.
- **Contradiction is metacontext-relative**: within one metacontext it is
  genuine; across disjoint metacontexts both claims coexist.
- **Association, not inheritance.** Metacontexts are linked by `based_on`
  edges and facts never flow between them, which keeps the diamond problem out
  of the knowledge layer.
- **Search takes an optional list of metacontexts**, because *every
  metacontext* is a coherent question at search, while ingest without one
  would be an unstated assumption.

---

## 5. Detection

### 5.1 Contradiction, supersession, succession, recurrence

`check_conflicts` nominates, for each new fact, `ACTIVE` and `HISTORICAL`
facts above the nomination bar (`SIMILARITY_NOMINATION_THRESHOLD`, 0.80), each
with its `status`, because telling `redundant` from `recurs` is the
active/retired distinction. `CORRECTED` is never nominated. `reflect` keeps a
similarity sweep as a safety net, because `check_conflicts` is opt-in, and
reports mixed active/historical pairs under `recurrences`, separate from
`contradictions`. `store_decomposition` reports facts that are word-for-word a
retired claim, and never acts on them.

**Ingest order is not validity order**: a 1970s memoir ingested today is older
truth. Direction comes from `published_at` and interval witness points where
they exist (`VALIDITY_DESIGN.md`); for undated pairs the agent reasons from the
documents.

A sweep recomputed from current state records declines, or a declined pair
returns on every reflect (`REVIEW_MODE.md` §1).

### 5.2 Evidential staleness

Retiring a fact adds, in the same transaction, `evidence_superseded` edges to
every inference `derived_from` or supported by it; a merge adds
`evidence_merged` edges from the absorbed fact to its dependents. This is
graph propagation, with no similarity.

---

## 7. Human in the loop

In conversation, with no separate UI. Which conflicts reach the user is a
warning policy a graph can change (`WARNINGS_AND_SETTINGS.md`), and how an
agent applies it is guidance in `epimemer_prompts/DEFAULT.md`.

## 8. Worked example: metacontext coexistence

*"Napoleon lost at Waterloo"* in `the-real` and *"Napoleon won at Waterloo"* in
a novel's metacontext do not contradict, and both are kept. An optional
`variant_of` edge records *same proposition, diverges here*, so *where does the
novel depart from reality?* is a traversal. `novel --based_on--> the-real`
records the relationship between the metacontexts without inheriting facts.

---

## 11. Rejected

- **Merging facts on similarity alone**: similarity cannot tell a duplicate
  from a contradiction or a succession.
- **One "supersede" for corrections and world changes**: it files historical
  truth as error.
- **Nodes without a metacontext reading as base reality**: silence would become
  an assertion about the real world.
- **Inheritance between metacontexts**: the diamond problem, in knowledge.

---

## 12. Value model and graph hygiene

Trivial knowledge would otherwise accumulate and dilute every similarity
search.

### 12.1 The value model

| Signal | Moves | Answers |
| --- | --- | --- |
| `retrieved_at` | stamped by `search`; never decays | is this being used? |
| `importance`, `importance_judged_at` | only by judgment (`judge_importance`, or the user) | does this matter? |
| structural importance | computed: knowledge-edge in-degree, excluding `NON_KNOWLEDGE_EDGE_TYPES` | is anything built on it? |

Nothing automatic erodes a judgment; what ages is the judgment's currency,
read through `importance_judged_at` by the `stale_judgment` nomination class.

`confidence` is a caller-supplied prior on a four-value ladder, stored absent
when unrated, with a `confidence_basis`. *Multiple sources* is a fact about the
graph that changes as the graph does, so it is derived at read time as
corroboration and never written to `confidence`. The same rule keeps decayed
relevance and stored novelty out: store the judgment, derive the fact about
the graph.

### 12.2 Upward paths

`search` stamps `retrieved_at`; `judge_importance(node_id, direction, reason,
related_id=None)` moves `importance` with a recorded reason, never as a raw
setter, so every change is auditable; structure is computed at read time.

### 12.3 Cleanup: the archival arm of the review loop

Cheap mechanical nomination, agent judgment, user approval, so cost follows
the junk rather than the graph. `docs/REFLECTION.md` §5 lists the classes and
why `HISTORICAL` nodes are never nominated.

- **Importance is judged at reflect time, not at ingest**, because triviality
  shows only once the neighbourhood exists.
- **Archiving a fact walks `derived_from`**: an inference whose whole evidence
  set is archived or retired is nominated, never auto-archived, because
  inferences are expensive to recreate.

### 12.4 Decisions

- Structural importance is computed at candidacy, never cached.
- **Value signals do not feed search ranking.** Use protects nodes from
  cleanup, which is benign; in ranking it would compound, popular nodes
  crowding out better matches. Adding a value term to ranking is a separate
  decision with its own analysis.

---

## 13. Temporal validity, as it lands on this loop

The model is `VALIDITY_DESIGN.md`; `docs/VALIDITY.md` and `docs/RETRIEVAL.md`
§6 have what a caller sees. What it changes here:

- **`supersedes` is correction alone, `succeeds` the world moving, `recurs` a
  historical twin true again** (§3). One declaration maps the reason to the
  status and the status to the edge, so node and edge cannot disagree.
- **Recall includes `HISTORICAL`** (§5.1).
- **`ValidityVerdict` has two members**, valid and unknown, because nothing
  can fill an *excluded* bucket (`VALIDITY_DESIGN.md` §4.1). `valid_as_of` and
  `timeline_id` come from the caller and neither defaults.
- **Intervals are half-open**, `[start, end)`, because closed intervals make
  every adjacent pair of periods overlap by a point and fire the soundness
  check on ordinary succession.
- **A merge of two nodes from the same document keeps both edges'
  intervals.** Edge migration collapses duplicates by `(src, dst, type)` and
  would drop one, so every backend hands the loser's intervals to the
  survivor; this is not the cross-source union the model forbids.
