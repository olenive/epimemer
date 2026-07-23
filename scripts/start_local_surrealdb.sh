#!/usr/bin/env bash
# Start a local SurrealDB instance via Colima + Docker.
#
# Handles: Colima not running, container not existing, container stopped.
# Safe to run repeatedly — idempotent.
#
# Usage:
#   ./scripts/start_local_surrealdb.sh
#   ./scripts/start_local_surrealdb.sh --memory 2   # override Colima memory (GB)

set -euo pipefail

CONTAINER_NAME="surrealdb"
SURREAL_IMAGE="surrealdb/surrealdb:latest"
SURREAL_PORT="8000"
SURREAL_VOLUME="surreal-data"              # named volume -> on-disk persistence
SURREAL_STORE="rocksdb:/data/epimemer.db"  # on-disk backend (survives restarts)
COLIMA_MEMORY="${1:-1}"  # Default 1 GB

# Strip --memory flag if passed as --memory 2
if [[ "$COLIMA_MEMORY" == "--memory" ]]; then
    COLIMA_MEMORY="${2:-1}"
fi

echo "==> Checking Colima..."
if colima status 2>/dev/null | grep -q "Running"; then
    echo "    Colima is already running."
else
    echo "    Starting Colima with ${COLIMA_MEMORY}GB memory..."
    colima start --memory "$COLIMA_MEMORY"
fi

echo "==> Checking SurrealDB container..."
if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    # Container exists. Warn if it predates on-disk persistence — an in-memory
    # container loses its whole graph on every restart, silently.
    if ! docker inspect "$CONTAINER_NAME" --format '{{json .Config.Cmd}}' | grep -q 'rocksdb'; then
        echo "    WARNING: existing '$CONTAINER_NAME' runs in-memory (no rocksdb path)."
        echo "             Its data will NOT survive a restart. To migrate to on-disk"
        echo "             storage: docker rm -f $CONTAINER_NAME  &&  re-run this script."
    fi
    if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
        echo "    SurrealDB container is already running."
    else
        echo "    Starting existing SurrealDB container..."
        docker start "$CONTAINER_NAME"
    fi
else
    echo "    Creating persistent SurrealDB container (on-disk rocksdb)..."
    # -u 0:0: the image's non-root user cannot write the root-owned volume mount,
    # so run as root. --restart unless-stopped brings it back after a Colima or
    # machine reboot. Data lives in the named volume, independent of the container.
    docker run -d \
        --name "$CONTAINER_NAME" \
        --restart unless-stopped \
        -u 0:0 \
        -p "${SURREAL_PORT}:8000" \
        -v "${SURREAL_VOLUME}:/data" \
        "$SURREAL_IMAGE" \
        start --user root --pass root "$SURREAL_STORE"
fi

# Wait for SurrealDB to be ready
echo "==> Waiting for SurrealDB on port ${SURREAL_PORT}..."
for i in $(seq 1 15); do
    if curl -sf "http://localhost:${SURREAL_PORT}/health" >/dev/null 2>&1; then
        echo "    SurrealDB is ready."
        echo ""
        PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

        echo "==> Registering epimemer MCP server with SurrealDB backend..."
        claude mcp remove epimemer 2>/dev/null || true
        claude mcp add epimemer \
            -e EPIMEMER_STORAGE_BACKEND=surrealdb \
            -e EPIMEMER_SURREALDB_URL="ws://localhost:${SURREAL_PORT}/rpc" \
            -e EPIMEMER_LOG_FILE=/tmp/epimemer.log \
            -- uv run --directory "$PROJECT_DIR" python -m epimemer.mcp.server

        echo ""
        echo "Done. Restart Claude Code to connect to the persistent graph."
        exit 0
    fi
    sleep 1
done

echo "    ERROR: SurrealDB did not become ready within 15 seconds."
echo "    Check: docker logs $CONTAINER_NAME"
exit 1
