# The demo service (Builder A)

Runs entirely on your laptop. No Archil account needed to start this.

```bash
cd rehearsal/service
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

```bash
curl localhost:8080/
curl localhost:8080/_debug/state
curl -X POST localhost:8080/ingest -d "key: value"
```

## What's done

- `/` — normal traffic endpoint (for the ticker).
- `/_debug/state` — localhost-only, dumps in-memory state (requests served,
  whether the implant is present, whether it's patched).
- `/_debug/patch` — localhost-only, swaps the unsafe YAML loader for the
  safe one **live, with no process restart**. Try it:
  ```bash
  curl -X POST localhost:8080/_debug/patch
  curl localhost:8080/_debug/state   # patched: true
  ```
- `/_debug/reset` — resets state for re-running the demo.

## TODO (see top-of-file docstring in `app.py` for details)

1. Confirm `pyyaml==5.3.1` installs in whatever base image we end up using
   in the sandbox. If it fails there, there's a documented fallback in the
   docstring.
2. Write the actual attack payload(s) that POST to `/ingest` and flip
   `STATE["implant_present"] = True` — give the judge 2 variants to choose
   from (per the build plan, this is what keeps the agent from looking
   scripted).
3. Once the Step 0 spike is a GO, wire this into a sandbox boot script
   (same shape as `rehearsal/spike/fork-timing-spike.ts`'s `bootScript`).
