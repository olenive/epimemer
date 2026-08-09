# Timeline visualisation — plan of action

A timeline panel in the dashboard, showing events as marks on an axis, with
hover detail, filtering, and zoom.

Status: **built**, including §7 (extraction proposing timepoints), which landed
last with two departures from the design sketched there — both argued in §7.2.

The panel was built horizontal (§§2–11) and then turned vertical (§12).
Sections 2–11 are kept as the record of the first build and of the parts that
did not change — the scale model (§5.1), the break heuristic (§5.2), the
filters (§5.5) and the backend work (§6) are all still exactly as described.
**§12 supersedes §4 (layout), §5.3 (zoom gestures) and §5.4 (vague
timepoints)**, and each of those points at what replaced it.

This document is the design record. Where the build diverged from the plan, the
plan has been corrected to describe what exists — §6.2 (one event, not two),
§8 (a fourth module), and §3.1 (what record mode can actually see) are the
places that moved, and each says why.

---

## 1. Decisions taken

| Question | Decision |
|---|---|
| Which time axis? | **Both**, as two modes of one panel: *content time* and *record time* (§3). |
| Panel placement | **Separate panel**, axis running left to right. Not folded into the graph panel. → **superseded by §12**: vertical axis in a split pane. |
| Vague timepoints | Positioned **below** the axis in a dedicated lane, in a defined order (§5.4). No fake coordinate on the axis. → **§12.6** keeps the rule, changes the place. |
| Large gaps | **Break the axis** where a gap is far above the local spacing, collapsing it into a marked break (§5.2). Unchanged by §12. |
| Zoom | Per-timeline zoom and pan, recomputing breaks from the visible domain (§5.3). → **§12.4** rebinds the gestures. |
| Timepoint population | Extraction should **propose** timepoints, not only manual curation — but as a later phase (§7). |
| Filters | Linked node type, node status, metacontext, date range, **plus free-text field filters** (§5.5). Unchanged by §12. |

Decisions added by the vertical redesign (§12), listed here so this table stays
the single index of what was settled:

| Question | Decision |
|---|---|
| Axis orientation | **Vertical**, past at the top and future at the bottom — chat-log convention (§12.1). |
| How many timelines at once? | **One**, chosen from a selector. Comparing timelines is a different feature (§12.2). |
| What does left/right mean? | Facts and topics **left**, inferences **right**, mixed marks **straddle** the axis (§12.3). |
| Where is "now"? | A per-timeline `reference_time` stored **in the graph**, not the browser (§12.5, §6.4). |
| Wheel gesture | Wheel **pans**; ⌘/ctrl-wheel zooms (§12.4). |
| Panel placement | **Split pane** with a draggable vertical divider; either panel can take the full width (§12.7). |

## 2. The starting point

What existed when this plan was written, kept because it explains the phasing
below. The frontend section is no longer true — §§3–11 were built — and §6.4
records the one backend field added since.

**Backend — the domain model is complete and unused by the frontend.**

- `Timepoint` (`epimemer/core/types.py:285`) — optional `start`/`end`, free-text
  `label`, `metadata`. A point may be concrete, an interval, vague (label only),
  or a concrete start with a descriptive label.
- `Timeline` (`epimemer/core/types.py:298`) — named, described, with timepoints
  embedded as an ordered list rather than as separate graph nodes.
- `EdgeType.TIMELINK` (`epimemer/core/types.py:62`) — node → timeline, carrying
  `timepoint_id` in edge metadata. Written in exactly one place,
  `epimemer/mcp/tools.py:1841`.
- Storage: `store_timeline` / `get_timeline` / `query_timelines`
  (`epimemer/storage/protocol.py:402`).
- Pure functions: `add_timepoint`, `find_nearest`, `get_in_range`,
  `reorder_timepoints`, `_split_concrete_vague`
  (`epimemer/pipelines/timeline/functions.py`).
- MCP tools: `create_timeline`, `add_timepoint`, `query_timeline`,
  `create_timelink`. (`set_reference_time` was added later — §6.4.)

**Frontend — no timeline anything.**

- Panels are the cytoscape knowledge graph (`graph-panel.ts`), the pipeline
  strip and detail overlay, a detail drawer, and the reflect badge.
- `assemble_snapshot` (`epimemer/visualization/snapshot.py:17`) returns
  `{graph, nodes, edges}` only. No timelines.
- No timeline event types on the bus (`epimemer/visualization/events.py`), so
  creating a timeline emits nothing and a panel would silently go stale.

