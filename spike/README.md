# Spike — run this first, together, before splitting up

This answers one question: **does forking a running Archil sandbox carry its
in-memory state into the clone, and how long does the pause take?**

If this works, Rehearsal is buildable. If it doesn't, stop and regroup.

## Setup (whoever is doing Archil account setup — see main README too)

```bash
cd rehearsal/spike
npm install
```

```bash
export ARCHIL_API_KEY=key-...      # from console.archil.com -> Tokens
export ARCHIL_REGION=aws-us-east-1 # whatever region you picked in the console
export ARCHIL_SANDBOX_ID=01a1c8c8-b76c-7d17-843a-7364df85e57b  # reuse the one you already created, omit to create a fresh one
npm run spike
```

## What to watch for in the output

- `FORK PAUSE: <N>ms` — write this number down. It drives the "no downtime"
  claim in the demo and goes straight into the shared status JSON
  (`fork_pause_ms`).
- `clone's in-memory state after fork` should show the **same** counter
  value and `secret_marker` as right before the fork — proving the clone
  resumed mid-process, not from a cold boot.
- `original's state (should have advanced)` should show a **different**
  (higher) counter — proving the original kept running independently.

## If a method call errors

The exact SDK method names (`client.sandboxes.create`, `sandbox.run`,
`sandbox.fork`, `.stop()`, `.delete()`) were reconstructed from docs.archil.com
during research, not verified live. If anything 404s or is `undefined`:

1. Open https://docs.archil.com/sdks/typescript and
   https://docs.archil.com/compute/sandboxes/introduction.
2. Fix just the API surface in `fork-timing-spike.ts` to match — the test
   logic underneath doesn't need to change.

## Decision gate

- **GO** if the clone carries the in-memory marker forward and the fork
  pause is on the order of low seconds or less.
- **STOP and message the team** if forking a running process loses state or
  is slow/blocked on our plan — tell them before building anything else.
