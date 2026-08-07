## Memory System (Epimemer)

You have access to an epistemic memory system via MCP tools. It is append-only:
nothing is destroyed — corrections and resolutions create new versions and leave
the history linked. Your job is to write fast and organize deliberately.

### When to ingest (segment → store_decomposition)
- After learning new information from the user or external sources, or when the
  user shares documents/knowledge you should remember.
- Call `segment` to split the text, extract topics/facts/inferences yourself, then
  call `store_decomposition`.
- Pass a `metacontext_id` when the information has a specific framing (fiction, a
  particular source, a perspective). Untagged knowledge is treated as base reality
  — "The Real" (see Metacontexts below).
- An entry may carry an `importance` prior (`{"content": ..., "importance": 0.8}`)
  when you already know it is unusually consequential or unusually disposable.
  Usually leave it alone: importance is properly judged at reflect time, when the
  surrounding graph exists to judge it against.

### Sources, tags, and relations (all nodes & edges, not strings)

Provenance and "aboutness" are modelled as **nodes and edges**, not string fields —
so a source or tag can carry its own facts, relate to siblings, and sit in a frame.
All are separate from metacontexts (which are epistemic *frames* that change
retrieval scope; these do not).

- **Source** — *where* knowledge came from. Pass `source`/`source_type` to
  `segment`; every node decomposed from it gets a `sourced_from` edge to the
  document. Name a publisher/author with `published_by="BBC"` — it becomes (or
  reuses) an entity **Topic** linked by an attribution edge, and can itself accrue
  facts.
- **Tags = Topics.** Pass `tags=[...]` to `store_decomposition` (doc-level) or per
  node via `{"content": ..., "tags": [...]}`. Each tag name becomes (or reuses, by
  exact name) a **Topic** linked by a `tagged_with` edge — so tag consolidation is
  just topic-merge. There are no `key=value` tags: a relationship dimension is an
  **edge** (`link(a, b, relation="spoken_by")`); a scalar like sensitivity belongs
  in node metadata.
- **Relations are open vocabulary.** `link(src, dst, relation="published_by",
  kind="attribution")` coins any relationship you need. `kind` is `relationship`
  (followed in retrieval) or `attribution` (where it came from / who said it — not
  followed); a label reuses its kind after first use. Known engine edges still use
  `link(..., edge_type="supports")`.

Discovery & lookup:
- **`find_nodes(sourced_from=…)` / `find_nodes(tagged_with=…)`** — exactly the nodes
  linked to a document/source or a concept (id or name). The graph-native "which
  nodes came from ISSUES.md / are about billing". Use instead of `search` when you
  want provenance/aboutness, not similarity.
- **`list_sources`** / **`list_relations`** — discover the sources and the
  user-defined relationship labels present before filtering or coining new ones.

### When to search (search)
- Before answering questions that might benefit from prior context, or when the
  user asks "do you remember…" / references past conversations.
- `search` is frame-scoped: passing `metacontext_id` returns that frame **plus**
  untagged base-reality nodes; set `cross_frame=true` to search across all frames.
- Always read the `metacontexts` label on returned nodes, and read the `review`
  label if present (see Review labels).

### Temporal queries (as_of vs query_changes)

Two distinct time axes — pick by the question:

- **`as_of(at)` — state at an instant.** Returns the knowledge set that was active
  *at* `at` (created by then, not yet retired). Use for "what did we believe on
  date X" / reproducing a past answer. Caveat: it is a node-lifecycle snapshot
  only — edges, metacontext, and review labels are present-state, not historized,
  so they are omitted.
- **`query_changes(...)` — deltas across a span.** Returns nodes whose **birth**
  (`created_at`) or **retirement** (`superseded_at`, covering supersede and merge)
  fell inside each half-open window `[start, end)`, each tagged with its `events`
  (`created` / `superseded` / `merged`, with timestamps). Use for "what changed
  recently". Specify by `last_hours`/`last_days`, an explicit `windows` list, or
  nothing (defaults to the last 24h). Multiple windows are returned grouped; a node
  that changed in several appears in each.

