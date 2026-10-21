# The visualisation contract

A specification, live: what a client of the visualisation hub may rely on.

The shapes are written down for more than one client: the TypeScript
dashboard, and the Bevy visualiser in the `epimemer-bevy` repository
(github.com/olenive/epimemer-bevy). Where this document quotes a shape, the
shape was read from `epimemer/visualization/`, and the Pydantic models there
remain the source of truth (§9).

The hub's architecture, the pipeline strip and the colour customisation are
in `VISUALISATION.md`; the event log's design is in `EVENT_LOG.md`. This
document does not restate their reasons, only the surface they produce.

## 1. The parts

Three processes take part.

- **A session** is one MCP server process. It owns its storage, dials out
  to the hub, registers, publishes events, and answers the hub's read
  requests from its own storage. A session's identity is a `session_id`,
  a fresh uuid4 per process, so a restarted server is a new session.
- **The hub** (`hub.py`) is a relay and a session registry. It owns the port,
  holds no storage and keeps only two bounded rings per session for the
  event log and the retrieval records. One hub serves every session on the
  machine.
- **A client** connects to the hub, lists the sessions, picks one, fetches
  that session's snapshot over HTTP and follows its events over a WebSocket.
  The dashboard is one client; this document is for the next one.

A client never speaks to a session directly. Every read it makes is an HTTP
request to the hub, which forwards it to the session as a remote procedure
call over the session's own socket (§4) and returns the session's answer.

## 2. Reaching a hub

