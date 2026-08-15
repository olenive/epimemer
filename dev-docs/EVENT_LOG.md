# Event log: a readable record of what the agent did

Design for a log panel in the dashboard — a list of what the agent changed,
filterable, with entries that highlight their nodes in the graph on click.

Decided 2026-08-17. Nothing here is built yet.

The motivating case, in the user's words: *"if the log entry is node 123 is
being superseded by node 124, it would be handy if I could click on the log
and have the nodes highlighted in the graph."*

That exact sentence **cannot be rendered from anything the system currently
emits**. Working out why is most of this design.

---

## 1. Why it is worth building

Supersession is a destructive-looking act. A belief the graph held is now
historical, the agent decided it, and there is no visible trace. Ten tool calls
in response to "update the graph with recent information" is normal, and all of
it is invisible.

An audit trail of status changes is the difference between a memory system and a
black box. Of the features currently queued it has the clearest epistemic
justification — it is the one that makes the system accountable to its user
rather than merely useful to its agent.

---

## 2. The defect to fix first, which is not the log

`NodeStatusChanged` (`visualization/events.py:215-221`) carries `node_id`,
`old_status`, `new_status`. It does **not** carry the superseding node.

The relation lives in a separate `SUPERSEDED_BY` edge, published as its own
`EdgeStored` a moment later (`visualization/instrumented_storage.py:234-243`).
Rendering "123 superseded by 124" therefore means joining two events by their
adjacency in the stream, which breaks the moment anything interleaves.

**The same gap exists in the durable path.** `events_in_window`
(`mcp/tools.py:764-782`) emits `created` plus the node's terminal status —
`corrected` / `historical` / `merged` — from node timestamps, also with no
counterpart. *(Corrected 2026-08-17: this originally said "`created` /
`superseded` / `merged`" — stale vocabulary; the code already mirrors the #53
status split, with a comment saying retiring-as-historical and
retiring-as-corrected are different things to report.)* So "superseded by whom" exists nowhere in
this system except the edge itself — not in the live event, not in
`query_changes`, not in any tool response.

Fix: carry the counterpart id on both surfaces. Both already hold it —
`supersede_node_tx` is handed `old_node`, `new_node` and `lineage_edge`
together, and `query_changes` can join the edge it already has access to.

**This lands first, on its own, regardless of whether the log is built.** It is
a defect in the event contract, not a missing feature, and it is filed as one:
**`ISSUES.md` #57** (2026-08-17 — the review found it had been called for here
but never filed).

---

## 3. Granularity: the transaction, not the write

Measured by instrumenting a real ingest (2026-08-17: `InMemoryStorage` behind
`instrument_storage`, a counting subscriber on the bus, mock embeddings — every
published event tallied):

| Act | Events emitted (measured) |
|---|---|
| One node stored, fresh ingest | 7.0 (`NodeStored` + `EmbeddingStored` + ~3.6 `EdgeStored` + pipeline share) |
| One `store_decomposition` (25 nodes) | **176** — 141 storage (25+25+90 `EdgeStored`+1 `TimelineStored`) + 35 pipeline |
| One `search` | 40 |
| One `update` (a supersede) | **4** — which directly confirms §3.1's "four or more" |
| A ten-call task | hundreds to ~2,000 by mix; only ingest-heavy tasks reach the top |
| Building the current graph | 1,700+ storage events (200 nodes + 200 embeddings + 1,299 edges) |

> **Corrected (2026-08-17, review).** This section originally claimed ~8.5
> events per node, ~200 per `store_decomposition` and 2,000–4,000 per
> ten-call task, extrapolated from `graph_stats` (6.5 edges/node). That edge
> density is the *mature* graph's — reflection adds edges after the fact; a
> fresh ingest creates ~3.6 per node — so the estimates ran ~25–50% high.
> The conclusion is untouched: even measured, one ingest emits two orders of
> magnitude more events than "on the order of ten readable entries". But the
> **ring bound and any batching thresholds must trace to the measured
> numbers**, not the withdrawn ones.

The estimate that prompted this feature was "on the order of ten". That is right
about *tool calls* and two orders of magnitude below the *event* count,
because events are emitted at storage-write granularity.

**A raw event log is a firehose, and shipping one would repeat a mistake this
codebase has already corrected.** `VISUALISATION.md` B.0 replaced raw
per-event pipeline rendering with aggregate tiles for exactly this reason. A raw
log re-introduces what B.0 removed.

