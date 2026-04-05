"""WebSocket relay server for visualization events.

Subscribes to the EventBus and relays all events as JSON to connected
WebSocket clients. Also serves the static frontend files.

The server is a thin relay — it contains no visualization logic.
Any frontend that understands the event JSON schema can connect.

Usage:
    bus = create_event_bus()
    app = create_app(bus)
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
from starlette.responses import FileResponse
from starlette.websockets import WebSocket, WebSocketDisconnect

from epimemer.visualization.event_bus import InProcessEventBus
from epimemer.visualization.events import Event

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class WebSocketRelay:
    """Manages WebSocket connections and relays events from the bus."""

    def __init__(self, bus: InProcessEventBus) -> None:
        self._bus = bus
        self._connections: set[WebSocket] = set()
        self._unsubscribe = bus.subscribe(handler=self._broadcast)

    async def _broadcast(self, event: Event) -> None:
        """Send event JSON to all connected clients."""
        if not self._connections:
            return

        data = event.model_dump_json()
        disconnected: list[WebSocket] = []

        async def _send(ws: WebSocket) -> None:
            try:
                await ws.send_text(data)
            except Exception:
                disconnected.append(ws)

        await asyncio.gather(*[_send(ws) for ws in self._connections])

        for ws in disconnected:
            self._connections.discard(ws)

    async def handle_websocket(self, websocket: WebSocket) -> None:
        """Accept a WebSocket connection and keep it alive until disconnect."""
        await websocket.accept()
        self._connections.add(websocket)
        logger.info("Visualization client connected (%d total)", len(self._connections))

        try:
            # Keep connection alive — client may send filter commands in the future
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            self._connections.discard(websocket)
            logger.info("Visualization client disconnected (%d remaining)", len(self._connections))

    def shutdown(self) -> None:
        """Clean up the bus subscription."""
        self._unsubscribe()


async def _index(request: Request) -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def create_app(bus: InProcessEventBus) -> Starlette:
    """Create the Starlette ASGI app for the visualization server.

    Routes:
        /         — serves the frontend HTML
        /ws       — WebSocket endpoint for live events
        /static/  — static assets (JS, CSS)
    """
    relay = WebSocketRelay(bus)

    routes = [
        Route("/", _index),
        WebSocketRoute("/ws", relay.handle_websocket),
    ]

    # Mount static files at root so /assets/... resolves from the static dir
    if STATIC_DIR.exists():
        routes.append(Mount("/", app=StaticFiles(directory=STATIC_DIR), name="static"))

    app = Starlette(routes=routes)
    return app
