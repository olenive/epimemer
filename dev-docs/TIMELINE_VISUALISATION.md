# Timeline visualisation

A timeline panel in the dashboard: a vertical axis with events as marks,
hover detail, filtering, zoom, and a split pane beside the graph. The panel
is built, including extraction proposing timepoints (§7). The valid-time
grammar (§13) is designed and not built.

---

## 1. Decisions

| Question | Decision |
|---|---|
| Which time axis? | **Both**, as two modes of one panel: *content time* and *record time* (§2) |
| Axis orientation | **Vertical**, past at the top and future at the bottom, the chat-log convention (§12.1) |
| Panel placement | **Split pane** beside the graph with a draggable vertical divider; either panel can take the full width (§12.7) |
| How many timelines at once? | **One**, chosen from a selector. Comparing timelines is a different feature (§12.2) |
| What does left and right mean? | Facts and topics **left**, inferences **right**, mixed marks **straddle** the axis (§12.3) |
| Where is "now"? | A per-timeline `reference_time` stored **in the graph**, not the browser (§6.4, §12.5) |
| Vague timepoints | In a dedicated **tray**, in authored order. Never a fake coordinate on the axis (§12.6) |
| Large gaps | **Break the axis** where a gap is far above the local spacing (§4) |
| Zoom | Per-timeline zoom and pan, recomputing breaks from the visible domain; wheel **pans**, ⌘/ctrl-wheel zooms (§12.4) |
| Timepoint population | Extraction **proposes** timepoints as well as manual curation (§7) |
| Filters | Linked node type, node status, metacontext, date range, plus free-text field filters (§5) |
| Charting library | **None.** A piecewise-linear scale is a few lines; `d3` would be the largest thing in the bundle for `x = f(t)` |

---

## 2. Two modes, one panel

The panel plots marks on an axis. What the axis *means* is the mode. Mode is
a toggle in the panel header; zoom, filter and selection state are kept per
mode, so switching back restores what you were looking at.

### 2.1 Record time: "when did the graph learn this?"

Derived entirely from fields already on `NodeView`: `created_at` and
`retrieved_at`. A node is drawn as an interval from its creation to its last
retrieval, the span over which it stayed in use, collapsing to a point when
no search has ever returned it. This mode needs no backend data beyond the
snapshot, which is why it was built first: it exercised the whole rendering
design against data that always exists.

**What it cannot show.** `viz_list_nodes` returns active nodes, so the
snapshot has no retired ones and record mode plots no retirements. Widening
the snapshot would change what the graph panel draws too, which is a
separate decision. Status changes that arrive live during a session are
applied, so the status filter is not inert, but a node retired before the
browser connected is simply absent.

Session activity is bursty (a graph sits untouched for days, then takes 200
nodes in three minutes), which makes record mode the ideal case for a broken
axis.

### 2.2 Content time: "when did this happen?"

The `Timeline` / `Timepoint` model. Marks are timepoints, drawn as points
when `end is None` and as bars when it is set. Needs the read path and events
in §6.

---

## 3. Scale model

Each timeline's scale is **piecewise linear**: a list of segments, each with
a time domain and a position range, separated by fixed-width breaks.

```
segments: {t0, t1, p0, p1}[]     breaks: {after: segmentIndex, span: Duration}[]
```

Available length is `L - breaks.length * BREAK_PX`, divided among segments in
proportion to each segment's own span, so dense regions are not compressed by
the sparse ones. With zero breaks this degenerates to an ordinary linear
scale, which is the common case. Both directions exist: `timeToPos` for
rendering, `posToTime` for pointer hit-testing and zoom anchoring. Zoom
state is `{t0, t1}` per timeline, not a scale factor, so it survives the data
changing underneath it.

Each timeline owns its domain: one graph can hold 1400 AD and last Tuesday,
and a common domain across those is unreadable at any zoom level.

---

## 4. Break heuristic

Inputs: the sorted concrete timepoints of one timeline, and the current
visible domain.

1. Compute the gap between consecutive marks as
   `start[i+1] - (end[i] ?? start[i])`, so intervals do not manufacture a gap
   that is not there.
