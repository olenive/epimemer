# Event log: a readable record of what the agent did

A log panel in the dashboard: a list of what the agent changed, filterable,
with entries that highlight their nodes in the graph on click.

The motivating case, in the user's words: *"if the log entry is node 123 is
being superseded by node 124, it would be handy if I could click on the log
and have the nodes highlighted in the graph."* Most of this design is about
why that sentence could not be rendered from the fine-grained event stream,
and what had to exist instead.

---

## 1. Why it is worth having

Supersession is a destructive-looking act. A belief the graph held is now
historical, the agent decided it, and without a log there is no visible trace.
Ten tool calls in response to "update the graph with recent information" is
normal, and all of it would be invisible.

An audit trail of status changes is the difference between a memory system
and a black box. It is the feature that makes the system accountable to its
user rather than merely useful to its agent.

---

## 2. Counterpart ids live on the lifecycle episode

"123 superseded by 124" needs both ids in one place. The fine-grained stream
does not offer that: `NodeStatusChanged` carries `node_id`, `old_status` and
`new_status`, and the relation is a separate lineage edge (`superseded_by` or
`temporally_followed_by`) published as its own `EdgeStored` a moment later.
Joining the two by adjacency in the stream breaks the moment anything
interleaves.

So the counterpart id rides on the node itself, in its append-only lifecycle
episode list (§6), and both surfaces read it from there: the live act
(`GraphActionRecorded`, §3.1) and the durable history (`query_changes`). Every
status flip goes through `set_node_status_tx` or one of the other transaction
boundaries, so every flip writes an episode by construction rather than by
discipline. `events_in_window` keeps a fallback that reads the scalar
`(status, superseded_at)` pair for graphs written before episodes existed.

---

## 3. Granularity: the transaction, not the write

Measured by instrumenting a real ingest (`InMemoryStorage` behind
`instrument_storage`, a counting subscriber on the bus, mock embeddings):

| Act | Events emitted |
|---|---|
| One node stored, fresh ingest | about 7 (`NodeStored` + `EmbeddingStored` + about 3.6 `EdgeStored` + pipeline share) |
| One `store_decomposition` (25 nodes) | 176 to 309 depending on segment layout; **one** act |
| One `search` | 40 |
| One `update` (a supersede) | 4 |
| A ten-call task | hundreds to about 2,000 by mix |

Fresh-ingest edge density (about 3.6 per node) is lower than a mature graph's
(about 6.5), because reflection adds edges after the fact; any ring bound or
batching threshold should trace to the measured numbers above rather than to
`graph_stats` on an old graph.

The estimate that prompted this feature was "on the order of ten". That is
right about *tool calls* and two orders of magnitude below the *event* count,
because events are emitted at storage-write granularity. A raw event log is a
firehose. `VISUALISATION.md` B.0 replaced raw per-event pipeline rendering
with aggregate tiles for exactly this reason, and a raw log would
re-introduce what B.0 removed.

### 3.1 The readable unit is the transaction

`supersede_node_tx` is one act that publishes four or more events. So do
`merge_nodes_tx` and `write_batch_tx`. The entry a person wants to read,
*"corrected 123 → 124, +3 evidence edges"*, corresponds to the transaction,
and no single fine-grained event represents it. Reconstructing it by grouping
in the frontend would need a correlation id that does not exist and would
guess wrong under concurrency. Instead, a coarse event is published at the
transaction boundary:

```python
class GraphActionRecorded(Event):
    """One human-meaningful act, at the transaction boundary that performed it."""

    category: Literal[EventCategory.GRAPH] = EventCategory.GRAPH  # §10
    event_type: Literal["graph_action_recorded"] = "graph_action_recorded"
    action_id: str  # monotonic, assigned at the source (§4.1)
    verb: ActionVerb  # §11
    subjects: list[str]  # node ids, primary first
    counts: dict[str, int]  # {"edges": 3, "nodes": 1}: what it swept up
    summary: str  # pre-rendered one-line text, for display and substring filter
```

