#!/usr/bin/env bash
# test_locally.sh — end-to-end smoke test for the Rehearsal demo payloads.
#
# Starts the vulnerable service (app.py) in the background, POSTs
# payload-a.yaml to /ingest, confirms /_debug/state shows the implant
# planted, calls /_debug/patch, confirms /_debug/state shows "patched": true,
# then kills the background server.
#
# Everything here talks only to 127.0.0.1 on a local port; nothing leaves
# the machine.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PORT="${REHEARSAL_TEST_PORT:-18080}"
BASE_URL="http://127.0.0.1:${PORT}"
VENV_DIR="$SERVICE_DIR/.venv"
SERVER_LOG="$SCRIPT_DIR/.test_server.log"
SERVER_PID=""

cleanup() {
  if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    echo "==> stopping background server (pid ${SERVER_PID})"
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "==> setting up venv (if needed)"
if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install -q -r "$SERVICE_DIR/requirements.txt"

echo "==> starting app.py in the background (on port ${PORT})"
# app.py hardcodes port 8080 in its own __main__ block, and we were asked
# not to edit app.py. _run_app_on_port.py runs app.py's actual code
# unmodified via runpy, only monkeypatching Flask.run()'s port argument at
# test time, so a stray process already on 8080 elsewhere on the machine
# can't make this test flaky. Set REHEARSAL_TEST_PORT=8080 to test the
# literal default port instead.
python3 "$SCRIPT_DIR/_run_app_on_port.py" "$SERVICE_DIR" "$PORT" >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!

echo "==> waiting for the server to come up"
for i in $(seq 1 30); do
  if curl -s -o /dev/null "$BASE_URL/health"; then
    break
  fi
  if [[ "$i" -eq 30 ]]; then
    echo "FAIL: server never came up; log follows:"
    cat "$SERVER_LOG"
    exit 1
  fi
  sleep 0.3
done

echo "==> resetting state"
curl -s -X POST "$BASE_URL/_debug/reset" >/dev/null

echo "==> posting payload A"
python3 "$SCRIPT_DIR/make_request.py" "$BASE_URL" a

echo "==> waiting a moment for the beacon thread to tick"
sleep 1.5

echo "==> checking /_debug/state shows the implant"
STATE_JSON="$(curl -s "$BASE_URL/_debug/state")"
echo "$STATE_JSON"

python3 - "$STATE_JSON" <<'PY'
import json, sys
state = json.loads(sys.argv[1])
assert state["implant_present"] is True, f"implant_present not True: {state}"
assert state["implant_marker"] == "variant-A-thread-beacon", f"unexpected marker: {state}"
assert state.get("implant_heartbeat", 0) >= 1, f"heartbeat did not advance: {state}"
assert state["libraries"]["pyyaml"], f"pyyaml version missing: {state}"
print("OK: implant A detected with a growing heartbeat")
PY

echo "==> calling /_debug/patch"
PATCH_JSON="$(curl -s -X POST "$BASE_URL/_debug/patch")"
echo "$PATCH_JSON"

python3 - "$PATCH_JSON" <<'PY'
import json, sys
resp = json.loads(sys.argv[1])
assert resp.get("patched") is True, f"patch did not report patched: {resp}"
print("OK: patch endpoint reports patched")
PY

echo "==> re-checking /_debug/state shows patched"
STATE_JSON_2="$(curl -s "$BASE_URL/_debug/state")"
echo "$STATE_JSON_2"

python3 - "$STATE_JSON_2" <<'PY'
import json, sys
state = json.loads(sys.argv[1])
assert state["patched"] is True, f"patched flag not set after patch: {state}"
print("OK: state reflects patched=true")
PY

echo "==> sanity: confirming the loader is actually swapped (a fresh exploit attempt is now inert)"
POST_PATCH_INGEST="$(curl -s -X POST --data-binary @"$SCRIPT_DIR/payload-b.yaml" "$BASE_URL/ingest")"
echo "$POST_PATCH_INGEST"
STATE_JSON_3="$(curl -s "$BASE_URL/_debug/state")"
python3 - "$STATE_JSON_3" <<'PY'
import json, sys
state = json.loads(sys.argv[1])
assert state["implant_marker"] != "variant-B-single-plant", (
    f"safe_load still let variant B run after patching: {state}"
)
print("OK: post-patch /ingest no longer executes embedded Python objects")
PY

echo "==> ALL CHECKS PASSED"
