# Epimemer — Epistemic Review Loop (design)

**Status (2026-06-27):** Phase 1 (atomicity) ✅; Phase 2a (vocabulary) ✅;
Phase 2b ✅ — supersede-by-existing + Case B propagation (2b.1) and detection &
recording tools `check_conflicts` / `record_contradiction` / `record_variant`
plus frame helpers and a frame-aware `reflect` sweep (2b.2); Phase 2c ✅ —
computed review labels on `search`/`query_graph` results + frame-scoped search;
Phase 2d ✅ — `reflect` surfaces the `pending_review` worklist and
`apply_reflection supersessions=[...]` resolves flagged nodes; Phase 2e ✅ —
agent guidance in `epimemer_prompts/DEFAULT.md`. **Phase 2 complete.** Phase 3 ✅
— opt-in `ws://` SurrealDB integration test (skipped unless reachable; verified
green against Dockerised SurrealDB). Phase 4 ✅ — the review-loop design ingested
into the `epimemer-docs` graph (15 topics / 55 facts / 2 inferences, tagged
`epimemer-repo-docs`). **All phases complete.**

**Update (2026-08-07):** §12 extends the loop with a value model and an archival
(hygiene) arm — designed, **not built**. Implementation plan: ISSUES.md #35–37.

Decisions settled for 2b: separate `check_conflicts` tool (opt-in); pre-compute
frame + scores; dedicated verdict tools; agent authority per §7; build
`supersede(old, by=existing)` (done, **does not migrate edges**); Case B
direct-only auto-flag (no inference-on-inference yet).
**Purpose:** single spec for how Epimemer reviews and reconciles knowledge over
time — outdated facts, stale inferences, contradictions, and frame-relative
("metacontext") truth — and how that work is split between the agent and the
human.

This document supersedes the scattered design discussion. Build against it.

---

## 1. Background & principles

Epimemer is an append-only, immutable-history epistemic memory. Nodes are never
mutated in content; corrections create new versions linked by history edges
(`superseded_by`, `temporally_followed_by`, `merged_into`). Lifecycle *metadata* (`status`, `superseded_at`,
`value` signals) is mutated in place — it is not the knowledge claim, so editing it
rewrites no history (see SUMMARY.md → Node History).
The guiding rhythm is **"write fast, organize slow"**: ingestion is mechanical and
cheap; organization (consolidation, review) is deliberate and, where it matters,
*agentic*.

Three principles drive this design:

1. **Detection = cheap recall + precise judgment.** Embeddings are a good
   *candidate generator* ("these facts are about the same thing") but a poor
   *judge* ("do they contradict / supersede / coexist?"). Similarity nominates;
   an agent decides.
2. **Nothing is destroyed; ambiguity is made visible.** Outdated/contested nodes
   stay `ACTIVE` and retrievable, but carry a computed review label so anything
   reading them knows they may be superseded or contested. Hard supersession is
   a deliberate, reversible act (history is preserved; archival is export, not
   delete).
3. **Two-tier epistemic responsibility.** The agent handles mechanical, clear-cut
   calls (dedup, obvious supersession, same-frame routing). It **escalates the
   epistemically-consequential ones to the human** (genuine contradictions,
   crossing frame boundaries). Human-in-the-loop is in-conversation.

---

## 2. The unified review loop

Contradictions, staleness, and metacontext coexistence are **not separate
subsystems** — they are outcomes of one loop. (§12 later extends the same loop
with a fourth outcome, *triviality* → archival; cleanup is one more arm of this
loop, not a new subsystem.)