`instrumented_storage.py` already sits at the transaction boundary and holds
every id involved. Fine-grained events keep flowing untouched; the graph panel
needs them. The log consumes only the coarse stream.

`summary` is pre-rendered on the emitting side deliberately. A log line the
frontend assembles from parts is a second place where the system's vocabulary
gets decided, and it would drift from the tool responses that use the same
words.

---

## 4. The record ring

### 4.1 Sequencing: `seq` cannot carry this

`seq` is assigned per browser connection by the hub at send time, starts at 0
for each socket, and resets on reconnect. Two browsers see different numbers
for the same event. It is a drop detector, not a position in a stream: a log
cannot use it to dedup across a reconnect, to request "everything after N", or
to guarantee stable order. Hence `action_id`, assigned by the session process
that emits the action.

`action_id` is a process-wide counter, zero-padded. One process is one session
(`session_id` is a fresh uuid4 per process), so the process is the right
scope, and the padding makes the lexical order a JSON consumer gets for free
the numeric one, so the frontend can sort and dedup on it without parsing.
`test_action_ids_are_monotonic_across_browser_reconnects` is the test a
`seq`-based implementation fails.

### 4.2 Placement: hub-side, at the existing choke point

Every event passes through one line in `hub.py`, where the hub stamps
`session_id` before fan-out, and `sessions[sid]` is already a per-session
dict. The ring hangs off it (`visualization/ring.py`, `LOG_RING_CAPACITY =
512`) and selects on `event_type == "graph_action_recorded"`, not on category
(§10). A browser receives backfill on subscribe, with no RPC round-trip and
without waking a session process. `_replay_actions` runs the same
`_subscribed(payload)` predicate the live path runs, so a replayed entry can
never reach a browser a live one would not have.

The ring is values, not a buffer: `remember` returns a new tuple. The hub
iterates its per-session dict while fanning out, and a ring that mutated in
place would have every reader sharing one buffer.

**512, not configurable.** A 25-node `store_decomposition` emits hundreds of
fine-grained events and one act, so the coarse stream is two orders of
magnitude smaller than the firehose §3 refused, and 512 entries is a long
working session. A knob here would be one more number nobody has measured,
and the ring is bounded either way.

**What it survives, stated plainly:** browser reloads, which is the common
case (you open the dashboard *after* noticing the agent did something), and
an MCP exit, because the hub keeps disconnected sessions. It does not survive
a hub restart, and a restarted MCP server is a different session with its own
ring and its own numbering. Only §6's durable path outlives that.

### 4.3 Retrieval records share the module

Retrieval provenance (`RETRIEVAL_PROVENANCE.md`) uses the same ring module,
a second instance, also hub-side, with the same sequencing discipline. Its
records carry response payloads, so the sensitivity boundary is the bind
rather than the process: on a non-loopback bind sessions mirror structural
metadata only. Details and caps are in `RETRIEVAL_PROVENANCE.md` §3.2.

---

## 5. Filtering

Structured filters, not search. Three of the four things you would look for
are filters over fields:

- **verb**: chips, multi-select
- **node id**: text box, exact
- **time range**: the log's own two date inputs, sharing the timeline's
  `TimeRange` rule (half-open, from `timeline-filter.ts`) but not its
  controls. The timeline's inputs are also written by shift-drag zoom on the
  axis, so sharing the controls would let scrubbing the timeline silently
  filter the log: a filter nobody set, whose cause is in another panel.
- **free text**: plain substring over `summary`

**Not BM25, and not because it is expensive.** Log vocabulary is a dozen verbs
repeated thousands of times, and SurrealDB's BM25 clamps IDF to zero above
50% document frequency (`LEXICAL_SEARCH.md` §2.5). Every verb term would sit
far above that threshold and every match would tie at `0.0`: a ranking
function returning a constant. Node ids would score well, being maximally
rare, but for an id you want exact match, which is a lookup.