| Item | Value |
|---|---|
| Default bind | `127.0.0.1:8765` |
| Environment | `EPIMEMER_VIZ_HOST`, `EPIMEMER_VIZ_PORT` (read by the hub and by the MCP server's `ServerConfig`, which also has `EPIMEMER_VIZ_ENABLED` and `EPIMEMER_VIZ_AUTOSPAWN`) |
| Start | `epimemer-viz` (or `python -m epimemer.visualization.hub`); an MCP server with autospawn on starts one itself when none answers |
| Status | `epimemer-viz --status`: the hub's pid and version, then one line per session |
| Stop | `epimemer-viz --stop`, through the pidfile at `~/.epimemer/viz-hub.pid` |
| Liveness | `GET /api/health` answers with `"service": "epimemer-viz-hub"`, which is how a probe tells a hub from anything else on the port |

**CORS.** The hub sets no cross-origin headers, and the dashboard needs none
because the hub serves it from the same origin. A browser client served from another origin, a
wasm build served by its own development server for instance, would need
the hub to grow CORS headers or be served from the hub's own static mount.
A native client is unaffected.

**Exposure.** The hub is meant for loopback. One guard follows the bind: on
a non-loopback `EPIMEMER_VIZ_HOST` a session publishes retrieval records
with the query text and response payloads removed (`structural_only` in
`epimemer/mcp/retrieval_records.py`), and the full records stay behind
`/api/retrievals`, which is answered by the session process. Nothing else
changes with the bind.

## 3. The schema version

An integer, `1` today, named by the constant `VIZ_SCHEMA_VERSION` in
`epimemer/visualization/protocol.py` and carried under the key
`schema_version`:

- on the snapshot (§5), at the top level;
- on the `Register` message a session sends the hub (§8), so the hub knows
  what each session speaks;
- on `GET /api/health`, for the hub itself;
- on each entry of `GET /api/sessions`, copied from that session's
  registration.

The rule for changing it: a field removed or renamed, a value whose meaning
changes, or a value vocabulary that loses a member, bumps the version. A
field added does not, and a client must ignore fields it does not know. A
client should compare the version before it parses anything else and say
plainly which version it was written for when the numbers differ.

A client should read an absent `schema_version` on `/api/health` or on the
snapshot as "older than 1". A session that registered without one, because
it runs an older Epimemer, is listed on
`/api/sessions` with `schema_version: 0` (the constant `UNVERSIONED_SCHEMA`),
a number rather than a missing field, so the comparison has no special
case.

## 4. The HTTP routes

Every route returns JSON. A missing required query parameter is a `400`
with `{"error": "<message>"}`. A route that forwards to a session returns
`404` when the session is unknown or not connected, `504` when the session
did not answer within ten seconds, and `502` when the session answered with
an error or disconnected during the call, each with the same `{"error"}`
body. `/api/health` and `/api/sessions` answer from the hub alone and never
return those three.

| Route | Parameters | Answered by | Response |
|---|---|---|---|
| `GET /` | | hub | The dashboard page, or `503` with plain text saying how to build it when the bundle is absent. Static assets are mounted beside it. |
| `GET /api/health` | | hub | `{"ok": true, "service": "epimemer-viz-hub", "pid", "version", "sessions", "schema_version"}`. `version` is the installed Epimemer package version, `sessions` the number of sessions listed, connected or not. |
| `GET /api/sessions` | | hub | A list, one object per session: the `SessionInfo` fields `session_id`, `pid`, `backend`, `active_graph`, `started_at`, plus `connected` (bool), `last_event_at` (ISO timestamp or null) and `schema_version`. A disconnected session stays listed for 300 seconds. |
| `GET /api/graphs` | `session` | session | `{"graphs": [names], "active_graph", "backend", "reflect": {"count", "threshold", "suggested"}}`. `reflect` describes the active graph only. |
| `GET /api/snapshot` | `session`, `graph` | session | The snapshot (§5). Any graph the session's backend holds, not only its active one. |
| `GET /api/embeddings` | `session`, `graph`, `node_type` (optional) | session | The embeddings (§6). |
| `GET /api/warnings` | `session` | session | What the active graph does about advisories, in the shape the MCP tool `configure_warnings` returns, naming the graph. |
| `GET /api/retrievals` | `session` | session | `{"records": [...]}`: this session's retrieval records, payloads included, each a serialised `RetrievalRecord` from `epimemer/mcp/retrieval_records.py`. |
| `WS /ws` | | hub | The client event stream (§7). |
| `WS /ingest` | | hub | The session side (§8). A client does not use it. |

`backend` is the storage kind: `memory`, `sqlite`, `surrealdb` or `postgres`.

## 5. The snapshot

`GET /api/snapshot?session=<id>&graph=<name>` returns one object:

```
{
  "schema_version": 1,
  "graph": "<name>",
  "nodes": [NodeView],
  "edges": [EdgeView],
  "boundary_proposals": [BoundaryProposalView],
  "timelines": [TimelineView],
  "metacontexts": [MetacontextView],
  "relation_labels": [RelationLabelView]
}
```

The session reads all of it inside one turn of its storage guard, so a
snapshot describes one instant: no write lands between the nodes being read
and the edges being read. On a real graph (the development graph, 2,026
nodes and 16,891 edges) the document is about 6.4 MB.

What is in it and what is left out:

- `nodes` are the graph's **active** nodes: topics, facts and inferences.
  Historical, corrected, merged and archived nodes are not sent, though a
  `node_status_changed` event (§7.4) tells a client when a node leaves the
  active set, and a `node_stored` event may carry a node in any status.
- `edges` are the **live** edges: an edge a judge retired (`retired_at`
  set) is left out, so a timelink a split or a merge moved does not put a
  claim on two dates at once.
- `boundary_proposals` are the boundaries reflect would offer for a period
  a source left open, computed from these same nodes and edges and never
  stored. Proposals a judge has declined are left out.
- `timelines` carry their timepoints embedded, with read-time positions for
  vague points and the occurrences of recurrence rules enumerated over a
  window the snapshot chooses (the span of the timeline's dated points,
  widened by one period of the rule on each side; the timeline's stated
  present when it has no dated points; no window, and no occurrences, when
  it has neither).
- `metacontexts` and `relation_labels` ride along so a client can name what
  an edge only references by id or by string.

### 5.1 Endpoints that are not nodes

An edge's `src_id` and `dst_id` are ids, and several edge types have an
endpoint that is **not** in `nodes`:

| Endpoint kind | Found in | Edge types |
|---|---|---|
| segment | nowhere in the snapshot | `about`, `contains`, `implies` have a segment as `src_id` |
| document | nowhere in the snapshot | `sourced_from` has a document as `dst_id` |
| metacontext | `metacontexts` | `has_metacontext` |
| timeline | `timelines` | `timelink`, with the timepoint named by `metadata.timepoint_id` on the edge |

A client must tolerate an endpoint id it cannot resolve, and should treat
the segment and document ones as what they are: the provenance a claim was
read from, usable as a filter key (every claim from one document shares a
`dst_id`) without a node to draw.

The same tolerance covers nodes. `nodes` holds the active ones, and `edges`
holds every live edge, including those whose endpoint is a node that is no
longer active: a retired claim's `tagged_with_topic`, `extracted_under_topic`
and `supports` edges stay live and point at an id that is not in `nodes`.
On the development graph on 2026-10-20 that was several hundred edges of
each of those types. An edge to an id that is neither in `nodes` nor one of
the endpoint kinds above is an edge to an inactive node, and a client draws
nothing for it.

### 5.2 Field tables

Every timestamp is an ISO 8601 string in UTC. Every `metadata` is a free
dict whose contents are not part of this contract (§10), with the one
exception named above, `timepoint_id` on a `timelink` edge.

**NodeView**

| Field | Type | Meaning |
|---|---|---|
| `node_id` | string | |
| `node_type` | `"topic"`, `"fact"`, `"inference"` | `"unknown"` is reserved for a kind this version does not know |
| `content` | string | The claim, or the topic's name |
| `description` | string | A topic's prose about what it covers; empty on facts and inferences |
| `status` | string | `active`, `corrected`, `historical`, `merged`, `archived`, and the legacy `superseded` on graphs written before the supersession split |
| `source_id` | string or null | The segment a claim was extracted from; null on topics created from tags |
| `extraction_method` | string | How the node was produced; `"unspecified"` when nobody said |
| `confidence` | number or null | Null when nobody rated the node. Do not substitute a default |
| `retrieved_at` | timestamp or null | When a search last returned it; null until one has |
| `created_at` | timestamp | When the node was recorded. Every node has it |
| `graph` | string | |
| `metadata` | dict | |

A fact's `claim_kind` (whether it describes a state or an event) is a field
on the stored fact and is **not** in this view today. A client that needs it
cannot get it from the snapshot; see §10.

**EdgeView**

| Field | Type | Meaning |
|---|---|---|
| `edge_id` | string | |
| `src_id`, `dst_id` | string | See §5.1 for endpoints that are not nodes |
| `edge_type` | string | The vocabulary of §5.3 |
| `weight` | number | `1.0` unless set |
| `validity` | list of ValidityInterval | Empty on every type but `sourced_from` |
| `created_at` | timestamp | When the edge was recorded |
| `graph` | string | |
| `metadata` | dict | |

**ValidityInterval**, the core model sent as stored

| Field | Type | Meaning |
|---|---|---|
| `start`, `end` | ImpreciseInstant | Half-open, `[start, end)`. Each is an object discriminated by `instant_kind`: `precise` (`at`, optional `label`), `named` (`label`), `unknown`, `unbounded`. Unknown is the default for an endpoint nobody supplied |
| `timeline_id` | string or null | The clock; null is the wall-clock timeline |
| `witnessed_at` | ImpreciseInstant or null | A moment the source asserts the interval contains |
| `basis` | `"stated"` or `"inferred"` | Whether the dates were in the text or read from tense and context |

**TimelineView**

| Field | Type | Meaning |
|---|---|---|
| `timeline_id`, `name`, `description` | string | |
| `timepoints` | list of TimepointView | |
| `recurrences` | list of RecurrenceView | |
| `reference_time` | timestamp or null | The timeline's own "now"; null means the wall clock, resolved by the client at render time |
| `created_at`, `graph`, `metadata` | | |

**TimepointView**

| Field | Type | Meaning |
|---|---|---|
| `timepoint_id` | string | |
| `start`, `end` | timestamp or null | `start` absent on a vague point |
| `label` | string or null | |
| `kind` | `"instant"`, `"interval"`, `"vague"` | Derived from the dates each time |
| `earliest`, `latest` | timestamp or null | Where the stated order puts a vague point; absent on a dated point and on a contested one |
| `contested` | bool | The stated order cannot hold |
| `temporal_contradiction_id` | string or null | The dispute that says so |
| `metadata` | dict | |

**RecurrenceView**: `recurrence_id`, `label`, `rule_kind`, `bounds_start`,
`bounds_end`, `window_start`, `window_end` (timestamps or null),
`occurrences` (list of OccurrenceView), `truncated` (bool, the occurrence
cap fired).

**OccurrenceView**: `occurrence_start` (the identity, the start the rule
gave it), `start` (where it is now), `end`, `moved_to`, `materialised_id`
(the timepoint somebody turned it into, when there is one).

**BoundaryProposalView**: `node_id`, `source_id`, `endpoint` (`"start"` or
`"end"`), `at` (timestamp), `timeline_id` (or null), `because_id`,
`because_source_id` (the claim the date was read from and the source that
dated it), `graph`.

**MetacontextView**: `metacontext_id`, `content`, `description`, `graph`.

**RelationLabelView**: `relation_label_id`, `name`, `kind`, `description`,
`graph`.

### 5.3 The edge vocabulary

`edge_type` is an open vocabulary. The engine's own types are an enum in
`epimemer/core/types.py`; user-defined relations all travel as
`edge_type: "related"` with the descriptor in the edge's label and the
graph's definitions in `relation_labels` (the MCP tools `list_relations`
and `describe_relation` manage them). A client should treat an unknown
`edge_type` as a line it can draw and not name.

