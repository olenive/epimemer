# Visualisation: hub architecture, pipeline strip, colour customisation

How the dashboard is put together, and the decisions that bind a change to it.
The shapes a client reads from the hub (the routes, the snapshot and the event
stream) are `VIZ_CONTRACT.md`; the live log and the warnings panel are
`EVENT_LOG.md`; the timeline panel is `TIMELINE_VISUALISATION.md`. The code is
`epimemer/visualization/`, with the dashboard under its `frontend/`.

Decisions made with the user, not to be re-litigated:

1. Hub lifecycle: the first MCP server spawns a detached hub if none is
   running, **and** the `epimemer-viz` CLI starts, stops and reports on one
   explicitly.
2. Transport: **plain WebSocket**, with MCP processes dialling out to the hub.
   No message broker.
3. UI: a **session selector**; one session viewed at a time; disconnected
   sessions grey out. The default is a session that answers, then one listed
   but not answering, then a disconnected one, the most recent
   `last_event_at` first within each.
4. No viz server is embedded in an MCP process, and the MCP process never
   binds the port: a stale MCP process holding it would show its own empty
   store while the session the user is driving served nothing. Hub down means
   no viz, with one stderr line from the MCP process.

## Rules every panel follows

The graph, the timeline, the pipeline strip, the live log and focus mode each
hold to these.

- **Ambient signal, deliberate detail.** A panel shows on its own that
  something happened: a tile glows, a counter ticks, a selector gains an entry.
  Detail opens only on a click. An arriving event never opens a panel or takes
  over the drawer, because the agent makes on the order of ten tool calls per
  task and a drawer that changed underneath the reader would clobber a detail
  they opened on purpose.
- **The session, then the graph.** Switching session clears what a panel holds
  for the old one; only the hub's rings are replayed for the new one. Within a
  session, a record or a log entry from graph A never highlights into graph B.
- **Opacity is status, saturation is focus.** Both panels draw a retired node
  fainter by opacity, and focus mode dims what a retrieval did not return by
  desaturation (`RETRIEVAL_PROVENANCE.md` §4.1), so a retired node the
  retrieval returned and an active one it missed never look alike. Both panels
  desaturate through the same `desaturate` in `theme.ts`.
- **A highlight never fails silently.** It reports the ids the current graph
  does not hold, and clears the type filter only when the filter would hide
  something being highlighted, since clearing it every time would undo a
  filter the user set.
- **Panel rules are pure functions.** Cytoscape cannot be instantiated under
  jsdom, which has no canvas, so every rule the graph panel follows is a pure
  function tested under vitest, and the modules that touch the DOM stay thin.
  The timeline keeps the same split for its geometry, query parsing and label
  layout.

---

# Part A: the viz hub

A standalone process that owns the dashboard port; MCP server processes
publish to it.

## A.0 Topology

- The hub is a **relay plus session registry** and holds no storage. Each MCP
  server process is a session that answers the hub's reads over its own
  socket, so reads execute inside the owning process. That is what makes
  in-process stores viewable: the in-memory backend, or SurrealDB on a
  `mem://` URL.
- If two hubs race to spawn, the second fails to bind, finds an Epimemer hub
  answering `/api/health` on the port and exits; its spawner's client retries
  connecting. A bind that fails against anything else exits with a stderr
  message saying the port is taken.

## A.1 Wire protocol (session ⇄ hub)

The messages are Pydantic models in `protocol.py`, imported by both the hub and
the session client so the shapes cannot drift (`VIZ_CONTRACT.md` §8).

- The hub never parses an event beyond routing it. It stamps `session_id` into
  the payload itself, and a session sends `Register` again when it switches
  graph, so the registry follows the active graph without the hub reading
  event payloads.
- One message on `/ingest` is bounded by `INGEST_MAX_MESSAGE_BYTES` (16 MiB),
  named once and passed to both sides. A message over it closes the socket,
  which takes the whole session off the hub, so a session replaces an answer
  that would not fit with an error naming the remedy (`VIZ_CONTRACT.md` §6) and
  stays connected.

## A.2 Hub server

What the route table in `VIZ_CONTRACT.md` §4 does not say:

- `GET /api/embeddings` is its own route, because vectors would make the
  snapshot several times larger.
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
`sqlite`, `surrealdb`, `postgres`), and the header shows it beside the graph,
because `MCP: default (memory)` reads instantly as "wrong server" where
`MCP: default` does not.

## A.5 MCP server lifespan

With `EPIMEMER_VIZ_ENABLED` on, the server probes the hub's health and, when
none answers and `EPIMEMER_VIZ_AUTOSPAWN` is on, spawns one detached in a new
process session, so it outlives the MCP process. `EPIMEMER_VIZ_HOST` and
`EPIMEMER_VIZ_PORT` describe the hub, and `EPIMEMER_VIZ_ENABLED` means "publish
to the hub". `use_graph` needs no viz-specific code, since the client
re-registers on `graph_switched`.

## A.6 `viz_status` MCP tool

The answer to "I opened the visualiser but can't find my graph". Asked through
the session the user is talking to, it names the hub URL, whether the hub is
reachable and lists this session as connected, and the `session_id`, backend
and active graph to pick in the session selector.

## A.7 Frontend

- The header's `reflect n/m` badge, amber once a reflect is due, is seeded from
  the `reflect` field on `/api/graphs` and then moved by
  `reflect_counter_updated` events, because events alone would leave a browser
  that connected to a graph already at 7 of 10 showing nothing until the next
  store.
- The hub sends a browser only the session it subscribed to, and on top of
  that the `graph_switched` and `reflect_counter_updated` handlers act only
  when the event's session is the selected one.

---

# Part B: the pipeline strip

## B.0 Layout

The knowledge graph takes most of the viewport; pipelines are ambient
awareness. The strip sits under the graph at a fixed height and collapses from
the header's **Pipelines** button.

- One **tile** per `pipeline_name`. Tiles persist across runs and across
  snapshot loads, so there is no shared "current pipeline" for a new
  `pipeline_started` to clobber.
- Each tile: a **mini glyph** of the net (places as dots, transitions as small
  rects, no labels), the pipeline name and a status line. While running, the
  border glows and the firing transition and token-holding places light up. A
  failed run gets a red border, with the error in the tile's tooltip.
- **Click a tile** for the detail view: the full Graphviz rendering with labels
  and per-place token badges, live-updating, as an overlay on the graph half so
  the strip stays visible. Esc or × closes it.

Tiles aggregate rather than drawing each event, because events are emitted at
storage-write granularity: one `store_decomposition` publishes hundreds
(`EVENT_LOG.md` §3).

## B.1 Tiles and topology

A tile appears on the first pipeline event for its name, and its glyph once a
`pipeline_started` brings the topology. When a transition lights is
`DEVELOPER_GUIDE.md`, *Petri net visualisation*. `reflect` is not a Petri net
but declares a synthetic linear topology (`phase_events.py`), so it gets a tile
and phase-by-phase lighting like the rest. Topologies cannot be pre-seeded
server-side, because the net builders need runtime inputs.

## B.2 The "how much data" number

`tokens_updated` carries absolute per-place counts. The tile's number is the
cumulative sum of positive deltas across updates, per place: a proxy for
"tokens pushed through" that needs no knowledge of which places are terminal.
While running the tile shows that number; after completion, the run count and
the last duration.

## B.3 Glyph rendering

The glyph is drawn by the same Graphviz WASM layout as the detail view, in a
mini mode with no labels and tighter spacing, so it is a genuine miniature of
what clicking reveals. Both sizes carry the same element ids, so one colour
overlay works on both. Each SVG is rendered once per topology and reused.

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

A dropdown of colour pickers for the dashboard's neutral colours (every
surface, the border and the text), with the choices persisted per viewer.
Export and import, preset themes and settable semantic hues are phase C4 in
`PROPOSED_FEATURES.md`.

## C.0 Three colour systems

1. **Tailwind utility classes**: the chrome (header, toolbars, panels,
   buttons, chips, the drawer). They are compiled at build time, so a colour
   picker cannot touch them, which is why C.1 exists.
2. **The runtime `Palette`** (`theme.ts`): the neutral fields read at render
   time by the three *drawn* surfaces that Tailwind cannot reach: the cytoscape
   canvas, the timeline SVG, and the Graphviz DOT for the pipeline detail.
3. **The runtime `SemanticPalette`** (`theme.ts`): the hues that say what kind
   of thing something is (C.6).

