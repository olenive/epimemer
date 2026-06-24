"""WebSocket relay server for visualization events.

Subscribes to the EventBus and relays all events as JSON to connected
WebSocket clients. Also serves the static frontend files and provides
read-only HTTP endpoints for graph listing and snapshot retrieval.

The server is a thin relay — it contains no visualization logic.
Any frontend that understands the event JSON schema can connect.

Usage:
    bus = create_event_bus()
    app = create_app(bus, storage)
    uvicorn.run(app, host="0.0.0.0", port=8765)
"""

import asyncio
import json
import logging
from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.websockets import WebSocket, WebSocketDisconnect

from epimemer.visualization.event_bus import InProcessEventBus
from epimemer.visualization.events import Event, edge_to_view, node_to_view

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class _ConnectionState:
    """Per-connection tracking for sequence numbers and subscriptions."""
    __slots__ = ("seq", "subscribed_graphs")

    def __init__(self) -> None:
        self.seq: int = 0
        self.subscribed_graphs: set[str] | None = None  # None = all graphs


class WebSocketRelay:
    """Manages WebSocket connections and relays events from the bus.

    Each connection gets per-connection sequence numbers injected into
    every event payload. Connections can subscribe to specific graphs
    by sending {"subscribe": ["graph-a", "graph-b"]} messages.
    """

    def __init__(self, bus: InProcessEventBus) -> None:
        self._bus = bus
        self._connections: dict[WebSocket, _ConnectionState] = {}
        self._unsubscribe = bus.subscribe(handler=self._broadcast)

    async def _broadcast(self, event: Event) -> None:
        """Send event JSON to all connected clients, with per-connection seq."""
        if not self._connections:
            return

        base_payload = json.loads(event.model_dump_json())
        event_graph = getattr(event, "graph", "")
        disconnected: list[WebSocket] = []

        async def _send(ws: WebSocket, state: _ConnectionState) -> None:
            # Filter by subscription
            if state.subscribed_graphs is not None and event_graph:
                if event_graph not in state.subscribed_graphs:
                    return

            state.seq += 1
            payload = {**base_payload, "seq": state.seq}
            try:
                await ws.send_text(json.dumps(payload))
            except Exception:
                disconnected.append(ws)

        await asyncio.gather(*[_send(ws, st) for ws, st in self._connections.items()])

        for ws in disconnected:
            self._connections.pop(ws, None)

    async def handle_websocket(self, websocket: WebSocket) -> None:
        """Accept a WebSocket connection and keep it alive until disconnect."""
        await websocket.accept()
        state = _ConnectionState()
        self._connections[websocket] = state
        logger.info("Visualization client connected (%d total)", len(self._connections))

        try:
            while True:
                text = await websocket.receive_text()
                try:
                    msg = json.loads(text)
                    if "subscribe" in msg:
                        graphs = msg["subscribe"]
                        if graphs is None:
                            state.subscribed_graphs = None
                        elif isinstance(graphs, list):
                            state.subscribed_graphs = set(graphs)
                except (json.JSONDecodeError, TypeError):
                    pass
        except WebSocketDisconnect:
            pass
        finally:
            self._connections.pop(websocket, None)
            logger.info("Visualization client disconnected (%d remaining)", len(self._connections))

    def shutdown(self) -> None:
        """Clean up the bus subscription."""
        self._unsubscribe()


async def _index(request: Request) -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def create_app(bus: InProcessEventBus, storage: object) -> Starlette:
    """Create the Starlette ASGI app for the visualization server.

    Args:
        bus: Event bus for WebSocket relay.
        storage: Raw (pre-instrumented) storage backend for snapshot reads.
            Viz reads bypass the event-emitting wrapper.

    Routes:
        /              — serves the frontend HTML
        /ws            — WebSocket endpoint for live events
        /api/graphs    — list available graphs and active graph
        /api/snapshot  — full node+edge snapshot for a graph
        /assets/...    — static assets (JS, CSS)
    """
    relay = WebSocketRelay(bus)

    async def api_graphs(request: Request) -> JSONResponse:
        graphs = await storage.list_databases()
        return JSONResponse({
            "graphs": graphs,
            "active_graph": storage.current_database,
        })

    async def api_snapshot(request: Request) -> JSONResponse:
        graph = request.query_params.get("graph")
        if not graph:
            return JSONResponse({"error": "Missing 'graph' query parameter"}, status_code=400)

        available = await storage.list_databases()
        if graph not in available:
            return JSONResponse({"error": f"Graph '{graph}' not found"}, status_code=404)

        nodes = await storage.viz_list_nodes(graph)
        edges = await storage.viz_list_edges(graph)

        node_views = [node_to_view(n, graph).model_dump(mode="json") for n in nodes]
        edge_views = [edge_to_view(e, graph).model_dump(mode="json") for e in edges]

        return JSONResponse({
            "graph": graph,
            "nodes": node_views,
            "edges": edge_views,
        })

    routes = [
        Route("/", _index),
        Route("/api/graphs", api_graphs),
        Route("/api/snapshot", api_snapshot),
        WebSocketRoute("/ws", relay.handle_websocket),
    ]

    # Mount static files at root so /assets/... resolves from the static dir
    if STATIC_DIR.exists():
        routes.append(Mount("/", app=StaticFiles(directory=STATIC_DIR), name="static"))

    app = Starlette(routes=routes)
    return app
