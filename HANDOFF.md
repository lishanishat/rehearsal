# Rehearsal — Handoff (open this file first)

**If you are an AI assistant reading this:** this is a live, in-progress
hackathon build. Read this whole file before touching anything. Everything
below is true as of the last session; don't re-derive it from scratch,
build on it. The user is under extreme time pressure (an Archil hackathon,
table demos at 9:30pm) — be decisive, don't ask unnecessary questions,
assume this working directory (`rehearsal/`) unless told otherwise.

## The project, in one paragraph

**Rehearsal**: when a server gets compromised, the evidence usually lives
only in memory — restarting it erases the evidence, and debugging it live
risks an outage. Rehearsal forks the live, compromised sandbox (using
Archil's ability to clone a *running* process, memory included), proves a
restarted copy loses the evidence while the fork keeps it, has an AI agent
search the web for the real fix and test it for real on a clone, then
applies the proven fix to production live, no restart. Built for "Agents
Need Solid Ground: An Archil Hackathon," judged on Most Creative / Best Use
of Archil / Technical Execution, plus a Keenable (web search API) sponsor
prize.

## Everything verified true tonight (don't re-test these, trust them)

- **Archil Sandboxes** (not disk.exec/branches) are the right primitive.
  Forking a **running** sandbox carries its live in-memory process state
  forward — proven with a real counter that kept incrementing through a
  real fork, across separate `.run()` calls.
- **Fork pause measured 4.7s–8.0s** across several real runs. The pitch
  must say "a short, measured hold" — never "zero downtime" or "instant."
- **The exact working pattern for a background process to survive between
  separate `.run()` calls** (this took real debugging to find — see "Bugs
  already found and fixed" below for what does NOT work):
  ```bash
  cd /root
  setsid nohup python3 app.py < /dev/null > /root/service.log 2>&1 &
  echo "service PID: $!"
  ```
  No `disown` needed. No `set -e` in the wrapping script (see below for why).
- **PyYAML 5.3.1 and Flask both import and work fine on Python 3.13.**
  This was our top suspect for a while and it's a dead end — don't
  re-investigate it.
- **`app.py`'s `/_debug/state` and `/_debug/patch` are gated to
  localhost only** (`request.remote_addr in (127.0.0.1, ::1)`). Hitting a
  sandbox's public exposed URL for these two routes will 403 — this is
  correct, by design, not a bug. Anything that needs them (the
  orchestrator, eventually the agent) must run the check **inside** the
  sandbox via `sandbox.run("curl ... localhost:8080/...")`, not from
  outside over the public URL.
- **Keenable's real API** (found live, not guessed): `POST
  https://api.keenable.ai/v1/search` with header `X-API-Key: <key>`, body
  `{"query","max_results"}`. Keyless alternative: `POST
  /v1/search/public` with header `X-Keenable-Title` (shared rate limit).
  See `agent/keenable_client.py` — it has a real implementation plus an
  honest, loudly-logged mock mode if `KEENABLE_API_KEY` isn't set.

## Bugs already found and fixed tonight (don't rediscover these)

1. **The original orchestrator boot script had `set -e` plus a `disown`
   call.** This shell's `/bin/sh` doesn't have `disown` as a builtin, so
   it failed with "command not found" — and `set -e` made that single
   failure abort the *entire* boot script immediately, before it ever
   reached the health check or printed "booted". This is why production
   silently never came up the first time, with no loud error. **Already
   fixed** in `orchestrator/orchestrator.ts`'s `bootScript()` function —
   `set -e` and `disown` are both removed now, matching the proven
   pattern above exactly.
2. **A diagnostic script (`spike/diagnose.ts`) once ran `pkill -f "python3
   app.py"` and killed itself**, because the whole script is passed to the
   shell as one command string that itself contains the literal text
   "python3 app.py", so the pattern matched its own invocation. Lesson: be
   very careful with any `pkill -f` / `pgrep -f` inside a script run this
   way — removed, not needed.

## What's built, where, and its real status

| Path | What it is | Status |
|---|---|---|
| `service/app.py` | The vulnerable Flask demo service (old pinned PyYAML, `/ingest`, `/_debug/state`, `/_debug/patch`, `/_debug/reset`) | **Done, works.** Runs locally fine; also runs fine inside a sandbox once booted with the proven pattern above. |
| `service/payloads/payload-a.yaml`, `payload-b.yaml` | Two real YAML exploit payloads via PyYAML's `!!python/object/apply`. A plants a persistent in-memory beacon thread (`implant_heartbeat` keeps climbing, survives a patch because the thread itself isn't killed by patching the loader); B plants once, no thread. | **Done, tested, both work.** See `service/payloads/README.md` for exact behavior differences. |
| `shared/mock_status_server.py` | Zero-dependency Python status server. Serves/accepts `POST`/`GET http://localhost:9000/status.json`. Has a `MOCK_DEMO` env var (`1`=cycles fake demo data for dashboard dev, `0`=real data only). | **Done, works.** Run with `MOCK_DEMO=0 python3 mock_status_server.py` once real data should flow. |
| `dashboard/index.html` | Single-page dashboard: Freeze/Prove/Operate panels, Attack button (payload A/B), Approve button, live ticker. Polls `:9000/status.json`. | **Done, visually verified against mock data.** `ATTACK_URL`/`APPROVE_URL` constants at top of its `<script>` need to point at wherever the orchestrator actually listens (currently **8787**, not 8099 — check it's set correctly). |
| `agent/agent.py` | The investigating agent. Reads real `/_debug/state`, has an LLM (free keyless pollinations.ai endpoint by default, no API key needed — can be slow/rate-limited, see below) diagnose it and write a real search query, calls Keenable, classifies results as testable, **actually tests** each by resetting/patching/replaying the real exploit, records real confirmed/refuted. | **Written by a teammate, newer/better than an earlier subagent version (kept as `agent_subagent_backup.py` as a fallback). Last known issue: when pointed at the sandbox's PUBLIC url, it 403s on `/_debug/state`/`/_debug/patch` (expected, see above) — it was being re-tested against the LOCAL service (`http://localhost:8080`) instead when the session ended. Re-run that local test first; it should work end to end.** |
| `agent/keenable_client.py` | Real Keenable HTTP client + honest mock mode. | **Done, verified the real keyless endpoint returns real results.** |
| `orchestrator/orchestrator.ts` | Boots production/clone/restarted sandboxes, exposes port 8080, `/attack` (POST payload, fork, boot a fresh "restarted" copy, compare implant presence), `/approve` (patch production live), a 1s ticker, `GET /sandboxes`. | **Boot bug just fixed (see above). Needs a restart to pick up the fix, then a real `/attack` call to verify the clone+restarted sandboxes also boot correctly with the fix.** Listens on **port 8787**. |
| `spike/fork-timing-spike.ts`, `spike/diagnose.ts` | Reference/diagnostic scripts used to find all of the above. Not part of the final demo, but useful if something regresses — they contain known-working Archil SDK call patterns. | Reference only. |

## Exact state of the last-known-running sandbox (may be stale by the time you read this — verify, don't assume)

- Most recent **production** sandbox id: `01a1c903-6432-7bc5-9a12-609e1bc156ec`,
  region `aws-us-west-2`, exposed at
  `https://8080-agq4sa3egj54lgqsmcpbxqkw5q.prod-aws-us-west-2-green.archil.app`
  — this was booted with the FIXED boot script and its `/health` was
  confirmed working externally. **Check `curl .../health` again before
  trusting it's still up** — sandboxes/processes may have been
  stopped/cleaned up since.
- Get current sandbox info any time the orchestrator is running:
  `curl http://localhost:8787/sandboxes`

## Credentials (rotate after tonight, don't ask the user to repaste them)

- An `ARCHIL_API_KEY` and `ARCHIL_REGION=aws-us-west-2` were exported in
  various terminal tabs tonight. **Do not ask the user to paste the key
  into chat** — just ask them to `export ARCHIL_API_KEY=...` in whatever
  terminal they run commands in, or check if it's already set
  (`echo $ARCHIL_API_KEY`) in a tab they're already using.
- A `KEENABLE_API_KEY` may or may not be set yet — `agent/keenable_client.py`
  works in an honest mock mode without one.

## Immediate next steps, in priority order

1. **Re-verify production is still up**: `curl <base-url-above>/health`.
   If it's down, restart the orchestrator (`cd orchestrator && npm start`
   — needs `ARCHIL_API_KEY`/`ARCHIL_REGION` set) and wait for
   `[orchestrator] production exposed at https://...`.
2. **Test `agent/agent.py` against the LOCAL service** (not the sandbox —
   see the localhost-gating note above) to confirm the full real
   diagnose → search → test → confirm/refute pipeline works:
   ```bash
   cd service && python3 -m pip install -q flask "pyyaml==5.3.1" && python3 app.py
   # in another tab:
   cd agent && python3 -m pip install -q requests && python3 agent.py --target http://localhost:8080 --status http://localhost:9000/status.json
   ```
3. **Trigger a real `/attack`** against the orchestrator to confirm the
   fixed boot script works for the clone and restarted sandboxes too:
   ```bash
   curl -X POST http://localhost:8787/attack -H "Content-Type: application/json" -d '{"variant":"a","payloadYaml":"<contents of service/payloads/payload-a.yaml>"}'
   ```
4. **Fix the dashboard's endpoint constants** if not already done (top of
   `dashboard/index.html`'s `<script>` — should point at port 8787).
5. **Decide how the agent reaches `/_debug/state` on the real sandbox for
   the actual demo** — either have the orchestrator run the agent's logic
   itself via `sandbox.run()`, or add an orchestrator endpoint that proxies
   those two calls in from outside. Given the clock, the fastest fix is
   probably: orchestrator exposes a `GET /debug-state/:role` and `POST
   /debug-patch/:role` that internally do `sandbox.run("curl
   localhost:8080/_debug/state")`, and point `agent.py` at those instead of
   the gated routes directly.
6. **Record a fallback video** the moment everything works once, even a
   partial version — a flaky live demo beats nothing.

## Folder to open

Open **this folder** (`rehearsal/`) as the project root in the new
session — everything referenced above is relative to it.
