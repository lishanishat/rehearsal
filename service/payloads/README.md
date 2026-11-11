# Rehearsal demo payloads

Two self-contained YAML payloads that exploit the real, historical PyYAML
`yaml.load(raw, Loader=yaml.Loader)` arbitrary-object-construction issue in
`app.py`'s `POST /ingest` endpoint. Both use the `!!python/object/apply:` tag
to call `builtins.exec` with a short string of Python source, which then
runs inside the Flask worker process and plants a marker in the service's
in-memory `STATE` dict.

**Both are inert by design**: no network calls, no subprocess, no file
access outside the running process's own memory, and no persistence beyond
that process (a daemon thread dies with the process; nothing is written to
disk or outside `/tmp`). They exist purely to be discovered and fixed live
in the Rehearsal demo.

## The two variants

| | `payload-a.yaml` | `payload-b.yaml` |
|---|---|---|
| Marker | `variant-A-thread-beacon` | `variant-B-single-plant` |
| Mechanism | Spawns a daemon thread that re-writes the marker every second, forever | Writes the marker once and returns immediately — no thread, no loop |
| `/_debug/state` after a few seconds | `implant_heartbeat` keeps climbing | no `implant_heartbeat` key ever appears |
| `implant_present` after `/_debug/patch` | flips back to `True` within ~1s (the background thread keeps re-asserting it — patching the endpoint doesn't kill an already-running thread) | stays `False` for good (nothing left running to flip it back) |

An agent that polls `GET /_debug/state` a couple of times a second or two
apart can tell the two apart from that behavior alone — it doesn't need to
be told which payload was used. This difference is also the more realistic
story for a demo: patching the vulnerable code path stops *new* exploitation
immediately, but variant A shows that a *previously planted* implant can
still need a full process restart to truly clear, while variant B shows a
case that patching alone fully resolves.

## Usage

Post either payload directly:

```bash
curl -X POST --data-binary @payload-a.yaml http://127.0.0.1:8080/ingest
curl -X POST --data-binary @payload-b.yaml http://127.0.0.1:8080/ingest
```

Or use the helper script:

```bash
python3 make_request.py http://127.0.0.1:8080 a
python3 make_request.py http://127.0.0.1:8080 b
```

Then inspect the implant and patch it live:

```bash
curl http://127.0.0.1:8080/_debug/state
curl -X POST http://127.0.0.1:8080/_debug/patch
curl http://127.0.0.1:8080/_debug/state   # patched: true
```

## Files

- `payload-a.yaml`, `payload-b.yaml` — the two exploit documents (heavily
  commented in-file; each comment block explains the mechanism and why it's
  safe).
- `make_request.py` — POSTs `payload-<a|b>.yaml` to `<base_url>/ingest`.
  Usage: `python3 make_request.py <base_url> <a|b>`.
- `test_locally.sh` — end-to-end smoke test: builds a local venv, starts the
  service in the background, posts variant A, confirms the implant shows up
  in `/_debug/state` (including the growing heartbeat), calls
  `/_debug/patch`, confirms `patched: true`, confirms a post-patch exploit
  attempt no longer executes embedded Python objects, then stops the
  background server. Run it with `bash test_locally.sh` from this
  directory. It was run and passes as of this writing.
- `_run_app_on_port.py` — test-only helper used by `test_locally.sh`. It
  runs `app.py`'s actual, unmodified code via `runpy` but monkeypatches
  `Flask.run()`'s `port` argument at test time (defaulting to `18080`
  instead of the hardcoded `8080`) so the smoke test can't collide with
  anything else already bound to 8080 on the machine running the test.
  It does not change `app.py` on disk. Set `REHEARSAL_TEST_PORT=8080`
  before running `test_locally.sh` to test the literal default port
  instead.

## Note for whoever owns `app.py`

No changes to `app.py` were needed to make these payloads work — the
exploit, the `/_debug/state` marker fields, and `/_debug/patch` all worked
exactly as already implemented. The only thing worth knowing: `/_debug/patch`
sets `implant_present = False` but does not (and, as an in-process endpoint,
realistically cannot) kill any threads a prior exploit already started —
that's intentional and is exactly what variant A is built to demonstrate.