**Consequence that shapes the phasing:** nothing in ingestion creates a
timeline, so a content-time panel would be empty for most graphs on day one.

## 3. Two modes, one panel

The panel plots marks on a horizontal axis. What the axis *means* is the mode.

### 3.1 Record time — "when did the graph learn this?"

Derived entirely from fields already present on `NodeView`: `created_at` and
`retrieved_at`. A node is drawn as an interval from its creation to its last
retrieval — the span over which it stayed in use — collapsing to a point when no
search has ever returned it (`retrieved_at` is null).

**This mode needs no backend change at all.** The snapshot the frontend already
fetches contains everything required. That makes it the right thing to build
first: it exercises the whole rendering design — scales, breaks, zoom, hover,
filters — against data that always exists.

**What it cannot show, and why.** `viz_list_nodes` returns active nodes, so the
snapshot has no retired ones and record mode plots no retirements. Widening the
snapshot would change what the *graph* panel draws too, which is a separate
decision from this one. Status changes that arrive live during a session are
applied, so the status filter is not inert — but a node retired before the
browser connected is simply absent.

It is also the ideal case for a broken axis. Session activity is bursty: a graph
sits untouched for days, then takes 200 nodes in three minutes. On a continuous
axis that is one vertical smear.

Rows: one per node type (topic / fact / inference).

### 3.2 Content time — "when did this happen?"

The `Timeline` / `Timepoint` model. Rows: one per timeline. Marks: timepoints,
drawn as points when `end is None` and as bars when it is set.

Needs the read path and events in §6.

Mode is a toggle in the panel header. Zoom, filter, and selection state are kept
per mode — switching back should restore what you were looking at.

## 4. Layout

```
┌─ TIMELINE ────────────── [content ▾] [filter…] [⌕ text] [reset zoom] ─┐
│                                                                       │
│  History of AI    1950 ──●───●──╱╱──────●─●─●──────────────── 2026     │
│                          a   b   ~40y   c d e                         │
│                                                                       │
│  Renaissance      1400 ──●────●────●─────────────────── 1520          │
│                          f    g    h                                  │
│                                                                       │
│  ┄┄ undated ┄┄  [during the Renaissance] [before the war] [later]     │
└───────────────────────────────────────────────────────────────────────┘
```

Each row owns its domain and its own zoom. There is no shared global axis —
one graph can hold 1400 AD and last Tuesday, and a common domain across those
is unreadable at any zoom level.

Rendered as SVG with Tailwind classes, in a functional TS module. **No charting
library.** A piecewise-linear scale is a few lines; `d3` would be the largest
thing in the bundle for what amounts to `x = f(t)`.

## 5. Design detail

### 5.1 Scale model

Each row's scale is **piecewise linear**: a list of segments, each with a time
domain and a pixel range, separated by fixed-width breaks.

```
segments: {t0, t1, x0, x1}[]     breaks: {after: segmentIndex, span: Duration}[]
```

Available pixel width is `W - breaks.length * BREAK_PX`. That is divided among
segments in proportion to each segment's own span, so dense regions are not
compressed by the sparse ones. With zero breaks this degenerates to an ordinary
linear scale, which is what the common case should be.

Both directions are needed — `timeToX` for rendering, `xToTime` for pointer
hit-testing and for zoom anchoring.

### 5.2 Break heuristic

The heuristic decides where the axis is cut. Inputs: the sorted concrete
timepoints of one row, and the current visible domain.

1. Compute the gap between consecutive marks as `start[i+1] - (end[i] ?? start[i])`.
   Intervals therefore do not manufacture a gap that isn't there.
2. Take `m` = median of the non-zero gaps. If every gap is zero or there are
   fewer than three marks, do not break — there is no local spacing for a gap to
   be anomalous against.
3. A gap is a **candidate** if it satisfies both of:
   - `gap > GAP_FACTOR * m` — it is anomalous relative to local spacing.
     `GAP_FACTOR = 10` initially.
   - `gap / visibleSpan > GAP_MIN_FRACTION` — it would actually waste screen.
     `GAP_MIN_FRACTION = 0.12`. Without this, breaking a gap that costs 8px
     gains nothing and only adds visual noise.
4. Take at most `MAX_BREAKS = 5` candidates, largest first. Beyond a handful of
   breaks the axis stops being an axis.
5. Render each break as a fixed-width (~24px) hatched marker labelled with the
   elapsed span in human units ("~600 years", "3 days").

**Recompute on zoom, from the visible domain only.** Zooming into a cluster
should dissolve the break that was hiding the space around it.

