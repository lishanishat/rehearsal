"""
Rehearsal — shared status server + mock data (both builders).

Zero dependencies (Python stdlib only) so nobody has to `pip install`
anything to start on the dashboard. Serves /status.json with CORS enabled.

Run:
    python3 mock_status_server.py
    open http://localhost:9000/status.json

Builder B: point the dashboard at this right now and build the UI against
the mock data below — don't wait on Builder A's real service or the Archil
spike. Swap the mock for real orchestrator writes later; the JSON shape is
the contract both halves already agreed on (see schema below and
REHEARSAL_BUILD_PLAN.md).
"""

import json
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------------------
# THE SHARED CONTRACT — both builders read/write this shape.
# phase moves: idle -> attacked -> forking -> diagnosing -> rehearsed
#              -> approved -> patched
# ---------------------------------------------------------------------------
STATUS = {
    "phase": "idle",
    "fork_pause_ms": 0,
    "requests_served": 0,
    "requests_failed": 0,
    "searches": 0,
    "hypotheses": [],
    "implant_present": {"production": False, "clone": False, "restarted": False},
    "exploit_replay_after_patch": "pending",
}

_lock = threading.Lock()


def _advance_mock_demo():
    """Walks STATUS through a fake end-to-end run on a loop, purely so the
    dashboard has something changing to render against while real pieces
    aren't wired up yet. Safe to delete once real data is flowing."""
    script = [
        {"phase": "idle", "requests_served": 0},
        {"phase": "attacked", "implant_present": {"production": True, "clone": False, "restarted": False}},
        {"phase": "forking", "fork_pause_ms": 1800},
        {
            "phase": "diagnosing",
            "implant_present": {"production": True, "clone": True, "restarted": False},
            "searches": 1,
            "hypotheses": [
                {"url": "https://example.com/advisory-1", "claim": "CVE-XXXX unsafe yaml.load", "status": "confirmed"}
            ],
        },
        {
            "phase": "diagnosing",
            "searches": 2,
            "hypotheses": [
                {"url": "https://example.com/advisory-1", "claim": "CVE-XXXX unsafe yaml.load", "status": "confirmed"},
                {"url": "https://example.com/red-herring", "claim": "unrelated memory leak bug", "status": "refuted"},
            ],
        },
        {"phase": "rehearsed", "exploit_replay_after_patch": "failed"},  # exploit fails on the rehearsed fix = good
        {"phase": "approved"},
        {
            "phase": "patched",
            "implant_present": {"production": False, "clone": True, "restarted": False},
        },
    ]
    i = 0
    while True:
        with _lock:
            STATUS.update(script[i % len(script)])
            STATUS["requests_served"] += 7
        i += 1
        time.sleep(4)


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/status.json":
            with _lock:
                body = json.dumps(STATUS).encode()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self._cors()
            self.end_headers()

    def do_POST(self):
        # Orchestrator (or the Approve button) posts a partial update here.
        if self.path == "/status.json":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                update = json.loads(raw)
            except json.JSONDecodeError:
                update = {}
            with _lock:
                STATUS.update(update)
                body = json.dumps(STATUS).encode()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self._cors()
            self.end_headers()

    def log_message(self, *a):
        pass  # keep the console quiet


if __name__ == "__main__":
    import os
    if os.environ.get("MOCK_DEMO", "0") == "1":
        threading.Thread(target=_advance_mock_demo, daemon=True).start()
        print("(MOCK_DEMO=1: cycling fake data every ~4s. Run with MOCK_DEMO=0 once real data is flowing.)")
    else:
        print("(MOCK_DEMO=0: fake demo loop disabled — only real POSTs will update status.json)")
    server = ThreadingHTTPServer(("127.0.0.1", 9000), Handler)
    print("Mock status server: http://localhost:9000/status.json")
    server.serve_forever()
