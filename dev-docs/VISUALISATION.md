# Visualisation: hub architecture, pipeline strip, colour customisation

A design, built, except colour phase C4 (Part C).

Three parts of the dashboard, each with the reasons behind its shape, and the
rules every panel follows. The shapes a client reads from the hub, the routes,
the snapshot and the event stream, are specified in `VIZ_CONTRACT.md`. The code
is `epimemer/visualization/`, with the dashboard under its `frontend/`.

- **Part A, the viz hub.** A standalone process that owns the dashboard port;
  MCP server processes publish to it. It exists to remove a failure class: an
  embedded per-process viz server meant a stale MCP process could hold the
  port and show *its* empty in-memory store while the session the user was
  actually driving failed to bind and served nothing, silently.
- **Part B, the pipeline strip.** The knowledge graph takes most of the
  viewport; a narrow bottom strip shows one small glyph per Petri net,
  lighting up as data flows, with counters. Click a glyph to expand the full
  net.
- **Part C, colour customisation.** A dropdown of colour pickers for the
  chrome and text, persisted per viewer.

Decisions made with the user, not to be re-litigated:

1. Hub lifecycle: **both** auto-spawn (the first MCP server spawns a detached
   hub if none is running) **and** a CLI entry point for explicit start, stop
   and status.
2. Transport: **plain WebSocket**. MCP processes dial out to the hub. No
   message broker.
3. UI: a **session selector** dropdown; one session viewed at a time;
   disconnected sessions grey out. The default is a session that answers, then
   one listed but not answering, then a disconnected one, the most recent
   `last_event_at` first within each.
4. There is no embedded viz server. One code path. Hub down means no viz,
   with a loud stderr note from the MCP process.

## Rules every panel follows

The graph, the timeline, the pipeline strip, the live log and focus mode each
hold to these, so a rule is decided once here rather than in each panel's
design.

- **Ambient signal, deliberate detail.** A panel shows on its own that
  something happened: a tile glows, a counter ticks, a selector gains an entry.
  Detail opens only on a click. An arriving event never opens a panel or takes
  over the drawer: the agent makes on the order of ten tool calls per task, and
  a drawer that changed underneath the reader would be unreadable and would
  clobber a detail they opened on purpose.
- **The session, then the graph.** Switching session clears what a panel holds
  for the old one; only the hub's rings are replayed for the new one. Within a
  session, a record or a log entry from graph A never highlights into graph B.
- **Opacity is status, saturation is focus.** Both panels draw a retired node
  fainter by opacity, and focus mode dims what a retrieval did not return by
  desaturation (`RETRIEVAL_PROVENANCE.md` §4.1). One channel per meaning, so a
  retired node the retrieval returned and an active one it missed never arrive
  at the same appearance. Both panels take the two from the same functions in
  `theme.ts`, so they cannot come to disagree.
- **A highlight never fails silently.** It could in two ways: an id not in the
  current graph, where the lookup returns an empty collection and the highlight
  does nothing, and a node the type filter hides, where the highlight lands on
  something invisible. The highlight reports the ids it could not find, and
  clears the type filter only when the filter would hide something being
  highlighted; clearing it every time would undo a filter the user set.
- **Panel rules are pure functions.** Cytoscape cannot be instantiated under
  jsdom, which has no canvas, so every rule the graph panel follows is a pure
  function tested under vitest, and the modules that touch the DOM stay thin.
  The timeline keeps the same split for its geometry, query parsing and label
  layout, which are the parts most likely to be wrong.

---

# Part A: the viz hub

## A.0 Topology

- The hub is a **relay plus session registry** and holds no storage. Each MCP
  server process is a session that answers the hub's reads over its own
  socket, so reads execute inside the owning process. That is what makes
  in-process stores viewable: the in-memory backend, or SurrealDB on a
  `mem://` URL.
- Port contention resolves itself. If two hubs race to spawn, the second fails
  to bind, finds an Epimemer hub answering `/api/health` on the port and exits
  quietly; its spawner's client just retries connecting. A bind that fails
  against anything else exits with a stderr message saying the port is taken.

## A.1 Wire protocol (session ⇄ hub)

