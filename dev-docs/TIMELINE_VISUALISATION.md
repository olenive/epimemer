# Timeline visualisation

The dashboard's timeline panel: a vertical axis with events as marks, hover
detail, filtering and zoom, in a split pane beside the graph. It draws the
timepoints extraction proposes (§7) and the validity intervals of the
valid-time grammar (§13). The panel is `timeline-panel.ts`, beside pure modules
for the scale, the filters, the marks, the labels and the validity lanes (§8).
The rules it shares with the other panels are `VISUALISATION.md`, *Rules every
panel follows*.

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
| Timepoint population | Extraction **proposes** timepoints, beside manual curation (§7) |
| Filters | Linked node type, node status, metacontext, date range, plus free-text field filters (§5) |
| Charting library | **None.** A piecewise-linear scale is a few lines; `d3` would be the largest thing in the bundle for `x = f(t)` |

---

## 2. Two modes, one panel

The mode, a selector in the panel header, says what the axis measures.
Switching mode resets the zoom and the selection; the filters are shared by
both modes.

### 2.1 Record time: "when did the graph learn this?"

Derived from `created_at` and `retrieved_at` on each node: a node is drawn as
an interval from its creation to its last retrieval, collapsing to a point when
no search has returned it. The snapshot carries active nodes only, so record
mode plots no retirements; widening the snapshot would change what the graph
panel draws too. Status changes that arrive live are applied, and a node
retired before the browser connected is absent.

### 2.2 Content time: "when did this happen?"

The `Timeline` / `Timepoint` model. Marks are timepoints, drawn as points when
`end is None` and as bars when it is set.

---

## 3. Scale model

Each timeline's scale is **piecewise linear**: segments, each with a time
domain and a position range, separated by fixed-width breaks. The length left
after the breaks is divided among segments in proportion to each segment's own
span, so dense regions are not compressed by the sparse ones; with no breaks
this is an ordinary linear scale. Zoom state is a time domain per timeline, not
a scale factor, so it survives the data changing underneath it.

Each timeline owns its domain: one graph can hold 1400 AD and last Tuesday,
and a common domain across those is unreadable at any zoom level.

---

## 4. Break heuristic

A gap breaks the axis when it is a large multiple of the median non-zero gap
**and** would waste a real share of the visible span; without the second test,
breaking a gap that costs 8px gains nothing and adds noise.

- A gap is measured from the furthest end any earlier mark reached, so
  intervals, and intervals that overlap, do not manufacture a gap.
- Fewer than three marks never break, with no special case: two marks give one
  gap, which is its own median.
- At most `MAX_BREAKS`, largest first; beyond that the axis stops being an
  axis.
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
- **Date range**: restricts visible marks independently of zoom. Range hides
  data; zoom magnifies.
- **Free text**: a query box matching each mark's fields: node type, status,
  metacontext, source, label (the timepoint's and the timeline's) and linked
  node content. `type:`, `status:`, `mc:`, `source:`, `label:` and `content:`
  target one field (`source:BBC`, `mc:fiction`); a bare term matches any field,
  terms AND together, and quoted terms match a phrase.

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
is an upsert of the whole record, so telling creation from extension would
need a read before every write; sending the current state is cheaper and
cannot drift from storage. The receiver replaces its copy rather than merging.

### 6.3 Metacontexts in the snapshot

`has_metacontext` edges carry only ids, so the snapshot carries the
metacontexts themselves and the filter can offer names rather than UUIDs. Where
one is still unresolvable the panel falls back to the raw id rather than
dropping the association.

### 6.4 `reference_time`: a timeline's own "now"

`Timeline.reference_time: datetime | None`, round-tripped on every backend
inside the record.

**`None` means "follow the wall clock"**, resolved by the browser at render
time, because storing the current instant at creation would freeze a
real-world timeline's present at the moment it was first saved.

**It lives in the graph, not in the browser**, because a fictional timeline's
present is a fact about that world ("the novel opens in May 1897") that an
agent should be able to record and every client should see. (Colour
preferences make the opposite call: `VISUALISATION.md` C.3.) Setting it is a
tool of its own, `set_reference_time`, because a fiction's anchor is usually
learned after ingesting enough of the source, and is often read wrong first.

---

## 7. Extraction proposing timepoints

During decomposition, temporal expressions in node content become
`Timepoint`s: a resolved `start`/`end` where the expression is concrete,
`label` only where it is not. "During the Renaissance" stays vague rather than
being guessed into 1500-01-01. Each proposing node gets a `TIMELINK` to the
timepoint, which is what puts a mark on the axis. The detector
(`pipelines/timeline/temporal.py`) and the proposer
(`pipelines/timeline/functions.py`) are pure functions; neither touches storage.

