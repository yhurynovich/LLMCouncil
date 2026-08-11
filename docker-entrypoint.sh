#!/bin/bash
# Runs two processes in this container:
#   1. The main FastAPI app (backend.main)       -> port 8001
#   2. The MCP server, Streamable HTTP transport -> port 8002
#
# The main app is treated as the process that matters for container health:
# if it dies, this script exits non-zero so `restart: unless-stopped`
# restarts the whole container. The MCP server is additive — if it crashes,
# that's logged loudly but the main app keeps serving rather than the whole
# container going down with it.

set -u

echo "[entrypoint] starting MCP server (Streamable HTTP) on :8002..."
MCP_TRANSPORT=http uv run python -m backend.mcp_server &
MCP_PID=$!

echo "[entrypoint] starting main API on :8001..."
uv run python -m backend.main &
MAIN_PID=$!

shutdown() {
    echo "[entrypoint] shutting down..."
    kill -TERM "$MAIN_PID" "$MCP_PID" 2>/dev/null
    wait "$MAIN_PID" 2>/dev/null
    wait "$MCP_PID" 2>/dev/null
    exit 0
}
trap shutdown TERM INT

# Block until whichever process exits first, then decide what to do.
wait -n

if ! kill -0 "$MAIN_PID" 2>/dev/null; then
    echo "[entrypoint] main API exited, shutting down container"
    kill -TERM "$MCP_PID" 2>/dev/null
    wait "$MCP_PID" 2>/dev/null
    exit 1
fi

echo "[entrypoint] WARNING: MCP server exited unexpectedly — main API is still running, container staying up"
wait "$MAIN_PID"
exit $?