The messages are Pydantic models in `protocol.py`, imported by both the hub and
the session client so the shapes cannot drift (`VIZ_CONTRACT.md` §8). The
choices behind them:

- The hub never parses an event beyond routing it. It stamps `session_id` into
  the payload itself, so sessions never have to remember to, and a session
  sends `Register` again when it switches graph, so the registry follows the
  active graph without the hub reading event payloads.
- One message on `/ingest` is bounded by `INGEST_MAX_MESSAGE_BYTES` (16 MiB),
  named once and passed to both sides so neither guesses the other's limit. A
  message over it closes the socket, which takes the whole session off the
  hub, so a session replaces an answer that would not fit with an error naming
  the remedy (`VIZ_CONTRACT.md` §6) and stays connected.

## A.2 Hub server

What the route table in `VIZ_CONTRACT.md` §4 does not say:

- `GET /api/embeddings` is its own route rather than part of the snapshot,
  which would grow several times over to carry vectors.
- `GET /api/warnings` is a read, and the panel behind it is read-only
  (`EVENT_LOG.md` §12.4).
- The per-session rings the hub keeps are `EVENT_LOG.md` §4 (acts and
  warnings) and `RETRIEVAL_PROVENANCE.md` §3.2 (retrieval records).

## A.3 Session client (MCP side)

`hub_client.py` connects, registers, forwards every bus event and answers the
hub's reads. Each read runs inside one turn of the storage `graph_guard`, so the
reads behind one answer describe a single instant and no tool call runs between
them (`DEVELOPER_GUIDE.md`, *The active graph holds still*). The snapshot sends
only active nodes; historical ones are read in the same turn only to work out
the boundaries reflect would propose.

The client never blocks the MCP server: it reconnects with capped backoff for
ever and drops the events published while it is away, which browsers recover
from by refetching the snapshot (`VIZ_CONTRACT.md` §7.5). The first connection
failure writes one line to stderr, which Claude Code surfaces: "viz hub
unreachable at …; run `uv run epimemer-viz --status`". Later retries log at
debug.

## A.4 Backend label

`SessionInfo.backend` and `/api/graphs` carry the backend kind (`memory`,
`sqlite`, `surrealdb`, `postgres`). It is the cheapest half of the
silent-wrong-viz fix: `MCP: default (memory)` reads instantly as "wrong
server" where `MCP: default` does not.

## A.5 MCP server lifespan

With `EPIMEMER_VIZ_ENABLED` on, the server probes the hub's health and, when
none answers and autospawn is on, spawns one detached in a new process session,
so it outlives the MCP process. The MCP process never binds the viz port:
`EPIMEMER_VIZ_HOST` and `EPIMEMER_VIZ_PORT` describe the hub, and
`EPIMEMER_VIZ_ENABLED` means "publish to the hub". `use_graph` needs no
viz-specific code, since the client re-registers on `graph_switched`.

## A.6 `viz_status` MCP tool

The durable answer to "I opened the visualiser but can't find my graph". Asked
through the session the user is provably talking to, it names the hub URL,
whether the hub can see this session, and the session to select in the
dropdown.

## A.7 Frontend

- The header's `reflect n/m` badge, amber once a reflect is due, is seeded from
  the `reflect` field on `/api/graphs` and then moved by
  `reflect_counter_updated` events. Events alone would leave a browser that
  connected to a graph already at 7 of 10 showing nothing until the next store.
- The hub sends a browser only the session it subscribed to, and on top of
  that the `graph_switched` and `reflect_counter_updated` handlers act only
  when the event's session is the selected one.
- The live log and the read-only warnings panel are `EVENT_LOG.md`.

---

# Part B: the pipeline strip

## B.0 Layout

The knowledge graph is the star; pipelines are ambient awareness. The strip
sits under the graph at a fixed height and collapses from the header.

- One **tile** per `pipeline_name`. Tiles persist across runs and across
  snapshot loads; there is no shared "current pipeline" that a new
  `pipeline_started` could clobber.
- Each tile: a **mini glyph** of the net topology (places as dots, transitions
  as small rects, no text labels), the pipeline name, and a status line. While
  running, the border glows and the firing transition and token-holding places
  light up. A failed run gets a red border, and the error is in the tile's
  tooltip.
