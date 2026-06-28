# Epimemer — Known Issues

Findings from an empirical walkthrough of `reflect` → dedup/enrich on a live
graph (`petri-app`), cross-checked against the source. Three of these compound
into a single user-visible failure: **`memory.update` on a topic degrades
retrieval** — the stale version keeps ranking and the corrected version becomes
unfindable.

Discovered 2026-06-25.

**Status update (2026-06-25, commit `22fc874`).** Issues 1–3 — and the compound
"`update` degrades retrieval" failure — are **resolved**. Issues 4 and 5 are kept
as documented, intended behaviour. Per-issue resolution notes are inline below;
the original analysis is retained for context. Regression coverage lives in
`tests/mcp/test_issues_reproduction.py` (plus storage/versioning parity tests).

**Follow-up hardening (2026-06-26).** Since the original fixes: `update` now
carries the node's value signal onto the replacement (no longer resets it);
`supersede`/`merge` are **atomic** (backend-native single-transaction);
`merge_nodes` is exposed via `apply_reflection merges=[...]` behind a high
similarity bar. Remaining/known items are tracked in **Open items** at the
bottom of this file.

---

## Issue 1 — `update` creates a node with no embedding (unindexed, unsearchable) ✅ RESOLVED

**Severity: high.** The whole point of `update` is to correct a node's content,
but the corrected node never enters the vector index, so `search` cannot return it.

**Symptom (observed).** After `update`-ing topic `b4e75a15` → `6232c74b`, a
`search` query built from the *new* node's near-verbatim text never returns it —
not in the top 25 — while the *superseded* original ranks #1. The correction is
invisible to retrieval. Running `reflect` afterwards does **not** fix it: decay
fired (relevance `0.475 → 0.45125`) but the new node remained absent and the
superseded one remained #1. So this is a permanent state, not indexing lag —
`reflect` only *reads* topic embeddings, it never writes them.

**Root cause.** `mcp/tools.py::update` (line ~349) builds the replacement node
and calls `versioning.supersede_node`, which only does
`storage.store_node(new_node)` (`pipelines/graph_construction/versioning.py`,
~line 45). No embedding is generated or stored for the new node.

Embeddings live in a separate store keyed by `EmbeddingRecord` (see
`storage/memory.py::store_embedding`, line ~201), and `vector_search` iterates
`embeddings.values()` (line ~217). A node with no `EmbeddingRecord` is therefore
unreachable by vector search, regardless of its content. The normal ingest path
(`segment` → `store_decomposition`) embeds nodes; `update`/`supersede_node` skips
that step.

**Suggested fix.** In `update`, after `supersede_node`, embed `new_node.content`
and `store_embedding` an `EmbeddingRecord(item_id=new_node.id, …)` — the exact
three lines `apply_reflection` already runs at `mcp/tools.py:90–92`. This requires
threading an `EmbeddingProvider` into `update` (it currently takes only
`storage`). Alternatively, fold the embed step into `supersede_node` itself so
every supersession path is correct by construction.

**Resolution.** Took the second option: `supersede_node` (and `merge_nodes`) now
embed the replacement and `store_embedding` it, so every supersession/merge path
is correct by construction. `EmbeddingProvider` is threaded through `update` and
`apply_reflection` (whose now-redundant separate embed was removed). Verified by
`test_update_embeds_the_replacement_node` and
`test_corrected_node_is_retrievable_by_search`.

---

## Issue 2 — `vector_search` / `search` does not filter superseded nodes ✅ RESOLVED

**Severity: high.** Stale, superseded content is returned by retrieval and can
outrank — or entirely displace — its active replacement.

**Symptom (observed).** `search` returned the superseded node `b4e75a15`
(`status: "superseded"`) as the #1 result across three separate queries.

**Root cause.** `storage/memory.py::vector_search` (line ~217) ranks over *all*
embeddings with no status check. Contrast `query_nodes` in the same file
(lines ~140–153), which *does* filter `node.status != status` (default
`ACTIVE`). The vector path simply omits the equivalent guard.

**Suggested fix.** Filter superseded nodes out of `vector_search` results (skip
`emb.item_id` whose node `status != ACTIVE`), or expose an `include_superseded`
flag defaulting to `False`. Note this is a real fix only in combination with
Issue 1: filtering out the superseded node without indexing the replacement would
leave the topic returning *nothing*.

**Resolution.** `vector_search` now restricts results to **active** nodes in both
backends — `storage/memory.py` skips any embedding whose node is missing or
`status != ACTIVE`; `storage/surrealdb_adapter.py` scopes both the typed and
untyped paths to active nodes. No flag was added (kept the signature stable).
Verified by `test_vector_search_excludes_superseded_nodes` /
`test_search_does_not_return_superseded_node` (and storage-level parity tests on
both backends).

---

## Issue 3 — `update` / `supersede_node` orphans the node's edges ✅ RESOLVED

**Severity: medium.** The replacement node inherits none of the original's
relationships; supporting facts continue to point at the dead version.

**Symptom (observed).** Old topic `b4e75a15` retained all 8 of its edges
(7 `supports` from facts, 1 `abstracts` from an inference, plus the `about`
provenance edge). New node `6232c74b` had zero edges until the edges were
recreated by hand via `link`. Same on the `43bb8d62` → `c95b91ee` rename.

