# Visualisation: the hub and the visualiser it serves

How Epimemer shows a graph being built: MCP server processes publish events to
a hub, and the hub serves a visualiser that reads them. The shapes a client
reads from the hub (the routes, the snapshot and the event stream) are
`VIZ_CONTRACT.md`; the record behind a visualiser's log is `EVENT_LOG.md`, and
the record of what each tool call returned is `RETRIEVAL_PROVENANCE.md`. The
code is `epimemer/visualization/`.

The visualiser is a separate program,
[epimemer-bevy](https://github.com/olenive/epimemer-bevy), with its own
repository and design notes. Epimemer holds none of its code; Part B says how
its page reaches the hub.

Decisions made with the user, not to be re-litigated:

1. Hub lifecycle: the first MCP server spawns a detached hub if none is
   running, **and** the `epimemer-viz` CLI starts, stops and reports on one
   explicitly.
2. Transport: **plain WebSocket**, with MCP processes dialling out to the hub.
   No message broker.
3. No viz server is embedded in an MCP process, and the MCP process never
   binds the port: a stale MCP process holding it would show its own empty
   store while the session the user is driving served nothing. Hub down means
   no viz, with one stderr line from the MCP process.
4. The visualiser is **fetched, not packaged**: its browser bundle is a
   release of another repository, downloaded when the user asks, so the wheel
   stays small and carries no build of a page.

---

# Part A: the viz hub

A standalone process that owns the visualisation port; MCP server processes
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
- `GET /api/warnings` is a read, and the hub has no route that writes a
  setting (`EVENT_LOG.md` §12.4).
- The per-session rings the hub keeps are `EVENT_LOG.md` §4 (acts and
  warnings) and `RETRIEVAL_PROVENANCE.md` §3.2 (retrieval records).
- The hub sends a client only the events of the session and graphs it
  subscribed to, live and replayed alike, so an entry from graph A never
  reaches a client drawing graph B.

## A.3 Session client (MCP side)

`hub_client.py` connects, registers, forwards every bus event and answers the
hub's reads. Each read runs inside one turn of the storage `graph_guard`, so the
reads behind one answer describe a single instant and no tool call runs between
them (`DEVELOPER_GUIDE.md`, *The active graph holds still*). The snapshot sends
only active nodes; historical ones are read in the same turn only to work out
the boundaries reflect would propose.

The client never blocks the MCP server: it reconnects with capped backoff for
ever and drops the events published while it is away, which a visualiser
recovers from by refetching the snapshot (`VIZ_CONTRACT.md` §7.5). The first
connection failure writes one line to stderr, which Claude Code surfaces: "viz
hub unreachable at …; run `uv run epimemer-viz --status`". Later retries log at
debug.

## A.4 Backend label

`SessionInfo.backend` and `/api/graphs` carry the backend kind (`memory`,
`sqlite`, `surrealdb`, `postgres`) for a visualiser to show beside the graph,
because `default (memory)` reads instantly as "wrong server" where `default`
does not.

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
and active graph to look for in the visualiser.

## A.7 The reflect counter

`/api/graphs` carries the active graph's `reflect` counter and
`reflect_counter_updated` events move it, because events alone would leave a
visualiser that connected to a graph already at 7 of 10 showing nothing until
the next store.

---

# Part B: the page

The hub serves one page at `/`, from a directory on disk
(`epimemer/visualization/bundle.py`).

## B.0 Fetching

`epimemer viz fetch` downloads a release of epimemer-bevy's browser bundle, a
gzipped tarball, and unpacks it into `~/.epimemer/viz/current`. The release
and its sha256 are pinned in the code (`PINNED_RELEASE`), so the digest is
known before the download starts and a tarball that does not match is
discarded unpacked. `--release` with `--sha256` fetches another release, with
the digest from that release's `.sha256` file. Nothing is downloaded unless
the command is run.

A fetch unpacks beside the bundle in use and swaps the new one in whole, so
the hub never serves half a page, and a fetch that fails leaves the old bundle
in place. The download, the unpacked size and the number of files each have a
ceiling, and a tarball member that is a link or that would land outside the
directory is refused.

**Moving the pin.** Take the version and the digest from the new release's
`.sha256` file, and try that visualiser against this hub first: the two share
a contract, and the pin is the statement that this Epimemer and that
visualiser agree on it.

## B.1 Serving

The hub looks for the page on every request, so a fetch shows from the next
page load with no restart. `EPIMEMER_VIZ_BUNDLE_DIR` names a directory to
serve in place of the fetched bundle, for a visualiser built locally. When it
is set it is the only place looked: someone pointing the hub at their own build
wants to be told it is broken rather than be shown another page.

With no page to serve, `/` answers `503` and says how to get one. The API and
the event stream work without the page.

---

# Part C: pipeline events

Every pipeline run publishes its shape and its progress, which a visualiser
draws as it likes; the events are `VIZ_CONTRACT.md` §7.

- **A run brings its own topology.** `pipeline_started` carries the net's
  places, transitions and edges. Topologies cannot be sent ahead of a run,
  because the net builders need runtime inputs, and pipeline events are never
  replayed, so a visualiser knows a pipeline's shape only from a start it
  heard.
- **`reflect` is not a Petri net** but declares a synthetic linear topology
  (`phase_events.py`), so it is drawn and lit phase by phase like the rest.
  When a transition lights is `DEVELOPER_GUIDE.md`, *Petri net visualisation*.
- **`tokens_updated` carries absolute counts** for the places it names, not
  changes. A visualiser that wants "how much went through" sums the positive
  changes per place, which needs no knowledge of which places are terminal.
- **Events arrive at storage-write granularity**: one `store_decomposition`
  publishes hundreds (`EVENT_LOG.md` §3), so a visualiser aggregates a run
  rather than drawing each event.
