#!/bin/bash
set -e

# Start Agent in its own process group and capture its PID (also PGID)
# Prefer venv python if present
PY_BIN="python"
if [ -x "/home/pierre/Desktop/intervention/agent/.venv/bin/python3" ]; then
  PY_BIN="/home/pierre/Desktop/intervention/agent/.venv/bin/python3"
fi
( cd agent/experiments && exec setsid "$PY_BIN" run_robots.py ) &
AGENT_PID=$!
echo "Agent started (pid=$AGENT_PID)"

# Start UI in its own process group and capture its PID (also PGID)
( cd ui && exec setsid yarn run start ) &
UI_PID=$!
echo "UI started (pid=$UI_PID)"

cleanup() {
trap - EXIT INT TERM HUP QUIT
echo "Stopping services..."
# Send TERM to process groups (created by setsid)
kill -TERM -$AGENT_PID -$UI_PID 2>/dev/null || true
sleep 2
# Force kill if still running
kill -KILL -$AGENT_PID -$UI_PID 2>/dev/null || true
# Wait for them to exit
wait $AGENT_PID $UI_PID 2>/dev/null || true
exit 130
}

trap cleanup EXIT INT TERM HUP QUIT

echo "All services started"

# Wait until either process exits; trap handles cleanup on signals or normal exit
set +e
wait -n $AGENT_PID $UI_PID
exit $?


Build: go build -o start-go /home/p/Desktop/intervention/start.go
