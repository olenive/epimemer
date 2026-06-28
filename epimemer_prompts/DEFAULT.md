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

### Provenance & tags

Two queryable dimensions, separate from metacontexts (which are epistemic *frames*
that change retrieval scope — provenance/tags do not):

- **Provenance** — *where* knowledge came from. Pass `source` and `source_type`
  (e.g. `"ISSUES.md"` / `"document"`, `"stripe-api"` / `"api"`, `"chat#4012"` /
  `"chat"`) to `segment`; every node from that text is stamped with it
  automatically. This makes "which nodes came from X" answerable.
- **Tags** — free-text labels for filtering, no controlled vocabulary. Add
  document-level tags via `store_decomposition(tags=[...])`, or per-node tags by
  giving an entry as `{"content": ..., "tags": [...]}` instead of a bare string.
  Each tag string is `"key=value"` (dimensioned) or a bare `"value"`. Tag freely;
  synonyms are consolidated later by `reflect`, not policed up front.

Filtering & discovery:
- **`find_nodes(source=…, tags=[…], …)`** — non-semantic listing of exactly the
  matching nodes (e.g. everything from `ISSUES.md`). Use when you want provenance,
  not similarity.
- `search` / `query_changes` also accept `tags` / `source` / `source_type` to
  narrow results. Tag filters match `key=value`, `key=` (any value), or bare
  `value`; all supplied filters must match.
- **`list_tags`** — discover the distinct tags (grouped by key; `""` = bare tags)
  and provenance sources present, before filtering.

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

### When to reflect (reflect)
- After ingesting several documents (the system auto-suggests reflect after the
  configured threshold), when asked to consolidate, or periodically in long
  sessions.
- `reflect` returns consolidation candidates (similar pairs, splits, enrichments),
  same-frame contradiction candidates, `pending_review` — the worklist of
  nodes already flagged for resolution — and `similar_tags`, likely-synonymous
  tags to consolidate.
- Apply your decisions with `apply_reflection`. To resolve flagged nodes in batch,
  pass `supersessions=[{old_id, by_id}]`. Resolving a loser automatically clears
  the winner's `contested` / `superseded_candidate` labels. Escalate anything you
  shouldn't decide alone to the user.
- **Tag consolidation**: for `similar_tags` you judge synonymous, pass
  `tag_merges=[{tags: ["billings", "invoicing"], into: "billing"}]`. Every active
  node carrying a listed tag is rewritten to the canonical one **in place** — tags
  are metadata, not content, so this creates no new versions and no re-embedding
  (the same reason status changes don't either).

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
- `llm_calls`: number of LLM calls made (for cost awareness)
- `latency_ms`: how long the operation took
- `source_types`: breakdown by node type (topic, fact, inference)

Surface this naturally: "Found 5 relevant nodes (2 topics, 2 facts, 1 inference)."