Counted on the development graph on 2026-10-18, and grouped the way a client
should treat them:

**Membership**, where one end files the other under something. These say
where a node belongs and are better turned into position, colour or a
filter than drawn as lines.

| `edge_type` | Joins | Count |
|---|---|---|
| `tagged_with_topic` | any node → topic (a retrieval index, not evidence) | 7,024 |
| `has_metacontext` | any node → metacontext | 2,153 |
| `sourced_from` | any node → document; the only type that carries `validity` | 2,096 |
| `contains` | segment → fact | 1,313 |
| `extracted_under_topic` | fact → topic, the topic of the segment it came from | 904 |
| `implies` | segment → inference | 491 |
| `abstracts` | inference → topic | 381 |
| `about` | segment → topic | 292 |
| `subtopic_of` | topic → topic, child to parent; the topic hierarchy | 0 on this graph |

**Claim to claim**, recorded at a moment, with no validity of their own.

| `edge_type` | Joins | Count |
|---|---|---|
| `supports` | fact → inference, evidential | 1,486 |
| `timelink` | any node → timeline, timepoint in `metadata.timepoint_id` | 475 |
| `assessed` | like nodes, a review verdict that two claims differ | 77 |
| `related` | anything, the open vocabulary | 5 |
| `similarity` | like nodes, symmetric | 2 |
| `derived_from`, `contradiction`, `variant_of`, `supersession_candidate` | see the enum | 0 on this graph |

