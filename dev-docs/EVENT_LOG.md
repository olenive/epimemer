# Event log: a readable record of what the agent did

The dashboard's log rail (`log-panel.ts`, `log-store.ts`): a list of what the
agent changed and of the warnings its tools raised, filterable, with entries
that highlight their nodes in the graph on click. It reads the
`graph_action_recorded` and `advisory_raised` events, whose wire shape, replay
and sequence number are `VIZ_CONTRACT.md` §7. The rules it shares with the
other panels are `VISUALISATION.md`, *Rules every panel follows*.

---

## 1. Why it is worth having

Supersession looks destructive: a belief the graph held becomes historical
because the agent decided so, and ten such calls in answer to "update the
graph" are normal. An audit trail of status changes is what makes the system
accountable to its user rather than merely useful to its agent.

---

## 2. Counterpart ids live on the lifecycle episode

"123 corrected by 124" needs both ids in one place, and joining a status change
to the lineage edge published beside it breaks the moment anything
interleaves. So the counterpart id rides on the node itself, in its
append-only lifecycle episode list (§6). The live act, the `counterpart` on
`node_status_changed` and the durable history (`query_changes`) all read it
from there. Every status flip goes through a transaction boundary, so every
flip writes an episode. Graphs written before episodes existed are read from
the node's scalar `(status, superseded_at)` pair instead.

---

## 3. Granularity: the transaction, not the write

Events are emitted at storage-write granularity, two orders of magnitude above
the number of tool calls, so a raw event log is a firehose. Measured on an
instrumented ingest (`InMemoryStorage` behind `instrument_storage`, a counting
subscriber on the bus, mock embeddings):

| Act | Events emitted |
|---|---|
| One node stored, fresh ingest | about 7 (`NodeStored` + `EmbeddingStored` + about 3.6 `EdgeStored` + pipeline share) |
| One `store_decomposition` (25 nodes) | 176 to 309 depending on segment layout; **one** act |
| One `search` | 40 |
| One `update` (a supersede) | 4 |
| A ten-call task | hundreds to about 2,000 by mix |

A mature graph has more edges per node than a fresh ingest, because reflection
adds them later, so a ring bound or batching threshold should trace to these
numbers rather than to `graph_stats` on an old graph. The pipeline strip shows
aggregate tiles for the same reason (`VISUALISATION.md` B.0).

### 3.1 The readable unit is the transaction

`supersede_node_tx`, `merge_nodes_tx` and `write_batch_tx` are each one act
that publishes four or more events. The entry a person wants to read,
*"corrected 123 → 124 (3 edges)"*, corresponds to the transaction, and
grouping fine-grained events in the frontend would need a correlation id that
does not exist and would guess wrong under concurrency. So one coarse event,
`graph_action_recorded`, is published for each act at the transaction boundary
by the storage wrapper (`instrumented_storage.py`), which already sits there and
holds every id involved; `graph_actions.py` decides what it says. The
fine-grained events keep flowing for the graph panel; the log reads only the
coarse stream.

`summary` is rendered on the emitting side, because a log line the frontend
assembles from parts would be a second place where the system's vocabulary
gets decided, and it would drift from the tool responses that use the same
words.

---

## 4. The record ring

### 4.1 Sequencing: `seq` cannot carry this

`seq` is per browser connection and resets on reconnect (`VIZ_CONTRACT.md`
§7.2), so two browsers see different numbers for the same event: it is a drop
detector, not a position. A log needs a position to dedup across a reconnect
and keep a stable order, hence `action_id`, assigned by the session that emits
the act from one numbering that its acts and warnings share, and zero-padded
so the lexical order a JSON consumer gets for free is the numeric one.

### 4.2 Placement: hub-side, at the existing choke point

Every event passes through the one place in the hub that stamps `session_id`
before fan-out, and the hub already keeps a dict per session. The ring hangs
off it and keeps the acts and the warnings that join them (§12). A browser
receives the backfill on subscribe, with no RPC round-trip and without waking a
session process, filtered by the same test as the live stream. Retrieval
records use the same ring module, `ring.py` (`RETRIEVAL_PROVENANCE.md` §3.2).