```
new/changed knowledge
        │
        ▼
  candidate generation   ← cheap: embedding similarity (recall)
        │
        ▼
  agentic judgment       ← precise: the agent classifies the relationship
        │
        ├─ redundant        → record SIMILARITY, keep both (see §3)
        ├─ supersedes       → correction: superseded_by, old → CORRECTED
        ├─ succeeds         → world moved: temporally_followed_by → HISTORICAL
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

---

## 3. Verdict taxonomy

When a newly-ingested fact is similar to an existing active fact, the agent
classifies the pair:

| Verdict | Meaning | Action |
| --- | --- | --- |
| **redundant** | same claim restated | dedup or ignore |
| **supersedes** | new corrects old — the old claim was wrong | correction (label old `superseded_candidate`; resolves via `superseded_by`, old → `CORRECTED`) |
| **contradicts** | conflicting claims, same frame, unclear which holds | record `CONTRADICTION`; resolve (agent/human) |
| **cross-frame** | "conflict" only because frames differ (fiction vs real) | not a conflict; both coexist; optional `variant_of` |
| **succeeds** | both true, over different periods — the world moved | write `temporally_followed_by` (old → new); old node → `HISTORICAL`, restorable (#53 T2) |
| **recurs** | the same claim, previously retired `HISTORICAL`, is true again | surface the historical twin; **explicit reactivation** — a `restore` widened to accept `HISTORICAL`, plus a new `sourced_from` edge with the new document's interval, in one transaction (#53 T2, third pass) |
| **compatible** | no conflict | nothing |

A separate, non-similarity trigger handles **evidential staleness**: when a fact
is superseded, inferences derived from it become suspect.

> **The sixth row was missing until 2026-08-12 and is now filled (#53 T2).**
> The table had no verdict for *both true, over different periods*, and
> `supersedes` quietly assumes the old version was wrong ("newer **correct**
> version") — true of a correction, false of a change in the world. `succeeds`
> is the temporal sibling of `cross-frame`, and it is an **edge** rather than
> only a label: `temporally_followed_by` states order, not replacement, so it
> survives a claim becoming true again. `supersedes` now means *correction*
> alone. A **seventh** row, `recurs`, was added by the second pass (same date):
> without it the taxonomy forces a recurrence into `redundant` (assumes an
> active twin) or `succeeds` (assumes a different claim). It can only ever fire
> if nomination includes `HISTORICAL` candidates — §5.1's note.
>
> **`recurs` is built (2026-08-19).** Nomination sees `HISTORICAL` through
> `vector_search(statuses=...)`, `check_conflicts` reports each candidate's
> status because that is the whole basis for choosing between `redundant` and
> `recurs`, and the verdict resolves through a `restore` that reactivates the
> twin and writes the new source's edge in one transaction. `CORRECTED` is
> refused at both ends — never nominated, never restored.

> **And `redundant` is a dead branch (review 2026-08-12).** Its action column
> says "dedup or ignore", but no fact-merge action exists — merge is wired for
> topics only — so the verdict either no-ops silently or tempts the judging
> agent into a supersession whose required `because` has no honest answer:
> "same claim" is neither *it was wrong* nor *the world changed*. That is the
> same forced-wrong-verdict failure as the missing sixth row, and unlike that
> one it is live today. Until dedup lands (ISSUES.md #52, deferred behind #53),
> the honest action for `redundant` is **record `SIMILARITY` and keep both** —
> which is also exactly what corroboration (#51) consumes.

---

## 4. Data model

### 4.1 Review labels (computed, not stored on the node)

Edges are the source of truth; retrieval **computes** a label per returned node:

| Label | Condition | Case |
| --- | --- | --- |
| `superseded_candidate` | node has an incoming `supersession_candidate` edge | A — temporal |
| `evidence_stale` | inference has `evidence_superseded` edge / `derived_from` a superseded fact | B — evidential |
| `contested` | node has a `contradiction` edge unresolved in its own frame | contradiction |

The node stays `ACTIVE`. Labels are surfaced the same way `metacontexts` already
are on search results, alongside the contesting/retired node id so the caller
can hop to it.

### 4.2 New / newly-used edge types

| Edge | From → To | Meaning |
| --- | --- | --- |
| `supersession_candidate` | newer fact → older fact | "this may replace that — review" (Case A) |
| `evidence_superseded` | superseded fact → dependent inference | "this inference's basis changed" (Case B) |
| `contradiction` | fact ↔ fact | genuine same-frame conflict (the enum exists today but is **never created** — wire it up) |
| `variant_of` | fact ↔ fact (across frames) | "same proposition, resolved differently per frame" — makes divergence queryable |
| `temporally_followed_by` | older fact → newer fact | "both true, over different periods" — order, **not** replacement, so it survives recurrence (#53 T2; designed, not built) |
| `based_on` / `associated_with` | metacontext → metacontext | frames relate (association, **not** inheritance) |

`supersession_candidate`, `evidence_superseded`, `contradiction`, and the history
edges are all excluded from default graph traversal and from edge migration
(treated like history/metadata, not knowledge).

### 4.3 Metacontext model

- **"The Real" is the default/base frame** — our actual physical reality. It is a
  **reserved, canonical metacontext with a stable id**, matched by id, *not* by
  content text (so a fiction frame that internally talks about "reality" is never
  confused with the base frame).
- **Untagged ⇒ implicitly The Real.** No backfill; the reserved id is available
  when explicit tagging is needed. Two untagged conflicting facts → genuine
  contradiction (both in base reality).
- **Contradiction is metacontext-relative:**
  - same frame (or both base) → *genuine* contradiction → resolve.
  - disjoint frames → *not* a contradiction → both coexist, framed.
  - one/both untagged-but-should-differ → prompt to assign the right frame
    (detection doubles as metacontext-hygiene).
- **Association, not inheritance.** Frames are linked by association edges; facts
  **never flow automatically** between frames. Reaching into an associated frame
  is an explicit, agentic (and often human-gated) choice — never an inheritance
  walk. This deliberately avoids the diamond/"triangle" problem for knowledge:
  there is no automatic resolution to be ambiguous.
- **Retrieval is frame-scoped by default** (a search in Frame F returns F +
  untagged-base, not other frames). Cross-frame is opt-in.

---

## 5. Detection

### 5.1 Case A — contradiction / supersession (reactive, at ingest)

1. After a document's facts are extracted, for each new fact the system runs a
   small vector lookup over active facts and returns the top-K matches above a
   **high, configurable threshold** (the recall stage). Cheap; few matches at a
   high bar.
2. The **agent judges** each candidate (the verdict table in §3) and records the
   appropriate edge(s). Detection is agentic; similarity only nominates.
3. `reflect` keeps the existing similarity sweep as a **safety net** for anything
   an ingest missed.

Cost note: one similarity lookup per new fact adds ingest latency. It is
configurable (threshold + on/off); can fall back to a `reflect`-time sweep if it
bites.

> **Review 2026-08-12: nomination order is ingest order, and ingest order is
> not validity order.** The judging agent sees each pair from the newer
> *document* — but a 1970s memoir ingested today is older *truth*, so a
> recency-driven `supersedes` (or, once it exists, `succeeds`) verdict points
> backwards in time. Verdicts about temporal succession must be
> validity-directed, not arrival-directed, and for undated pairs that needs
> world knowledge the agent may not have. ISSUES.md #53, review item 3.

> **Second pass (2026-08-12): step 1's recall must include `HISTORICAL`
> candidates once #53 T2 lands.** Both nomination passes scan active facts
> only today, so a historical twin is never nominated and the `recurs` verdict
> (§3) can never fire — active-only recall is precisely what hides the twin
> the recurrence design depends on surfacing. ISSUES.md #53 → T2, second pass.
>
> **Third pass, same date — where that change lives.** `check_conflicts` does
> no status filtering of its own; it inherits `vector_search`'s, which is
> `ACTIVE`-only on both backends *by design* (*"Superseded and merged nodes must
> never resurface here"*). Widening recall therefore means one `statuses`
> parameter on `vector_search`, defaulting to `{ACTIVE}` so nothing changes
> until a caller asks — and the same parameter is what §13.10's
> `include_historical` default needs, so it is one change with two customers.
> **And the candidates must carry their `status`:** telling `redundant` from
> `recurs` *is* the active/retired distinction, so a candidate list that hides
> it invites the misclassification the verdict exists to prevent.
>
> **Built 2026-08-19, and both passes were widened, not just the ingest one.**
> `vector_search` takes `statuses`, `check_conflicts` asks for
> `{ACTIVE, HISTORICAL}` and returns each candidate's status. Reflect's sweep —
> step 3's safety net, which matters because `check_conflicts` is opt-in and a
> graph whose agent never ran it would never be asked — nominates the same set
> and reports the mixed pairs under **`recurrences`**, separately from
> `contradictions`. Separately on purpose: a claim beside its own successor is
> not a contradiction, and filing it under that word is the misreading `recurs`
> exists to prevent, arriving from the other side. The wider sweep still scores
> one matrix — the set is partitioned after scoring, not scored twice — because
> this is the phase that crosses the tool timeout as a graph grows (#39).
>
> A cheap floor sits under both: `store_decomposition` reports
> **`historical_twins`**, facts just stored that are word-for-word a retired
> claim. It reports and never acts, and it is affordable only because #48 was
> fixed in the same visit — one indexed lookup per fact rather than a table
> scan.

### 5.2 Case B — evidential staleness (reactive, at supersede)

When `update`/`supersede` retires a fact, the **same atomic operation** adds
`evidence_superseded` edges to every inference that `derived_from`/`supports` it.
No similarity needed — pure graph propagation. (Requires the atomic-supersede
path; see Phase 1.)

---

## 6. Resolution

A flagged/contested node is resolved by one of:

1. **Supersede the loser** — agent-decidable from recency/source. Needs a new
   action (§6.1).
2. **Coexist via metacontexts** — re-classify as cross-frame; ensure both facts
   are tagged; optionally add a `variant_of` link. Often the *right* answer (the
   apparent conflict was a framing difference).
3. **Escalate to the human** — when the agent can't/shouldn't decide. Surfaced
   in-conversation; the user decides; the agent applies the outcome.
4. **Leave contested** — until resolved, retrieval flags both facts so nothing
   downstream trusts a contested fact blindly.

### 6.1 New action: `supersede(old_id, by=existing_id)`

Today `update(node_id, new_content)` only ever creates a *brand-new* node, and
`link(A, B, "superseded_by")` would add an edge without flipping A's status. So
we add `supersede(old_id, by=existing_id)`: atomically mark `old` superseded by
an **existing** node (status + `superseded_by` edge + clear the candidate edge).
`reflect` surfaces the flagged set for batch review; `apply_reflection` may gain
a `supersessions` / resolution action.

---

## 7. Human-in-the-loop

- **In-conversation**, not a separate UI (a dashboard panel is a possible
  later add).
- **Notify on genuine same-frame contradictions:** the agent says "new fact
  conflicts with existing fact X *in the same context* — how should I resolve
  it?" Cross-frame "conflicts" do **not** interrupt the user (they aren't
  conflicts; at most a quiet "tagged to frame Y").
- **Frame-crossing consultation:** when a frame-scoped answer looks thin or an
  associated frame may be relevant, the agent **proposes** consulting it and the
  user **approves** — *"this is in Frame F; associated Frame G may have relevant
  info — include it?"* — always labeling borrowed knowledge with its frame. Don't
  nag: ask when the crossing is significant (different-nature frames, e.g.
  fiction ↔ real, or genuine uncertainty); just-do-with-provenance for expected
  pulls (a frame `based_on` The Real drawing base facts).

- **Archival approval (§12.3):** cleanup reuses this same channel — `reflect`
  surfaces `archival_candidates` the way it surfaces `pending_review`, the user
  approves in-conversation, `apply_reflection(archivals=[...])` applies. No
  separate cleanup UI or workflow; archival is just another resolution the
  human signs off on.

Most of this is **agent guidance** (in `epimemer_prompts/DEFAULT.md` / the system
prompt) plus the visibility the data model already provides (provenance on every
node, `metacontext_id` filtering on `search`, association edges).

---

## 8. Worked example (metacontext coexistence)

- Base reality (untagged ⇒ The Real): `"Napoleon lost at Waterloo"`.
- Novel-X frame (tagged `Novel-X`): `"Napoleon won at Waterloo"`.

These **do not contradict** — different frames; both kept. An optional
`variant_of` edge between them records "same proposition, diverges here," so
*"where does Novel-X depart from reality?"* is a graph traversal rather than a
re-derivation. `Novel-X --based_on--> The Real` records the frame relationship
without inheriting any facts.

---

## 9. Already in place (foundation)

- Immutable history: `status` ∈ {ACTIVE, CORRECTED, HISTORICAL, MERGED,
  ARCHIVED} (plus legacy SUPERSEDED, which nothing writes any more);
  `superseded_by` / `temporally_followed_by` / `merged_into` lineage;
  `query_nodes` filters to ACTIVE.
- `supersede`/`merge` are **atomic** (backend-native single transaction);
  `update` carries the node's value signal; edges migrate + dedupe on
  supersede/merge; `vector_search` excludes non-active nodes.
- `merge_nodes` wired into `apply_reflection merges=[...]` (topics-only, pairwise
  similarity bar 0.92).
- `reflect` surfaces `similar_pairs`, `split_candidates`, `enrichment_candidates`,
  `contradictions` (the last is similarity-only and surfaced, **not** recorded).
- Metacontexts: flat `HAS_METACONTEXT` tags; `create_metacontext`,
  `get_metacontexts_for_node`; `search` filters by `metacontext_id` and surfaces
  metacontext labels.
- Archival sweeps both SUPERSEDED and MERGED (export, restorable).

Gaps this design fills: `CONTRADICTION` edge unused; `update_value_on_contradiction`
is dead code; no ingest-time detection; no recording/resolution; no
human-in-the-loop; no metacontext association; inferences never revisited.

---

## 10. Phased plan

**Phase 1 — Full atomicity (foundation; resume in-progress work)**
- `write_batch_tx(*, nodes, edges, embeddings)` primitive — pure-insert,
  all-or-nothing. Protocol + InMemory (snapshot/restore) + SurrealDB (one
  `BEGIN…COMMIT`) + InstrumentedStorage (delegate + events). Rollback tests both
  backends.
- `store_decomposition` → accumulate the whole document, write once (atomic per
  document).
- `apply_reflection parents`/`splits` → plan then batch-write atomically.

**Phase 2 — Unified review loop**
- 2a. ✅ **Done.** Vocabulary: edge types `supersession_candidate`,
  `evidence_superseded`, `variant_of`, `based_on` added; edge categories
  `REVIEW_EDGE_TYPES` / `NON_KNOWLEDGE_EDGE_TYPES` defined and wired into edge
  migration (both backends) + default traversal exclusion; reserved base
  metacontext (`BASE_METACONTEXT_ID` + `ensure_base_metacontext`). (`contradiction`
  edge type already existed; it gets *created* in 2b.)
- 2b.1. ✅ **Done.** Resolution backbone: `supersede(old, by=existing)` (storage
  `supersede_by_existing_tx` both backends + wrapper, domain `supersede_by_existing`,
  tool `supersede_by`, MCP); **Case B** propagation (`evidence_superseded` flags on
  direct dependent inferences) folded atomically into *both* supersede paths;
  candidate-edge clearing on supersession. Helpers in
  `pipelines/reflection/review.py` (`plan_evidence_stale_edges`,
  `find_candidate_edge_ids_into`).
- 2b.2. ✅ **Done.** Detection & recording tools (all opt-in, agent-driven):
  - `frames_of(node_id, storage)` / `same_frame(a, b, storage)` in `review.py` —
    metacontexts of a node, treating untagged as `BASE_METACONTEXT_ID`; two nodes
    share a frame if their frame sets overlap (untagged⇒base, so two untagged are
    same-frame; disjoint frames are not).
  - `check_conflicts(fact_ids, storage, embedding_provider, *, threshold=0.83,
    k=5)` — per fact, vector-search active facts (exclude self) above threshold;
    returns candidates with score + the candidate's metacontext labels + same-frame
    flag. Tool + MCP. Opt-in; the agent calls it on freshly-ingested facts.
  - `record_contradiction(a_id, b_id, storage)` — idempotent `contradiction` edge
    (one per pair, either direction); both stay active; returns `notify_user`
    (= same-frame) and a warning when cross-frame. Tool + MCP.
  - `record_variant(a_id, b_id, storage)` — idempotent `variant_of` edge; warns
    when the pair shares a frame. Tool + MCP.
  - `reflect` frame-aware sweep: `detect_contradictions` output filtered to keep
    only same-frame pairs (safety net for anything ingest missed).
  - Edge dedup helper `_ensure_symmetric_edge`; `_metacontext_labels` factored out
    of `search`. Tests: frame helpers + `check_conflicts` + both record tools +
    frame-aware `reflect` (382 passing).
- 2c. ✅ **Done.** Retrieval visibility: `review_labels(node, storage)` in
  `review.py` derives `superseded_candidate` (incoming `supersession_candidate`),
  `evidence_stale` (inference with `evidence_superseded` flag and/or `derived_from`
  a SUPERSEDED fact), and `contested` (a `contradiction` to an ACTIVE same-frame
  node — cleared once the partner is retired or if cross-frame), each mapped to
  the related node ids. Surfaced as a `review` field on `search` and `query_graph`
  results, alongside `metacontexts`. `search` is now frame-scoped: a
  `metacontext_id` returns that frame **plus** untagged base-reality nodes, with
  `cross_frame=True` to opt out (MCP exposes it). `_metacontext_labels` reused.
- 2d. ✅ **Done.** Resolution: `supersede_by`/`record_contradiction` (2b) plus
  `gather_pending_review(storage)` in `review.py` (active nodes carrying review
  labels) surfaced as `reflect`'s `pending_review` worklist, and a batch
  resolution action `apply_reflection(supersessions=[{old_id, by_id}])` that calls
  `supersede_by_existing` per pair (atomic; Case-B flagging + candidate clearing;
  missing/self pairs skipped). Resolving the loser auto-clears the winner's
  `contested`/`superseded_candidate` labels (computed from active partners). MCP
  exposes both.
- 2e. ✅ **Done.** Agent guidance rewritten in `epimemer_prompts/DEFAULT.md`:
  current ingest flow (`segment`→`store_decomposition`), the detect→classify→record
  loop (`check_conflicts` + verdict→tool table), review labels at retrieval,
  `reflect`'s `pending_review` + `apply_reflection supersessions`, frame-scoped
  search/`cross_frame`, and human-in-the-loop (notify on same-frame contradictions,
  propose-and-approve on frame-crossing). Prose only; the file is not loaded by
  code or tests.

**Phase 3 — Integration tests (opt-in, low priority) ✅**
- `tests/storage/test_surrealdb_integration.py`: real `ws://` SurrealDB run,
  skipped unless `EPIMEMER_SURREAL_WS_URL` points at a reachable server (no
  connection attempted by default — not a CI gate). Covers connection/auth and
  transaction atomicity under genuine concurrency (separate ws connections):
  concurrent write-batches all commit, a colliding batch rolls back fully while
  good ones commit, concurrent supersedes on distinct nodes apply cleanly. Each
  test uses a unique throwaway database, dropped on teardown. Verified green
  against `surrealdb/surrealdb:latest` in Docker.