**Succession and merge**, where one node gave way to another. These are the
edges that make a node's history legible and are the ones a client should
always draw.

| `edge_type` | Joins | Count |
|---|---|---|
| `temporally_followed_by` | like nodes; a world-change, the claim was right and the world moved on; not gapless, cycles legal, never deduplicate by endpoints | 77 |
| `merged_into` | like nodes; the retired source points at the survivor | 38 |
| `evidence_superseded` | fact → inference; the retired fact points at what depended on it | 37 |
| `evidence_merged` | fact → inference; the merged fact points at what depended on it | 31 |
| `superseded_by` | like nodes; a correction | 9 |

Only 41 of the 16,891 edges carried a validity interval, all of them
`sourced_from`.

## 6. The embeddings

`GET /api/embeddings?session=<id>&graph=<name>&node_type=<kind>` returns the
stored embedding vectors of a graph's **active** nodes, so a client can lay
nodes out by meaning without an embedder of its own. `node_type` is optional
and one of `topic`, `fact`, `inference`; without it every node type is
returned, and any other value is refused rather than answered with an empty
list.

```
{
  "schema_version": 1,
  "graph": "<name>",
  "node_type": "topic" | null,
  "models": {"<model_id>": <dimension>},
  "embeddings": [
    {"node_id": "...", "node_type": "topic", "model_id": "...", "vector": [floats]}
  ]
}
```

A node with no embedding is simply absent. A node embedded under two models
appears twice, once per `model_id`, and `models` lists every model present
with its vector length; vectors from different models are never comparable.
Entries come in the snapshot's node order, then by model, so a client can
join them to `nodes` by `node_id` with nothing to reconcile. Embeddings
exist for nodes only, never for segments (§10). The 415 topic nodes of the
development graph come to a few hundred kilobytes under a 384-dimensional
model; asking for every node is a multi-megabyte answer.