2. Take `m` = median of the non-zero gaps. If every gap is zero or there are
   fewer than three marks, do not break: there is no local spacing for a gap
   to be anomalous against.
3. A gap is a candidate if both hold: `gap > GAP_FACTOR * m` (anomalous
   relative to local spacing; `GAP_FACTOR = 10`) and `gap / visibleSpan >
   GAP_MIN_FRACTION` (it would actually waste screen; `GAP_MIN_FRACTION =
   0.12`). Without the second, breaking a gap that costs 8px gains nothing
   and adds noise.
4. Take at most `MAX_BREAKS = 5` candidates, largest first. Beyond a handful
   of breaks the axis stops being an axis.
5. Render each break as a fixed-width hatched marker labelled with the
   elapsed span in human units ("~600 years", "3 days").

Recompute on zoom, from the visible domain only: zooming into a cluster
dissolves the break that was hiding the space around it. Hysteresis stops
flicker during a zoom drag: once a break exists it persists until its gap
falls below `GAP_FACTOR * m * 0.7`.

All of this is pure, `(marks, visibleDomain, length) → {segments, breaks}`,
in `timeline-scale.ts` with no DOM access. The constants are named there so
they can be retuned against real data without hunting through render code.

---

## 5. Filtering

All filters are client-side predicates over the in-memory snapshot, composed
with AND:

- **Linked node type**: topic, fact, inference.
- **Node status**: hide marks whose only linked nodes are retired.
- **Metacontext**: keeps fictional and factual events visually separate.
- **Date range**: restricts visible marks independently of zoom. Range and
  zoom are different things: one hides data, the other magnifies.
- **Free text**: a query box matching against a flattened searchable string
  per mark (timepoint label, linked node content, source name, metacontext
  name, stringified metadata values). `field:value` prefixes make
  origin-style queries expressible (`source:BBC`, `mc:fiction`, `type:fact`,
  `label:war`), a bare term matches any field, multiple terms AND together,
  quoted terms match a phrase. Parsing is pure, in `timeline-filter.ts`.

A mark is retained if **any** of its linked nodes passes the node-level
filters, so a timepoint linked to one live and one retired fact stays visible
under "active only".

---

## 6. Backend

### 6.1 Read path

```python
async def viz_list_timelines(self, database: str) -> Sequence[Timeline]: ...
```

`query_timelines()` cannot be reused: it has no database argument and reads
the session's active graph, while the hub must read any graph of a session
without switching the connection. Implemented on both backends as a real
method. `viz_*` methods are never registered as MCP tools or imported under
`epimemer/mcp/`. `assemble_snapshot` carries a `timelines` key, and
`TimelineView` / `TimepointView` mirror the shape in `types.ts`. Timepoints
ship embedded in their timeline, as they are stored.

### 6.2 Events

One event, `TimelineStored`, carrying the whole `TimelineView`. `store_timeline`
is an upsert and the only write path (adding a timepoint re-stores the
timeline), so telling creation from extension would require a read before
every write to learn something the viewer can see for itself. Timelines are
small; sending the current state is cheaper and cannot drift from storage.
The receiver replaces its copy rather than merging. Published from
`instrumented_storage.store_timeline`. `TIMELINK` edge creation already emits
`EdgeStored`.

### 6.3 Metacontexts in the snapshot

`has_metacontext` edges carry only ids, so without the metacontexts
themselves the frame filter could offer nothing but UUIDs.
`viz_list_metacontexts(database)` mirrors `viz_list_timelines`, and
`assemble_snapshot` carries a `metacontexts` key. Where a frame is still
unresolvable the panel falls back to the raw id rather than dropping the
association.

### 6.4 `reference_time`: a timeline's own "now"

`Timeline.reference_time: datetime | None`, round-tripped on both backends
inside the record like every other `Timeline` field.

**`None` means "follow the wall clock", and is deliberately not the same as
storing the current instant at creation.** A real-world timeline written with
today's date would have its present frozen at the moment it was first saved.
Unset resolves to `now` at read time instead.

