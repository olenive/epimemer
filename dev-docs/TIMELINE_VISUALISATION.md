# Timeline visualisation

A design, built: the panel is `timeline-panel.ts` beside pure modules for the
scale, the filters, the marks, the labels and the validity lanes.

A timeline panel in the dashboard: a vertical axis with events as marks,
hover detail, filtering, zoom, and a split pane beside the graph, fed by
extraction proposing timepoints (§7) and drawing validity intervals by the
valid-time grammar (§13). The rules it shares with the other panels are
`VISUALISATION.md`, *Rules every panel follows*.

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
| Vague timepoints | A point the order places is drawn on the axis as a **hatched band** from `earliest` to `latest`, label verbatim on it. A point nothing constrains goes to the **tray**, in authored order, never a fake coordinate (§12.6) |
| Disputed order | A **contested mark** in the contradiction hue beside the point, which keeps whatever place it had. The mark says the order is disputed, not that the date is wrong (§12.6) |
| Recurrence | A rule's occurrences are **beads on a dotted spine** in a lane beside the axis, one lane per rule. A materialised occurrence is drawn as the ordinary point it became (§12.6, §13.1) |
| Large gaps | **Break the axis** where a gap is far above the local spacing (§4) |
| Zoom | Per-timeline zoom and pan, recomputing breaks from the visible domain; wheel **pans**, ⌘/ctrl-wheel zooms (§12.4) |
| Timepoint population | Extraction **proposes** timepoints as well as manual curation (§7) |
| Filters | Linked node type, node status, metacontext, date range, plus free-text field filters (§5) |
| Charting library | **None.** A piecewise-linear scale is a few lines; `d3` would be the largest thing in the bundle for `x = f(t)` |

---

## 2. Two modes, one panel

The panel plots marks on an axis. What the axis *means* is the mode, a selector
in the panel header. Switching mode resets the zoom and the selection; the
filters are shared by both modes.

### 2.1 Record time: "when did the graph learn this?"

Derived entirely from `created_at` and `retrieved_at` on each node. A node is
drawn as an interval from its creation to its last retrieval, the span over
which it stayed in use, collapsing to a point when no search has ever returned
it.

**What it cannot show.** The snapshot carries active nodes only, so record mode
plots no retirements. Widening the snapshot would change what the graph panel
draws too, which is a separate decision. Status changes that arrive live during
a session are applied, so the status filter is not inert, but a node retired
before the browser connected is simply absent.

Session activity is bursty (a graph sits untouched for days, then takes 200
nodes in three minutes), which makes record mode the ideal case for a broken
axis.

### 2.2 Content time: "when did this happen?"

The `Timeline` / `Timepoint` model. Marks are timepoints, drawn as points when
`end is None` and as bars when it is set.

---

## 3. Scale model

Each timeline's scale is **piecewise linear**: segments, each with a time
domain and a position range, separated by fixed-width breaks. The length left
after the breaks is divided among segments in proportion to each segment's own
span, so dense regions are not compressed by the sparse ones; with no breaks
this is an ordinary linear scale, the common case. Zoom state is a time domain
per timeline, not a scale factor, so it survives the data changing underneath
it.

Each timeline owns its domain: one graph can hold 1400 AD and last Tuesday,
and a common domain across those is unreadable at any zoom level.

---

## 4. Break heuristic

A gap breaks the axis when it is anomalous against the local spacing, a large
multiple of the median non-zero gap, **and** would waste a real share of the
visible span. Without the second test, breaking a gap that costs 8px gains
nothing and adds noise. The reasons behind the rest:

- A gap is measured from the furthest end any earlier mark reached, so
  intervals, and intervals that overlap, do not manufacture a gap that is not
  there.
- Fewer than three marks never break, with no special case: two marks give one
  gap, which is its own median.
- At most a handful of breaks, largest first. Beyond that the axis stops being
  an axis.
- Each break is a fixed-width plate labelled with the elapsed span in human
  units ("~600 years", "3 days").