**Phase 4 — Refresh `epimemer-docs` graph ✅**
- The review-loop design (this document, distilled to grounded prose) ingested
  into `epimemer-docs` via `segment` → `store_decomposition`, tagged with the
  existing `epimemer-repo-docs` metacontext: 15 topics, 55 facts, 2 inferences,
  207 edges (graph now 420 nodes / 1097 edges). The graph previously held only the
  pre-review-loop design (it still listed "contradictions" as an *open* question);
  it now carries the unified loop, verdict taxonomy, review labels, edge types,
  metacontext/"The Real" model, detection (Case A/B), resolution, and decisions.
- Note: the *running* MCP server predates the review-loop code, so the new tools
  (`check_conflicts`, `supersede_by`, frame-scoped `search`) aren't live until it
  is restarted. Once restarted, running `check_conflicts` on the new design facts
  would surface (and `supersede_by` could resolve) the now-stale "open question"
  nodes about contradiction handling — the review loop applied to its own memory.

Checkpoints: the suite stays green and the user commits at each phase boundary;
each step is independently reviewable.

---

## 11. Decisions

**Settled**
- Merge stays **topics-only** (similarity can't distinguish duplicate from
  contradiction for facts; inferences are meant to coexist).
- Contradiction handling is **unified** into the review loop, not separate.
- Base frame **"The Real"**, **implicit** untagged membership, **reserved id**.
- `variant_of` is **in v1** (divergences queryable).
- **Association, not inheritance**, for metacontexts.
- Human-in-the-loop is **in-conversation**, with notification on genuine
  same-frame contradictions and on frame-crossing.