### 3.1 The readable unit already exists in the code

`supersede_node_tx` is one act that publishes four or more events. So do
`merge_nodes_tx` and `write_batch_tx`. The entry a person wants to read —
*"superseded 123 → 124, +3 evidence edges"* — corresponds to the
**transaction**, and no single event represents it.

Reconstructing that by grouping in the frontend needs a correlation id that does
not exist, and would guess wrong under concurrency. Instead, publish a coarse
event at the transaction boundary:

```python
class GraphActionRecorded(Event):
    """One human-meaningful act, at the transaction boundary that performed it."""
    category: Literal[EventCategory.GRAPH] = EventCategory.GRAPH  # see §10
    event_type: Literal["graph_action_recorded"] = "graph_action_recorded"
    action_id: str              # monotonic, assigned at the source — see §4.1
    verb: ActionVerb            # stored | corrected | world_changed | merged | archived | restored | …
    subjects: list[str]         # node ids, primary first
    counts: dict[str, int]      # {"edges": 3, "nodes": 1} — what it swept up
    summary: str                # pre-rendered one-line text, for display and substring filter
```

`instrumented_storage.py` already sits exactly at the transaction boundary and
already holds every id involved. Fine-grained events keep flowing untouched —
the graph panel needs them, and nothing about it changes. The log consumes only
the coarse stream.

**`summary` is pre-rendered on the emitting side deliberately.** A log line that
the frontend assembles from parts is a second place where the vocabulary of the
system gets decided, and it would drift from the tool responses that use the
same words.

> **Revised (2026-08-17, review): there is no `superseded` verb.** #53 decided
> supersession is two opposite acts — a correction (`it_was_wrong` →
> CORRECTED, `superseded_by`, terminal) and a world-change
> (`the_world_changed` → HISTORICAL, `temporally_followed_by`, restorable) —
> and a log line reading "superseded 123 → 124" flattens exactly the
> distinction the graph records. By this section's own drift rule, the verbs
> are **`corrected`** and **`world_changed`**, matching the terminal statuses
> and what `events_in_window` already emits, so the live log and the durable
> history speak one vocabulary. Summaries render accordingly
> ("corrected 123 → 124" vs "world-change: 123 → 124"). Recurrence needs no
> new verb: #53 T2's `recurs` verdict resolves as *restore + new source
> edge*, which is `restored` with the edge in `counts` — recorded here so
> nobody mints a `recurs` verb later and splits the vocabulary again.

---

## 4. The record ring

### 4.1 Sequencing: `seq` cannot carry this

`seq` is assigned per browser connection by the hub at send time
(`hub.py:99-101`), starts at 0 for each socket (`hub.py:252`), and resets on
reconnect (`frontend/src/events.ts:94`). Two browsers see different numbers for
the same event.

It is a **drop detector, not a position in a stream**. A log cannot use it to
dedup across a reconnect, to request "everything after N", or to guarantee
stable order. Hence `action_id`, assigned by the session process that emits the
action.

### 4.2 Placement: hub-side, at the existing choke point

Every event already passes through one line — `hub.py:197-200`, where the hub
stamps `session_id` before fan-out. `sessions[sid]` is already a per-session
dict. The ring hangs off it, and the append is one statement at a place that
already exists.

A browser then receives backfill on subscribe, with no RPC round-trip and
without waking a session process. The ring selects on
`event_type == "graph_action_recorded"` — not on category, which nothing in
the pipeline consumes (§10).

**Stated honestly, because it is easy to oversell:** this survives browser
reloads — the common case, and the one that matters, since you open the
dashboard *after* noticing the agent did something. It does **not** survive an
MCP restart: `session_id` is a fresh uuid4 per process (`protocol.py:27`), so a
restarted server registers as a different session and its ring starts empty.
Neither placement fixes that; only §6's durable path does.

### 4.3 Retrieval records go in the session process instead

The retrieval-provenance feature (`RETRIEVAL_PROVENANCE.md`) wants a
structurally identical ring, and the reuse argument for one implementation
stands. But its records carry **response
payloads** — the largest and most sensitive thing the system holds — and those
should stay in the process that produced them, fetched by RPC on demand
(`protocol.py:75`).

So: one generic bounded-ring module, two instances, placed by payload size and
sensitivity rather than by symmetry. Same sequencing discipline in both.

