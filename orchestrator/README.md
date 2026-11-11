# Rehearsal — orchestrator

The core piece: creates/forks real Archil sandboxes running `service/app.py`,
attacks production, proves fork-keeps-evidence vs restart-loses-it, and
live-patches production with no restart. Writes progress to the shared
status server as it goes.

## Setup

```bash
cd rehearsal/orchestrator
npm install
```

## Required env vars

```bash
export ARCHIL_API_KEY=key-...        # from console.archil.com -> Tokens
export ARCHIL_REGION=aws-us-west-2   # or wherever your account/image is
```

Optional:

```bash
export STATUS_URL=http://localhost:9000/status.json   # default shown
export ORCH_PORT=8787                                   # default shown
```

## Run

1. Start the shared status server (separate terminal, if not already running):
   ```bash
   cd rehearsal/shared
   python3 mock_status_server.py
   ```
2. Start the orchestrator:
   ```bash
   cd rehearsal/orchestrator
   npm start
   ```
   On boot it immediately creates and boots the **production** sandbox
   (`baseImage: python:3.13`, pip-installs `flask` + `pyyaml==5.3.1`, writes
   the real `service/app.py` into it via heredoc, starts it with
   `setsid nohup ... &` so it survives past the booting call, exposes port
   8080 publicly). This takes **well over a minute** (sandbox create + pip
   install), so give it time before calling `/attack`. Watch the console —
   it logs each step. Once up, a ticker starts hitting production's `/`
   every ~1s and posts `requests_served`/`requests_failed` to the status
   server.

## HTTP API (this process, default port 8787)

- `GET /health` — `{status, productionReady}`
- `GET /sandboxes` — current sandbox ids + URLs for production/clone/restarted
- `POST /attack` — body `{"variant": "a"|"b", "payloadYaml": "<yaml string>"}`
  1. POSTs `payloadYaml` to production's public `/ingest`.
  2. Forks the running, now-attacked production sandbox -> **clone** (timed;
     `fork_pause_ms` is the real `sandbox.fork()` wall time).
  3. In parallel, boots a brand-new, never-attacked sandbox from the same
     clean image -> **restarted** (stands in for "what production looks like
     after a restart": same code, no in-memory implant, by construction).
  4. Checks `/_debug/state` on all three (see caveat below) and posts
     `implant_present: {production, clone, restarted}` + `fork_pause_ms` +
     `phase: "diagnosing"` to the status server.
  5. Returns `fork_pause_ms`, `implant_present`, raw `/_debug/state` for all
     three, and each sandbox's id + URLs (`/`, `/health`, `/ingest`,
     `/_debug/state`, `/_debug/patch`).
- `POST /approve` — body `{"source": "clone"}` (source is accepted but not
  currently branched on — there's one patch action, `_safe_load`, so
  "copying the clone's rehearsed fix" and "applying the fix" are the same
  call). Calls production's `/_debug/patch` live (no restart), replays the
  last attack payload against production to prove the exploit now fails,
  and posts `phase: "patched"`, `implant_present.production: false`,
  `exploit_replay_after_patch: "failed"|"succeeded"` ("failed" = good, the
  exploit no longer works).

## Important caveat: `/_debug/*` and the public exposed port

`service/app.py`'s `/_debug/state` and `/_debug/patch` are deliberately
gated to `request.remote_addr in (127.0.0.1, ::1)`. An Archil **exposed**
port (`sandbox.exposePort(8080)`) is reached through a public
TLS-terminating gateway — Flask sees the gateway's address, not localhost —
so hitting `/_debug/state` or `/_debug/patch` **through the public exposed
URL will 403**. That's `app.py`'s own guard working as intended, not a bug
here.

This orchestrator therefore never calls `/_debug/*` over the public URL
itself — it runs `curl` **inside** the sandbox via `sandbox.run()`, which
genuinely originates from localhost. `/attack`'s response still includes
`debug_state` / `debug_patch` URLs for every sandbox (per the spec, and
useful for manual debugging), but expect those two specific routes to 403
if called directly from your laptop. `/`, `/health`, and `/ingest` are not
gated and work fine over the public URL — those are what the agent-loop
piece should use for traffic/exploit replay; for implant/patch state it
should do the same in-sandbox-`curl` trick, or you can add a thin
"debug proxy" endpoint here later if the demo needs it externally too.

## What's verified vs. untested

**No `ARCHIL_API_KEY` was available in this environment**, so the real
Archil calls (`sandboxes.create`, `.run`, `.fork`, `.exposePort`) were
**not exercised end-to-end here**. What *was* verified in this session:

- The orchestrator's HTTP server starts, logs the missing-key warning
  instead of crashing, and all four routes (`/health`, `/sandboxes`,
  `/attack`, `/approve`) respond/behave correctly without a key (e.g.
  `/attack` correctly validates `payloadYaml` and then blocks on
  `productionReady` rather than crashing).
- `npx tsc --noEmit` passes cleanly against `disk@1.12.1`'s real shipped
  type declarations (`node_modules/disk/dist/archil-CLQrudfP.d.mts`) — I
  read those declarations directly and confirmed `Sandbox.run()`,
  `.exec()`, `.fork(options?)`, `.exposePort(port): Promise<string>`,
  `.listPorts()`, `.unexposePort()`, and `Sandboxes.create()/get()` all
  match exactly what this code calls (not guessed from docs — read from
  the actual installed package).
- `app.py`'s real content is read live off disk at startup
  (`fs.readFileSync(../service/app.py)`) and embedded into the boot
  heredoc, so it can never drift from Builder A's file.
- The `setsid nohup ... < /dev/null > log 2>&1 &` backgrounding pattern and
  the `fork_pause_ms` timing approach are copied directly from
  `spike/fork-timing-spike.ts`, which the team confirmed works against a
  real Archil account tonight (4.7s–8.0s fork pause).
- The port-exposure API (`sandbox.exposePort(port) -> Promise<string>`
  hostname, build the reachable URL as `https://${hostname}`) was
  confirmed against `https://docs.archil.com/compute/sandboxes/introduction`
  and `.../api-reference/sandboxes/expose-sandbox-port.md` just now, not
  assumed.

**Genuinely untested / best-effort, flag for the human to check first:**

- Whether `pip install flask "pyyaml==5.3.1"` succeeds unmodified on the
  `python:3.13` base image (newer Debian/pip may need
  `--break-system-packages` — the boot script already tries plain
  `pip install` first and falls back to `--break-system-packages` if that
  fails, but neither path has been run for real).
  Note: `pyyaml==5.3.1` (2020-era) may fail to build/wheel on Python 3.13
  specifically — if `/attack` fails at the boot step, check
  `/root/service.log` inside the sandbox (via `sandbox.run("cat /root/service.log")`
  or the `/sandboxes` debug info) first; app.py's own TODO comment already
  flags this as a known risk and suggests falling back to calling
  `yaml.load(data)` with no `Loader=` if the pin won't install.
- Whether a forked sandbox needs its own fresh `exposePort(8080)` call
  (assumed yes, since it's a new `Sandbox` object/id) — not verified live.
- The exact wall-clock time for `sandboxes.create` + pip install + flask
  boot (expect roughly 30–90s; not measured here).
- `exploit_replay_after_patch` logic in `/approve` (replays the same
  payload against patched production and checks `/_debug/state`) — logic
  only, not run against a live sandbox.

## Files

- `orchestrator.ts` — everything above, single file by design (hackathon
  time budget).
- `package.json`, `tsconfig.json` — `disk@^1.12.1`, run via `tsx`.