### 7.1 Timelines travel in the write batch

`write_batch_tx` takes `timelines` beside `nodes`, `edges` and `embeddings`,
because a timeline written outside the batch could leave `TIMELINK` edges
pointing at a timeline that was never stored after a mid-document failure, and
the read path resolves a dangling `TIMELINK` to an empty row rather than an
error, so that failure would be silent.

Timelines are the one **upsert** in an otherwise insert-only batch, since
appending a timepoint replaces the whole record; on rollback the in-memory
backend restores the previous row explicitly, and SQLite, SurrealDB and
Postgres get that from their transactions.

### 7.2 Two rules

**Detection reads node content, not segment text**, because a mark needs a
node to hang on, and a date found in segment text leaves "which of this
segment's nodes is this about?" to a guess.

**One shared timeline per graph, not one per document**, because the panel
shows one timeline at a time (§12.2) and a timeline per document would spread a
chronology across many near-empty selector entries; every node keeps its
`sourced_from` edge, so provenance is not lost. `store_decomposition`'s
`timeline_id` routes a document onto a curated timeline, which must already
exist, because creating one silently would put the document somewhere the
caller cannot find.

### 7.3 What the detector will not do

A missed expression costs a mark; an invented one is indistinguishable from
evidence once stored. So the detector resolves only what the text states (a
day, a month, a year, a decade, a century, all intervals of different widths),
and everything else that reads as temporal comes back as a label with no
dates. It leaves out relative expressions ("three years later"), which need a
document-level anchor; `of` before a year, because "the winter of 1897" has the
shape of "a group of 1500 people"; and clock times. A bare four-digit number is
a year only after a preposition ("in 1897") or inside a date pattern, or
`3000 troops` becomes a date, and the preposition needs a word boundary in
front of it, or "versi*on* 2024" reads as "on 2024".

---

## 8. Modules

`timeline-scale.ts`, `timeline-filter.ts`, `timeline-model.ts`,
`timeline-labels.ts` and `timeline-validity.ts` are pure modules with no DOM
access, beside `timeline-panel.ts`, which draws and handles the pointer
(`VISUALISATION.md`, *Rules every panel follows*). `timeline-model.ts` decides
which marks there are: it resolves `TIMELINK` edges to nodes, names
metacontexts and splits dated from undated, which is as error-prone as the
geometry and just as testable without a browser.

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

A change to the break heuristic or the label layout gets mutation-tested,
because both are code where a behavioural test passes while the logic is
wrong: an inverted comparison still produces *an* axis.

---

## 11. Open risks

- **Very large graphs.** Record mode plots every node. Past a few thousand
  marks the SVG needs aggregation into density bins at low zoom or virtualised
  rendering of the visible domain; neither is built. Measure before
  optimising.
- **Dense clusters defeat any label layout.** The panel says how many labels
  it hid and to zoom in, rather than overlapping them. A "12 marks" cluster
  that expands on zoom is unbuilt and its own design.

---

## 12. The vertical panel

A horizontal axis makes a mark's label compete with the axis for horizontal
room. A vertical axis gives time the scroll direction, which is unbounded, and
gives text the width.

### 12.1 Direction: past at the top

Earlier time is higher on screen; scrolling down moves into the future. This
is the chat-log convention, and position along the axis increases with time
exactly as the scale computes it, so nothing inverts anywhere.

### 12.2 One timeline at a time

The panel shows a single timeline, chosen from a selector in its header.
Record mode has a single entry holding every node, since node type is carried
by the side a mark sits on (§12.3); an entry per type would hide two thirds of
the graph to say something the layout already says.

### 12.3 Sides carry node type

Facts and topics sit **left** of the axis, inferences **right**, and both
sides get the full half-width for text. Facts and topics are what the graph
was told; inferences are what it worked out, so "how much of this timeline is
inferred?" is answerable at a glance.

**Mixed marks straddle the axis.** One timepoint can hold nodes of different
types, and is drawn as one wider block crossing the axis, listing its members;
two marks would invent a second thing where the data has one.

### 12.4 Gestures

| Gesture | Action |
|---|---|
| Wheel | Pan through time |
| ⌘/ctrl + wheel | Zoom, anchored at the pointer's time value |
| Drag | Pan |
| Shift-drag | Select a range to zoom into |

Wheel-to-pan is what scrolling means on a vertical axis; ⌘-wheel for zoom is
the map and drawing-tool convention. Zoom clamps to the timeline's full data
extent plus a small margin, with a reset control.

