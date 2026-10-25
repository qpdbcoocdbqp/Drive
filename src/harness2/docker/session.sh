#!/usr/bin/env bash
# Container entrypoint: one `docker compose run` is one session (README "Where runs are stored").
# Everything it writes goes to runs/<session-id>/, laid out like an API job:
#   session.json   what ran, when, how it ended
#   log.txt        all output
#   ...            harness2's own files (HARNESS2_RUNS_DIR points here)
# Pass -e HARNESS2_SESSION_ID=<id> to continue an existing session (harness2 resumes from it).
set -uo pipefail

if [ -z "${HARNESS2_SESSION_ID:-}" ]; then
  HARNESS2_SESSION_ID="$(date -u +%Y%m%d-%H%M%S)-$(od -An -N2 -tx1 /dev/urandom | tr -d ' \n')"
fi
export HARNESS2_SESSION_ID
export HARNESS2_RUNS_DIR="/opt/harness2/runs/$HARNESS2_SESSION_ID"
mkdir -p "$HARNESS2_RUNS_DIR"
[ $# -gt 0 ] || set -- bash

# session_json <state> [exit-code] -- <command...>
session_json() {
  python - "$HARNESS2_RUNS_DIR/session.json" "$HARNESS2_SESSION_ID" "$@" <<'PY'
import json, os, sys, time
from pathlib import Path

path, sid, state, code, _, *cmd = sys.argv[1:]
p = Path(path)
s = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"id": sid, "kind": "cli"}
t = round(time.time(), 3)
s.setdefault("created", t)
if state == "running":
    s.update(started=t, command=cmd, models={
        k: os.environ.get(f"HARNESS2_{k}_MODEL", "") for k in ("SOLVER", "IMPROVER", "JUDGE")})
    s.pop("finished", None)
else:
    s.update(finished=t, exit_codes={"cli": int(code)})
s["state"] = state
tmp = p.with_suffix(".json.tmp")
tmp.write_text(json.dumps(s, indent=2), encoding="utf-8")
tmp.replace(p)
PY
}

echo "[session] $HARNESS2_SESSION_ID -> runs/$HARNESS2_SESSION_ID/" >&2
session_json running "" -- "$@"
log="$HARNESS2_RUNS_DIR/log.txt"
printf '\n$ %s\n' "$*" >>"$log"
if [ -t 0 ] && [ -t 1 ]; then
  # Interactive: keep the terminal and record it.
  script -qefa -c "$(printf '%q ' "$@")" "$log"
else
  "$@" 2>&1 | tee -a "$log"
fi
code=${PIPESTATUS[0]}
# A killed container never gets here: its session.json stays "running" with no "finished".
session_json "$([ "$code" -eq 0 ] && echo completed || echo failed)" "$code" --
exit "$code"
