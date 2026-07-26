# Timeline visualisation — plan of action

A left-to-right timeline panel in the dashboard, showing events as marks on an
axis, with hover detail, filtering, and per-timeline zoom.

Status: **built**, except §7 (extraction proposing timepoints), which stays open
and is tracked in `ISSUES.md`.

This document is the design record. Where the build diverged from the plan, the
plan has been corrected to describe what exists — §6.2 (one event, not two),
§8 (a fourth module), and §3.1 (what record mode can actually see) are the
places that moved, and each says why.

---

## 1. Decisions taken

| Question | Decision |
|---|---|
| Which time axis? | **Both**, as two modes of one panel: *content time* and *record time* (§3). |
| Panel placement | **Separate panel**, axis running left to right. Not folded into the graph panel. |
| Vague timepoints | Positioned **below** the axis in a dedicated lane, in a defined order (§5.4). No fake coordinate on the axis. |
| Large gaps | **Break the axis** where a gap is far above the local spacing, collapsing it into a marked break (§5.2). |
| Zoom | Per-timeline zoom and pan, recomputing breaks from the visible domain (§5.3). |
| Timepoint population | Extraction should **propose** timepoints, not only manual curation — but as a later phase (§7). |
| Filters | Linked node type, node status, metacontext, date range, **plus free-text field filters** (§5.5). |

## 2. What exists today

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
  `create_timelink`.

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
`last_reinforced`. A node is drawn as an interval from its creation to its last
reinforcement — the span over which it stayed relevant — collapsing to a point
when it was never reinforced.

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

## 7. Extraction proposing timepoints

Deliberately last. It is a backend change of a different character to the rest,
and the panel must exist first to make its output inspectable.

Sketch: during decomposition, detect temporal expressions in segment text and
propose `Timepoint`s with a resolved `start`/`end` where the expression is
concrete, or `label` only where it is not — "during the Renaissance" resolves to
nothing and should stay vague rather than being guessed into 1500-01-01.
Proposals attach to a per-document timeline, or to a named one when the agent
supplies it.

**Blocker to resolve first:** `write_batch_tx`
(`epimemer/storage/protocol.py:355`) takes `nodes`, `edges`, and `embeddings`
only. Ingestion is atomic today precisely because everything goes through it. If
extraction writes timelines, either they join that batch or a mid-document
failure can leave `TIMELINK` edges pointing at a timeline that was never stored.
Extending the batch is the right fix; it touches both backends and their
rollback paths.

That makes §7 its own issue, sequenced after §6, and not to be started until the
batch question is settled.

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
6. ⏳ **Extraction proposes timepoints** — open, gated on the `write_batch_tx`
   question in §7.

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