**512, not configurable.** The coarse stream is two orders of magnitude smaller
than the firehose §3 refused, and 512 entries is a long working session. A
knob here would be one more number nobody has measured, and the ring is
bounded either way.

**What it survives:** browser reloads, the common case (you open the dashboard
*after* noticing the agent did something), and an MCP exit, because the hub
keeps a disconnected session listed for 300 seconds. It does not survive a hub
restart, and a restarted MCP server is a new session with its own ring and its
own numbering. Only §6's durable path outlives that.

---

## 5. Filtering

Structured filters, not search:

- **verb**: chips, multi-select, built from the verbs the log holds
- **node id**: text box, exact
- **time range**: the log's own two date inputs, sharing the timeline's
  `TimeRange` rule (an empty input leaves that end open) but not its controls.
  The timeline's inputs are also written by shift-drag zoom on the axis, so
  sharing the controls would let scrubbing the timeline filter the log: a
  filter nobody set, whose cause is in another panel.
- **free text**: plain substring over `summary`

**This is a frontend feature, and must not be routed through `text_search`.**
The ring is a few hundred entries in memory and filtering is
`Array.prototype.filter`: no protocol method, no storage schema, no
cross-backend parity problem. Ranked search would not help: log vocabulary is a
dozen verbs repeated thousands of times, and SurrealDB's BM25 clamps IDF to
zero above 50% document frequency, so every verb match would tie at `0.0`; an
id wants exact match, which is a lookup. Log entries hold ids rather than
content, so a question about content ("what happened to the fact about
deployment rollbacks?") belongs to a graph search, not to the log.

---

## 6. Live log and history: two features

`query_changes` answers "what was born or retired in `[start, end)`" for all
time, persisted, on every backend, with per-node lifecycle events: the durable
audit trail. The dashboard shows the live log only.

| | Source | Lifetime | Filtering |
|---|---|---|---|
| **Live log** | hub ring (§4.2) | session | client-side (§5) |
| **History** | `query_changes` | as long as the graph | time window + node type |

If the UI ever presents the two as one timeline, they stay two features
underneath: collapsing them is how a bounded ring quietly becomes a database
with a retention policy nobody chose.

**History is a reading of node fields, not a second store.** Nothing is
ring-buffered and nothing grows on its own: the trail's size is the graph's
size, bounded by archival, and its durability is the backend's. It is
node-only: no edge changes, no pipeline runs, no retrievals.

**Lifecycle episodes.** A scalar `(status, superseded_at)` pair cannot
represent *retired, then came back*, and since the validity model allows
cycles, a scalar `restored_at` only defers the same overwrite to the second
retirement. So each node carries an append-only list of `LifecycleEpisode`s
(`core/types.py` says why in full), with `(status, superseded_at)` kept as the
current-state snapshot for fast paths.

This is transaction time only: what the graph did, and when. "When was Labour
in power" is valid time (`VALIDITY_DESIGN.md`), and "the Christmas holiday
period" is a recurrence rule with no lifecycle at all.

---

## 7. Click to highlight

Clicking an entry highlights the nodes it acted on, and a highlight that finds
nothing says so in the rail (`VISUALISATION.md`, *Rules every panel follows*).

Selection is bidirectional: clicking a node in the graph sets the log's
node-id filter and does not reveal the rail, since opening a panel on every
node click is the drawer-stealing behaviour the same rules forbid. The filter
is there when you open the log.

---

## 8. Tests

A new transaction boundary gets its act test in `tests/visualization/` on the
`storage` fixture, so it runs on every backend, beside a parity case in
`tests/storage/test_storage_parity.py`.

---

## 9. Where acts are emitted

At the transaction boundaries (the `_tx` methods) and nowhere else. The one
boundary that emits no act is `resegment_tx`: a re-cut is material rather than
a judgment, and the live log names acts a person would review. Single writes
(`store_node`, `store_edge`, `store_timeline`) emit no act, because they are
writes, not transactions, so an act performed entirely through single writes
does not appear in the log.

### 9.1 Timeline decisions, and why they needed a boundary of their own

The six timeline decisions, `order_timepoints`, `resolve_temporal_contradiction`,
`merge_timepoints`, `add_recurrence`, `end_recurrence` and
`record_recurrence_exception`, land through `write_timeline_tx`, so the live
log sees each one the journal records.

The boundary also makes the write atomic. A split or a merge changes the
timeline record *and* re-points every `TIMELINK` naming the point it touched;
as separate writes, a failure in the middle would leave the record saying a
fact had moved while its link still named the old date. One transaction takes
the timeline, the retired links and their replacements, and the wrapper
publishes one act for the whole decision, so a split that moves four facts is
one line. The decision's kind, counts and judge travel on the write because
the wrapper has no other way to learn which decision it persisted.

**The four mark-placing tools stay silent**: `create_timeline`,
`set_reference_time`, `add_timepoint` and `create_timelink` name no judge and
write no journal row. They record where something sits rather than deciding
anything about it, and a log line for each would bury the six decisions. A
restore replays somebody else's decisions and makes none, so it reads as the
single `stored` act `write_verbatim_tx` emits.

### 9.2 `reopen_tx`, the boundary a withdrawal lands through

Withdrawing a suppression is a judgment, and its layers clear differently (a
fact pair retires its `assessed` edge, a label pair takes a new verdict row, a
kept node or a declined boundary needs neither); what they share is the
journal row, which no other boundary takes. So `reopen_tx` takes the decision
record itself, plus whatever the layer had to write, and lands them together.

**The row is the act here, not a note about one.** A reopen whose row was lost
would put a question back on the worklist with nothing saying it had ever been
declined, in front of a judge who cannot see the history. The wrapper reads
the act straight off the row, and republishes the retired edges, because a
viewer holding the live edge would go on drawing one nobody follows.

---

## 10. Placement and category

**The panel is a rail, not a drawer tab.** The drawer holds the Node and
Response tabs (`RETRIEVAL_PROVENANCE.md` §5.1), and a third tab would put three
unrelated drivers in one pane. The rail is a fixed-width sibling of the split
container, hidden until the header's **Log** button asks for it, so the graph
keeps its width by default. It sits *beside* the split container rather than
inside it, because anything inside resizes the timeline on every hover;
`layout.test.ts` guards that the container holds only its two halves and the
divider.

**`graph_action_recorded` is category `GRAPH`**, because it summarises graph
mutations. Nothing filters by category: the hub filters by session and graphs,
and the frontend dispatches by `event_type`, ignoring types with no handler, so
an older client drops the coarse stream at the cost of wire bytes only.

---

## 11. Verbs

`ActionVerb` is `stored`, `corrected`, `world_changed`, `merged`, `archived`,
`restored`, `undetermined`. **There is no `superseded` verb.** Supersession is
two opposite acts, a correction (`it_was_wrong`, CORRECTED, `superseded_by`,
terminal) and a world change (`the_world_changed`, HISTORICAL,
`temporally_followed_by`, restorable), and a log line reading "superseded
123 → 124" would flatten exactly the distinction the graph records. The verbs
match the terminal statuses and what `query_changes` reports, so the live log
and the durable history speak one vocabulary. Lifecycle recurrence needs no
verb of its own: the `recurs` verdict, a retired claim becoming true again,
resolves as restore plus a new source edge, which is `restored` with the edge
in `counts`.

### 11.1 `undetermined`

`NodeStatus.SUPERSEDED` still exists for rows that predate the split and do
not record which act they were. It maps to `undetermined`, not to `corrected`
(an invented answer) and not to `superseded` (what §11 forbids). A status the
module has never heard of also falls through to `undetermined`, because a
status added later need not be a retirement, and a default that claimed one
would state something false about what the agent did. The legacy map entry in
`graph_actions.py` goes in the same change that removes
`NodeStatus.SUPERSEDED`; the fall-through stays.

The two defaults in the system point opposite ways on purpose. The frontend
fades a status it does not know; this fall-through refuses to claim one.
Fading a live node is cosmetic; a log line asserting a retirement that did not
happen is a false statement about the agent. Because the verb names a state
rather than an act, the line reads "status undetermined: N nodes".

### 11.2 A timeline act carries its `DecisionKind`

The six timeline decisions, and a reopen, take no verb from `ActionVerb`.
`DecisionKind` already names them (`temporal_order`, `temporal_verdict`,
`timepoint_merge`, `recurrence`, `recurrence_bound`, `recurrence_exception`,
`reopened`), the journal records them under those names, and `review` selects
on them; more members here would be a second set of names that would drift. So
`verb` is `ActionVerb | DecisionKind`, and a timeline act carries the kind.

Widened rather than given a second `kind` field, because the log filters and
renders one word per act; the two vocabularies share no value, so what arrives
on the wire is unambiguous. Each kind has a verb phrase ("merged timepoints on a1b2c3d4", "reopened a1b2c3d4");
a kind with no phrase falls back to its own words, because a line saying
"1 node" about a timeline would state something false about what was touched.

---

## 12. The second stream: warnings

A warning is computed inside a tool call, before or after the write, from what
the call was about; the kinds, the per-graph policy and the mute are
`WARNINGS_AND_SETTINGS.md`. The tool returns the warning to the agent,
journals a "proceeded despite warning" row when the warning objects to the
call, and publishes it to the dashboard, which shows it beside the act it
accompanied. The Python class is `Advisory`; on the wire and here, a warning is
what the agent receives under `warnings`.

### 12.1 A new event, not a field on the act

The dashboard hears about a warning through its own event, `advisory_raised`,
published by the tool layer where the warnings are computed
(`publish_advisories` in `epimemer/mcp/tools.py`), as `retrieval_recorded` is.
It cannot ride on the act: acts are published by the storage wrapper, which
sees storage calls only; a call can produce several acts, so there is no
single act to put it on; and `reflect` raises warnings with no act at all.

### 12.2 Everything computed is published, with whether the agent saw it

The event carries every warning the tool computed, muted or not, with two
booleans: `surfaced` (was it in the agent's response) and `notify_user` (was
the agent told to raise it with the user). The dashboard is where a person
looks at what the agent was not told, so hiding muted warnings there would
defeat the point; the journal records regardless of the mute too. One rule,
`is_surfaced` in `core/advisories.py`, decides `surfaced` for the response and
the event alike. `reflect`'s warnings on merge candidates publish on the same
terms: `surfaced: false` for one the mute stripped from the candidate, and
`notify_user` only where a surfaced warning resolves to `flag`.

### 12.3 A row in the live log, not a separate panel

A warning is a line in the story of a session. It joins the log with the verb
`warned`, which the verb filter handles with no special case, and the
substring, node id and time filters apply as they do to an act. The row reads
"*tool* warned: *message*; the agent proceeded" (or "flagged to the user"),
with the message word for word. It is marked in the `pending` hue; a warning
the agent was not shown draws dimmer, with "not shown to the agent" in its
tooltip; a click highlights its subjects. There is no unread badge, since the
dashboard has no notion of "read".

**One sequence, one ring.** A call publishes one event per warning, after the
acts of the same call, with an `action_id` from the numbering the acts use, so
a warning reads as a line after what it was about. It is not a link to an act,
and none exists: the tool never sees the numbers its acts were given, and
arrival order is what a log needs. The hub keeps warnings and acts in one ring
for the same reason: two rings replayed one after the other would put every
warning after every act.

### 12.4 Settings are shown, not changed

The header's **warnings** button opens a small read-only panel titled with the
graph's name: the mute in words, then one row per kind with the action in force
and whether it is inherited from the process default or set on this graph. The
panel asks the session for exactly the response `configure_warnings` gives, in
which a kind absent from `overridden.by_kind` is inherited, and re-reads on
`graph_switched`, as the reflect badge does.

Nothing on the dashboard writes a setting, because a write from the browser
would be the first write into a graph with no author: every change is
journalled against a session and a judge, and a browser has neither. That is
the identity question parked under "Sharing a graph between users" in
`PROPOSED_FEATURES.md`, and this panel must not settle it by accident.