- **Click a tile** for the detail view: the full Graphviz rendering with labels
  and per-place token badges, live-updating, as an overlay that expands upward
  over the graph so the strip stays visible. Esc or × closes it.

Tiles aggregate rather than drawing each event, because events are emitted at
storage-write granularity: one `store_decomposition` publishes hundreds
(`EVENT_LOG.md` §3).

## B.1 Tiles and topology

A tile appears on the first pipeline event for its name, and its glyph once a
`pipeline_started` brings the topology. When a transition lights is
`DEVELOPER_GUIDE.md`, *Petri net visualisation*. `reflect` is not a Petri net
but declares a synthetic linear topology, so it gets a tile and phase-by-phase
lighting like the rest. Pre-seeding real topologies server-side is out of
scope: net builders need runtime inputs.

## B.2 The "how much data" number

`tokens_updated` carries absolute per-place counts. The tile's number is the
cumulative sum of positive deltas across updates, per place: a robust proxy for
"tokens pushed through" that does not require knowing which places are
terminal. While running the tile shows that number; after completion, the run
count and the last duration.

## B.3 Glyph rendering

The glyph is drawn by the same Graphviz WASM layout as the detail view, in a
mini mode with no labels and tighter spacing, so it is a genuine miniature of
what clicking reveals. Both sizes carry the same element ids, so one colour
overlay works on both. The SVG is rendered once per topology and cached.

## B.4 Behaviour

- Two pipelines in quick succession animate their tiles independently, and an
  open detail view follows only its own pipeline.
- On a WebSocket gap, running tiles are marked stale ("…", grey pulse) until
  the pipeline's next start, firing, completion or failure event.
- Run counts and items processed are session-lifetime counters: a snapshot
  refresh does not reset them.
- Switching sessions clears the strip; pipeline history is not replayed.

---

# Part C: colour customisation

A dropdown of colour pickers for the parts of the dashboard the user actually
looks at (timeline text, detail text, every background), with the choices
persisted per viewer. Phase C4, in `PROPOSED_FEATURES.md`, would add export and
import, preset themes and settable semantic hues (C.6).

## C.0 Three colour systems

1. **Tailwind utility classes**: the chrome (header, toolbars, panels,
   buttons, chips, the drawer). They are compiled at build time, so a colour
   picker cannot touch them, which is why C.1 exists.
2. **The runtime `Palette`** (`theme.ts`): the neutral fields read at render
   time by the three *drawn* surfaces that Tailwind cannot reach: the cytoscape
   canvas, the timeline SVG, and the Graphviz DOT for the pipeline detail.
3. **The runtime `SemanticPalette`** (`theme.ts`): the hues that say what kind
   of thing something is (C.6).

Most of the work is giving systems 1 and 2 a single source of truth.

## C.1 Token model

The raw greys are replaced by nine **semantic tokens** in `tokens.css`, each
backed by a CSS custom property: four surfaces, a border and four weights of
text. About 22 distinct grey classes collapse to nine because most were the
light and dark halves of the same idea, and nine is about the right number of
knobs: a picker per raw class would be 22 controls that mostly move together.

**The drawn surfaces derive from the same tokens**: a node label is
`--text-primary`, an axis plate is `--surface-chrome`, and so on. Genuinely
draw-only fields (the Graphviz set) stay as hex literals per theme in
`theme.ts`.

Tokens hold sRGB channels rather than hex, because some class occurrences
carry an alpha modifier and `rgb(var(--token) / <alpha-value>)` is the only
form Tailwind can compose one with. The defaults are written in `tokens.css`
and again in `theme.ts`, which needs values where no stylesheet is loaded; a
test asserts the pair agrees.

The structural test in `layout.test.ts` is what stops the migration rotting
back: no raw grey (or white) `bg-`, `text-` or `border-` class survives in
`index.html` or the TS modules. A new panel written the old way would still
look right in both themes while ignoring the user's settings entirely.

## C.2 Mechanism

Tailwind's colours are extended with names backed by the variables, so
`bg-surface-chrome` reads `var(--surface-chrome)`, and the `dark:` variants
disappear from the markup: the dark theme is a different set of values for
the same variables, declared once in `tokens.css` under `:root` and `.dark`.