**Hysteresis, to stop flicker during a zoom drag.** Once a break exists it
persists until its gap falls below `GAP_FACTOR * m * 0.7`. A break appearing and
vanishing every few pixels of scroll is worse than either state.

All of this is pure: `(marks, visibleDomain, width) → {segments, breaks}`. It
lives in its own module with no DOM access, so it is unit-testable directly.

### 5.3 Zoom

- Scroll wheel over a row zooms that row, anchored at the pointer's time value
  so the mark under the cursor stays put.
- Drag pans. Shift-drag selects a range to zoom into.
- Zoom clamps to the row's full data extent, plus a small margin.
- Per-row "reset", plus a panel-level "reset all".
- A modifier (⌥) applies the zoom to every row at once, for the case where rows
  genuinely share a scale and you want to compare them.
- Zoom state is `{t0, t1}` per row id, not a scale factor — it survives the data
  changing underneath it.

### 5.4 Vague timepoints

Timepoints with no `start` cannot be placed on a metric axis, and placing them
anywhere on it would assert something false. They go in a lane below the axis,
rendered as labelled chips.

Order: **authored order within the timeline**, which is exactly what
`reorder_timepoints` already establishes — concrete points sorted by start,
vague ones appended in their original sequence. Reusing that convention means
the panel and the backend agree on what "the order of a timeline" means, rather
than the frontend inventing a second answer.

The chips are interactive on the same terms as marks: hover for detail, click to
select, and they obey the same filters.

### 5.5 Filtering

All filters are client-side predicates over the in-memory snapshot; no round
trip. Composed with AND.

- **Linked node type** — topic / fact / inference. Mirrors the existing
  `graph-filter` select in `index.html:50`.
- **Node status** — hide marks whose only linked nodes are superseded or merged.
- **Metacontext** — keeps fictional and factual events visually separate, which
  CLAUDE.md already treats as a hard rule rather than a preference.
- **Date range** — numeric or brush, restricting *visible marks* independently of
  zoom. Range and zoom are different things: one hides data, the other magnifies.
- **Free text** — a query box matching against a flattened searchable string per
  mark: timepoint label, linked node content, source name, metacontext name, and
  stringified metadata values.

  Supports `field:value` prefixes so origin-style queries are expressible —
  `source:BBC`, `mc:fiction`, `type:fact`, `label:war` — with a bare term
  matching any field. Multiple terms AND together; quoted terms match a phrase.
  Parsing the query is pure and belongs in its own tested module.

A mark is retained if **any** of its linked nodes passes the node-level filters,
so a timepoint linked to one live and one retired fact stays visible under
"active only".

### 5.6 Interaction

- **Hover** — reuse the existing detail drawer rather than inventing a tooltip.
  Shows the timepoint's label, resolved dates, and its linked nodes.
- **Click** — selects, and emits through the existing event router so the graph
  panel can highlight the linked nodes. Timepoints have no node id in the graph
  (they are embedded in the timeline, not nodes), so the bridge is the
  `TIMELINK` edge's `timepoint_id` metadata → linked node ids.
- **Esc** clears selection, consistent with the pipeline detail overlay.

## 6. Backend work (content-time mode only)

### 6.1 Read path

Add to the viz/admin read section of `StorageBackend`
(`epimemer/storage/protocol.py:500`):

```python
async def viz_list_timelines(self, database: str) -> Sequence[Timeline]: ...
```

`query_timelines()` cannot be reused: it has no database argument and so reads
the session's *active* graph, while the hub must read any graph of a session
without switching the connection.

Implemented on **both** backends — `InMemoryStorage` and `SurrealDBStorage` — as
a real method, not a capability flag or a `hasattr` probe.

Note the standing rule in that section: `viz_*` methods must never be registered
as MCP tools or imported under `epimemer/mcp/`.

Then:

- `assemble_snapshot` gains a `timelines` key.
- `SnapshotResponse` in `api.ts` and a `TimelineView` / `TimepointView` in
  `types.ts` gain the matching shape.

Timepoints ship embedded in their timeline, as they are stored — no flattening.

### 6.2 Events

Without these the panel is correct only at load.

The plan called for two events, `TimelineCreated` and `TimepointAdded`. The
build has **one**, `TimelineStored`, carrying the whole `TimelineView`.
`store_timeline` is an upsert and the only write path — adding a timepoint
re-stores the timeline — so telling creation from extension would require a read
before every write to learn something the viewer can see for itself. Timelines
are small; sending the current state is cheaper and cannot drift from storage.
The receiver replaces its copy rather than merging.