- Breaks are recomputed on zoom from the visible domain only, so zooming into a
  cluster dissolves the break that was hiding the space around it. Hysteresis
  stops flicker during a zoom drag: a break persists until its gap falls well
  below the threshold that created it.

The constants are named in `timeline-scale.ts` so they can be retuned against
real data without hunting through render code.

---

## 5. Filtering

All filters are client-side predicates over the in-memory snapshot, composed
with AND:

- **Linked node type**: topic, fact, inference.
- **Node status**: one status, or any; *Active* hides marks whose only linked
  nodes are retired.
- **Metacontext**: keeps fictional and factual events visually separate.
- **Date range**: restricts visible marks independently of zoom. Range and
  zoom are different things: one hides data, the other magnifies.
- **Free text**: a query box matching against a flattened searchable string
  per mark (timepoint label, linked node content, source name, metacontext
  name, stringified metadata values). `field:value` prefixes make
  origin-style queries expressible (`source:BBC`, `mc:fiction`, `type:fact`,
  `label:war`), a bare term matches any field, multiple terms AND together,
  quoted terms match a phrase.

A mark is retained if **any** of its linked nodes passes the node-level
filters, so a timepoint linked to one live and one retired fact stays visible
under "active only".

---

## 6. Backend

### 6.1 Read path

The hub reads timelines through `viz_list_timelines(database)` on every
backend, because it must read any graph of a session without switching the
connection, and `query_timelines()` reads the active graph. `viz_*` methods
are never registered as MCP tools or imported under `epimemer/mcp/`.
Timepoints ship embedded in their timeline, as they are stored
(`VIZ_CONTRACT.md` §5).

### 6.2 Events

One event, `timeline_stored`, carrying the whole timeline. Every timeline write
is an upsert of the whole record (adding a timepoint re-stores the timeline),
so telling creation from extension would need a read before every write to
learn something the viewer can see for itself. Timelines are small; sending the
current state is cheaper and cannot drift from storage. The receiver replaces
its copy rather than merging.

### 6.3 Metacontexts in the snapshot

`has_metacontext` edges carry only ids, so without the metacontexts
themselves the metacontext filter could offer nothing but UUIDs; the snapshot
carries them. Where one is still unresolvable the panel falls back to the raw
id rather than dropping the association.

### 6.4 `reference_time`: a timeline's own "now"

`Timeline.reference_time: datetime | None`, round-tripped on every backend
inside the record like every other `Timeline` field.

**`None` means "follow the wall clock", and is deliberately not the same as
storing the current instant at creation.** A real-world timeline written with
today's date would have its present frozen at the moment it was first saved.
Unset resolves to `now` at read time instead, and the browser resolves it at
render rather than on arrival, so a long-lived session does not pin the
present to whenever the snapshot was assembled.

**It lives in the graph, not in the browser.** A fictional timeline's present
moment is a fact about that world ("the novel opens in May 1897"), so an
agent that reads the source should be able to record it, every client should
see the same answer, and it should survive a new machine. `localStorage`
would have satisfied the renderer and none of that. (Colour preferences make
the opposite call, for the opposite reason: `VISUALISATION.md` C.3.) Setting
it is a tool of its own, `set_reference_time`, because a fiction's anchor is
usually learned after ingesting enough of the source to say, and is often read
wrong first.

---

## 7. Extraction proposing timepoints

During decomposition, temporal expressions in node content become
`Timepoint`s: a resolved `start`/`end` where the expression is concrete,
`label` only where it is not. "During the Renaissance" resolves to nothing
and stays vague rather than being guessed into 1500-01-01. Each proposing
node gets a `TIMELINK` to the timepoint, which is what puts a mark on the
axis. The detector and the proposer are pure functions in
`pipelines/timeline/`; neither touches storage.

### 7.1 Timelines travel in the write batch