- Atomicity (Phase 1) lands **before** the review loop.

**Still open**
- Final names for the new edge types/labels.
- Whether ingest-time detection defaults **on** or **off** (and the threshold).
- Dashboard panel for human resolution (later; in-conversation first).

---

## 12. Value model & graph hygiene (designed and built 2026-08-07)

Phases 1–4 handle *wrong* knowledge — superseded, contradicted, evidentially
stale. They do nothing about *trivial* knowledge: small decisions, transient
error records, one-off details that were worth writing but not worth keeping.
Under principle 2 these accumulate forever — active, retrievable, diluting
every similarity search. This section extends the review loop with a hygiene
arm. **Built 2026-08-07** — ISSUES.md #35–37 carry the implementation notes,
including four things the plan below did not anticipate (a generic status-flip
transaction rather than an archival-specific one; `restore` needing to flip
rather than re-insert; `last_reinforced == created_at` never being exactly
true (since removed — the two clocks are now nullable and named for what
writes them); and segment anchors having to be excluded from structural in-degree).

### 12.1 The value model, revised

`ValueSignal` exists on every node (`novelty` / `confidence` / `relevance` /
`retrieved_at`, `core/types.py`) but is write-only today. Its only writers
are creation defaults, `apply_decay` (down only, uniform across all active
nodes), and topic-merge; nothing reinforces it and nothing reads it — not
retrieval ranking, not archival candidacy. Relevance is therefore a monotone
function of age and carries no information about a node's worth.