## C.1 Token model

The neutral colours are nine **semantic tokens** in `tokens.css`, each backed
by a CSS custom property: four surfaces, a border and four weights of text.
Nine, because most raw grey classes were the light and dark halves of the same
idea, and a picker per raw class would be many controls that mostly move
together.

**The drawn surfaces derive from the same tokens**: a node label is
`--text-primary`, an axis plate is `--surface-chrome`, and so on. Draw-only
fields (the Graphviz set) stay as hex literals per theme in `theme.ts`.

Tokens hold sRGB channels rather than hex, because some classes carry an alpha
modifier and `rgb(var(--token) / <alpha-value>)` is the only form Tailwind can
compose one with. The defaults are written in `tokens.css` and again in
`theme.ts`, which needs values where no stylesheet is loaded; `theme.test.ts`
asserts the pair agrees.

The structural test in `layout.test.ts` fails on any raw grey (or white) `bg-`,
`text-` or `border-` class in `index.html` or the TS modules, because a panel
written that way would look right in both themes while ignoring the user's
settings entirely.

## C.2 Mechanism

Tailwind's colours are extended with names backed by the variables, so
`bg-surface-chrome` reads `var(--surface-chrome)`, and the markup needs no
`dark:` variant for a neutral: the dark theme is a different set of values for
the same variables, declared once in `tokens.css` under `:root` and `.dark`.

**The one performance trap.** The palette is read on every render, and the
timeline re-renders on every step of a pan. Reading the variables through
`getComputedStyle` in that loop forces a reflow each time, so they are read
once per theme or override change into a cached `Palette`, never per render.

## C.3 Where the settings live

**`localStorage`, keyed per theme**, not the backend. A colour preference is a
property of the viewer: two people looking at the same graph should be able to
disagree about it, and one of them changing it should not rewrite anything the
other reads. This is the opposite call from `reference_time`
(`TIMELINE_VISUALISATION.md` §6.4), which is a fact about the material and so
lives in the graph.

Overrides are stored per theme, `{ light: {token: hex}, dark: {token: hex} }`,
because a single shared map would let a colour chosen in dark mode overwrite
light mode.

```
epimemer.palette → {"version":1,"light":{"--surface-chrome":"#e5e7eb"},"dark":{}}
```

Only overridden tokens are stored, so a default that changes later still
reaches users who never touched it. `version` is there so a later rename can
migrate; a version this code does not know is discarded rather than read.
Unreadable or unparseable storage falls back to the defaults.

## C.4 The picker UI

A dropdown from a paintbrush button beside the theme toggle, grouped into
surfaces and text. Each row: a native `<input type="color">`, the hex, a
per-token reset, and for text tokens a live contrast ratio against
`--surface-chrome`. Native, because it is one accessible element and brings the
platform's eyedropper. Changes apply live on `input` and persist on `change`,
so dragging through a gradient does not write to storage on every step.

## C.5 Contrast, and the way back

A colour picker over the whole UI can render the UI unusable.

- **Live WCAG ratio** beside every text token, warning below 4.5:1 for small
  text.
- **The reset control is not themeable.** "Reset all" keeps hard-coded inline
  colours, so it is legible whatever the user has done.
- `?palette=reset` clears the stored choices before anything is drawn, a
  second way back for when the button is unreachable.

## C.6 The semantic hues: shared across panels, not customisable

The semantic hues are fixed: node types, edge types, contradiction red,
selection pink, the `pending` colour of proposals, and the pipeline's active,
completed and failed colours. They are how the graph says what kind of thing
you are looking at, and settable hues would first need an answer to "what
happens when two of them are set to the same value".

One palette, `SemanticPalette` in `theme.ts`, is shared by the graph panel and
the timeline, so one window cannot give two answers to "what colour is a
fact". The set passes, in both themes, an OKLCH lightness band, a chroma floor,
colour-vision-deficiency ΔE separation and contrast against the surface; a
changed hue needs the same checks. How the timeline uses the hues is
`TIMELINE_VISUALISATION.md` §13.3. The graph panel's selection border and
highlight are its own colours, in `graph-panel.ts`.

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
- **`historical` is in the palette and neither panel draws with it**, since
  status is drawn by opacity (*Rules every panel follows*).