`write_batch_tx` takes `timelines` beside `nodes`, `edges` and `embeddings`,
because ingestion is atomic only if everything goes through it: a timeline
written outside the batch could leave `TIMELINK` edges pointing at a timeline
that was never stored after a mid-document failure, and the read path resolves
a dangling `TIMELINK` to an empty row rather than an error, so that failure
would be silent.

Timelines are the one **upsert** in an otherwise insert-only batch. A
timeline is a single record holding a list of timepoints, so appending a
timepoint *is* a replacement of that record, and undoing it on rollback means
restoring the row's previous content: the in-memory backend does that
explicitly, and SQLite, SurrealDB and Postgres get it from their transactions.

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

Two guards matter more than they look. A bare four-digit number is a year only
when a preposition in front of it says so ("in 1897", "by 1897") or a date
pattern surrounds it; otherwise `3000 troops` and `error code 1997` become
dates. And the preposition needs a word boundary in front of it, or
"versi*on* 2024" reads as "on 2024".

---

## 8. Modules

The scale, the filters, the marks, the labels and the validity lanes are pure
modules with no DOM access, beside the one panel module that draws and handles
the pointer (`VISUALISATION.md`, *Rules every panel follows*). Deciding *which
marks there are*, resolving `TIMELINK` edges to nodes, naming metacontexts and
splitting dated from undated, is as error-prone as the geometry and just as
testable without a browser, so it is a module of its own.

---

## 9. Interaction

- **Hover** highlights the mark's linked nodes in the graph panel and shows the
  timepoint's label, resolved dates and linked nodes in the detail drawer. The
  mark also carries the same text as a native tooltip, except where the label
  itself is the text being read.
- **Click** does the same, and also selects the mark and expands it in place
  (§12.10). Timepoints have no node id in the graph, so the bridge is the
  `TIMELINK` edge's `timepoint_id` metadata.

---

## 10. Testing

The break heuristic and the label layout were **mutation-tested**, because
both are exactly the shape of code where a behavioural test passes while the
logic is wrong: an inverted comparison still produces *an* axis. A change to
either deserves the same.

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

### 12.1 Direction: past at the top

Earlier time is higher on screen; scrolling down moves into the future. This
is the chat-log convention, and it reads with the page rather than against
it. It is also the cheaper option: position along the axis increases with
time exactly as the scale computes it, so nothing inverts anywhere.
Future-at-the-top needed a flip at render time and gained nothing.

### 12.2 One timeline at a time

The panel shows a single timeline, chosen from a selector in its header.
Record mode has a single entry holding every node, since node type is carried
by the side a mark sits on (§12.3); an entry per type would mean choosing one
type at a time, hiding two thirds of the graph to say something the layout
already says. Comparing two timelines against each other is a different
feature.

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

The backend half is §6.4. The panel opens centred on a stated reference time
rather than at either end (a timeline holding future events has no
meaningful edge to start at), and fitted to the data when none is stated. It
draws a labelled dashed neutral rule across the axis so past and future are
readable without arithmetic, and offers a "jump to now" control. A stated
reference time is folded into the extent computation, because the domain is
clamped to the data extent and a reference time outside the data (one whose
events are all past) could not otherwise be centred on; the wall clock is not
folded in, since stretching an 1897 timeline to today would bury the data.
Record mode ignores a stated time: record time is wall-clock, so there the
control stays visible and goes to real `now`.

### 12.6 The undated tray

A timepoint with no `start` and nothing constraining it gets no coordinate;
placing it anywhere on the axis would assert something false. "Below the axis"
means "later" on a vertical axis, so such chips sit in a visually separate tray,
a bordered, labelled block outside the scrolling axis. Order is authored order
within the timeline, which is what `reorder_timepoints` establishes (concrete
points sorted by start, vague ones appended in their original sequence), so the
panel and the backend agree on what "the order of a timeline" means. Chips are
interactive on the same terms as marks and obey the same filters.