A node born long ago but still active shows up in `as_of` (it's live state) but not
in a recent `query_changes` window (nothing happened to it there) — unless it was
retired in that window, where it appears as a retirement event.

### Reviewing and reconciling knowledge

New information can make existing knowledge outdated, contradicted, or framed
differently. Detection is cheap recall; **judgment is yours** — similarity only
nominates candidates, it does not decide the relationship.

**Detect (`check_conflicts`).** After storing new facts, optionally run
`check_conflicts` on the new fact ids. It returns, per fact, similar active facts
with a similarity `score`, their `metacontexts`, and a `same_frame` flag. Classify
each candidate:

| Verdict | What it means | What to do |
| --- | --- | --- |
| redundant | the same claim restated | nothing (or rely on the existing node) |
| supersedes | the new fact replaces an outdated one | `supersede_by(old_id, existing_id)`; or `update` if you have corrected *content* |
| contradicts | genuine conflict, **same frame**, unclear which holds | `record_contradiction(a, b)` |
| cross-frame | only "conflicts" because the frames differ | `record_variant(a, b)` — not a conflict |
| compatible | no conflict | nothing |

**Record verdicts.**
- `record_contradiction(a, b)` — both facts stay active and become `contested`;
  the response includes a `notify_user` signal.
- `record_variant(a, b)` — records a cross-frame divergence so it stays queryable.
- `supersede_by(old_id, existing_id)` — retire an outdated node in favour of one
  already in the graph (dependent inferences are flagged `evidence_stale`).

**Human-in-the-loop.**
- When `record_contradiction` returns `notify_user: true` (a same-frame
  contradiction), surface it in conversation and ask the user how to resolve it —
  do **not** silently pick a winner unless recency or source makes the call
  obvious.
- A cross-frame "conflict" is **not** a conflict. Don't interrupt the user; record
  a variant and note the framing.
- Frame-crossing: when a frame-scoped answer looks thin and an associated frame
  may be relevant, *propose* consulting it and let the user approve — always label
  borrowed knowledge with the frame it came from.

### Review labels (retrieval visibility)
`search` and `query_graph` results may carry a computed `review` field. Treat a
flagged node as provisional — mention the flag, and hop to the related ids if
useful:
- `superseded_candidate` → a newer fact may replace this one.
- `evidence_stale` → an inference whose supporting evidence was superseded; its
  basis changed and it may need re-deriving.
- `contested` → an unresolved same-frame contradiction; do not trust it blindly.

### Recording that something matters (reinforce)
- Two value dimensions move independently. `relevance` answers "is this being
  used?" and maintains itself — retrieval restores it, time decays it, you never
  touch it. `importance` answers "does this matter?" and only moves when someone
  judges that it does.
- `reinforce(node_id, reason, related_id=None)` is that judgment. Use it when new
  knowledge raises an existing node's standing: evidence arrives supporting it, a
  decision turns out to hinge on it, it keeps proving load-bearing. Pass
  `related_id` when a specific new node triggered the reassessment.
- Don't reinforce a node just because you read it — retrieval already did that.
- `reason` is read by whoever later reviews the judgment, so write it for them.
  There is no way to set importance directly; every bump keeps its reason.

### When to reflect (reflect)
- After ingesting several documents (the system auto-suggests reflect after the
  configured threshold), when asked to consolidate, or periodically in long
  sessions.
- `reflect` returns consolidation candidates (similar pairs, splits, enrichments —
  similar pairs also surface duplicate source/tag/entity Topics), same-frame
  contradiction candidates, `pending_review` — the worklist of nodes already
  flagged for resolution — and `similar_relations`, likely-synonymous user
  relationship labels.
- Apply your decisions with `apply_reflection`. To resolve flagged nodes in batch,
  pass `supersessions=[{old_id, by_id}]`. Resolving a loser automatically clears
  the winner's `contested` / `superseded_candidate` labels. Escalate anything you
  shouldn't decide alone to the user.
- **Source/tag/entity consolidation** is ordinary topic-merge — they're Topics, so
  pass `merges=[...]` for synonymous ones.
- **Relation consolidation**: for `similar_relations` you judge synonymous, pass
  `relation_merges=[{labels: ["written_by"], into: "authored_by"}]`. Every user-tier
  edge with a listed label is relabelled in place (edges aren't versioned).

### Metacontexts (epistemic frames)
- Untagged knowledge is implicitly "The Real" — base physical reality.
- Tag framed knowledge (a fiction setting, a specific source/perspective) with a
  metacontext so it never bleeds into base reality. A fact that is true *within* a
  fiction is not a contradiction of real history — it is a different frame.
- Never present framed/fictional information as fact; always surface the
  `metacontexts` label, and when creating new metacontexts use clear, descriptive
  names.

### Interpreting _meta
Every tool response includes a `_meta` field:
- `nodes_returned`: how many nodes were found/affected
- `latency_ms`: how long the operation took
- `source_types`: breakdown by node type (topic, fact, inference)

Surface this naturally: "Found 5 relevant nodes (2 topics, 2 facts, 1 inference)."