> **Revised (2026-08-17, review): both rings are hub-side; the boundary moves
> from "which process" to "which bind".** The split above silently gave up
> the feature's own normal case: the hub *keeps* disconnected sessions
> (`_mark_disconnected` marks, never pops — hub.py:222), so this log ring
> survives an MCP exit — but RPC to a disconnected session raises
> (hub.py:139-140), so retrieval records died with the session exactly when
> "open the dashboard after noticing" needs them. Decision: retrieval records
> mirror into a hub-side ring off `sessions[sid]` too (session-keyed by
> placement; the hub already stamps `session_id` at ingest, hub.py:197-200),
> with the §3.2 per-record caps. The sensitivity argument is kept where it is
> real: **when the hub is bound to a non-loopback host, sessions mirror
> structural metadata only** — no query text, no payloads — and the RPC path
> remains for guarded-mode fetch while the session lives. Details, caps and
> the stated multi-agent assumptions: `RETRIEVAL_PROVENANCE.md` §3.2.

---

## 5. Filtering

Structured filters, not search. Three of the four things you would look for are
filters over fields:

- **verb** — chips, multi-select
- **node id** — text box, exact
- **time range** — reuses the timeline panel's range inputs
- **free text** — plain substring over `summary`

**Not BM25, and not because it is expensive.** Log vocabulary is a dozen verbs
repeated thousands of times, and SurrealDB's BM25 clamps IDF to zero above 50%
document frequency (measured — see `LEXICAL_SEARCH.md` §2.5). Every verb term
would sit far above that threshold and every match would tie at `0.0`: a ranking
function returning a constant. The property that makes BM25 right for the graph
corpus makes it wrong for this one.

Node ids *would* score well, being maximally rare — but for an id you want exact
match, which is a lookup, not ranked retrieval.

**This is a frontend feature.** The ring is a few hundred entries in memory;
filtering is `Array.prototype.filter`. No protocol method, no storage schema, no
cross-backend parity problem. It must not be routed through `text_search`.

### 5.1 The genuinely textual query is a graph search, not a log search

*"What happened to the fact about deployment rollbacks?"* is unanswerable from
the log, whose entries hold ids rather than content. It is a **graph** lexical
search whose hit ids then filter the log — free once `LEXICAL_SEARCH.md` lands,
and needing nothing on the log side.

---

## 6. Live log vs history — two features, one UI

`query_changes` (`storage/protocol.py:234`, tool at `mcp/tools.py:817`) already
answers "what was born or retired in `[start, end)`" for all time, persisted, on
both backends, with per-node lifecycle events. **That is a durable audit trail,
it is already implemented, and the dashboard does not surface it at all.**

| | Source | Lifetime | Filtering |
|---|---|---|---|
| **Live log** | hub ring (§4.2) | session | client-side (§5) |
| **History** | `query_changes` | forever, already persisted | time window + node type |

Present them as one timeline in the UI if that reads better. **Design them as
two.** Collapsing them is how a bounded ring quietly becomes a database with a
retention policy nobody chose.

Caveat, so this is not oversold: `query_changes` is node-only. No edge changes,
no pipeline runs, no retrievals. It is a node-lifecycle history, not a full
audit trail.

> **Revised (2026-08-17, review): what "forever" means, and the episode fix.**
>
> First, sizing, so nobody reads "durable, forever" as a growing log:
> **history here is a reading of node fields, not a second store.** There is
> nothing to ring-buffer and nothing that grows on its own — the trail's size
> *is* the graph's size, bounded by the existing archival system, and its
> durability is the backend's (on `InMemoryStorage` it lives exactly as long
> as the graph does).
>
> Second, a correctness collision with #53 T2, found by asking what happens
> when the `recurs` verdict restores a `HISTORICAL` node. The derivation
> reads `(superseded_at, status)`, and that pair cannot represent *retired,
> then came back*: clear `superseded_at` on restore and the retirement
> vanishes from every window; keep it and the event's kind — which is just
> `node.status.value` — reads `"active"` at the retirement timestamp.
> And since T2 legalised cycles, a scalar `restored_at` only defers the same
> overwrite to the second retirement.
>
> **Resolution: an append-only lifecycle episode list on the node** — each
> episode `{retired_at, because, restored_at | None}` — with
> `(status, superseded_at)` kept as the current-state snapshot for fast
> paths. `events_in_window` derives from the episodes, so every retirement
> and every return stays reportable, with the right kinds, through any number
> of cycles. Episodes are append-only: nothing is ever cleared or
> overwritten. The #57 counterpart id lives on the episode, which lands both
> changes in one shape. It grows only on actual transitions — most nodes
> never have more than one episode.
>
> For completeness, since the examples that motivated this were "when was
> Labour in power" and "the Christmas holiday period": neither is answered
> here. The first is **valid time** — T1's per-source interval lists, already
> open-ended in count. The second is a **recurrence rule** — the
> `CyclicalTimeline` case, no lifecycle at all; see the T2 constraint in
> `ISSUES.md` #53. This section is transaction time only: what *the graph*
> did, and when.