**It lives in the graph, not in the browser.** A fictional timeline's present
moment is a fact about that world ("the novel opens in May 1897"), so an
agent that reads the source should be able to record it, every client should
see the same answer, and it should survive a new machine. `localStorage`
would have satisfied the renderer and none of that. (Colour preferences make
the opposite call, for the opposite reason: `VISUALISATION.md` C.3.)

Reachable three ways: `create_timeline(name, reference_time=…)` when it is
known up front; `set_reference_time(timeline_id, reference_time=…)`, separate
because a fiction's anchor is usually learned after ingesting enough of the
source to say, and is often read wrong first (passing nothing clears it);
and `query_timeline` reports it on every call. `TimelineView` carries it as
`reference_time: string | null`, resolved to real `now` at render rather than
substituted on arrival, so a long-lived browser session does not pin the
present to whenever the snapshot was assembled.

---

## 7. Extraction proposing timepoints

During decomposition, temporal expressions in node content become
`Timepoint`s: a resolved `start`/`end` where the expression is concrete,
`label` only where it is not. "During the Renaissance" resolves to nothing
and stays vague rather than being guessed into 1500-01-01. Each proposing
node gets a `TIMELINK` to the timepoint, which is what puts a mark on the
axis.

`detect_temporal_expressions` (`pipelines/timeline/temporal.py`) is a pure
function over text, and `propose_timepoints` (`pipelines/timeline/functions.py`)
turns its output into a timeline and edges. Neither touches storage.

### 7.1 Timelines travel in the write batch

`write_batch_tx` takes `timelines` beside `nodes`, `edges` and `embeddings`,
because ingestion is atomic only if everything goes through it: a timeline
written outside the batch meant a mid-document failure could leave `TIMELINK`
edges pointing at a timeline that was never stored, and the read path
resolves a dangling `TIMELINK` to an empty row rather than an error, so that
failure was silent.

Timelines are the one **upsert** in an otherwise insert-only batch. A
timeline is a single record holding a list of timepoints, so appending a
timepoint *is* a replacement of that record. The consequence is in the
rollback path: undoing an upsert means restoring the row's previous content,
which the in-memory backend does explicitly and SurrealDB gets from its
transaction.

Testing that is asymmetric. SurrealDB builds every statement before running
any, so a failure injected from Python aborts before the transaction opens:
the parity test proves the observable ("the old timeline is intact") on both
backends but only exercises the in-memory restore.
`test_write_batch_tx_rolls_back_a_timeline_upsert` in
`test_surrealdb_storage.py` collides inside the transaction, after the upsert
in statement order, which is what proves the database rolls it back.

### 7.2 Two rules

**Detection reads node content, not segment text.** A mark needs a node to
hang on. A date found in segment text leaves "which of this segment's nodes
is this about?" unanswerable, and any answer is a guess; a date found in a
fact's own content belongs to that fact. A date stated in a segment but
dropped by the agent's decomposition is not proposed, which is correct,
since nothing in the graph claims it.

**One shared timeline per graph, not one per document.** The panel shows one
timeline at a time (§12.2), and a timeline per document would turn every
ingest into another near-empty entry in the selector with the marks that
make a chronology legible spread across all of them. Provenance is not lost
by sharing: every node keeps its `sourced_from` edge. `timeline_id` still
routes a document onto a curated timeline when the agent has one; that named
timeline must already exist, because creating one silently would put the
document somewhere the caller cannot find, under a name they never chose.

### 7.3 What the detector will not do

A missed expression costs a mark on the timeline; an invented one is
indistinguishable from evidence once stored. So it resolves only what the
text states (a day, a month, a year, a decade, a century are all intervals,
differing in width but not in kind), and everything else that reads as
temporal comes back as a label with no dates. Out of scope on purpose:

- **Relative expressions** ("three years later"). They need a document-level
  anchor this function does not have; `reference_time` (§6.4) is where one
  would come from, and resolving against it is separate work.
- **`of` as a temporal framing.** "The winter of 1897" would be worth having,
  but "a group of 1500 people" is the same shape and the cost is not
  symmetric.
