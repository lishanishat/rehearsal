# Shared contract (both builders)

## Start the mock status server now (Builder B, no waiting required)

```bash
cd rehearsal/shared
python3 mock_status_server.py
```

Open http://localhost:9000/status.json in a browser — you'll see JSON that
changes every ~4 seconds, cycling through a fake end-to-end run:
`idle -> attacked -> forking -> diagnosing -> rehearsed -> approved -> patched`.

Build the dashboard against this right now. When the real orchestrator is
ready, it `POST`s partial updates to the same `/status.json` endpoint and
you swap the URL — the shape doesn't change.

## The shape (the one thing both halves must agree on)

```json
{
  "phase": "idle|attacked|forking|diagnosing|rehearsed|approved|patched",
  "fork_pause_ms": 0,
  "requests_served": 0,
  "requests_failed": 0,
  "searches": 0,
  "hypotheses": [{"url": "", "claim": "", "status": "confirmed|refuted"}],
  "implant_present": {"production": true, "clone": true, "restarted": false},
  "exploit_replay_after_patch": "pending|failed|succeeded"
}
```

- `exploit_replay_after_patch: "failed"` means the replayed exploit **failed
  to work** against the patched clone — that's the good outcome, the one
  the demo is building to.
- `implant_present.restarted` should go to `false` early and stay there —
  it's the "restart destroys the evidence" side of the side-by-side proof.

Don't change this shape without telling the other builder — it's the only
thing connecting the two halves of the build.
