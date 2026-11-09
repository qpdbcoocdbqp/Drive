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
# 5. install dependencies (optional)
# -------------------------------
source /opt/hermes/.venv/bin/activate
echo "[Step 4] Installing dependencies (best-effort)..."

# v0.23.0 image no longer ships `uv` (nor pip); bootstrap pip as a fallback.
if command -v uv >/dev/null 2>&1; then
  pip_install() { uv pip install "$@"; }
else
  python -m ensurepip --upgrade >/dev/null 2>&1 || true
  pip_install() { python -m pip install --quiet "$@"; }
fi
# python-docx is bundled in the image already; install the rest.
pip_install nano-pdf pymupdf || echo "[WARN] extra python deps install failed"

# -------------------------------
# 5b. langfuse plugin (idempotent, runs on every boot)
# -------------------------------
# Since v0.23.0 Langfuse is the official `langfuse` plugin and its SDK is owned
# by `hermes pm` (NOT .venv, so pip/uv here does nothing). `hermes plugins ...`
# hangs if run before the image's stage2 bootstrap or while the gateway holds
# the install lock, so we do NOT call it from this script. Instead we drop a
# s6 cont-init hook: /etc/cont-init.d runs as root after stage2 (01-) and
# before any service starts, so nothing contends for the lock. The hook is a
# no-op once the plugin, SDK and config entry exist (state lives in $HERMES_HOME).
echo "[Step 4b] Registering langfuse plugin cont-init hook..."
cat > /etc/cont-init.d/019-langfuse-plugin <<'HOOK'
#!/command/with-contenv bash
# Ensure the langfuse plugin + SDK are installed and enabled. Never fails boot.
set +e
HH="${HERMES_HOME:-/opt/data}"

if [ -z "${HERMES_LANGFUSE_PUBLIC_KEY:-}" ] || [ -z "${HERMES_LANGFUSE_SECRET_KEY:-}" ]; then
  echo "[langfuse] HERMES_LANGFUSE_*_KEY not set, skipping plugin setup"
  exit 0
fi

plugin_ok()  { [ -d "$HH/plugins/langfuse" ]; }
sdk_ok()     { compgen -G "$HH/installs/*/environments/*/venv/lib/python*/site-packages/langfuse-*.dist-info" >/dev/null; }
enabled_ok() { grep -qE '^[[:space:]]*-[[:space:]]*langfuse[[:space:]]*$' "$HH/config.yaml" 2>/dev/null; }

if plugin_ok && sdk_ok && enabled_ok; then
  echo "[langfuse] plugin already installed and enabled"
  exit 0
fi

# run as the hermes user with HOME=$HH (mirrors docker/main-wrapper.sh); stdin
# closed so the plugin's interactive env-var prompt is skipped.
hx() { HOME="$HH" /command/s6-setuidgid hermes timeout 2400 hermes "$@" </dev/null; }

echo "[langfuse] installing plugin (first boot can take several minutes on bind mounts)..."
if ! plugin_ok || ! sdk_ok; then
  hx plugins install langfuse --force || echo "[langfuse][WARN] plugins install failed"
fi
if ! enabled_ok; then
  hx plugins enable langfuse || echo "[langfuse][WARN] plugins enable failed"
fi

if plugin_ok && sdk_ok && enabled_ok; then
  echo "[langfuse] setup complete"
else
  echo "[langfuse][WARN] setup incomplete; tracing disabled (will retry on next boot)"
fi
exit 0
HOOK
chmod 755 /etc/cont-init.d/019-langfuse-plugin

# -------------------------------
# 6. start hermes via the image's native entrypoint
# -------------------------------
# Since v0.23.0 the image bootstraps through s6-overlay (UID remap, chown,
# config seed, plugin install artifact). Overriding ENTRYPOINT with `hermes ...`
# skips all of that, so hand over to the stock dispatcher (we stay PID 1).
echo "[Step 5] Starting Hermes Gateway..."
exec /opt/hermes/docker/entrypoint-dispatch.sh gateway run --verbose