> **Outcome (ISSUES.md #44, since resolved): `relevance` was deleted, not
> fixed.** Adding retrieval reinforcement made it non-monotone, as planned
> below — and it still had no reader, because §12.4 rules it out of ranking on
> purpose and archival ended up reading `retrieved_at` instead. The deeper
> problem was that a decayed float could not answer the question anyway: its
> value depends on how often an operator ran `reflect`, so 0.3 might be "used
> once, long ago" or "used often, on a busy graph". A nullable timestamp
> separates *never* from *long ago* without that confound. `apply_decay` went
> with it, which makes `reflect` a pure read.
>
> **The same audit checked the siblings, and they are not in the same position**
> — worth recording so the question is not reopened from scratch:
> `confidence` *is* read (topic merge picks the higher-confidence description as
> primary) and both it and `novelty` are rendered in the viz tooltip, so neither
> was write-only in the way `relevance` was. But neither is ever *computed*:
> every ingested node gets 1.0 / 0.5 and nothing updates them, which makes the
> merge comparison a permanent tie. That is ISSUES.md **#46**, and it is a
> different problem with a different answer — the fields are documented as
> measurements and are really unset priors, so the fix is more likely to be
> letting the calling agent supply them than deleting them.
>
> **Outcome (2026-08-11): #46 was split, and the two halves went different
> ways.** Bundling them was the mistake — they shared a symptom (an uncomputed
> constant documented as a measurement) and nothing else. #46 now covers
> `confidence` alone; the `novelty` half is resolved and its entry deleted.
>
> **`novelty` was deleted.** Not for want of a reader, and not because
> computing it was expensive: the number cannot be stored honestly at all.
> Measured at ingest it answers "unexpected relative to what the graph held
> *then*" — a fact about arrival order, frozen for the life of the node — while
> the question anyone wants asked is against the graph as it stands. That one is
> well-posed at any time and already answerable from the nearest-neighbour
> distance `vector_search` returns, so it needs no field, no migration and no
> baseline convention. The name was also carrying two meanings: *new to the
> graph*, which `created_at` gives exactly, and *unlike what is known*, which is
> the one that mattered. **"Surprise" is the better term** for the second and is
> now what the design docs say — it names unexpectedness rather than newness, and
> it makes its own precondition audible (surprising relative to *what*). It is
> reserved for a caller-supplied signal if one is ever wanted: an
> observer-relative name suits a reported judgment, as `importance` is, and
> misleads on a computed one — surprisal has a definition (−log p) with
> additivity a cosine distance does not have.
>
> **`confidence` stayed open as #46 and was decided on 2026-08-12.** It has a
> live reader, it is already returned to the caller, and unlike `novelty` a
> stored form is defensible — so it survives, as a **caller-supplied prior**
> with a four-value ladder and written tool guidance, on the same footing as
> `importance`.
>
> **The decision corrected a claim made here.** This section previously offered
> "an objective definition already computed elsewhere
> (`knowledge_in_degree_for`, used by archival)" as a point in `confidence`'s
> favour. That was wrong twice. Archival consumes that function as *structural
> importance* — its own docstring says so — so deriving `confidence` from it
> would have produced `importance` under a second name, which is the defect
> family this whole audit was clearing. And in-degree does not measure
> corroboration in the first place: ten inferences drawn from one document raise
> it tenfold and add no independent support.
>
> **So the documented promise was two claims, and it split along that seam** —
> the third field in this review to do so. *"How well-supported by evidence"* is
> a judgment about material only the ingesting agent has read, and stays the
> stored prior. *"Multiple independent sources increase confidence"* is a fact
> about the graph that changes as the graph does, so it is derived at read time
> from distinct source documents — better, distinct `published_by` entities —
> and never writes the field. That is ISSUES.md **#51**.
>
> Recorded because the shape keeps recurring: **`relevance` mixed use with
> judgment, `novelty` mixed newness with unexpectedness, `confidence` mixed
> support with corroboration.** In all three the fix was to ask which half the
> stored form serves, and to derive the other at read time.
>
> **The same audit found the model's one live defect (#45, since resolved).**
> Both merge sites rebuilt the signal field by field and named only the scalars,
> so a merged node carried `importance = max(sources)` forward while both clocks
> reset to null. The lost timestamp was not the damage; the false *pair* was.
> `judgment_is_stale` reads importance and its date together, and an unjudged
> node is correctly never stale — so the class below, which exists precisely so
> that importance cannot protect a node forever, was unreachable for anything a
> merge produced. Fixed by one shared `merged_value_signal` in `core/types.py`:
> a field-by-field rebuild silently resets what it forgets to name, and one
> function means the next field added to `ValueSignal` has one place to be
> considered rather than two places to be missed.

The revision splits value into two dimensions with different dynamics:

| Dimension | Moves down | Moves up | Answers |
| --- | --- | --- | --- |
| ~~`relevance`~~ → `retrieved_at` | n/a — a timestamp does not decay | stamped on retrieval | "is this being used?" |
| `importance` (new) | judgment only, via `judge_importance` | explicit judgment: agent `judge_importance` tool, human review | "does this matter?" |

Nothing automatic may erode a judgment: an agent that marks a node important is
recording an assessment, not starting a timer. That is why importance is a
separate field rather than a usage bump. With decay gone the rule is easier to
hold — nothing in the system lowers `importance` except another judgment — and
what ages instead is *confidence in the judgment's currency*, expressed by
`importance_judged_at` and read by the `stale_judgment` nomination class.

A third signal is **computed, not stored**: *structural importance* — a node's
knowledge-edge in-degree (inferences `derived_from` it, facts supporting it).
"New information makes X more important" usually arrives as an edge, so the
graph already holds the evidence; candidacy reads it live rather than caching
a number that goes stale. Edges in `NON_KNOWLEDGE_EDGE_TYPES` (§4.2) do not
count.

All of this is lifecycle metadata under §1 — mutable in place, no history
rewrite.

### 12.2 Upward paths

1. **Usage recording (automatic).** A node returned by `search` gets
   `retrieved_at = now`. System-driven, no judgment. (As built this also raised
   an asymptotic `relevance` float; that field is gone — the timestamp is now
   the whole of what retrieval records.)
2. **Agent judgment (explicit).** New tool
   `judge_importance(node_id, direction, reason, related_id=None)`: moves
   `importance` up or down and
   records *why* — and optionally *which* new node triggered the
   re-assessment. Deliberately **not** a raw setter: every bump leaves an
   auditable trace, so a human reviewing a trivial-looking fact rated high can
   see the justification. Same asymptotic step form as above.
3. **Structure (derived).** Knowledge-edge in-degree, computed at read time
   (see 12.1).

### 12.3 Cleanup: the archival arm of the review loop

Cleanup is **not a new subsystem** — it is one more arm of the §2 loop, reusing
every stage that already exists: nomination plays the candidate-generation
role, the agent judges, resolution goes through `reflect` →
`apply_reflection`, and the human approves in-conversation exactly as §7
prescribes for contradictions. Nothing new is invented except the nomination
heuristics and the `ARCHIVED` status; everything else is the proven
`pending_review` pattern with a different verdict.

Same three-tier shape as detection (§1, §5): cheap nomination → agent
judgment → human approval. Cost stays proportional to the junk, not the graph.

- **Nominate (mechanical, no LLM):** in priority order — superseded/merged
  nodes with low importance (the existing age-based candidates, now
  value-aware); then `evidence_stale` inferences; then *active* facts never
  retrieved since creation (`retrieved_at is None`) with low
  importance and zero knowledge in-degree. `source_type` may weight the
  ordering (chat/error-log before document).
- **Judge (agent):** the LLM reviews the nominated set *with graph context*.
  Importance is judged at reflect time, not ingest time — triviality is only
  visible once the neighbourhood exists ("error message X" matters until the
  bug is fixed, then doesn't). The agent may judge a nominee up instead of
  letting it go.
- **Approve (human, in-conversation):** `reflect` surfaces an
  `archival_candidates` worklist (exactly like `pending_review`);
  `apply_reflection(archivals=[...])` applies the approved set. **Archive,
  never delete** — export via the existing archive path, `restore` reverses.

**New status `ARCHIVED`.** Today `archive_nodes` is export-only and its
candidates are already SUPERSEDED/MERGED, so nothing needed to change state.
Archiving an *active* trivial fact must remove it from the active set:
approved archival = export **+** atomic status flip to `ARCHIVED`. Existing
`status = 'active'` filters (queries, `vector_search`) then exclude it with no
further changes; `restore` flips it back.

**Inference follow-on.** Archiving a fact walks `derived_from`: an inference
whose *entire* evidence set is now archived/superseded joins the next
candidate list — flagged for the same review, never auto-archived. Inferences
are the expensive-to-recreate layer.

### 12.4 Decisions

- `importance` is a **stored field**; structural importance is **computed** —
  blended at candidacy time, never cached.
- Retrieval reinforcement is **on by default** (k writes per search; boost
  configurable).
- Importance is judged **at reflect time**; `store_decomposition` *may* accept
  an optional per-fact importance as a prior (default 0.5).
- Cleanup is **archive-only**. Deletion stays out of the system.
- `judge_importance` records provenance for every judgment, in both
  directions; there is no raw setter.
- **Value signals do not feed search ranking.** Recording use creates a
  feedback loop (retrieved → ranked higher → retrieved). At archival
  granularity that loop is benign — it only protects used nodes from cleanup.
  Wired into ranking it would compound: popular nodes crowd out better matches,
  and are then protected from cleanup for it. If ranking ever wants a value
  term, that is a deliberate future decision with its own analysis, not a free
  by-product of this design. This is the rule that left `relevance` with no
  consumer and eventually removed it (#44).

---

## 13. Temporal validity — the Saint Petersburg Problem (found 2026-08-12)

Filed as ISSUES.md **#53**, recorded here because it is an epistemic defect
rather than an implementation one, and because it lands squarely on §3 and §5.1
of this document.

### 13.1 The finding

**The graph cannot say when a claim was true.** A `Fact` carries `created_at`
(when the graph learned it) and `superseded_at` (when the graph stopped
believing it). Neither is *validity* — the period the claim actually held.

Saint Petersburg became Petrograd in 1914, Leningrad in 1924, and Saint
Petersburg again in 1991. Every one of those names was correct. Given
*"the city is called Leningrad"* and *"the city is called Saint Petersburg"*,
the model has two moves available and both misdescribe the pair: call it a
contradiction (neither is false) or supersede one by the other (which files a
historical truth as an error and removes it from the active set).

### 13.2 What it does to the review loop this document specifies

**§3's verdict taxonomy has five verdicts and needs a sixth.** Read the
`supersedes` row again: *"new replaces old (newer correct version)"*. The
parenthesis assumes the old version was **in**correct. That assumption is the
bug in miniature — it is true of a correction and false of a change in the
world. There is no verdict for *both true, different periods*, so the agent
classifying a pair is forced into a wrong one:

| Verdict | What it assumes | Saint Petersburg |
| --- | --- | --- |
| `supersedes` | the old was wrong | ✗ it was right, then stopped being |
| `contradicts` | one of them is false | ✗ neither is |
| `cross-frame` | the frames differ | ✗ same frame, different *time* |
| `redundant` | same claim | ✗ different claims |
| `compatible` | no relation to record | ✗ there is one, and it is temporal |

The missing verdict — call it **`succeeds`** — means *the world changed; both
hold over their own periods*. **The status half of it now exists** (2026-08-12):
`NodeStatus.HISTORICAL` alongside `NodeStatus.CORRECTED`, chosen by a required
`because` on `update` / `supersede_by` / `apply_reflection`. The table above is
still short a row — this records which of the two happened, not that both claims
remain true of their periods. It is the temporal sibling of `cross-frame`, and
the parallel is exact: `cross-frame` says "not a conflict, the *frame* differs",
`succeeds` says "not a conflict, the *period* differs". §4.3's metacontext model
already establishes that two contradictory-looking claims can coexist when they
are indexed by something; time is the second such index and is unmodelled.

**§5.1's similarity recall makes it likelier, not less likely.** The pass
nominates the top-K most similar active facts, and successive states of the same
subject are maximally similar — same entity, same predicate, differing only in
value. So the pair most likely to be nominated is precisely the pair the
taxonomy cannot classify.

### 13.3 Why it outranks the rest of the open list

Everything else open is a defect inside a sound model. This one says the model
cannot express something true, and it propagates:

- **Inference is unsound.** Deriving from a fact set with no validity lets an
  inference combine claims that were never simultaneously true, with nothing to
  detect it. This is the layer the system exists to provide.
- **Supersession destroys history** rather than dating it (§6.1's
  `supersede(old_id, by=existing_id)` is the mechanism).
- **Contradiction detection is unsound in both directions** — temporal change
  reads as conflict, and genuine conflict is indistinguishable from change.
- **Corroboration (#51) inflates**, and **dedup (#52) cannot be made safe**.

### 13.4 The constraint that decides the design

**Validity is a set of intervals, not an interval.** A claim can be true, stop,
and become true again: *"the Labour Party is in government"* holds over 1945–51,
1964–70, 1974–79, 1997–2010 and 2024–. Saint Petersburg is the same shape.

This eliminates the cheap answer (two nullable datetimes on the node cannot hold
a disjoint set) and it eliminates a lineage chain (alternating claims would need
`succeeded_by` in both directions between one pair — a cycle). It also **inverts
the outlook for dedup**: identical claims recurring over disjoint periods are one
node whose intervals union, so a set model turns #52's worst case into its
cleanest.

`Timepoint` (`core/types.py:377`) already models intervals *and* already
tolerates vagueness — `label` alone, "during the Renaissance" — which matters
because vagueness is the normal case here ("under the USSR", "before the
merger"). Attaching several validity timepoints per node gives the set for free
and composes with both timelines and metacontexts. That is the recommendation;
see #53 for the alternatives and the open sub-question about whether the gaps
between intervals need to be explicit.

### 13.5 What is built

**The floor, and only the floor** — ISSUES.md #53 step 1, guarded by
`tests/pipelines/test_supersession_kind.py`. Supersession now records *why*: a
node retired because the world changed is `HISTORICAL`, one retired because it
was wrong is `CORRECTED`, and the caller must say which. Archival skips the
historical ones, which is what stops the graph ageing out things that are still
true of their period.

`SUPERSEDED` is kept as a legacy value rather than migrated, because rows
written before the split do not record which kind they were and assigning one
would be a fabrication — the same reasoning that keeps `retrieved_at` nullable.

**What it does not do**, and what the interval model is still for: recurrence
(a claim becoming true again has nowhere to say so) and validity dates of any
kind.

> **Amended 2026-08-19.** Validity dates now exist and are stored — on the
> `sourced_from` edge, supplied at ingest (§13.8). Recurrence still has nowhere
> to say so: the detector is step 4 and the `recurs` verdict is unbuilt. Nothing
> reads validity yet either, so the floor is wider than it was but the sentence
> above still describes what the graph *does* with any of it.

**Edge ownership was the third item and is now fixed** (ISSUES.md **#54**, built
2026-08-12). It did not wait for the interval model, because migration was a
**move** — `_migrate_edges_inplace` re-pointed edges in place — so every
world-change supersession stripped the historical node of its own provenance,
and that damage accrued in data while the design was pending. Migration is now
**per edge type**, via `migration_disposition(edge_type, status)`: a correction
moves everything but history and review; a world-change moves nothing, keeps
provenance and the judgments made about the old claim on the old node, and
copies only `has_metacontext` and `tagged_with`. Both blanket answers were tried
and withdrawn — copying everything fabricates attribution, and migrating nothing
drops the frame, which would move a fiction-frame replacement into base reality.
The *validity* half still waits for the interval model, but it waits safely: the
intervals will ride on `sourced_from` edges that no longer go anywhere.

### 13.6 The method that found it

**When an issue is blocked on a precondition, check whether the precondition's
absence is itself the larger defect.** #52 was blocked on "can we require
temporal agreement before merging two facts?" The answer was no, and the reason
was not about dedup at all. Fourth method worth reusing, alongside the three in
ISSUES.md.

### 13.7 Review findings (2026-08-12)

A design review of the open set (#46, #51, #52, #53) confirmed recommendation
(b) and found six places where it is not yet decidable as written. The full
detail — evidence, options, and what each decision must say — lives in
ISSUES.md as blockquotes marked *Review 2026-08-12* inside each entry; this is
the shape, so this document stays the design record:

- **§13.5's floor and §13.4's recommendation disagree about what a
  world-change *is*** — node replacement (old → `HISTORICAL`, new node) versus
  an interval closing on one `ACTIVE` node. Two mechanisms for one event; the
  decision must say which owns it once intervals exist, or agents face a
  forced choice at mechanism grain — the §13.2 failure, one level up.
- **The empty validity set is the common case, and it currently means three
  things** — *always true*, *unknown*, and *nobody supplied it*. Validity is
  exactly what is not in the text, so population is agent world-knowledge,
  supplied unevenly. Representation was the easy half of the problem; absence
  needs an explicit reading before anything consumes it.
- **Ingest order is not validity order** (§5.1 note above): succession
  verdicts must be validity-directed, which argues for verdicts carrying their
  proposed intervals so a human can check the direction.
- **Vague timepoints make the inference-soundness check three-valued** —
  overlap / disjoint / *unknown*, and unknown is the common outcome since
  vagueness is the normal case by §13.4's own argument. What unknown does is a
  decision, not an accident of control flow.
- **`HISTORICAL` has no retrieval reader yet** — search filters to `ACTIVE`,
  so corrected and historical are indistinguishable to a searching agent:
  gone. And `as_of` is transaction time; adopt the bitemporal vocabulary
  (valid time vs transaction time — Snodgrass, XTDB/Datomic) before the two
  axes blur in the tool surface.
- **Validity timepoints need a concrete home** — timepoints are embedded in
  timelines and referenced weakly from `TIMELINK` edge metadata; "several
  validity timepoints per node" has to name its timeline and harden that
  reference before soundness depends on it.

The same review amended the neighbouring designs: #46 (store the unrated
confidence case as absent, not 0.5; record a basis alongside non-default
priors), #51 (exclude contradictors and variants from the corroboration
neighbourhood; state that corroboration and confidence do not interact), and
#52 (the interval model dedupes *states*, never *events* — the re-open trigger
now carries that distinction, and §3's `redundant` verdict has an interim
action).

One finding did not wait for any decision: world-change supersession *moves*
the old node's provenance onto its replacement, so the node kept for being
true of its period cannot say who asserted it. Filed as **#54**, actionable
now.

### 13.8 T1 decided (2026-08-12) — the shape of valid time

The decision was split three ways in dependency order: **T1** what a validity
interval *is* and where it lives, **T2** which mechanism owns a world-change,
**T3** the retrieval surface and naming. T1 is settled. The full statement —
eleven numbered sections, with the arguments and the rejected alternatives —
lives in `ISSUES.md` #53 under *T1 decided*; this section records the shape and
what it changes about the design above.

**Valid time versus transaction time.** The vocabulary is fixed before the
fields exist, because the two axes blur silently once there is code and are
near-impossible to separate afterwards. Valid time is when a claim was true;
`created_at`, `superseded_at` and `as_of` are transaction time.

**T1 supersedes §13.4's recommendation (b).** Validity is not `Timepoint`s hung
off a new edge type. Six things decide the shape:

1. **It lives on the `sourced_from` edge, per source** — not on the node. A
   node-level set must union what its sources assert, and union takes one
   careful source and one sloppy one and yields a period *neither claims*. Same
   failure as a false dedup manufacturing corroboration. It also puts validity
   beside #46's per-source confidence, on the same edge for the same reason.
2. **It is read back per source, with no default collapse.** Union breaks when
   sources disagree about the same episode; intersection breaks when they
   describe different episodes; nothing in the data says which. That is §3's
   state-versus-event distinction arriving from the temporal side.
3. **Endpoints distinguish `unknown` from `unbounded`.** *"The city is named
   Placeberg"* has an unknown start; *"water is H₂O"* has no start. Collapsing
   them reproduced the empty-set ambiguity one level down, which is why
   `Timepoint` is **not** reused — its `start: datetime | None` means both, and
   it models *mention* time, a different thing from *true during*.
4. **Each interval names a timeline, not a metacontext.** The axes cross both
   ways: one frame can need two clocks (a revision to a fictional history has a
   real publication date *and* in-universe dates), and two frames can share one
   (competing accounts of real history both run on CE dates). Cross-clock
   comparison returns `unknown`, never `disjoint` — which makes an inference
   spanning fiction and fact temporally uncheckable, the temporal sibling of
   `cross-frame`.
5. **The agent is not a source.** Hallucinated validity would be
   indistinguishable from documented validity once stored, and the server makes
   no LLM calls. Accepted consequence: the Leningrad case cannot get its 1991
   boundary from world knowledge — only from a document, or from reflect seeing
   two documents. Since the line between reading and inventing is not clean
   (judging tense is already reading), every interval is marked **stated** or
   **inferred**, and a caller can filter to stated-only. Without the marking,
   "stick to the source" is a prompt instruction with nothing checking it.
6. **`RawDocument` gains an optional `published_at`**, because `created_at` is
   ingestion time and using it as evidence about when a claim held is
   transaction time wearing valid time's clothes. **No fallback**: no
   publication date means no witness point, since a fallback would have every
   undated document claim its facts were witnessed on the day it happened to be
   ingested.

**§5.1's nomination note is answered in part.** Direction now comes from
`published_at` and per-interval witness points rather than from arrival order.
What remains is whether succession *verdicts* carry proposed intervals — that
belongs to T2.

**§13.2's missing sixth verdict is still missing**, and T2 is where it lands.

**One correction to §13.1, which overstated the check.** With open-world
interval semantics — a source asserts what it asserts and says nothing about the
outside — nothing can prove two claims were **never** simultaneously true. The
honest guarantee is *no source asserts them true at a common moment*, firing
only when both facts carry intervals and those intervals do not intersect. It
flags and never blocks, and never fires on `unknown`. Narrower than originally
written, and the wording of the guarantee matters more than its strength.

**Where the six review findings stand.** Items 2 (empty set), 4 (three-valued
check) and 6 (a home for timepoints) are closed by T1. Item 3 is partly closed.
Item 5's vocabulary half is fixed and its retrieval half is T3. **Item 1 — which
mechanism owns a world-change — is T2**, and it decides how #54 is written, so
#54 no longer goes first.

**Built 2026-08-19 — the type and the comparison.** `epimemer/core/temporal.py`:
`ImpreciseInstant` (a discriminated union over precise / named / unknown /
unbounded endpoints), `ValidityInterval` (endpoints, timeline, witness point,
`stated` or `inferred` basis), and `compare_intervals` returning the four
values. Pure, and offering **no collapse over sets** — the union/intersection
trap §3 refuses is easy to add later and near-impossible to remove once callers
depend on it.

**Built the same day — storage and ingest.** `NodeEdge.validity` holds the list
and **only a `sourced_from` edge may carry one**: anywhere else is a period
attributed to nobody, which is the node-level set §2 rejected, reached by
accident. `RawDocument.published_at` records publication and never falls back to
ingest time. An ingesting agent supplies both, and the guidance is most of the
deliverable — it names the endpoint shapes, says that omitting validity is the
common and correct case, and states the prohibition the type cannot express: a
date the agent knows and the document does not give is neither *stated* nor
*inferred*, and must not be supplied.

Building it found one defect the field would otherwise have shipped with. Edge
migration collapses duplicates by `(src, dst, type)`, so merging two nodes from
the same document dropped a provenance edge and everything it asserted —
precisely where §2's "intervals survive merges for free" would have quietly
stopped being true. Both backends now hand the loser's intervals to the
survivor. That is not the union §3 forbids: that union is across *sources*,
where a sloppy one widens a careful one's period, while these came from the same
document about what is now the same claim.

**Nothing reads validity yet.** There is no `(source, interval)` retrieval
surface — the whole edge is visible through `query_graph`, and the purpose-built
read waits for §13.10 rather than being invented ahead of its naming decisions.

Two things this document leaves open and construction had to fix, recorded here
because both are now load-bearing. Intervals are **half-open**, `[start, end)`:
under closed intervals the exact instant of the 1991 renaming is one the city is
provably called both names, and every adjacent pair of periods overlaps by a
point, which would fire §11's check on ordinary succession. And the "point"
state is **two classes**, `precise` and `named` — the single-class version is
`at: datetime | None` with a label beside it, the same `None`-means-two-things
shape that disqualified `Timepoint`. A named endpoint compares identically to
`unknown` and differs only in carrying the source's words, which are the
evidence for any later resolution. The full construction note is in `ISSUES.md`
#53.

### 13.9 T2 decided (2026-08-12) — which mechanism owns a world-change

Review item 1. Full statement in `ISSUES.md` #53 → *T2 decided*; this section
existed only as dangling references until the second pass — the shape:

**Status and intervals are not alternatives; they answer different questions.**
Validity intervals are claims about the *world*: source-attributed, sparse,
open-world, never invented by the agent. `NodeStatus` is bookkeeping about the
*graph's current answer*: always present, closed by construction, and
legitimately the agent's to set. Both happen on a world-change, and the only
judgment remains the `because` call. The tempting alternative — derive "not
current" from "no interval contains now" and delete the status — dies on T1's
own honesty: with open-world semantics and `unknown` endpoints, the derivation
answers *unknown* for nearly every node. A model built to admit ignorance
propagates that ignorance into everything computed from it.

**The edge splits the way the status did.** A correction writes
`superseded_by` and is terminal (`CORRECTED`). A world-change writes the new
**`temporally_followed_by`** — order, not replacement — and is reversible
(`HISTORICAL`, restorable). The name rejects `succeeded_by`, a near-homograph
of `SUPERSEDED_BY` denoting its opposite. The edge records one observed
transition and never claims adjacency, so a later-discovered intermediate step
makes no existing edge wrong. It joins `HISTORY_EDGE_TYPES`: lineage, not
knowledge — excluded from migration and default traversal.

**Built 2026-08-19.** `lineage_edge_type_for(status)` (`core/types.py`), paired
with `superseded_status_for(because)` so the node and the edge cannot disagree
about which act happened — they had disagreed for a week, the status split
having shipped alone.

**And the reversibility it exists for, the same day.** `HISTORICAL` is
restorable and `CORRECTED` is not — `RESTORABLE_STATUSES` and
`NOMINATED_STATUSES` in `core/types.py` say so once and both ends read it.
`restore` reactivates a named node and writes the new source's `sourced_from`
edge in the *same transaction*, because a claim back to ACTIVE with no edge
saying what asserted it again is an assertion the graph makes and cannot
attribute. Reactivating without naming that source is refused. The retirement
stays in the lifecycle history, which is what makes a second cycle describable.

**Second pass (2026-08-12), binding:** recurrence makes **cycles legal** for
this edge type (Saint Petersburg's chain returns to its own node) and
**parallel same-direction edges legal** (Labour → Conservatives, observed in
1951, 1970, 1979 and 2010 — one edge per transition). Every walker must be
cycle-safe — §13.10's lineage collapse is the first — and nothing may dedup
this type by `(src, dst, type)` signature. The recurrence *detector* is
similarity nomination **including `HISTORICAL` candidates**, resolved by the
`recurs` verdict (§3) as an explicit reactivation: `restore` plus the new
source's edge. And a world-change migrates **per edge type**: `sourced_from`
(which carries validity once T1 is built) neither moves nor copies, because
putting it on a different claim fabricates attribution — while `has_metacontext`
and `tagged_with` *are* copied, because a frame and a topic are not claims about
the world. The blanket answer in either direction was wrong, and "migrate
nothing" was wrong dangerously: it moves a fiction-frame replacement into base
reality. ISSUES.md **#54** carries the table.

**Third pass, same date:** the two mechanisms above now name the code they
change — recall via a `statuses` parameter on `vector_search` (§5.1), and
reactivation via a `restore` widened to accept `HISTORICAL` while still refusing
`CORRECTED`, which is what its docstring always literally said.

### 13.10 T3 decided (2026-08-12) — retrieval and naming; the design is complete

Review item 5, in two halves: `HISTORICAL` had no reader at retrieval, and
`as_of` would be misread once valid time existed. Full statement in `ISSUES.md`
#53 → *T3 decided*. **With this, #53's design is complete and none of it is
built.**

> *True on the day it was written.* Construction began 2026-08-19: the lineage
> edge (§13.9), the interval type and its storage (§13.8) are built. **T3 itself
> is still entirely unbuilt** — nothing below this line has been started, and
> the missing retrieval surface is now the reason validity can be written but
> not read.

**The same trap, a third time.** A valid-time *filter* is the obvious surface
and is dishonest for the reason T1 and T2 already met: open-world data has to
put *unknown* somewhere, and both destinations lie. Since most nodes will never
carry intervals, exclusion is the default failure — a near-empty result reads
as *the graph does not know*, when the graph holds the claim and merely lacks a
date. **A filter converts missing metadata into a silent false negative.**
Stated once as a rule: *wherever open-world data meets a boolean question, the
answer has three values, and squeezing it into two is where the lie enters.*

So valid-time retrieval returns **buckets** — *provably valid at t* and
*unknown*, with *provably not valid* excluded — which is §13.8's
`before | after | overlap | unknown` applied at retrieval. Shape decided now;
code waits for validity.

**Reachability.** Two parameters. `include_historical` defaults **on**, because
knowledge that is not current is still knowledge. `include_corrected` defaults
**off**, because it is kept for the audit trail rather than for reading. An
earlier draft made `CORRECTED` unreachable from search entirely and was
rejected: it contradicts the report-and-let-the-caller-decide principle applied
everywhere else, and it makes *"what did we believe that turned out wrong?"*
answerable only by someone who already knows the node id. Retrieval is the third
consumer of the `CORRECTED`/`HISTORICAL` split, after archival and `restore`.

**Default-on requires lineage collapse.** Search ranks by similarity and a
historical claim is near-identical text to its replacement, so a claim with four
predecessors would fill half a top-10 with versions of one thing. When both
match, the successor takes the slot and the historical node attaches to it —
computable precisely because §13.9 created `temporally_followed_by`. Without
this, default-on is a regression; with it, it is strictly better than today.

**`as_of` → `graph_as_of`**, reserving `valid_as_of`. SQL:2011 prefixes the
phrase in both cases (`FOR SYSTEM_TIME AS OF`, `FOR APPLICATION_TIME AS OF`)
because "as of" alone does not say which clock. The decisive argument is which
name gets marked: **the unmarked name inherits the default reading**, and in a
knowledge graph "as of 1980" reads as *what was true then* — the wrong axis.
Leaving `as_of` bare would mark the safe name and leave the misreadable one
unmarked. The only piece of #53 carrying a migration cost, and cheapest now.

**A constraint recorded before anyone builds the reader: "current" is
timeline-relative.** §13.8 keys intervals by timeline and
`Timeline.reference_time` is already that timeline's *now*. So *is this claim
current?* must be asked against the relevant clock — a fictional claim is
current when its interval contains that timeline's reference time, not
wall-clock now. The first implementation will reach for `datetime.now()`;
unpicking that later is painful.

**All six review findings are now answered** — 2, 4 and 6 by T1 (§13.8), 1 by T2
(§13.9), 3 across both, 5 here. What remains in #53 is construction.
