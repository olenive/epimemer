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
    # Container exists — check if running
    if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
        echo "    SurrealDB container is already running."
    else
        echo "    Starting existing SurrealDB container..."
        docker start "$CONTAINER_NAME"
    fi
else
    echo "    Creating new SurrealDB container..."
    docker run -d \
        --name "$CONTAINER_NAME" \
        -p "${SURREAL_PORT}:8000" \
        "$SURREAL_IMAGE" \
        start --user root --pass root
fi

# Wait for SurrealDB to be ready
echo "==> Waiting for SurrealDB on port ${SURREAL_PORT}..."
for i in $(seq 1 15); do
    if curl -sf "http://localhost:${SURREAL_PORT}/health" >/dev/null 2>&1; then
        echo "    SurrealDB is ready."
        echo ""
        PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

        echo "    SurrealDB is ready."
        echo ""
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