**This is a frontend feature.** The ring is a few hundred entries in memory;
filtering is `Array.prototype.filter`. No protocol method, no storage schema,
no cross-backend parity problem. It must not be routed through `text_search`.

### 5.1 The genuinely textual query is a graph search, not a log search

*"What happened to the fact about deployment rollbacks?"* is unanswerable
from the log, whose entries hold ids rather than content. It is a graph
lexical search whose hit ids then filter the log, and it needs nothing on the
log side.

---

## 6. Live log and history: two features, one UI

`query_changes` answers "what was born or retired in `[start, end)`" for all
time, persisted, on both backends, with per-node lifecycle events. That is a
durable audit trail.

| | Source | Lifetime | Filtering |
|---|---|---|---|
| **Live log** | hub ring (§4.2) | session | client-side (§5) |
| **History** | `query_changes` | as long as the graph | time window + node type |

Present them as one timeline in the UI if that reads better. Design them as
two. Collapsing them is how a bounded ring quietly becomes a database with a
retention policy nobody chose.

**History is a reading of node fields, not a second store.** Nothing is
ring-buffered and nothing grows on its own: the trail's size is the graph's
size, bounded by archival, and its durability is the backend's. It is
node-only: no edge changes, no pipeline runs, no retrievals.

**Lifecycle episodes.** A scalar `(status, superseded_at)` pair cannot
represent *retired, then came back*: clear `superseded_at` on restore and the
retirement vanishes from every window; keep it and the event's kind reads
`"active"` at the retirement timestamp. Since the validity model allows
cycles, a scalar `restored_at` only defers the same overwrite to the second
retirement. So each node carries an append-only list of episodes, each
`{retired_at, because, counterpart, restored_at | None}`, with `(status,
superseded_at)` kept as the current-state snapshot for fast paths.
`events_in_window` derives from the episodes, so every retirement and every
return stays reportable, with the right kinds, through any number of cycles.
Episodes grow only on actual transitions; most nodes never have more than
one. `test_query_changes_reports_every_episode_of_a_recurring_node` is the
test a scalar implementation fails.

This section is transaction time only: what the graph did, and when. "When
was Labour in power" is valid time (per-source intervals,
`VALIDITY_DESIGN.md`), and "the Christmas holiday period" is a recurrence rule
with no lifecycle at all.

---

## 7. Click to highlight

`highlightNodes` in `graph-panel.ts` returns a report, and the caller says
what happened. Two silent failures are closed:

1. **Unknown id.** `cy.getElementById(id)` returns an empty collection and
   `.addClass` is a no-op. The report names the ids not in the current graph.
2. **Filtered-out node.** The type filter sets `display: none`, so the class
   would land on something invisible. The filter is cleared only when it
   would hide something being highlighted; clearing unconditionally would
   undo a filter the user set every time they clicked an entry about a node
   that filter already showed.

Both are pure functions (`missingFrom`, `filterAfterHighlight`,
`highlightNote`) because cytoscape cannot be instantiated under jsdom, so the
rule had to be extractable to be testable at all. The same two failures are
shared with focus mode (`RETRIEVAL_PROVENANCE.md` §4.4) and fixed once.

Selection is bidirectional, and clicking a node does not reveal the rail: it
sets the log's node-id filter and stops there. Opening a panel on every node
click is the drawer-stealing behaviour `RETRIEVAL_PROVENANCE.md` §5.2 rules
out; the filter is simply there when you open the log.

Scoping follows the pipeline strip's rule: a session switch clears, and
entries from another graph never highlight into the viewed one.

---

## 8. Tests

`tests/visualization/test_graph_actions.py`, `test_hub.py`, `test_ring.py`,
`log-store.test.ts` and `graph-panel.test.ts`. The two load-bearing ones are
named in §4.1 and §6.

---

## 9. Where acts are emitted