**What leaves the tray.** A point with no `start` can still have a position, if
a source stated that it came after one dated point and before another. The
snapshot carries the derived `earliest` and `latest` for it (`TIMELINES.md`
§3.3), computed by the function that answers `query_timeline`, and such a point
is drawn on the axis as §13.1's hatched band spanning the two, with its label
written on it word for word. §13.2's rule 3 still holds: resolution adds a
position and never replaces the words. With only one bound known the band runs a
short way past it and dissolves, which is §13.1's unknown endpoint: "after the
fire, we do not know when" is fog below a known edge, not a bar stopping
somewhere nobody asserted.

**What the contested mark says.** A point whose stated order contradicts another
source's, or its own dates, is reported as contested and gets no derived
position. It keeps whatever place it had, a date or the tray, and gains a
zigzag in the contradiction hue beside it. The shape matters: the stroke runs
along the axis, the direction time runs in, and doubles back, so what reads as
tangled is the sequence. A cross or a ring over the mark would put the doubt on
the date, and a dispute about order is exactly what leaves a date standing.

**What stays in the tray**: points nothing constrains, contested points with no
date, and points from another timeline's clock (§13.2's rule 6).

**Recurrence beads.** A rule's occurrences are drawn as §13.1's beads on a
hairline dotted spine, in a lane left of the axis, one lane per rule. The spine
means "same rule, nothing asserted in between". A materialised occurrence gets
no bead: materialising turns it into an ordinary timepoint, which is already on
the axis as a full mark, and a bead as well would draw one date twice. A moved
occurrence's bead sits at the date it moved to. The window the snapshot
enumerates is `VIZ_CONTRACT.md` §5; it never falls back to the wall clock,
because enumerating from today would put occurrences at a place the graph never
spoke about.

### 12.7 Split pane

The panel is the right half of a split pane with a draggable vertical
divider; either half can be collapsed so the graph or the timeline takes the
full width. The split is held as a fraction rather than pixels, so a window
resize keeps the proportion instead of stranding a panel; the focused divider
resizes with the arrow keys; the split persists per viewer; and the last
visible half cannot be collapsed, which would leave an empty window with no way
back.

### 12.8 Tick labels in the mark column

Tick labels are drawn after every mark, on an opaque pill in `surfaceChrome`,
the same colour the break marker and the expanded card use, because all three
are axis chrome that has to overwrite what is behind it. The plate hides marks
landing at a tick's exact time, a real cost since position means time, but the
gutter between the mark column and the text columns is too narrow for any other
placement. The tick labels are not bold: hierarchy is carried by hue and
opacity, and chrome stays subordinate to data (`VISUALISATION.md` C.6).

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

The card goes to the label layout first, which makes it the highest priority,
so the one label the reader asked for is never the one dropped. A straddling
mark keeps its immovable block on the axis and gets a card in the left column
with a leader line tying it back. The hover drawer stays for text longer than
five lines.

### 12.11 The label layout's contract

Labels that do not fit are **dropped and reported, never spilled**. Letting an
oversized stack spill out of its space, on the reasoning that crowded text
beats absent text, passes every hand-written test and fails a seeded
randomised check asserting only "no overlaps, no reordering": a stack crammed
between two straddling blocks lands on a label sixty pixels away. What is
dropped is a label, not a mark: the tick is still drawn, and the count of
suppressed labels is shown. **The caller's order is the priority order.** The
module holds no opinion about which labels matter; the panel hands them over
in axis order, and a caller that wants something else can sort first.

---

## 13. Valid-time grammar

How the panel draws **validity intervals**, per `VALIDITY_DESIGN.md`: the
per-source intervals each `sourced_from` edge carries in the snapshot and on
the live `edge_stored` event. `timeline-validity.ts` decides what each interval
is and where it goes; `timeline-panel.ts` draws it.

Two of the shapes below also serve another feature: the hatched band draws a
point the order places, and beads on a dotted spine draw a rule's occurrences
(§12.6).

**A rendered mock of every mark in this section is checked in at
`dev-docs/mockups/valid-time-grammar.html`**: self-contained, theme-aware, no
dependencies. It is the visual reference for the *marks*; §13.3 is the
reference for the hues, two of which changed after the mock was drawn.

