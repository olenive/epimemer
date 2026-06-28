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
(`superseded_by`, `merged_into`). The guiding rhythm is **"write fast, organize
slow"**: ingestion is mechanical and cheap; organization (consolidation,
review) is deliberate and, where it matters, *agentic*.

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
subsystems** — they are outcomes of one loop:

```
new/changed knowledge
        │
        ▼
  candidate generation   ← cheap: embedding similarity (recall)
        │
        ▼
  agentic judgment       ← precise: the agent classifies the relationship
        │
        ├─ redundant        → dedup / ignore
        ├─ supersedes       → mark older outdated (temporal)
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
| **supersedes** | new replaces old (newer correct version) | temporal supersession (label old `superseded_candidate`) |
| **contradicts** | conflicting claims, same frame, unclear which holds | record `CONTRADICTION`; resolve (agent/human) |
| **cross-frame** | "conflict" only because frames differ (fiction vs real) | not a conflict; both coexist; optional `variant_of` |
| **compatible** | no conflict | nothing |

A separate, non-similarity trigger handles **evidential staleness**: when a fact
is superseded, inferences derived from it become suspect.

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

- Immutable history: `status` ∈ {ACTIVE, SUPERSEDED, MERGED}; `superseded_by` /
  `merged_into` lineage; `query_nodes` filters to ACTIVE.
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
