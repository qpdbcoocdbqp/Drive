#!/bin/bash
set -euo pipefail

echo "======================================"
echo "[Hermes Bootstrap] Starting..."
echo "======================================"

CHROME_URL="http://headless-chrome:9223/json/version"

# -------------------------------
# 1. wait for chrome HTTP endpoint
# -------------------------------
echo "[Step 1] Waiting for Chrome HTTP endpoint..."

for i in $(seq 1 60); do
  RAW=$(curl -sS --max-time 2 "$CHROME_URL" || true)

  if [[ -n "$RAW" ]] && echo "$RAW" | grep -q "webSocketDebuggerUrl"; then
    echo "[OK] Chrome CDP is ready"
    break
  fi

  echo "[WAIT] Chrome not ready ($i/60)"
  sleep 1
done

# -------------------------------
# 2. validate response
# -------------------------------
if [[ -z "${RAW:-}" ]] || ! echo "$RAW" | grep -q "webSocketDebuggerUrl"; then
  echo "[ERROR] Chrome CDP failed to initialize"
  echo "Last response: $RAW"
  exit 1
fi

echo "[INFO] Chrome CDP is ready: $RAW"

# -------------------------------
# 3. extract websocket url safely
# -------------------------------
echo "[Step 2] Parsing WebSocket URL..."

clean_raw=$(echo "$RAW" | tr -d '\r')

BROWSER_CDP_URL=$(python3 - <<EOF
import json
raw = """$clean_raw"""
data = json.loads(raw)
print(data.get("webSocketDebuggerUrl",""))
EOF
)

if [[ -z "$BROWSER_CDP_URL" ]]; then
  echo "[ERROR] Failed to parse webSocketDebuggerUrl"
  exit 1
fi

# -------------------------------
# 4. fix hostname for docker network
# -------------------------------
BROWSER_CDP_URL="${BROWSER_CDP_URL/ws:\/\/localhost/ws:\/\/headless-chrome:9223}"

export BROWSER_CDP_URL
echo "[Step 3] Final CDP URL:"
echo "$BROWSER_CDP_URL"
echo "BROWSER_CDP_URL=$BROWSER_CDP_URL" >> /etc/environment

# -------------------------------
# 5. install dependencies and plugins
# -------------------------------
source /opt/hermes/.venv/bin/activate
echo "[Step 4] Installing dependencies..."
/opt/hermes/tools/uv-*/uv pip install nano-pdf pymupdf || echo "[WARN] python deps install failed"

[ -d /opt/data/plugins/langfuse ] || timeout 600 hermes plugins install langfuse </dev/null || echo "[WARN] langfuse install failed"
timeout 600 hermes plugins enable langfuse </dev/null || echo "[WARN] langfuse enable failed"

# -------------------------------
# 6. start hermes via the image's native entrypoint
# -------------------------------
# hand over to the image entrypoint (s6-overlay) as PID 1
echo "[Step 5] Starting Hermes Gateway..."
exec /opt/hermes/docker/entrypoint-dispatch.sh gateway run --verbose
