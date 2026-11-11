"""
Rehearsal — the demo target service (Builder A).

Runs standalone, no Archil needed to develop/test this file locally:
    pip install flask pyyaml==5.3.1
    python3 app.py
    curl localhost:8080/

This is a DEMO FIXTURE for a hackathon security/reliability project: it
deliberately uses an old, known-unsafe yaml.load() call so our own agent can
find and fix a real, documented vulnerability class (not a toy string we
invented). Nothing here phones out or targets anyone else's system.

Three things live in this one process:
  1. Normal traffic: GET /  (what the live request ticker hits)
  2. The vulnerability: POST /ingest — takes YAML and loads it unsafely.
     This is the textbook PyYAML yaml.load() code-execution issue; pin
     PyYAML to an old version (e.g. 5.3.1) so the behavior is real, not
     simulated.
  3. Debug hooks (localhost-only, never exposed publicly):
       GET  /_debug/state  -> dumps in-memory markers (for agents to read)
       POST /_debug/patch  -> swaps the unsafe loader for a safe one, LIVE,
                              no process restart (this is what makes the
                              "operate without downtime" claim real)

TODO (Builder A, in order):
  [ ] Confirm PyYAML 5.3.1 (or similar pre-fix version) installs in whatever
      base image the sandbox uses. If it won't, fall back to calling
      yaml.load(data) directly in-app without a Loader= argument (also
      unsafe, just less "it's a real CVE in a real library version").
  [ ] Write the actual attack payload (two variants, per the build plan —
      judge picks one). A payload should set IMPLANT["present"] = True and
      IMPLANT["marker"] to something identifying which variant ran.
  [ ] Decide exactly what "removes the implant from live memory" means for
      /_debug/patch - at minimum: flip IMPLANT["present"] back to False and
      replace _unsafe_load with _safe_load as the active loader.
  [ ] Wire /ingest's 500s (if a bad patch breaks parsing) into the ticker's
      failed-request counter.
"""

from flask import Flask, request, jsonify
import yaml
import time
import threading

app = Flask(__name__)

START_TIME = time.time()

# In-memory only. Nothing here touches disk. This is the "evidence that
# restarting the process would destroy" — the whole point of the project.
STATE = {
    "requests_served": 0,
    "requests_failed": 0,
    "implant_present": False,
    "implant_marker": None,
    "patched": False,
}
_lock = threading.Lock()


def _unsafe_load(raw: str):
    # Vulnerable path: yaml.load without a safe Loader can execute arbitrary
    # Python objects embedded in the YAML (the real, historical PyYAML
    # issue). This is what /_debug/patch needs to neutralize.
    return yaml.load(raw, Loader=yaml.Loader)  # noqa: S506 (intentional, demo fixture)


def _safe_load(raw: str):
    return yaml.safe_load(raw)


# The "active loader" is a mutable reference so /_debug/patch can swap it
# live, in the running process, with no restart.
_active_loader = {"fn": _unsafe_load}


@app.route("/")
def index():
    with _lock:
        STATE["requests_served"] += 1
    return jsonify(status="ok", uptime_s=round(time.time() - START_TIME, 1))


@app.route("/health")
def health():
    return jsonify(status="ok")


@app.route("/ingest", methods=["POST"])
def ingest():
    """Normal-looking feature: accepts YAML config from a client.
    This is the entry point for the exploit. TODO: Builder A fills in what
    a real payload does here (plant the in-memory marker)."""
    raw = request.get_data(as_text=True)
    try:
        with _lock:
            STATE["requests_served"] += 1
        result = _active_loader["fn"](raw)
        return jsonify(status="parsed", result_type=str(type(result)))
    except Exception as e:  # noqa: BLE001 (demo fixture)
        with _lock:
            STATE["requests_failed"] += 1
        return jsonify(status="error", error=str(e)), 500


# ---------------------------------------------------------------------------
# Debug hooks — localhost only. Do not expose these on a public port.
# ---------------------------------------------------------------------------

def _require_localhost():
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify(error="localhost only"), 403
    return None


@app.route("/_debug/state", methods=["GET"])
def debug_state():
    denied = _require_localhost()
    if denied:
        return denied
    with _lock:
        state = dict(STATE)
    # Real, not hardcoded: read the ACTUAL installed PyYAML version at
    # runtime, so the agent has to look this up rather than being told it.
    state["libraries"] = {"pyyaml": getattr(yaml, "__version__", "unknown")}
    return jsonify(state)


@app.route("/_debug/patch", methods=["POST"])
def debug_patch():
    """Apply the rehearsed fix to the LIVE process. No restart.
    This endpoint is the whole "operate without downtime" claim — keep it
    this simple and make sure it actually works before adding anything else."""
    denied = _require_localhost()
    if denied:
        return denied
    with _lock:
        _active_loader["fn"] = _safe_load
        STATE["implant_present"] = False
        STATE["patched"] = True
    return jsonify(status="patched", patched=True)


@app.route("/_debug/reset", methods=["POST"])
def debug_reset():
    """Dev convenience only — resets state for re-running the demo."""
    denied = _require_localhost()
    if denied:
        return denied
    with _lock:
        STATE.update(
            requests_served=0,
            requests_failed=0,
            implant_present=False,
            implant_marker=None,
            patched=False,
        )
        _active_loader["fn"] = _unsafe_load
    return jsonify(status="reset")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