**The viewport is virtual: no native scrollbar.** The break heuristic
recomputes from the visible domain (§4) and zoom is a domain transform (§3),
so wheel events adjust the domain rather than scrolling a tall SVG.

### 12.5 Reference time

The backend half is §6.4. The panel opens centred on a stated reference time
(a timeline holding future events has no meaningful edge to start at), and
fitted to the data when none is stated. It draws a labelled dashed neutral rule
across the axis and offers a "jump to now" control. A stated reference time is
folded into the extent computation, because the domain is clamped to the data
extent and a reference time outside the data could not otherwise be centred
on; the wall clock is not folded in, since stretching an 1897 timeline to today
would bury the data. Record mode ignores a stated time: record time is
wall-clock, so there the control goes to real `now`.

### 12.6 The undated tray

A timepoint with no `start` and nothing constraining it gets no coordinate,
since placing it anywhere on the axis would assert something false. Such chips
sit in a bordered, labelled tray outside the scrolling axis ("below the axis"
would mean "later"). Order is authored order within the timeline, which is
what `reorder_timepoints` establishes (concrete points sorted by start, vague
ones appended in their original sequence), so the panel and the backend agree
on what "the order of a timeline" means. Chips are interactive on the same
terms as marks and obey the same filters.

**What leaves the tray.** A point with no `start` can still have a position,
when a source stated that it came after one dated point and before another.
The snapshot carries the derived `earliest` and `latest` for it (`TIMELINES.md`
§3.3), computed by the function that answers `query_timeline`, and the point is
drawn as §13.1's hatched band spanning the two, with its label on it word for
word (§13.2's rule 3: resolution adds a position and never replaces the
words). With only one bound known the band runs a short way past it and
dissolves, which is §13.1's unknown endpoint.

**What the contested mark says.** A point whose stated order contradicts
another source's, or its own dates, is reported as contested and gets no
derived position. It keeps whatever place it had, a date or the tray, and
gains a zigzag in the contradiction hue beside it. The stroke runs along the
axis and doubles back, so what reads as tangled is the sequence; a cross or a
ring over the mark would put the doubt on the date, which a dispute about order
leaves standing.

**What stays in the tray**: points nothing constrains, contested points with no
date, and points from another timeline's clock (§13.2's rule 6).

**Recurrence beads.** A rule's occurrences are drawn as §13.1's beads on a
hairline dotted spine, in a lane left of the axis, one lane per rule; the spine
means "same rule, nothing asserted in between". A materialised occurrence gets
no bead, since it is already on the axis as an ordinary timepoint. A moved
occurrence's bead sits at the date it moved to. The window the snapshot
enumerates is `VIZ_CONTRACT.md` §5; it never falls back to the wall clock,
because enumerating from today would put occurrences where the graph never
spoke.

### 12.7 Split pane

The panel is the right half of a split pane with a draggable vertical
divider; either half can be collapsed so the other takes the full width. The
split is held as a fraction rather than pixels, so a window resize keeps the
proportion; the focused divider resizes with the arrow keys; the split persists
per viewer; and the last visible half cannot be collapsed, which would leave an
empty window with no way back.

### 12.8 Tick labels in the mark column

Tick labels are drawn after every mark, on an opaque pill in `surfaceChrome`,
the colour the break marker and the expanded card use, because all three are
axis chrome that has to overwrite what is behind it. The plate hides marks
landing at a tick's exact time, a real cost, but the gutter between the mark
column and the text columns is too narrow for any other placement. Tick labels
are not bold: chrome stays subordinate to data (`VISUALISATION.md` C.6).

### 12.9 Label layout

Labels are **truncated** to one line, budgeted from the half-width, with the
full text on hover and in the expanded card, because wrapping would make a
label's height depend on its content. The layout is pure
(`timeline-labels.ts`) so it is testable without a browser, and its contract
is §12.11.

### 12.10 Expand on select

Clicking a mark expands its text in place: the label becomes a bordered card
of up to five wrapped lines carrying the timepoint's dates and content, and
the neighbouring labels slide out of the way.

**The room is made in the label column, not on the axis**, because position
means time: pushing later marks down would put them where their timestamps do
not, and move them relative to the reference-time rule. (A break states a fact
about the data, the span it removed, rather than about UI state.)

The card goes to the label layout first, which makes it the highest priority,
so the label the reader asked for is never the one dropped. A straddling mark
keeps its block on the axis and gets a card in the left column with a leader
line tying it back. The hover drawer stays for text longer than five lines.

### 12.11 The label layout's contract