**The governing principle: one mark per validity concept, and nothing drawn
that the data does not assert.** If two things are different in the model
they must be different on screen; if the model refuses to claim something
(open-world gaps, unknown endpoints), the pixels must refuse too.

### 13.1 The marks

| Concept | Mark |
|---|---|
| Endpoint: **point** (stated) | Crisp bar cap: a short perpendicular tick |
| Endpoint: **unknown** | The bar **dissolves**: a linear gradient to fully transparent over roughly 24px. "The edge is somewhere in this fog" |
| Endpoint: **unbounded** | The bar keeps **full weight and runs off the edge of the drawing**. "There is no edge" |
| **Witness point** | Solid dot on the bar with a faint halo ring. A claim with no endpoints and one witness, the commonest real record, is a dot with the bar fading **symmetrically** away from it in both directions |
| **Vague label, resolved** ("during the Renaissance" with explicit approximate bounds) | **Hatched** band (45° line pattern), the stored label rendered verbatim on the band |
| **Vague label, unresolved**, nothing else placing the interval | Never placed on the axis. Goes to the undated tray (§12.6) as a chip, label intact |
| **Labelled edge**: a named endpoint on an interval whose other endpoint or witness has a date | The placed part draws as usual. Towards the named side the bar keeps full weight for a short stub, the length of a fade, and ends square with the stored label sitting at its end, verbatim, in the mono face. No tick, no date, no fade: the edge is a word, and the word is drawn where the edge would be |
| **Per-source validity** | One thin strip per source, stacked under the fact, each **direct-labelled** with its source. No filled union bar, ever. An optional summary is a **hollow dashed envelope** whose tooltip reads "any source asserts", outline-only so it cannot be read as a claim |
| Interval marked **stated** | Solid fill |
| Interval marked **inferred** | Hollow fill (soft tint) with a dashed outline; squinting performs the stated-only filter the API offers |
| Reflect-**proposed** boundary: an unknown edge reflect has offered a date for, taken from the claim on the other side of a succession | The solid body stops where the record's own dates stop. From there a hollow dashed extension in the pending colour runs to the proposed date and ends in a dashed cap of the same colour, with a chip beside it, "proposed · review". The extension replaces the fade that edge would otherwise wear, so an endpoint still carries one mark. Accepting rewrites the interval with that date and `inferred` basis, so the same stretch turns into an ordinary hollow dashed bar in the claim hue and the chip goes |
| **Several periods** (one claim, one source, more than one interval) | One bar per period in the source's lane, joined by a **hairline dotted spine**; the spine says "same claim, nothing asserted here". A recurrence *rule*'s occurrences are beads on a dotted spine instead (§12.6) |
| **`temporally_followed_by`** | A small elbow connector from the end of one claim's bar to the start of the next, with a terminal dot: order, not replacement. The renderer must be cycle-safe, since recurrence makes cycles legal for this edge |
| Status **`HISTORICAL`** | Fainter bar (lower opacity), **never hidden**: still true of its period |
| Status **`CORRECTED`** | Hidden by default, mirroring `include_corrected=off`; struck-through when summoned |
| The **now-line** | A dashed **neutral** rule across the axis, labelled "now". It is the timeline's `reference_time`, never wall-clock |

### 13.2 Rules: places a reasonable rendering would lie

1. **A gap is never styled as "false".** Open world: outside a stated
   interval is *no assertion*. A red or shaded gap draws a claim nobody made.
2. **Unknown and unbounded never share a treatment.** They are different
   values in the model, so fade for one, running off the edge for the other.
3. **A label-only interval never sits on the axis.** Tray until resolved;
   resolution *adds* a position, it never replaces the words. An interval
   with a date at one end and a label at the other is not label-only: the
   date goes where dates go and the label is drawn as a labelled edge, never
   as a fade, since a fade says "unknown" about an edge the source named.
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
   premises are not what is unsound. No panel draws a soundness flag yet; the
   rule is pinned by a test that no premise carries the pending colour.