Published from `instrumented_storage.store_timeline`, mirrored in the frontend
`GraphEvent` union in `types.ts`.

`TIMELINK` edge creation already emits `EdgeStored`, so link changes are covered.

### 6.3 Metacontexts in the snapshot

Not in the original plan, and required by the frame filter: `has_metacontext`
edges carry only ids, so without the metacontexts themselves the filter could
offer nothing but a list of UUIDs. `viz_list_metacontexts(database)` mirrors
`viz_list_timelines` exactly, and `assemble_snapshot` gained a `metacontexts`
key. Where a frame is still unresolvable the panel falls back to the raw id
rather than dropping the association, so filtering stays correct even when the
label is poor.

### 6.4 `reference_time` — a timeline's own "now" (built)

Added for the vertical redesign (§12.5) and useful on its own. `Timeline` gained
`reference_time: datetime | None`, and it round-trips on both backends with no
adapter change — the field travels inside the record the way every other
`Timeline` field does.

**`None` means "follow the wall clock", and is deliberately not the same as
storing the current instant at creation.** A real-world timeline written with
today's date would have its present frozen at the moment it was first saved,
drifting further out of date every day it was used. Unset resolves to `now` at
read time instead.

**It lives in the graph, not in the browser.** A fictional timeline's present
moment is a fact about that world ("the novel opens in May 1897"), so an agent
that reads the source should be able to record it, every client should see the
same answer, and it should survive a new machine. `localStorage` would have
satisfied the renderer and none of that.

Reachable three ways:

- `create_timeline(name, reference_time=…)` — when it is known up front.
- `set_reference_time(timeline_id, reference_time=…)` — separate because a
  fiction's anchor is usually learned after ingesting enough of the source to
  say, and is often read wrong first. Passing nothing **clears** it.
- `query_timeline` reports it on every call, so a caller reading timepoints can
  tell past from future without a second round trip.

`TimelineView` carries it to the frontend as `reference_time: string | null`,
resolved to real `now` at render rather than substituted on arrival — otherwise
a long-lived browser session would pin the present to whenever the snapshot was
assembled.

## 7. Extraction proposing timepoints (built 2026-08-09)

Deliberately last. It is a backend change of a different character to the rest,
and the panel must exist first to make its output inspectable.

During decomposition, temporal expressions in node content become `Timepoint`s:
a resolved `start`/`end` where the expression is concrete, `label` only where it
is not — "during the Renaissance" resolves to nothing and stays vague rather
than being guessed into 1500-01-01. Each proposing node gets a `TIMELINK` to the
timepoint, which is what puts a mark on the axis.

`detect_temporal_expressions` (`pipelines/timeline/temporal.py`) is a pure
function over text, and `propose_timepoints` (`pipelines/timeline/functions.py`)
turns its output into a timeline and edges. Neither touches storage, so both are
tested without a backend.

### 7.1 The batch question, settled

`write_batch_tx` took `nodes`, `edges` and `embeddings` only. Ingestion is
atomic *because* everything goes through it, so a timeline written outside the
batch meant a mid-document failure could leave `TIMELINK` edges pointing at a
timeline that was never stored — and the read path resolves a dangling
`TIMELINK` to an empty row rather than an error, so that failure is silent.

It now takes `timelines`, and they are the one **upsert** in an otherwise
insert-only batch. Not a compromise: a timeline is a single record holding a
list of timepoints, so appending a timepoint *is* a replacement of that record.
There is no insert-shaped way to say it. The consequence is in the rollback
path — undoing an upsert means restoring the row's previous content, not
deleting the row — which the in-memory backend now does explicitly and
SurrealDB gets from its transaction.

Testing that is asymmetric, and the parity suite alone would have been
misleading. SurrealDB builds every statement before running any of them, so a
failure injected from Python aborts before the transaction opens: the parity
test proves the observable ("the old timeline is intact") on both backends but
only exercises the in-memory restore. `test_write_batch_tx_rolls_back_a_timeline_upsert`
in `test_surrealdb_storage.py` collides *inside* the transaction, after the
upsert in statement order, which is what actually proves the database rolls it
back.

### 7.2 Two departures from the sketch above

**Detection reads node content, not segment text.** A mark needs a node to hang
on. A date found in segment text leaves "which of this segment's nodes is this
about?" unanswerable, and any answer is a guess; a date found in a fact's own
content belongs to that fact. The cost is that a date stated in a segment but
dropped by the agent's decomposition is not proposed — which is correct, since
nothing in the graph claims it.