- **Clock times**, and anything needing the reader's present.

Two guards matter more than they look. A bare four-digit number is a year
only when a preposition frames it or a date pattern surrounds it; otherwise
`3000 troops` and `error code 1997` become dates. And the preposition needs a
word boundary in front of it, or "versi*on* 2024" reads as "on 2024".

---

## 8. Modules

| File | Contents | DOM? |
|---|---|---|
| `timeline-scale.ts` | gap heuristic, piecewise scale, `timeToPos`/`posToTime`, zoom domain maths | no |
| `timeline-filter.ts` | query parsing, mark predicates | no |
| `timeline-model.ts` | snapshot → marks, for both modes; side per mark; facet gathering | no |
| `timeline-labels.ts` | label layout and text wrapping (§12.10, §12.11) | no |
| `timeline-panel.ts` | SVG rendering, pointer handling, wiring to the event router | yes |
| `split-pane.ts` | the graph/timeline divider (§12.7) | yes |

The pure/impure split is the point: the parts most likely to be wrong (break
placement, zoom anchoring, query parsing, mark linkage, label layout) need no
browser to test. `timeline-model.ts` exists because deciding *which marks
there are* (resolving `TIMELINK` edges to nodes, naming frames, splitting
dated from undated) is as error-prone as the geometry and just as testable
without a DOM.

Backend: `protocol.py`, `memory.py`, `surrealdb_adapter.py`,
`instrumented_storage.py`, `snapshot.py`, `events.py`.

---

## 9. Interaction

- **Hover** shows the timepoint's label, resolved dates and linked nodes in
  the detail drawer rather than a tooltip.
- **Click** selects, expands the mark in place (§12.10), and emits through the
  event router so the graph panel highlights the linked nodes. Timepoints
  have no node id in the graph, so the bridge is the `TIMELINK` edge's
  `timepoint_id` metadata.
- **Esc** clears selection.

---

## 10. Testing

Failing test first, storage additions through the parameterised `storage`
fixture, frontend pure modules under vitest. The break heuristic and the
label layout were **mutation-tested**, because both are exactly the shape of
code where a behavioural test passes while the logic is wrong: an inverted
comparison still produces *an* axis. The sweeps found real gaps the
behavioural tests had missed, and dead guards, which were removed rather than
kept as untestable insurance.

---

## 11. Open risks

- **Break heuristic constants are guesses**, named in one module so they can
  be retuned.
- **Very large graphs.** Record mode plots every node. Past a few thousand
  marks the SVG needs either aggregation into density bins at low zoom or
  virtualised rendering of the visible domain only. Measure before
  optimising.