**The answer has a ceiling.** A session's answer to the hub travels in one
WebSocket message, and the hub accepts at most 16 MiB in one message. An
answer that would pass it is replaced by an error naming
the method, the size and the remedy, which the HTTP caller receives as the
`502` of §4; the session stays connected. Under a 384-dimensional model the
ceiling falls at roughly 1,900 vectors, under a 768-dimensional one at
roughly 950, so ask by `node_type` on a large graph. The snapshot has the
same ceiling and reaches it on a far larger graph. A denser encoding of the
vectors (float32 bytes rather than JSON numbers, about a quarter of the size)
would be a change of meaning for `vector` and so a new schema version; it is
the known next step if topics alone come to outgrow the ceiling.

A hub older than this route answers `404` from Starlette's router rather
than the `{"error"}` body of §4.

## 7. The client event stream

### 7.1 Connecting and subscribing

A client opens `WS /ws`. The hub sends nothing until the client subscribes:

```
{"subscribe": {"session": "<session_id>", "graphs": ["<name>", ...]}}
```

`graphs` is optional; without it, or with anything other than a list, the
client receives that session's events for every graph. A client may send a
new `subscribe` at any time to change session or graphs. Any other text the
client sends is ignored.

On subscribe the hub **replays** that session's two rings, oldest first:
the actions ring (`graph_action_recorded` and `advisory_raised`, the most
recent 512 together, in arrival order) and then the retrievals ring
(`retrieval_recorded`, the most recent 20). Replay is filtered by the same
session-and-graph test the live stream uses. Replay does **not** include
`node_stored`, `edge_stored` or any other event: a client builds its picture
of the graph from the snapshot and then applies live events to it. The rings
belong to the session and survive a client reload and a hub blip, but a
restarted MCP server is a new session with empty rings.

### 7.2 Every message

Every message the hub sends on `/ws` is one JSON object with a `seq`
integer added. `seq` is **per connection**: it starts at 1 with the first
message on this socket and increases by one per message of any kind, system
messages and replay included. It says nothing about position in any
session's stream and resets when the client reconnects. The dashboard treats
a gap as a reason to refetch the snapshot; so should any client.

### 7.3 System messages

Sent to **every** connected client, subscribed or not:

| `type` | Body | When |
|---|---|---|
| `session_connected` | `{"session": <the /api/sessions entry>}` | A session registered, reconnected, or re-registered because its active graph changed |
| `session_disconnected` | `{"session_id"}` | The session's socket closed; it stays listed for 300 seconds |
| `session_dropped` | `{"session_id"}` | The grace period ended without a reconnect and the session is gone |

A `session_connected` for a `session_id` the client already knows means the
session's socket to the hub was re-established, or its active graph
changed. Events published while the session was disconnected from the hub
were dropped (§7.5), so a client should refetch the snapshot on it.

### 7.4 Events

An event is the serialised `Event` model from `epimemer/visualization/events.py`
with two fields added: `session_id`, stamped by the hub, and `seq`. Every
event carries:

| Field | Type | Meaning |
|---|---|---|
| `event_type` | string | Discriminator, below |
| `category` | `"graph"` or `"pipeline"` | |
| `timestamp` | timestamp | When the producer created it |
| `graph` | string | The graph it is about; empty on a few system events |
| `session_id` | string | Added by the hub |
| `seq` | integer | Added by the hub, §7.2 |

The graph events:

| `event_type` | Fields beyond the common ones | Meaning |
|---|---|---|
| `node_stored` | `node: NodeView` | A node was created or updated; the same shape as the snapshot's |
| `node_status_changed` | `node_id`, `old_status`, `new_status`, `counterpart` (string or null) | A node left or re-entered the active set; `counterpart` is the node that replaced, followed or absorbed it, null for archival |
| `edge_stored` | `edge: EdgeView` | An edge was created |
| `timeline_stored` | `timeline: TimelineView` | A timeline was created or changed; the whole timeline every time, since adding a timepoint re-stores it |
| `embedding_stored` | `item_id`, `model_id`, `dimensions` | The association only, never the vector |
| `document_stored` | `document_id`, `content_preview`, `metadata` | |
| `segment_stored` | `segment_id`, `source_id`, `text_preview`, `span_start`, `span_end` | |
| `graph_action_recorded` | `action_id`, `verb`, `subjects` (ids), `counts` (dict of int), `summary`, `judged_by` (or null) | One human-meaningful act at its transaction boundary; `verb` is one of `stored`, `corrected`, `world_changed`, `merged`, `archived`, `restored`, `undetermined`, or a timeline decision kind. `action_id` is monotonic per session and is the position a log should use, since `seq` is not one |
| `advisory_raised` | `action_id`, `tool`, `kind`, `message`, `subjects`, `detail`, `action`, `surfaced`, `notify_user`, `judged_by` | A warning a tool computed, published whether or not the agent was shown it |
| `retrieval_recorded` | `record` (dict) | What a tool handed the agent; structural only on a non-loopback bind (§2) |
| `graph_switched` | `previous_graph`, `new_graph` | The session's active graph changed; a `session_connected` with the new `active_graph` follows |
| `reflect_counter_updated` | `count`, `threshold`, `suggested` | |

