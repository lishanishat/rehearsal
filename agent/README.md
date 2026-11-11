# Rehearsal — investigating agent

A real agent loop (Python) that:

1. `GET /_debug/state` from the live target service (`service/app.py`) — real
   in-memory state, including the actually-installed `pyyaml` version.
2. Builds a search query **from that real response** (not hardcoded).
3. Calls a Keenable web-search client (real HTTP, or mock-mode fallback) with
   that query.
4. Turns the top real result into a real claim ("switch to `yaml.safe_load()`").
5. **Tests** the claim for real: resets the target, replays the real exploit
   payload unpatched (to prove it's actually vulnerable), applies the
   service's own `/_debug/patch`, replays the **same** payload again, and
   reads `/_debug/state` + the `/ingest` HTTP response to decide
   `confirmed` / `refuted` from what actually happened.
6. Also runs one deliberately-wrong hypothesis ("it's a Flask threading bug,
   not the YAML loader") through the exact same real test, so there's a
   genuine `refuted` example too — the test decides, not a script.
7. POSTs `searches`, `hypotheses`, and `exploit_replay_after_patch` to the
   shared status server (`shared/mock_status_server.py` / its real
   successor) as it goes, per `shared/README.md`'s schema.
8. Prints a readable, step-numbered log of everything as it happens.

## Files

- `agent.py` — the loop (steps 1–8 above).
- `keenable_client.py` — Keenable HTTP client, real API + mock-mode fallback.
- `test_payloads/fallback_payload.yaml` — a payload **this agent's builder**
  wrote, used only if `service/payloads/*.yaml` doesn't exist yet. Unused in
  the current run since `service/payloads/payload-a.yaml` landed before this
  was finished; `agent.py` prefers the real one automatically.

## Setup

```bash
cd rehearsal
python3 -m venv .venv
./.venv/bin/pip install flask "pyyaml==5.3.1" requests
```

## Run the target + shared status server (separate terminals, or background)

```bash
cd rehearsal
./.venv/bin/python3 service/app.py                 &  # port 8080
./.venv/bin/python3 shared/mock_status_server.py   &  # port 9000
```

## Run the agent

```bash
cd rehearsal/agent
../.venv/bin/python3 agent.py \
  --target http://localhost:8080 \
  --status http://localhost:9000/status.json
# --payload <path> to force a specific payload file; otherwise it auto-picks
# the first file under ../service/payloads/*.yaml, falling back to
# test_payloads/fallback_payload.yaml if that dir is still empty.
```

Env vars work too: `REHEARSAL_TARGET`, `REHEARSAL_STATUS_URL`,
`REHEARSAL_PAYLOAD_PATH`.

### Keenable

```bash
export KEENABLE_API_KEY=keen_...     # real key from the dashboard, once issued
# KEENABLE_BASE_URL defaults to https://api.keenable.ai, override if needed
```

Unset `KEENABLE_API_KEY` and the client runs in **MOCK MODE** — it logs
`[MOCK] ...` loudly to stderr and returns a small fixed set of plausible
PyYAML-advisory-shaped results, so the rest of the pipeline is fully
testable without a key. Set the key and it calls the real documented
endpoint (`POST https://api.keenable.ai/v1/search`, header `X-API-Key`,
body `{"query", "max_results"}`, response `{"results": [{"title","url","snippet"}]}`)
with no code changes — just the env var.

`keenable_client.search_keyless()` is also provided and was used to verify
the real HTTP integration end-to-end against Keenable's actual **keyless**
endpoint (`POST /v1/search/public`, header `X-Keenable-Title`, no API key
required, shared 1000 req/hr/IP) — this is not the code path `agent.py`
uses by default, but proves the real API shape and parsing are correct.

## What was actually run and verified (this session)

- Installed `flask` + `pyyaml==5.3.1` in a local venv; started the real
  `service/app.py` on `localhost:8080` and the real
  `shared/mock_status_server.py` on `localhost:9000`.
- Ran `agent.py` end-to-end against them, using the **real** exploit payload
  that landed mid-build at `service/payloads/payload-a.yaml` (the other
  builder's "thread-beacon" variant).
- Confirmed real results, decided by real HTTP responses and real
  `/_debug/state` reads, not scripted:
  - **Hypothesis #1** ("switch to `yaml.safe_load()`"), from the real
    (mock-mode) search's top result → tested by resetting, confirming the
    unpatched target accepts the payload (`POST /ingest` → HTTP 200,
    unsafe `yaml.load()` actually executes the `!!python/object/apply` tag),
    then patching and replaying the same payload → HTTP 500 `could not
    determine a constructor for the tag '...python/object/apply:...'`
    (`yaml.safe_load()` categorically refuses the tag at parse time). Result:
    **confirmed**, and `exploit_replay_after_patch` → **failed** (good
    outcome — exploit replay failed against the patched target).
  - **Hypothesis #2** (deliberately wrong: "it's a Flask threading bug, not
    the loader") → tested the same way on an unpatched target: the exploit
    still parses/executes (HTTP 200), which contradicts the claim. Result:
    **refuted** — the real test decided this, it wasn't pre-picked.
  - Both hypotheses and the replay result were POSTed to
    `http://localhost:9000/status.json` and read back successfully.
- Verified Keenable's real, documented API shape live by fetching
  `https://keenable.ai`, `https://docs.keenable.ai`, and
  `https://docs.keenable.ai/api-keys.md`, and then actually calling the real
  keyless endpoint (`search_keyless()`) over HTTPS, which returned real
  Snyk/PyYAML advisory results — not guessed.

### One honest caveat found during testing

`payload-a.yaml`'s own background "beacon" thread has a latent bug unrelated
to the vulnerability itself: it does `import sys, threading, time` inside
the `exec()`'d string, but because that `exec()` call sits inside a
constructor *function* (not module scope), the nested `_beacon` closure
can't see those names as globals, so the thread dies with
`NameError: name 'time' is not defined` the moment it runs (visible in the
app's server log). This means `implant_present`/`implant_marker` don't
reliably flip true even on the unpatched target. I did **not** edit
`payload-a.yaml` (out of scope — it belongs to the other builder). Instead
`agent.py`'s vulnerability/fix checks use a second, independent, and
actually more rigorous real signal that isn't affected by that bug: whether
`POST /ingest` itself returns HTTP 200 (unsafe loader accepted and began
executing the tag) vs HTTP 500 `could not determine a constructor for the
tag ...` (safe loader rejected the malicious tag outright, parse-time). The
patch's effect is proven either way; whoever owns `payload-a.yaml` may want
to fix the `exec()` globals bug (e.g. `exec(code, {})`) so the
`implant_present`/heartbeat signal also works for their own needs.

## Left to wire once the human has a real Keenable key

Nothing code-wise — only `export KEENABLE_API_KEY=...` is needed; `search()`
will immediately start hitting the real keyed endpoint instead of mock mode,
with identical downstream handling (same `SearchResult` shape either way).

## Left to wire once a different/second real payload lands

Nothing — `agent.py` auto-discovers any `*.yaml` under `service/payloads/`;
if more than one lands, it currently just uses the first alphabetically
(`find_payload_path`). Trivial to extend to loop over all of them if the
demo wants to show multiple variants.