At the five `_tx` boundaries and nowhere else: `supersede_node_tx`,
`supersede_by_existing_tx`, `merge_nodes_tx`, `set_node_status_tx`,
`write_batch_tx`. Single writes (`store_node`, `store_edge`) emit no act: they
are writes, not transactions, and the production callers of `store_node` are
source and tag upserts that nobody wants a log line for. Consequence, stated
rather than hidden: an act performed entirely through single writes would not
appear in the log. Nothing on the production path does.

---

## 10. Placement and category

**The panel is a rail, not a drawer tab.** The drawer holds Node and Response
tabs (`RETRIEVAL_PROVENANCE.md` §5.1); a third tab would put three unrelated
drivers in one pane. The rail is a fixed-width sibling of the split container,
hidden until the header's **Log** button asks for it, so the graph keeps its
width by default. It sits *beside* `#split-container`, not inside it:
`layout.test.ts` guards that the container has exactly two halves and the
divider as children, because the detail drawer once sat inside it and every
hover resized the timeline.

**`GraphActionRecorded` is category `GRAPH`.** Nothing filters by category:
hub subscription filtering keys off session and graphs, and the frontend
router dispatches by `event_type`, silently ignoring types with no registered
handler. So existing clients receive the coarse stream regardless and drop it
on the floor; the only cost is wire bytes. It is a graph-mutation summary,
which is what GRAPH means, and if category filtering ever becomes real,
"coarse actions are GRAPH events" is the reading that keeps old subscribers
working.

---

## 11. Verbs

`ActionVerb` is `stored`, `corrected`, `world_changed`, `merged`, `archived`,
`restored`, `undetermined`. **There is no `superseded` verb.** The validity
model made supersession two opposite acts, a correction (`it_was_wrong`,
CORRECTED, `superseded_by`, terminal) and a world change
(`the_world_changed`, HISTORICAL, `temporally_followed_by`, restorable), and a
log line reading "superseded 123 → 124" would flatten exactly the distinction
the graph records. The verbs match the terminal statuses and what
`events_in_window` emits, so the live log and the durable history speak one
vocabulary. Recurrence needs no verb of its own: the `recurs` verdict resolves
as restore plus a new source edge, which is `restored` with the edge in
`counts`. `test_there_is_no_superseded_verb` guards this.

### 11.1 `undetermined`, and when it goes

`NodeStatus.SUPERSEDED` still exists, kept for rows that predate the split and
genuinely do not record which act they were. It maps to `undetermined`, not
to `corrected` (an invented answer) and not to `superseded` (what §11
forbids). `verb_for_status` also falls through to `undetermined` for a status
the module has never heard of, because "it left the active set" is an
assumption, not a fact: `ACTIVE → restored` is already a non-retirement, so a
status added later need not be a retirement either, and a default that
claimed one would state something false about what the agent did.

The two defaults in the system point opposite ways on purpose. The frontend's
`statusOpacity` fades an unlisted status; this fall-through refuses to claim
one. Fading a live node is cosmetic; a log line asserting a retirement that
did not happen is a false statement about the agent. Because the verb names a
state rather than an act, `summarise` renders it as "status undetermined: N
nodes". Guarded by
`test_an_unrecognised_status_does_not_claim_the_node_was_retired` and
`test_an_undetermined_act_still_reads_as_a_line`.

**Sunset.** The map entry `NodeStatus.SUPERSEDED: ActionVerb.UNDETERMINED` is
dead code the day `NodeStatus.SUPERSEDED` leaves the enum, and should be
deleted in that same change, not before and not as a tidy-up of its own:
"no graph anywhere still holds such a row" is not something this repository
can observe, and the enum member is the whole remaining supply. Whoever
removes it owns the read-side question too, since a stored row still carrying
the string would then fail at the Pydantic boundary. The fall-through has no
sunset: it answers for statuses that do not exist yet, so
`ActionVerb.UNDETERMINED` outlives the legacy status it was first written for.