**One shared timeline per graph, not one per document.** The sketch said
per-document. §12.2 then decided the panel shows one timeline at a time, and the
two do not fit: a timeline per document turns every ingest into another
near-empty entry in the selector, and the marks that would make a chronology
legible are spread across all of them. Provenance is not lost by sharing — every
node keeps its `sourced_from` edge — and `timeline_id` still routes a document
onto a curated timeline when the agent has one. That named timeline must already
exist; creating one silently would put the document somewhere the caller cannot
find, under a name they never chose.

### 7.3 What the detector will not do

The asymmetry is the whole design: *a missed expression costs a mark on the
timeline; an invented one is indistinguishable from evidence once stored.* So it
resolves only what the text states — a day, a month, a year, a decade, a century
are all intervals, differing in width but not in kind — and everything else that
reads as temporal comes back as a label with no dates.

Concretely out of scope, and left that way on purpose:

- **Relative expressions** ("three years later", "the following spring"). They
  need a document-level anchor this function does not have.
  `Timeline.reference_time` (§6.4) is where one would come from; resolving
  against it is separate work.
- **`of` as a temporal framing.** "The winter of 1897" would be worth having,
  but "a group of 1500 people" is the same shape and the cost is not symmetric.
- **Clock times**, and anything needing the reader's present.

Two guards were worth more than they look. A bare four-digit number is a year
only when a preposition frames it, or a date pattern surrounds it — otherwise
`3000 troops` and `error code 1997` become dates. And the preposition needs a
word boundary in front of it: without one, "versi*on* 2024" reads as "on 2024"
and ships a date. Running the detector over ordinary prose is what found that,
along with the mirror-image bug: the guard against `1897.5` was rejecting every
year followed by a full stop, which is exactly where years sit.

## 8. Modules

**Frontend** (`epimemer/visualization/frontend/src/`):

| File | Contents | DOM? |
|---|---|---|
| `timeline-scale.ts` | gap heuristic, piecewise scale, `timeToX`/`xToTime`, zoom domain maths | no |
| `timeline-filter.ts` | query parsing, mark predicates | no |
| `timeline-model.ts` | snapshot → rows of marks, for both modes; facet gathering | no |
| `timeline-panel.ts` | SVG rendering, pointer handling, wiring to the event router | yes |

`timeline-model.ts` was not in the original plan. It exists because deciding
*which marks there are* — resolving `TIMELINK` edges to nodes, naming frames,
splitting dated from undated — turned out to be as error-prone as the geometry
and just as testable without a DOM. Folding it into the panel would have hidden
it behind rendering.

The pure/impure split is the point: the parts most likely to be wrong (break
placement, zoom anchoring, query parsing, mark linkage) need no browser to test.
The panel itself is covered separately under jsdom.

**Backend**: `protocol.py`, `memory.py`, `surrealdb_adapter.py`,
`instrumented_storage.py`, `snapshot.py`, `events.py`.

## 9. Order of work

1. ✅ **Record-time panel** — scale module, break heuristic, zoom, hover,
   node-type and status filters.
2. ✅ **Free-text, metacontext and date-range filters** — pure modules.
3. ✅ **Content-time read path** — `viz_list_timelines` on both backends,
   `viz_list_metacontexts` alongside it (§6.3), snapshot fields, frontend types.
4. ✅ **Timeline events** — `TimelineStored` through `instrumented_storage`;
   live updates without a refresh.
5. ✅ **Graph-panel cross-highlighting** — `highlightNodes` on the graph panel
   handle, driven by the selected mark's linked node ids.
6. ✅ **Extraction proposes timepoints** — `write_batch_tx` carries timelines,
   a pure detector over node content, one shared timeline per graph (§7).

## 10. Testing

Following existing project conventions:

- **Failing test first**, then the scoped fix.
- Storage additions go through the parameterised `storage` fixture in
  `tests/conftest.py`, so `viz_list_timelines` is verified identically on
  `InMemoryStorage` and `SurrealDBStorage(url="mem://")`. Backend-specific
  internals go in the per-backend files.
- Frontend pure modules under vitest. Cases worth pinning explicitly:
  - a gap that is large in absolute terms but not relative to spacing → no break
  - a gap that is relatively large but costs few pixels → no break
  - more than `MAX_BREAKS` candidates → the largest five, in order
  - zoom anchored at the pointer keeps the anchored time fixed
  - hysteresis: a break already present survives a small zoom-out
  - vague points never receive an x coordinate
  - `field:value` parsing, including quoted phrases and unknown field names