**The one performance trap.** The palette is read on every render, and the
timeline re-renders on every step of a pan. Reading the variables through
`getComputedStyle` in that loop is forced-reflow jank, so they are read once
per theme or override change into a cached `Palette`, never per render.

## C.3 Where the settings live

**`localStorage`, keyed per theme**, not the backend.

This is the opposite call from `reference_time` (`TIMELINE_VISUALISATION.md`
§6.4), deliberately. A fictional timeline's present is a fact about the
material, so it belongs in the graph where every client and the agent can
see it. A colour preference is a property of the viewer: two people looking
at the same graph should be able to disagree about it, and one of them
changing it should not rewrite anything the other reads.

Overrides are stored per theme, `{ light: {token: hex}, dark: {token: hex} }`.
A single shared map would mean choosing a colour in dark mode silently
destroying light mode.

```
epimemer.palette → {"version":1,"light":{"--surface-chrome":"#e5e7eb"},"dark":{}}
```

Only overridden tokens are stored, so a default that changes later still
reaches users who never touched it. `version` lets a future rename migrate
rather than silently drop. Unreadable or unparseable storage falls back to
the defaults.

## C.4 The picker UI

A dropdown from a paintbrush button beside the theme toggle, grouped into
surfaces and text. Each row: a native `<input type="color">`, the hex, a
per-token reset, and for text tokens a live contrast ratio against
`--surface-chrome`. Native rather than a custom picker: it is one element, it
is accessible, it gets the platform's eyedropper for free, and a hand-rolled
HSV wheel is a lot of code that has nothing to do with this project. Changes
apply live on `input` and persist on `change`, so dragging through a gradient
does not write to storage on every step.

## C.5 Contrast, and the way back

A colour picker over the whole UI can render the UI unusable.

- **Live WCAG ratio** beside every text token, warning below 4.5:1 for small
  text.
- **The reset control is not themeable.** "Reset all" keeps hard-coded inline
  colours, so it is legible no matter what the user has done.
- A query parameter (`?palette=reset`) is a second way back for the case
  where the button is somehow unreachable.

## C.6 The semantic hues: shared across panels, not customisable

The semantic hues are fixed: node types, edge types, contradiction red,
selection pink, the `pending` colour of proposals, and the pipeline's active,
completed and failed colours. They are how the graph says what kind of thing
you are looking at.

One palette, `SemanticPalette` in `theme.ts`, is shared by the graph panel and
the timeline, so one window cannot give two answers to "what colour is a
fact". The set was validated in both themes with a perceptual checker: OKLCH
lightness band, chroma floor, colour-vision-deficiency ΔE separation, and
contrast against the surface. The values live in `theme.ts`; how the timeline
uses them is `TIMELINE_VISUALISATION.md` §13.3. The graph panel's selection
border and highlight are still its own colours, in `graph-panel.ts`.

Choices worth their reasons:

- **Topic is green**, the furthest validated hue from fact's blue and
  inference's violet.
- **Contradiction keeps red and the now-line gives it up.** The now-line is
  chrome, an annotation on the axis rather than a thing in the data, so it is
  a dashed neutral rule (`--text-muted` stroke, `--text-secondary` label).
  Chrome stays subordinate to data throughout: no bold, no semantic hue on
  axis furniture.
- **Source strips are outside this palette.** A per-source strip is always
  direct-labelled, so its colour carries no meaning and must not compete with
  one that does. It draws from a plain rotation, `stripHue`, whose only
  requirement is that adjacent strips differ.
- **`historical` is defined and drawn by neither panel**, since status is
  drawn by opacity (*Rules every panel follows*).

Making the semantic hues settable (C4) needs an answer to "what happens when
two of them are set to the same value", which is a different question from
"let me darken this background".

## C.7 Open questions

1. **Should font size be in scope?** The same dropdown is the natural home
   for it, and "make the labels bigger" is a more common request than "make
   them green". It would change `CARD_LINE_HEIGHT`, `LABEL_HEIGHT` and the
   character budget, which the label layout already takes as inputs, so it is
   cheaper than it looks. Out of scope as written.
2. **Per-graph palettes?** Colouring a fiction graph differently from a real
   one is genuinely useful and would argue for the backend after all. Not
   proposed; it would supersede C.3.
