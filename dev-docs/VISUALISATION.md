# Visualization: Hub Architecture, Pipeline Strip, Colour Customisation

Implementation plans. **Parts A and B are built and merged** (written
2026-07-24) and are kept as the design record. **Part C — colour
customisation — is designed and not built** (written 2026-08-08); it starts
below Part B.

One piece of Part C went in ahead of the rest: **C.6's semantic palette is
built** (2026-08-12, ISSUES.md #56). It was not a picker feature — the two
panels disagreed about what colour a fact is, and fixing that meant giving the
hues a single per-theme home. C.1's token migration, the store and the picker are all
still unbuilt.

The failure this was written to kill: a stale MCP process holds the fixed viz
port, so the browser shows *its* empty in-memory store while the session the
user is actually driving fails to bind and serves no visualization at all —
silently, with the bind error going only to a log file nobody reads. It was
Issue 24 in ISSUES.md at the time; that entry is resolved and deleted, so this
document is now its description. (#16, referenced below, is still open.)

Two independent work packages:

- **Part A — Multi-client viz hub.** Replace the embedded per-process viz server
  with a standalone hub that MCP server processes publish to. Removes the
  port-contention failure class structurally — nothing but the hub ever binds
  the port, so there is no race to lose — and unblocks the multi-client
  scenario (#16's trigger).
- **Part B — Pipeline strip.** Knowledge graph takes most of the viewport; a
  narrow bottom strip shows one small glyph per Petri net, lighting up as data
  flows, with counters. Click a glyph to expand the full net.

Recommended order: **A first, then B** (B's tiles are keyed per session, which A
introduces). B *can* be built standalone against the current embedded server if
priorities change — session keying is additive.

Decisions already made with the user (do not re-litigate):

1. Hub lifecycle: **both** auto-spawn (first MCP server spawns a detached hub if
   none is running) **and** a CLI entry point for explicit start/stop/status.
2. Transport: **plain WebSocket** — MCP processes dial out to the hub. No
   Redis/NATS (supersedes an earlier sketch that assumed a message broker).
3. UI: **session selector** dropdown; one session viewed at a time; most
   recently active is the default; disconnected sessions grey out.
4. The embedded viz server is **fully replaced**. One code path. Hub down ⇒ no
   viz, with a loud stderr note from the MCP process.

Style constraints (from CLAUDE.md): functional style, avoid classes with `self`
(Pydantic `BaseModel` for data is fine — the existing `WebSocketRelay` /
`_ConnectionState` classes are grandfathered; new code should prefer closures
over classes). Type annotations without over-complication.

---

## Starting point — the architecture this replaced (historical)

> **None of this is current.** It is the *before* picture the plan below was
> written against, kept so the design decisions have something to argue with.
> `ws_server.py`, `pipeline-panel.ts` and `split-pane.ts` no longer exist; the
> MCP process no longer binds a port. For what the code looks like now, read
> Parts A and B — they were followed closely and their specs still match
> (`protocol.py`, the hub routes, `viz_status`, the frontend file plan were all
> checked against the code on 2026-07-29). Line numbers throughout this document
> are plan-time coordinates and have drifted.

- `epimemer/mcp/server.py:86-141` — lifespan conditionally instruments storage
  (`instrument_storage`) with an `InProcessEventBus`, builds the viz Starlette
  app (`create_app`), and runs uvicorn **inside the MCP process** on
  `config.viz_port` (default 8765, `epimemer/mcp/config.py:40-42`,
  env `EPIMEMER_VIZ_*`). Bind failure is swallowed into a log-file warning
  (`_run_viz`, server.py:112-121) — the silent-wrong-viz bug described at the
  top.
- `epimemer/visualization/ws_server.py` — Starlette app: `/` + static,
  `/ws` (browser WebSocket, per-connection seq numbers, graph subscription
  filtering), `/api/graphs`, `/api/snapshot?graph=X` (reads via
  `storage.viz_list_nodes/viz_list_edges` on the **raw** pre-instrumented
  storage).
- `epimemer/visualization/events.py` — Pydantic event contract
  (`NodeView`/`EdgeView`, graph events, pipeline events, `GraphSwitched`).
  This contract is good; Part A only adds an envelope + session identity
  around it, Part B consumes it unchanged.
- `epimemer/visualization/event_bus.py` — in-process pub/sub. Stays; the hub
  client becomes one more subscriber.
- `epimemer/visualization/instrumented_storage.py`, `instrumented_executor.py`
  — event producers. Unchanged.
- Frontend (`epimemer/visualization/frontend/src/`): `main.ts` wires a
  left/right split (`split-pane.ts`) of pipeline panel (Graphviz WASM SVG,
  `pipeline-panel.ts` — renders **one pipeline at a time**, each
  `PipelineStarted` overwrites `container.innerHTML`) and knowledge graph panel
  (`graph-panel.ts`, Cytoscape). Built bundle is committed under
  `epimemer/visualization/static/` (`cd frontend && npm run build`).
- Pipelines currently wired through `_run_net` (`epimemer/mcp/tools.py:42-69`):
  `segmentation:semantic`, `segmentation:paragraph`, `edge_creation`,
  `retrieval`. `reflect` is not a Petri net today.

---

# Part A — Multi-client viz hub

## A.0 Topology

```
browser ──ws──► ┌─────────────┐ ◄──ws (dial-out)── MCP server (session A, surrealdb/memory)
browser ──ws──► │   viz hub    │ ◄──ws (dial-out)── MCP server (session B, mem://default)
                │  :8765       │
                │  static UI   │        Hub owns the port. MCP processes never bind it.
                └─────────────┘        Stale MCP orphans ⇒ dead sessions, not wrong UIs.
```

- The hub is a **relay + session registry**. It holds no storage and no
  visualization logic.
- Each MCP server process is a **session**: it registers, publishes events, and
  answers snapshot/graph-list RPCs over its own socket (this is what makes
  `mem://` backends viewable — reads execute inside the owning process).
- Port contention self-resolves: if two hubs race to spawn, the second fails to
  bind and exits; its spawner's client just retries connecting.

## A.1 Wire protocol (session ⇄ hub)

New module `epimemer/visualization/protocol.py` — Pydantic models, shared by hub
and client:

```python
class SessionInfo(BaseModel):
    session_id: str          # uuid4, generated by the MCP process at startup
    pid: int
    backend: str             # "memory" | "surrealdb"  (see A.4 backend label)
    active_graph: str
    started_at: datetime

class Register(BaseModel):       # session → hub, first message on connect
    type: Literal["register"] = "register"
    info: SessionInfo

class PublishEvent(BaseModel):   # session → hub
    type: Literal["event"] = "event"
    payload: dict                # serialized AnyEvent (events.py), unmodified

class RpcRequest(BaseModel):     # hub → session
    type: Literal["rpc_request"] = "rpc_request"
    request_id: str
    method: Literal["list_graphs", "snapshot"]
    params: dict                 # snapshot: {"graph": str}

class RpcResponse(BaseModel):    # session → hub
    type: Literal["rpc_response"] = "rpc_response"
    request_id: str
    result: dict | None = None
    error: str | None = None
```

Notes:

- `PublishEvent.payload` is the existing event JSON — the `events.py` contract
  is untouched. The hub injects `"session_id"` into the payload before fanning
  out to browsers (browsers need it for routing; sessions shouldn't have to
  remember to stamp it).
- Sessions also send `Register` again (same `session_id`) whenever
  `active_graph` changes (piggyback on the existing `GraphSwitched` event
  emission point), so the hub's registry stays current without parsing event
  payloads.
- RPC timeout: hub side, 10s per request → HTTP 504 to the browser.

## A.2 Hub server

New module `epimemer/visualization/hub.py`. Reuses the relay logic from
`ws_server.py` (move/adapt; `ws_server.py` is deleted at the end — see A.8).

Starlette app, functional construction (`create_hub_app() -> Starlette`):

- `GET /` + static mount — serve the existing frontend bundle from
  `visualization/static/` (unchanged mechanism, ws_server.py:116-117,165-174).
- `WS /ws` — browser connections. Keep the existing per-connection sequence
  numbers and subscription mechanism (ws_server.py:36-113), with subscription
  extended from graphs to `{"subscribe": {"session": "<id>", "graphs": [...]}}`
  (browser subscribes to one session at a time per the session-selector
  decision; `graphs: null` = all graphs of that session).
- `WS /ingest` — session connections. Loop: first message must be `Register`
  (else close 1002); then handle `PublishEvent` (inject `session_id`, forward
  to browser fan-out) and `RpcResponse` (resolve pending future by
  `request_id`). On disconnect: mark session disconnected (keep it listed,
  greyed, for 5 minutes, then drop) and broadcast a `session_disconnected`
  system message to browsers; on register broadcast `session_connected`.
- `GET /api/health` — `{"ok": true, "pid": ..., "version": ...}`. Used by
  auto-spawn probing and `--status`.
- `GET /api/sessions` — list of `SessionInfo` + `connected: bool` +
  `last_event_at`.
- `GET /api/graphs?session=<id>` — RPC `list_graphs` to that session; response
  shape stays `{"graphs": [...], "active_graph": ...}` plus `"backend"`.
- `GET /api/snapshot?session=<id>&graph=<g>` — RPC `snapshot`; response shape
  unchanged (`{"graph", "nodes", "edges"}`) so `graph-panel.ts` needs no
  changes.

State: a plain dict `session_id -> {"ws": WebSocket, "info": SessionInfo,
"pending_rpcs": dict[str, asyncio.Future], "connected": bool, "last_event_at":
datetime}` captured in closures. Single asyncio event loop; no locks needed
beyond what the existing relay already does.

`__main__` entry (`python -m epimemer.visualization.hub`):

- `run` (default): write pidfile, bind `EPIMEMER_VIZ_HOST:EPIMEMER_VIZ_PORT`
  (reuse the existing config env names — they now describe the *hub*), serve.
  If the bind fails: if a health probe of the port answers as an epimemer hub,
  exit 0 quietly (lost the spawn race — fine); otherwise exit 1 with a clear
  stderr message (port taken by a stranger).
- `--status`: probe health + read pidfile, print hub pid, sessions, exit 0/1.
- `--stop`: read pidfile, SIGTERM, wait briefly, report.
- Pidfile: `~/.epimemer/viz-hub.pid` (create `~/.epimemer/` if needed). Stale
  pidfile (no such process) is overwritten silently.
- Also add a console script `epimemer-viz = epimemer.visualization.hub:main` in
  `pyproject.toml` so `uv run epimemer-viz --status` works.

## A.3 Session client (MCP side)

New module `epimemer/visualization/hub_client.py`:

```python
async def start_hub_client(
    bus: InProcessEventBus,
    raw_storage: StorageBackend,      # pre-instrumentation, for RPC reads
    info: SessionInfo,
    hub_url: str,                     # ws://host:port/ingest
) -> Callable[[], Awaitable[None]]:   # returns async stop()
```

Behaviour:

- Background task: connect (use the `websockets` library — already a transitive
  dependency; add it as a direct dependency in `pyproject.toml`), send
  `Register`, then concurrently (a) forward every bus event
  (`bus.subscribe`) as `PublishEvent`, (b) answer `RpcRequest`s.
- RPC handlers run **in this process** against `raw_storage`:
  - `list_graphs` → `await raw_storage.list_databases()` + `current_database`
    + backend label (A.4).
  - `snapshot` → `viz_list_nodes`/`viz_list_edges` + `node_to_view`/
    `edge_to_view` (move this assembly out of `ws_server.create_app.api_snapshot`
    into a small shared function, e.g. `visualization/snapshot.py`, so hub_client
    is its only caller after ws_server.py is deleted).
  - Serialize RPC reads with an `asyncio.Lock` shared with nothing else *yet* —
    but note in a comment this is the same shared-connection hazard as
    ISSUES.md #16; the lock only prevents two concurrent *viz* reads from
    interleaving their `use()` switches. #16 proper (viz read racing a tool
    call) remains deferred; the hub makes it no worse and its RPC handler is
    where the eventual fix (dedicated read connection for surrealdb) will land.
- Reconnect loop with capped exponential backoff (1s → 30s), forever. Events
  published while disconnected are **dropped** (browsers recover via
  snapshot refresh — the existing gap-detection → Refresh mechanism in
  `main.ts:159-161` already handles missed events).
- First connection failure logs **one** `logger.error` *and writes one line to
  stderr* (Claude Code surfaces MCP stderr): "viz hub unreachable at ...; run
  `uv run epimemer-viz --status`". Subsequent retries log at debug.
- `stop()` cancels the task and closes the socket; called from the lifespan
  `finally`.

## A.4 Backend label

`SessionInfo.backend` and `/api/graphs` need a human-readable backend kind.
Per the project's explicit-protocol preference (no `hasattr` probing): add
`backend_name: str` as a property to the `StorageBackend` protocol and both
implementations (`"memory"` for `InMemoryStorage`, `"surrealdb"` for
`SurrealDBStorage`). Every backend implements it. This is also what lets the UI
name the backend it is showing — the cheapest half of the silent-wrong-viz fix,
since `MCP: default (in-memory)` reads instantly as "wrong server" where
`MCP: default` does not.

## A.5 MCP server changes (`epimemer/mcp/server.py`)

Replace lines 86-128 and the `finally` teardown:

- Keep: `create_event_bus()`, `instrument_storage(storage, event_bus)`.
- Remove: `create_app`, uvicorn config/server, `_run_viz`, the
  `viz_server.should_exit` teardown. The MCP process **never binds the viz
  port again**.
- Add, when `config.viz_enabled`:
  1. Build `SessionInfo` (uuid4, `os.getpid()`, `storage.backend_name`,
     `storage.current_database`, now).
  2. **Auto-spawn probe**: `GET http://{viz_host}:{viz_port}/api/health`
     (0.5s timeout, use `aiohttp` which is already a dependency). If no
     healthy hub and `config.viz_autospawn` (new setting, default `True`, env
     `EPIMEMER_VIZ_AUTOSPAWN`): spawn
     `[sys.executable, "-m", "epimemer.visualization.hub"]` with
     `subprocess.Popen(..., start_new_session=True, stdin/stdout/stderr to
     DEVNULL or the epimemer log file)` so it survives the MCP process. Then
     poll health up to ~3s.
  3. `stop_client = await start_hub_client(bus, raw_storage, info, ws_url)`.
  4. Log (info) the hub URL — and see A.6 for surfacing it to the user.
- `use_graph` tool: after a successful switch, besides the existing
  `GraphSwitched` event, the hub client re-sends `Register` with the new
  `active_graph` (simplest: hub_client subscribes to the bus and re-registers
  whenever it sees a `graph_switched` event — no new coupling in tools code).

## A.6 `viz_status` MCP tool

Small new tool in `epimemer/mcp/server.py`/`tools.py` so the user can always
ask *through the session they're provably talking to*:

```
viz_status() -> {
  "hub_url": "http://127.0.0.1:8765",
  "hub_reachable": true,
  "connected": true,            # this session's ingest socket state
  "session_id": "...",
  "backend": "surrealdb",
  "active_graph": "memory",
  "sessions_on_hub": 2          # from /api/health or /api/sessions
}
```

This is the durable answer to "I opened the visualizer but can't find my
graph": the tool names the session to select in the UI dropdown.

## A.7 Frontend changes

- `api.ts`: `fetchSessions()`; `fetchGraphs(sessionId)`,
  `fetchSnapshot(sessionId, graph)` gain the session param.
- `main.ts`:
  - New **session selector** in the header (populated from `/api/sessions`,
    refreshed on `session_connected`/`session_disconnected` ws messages).
    Option label: `{backend}:{active_graph} (pid {pid})`. Disconnected
    sessions stay listed but greyed/disabled. Default selection: most recent
    `last_event_at`.
  - Selecting a session: send the extended subscribe message, re-fetch graphs,
    load snapshot — the existing `switchViewedGraph` flow, session-scoped.
  - Header shows `MCP: {active_graph} ({backend})` for the selected session
    — the UI half of the backend label (A.4): it is what makes an empty
    in-memory store legible as the wrong one. *Added later:* a `reflect n/m`
    badge beside it, amber once a reflect is due — seeded from the `reflect`
    field on `/api/graphs` and then moved by `reflect_counter_updated` events.
    Seeding matters: events alone would leave a browser that connected to a
    graph already at 7 of 10 showing nothing until the next store.
  - `graph_switched` handler (main.ts:164-174): only act if the event's
    `session_id` matches the selected session.
- `events.ts`: pass through `session_id`; drop events from non-selected
  sessions defensively (hub already filters by subscription).
- `types.ts`: add `session_id?: string` to the event base; `SessionInfo` type;
  the two system messages.
- `graph-panel.ts`, `pipeline-panel.ts`, `split-pane.ts`: unchanged in Part A.
- Rebuild: `cd epimemer/visualization/frontend && npm run build`; commit
  `epimemer/visualization/static/`.

## A.8 Deletions & doc updates

- Delete `epimemer/visualization/ws_server.py` after moving the relay +
  snapshot-assembly pieces into `hub.py` / `snapshot.py`. Migrate
  `tests/visualization/test_ws_server.py` / `test_ws_relay.py` /
  `test_viz_endpoints.py` to target the hub app (most cases port 1:1 — the
  browser-facing routes are shape-compatible).
- ISSUES.md: the port-contention issue → resolved by this work (the failure
  class is structural now, so the entry can be deleted per the workflow); note
  on #16 that viz reads now happen in the owning process behind a lock,
  remaining hazard unchanged and still deferred.
- DEVELOPER_GUIDE / SUMMARY: hub lifecycle, `epimemer-viz` CLI, `viz_status`
  tool, env vars (`EPIMEMER_VIZ_HOST/PORT` now describe the hub;
  `EPIMEMER_VIZ_AUTOSPAWN` new; `EPIMEMER_VIZ_ENABLED` now means "publish to
  hub").
- Migration note for users: old MCP processes running pre-hub code may still
  hold :8765 — `pkill -f epimemer.mcp.server` once, or `epimemer-viz --status`
  will report the stranger on the port.

## A.9 Tests (write first, per ISSUES.md workflow)

`tests/visualization/test_hub.py`:
- register → session appears in `/api/sessions`; disconnect → `connected:
  false`.
- event published on ingest socket fans out to a browser ws with `session_id`
  injected and per-connection `seq`.
- browser subscribed to session A receives nothing from session B.
- RPC round-trip: hub `/api/snapshot?session=X` → `RpcRequest` on ingest →
  canned `RpcResponse` → HTTP body; timeout → 504.
- bind-race: second hub run against an occupied port with a healthy hub exits
  0; against a non-hub listener exits 1.

`tests/visualization/test_hub_client.py`:
- client connects to a fake hub (in-test websocket server), registers, forwards
  a bus event, answers a `list_graphs` RPC from `InMemoryStorage`.
- reconnect: kill the fake hub, restart it, client re-registers.
- unreachable hub: exactly one stderr/error line, then silence at debug level.

`tests/mcp/`:
- `viz_status` tool returns session/backend/graph with a live fake hub and
  `hub_reachable: false` without one.
- `backend_name` present on both storage backends (parameterized `storage`
  fixture, per the backend-parity rule in ISSUES.md).

Manual QA: start two MCP servers (one surrealdb, one default mem://) →
`epimemer-viz --status` shows both sessions → browser selector switches between
them; kill one → greys out; `pkill` the hub → next tool call on either server
respawns it (autospawn) and sessions reconnect.

## A.10 Suggested commit sequence

1. `protocol.py` + `backend_name` on the storage protocol (+ tests).
2. Hub server + CLI + pidfile (+ tests).
3. Hub client + reconnect (+ tests).
4. MCP lifespan rewire + autospawn + `viz_status` tool; delete embedded path
   and `ws_server.py`; migrate its tests.
5. Frontend session selector + rebuild static bundle.
6. Docs + ISSUES.md updates.

---

# Part B — Pipeline strip (glyph dashboard)

## B.0 Goal & layout

The knowledge graph is the star; pipelines become ambient awareness. Replace
the current left/right split with:

```
┌──────────────────────────────────────────────────────┐
│ header (session ▾, graph ▾, badges)                  │
├──────────────────────────────────────────────────────┤
│                                                      │
│               knowledge graph (flex-1)               │
│                                                      │
├──────────────────────────────────────────────────────┤
│ ▂ pipeline strip (~110px, collapsible)               │
│ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐      │
│ │ ○─▭─○   │ │ ○─▭─○   │ │ ○▭○▭○   │ │ ○─▭─○   │      │
│ │ seg:sem │ │ seg:par │ │ edges   │ │ retrieve│      │
│ │ 3 runs  │ │ idle    │ │ ●12     │ │ idle    │      │
│ └─────────┘ └─────────┘ └─────────┘ └─────────┘      │
└──────────────────────────────────────────────────────┘
```

- One **tile** per `pipeline_name`. Tiles persist across runs (fixing today's
  behaviour where each `PipelineStarted` clobbers the previous net and every
  snapshot load wipes the panel, `main.ts:114-124`).
- Each tile: a **mini glyph** of the net topology (places as dots, transitions
  as small rects, no text labels), the pipeline name, and a status line.
- **Live feel**: while running, the tile border glows; inside the glyph the
  firing transition lights up and token-holding places light up — same event
  handling as today, minus labels and badges (except one aggregate number).
- **Click a tile** → detail view: the existing full Graphviz rendering with
  labels and per-place token badges, live-updating, as an overlay panel that
  expands upward over the graph (or a modal — implementer's choice; overlay
  preferred so the strip stays visible). Close (Esc/×) returns to ambient mode.
- Strip is collapsible (reuse/replace the `Pipeline` toggle button in the
  header). `split-pane.ts` (left/right resize) is no longer needed — delete it
  and its wiring; the strip has a fixed height.

## B.1 Data model (frontend)

New `pipeline-store.ts` — a plain state module, no rendering:

```ts
interface PipelineRunState {
  topology: PipelineStarted;          // last-seen topology (has names + edges)
  status: "idle" | "running" | "completed" | "failed";
  activeTransition: string | null;
  placeTokens: Record<string, number>; // live counts
  runsCompleted: number;
  lastDurationMs: number | null;
  lastTransitionsFired: number | null;
  itemsProcessed: number;             // cumulative, see B.2
  lastError: string | null;
}
// keyed by pipeline_name; if Part A landed, keyed per selected session and
// cleared on session switch.
```

Tiles are created lazily on first `PipelineStarted` for a name (topology
arrives with that event, `events.py:196-203`). Before any run, the strip shows
nothing — acceptable; optionally pre-seed placeholder tiles for the known
names (`segmentation:semantic`, `segmentation:paragraph`, `edge_creation`,
`retrieval`, and — added later — `reflect`, which is not a net but declares a
synthetic linear topology via `visualization/phase_events.py`) with an
empty-glyph "not yet run" look. Pre-seeding real
topologies server-side is **out of scope** (net builders need runtime inputs).

## B.2 The "how much data" number

`TokensUpdated` (`events.py:233-238`) carries absolute per-place counts.
Maintain `itemsProcessed` as the cumulative sum of **positive deltas** across
updates (`sum(max(0, new - old))` per place): a robust proxy for "tokens pushed
through" that doesn't require knowing which places are terminal. Show it as
the tile's number while running (amber dot + count); after completion show
`{runsCompleted} runs · {lastDurationMs}ms`. Keep the metric in one pure
function (`applyTokensUpdate(state, event) -> state`) so it's testable and easy
to swap later.

## B.3 Glyph rendering

Reuse the Graphviz WASM path — same layout engine as the detail view, so the
glyph is a genuine miniature of what clicking reveals:

- Factor `generateDot(event, opts)` in `pipeline-panel.ts` to accept
  `{ mini: boolean }`: mini mode emits `label=""` on all nodes, smaller
  `width`/`height` (places ~0.12, transitions ~0.25×0.12), `nodesep`/`ranksep`
  tightened, no edge labels. Same `id="place-..."`/`id="transition-..."`
  attributes — the existing `setSvgNodeColor` overlay (`pipeline-panel.ts:89-104`)
  then works unchanged on both sizes.
- Render once per topology, cache the SVG string per pipeline_name (re-render
  only if a new `PipelineStarted` topology differs).
- Scale to fit the tile with the existing responsive-SVG treatment
  (`pipeline-panel.ts:198-204`).
- Colors: keep the existing palette (idle blue/gray, firing pink, completed
  green, token-holding amber, `pipeline-panel.ts:83-87`). Failed run: tile
  border red + error in the tile tooltip/status line.

## B.4 File plan

- `pipeline-store.ts` (new): state + pure event-application functions.
- `pipeline-strip.ts` (new): renders tiles from the store, handles click →
  opens detail; subscribes to the six pipeline event types (reuse the routing
  switch from `pipeline-panel.ts:273-303`).
- `pipeline-panel.ts` → rename `pipeline-detail.ts`: keep DOT generation
  (with `mini` option), SVG overlay helpers, and full-size rendering; it now
  renders *from the store's state for one chosen pipeline* instead of owning
  event subscriptions. Token badges (`setTokenCount`) only in detail mode.
- `main.ts`: replace split-pane wiring with graph-panel (flex-1) + strip;
  remove `pipelinePanel.clearPipeline()` from `loadGraphSnapshot`
  (main.ts:118) — snapshot loads must not wipe pipeline history. Strip
  collapse toggle. Esc closes detail.
- `index.html`: new layout per B.0 (delete the split-pane markup and
  `resize-handle`); `style.css` for tile styling (Tailwind utility classes
  preferred, per frontend style rules).
- Delete `split-pane.ts`.
- Rebuild and commit the static bundle.

## B.5 Behaviour details

- Two pipelines in quick succession (a `segment` + `store_decomposition` call
  fires segmentation then edge_creation): both tiles animate independently —
  the store is keyed by `pipeline_name`, so there is no shared "current
  pipeline" (the root cause of today's clobbering).
- Detail view open while another pipeline runs: only the viewed pipeline's
  events update the big SVG; other tiles keep animating in the strip.
- WS reconnect / gap detected: pipeline state may be stale mid-run; on
  `gap` (existing mechanism), mark running tiles as "unknown" (grey pulse)
  until the next event for that pipeline arrives. Snapshot refresh does not
  reset `runsCompleted`/`itemsProcessed` (session-lifetime counters).
- If Part A landed: switching sessions clears the store and repopulates from
  that session's subsequent events (pipeline history is not replayed — fine).

## B.6 Tests / verification

> **Superseded 2026-07-28.** This section said "there is no frontend test
> harness; keep it that way for now" — that was the state when the plan was
> written, and the pure-function shape it prescribed is exactly what made the
> harness cheap to add later. There is now a vitest suite (`make test-frontend`)
> over `pipeline-store.ts`, `events.ts` and `api.ts`; the rendering modules are
> still covered by `tsc` and the manual QA script below.

Keep logic testable-by-inspection:

- All state transitions live in `pipeline-store.ts` as pure functions
  (event in → new state out), separated from DOM code.
- `make test-frontend` (type-check + vitest) must pass.
- Manual QA script (run against a live server): call `segment` on a multi-
  paragraph text → segmentation tile animates, counter increments, ends
  "1 run"; call `store_decomposition` → edge_creation tile animates while
  segmentation tile stays; call `search` → retrieval tile; click each tile →
  detail matches today's full rendering incl. token badges; press Esc; load a
  different graph snapshot → tiles unchanged; kill/restart hub (if Part A) →
  tiles grey-pulse then resume.

## B.7 Suggested commit sequence

1. `pipeline-store.ts` + pure event application (incl. itemsProcessed).
2. Layout swap in `index.html`/`main.ts` (graph on top, empty strip, split-pane
   deleted) — UI still builds and graph panel works.
3. Tiles + mini glyph rendering + live overlay.
4. Detail view refactor (`pipeline-detail.ts`) + click/Esc wiring.
5. Polish: collapse toggle, gap-staleness, failed-run styling; rebuild bundle.

---

# Part C — Colour customisation

Designed 2026-08-08. **Not built.**

Goal: a dropdown of colour pickers for the parts of the dashboard the user
actually looks at — timeline text, detail text, and every background — with the
choices persisted.

## C.0 The problem this runs into immediately

The dashboard had **three** colour systems, and the request lands across all of
them. (#56 turned the third into a runtime palette; the count below is the
current state, not the one this section was written against.)

1. **Tailwind utility classes** — the chrome: header, toolbars, panels,
   buttons, chips, the detail drawer. About **22 distinct grey classes over
   ~230 occurrences** in `index.html` and the TS modules. These are compiled
   into a stylesheet at build time. **A colour picker cannot touch them.**
2. **The runtime `Palette`** (`theme.ts`) — 16 neutral fields read at render
   time by the three *drawn* surfaces that Tailwind cannot reach: the cytoscape
   canvas, the timeline SVG, and the graphviz DOT for the pipeline detail.
   These *can* be changed live today.
3. **The runtime `SemanticPalette`** (`theme.ts`, added by #56) — the hues that
   say what kind of thing something is, read at render time by both the graph
   and the timeline. This *used* to be system 3, "hard-coded hues, deliberately
   outside the palette because 'fact green' must mean the same thing in both
   themes" — and that reasoning is exactly what let the graph and the timeline
   disagree about what colour a fact is, since neither had a theme axis forcing
   anyone to reconcile them. The pipeline's active/completed/failed colours are
   the remainder, still hard-coded and still their own group (C.6).

The two things asked for split across the hard boundary: timeline mark text is
`palette.nodeLabel` (system 2, easy), and the timepoint detail text plus every
background is Tailwind (system 1, needs the migration below).

**So the bulk of this work is not the picker.** It is giving systems 1 and 2 a
single source of truth. The picker is a couple of hundred lines on top.

## C.1 Token model

Replace the raw greys with **semantic tokens**, each backed by a CSS custom
property. The 22 classes collapse to **nine tokens**, because most of the
classes are the light and dark halves of the same idea:

| Token | Today: light → dark | Used for |
|---|---|---|
| `--surface-page` | `gray-200` → `gray-950` | The page and the graph canvas |
| `--surface-chrome` | `gray-300` → `gray-900` | Headers, toolbars, drawers, trays |
| `--surface-raised` | `gray-100` → `gray-800` | Buttons, chips, selects |
| `--surface-raised-hover` | `gray-50` → `gray-700` | Their hover state |
| `--border` | `gray-400` → `gray-700/800` | Every divider and outline |
| `--text-strong` | `gray-900` → `gray-200` | Headings, hovered controls |
| `--text-primary` | `gray-700` → `gray-300` | Body text, node labels |
| `--text-secondary` | `gray-600` → `gray-400` | Labels, captions, most chrome |
| `--text-muted` | `gray-500` → `gray-500` | Hints, counts, disabled |

Nine tokens is also about the right number of *knobs*: a picker per raw class
would be 22 controls that mostly move together, which is a worse UI than the
thing it replaces.

**The drawn surfaces then derive from the same tokens.** `palette.nodeLabel` is
`--text-primary`; `palette.surfaceChrome` is `--surface-chrome` (already named
for its token, and already shared by three drawn surfaces);
`palette.axis` and `palette.tick` are `--border` and `--text-muted`. That is a
real simplification independent of the picker — those values are currently
duplicated between `theme.ts` and the markup, and have already drifted once (the
light-mode darkening pass had to fix the timeline axis separately from the
chrome).

Genuinely draw-only fields — `placeFill`, `transitionStroke`, `dotEdge` and the
rest of the graphviz set — stay as a second group with their own tokens.

## C.2 Mechanism

Tailwind 3.4 with a JS config (`tailwind.config.js`), `darkMode: "class"`.

```js
theme: { extend: { colors: {
  surface: { page: "var(--surface-page)", chrome: "var(--surface-chrome)", … },
  content: { strong: "var(--text-strong)", primary: "var(--text-primary)", … },
} } }
```

`bg-surface-chrome` then compiles to `background-color: var(--surface-chrome)`,
and **the `dark:` variants disappear from the markup entirely** — the dark
theme becomes a different set of values for the same variables, declared once:

```css
:root      { --surface-chrome: #d1d5db; … }
.dark      { --surface-chrome: #111827; … }
```

That is a large but mechanical diff: ~230 class occurrences, roughly halved
because each `x dark:y` pair becomes one class.

**The one performance trap.** `currentPalette()` is called on every render, and
the timeline re-renders on every frame of a pan. Reading nine-plus variables
through `getComputedStyle` in that loop is exactly the kind of forced-reflow
jank this codebase has so far avoided. **Read the variables once per theme or
override change into a cached `Palette` object**, and invalidate on change —
never per render. The cache belongs in `theme.ts`, which is already the single
place the `dark` class is read and written.

## C.3 Where the settings live

**`localStorage`, keyed per theme** — not the backend.

This is the opposite call from `reference_time` (`TIMELINE_VISUALISATION.md`
§6.4), and deliberately so. A fictional timeline's present is a fact about the
material, so it belongs in the graph where every client and the agent can see
it. A colour preference is a property of the *viewer*: two people looking at
the same graph should be able to disagree about it, and one of them changing it
should not rewrite anything the other reads.

**Overrides are stored per theme**, `{ light: {token: hex}, dark: {token: hex} }`.
A single shared map would mean choosing a colour in dark mode silently
destroying light mode — the user would have to notice, switch, and repair it.

Shape, following the existing `epimemer.theme` / `epimemer.split` keys:

```
epimemer.palette → {"version":1,"light":{"--surface-chrome":"#e5e7eb"},"dark":{}}
```

Only overridden tokens are stored, so a default that changes later still
reaches users who never touched it. `version` is there so a future rename can
migrate rather than silently drop. Unreadable or unparseable storage falls back
to defaults, as `theme.ts` and `split-pane.ts` already do.

## C.4 The picker UI

A dropdown from a paintbrush button beside the theme toggle, grouped by the
table in C.1:

```
┌ Colours ───────────────────── [Reset all] ┐
│ Surfaces                                  │
│   Page          ▢ #e5e7eb   ⟲             │
│   Panels        ▢ #d1d5db   ⟲             │
│   Controls      ▢ #f3f4f6   ⟲             │
│ Text                                      │
│   Headings      ▢ #111827   ⟲  AA 12.6:1  │
│   Body          ▢ #374151   ⟲  AA  8.9:1  │
│   Captions      ▢ #4b5563   ⟲  ⚠ 3.2:1    │
│ Graph & timeline                          │
│   Topic / Fact / Inference   ▢ ▢ ▢        │
│ ───────────────────────────────────────── │
│ [Export]  [Import]                        │
└───────────────────────────────────────────┘
```

Each row: a native `<input type="color">`, the hex, a per-token reset, and for
text tokens a **live contrast ratio** against the surface it is drawn on.

Native `<input type="color">` rather than a custom picker: it is one element,
it is accessible, it gets the platform's eyedropper for free, and a
hand-rolled HSV wheel is a lot of code that has nothing to do with this
project.

Changes apply **live on input**, and are persisted on `change` (commit), so
dragging through a gradient does not write to storage on every frame.

## C.5 Contrast, and the way back

A colour picker over the whole UI can render the UI unusable — dark grey text
on dark grey chrome, or a picker panel the same colour as its background.

- **Live WCAG ratio** beside every text token, against the surface it sits on,
  warning below **4.5:1** for small text. This project already reasons in these
  numbers: the light-mode darkening pass was validated by computing them, and
  found `text-gray-500` on `gray-300` at 3.28:1. The formula is a small pure
  function and belongs in its own tested module.
- **The reset control must not be themeable.** "Reset all" keeps hard-coded
  inline colours, so it is legible no matter what the user has done. Otherwise
  the escape hatch can be painted shut.
- A **query parameter** (`?palette=reset`) as a second way back for the case
  where the button is somehow unreachable.

## C.6 The semantic hues — shared across panels, and not customisable

The semantic hues stay fixed in this phase: node types, edge types,
contradiction red, selection pink, and the pipeline's active/completed/failed
colours.

They are not decoration — they are how the graph says what kind of thing you
are looking at. This section used to add *"and the panels agree on them"*, which
was **not true**: the graph panel drew facts green and inferences amber
(`graph-panel.ts:29`) while the timeline drew the same two blue and violet
(`timeline-panel.ts:89`). One window, two answers to "what colour is a fact".

**Decided and built 2026-08-12 (#56): one semantic palette, shared by both
panels**, taken from the valid-time grammar's set
(`TIMELINE_VISUALISATION.md` §13.3) because that
set was perceptually validated in both themes — lightness band, chroma floor,
colour-vision-deficiency separation, contrast against the surface — and the
graph panel's was not.

| Meaning | Light | Dark | Was |
|---|---|---|---|
| **fact / claim** | `#2a78d6` | `#3987e5` | graph green `#22c55e`, timeline `#3b82f6` |
| **inference** | `#4a3aa7` | `#9085e9` | graph amber `#f59e0b`, timeline `#a78bfa` |
| **topic** | `#1baf7a` | `#199e70` | indigo `#6366f1` |
| **historical / retired** | `#8095aa` | `#5d6d7e` | — (new; see #55) |
| **pending / proposed** | `#9a6b00` on `#f6ecd4` | `#fab219` on `#33290e` | — |
| **contradiction** | `#ef4444` | `#ef4444` | unchanged |
| **selection** | `#ec4899` | `#ec4899` | unchanged |

Two choices inside that are worth their reasons:

**Topic moves rather than staying indigo.** Fact takes blue and inference takes
violet, which puts inference next to indigo in both themes. Topic takes the
green the grammar had spare. It is the furthest hue from both, and it was
already validated, so nothing has to be re-checked.

**Contradiction keeps red; the now-line gives it up.** §13.3 reserved red for
the now-line, and contradiction has been red in the graph panel far longer. The
now-line is *chrome* — an annotation on the axis, not a thing in the data — so
it becomes a **dashed neutral rule** (`--text-secondary` stroke, `--text-strong`
label) and stops competing with a semantic hue. It is also no longer amber,
which the grammar needs for *pending*.

**Source strips are deliberately outside this palette.** §13.3's own invariant
is that a per-source strip is *always* direct-labelled, so its colour carries no
meaning and may reuse any hue. They draw from a plain rotation whose only
requirement is that adjacent strips in one stack differ.

Making the semantic hues settable is a reasonable *later* phase (C4), but it
needs an answer to "what happens when two of them are set to the same value",
which is a different question from "let me darken this background".

## C.7 File plan

| File | Change |
|---|---|
| `src/tokens.css` | **New.** `:root` and `.dark` blocks declaring every token's default. The one place a default colour is written. |
| `tailwind.config.js` | Extend `theme.colors` with the CSS-var-backed semantic names. |
| `index.html` + all TS | Mechanical: `bg-gray-300 dark:bg-gray-900` → `bg-surface-chrome`. |
| `src/theme.ts` | `Palette` derives from the tokens; cache per theme/override change (C.2). |
| `src/palette-store.ts` | **New, pure.** Defaults, override merge, per-theme resolution, hex validation, serialize/parse, reset. |
| `src/contrast.ts` | **New, pure.** Relative luminance and WCAG ratio. |
| `src/palette-picker.ts` | **New.** The dropdown; DOM only, state from `palette-store`. |
| `src/main.ts` | Wire the picker; re-render drawn panels on change, as the theme toggle already does. |

## C.8 Tests

Pure, and the bulk of the value:

- `palette-store.test.ts` — defaults; a partial override merges rather than
  replaces; overrides are per theme and do not leak across; invalid hex
  rejected; corrupt or unavailable storage falls back to defaults; `version`
  mismatch discards rather than misreads; reset restores exactly the defaults.
- `contrast.test.ts` — known pairs (black on white 21:1, the 3.28:1 the
  darkening pass found), symmetry, and the 4.5:1 boundary.

jsdom:

- `palette-picker.test.ts` — a change writes the CSS variable on `:root`;
  live-on-input but persist-on-commit; per-token reset restores one token and
  leaves the others; reset-all clears storage.
- `theme.test.ts` — additions: the cached palette invalidates on theme change
  and on override change, and **not** on every read.

Structural, in `layout.test.ts` (which already guards markup):

- No `bg-gray-*`, `text-gray-*` or `border-gray-*` class survives in
  `index.html` or the TS modules. That is what stops the migration silently
  rotting back — a new panel written the old way would still *look* right in
  both themes while ignoring the user's settings entirely.

## C.9 Phasing

Each phase is shippable on its own.

1. **C1 — Token migration.** No UI, no behaviour change: the dashboard looks
   identical afterwards. The largest and riskiest diff, done alone so a
   regression is unambiguous. Ends with the structural test in C.8.
2. **C2 — Store and apply.** `palette-store.ts`, `contrast.ts`, persistence,
   and applying overrides to `:root` at startup. Still no UI — verified by
   tests and by setting `localStorage` by hand.
3. **C3 — The picker.** The dropdown, live preview, contrast badges, resets.
   This is where the user's original request is satisfied.
4. **C4 — Later, if wanted.** Export/import, preset themes (high contrast,
   solarized), and semantic hues (C.6).

Doing C1 first is the point: it is what makes the timeline text, the detail
text and every background settable *at all*, and it is worth landing even if
the picker is never built, because it removes the duplication between
`theme.ts` and the markup that has already drifted once.

## C.10 Open questions

1. **Does the timepoint detail card count as chrome or as a drawn surface?**
   It is SVG inside the timeline (`renderCard`), so it reads the runtime
   palette — but the *drawer* showing the same information is Tailwind. After
   C1 both read the same tokens and the question disappears, which is another
   argument for doing C1 first.
2. **Should font size be in scope?** The same dropdown is the natural home for
   it, and "make the labels bigger" is a more common request than "make them
   green". It would change `CARD_LINE_HEIGHT`, `LABEL_HEIGHT` and the character
   budget, which the label layout already takes as inputs — so it is cheaper
   than it looks. Out of scope as written.
3. **Per-graph palettes?** Colouring a fiction graph differently from a real
   one is genuinely useful and would argue for the backend after all. Not
   proposed here; it would supersede C.3.