- **Mutation-test the heuristic.** It is exactly the shape of code where a
  behavioural test passes while the logic is wrong — an inverted comparison or a
  dropped clause still produces *an* axis. Verify that removing each condition
  in step 3 of §5.2 fails a test.

  The sweep run against the built module covered both thresholds, the break cap,
  hysteresis in both directions, the ordering, the interval-overlap rule, break
  pixel reservation, proportional segment widths, collapsed-time pinning, the
  zoom anchor, and every clamp — plus the filter module's facet semantics, query
  parsing, and each clause of the composed predicate. It found three real gaps
  the behavioural tests had missed, and two guards that were dead code because
  `clampToExtent` and the median arithmetic already enforced them; both were
  removed rather than left untestable.
- Full suite (`uv run python -m pytest tests/ -q`) plus `make test-frontend`;
  `make test-integration` when storage is touched.

## 11. Open risks

- **An empty panel in content mode** until §7 lands. Mitigated by shipping
  record mode first, and by an explicit empty state that says *why* it is empty
  and which tool creates a timeline.
- **Break heuristic constants are guesses.** `GAP_FACTOR`, `GAP_MIN_FRACTION`
  and `MAX_BREAKS` are named constants in one module precisely so they can be
  retuned against real data without hunting through render code.
- **Very large graphs.** Record mode plots every node. Past a few thousand marks
  the SVG needs either aggregation into density bins at low zoom or virtualised
  rendering of the visible domain only. Worth measuring before optimising —
  every performance guess in this project so far has been overturned by the
  profile.

---

## 12. Vertical redesign (designed 2026-08-07, built 2026-08-08)

The horizontal panel works, and it is starved of the one dimension that
matters. A mark's label competes with the axis for horizontal room, so marks
carry a truncated title and everything else lives in the hover drawer. Turning
the axis 90° trades a scarce dimension for an abundant one: time gets the
scroll direction, which is unbounded, and text gets the width, which is what it
needed.

**This supersedes §4, §5.3 and §5.4.** Everything else in §§2–11 stands — the
scale model (§5.1), the break heuristic (§5.2), the filters (§5.5), the read
path and events (§6) are all orientation-agnostic and unchanged.

```
┌─ GRAPH ─────────────────┬─│─ TIMELINE  [History of AI ▾] [⌕] [content ▾] ─┐
│                         │ │                                              │
│                         │ │   1950 ┬                                     │
│      (cytoscape)        │ │        │                                     │
│                         │ │  Dartmouth workshop ●                        │
│                         │ │  coins "AI"         │                        │
│                         │ │                     │                        │
│                         │ │                     ╪  ~40y                  │
│                         │ │                     │                        │
│                         │ │                     ●  the field had already │
│                         │ │                     │  split by then         │
│                         │ │                     │                        │
│                         │ │  AlphaGo beats  ▐███████▌  a decisive result │
│                         │ │  Lee Sedol          │      for deep learning │
│                         │ │ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┼ ─ ─ ─ ─ ─  now  ─ ─ ─ ─│
│                         │ │                     │                        │
│                         │ │   2030 ┴                                     │
│                         │ │  ┄ undated ┄  [during the boom] [later]      │
└─────────────────────────┴─│──────────────────────────────────────────────┘
        facts & topics ──────┘└────── inferences        ▐███▌ straddles both
```

Past at the top, future below. The dashed rule is the reference time (§12.5);
`╪` is a collapsed gap (§5.2); the block crossing the axis is a timepoint
holding both a fact and an inference (§12.3).

### 12.1 Direction: past at the top

Earlier time is higher on screen; scrolling down moves into the future. This is
the chat-log convention, and it reads with the page rather than against it.

It is also the cheaper of the two options. With past at the top, position along
the axis increases with time exactly as the scale already computes it, so the
renderer maps `y = pos` and **nothing inverts anywhere**. The scale stays
monotonic, and the break, zoom and pan logic keep both their behaviour and
their tests.

(The first sketch of this redesign put the future at the top. It needed a flip
at render time and gained nothing; the direction is recorded here because it is
the kind of decision that gets relitigated.)

### 12.2 One timeline at a time

The panel shows a single timeline, chosen from a selector in its header.

`buildContentRows` already returns one row per timeline, so the model keeps its
shape and feeds the selector; only the renderer narrows. Record mode's three
rows (topic / fact / inference) fold into §12.3's two sides and stop being rows
at all.