Labels that do not fit are **dropped and reported, never spilled**: letting an
oversized stack spill passes hand-written tests and fails a seeded randomised
check of "no overlaps, no reordering", because a stack crammed between two
straddling blocks lands on a label sixty pixels away. What is dropped is a
label, not a mark: the tick is still drawn, and the count of suppressed labels
is shown. **The caller's order is the priority order**: the panel hands labels
over in axis order, and a caller that wants something else sorts first.

---

## 13. Valid-time grammar

How the panel draws **validity intervals**, per `VALIDITY_DESIGN.md`: the
per-source intervals each `sourced_from` edge carries in the snapshot and on
the live `edge_stored` event. `timeline-validity.ts` decides what each interval
is and where it goes; `timeline-panel.ts` draws it. Two of the shapes also
serve another feature: the hatched band draws a point the order places, and
beads on a dotted spine draw a rule's occurrences (§12.6).

`dev-docs/mockups/valid-time-grammar.html` is a self-contained, theme-aware
rendering of every mark below and the visual reference for the *marks*; §13.3
is the reference for the hues.

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
| Status **`CORRECTED`** | Hidden by default, mirroring `include_corrected=off`; struck through when summoned by choosing *Corrected* in the status filter |
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
   premises are not what is unsound. No panel draws a soundness flag; the
   rule is pinned by a test that no premise carries the pending colour.
9. **A proposal never thickens the body.** The solid part ends at the last
   date the source gave. What reflect offers is drawn in the pending colour
   and hollow, the reading it would get once accepted, since an accepted
   boundary makes the interval `inferred`; until then it is not the record and
   is not coloured as if it were.

### 13.3 Colour tokens

The hues are the shared `SemanticPalette`, whose values, validation and
reasons are `VISUALISATION.md` C.6. How this panel uses them:

| Token | Role here |
|---|---|
| `fact` | fact and topic marks; the timeline draws topics in `fact` |
| `inference` | inference cards and marks |
| `pending` | proposals, on the `pendingSurface` ground |
| contradiction | the contested zigzag |

The now-line is chrome, a dashed neutral rule with no token of its own. A
historical strip keeps its strip hue at lower opacity, so the `historical`
token is not drawn here. Source strips do not draw from the palette at all
(`VISUALISATION.md` C.6).

### 13.4 Implementation notes

- **Every `temporally_followed_by` walk must be cycle-safe.** Each step is
  drawn once, and a walk stops on a step it has already drawn, so a chain that
  returns to its own lane terminates.
- **The tooltip layer** carries what the mark compresses: the `(source,
  interval)` pairs verbatim, endpoint kinds by name, `stated`/`inferred`, and
  the witness date. The mark is the summary; the tooltip is the record.
- **Content time only.** These are periods the world was in, so they belong on
  the axis that measures when things happened; plotting them against
  `created_at` would put a date on the wrong clock.
- **Lanes are reused down the axis**, since column position carries no
  meaning: a lane is free again once the period above it has ended. A side
  draws three lanes at once and counts the rest, the way the label layout
  counts the labels it could not place.
- **The envelope appears on selection**, the gesture this panel already uses
  for detail on demand (§12.10), for a selected fact with more than one source,
  where there is a disagreement to summarise.
- **A named endpoint keeps the rest of its interval on the axis.** The source
  said where that edge is, so a fade would be false; the labelled edge of
  §13.1 is the mark. An interval draws when a date or a witness places it, and
  each endpoint wears its own mark. The stub asserts no extent: it is a fixed
  length in pixels, and its word is rotated into the lane's own column so it
  cannot cross what the neighbouring lanes draw. Only an interval nothing
  places (two labels, or a label beside an unknown or unbounded edge with no
  witness) goes to the tray.
- **A proposed boundary is derived, never stored.** Reflect works proposals out
  from a `temporally_followed_by` edge plus a located date on the other claim,
  and writes nothing; a stored "proposed" field would go stale the moment
  either claim changes. So the snapshot carries `boundary_proposals`, worked
  out by the same pure core `reflect` calls, from the same reads that produce
  the strips, and the browser never re-derives a server rule. The chip is
  passive: accepting goes through `apply_reflection(boundaries=…)`. A declined
  proposal is not drawn. An interval whose only date would be the proposal
  stays in the tray with a pending badge on its chip, since the graph holds no
  date for it yet.
- **A timeline with a stated `reference_time` keeps its own clock.** An
  interval naming no clock is measured against the default wall-clock
  timeline, the one that states no present (`VALIDITY_DESIGN.md` §2.5), so it
  draws on a timeline that follows the wall clock and on no other. An interval
  naming this timeline always draws. Anything else is cross-clock and goes to
  the tray with the clock badge.