---

## 7. Click to highlight

`highlightNodes` (`frontend/src/graph-panel.ts:391`) exists and is already used
by the timeline bridge. Two silent-failure modes have to be closed before it is
driven from the log:

1. **Unknown id** — `cy.getElementById(id)` returns an empty collection and
   `.addClass` is a no-op. Click, nothing happens, no explanation.
2. **Filtered-out node** — the type filter sets `display: none`
   (`graph-panel.ts:431-437`), so the class lands on something invisible. Same
   symptom, different cause.

Both need the same treatment: report when the id is not in the current graph,
and clear a conflicting type filter rather than highlighting into nothing.

Selection is bidirectional — click a node, filter the log to it.

Scoping follows the pipeline strip's existing rule (`main.ts:339`): a session
switch clears, and entries from another graph never highlight into the viewed
one.

---

## 8. Tests, written first

Per the `ISSUES.md` workflow — each named test failing for its stated reason
before the code that satisfies it.

- `test_node_status_changed_names_the_superseding_node` — §2, on the live event.
  Must be shown failing first; it is the whole reason the example does not work.
- `test_query_changes_names_the_superseding_node` — §2, durable path.
- `test_supersede_publishes_one_action_for_four_events` — §3.1: the coarse event
  is emitted once per transaction, not once per write.
- `test_action_ids_are_monotonic_across_browser_reconnects` — §4.1. A
  `seq`-based implementation passes every other test here and fails this one.
- `test_ring_evicts_oldest_and_backfills_on_subscribe` — §4.2, bounded and
  replayable.
- `test_log_filters_by_verb_and_substring` — §5, pure, no DOM.
- `test_query_changes_reports_every_episode_of_a_recurring_node` — §6 revised:
  retire a node as HISTORICAL, restore it, retire it again; windows spanning
  each transition report all three events with the right kinds. A scalar
  `(superseded_at, status)` implementation — or a scalar `restored_at` —
  loses one of them and fails.
- `test_highlight_reports_an_id_absent_from_the_graph` — §7.1.
- `test_highlight_clears_a_conflicting_type_filter` — §7.2.

Storage is untouched, so the unit suite covers this; no integration run needed
beyond what §2's durable-path change requires.

---

## 9. Commit sequence

1. Counterpart id on `NodeStatusChanged` and `events_in_window` (§2).
   Independent of everything else here, and worth landing alone.
2. `GraphActionRecorded` + emission at the transaction boundaries (§3.1).
3. The bounded ring module, pure, with tests.
4. Hub-side instance + backfill on subscribe (§4.2).
5. Log panel: rendering and filters (§5).
6. Click-to-highlight, both silent-failure fixes, bidirectional selection (§7).

Steps 1–3 change nothing a user sees. Step 4 is where the panel becomes
possible.

---

## 10. Open

- **Event category — resolved (2026-08-17, review), and the premise was
  wrong.** Nothing filters by category anywhere: hub subscription filtering
  keys off **session and graphs** (`hub.py:114-125`, subscribe handling at
  `hub.py:261-266`), and the frontend router dispatches by `event_type`,
  silently ignoring types with no registered handler (`events.ts:69`).
  `EventCategory` is currently load-free. Existing clients therefore receive
  the coarse stream regardless of its category and drop it on the floor; the
  only cost is wire bytes. Decision: `GraphActionRecorded` carries
  `category: GRAPH` — it is a graph-mutation summary, which is what GRAPH
  means, and if category filtering ever becomes real, "coarse actions are
  GRAPH events" is the reading that keeps old subscribers working.
- **Ring size**, and whether it is configurable.
- **Where the panel lives.** The layout critique from the retrieval discussion
  applies unchanged: the dashboard already has two vertical panels, a drawer and
  a strip, and a fourth vertical column would starve the graph. A log is a
  narrow list and probably wants the drawer or a rail, not a division.