**Root cause.** `supersede_node` stores the new node and a single `superseded_by`
edge (old → new). It never re-points (or copies) the old node's inbound/outbound
edges onto the replacement.

**Suggested fix.** On supersession, migrate the old node's edges to the new node
(re-point `dst`/`src` as appropriate), excluding version-lineage edges. Decide
explicitly whether to rewrite in place or copy-and-leave on the superseded node.

**Resolution.** Chose **rewrite in place**: `supersede_node` re-points every
non-lineage edge from the old node onto the replacement via a shared
`_migrate_edges` helper (delete + re-store, preserving edge identity), leaving
only `superseded_by`/`merged_into` on the dead node. This required a new
`delete_edge` primitive, added to the storage protocol and both backends (and the
instrumented wrapper). The same helper backs `merge_nodes`, where it also
collapses self-loops and duplicate edges from combining multiple sources.
Verified by `test_supporting_edge_follows_to_replacement` and
`test_merge_migrates_and_dedupes_edges`.

---

## Issue 4 (minor / unconfirmed in code) — `superseded_by` lineage is not traversable

**Observed.** `query_graph` on a superseded node returned its `about` + `supports`
edges but **not** the `superseded_by` edge created by `update`, and did not
surface the replacement node. So you cannot hop dead → replacement via traversal;
currency is recoverable only from the `status` field, not from graph structure.

**Note.** Possibly intentional (lineage treated as metadata, not knowledge). Flag
only because it makes "given a stale node, find its current version" impossible
through the graph API.

---

## Issue 5 (minor) — `link` cannot target source-document nodes

**Observed.** Recreating an `about` edge from a source document
(`19d09414-…`) via `link` failed with `Source node not found`. `link` resolves
only epistemic nodes (topic/fact/inference); `about` provenance edges are
ingest-only and cannot be hand-rebuilt. Consequence: after Issue 3's manual edge
repair, provenance to the original document cannot be restored on the new node.

**Note (post Issue 3 fix).** The motivating scenario no longer arises: supersession
now migrates the `about` provenance edge onto the replacement automatically, so no
manual rebuild is needed. The `link` restriction itself is unchanged and kept.

---

## Combined effect & guidance

**Resolved (2026-06-25, commit `22fc874`).** All three fixes landed together, so
the compound failure no longer occurs: after `update`, the correction is embedded
and searchable (1), the superseded original no longer ranks (2), and the
replacement inherits its predecessor's edges (3). `update` is now safe for topics
with children and for content that must stay searchable, and `apply_reflection
enrichments` — which routes through the same `supersede_node` — is correct on all
three counts as well. End-to-end check:
`test_update_makes_correction_findable_and_hides_stale`.

<details>
<summary>Original guidance (pre-fix, retained for context)</summary>

Issues 1–3 together mean **`update` should not be used to revise a topic that has
children or that needs to remain searchable.** `update` is, at best, safe only
for demoting a standalone leaf — and even then the old version stays searchable
(Issue 2).

For description revisions, prefer **`apply_reflection enrichments`**, which
**does re-embed** the replacement (verified: `mcp/tools.py:90–92`) — so it avoids
Issue 1. But note it routes through the same `supersede_node`, so it still
**inherits Issue 3 (edge orphaning)** and Issue 2 (old version remains
searchable). In other words, enrichment fixes findability of the new node but not
the orphaned-edges or stale-still-ranks problems. A complete fix needs all three:
embed on supersession (1), filter superseded from `vector_search` (2), and
migrate edges on supersession (3).

</details>

---

## Open items (living list, 2026-06-26)

Tracked here so this file stays the single place to re-visit. Not all are bugs.

1. ~~**Partial atomicity.**~~ **RESOLVED (2026-06-27).** A `write_batch_tx`
   primitive (pure-insert, all-or-nothing) now backs `store_decomposition`
   (one atomic write per document) and `apply_reflection parents`/`splits`
   (plan cycle-checks, then atomic batch). With `supersede`/`merge` already
   atomic, all multi-write paths are now all-or-nothing. (`restore` now also
   reconstructs the whole archive then persists it via one `write_batch_tx`;
   `archive` is a read-only export.)
2. ~~**SurrealDB verified on embedded `mem://` only.**~~ **RESOLVED
   (2026-06-28).** `tests/storage/test_surrealdb_integration.py` exercises a real
   networked `ws://` server (opt-in via `EPIMEMER_SURREAL_WS_URL`, skipped
   otherwise): connection/auth plus transaction atomicity under genuine
   concurrency (concurrent batches all commit, a colliding batch rolls back fully,
   concurrent supersedes apply). Verified green against Dockerised SurrealDB. The
   adapter remains documented single-connection for the *shared viz* path.
3. **Merge is Topic-only (wired path).** `merge_nodes` is type-agnostic, but
   `apply_reflection merges` only accepts Topics. Whether to extend to
   Facts/Inferences is under discussion (Inferences are meant to let competing
   derivations coexist, so merging them should be rare).
4. **Issue 4 / Issue 5 kept by design** (see above): history edges hidden from
   default traversal; `link` resolves epistemic nodes only.
5. **No retroactive repair of existing graphs.** Fixes apply to new operations;
   pre-existing graphs (e.g. `petri-app`) keep any old orphaned/stale state until
   rebuilt or cleaned. (Accepted for now — graph will be rebuilt.)