Comparing two timelines against each other is a real want and a different
feature. The per-row independent domains of §4 exist because one graph can hold
1400 AD and last Tuesday; with one timeline on screen that problem does not
arise, and the ⌥-apply-to-all-rows gesture (§5.3) has nothing left to mean.

### 12.3 Sides carry node type

Facts and topics sit **left** of the axis, inferences **right**. Both sides get
the full half-width for text, which is the point of the redesign.

The split is not arbitrary: facts and topics are what the graph was told,
inferences are what it worked out. Putting the derived layer on its own side
makes "how much of this timeline is inferred?" answerable at a glance.

**Mixed marks straddle the axis.** A content-mode mark is a *timepoint*, not a
node, and `nodesForTimepoint` can return several nodes of different types — the
existing `TimelineMark.nodeIds` is already a list. A timepoint holding both a
fact and an inference is drawn as one wider block crossing the axis, listing
its members. Splitting it into two marks was considered and rejected: one
timepoint is one thing in the data, and splitting would invent a second.

### 12.4 Gestures

| Gesture | Action |
|---|---|
| Wheel | Pan through time |
| ⌘/ctrl + wheel | Zoom, anchored at the pointer's time value |
| Drag | Pan |
| Shift-drag | Select a range to zoom into |
| ⌥ + anything | *(dropped — there is only one timeline)* |

Wheel-to-pan is what "scrolling" means once the axis is vertical, and it costs
the wheel-to-zoom binding of §5.3. ⌘-wheel for zoom is the map and
drawing-tool convention.

**The viewport stays virtual — no native scrollbar.** A tall SVG in an
`overflow-y` container would give free momentum, and it would break the model:
the break heuristic recomputes from the *visible domain* (§5.2) and zoom is a
domain transform (§5.3). Wheel events adjust the domain, exactly as the
horizontal panel's drag-pan does today.

### 12.5 Reference time

The backend half is built and described in §6.4. What the panel does with it:

1. **Initial position** — the view opens centred on the reference time, not at
   either end. A timeline holding future events has no meaningful edge to start
   at.
2. **A marker rule** — a labelled horizontal line across the axis, so past and
   future are readable without doing arithmetic.
3. **A "jump to now" control** — the chat-app affordance for getting back after
   scrolling away.

Two things that must not be forgotten:

- **The extent has to include it.** The domain is clamped to the data extent
  (`paddedExtent`, `panDomain(…, extent)`), so a reference time outside the data
  — an empty timeline, or one whose events are all in the past — cannot be
  centred on under today's clamp. Fold the reference time into the extent
  computation.
- **Record mode ignores it.** Record time is wall-clock (`created_at`,
  `retrieved_at`), so a fictional anchor is meaningless there; record mode
  always marks real `now`, and the control is hidden. A setting that appears in
  a mode where it cannot apply is worse than no setting.

### 12.6 The undated tray

§5.4's rule is unchanged — a timepoint with no `start` gets no coordinate, and
authored order is the order. What changes is the place.

"Below the axis" now means "later", so undated chips at the bottom would read
as far-future. They move into a visually separate tray — its own bordered,
labelled block outside the scrolling axis — rather than a lane positioned along
it.

### 12.7 Split pane

The panel becomes the right half of a split pane with a draggable vertical
divider, and either half can be collapsed so the graph or the timeline takes
the full width.

`split-pane.ts` did exactly this (88 lines: left/right panels, drag handle,
per-panel toggles, collapse-to-full-width) and was removed when the layout was
reworked. It was recovered from commit `c94e5b5` rather than rewritten, and
gained four things it needed: the split held as a *fraction* rather than pixels
(so a window resize keeps the proportion instead of stranding a panel), arrow-key
resizing on the focused divider, persistence in `localStorage`, and a refusal to
collapse the last visible half — which would leave an empty window with no way
back.

### 12.8 What each module cost

Estimated before the build, and recorded after. The estimate held except for
one thing it missed, noted below.

| Module | Change |
|---|---|
| `timeline-filter.ts` | **None**, as predicted. 180 lines, 31 tests, orientation-agnostic. |
| `timeline-scale.ts` | **Rename only**: `timeToX`/`xToTime` → `timeToPos`/`posToTime`, `x0`/`x1` → `p0`/`p1`. No logic change, so its 34 tests kept their meaning. |
| `timeline-model.ts` | `sideForTypes` derives left / right / straddle per mark — **and record mode collapsed from three rows to one** (see below). |
| `timeline-panel.ts` | The bulk, as expected: new renderer, new gestures, reference-time rule, undated tray, timeline selector. |
| `timeline-labels.ts` | **New**, pure, 27 tests. Its story is §12.10. |
| `split-pane.ts` | Restored from `c94e5b5` and given ratio state, keyboard resizing, persistence and 19 tests. |
| `index.html` / `main.ts` | Split-pane markup and wiring, replacing the toggled bottom strip. |