9. **A proposal never thickens the body.** The solid part ends at the last
   date the source gave. What reflect offers is drawn in the pending colour
   and hollow, which is the reading it would get once accepted, since an
   accepted boundary makes the interval `inferred`; until then it is not the
   record and is not coloured as if it were.


### 13.3 Colour tokens

The hues are the shared `SemanticPalette`, whose values, validation and
reasons are `VISUALISATION.md` C.6. How this panel uses them:

| Token | Role here |
|---|---|
| `fact` | fact and topic marks; the timeline draws topics in `fact` |
| `inference` | inference cards and marks |
| `pending` | proposals, on the `pendingSurface` ground |
| contradiction | the contested zigzag |

The now-line is chrome, not data: a dashed neutral rule, with no token of its
own. A historical strip keeps its strip hue at lower opacity, so the
`historical` token is not drawn here. Source strips do not draw from the
palette at all (`VISUALISATION.md` C.6).

### 13.4 Implementation notes

- **Every `temporally_followed_by` walk must be cycle-safe.** Each step is
  drawn once, and a walk stops on a step it has already drawn, so a chain that
  returns to its own lane terminates.
- **The tooltip layer** carries what the mark compresses: the `(source,
  interval)` pairs verbatim, endpoint kinds by name, `stated`/`inferred`, and
  the witness date. The mark is the summary; the tooltip is the record.
- **Content time only.** These are periods the world was in, so they belong on
  the axis that measures when things happened. Plotting them against
  `created_at` would put a date on the wrong clock.
- **Historical is dimmed rather than desaturated**, the panels' shared rule
  (`VISUALISATION.md`, *Rules every panel follows*), so a claim the retrieval
  missed and a claim that is history never arrive at the same appearance.
- **Lanes are reused down the axis**, since column position carries no meaning:
  a lane is free again once the period above it has ended. A side draws three
  lanes at once and counts the rest, the way the label layout counts the labels
  it could not place.
- **The envelope appears on selection.** It is the optional summary, and
  selecting a mark is the gesture this panel already uses for detail on demand
  (§12.10), so it needed no control of its own. It is drawn for a selected fact
  with more than one source, where there is a disagreement to summarise.
- **A named endpoint keeps the rest of its interval on the axis.** The source
  said where that edge is ("the Renaissance"), so a fade, which says "we do not
  know where", would be false; the labelled edge of §13.1 is the mark. An
  interval draws when a date or a witness places it, and each endpoint wears
  its own mark. The stub asserts no extent, the way a fade asserts none: it is
  a fixed length in pixels, and its word is rotated into the lane's own column
  so it cannot cross what the neighbouring lanes draw. Only an interval nothing
  places, two labels, or a label beside an unknown or unbounded edge with no
  witness, still goes to the tray.
- **A proposed boundary is derived, never stored.** Reflect works proposals out
  from a `temporally_followed_by` edge plus a located date on the other claim,
  and writes nothing; a stored "proposed" field would be a snapshot of that
  derivation that goes stale the moment either claim changes. So the snapshot
  carries `boundary_proposals`, worked out by the same pure core `reflect`
  calls, from the same reads that produce the strips, and the browser never
  re-derives a server rule. The chip is passive: the dashboard is the read
  side, and accepting goes through `apply_reflection(boundaries=…)`. A declined
  proposal is not drawn, so the endpoint wears the mark it would without it. An
  interval whose only date would be the proposal stays in the tray with a
  pending badge on its chip: the graph holds no date for it yet.
- **A timeline with a stated `reference_time` keeps its own clock.** An
  interval naming no clock is measured against the default wall-clock timeline,
  which is the one that states no present (`VALIDITY_DESIGN.md` §2.5), so it
  draws on a timeline that follows the wall clock and on no other. An interval
  naming this timeline always draws. Anything else is cross-clock and goes to
  the tray with the clock badge.