The pipeline events, which describe the Petri nets the session runs
(segment, store, reflect and the rest) and are what the dashboard's
pipeline strip draws:

| `event_type` | Fields beyond the common ones |
|---|---|
| `pipeline_started` | `pipeline_name`, `place_names`, `transition_names`, `edges` (list of `{source, target, label}`), the whole topology |
| `transition_fired` | `pipeline_name`, `transition_name`, `input_places`; sent as the transition goes in flight |
| `transition_completed` | `pipeline_name`, `transition_name`, `output_places`, `duration_ms` |
| `tokens_updated` | `pipeline_name`, `place_token_counts` (dict of int) |
| `pipeline_completed` | `pipeline_name`, `transitions_fired`, `duration_ms` |
| `pipeline_failed` | `pipeline_name`, `error`, `transition_name` (or null), `transitions_fired`, `duration_ms` |

### 7.5 Ordering and loss

Within one session, events arrive in the order the session published them:
one queue in the session, one socket to the hub, one fan-out loop in the
hub. Across sessions nothing is promised; two sessions' events interleave
however their sockets deliver them.

Events can be lost in two places, and both are by design. A session that
cannot reach the hub drops every event until it reconnects (it retries
with capped backoff and never blocks the MCP server), and a session's
outbound queue holds 1,000 events and drops new ones when full. A client
recovers by refetching the snapshot: on a `seq` gap, on a
`session_connected` for a session it already follows, and on its own
reconnect.

## 8. The session side, briefly

A client never speaks `/ingest`, but the shapes explain what the hub knows.
A session connects, sends `{"type": "register", "info": SessionInfo,
"schema_version"}` first, then `{"type": "event", "payload": <event>}` for
each event, and answers `{"type": "rpc_request", "request_id", "method",
"params"}` with `{"type": "rpc_response", "request_id", "result" | "error"}`.
The methods are `list_graphs`, `snapshot` (`{"graph"}`), `embeddings`
(`{"graph", "node_type"}`), `retrievals` and `warnings`; each HTTP route of
§4 that names a session is one of these forwarded. A session re-sends
`register` when its active graph changes. The models are in
`epimemer/visualization/protocol.py`.

## 9. Conformance

The Pydantic models are the source of truth: the view models and events in
`epimemer/visualization/events.py`, the session messages in
`epimemer/visualization/protocol.py`, and `ValidityInterval` in
`epimemer/core/temporal.py`. A client in another language generates its
types from `model_json_schema()` on those classes, or writes them by hand
and checks them against snapshots and events a real session produced. No
schema file ships with the package, and none is promised; the version
number of §3 is the promise.

## 10. What is not promised

- **As-of snapshots.** The snapshot is the graph now. The MCP tool
  `graph_as_of` answers questions about the past, and nothing on the hub
  does yet.
- **Layout.** No route returns positions. A client lays the graph out
  itself; the embeddings of §6 are the help it gets.
- **Embeddings for segments.** Only nodes are embedded, deliberately
  (the reasons are on `EmbeddingRecord` in `epimemer/core/types.py`).
- **`claim_kind` on the wire.** A fact's claim kind is stored and not sent
  (§5.2). Adding it to `NodeView` would be an added field and no version
  bump.
- **The contents of any `metadata` dict**, on nodes, edges, timelines or
  timepoints, except `timepoint_id` on a `timelink` edge.
- **The retrieval record's shape**, which belongs to the MCP layer and is
  carried opaquely here.
- **Cross-session ordering**, and delivery of events a session could not
  send (§7.5).