**What the estimate missed: record mode's rows had to go.** It had one row per
node type (Topics / Facts / Inferences). Once node type is carried by the
*side* a mark sits on, keeping the rows too would have meant choosing one type
at a time from the timeline selector — hiding two thirds of the graph to say
something the layout already says. `buildRecordRows` now returns a single row
holding every node. §12.2 said this in a sentence; it turned out to be a code
change, not a rendering detail.

### 12.9 Risks, and how they landed

- **Label layout was the unproven part** — correctly identified, and it is the
  one place the design actually changed. §12.10.
- **Truncate or wrap** was left open and settled as **truncate**: one line per
  label, budgeted from the half-width, with the full text in the hover detail.
  Wrapping would make a label's height depend on its content, which the layout
  would then have to solve against, and no graph seen so far needs it.
- **Dense clusters still defeat any layout**, as expected. What the build adds
  is that the panel now *says so*: labels that will not fit are counted and
  reported ("+7 labels hidden — zoom in") rather than quietly overlapping. The
  aggregation answer (a "12 marks" cluster that expands on zoom) remains
  unbuilt and is still its own design.

### 12.10 Expand on select

Clicking a mark expands its text **in place**: the label becomes a bordered
card of up to five wrapped lines carrying the timepoint's dates and content,
and the neighbouring *labels* slide out of the way to make room.

**The room is made in the label column, not on the axis.** The obvious reading
of "expand the timeline to fit the text" is to insert height at that point on
the axis — and that would break the one thing the axis is for. Position means
time, so pushing later marks down puts them where their timestamps do not, and
moves them relative to the reference-time rule, which is drawn at
`timeToPos(now)`. The panel would start asserting things that are false. (The
broken axis is not a counter-example: a break is *labelled* with the span it
removed, so it states a fact about the data rather than about UI state.)

Doing it in the label column costs almost nothing, because `timeline-labels`
was already built for it: a `LabelRequest` carries its own height, and the
layout's whole job is sliding labels apart without touching marks. The
expansion is a taller request. Two details make it work:

- **The card is passed to the layout first**, which makes it the highest
  priority — the caller's order is the priority order (§12.11), so the
  one label the reader deliberately asked for is never the one dropped.
- **A straddling mark emits two requests**: the immovable block on the axis,
  and a card in the left column with its own id. The block stays where its
  timestamp puts it; the leader line ties the card back to it.

`wrapText` lives in `timeline-labels.ts` rather than the panel because it is
the other half of the same question — the layout needs a label's height before
it can place it, and the height is however many lines the text wraps to.
Existing newlines are kept as hard breaks, since a timepoint's detail is
already structured as when / label / linked nodes.

The hover-to-drawer preview stays. Hover is for skimming, the card is for
reading, and the drawer holds text longer than five lines will ever show.

### 12.11 What building the label layout changed

`timeline-labels.ts` was written first and pure, as §12.9 advised, and the
advice paid for itself immediately.

Its first version let an oversized stack of labels **spill** out of the space
it had been given, on the reasoning that crowded text beats absent text. Every
hand-written test passed. A seeded randomized property check — 300 cases,
asserting only "no overlaps, no reordering" — failed on its first run: a stack
crammed between two straddling blocks had overflowed and landed on a label
sixty pixels away, belonging to a different part of the axis entirely.

So the contract changed. Labels that do not fit are **dropped and reported**,
never spilled:

- What is dropped is a *label*, not a mark. The tick is still drawn, and the
  count of suppressed labels is shown.
- **The caller's order is the priority order.** The module holds no opinion
  about which labels matter; the panel hands them over in axis order, and a
  caller that wants something else (`importance`, say) can sort first.

Two further things came out of verifying it. A mutation sweep (23 mutants, all
killed after four rounds of fixing the *tests*) found that the "shares the
displacement" test used identical anchors — so it could not tell a mean from a
first or a last — and that nothing forced a label into the second free range.
And a 5,000-case run showed the outer repeat-until-stable loop was dead: the
inner merge loop already converges. It was removed rather than kept as
untestable insurance.