- **Dense clusters defeat any label layout.** The panel says so ("+7 labels
  hidden, zoom in") rather than overlapping. The aggregation answer (a "12
  marks" cluster that expands on zoom) is unbuilt and its own design.

---

## 12. The vertical panel

A horizontal axis is starved of the one dimension that matters: a mark's
label competes with the axis for horizontal room. Turning the axis 90°
trades a scarce dimension for an abundant one. Time gets the scroll
direction, which is unbounded, and text gets the width, which is what it
needed.

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

### 12.1 Direction: past at the top

Earlier time is higher on screen; scrolling down moves into the future. This
is the chat-log convention, and it reads with the page rather than against
it. It is also the cheaper option: position along the axis increases with
time exactly as the scale computes it, so the renderer maps `y = pos` and
nothing inverts anywhere. Future-at-the-top was tried first, needed a flip at
render time, and gained nothing.

### 12.2 One timeline at a time

The panel shows a single timeline, chosen from a selector in its header.
`buildContentRows` returns one row per timeline and feeds the selector; the
renderer narrows. Record mode has a single row holding every node, since
node type is carried by the side a mark sits on (§12.3); rows per type would
mean choosing one type at a time from the selector, hiding two thirds of the
graph to say something the layout already says. Comparing two timelines
against each other is a different feature.

### 12.3 Sides carry node type

Facts and topics sit **left** of the axis, inferences **right**, and both
sides get the full half-width for text. Facts and topics are what the graph
was told; inferences are what it worked out. Putting the derived layer on its
own side makes "how much of this timeline is inferred?" answerable at a
glance.

**Mixed marks straddle the axis.** A content-mode mark is a *timepoint*, and
one timepoint can hold nodes of different types. Such a mark is drawn as one
wider block crossing the axis, listing its members. Splitting it into two
marks would invent a second thing where the data has one.

### 12.4 Gestures

| Gesture | Action |
|---|---|
| Wheel | Pan through time |
| ⌘/ctrl + wheel | Zoom, anchored at the pointer's time value |
| Drag | Pan |
| Shift-drag | Select a range to zoom into |

Wheel-to-pan is what "scrolling" means once the axis is vertical; ⌘-wheel
for zoom is the map and drawing-tool convention. Zoom clamps to the
timeline's full data extent plus a small margin, with a reset control.

**The viewport is virtual: no native scrollbar.** A tall SVG in an
`overflow-y` container would give free momentum and break the model: the
break heuristic recomputes from the visible domain (§4) and zoom is a domain
transform (§3). Wheel events adjust the domain.

### 12.5 Reference time

The backend half is §6.4. The panel opens centred on the reference time
rather than at either end (a timeline holding future events has no
meaningful edge to start at), draws a labelled dashed neutral rule across the
axis so past and future are readable without arithmetic, and offers a "jump
to now" control. The reference time is folded into the extent computation,
because the domain is clamped to the data extent and a reference time
outside the data (an empty timeline, or one whose events are all past) could
not otherwise be centred on. Record mode ignores it: record time is
wall-clock, so record mode always marks real `now` and the control is
hidden.

### 12.6 The undated tray

A timepoint with no `start` gets no coordinate; placing it anywhere on the
axis would assert something false. "Below the axis" means "later" on a
vertical axis, so undated chips sit in a visually separate tray, a bordered,
labelled block outside the scrolling axis. Order is authored order within the
timeline, which is what `reorder_timepoints` establishes (concrete points
sorted by start, vague ones appended in their original sequence), so the
panel and the backend agree on what "the order of a timeline" means. Chips
are interactive on the same terms as marks and obey the same filters.

### 12.7 Split pane

The panel is the right half of a split pane with a draggable vertical
divider; either half can be collapsed so the graph or the timeline takes the
full width. `split-pane.ts` holds the split as a fraction rather than pixels
(so a window resize keeps the proportion instead of stranding a panel),
supports arrow-key resizing on the focused divider, persists in
`localStorage`, and refuses to collapse the last visible half, which would
leave an empty window with no way back.

### 12.8 Tick labels in the mark column

SVG has no z-index, so the later sibling wins. `renderAxis` returns its tick
labels in a group that the caller appends last, after every mark, and each
label carries an opaque plate (a pill, `rx` at half its height) in
`surfaceChrome`, the same colour the break marker and the expanded card use,
because all three are axis chrome that has to overwrite what is behind it.
The plate hides marks landing at a tick's exact time, a real cost since
position means time, but the gutter between the mark column and the text
columns is too narrow for any other placement. Neither panel uses
`font-weight`; hierarchy is carried by hue and opacity, and chrome stays
subordinate to data (§13.3), so the tick labels are not bold. Guarded by the
*tick labels* tests in `timeline-panel.test.ts`.

### 12.9 Label layout

Labels are **truncated** to one line, budgeted from the half-width, with the
full text on hover and in the expanded card. Wrapping would make a label's
height depend on its content, which the layout would then have to solve
against. Labels that will not fit are counted and reported rather than
overlapped (§11). The layout is pure (`timeline-labels.ts`) because it was
the unproven part of the redesign and had to be testable without a browser.

### 12.10 Expand on select

Clicking a mark expands its text in place: the label becomes a bordered card
of up to five wrapped lines carrying the timepoint's dates and content, and
the neighbouring labels slide out of the way.

**The room is made in the label column, not on the axis.** Inserting height
at that point on the axis would break the one thing the axis is for:
position means time, so pushing later marks down puts them where their
timestamps do not, and moves them relative to the reference-time rule. (A
break is not a counter-example: it is labelled with the span it removed, so
it states a fact about the data rather than about UI state.)

`timeline-labels` was built for exactly this: a `LabelRequest` carries its own
height, and the layout's job is sliding labels apart without touching marks.
The card is passed to the layout first, which makes it the highest priority,
so the one label the reader asked for is never the one dropped. A straddling
mark emits two requests: the immovable block on the axis, and a card in the
left column with a leader line tying it back. `wrapText` lives in
`timeline-labels.ts` because the layout needs a label's height before it can
place it, and the height is however many lines the text wraps to; existing
newlines are kept as hard breaks. The hover drawer stays for text longer than
five lines.

### 12.11 The label layout's contract

Labels that do not fit are **dropped and reported, never spilled**. A first
version let an oversized stack spill out of its space on the reasoning that
crowded text beats absent text; every hand-written test passed, and a seeded
randomised property check (300 cases, asserting only "no overlaps, no
reordering") failed on its first run, with a stack crammed between two
straddling blocks landing on a label sixty pixels away. What is dropped is a
label, not a mark: the tick is still drawn, and the count of suppressed
labels is shown. **The caller's order is the priority order.** The module
holds no opinion about which labels matter; the panel hands them over in axis
order, and a caller that wants something else can sort first.

A mutation sweep found that the "shares the displacement" test used identical
anchors, so it could not tell a mean from a first or a last, and that nothing
forced a label into the second free range. A 5,000-case run showed the outer
repeat-until-stable loop was dead, because the inner merge loop already
converges; it was removed.

---

## 13. Valid-time grammar (designed, not built)

How the panel would draw **validity intervals**, per `VALIDITY_DESIGN.md`.
The validity model is built; the viz snapshot carries no validity yet, so the
grammar has nothing to draw (`PROPOSED_FEATURES.md`). It was designed early
because it exposed two rendering decisions (gaps, the now-line) that would
otherwise be made by accident in code.

**A rendered mock of every mark in this section is checked in at
`dev-docs/mockups/valid-time-grammar.html`**: self-contained, theme-aware, no
dependencies. It is the visual reference for the *marks*; §13.3 is the
reference for the hues, two of which changed after the mock was drawn.

**The governing principle: one mark per validity concept, and nothing drawn
that the data does not assert.** If two things are different in the model
they must be different on screen; if the model refuses to claim something
(open-world gaps, unknown endpoints), the pixels must refuse too.

Provenance of the idea: Priestley's *Chart of Biography* (1765) drew certain
lifespans as solid lines and uncertain ones as dots. The witness dot below is
that idea; the overall register (muted bands, one accent, annotation over
ornament) is the Information-is-Beautiful school.

### 13.1 The marks

| Concept | Mark |
|---|---|
| Endpoint: **point** (stated) | Crisp bar cap with a short perpendicular tick; date label in the mono face |
| Endpoint: **unknown** | The bar **dissolves**: a linear gradient to fully transparent over roughly 24px. "The edge is somewhere in this fog" |
| Endpoint: **unbounded** | The bar keeps **full weight and exits the frame**. "There is no edge" |
| **Witness point** | Solid dot on the bar with a faint halo ring. A claim with no endpoints and one witness, the commonest real record, is a dot with the bar fading **symmetrically** away from it in both directions |
| **Vague label, resolved** ("during the Renaissance" with explicit approximate bounds) | **Hatched** band (45° line pattern), soft `c.` date labels, the stored label rendered verbatim on the band |
| **Vague label, unresolved** | Never placed on the axis. Goes to the undated tray (§12.6) as a chip, label intact |
| **Per-source validity** | One thin strip per source, stacked under the fact, each **direct-labelled** with its source. No filled union bar, ever. An optional summary is a **hollow dashed envelope** labelled "any source asserts", outline-only so it cannot be read as a claim |
| Interval marked **stated** | Solid fill |
| Interval marked **inferred** | Hollow fill (soft tint) with a dashed outline; squinting performs the stated-only filter the API offers |
| Reflect-**proposed** boundary | Dashed cap in the pending colour wearing a review chip ("proposed · review") until accepted |
| **Recurrence** (one node, several intervals) | Beads on a **hairline dotted claim spine**; the spine says "same claim, nothing asserted here" |
| **`temporally_followed_by`** | A small elbow connector from the end of one claim's bar to the start of the next, with a terminal dot: order, not replacement. The renderer must be cycle-safe, since recurrence makes cycles legal for this edge |
| Status **`HISTORICAL`** | Desaturated bar, **never hidden**: still true of its period |
| Status **`CORRECTED`** | Hidden by default, mirroring `include_corrected=off`; struck-through when summoned |
| The **now-line** | A dashed **neutral** rule across the axis, labelled "now — this timeline's clock". It is the timeline's `reference_time`, never wall-clock |

### 13.2 Rules: places a reasonable rendering would lie

1. **A gap is never styled as "false".** Open world: outside a stated
   interval is *no assertion*. A red or shaded gap draws a claim nobody made.
2. **Unknown and unbounded never share a treatment.** They are different
   values in the model, so fade for one, frame-exit for the other.
3. **A label-only interval never sits on the axis.** Tray until resolved;
   resolution *adds* a position, it never replaces the words.
4. **No filled union bar.** "No default collapse" is a data rule; the hollow
   envelope is the only summary allowed.
5. **Historical is muted, not hidden; corrected is hidden, not deleted.**
6. **Cross-clock claims never share an axis.** Comparison across timelines is
   `unknown` by definition; an in-universe claim on the CE axis asserts a
   mapping nobody made. Tray, with a clock badge.
7. **Bars fade *through* the now-line, they do not stop at it.** Stopping
   asserts an endpoint. Currency is a reading against the timeline's
   reference clock, not a stored boundary.
8. **Soundness flags sit on the inference, not on its premises.** The
   premises are not what is unsound.

### 13.3 Colour tokens

Validated in both themes with a perceptual checker (OKLCH lightness band,
chroma floor, colour-vision-deficiency ΔE separation, contrast against the
surface). This set became the **shared semantic palette for both panels**,
`SemanticPalette` in `theme.ts`; `VISUALISATION.md` C.6 is the source of
truth.

| Token | Light | Dark | Role |
|---|---|---|---|
| `claim` | `#2a78d6` | `#3987e5` | active claim bars, witness dots; also the graph's fact nodes |
| `inference` | `#4a3aa7` | `#9085e9` | inference cards and marks; also the graph's inference nodes |
| `topic` | `#1baf7a` | `#199e70` | the shared topic hue |
| `historical` | `#8095aa` | `#5d6d7e` | HISTORICAL bars |
| `pending` | `#9a6b00` on `#f6ecd4` | `#fab219` on `#33290e` | proposals, soundness flags |

There is no `now` token. Red belongs to contradiction, which appears in both
panels; the now-line is chrome, not data, and is a dashed neutral rule
(`--text-secondary` stroke, `--text-strong` label). Chrome stays subordinate
to data throughout: no bold, no semantic hue on axis furniture.

**Source strips do not draw from this table.** A strip is always
direct-labelled, so its hue carries no meaning and must not compete with one
that does. They take a plain rotation whose only requirement is that adjacent
strips in a stack differ.

### 13.4 Implementation notes

- **Fits the existing panel.** Vertical axis, past at top; facts left,
  inferences right; the undated tray absorbs unresolved labels and cross-clock
  chips; the reference-time marker *is* the now-line. Bars become vertical
  spans in lanes beside the axis.
- **Gradients and hatching** are plain SVG `linearGradient` / `pattern` fills
  referencing the CSS variables; the mock has working markup.
- **Every `temporally_followed_by` walk must be cycle-safe.** The elbow
  renderer and any lineage layout must terminate on revisited nodes.
- **The tooltip layer** carries what the mark compresses: the `(source,
  interval)` pairs verbatim, endpoint kinds by name, `stated`/`inferred`, and
  the witness date. The mark is the summary; the tooltip is the record.
- **Two parts before any of this draws**: per-source intervals into
  `snapshot.py`, then the SVG grammar.
