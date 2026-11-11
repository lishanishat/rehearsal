# Rehearsal

Built at **Agents Need Solid Ground: An Archil Hackathon** (Nov 10, 2026).

**The pitch:** when a server gets compromised, the evidence usually lives
only in memory — restart it and the evidence is gone, debug it live and
you risk an outage. Rehearsal forks the live, compromised machine using
Archil's ability to clone a *running* process (memory included), proves a
restart would have erased the evidence while the fork keeps it, has an AI
agent search the web for the real fix and test it for real against a
clone, then applies the proven fix to production live — no restart.

## What's real and verified (not just working code — actually run and checked)

- **Archil sandbox forking carries live, in-memory process state forward.**
  Verified with a real counter that kept incrementing through a real fork,
  across separate calls — not just a value sitting in a file.
- **Fork pause measured 4.5s–8.0s** across multiple real runs. We say that
  number out loud in the demo rather than claiming "instant" or "zero
  downtime" — it's a short, measured hold, and that's the honest and more
  credible claim.
- **The investigating agent runs fully end to end, for real:** it reads
  the service's actual live state, has an LLM form its own web search
  query from that state (not a hardcoded query), calls a real search API
  (Keenable), classifies which results are actually testable, and tests
  each one for real — resetting the service, confirming it's genuinely
  vulnerable, applying the fix, and replaying the real exploit against the
  patched service to see if it still works. Confirmed/refuted results come
  from what actually happened, not a script.
- **A real exploit, not a toy string:** the demo service pins an old,
  real-world-vulnerable PyYAML version and exploits its real unsafe
  `yaml.load()` behavior via `!!python/object/apply` — the same technique
  as a real historical PyYAML vulnerability class, not an invented bug.

## Honest gap: the two halves aren't connected yet

We ran out of time to wire these two proven pieces into one live pipeline,
and we'd rather say that plainly than imply it's seamless:

1. **The Archil fork mechanism** is proven against a real sandbox (see
   `spike/fork-timing-spike.ts` and its measured results above).
2. **The exploit → investigate → fix pipeline** is proven end to end (see
   `agent/agent.py`), but only against the service running **locally**,
   not yet against a copy running **inside** an Archil sandbox.

The reason: the demo service's debug endpoints (`/_debug/state`,
`/_debug/patch` — what the agent needs to read state and apply the fix)
are deliberately restricted to localhost-only requests, for security. A
request from the agent's laptop to the sandbox's public URL arrives
through Archil's gateway, not from inside the sandbox, so it's correctly
rejected. Our orchestrator works around this today by running `curl`
*inside* the sandbox (`sandbox.run(...)`) for its own health checks — the
agent doesn't do this yet. That's the specific, known piece of wiring
left, not a hidden failure in either half. See `HANDOFF.md` for the exact
next step we'd take.

## Project layout

- `service/` — the vulnerable demo Flask service (old pinned PyYAML) with
  `/_debug/state` and `/_debug/patch` (patches the *live* process, no
  restart).
- `service/payloads/` — two real exploit payloads. **Use `payload-b.yaml`
  for demos** — `payload-a.yaml` deliberately plants a persistent
  background thread that survives a patch (an intentional, interesting
  finding on its own — see `service/payloads/README.md` — but it
  permanently contaminates a running service process until it's
  restarted, so it's not repeatable mid-demo).
- `agent/` — the investigating agent (`agent.py`) and a real Keenable
  search client (`keenable_client.py`, with an honest, loudly-logged mock
  mode if no API key is set).
- `orchestrator/` — boots the service inside a real Archil sandbox, forks
  it, boots a freshly-restarted copy for comparison, and can apply the
  patch to production live.
- `dashboard/` — a single-page live dashboard (Freeze / Prove / Operate
  panels) polling a shared status endpoint.
- `shared/` — the shared status server + JSON schema both halves read and
  write.
- `spike/` — the scripts that proved the core Archil fork mechanism and
  diagnosed real bugs along the way (kept as reference, not part of the
  final demo path).
- `HANDOFF.md` — a detailed, point-in-time account of everything verified,
  every bug found and fixed, and the exact next steps, written mid-build
  for session continuity. Still accurate as a technical log.

## Running it

See the `README.md` inside each subfolder for exact setup/run commands.
Quick version, for the demo path that's fully proven:

```bash
# 1. Start the local vulnerable service
cd service && python3 -m pip install -q flask "pyyaml==5.3.1" && python3 app.py

# 2. Start the shared status server (another terminal)
cd shared && MOCK_DEMO=0 python3 mock_status_server.py

# 3. Open the dashboard (another terminal)
cd dashboard && python3 -m http.server 8777
# then open http://localhost:8777/index.html

# 4. Run the real investigation (another terminal)
curl -X POST localhost:8080/_debug/reset
cd agent && python3 -m pip install -q requests && \
  python3 agent.py --target http://localhost:8080 \
    --status http://localhost:9000/status.json \
    --payload ../service/payloads/payload-b.yaml
```

For the Archil fork proof:

```bash
cd spike
export ARCHIL_API_KEY=<your key>
export ARCHIL_REGION=aws-us-west-2
npx tsx fork-timing-spike.ts
```
