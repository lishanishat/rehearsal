/**
 * STEP 0 SPIKE — the go/no-go gate for "Rehearsal".
 *
 * What this proves (or disproves):
 *   1. We can start a persistent Archil sandbox running a process that holds
 *      state only in memory.
 *   2. We can fork that RUNNING sandbox and the fork resumes mid-process,
 *      with the in-memory state intact.
 *   3. The fork is fast enough to be a believable "no restart needed" demo.
 *
 * If step 2 fails (fork loses the in-memory state) or fork time is multiple
 * seconds, STOP and tell the team — both competing ideas depended on this,
 * and Rehearsal's whole pitch rests on it.
 *
 * Run:
 *   npm install disk
 *   ARCHIL_API_KEY=... ARCHIL_REGION=aws-us-east-1 npx tsx fork-timing-spike.ts
 *
 * NOTE: method names below (client.sandboxes.create, sandbox.run, sandbox.fork,
 * sandbox.connections / exposePort) are reconstructed from docs.archil.com
 * during research tonight, not from a live test. If any call below 404s or
 * is undefined, check https://docs.archil.com/sdks/typescript and
 * https://docs.archil.com/compute/sandboxes/introduction for the exact
 * current method name/shape, fix it here, and keep going — the goal of this
 * file is to get an answer in the first 20 minutes, not to be pretty.
 */

import { Archil } from "disk";

const API_KEY = process.env.ARCHIL_API_KEY;
const REGION = process.env.ARCHIL_REGION || "aws-us-east-1";
const EXISTING_SANDBOX_ID = process.env.ARCHIL_SANDBOX_ID; // reuse one created in the console instead of making a new one

if (!API_KEY) {
  console.error("Set ARCHIL_API_KEY first (from console.archil.com -> Tokens).");
  process.exit(1);
}

function now() {
  return Number(process.hrtime.bigint()) / 1e6; // ms, monotonic
}

async function main() {
  const client = new Archil({ apiKey: API_KEY, region: REGION as any });

  console.log(`[spike] creating sandbox in ${REGION}...`);
  const t0 = now();

  // A stateful "service" that needs NOTHING but bash/cat/sleep (no python3,
  // no curl, no network) — this console-created sandbox's base image has
  // neither, so we can't assume anything fancier is installed.
  //
  // It's a background bash loop holding a counter in memory, incrementing
  // once a second, writing it to a file each tick. The real proof we want
  // isn't "a file has a value" (disk checkpoints preserve that trivially
  // even from a stopped VM) — it's that the SAME RUNNING PROCESS keeps
  // executing and incrementing inside the fork, which only a live-process
  // memory fork can do. So after forking we read the counter twice, a
  // couple seconds apart, and check it's still climbing.
  const marker = `PID-marker-${Date.now()}`;
  // setsid + disown + </dev/null fully detaches this from the invoking
  // .run() call's session/process-group, which is the standard way to make
  // a background process survive its parent exiting. Plain `nohup ... &`
  // was NOT enough last run — the loop died the instant the boot .run()
  // call returned (counter froze at exactly the value reached during that
  // call's own `sleep 2`), everywhere: pre-fork, in the clone, AND in the
  // original. That's a strong, repeatable signal, not noise.
  const bootScript = `
setsid nohup bash -c '
counter=0
marker="${marker}"
while true; do
  counter=$((counter+1))
  echo "$counter $marker" > /tmp/live_counter.txt
  sleep 1
done
' < /dev/null > /root/stateful.log 2>&1 &
disown
echo "bash PID: $!"
sleep 2
echo "booted"
`;

  let sandbox;
  if (EXISTING_SANDBOX_ID) {
    console.log(`[spike] attaching to existing sandbox ${EXISTING_SANDBOX_ID}...`);
    sandbox = await client.sandboxes.get(EXISTING_SANDBOX_ID);
    console.log(`[spike] attached in ${(now() - t0).toFixed(1)}ms`);
  } else {
    sandbox = await client.sandboxes.create({
      name: `fork-spike-${Date.now()}`,
      baseImage: "python:3.13",
    });
    console.log(`[spike] sandbox created in ${(now() - t0).toFixed(1)}ms: ${sandbox.id ?? sandbox}`);
  }

  const boot = await sandbox.run(bootScript);
  const bootResult = await boot.wait();
  console.log("[spike] boot stdout:", bootResult.stdout);
  console.log("[spike] boot stderr:", bootResult.stderr);

  // Sanity: is the loop actually running and climbing before we touch fork?
  const sanity = await sandbox.run(`echo "--- read 1 ---"; cat /tmp/live_counter.txt; sleep 2; echo "--- read 2 (should be higher) ---"; cat /tmp/live_counter.txt; echo "--- process ---"; ps aux | grep stateful || ps aux | grep bash`);
  const sanityResult = await sanity.wait();
  console.log("[spike] SANITY CHECK (counter climbing before fork?):\\n", sanityResult.stdout, sanityResult.stderr);

  console.log("[spike] forking the RUNNING sandbox now...");
  const tFork0 = now();
  const fork = await sandbox.fork({ name: `fork-spike-clone-${Date.now()}` });
  const tFork1 = now();
  const forkMs = tFork1 - tFork0;
  console.log(`[spike] FORK PAUSE: ${forkMs.toFixed(1)}ms  <-- write this number down`);

  // THE KEY TEST: read the clone's counter twice, a couple seconds apart.
  // If the clone's loop is a live, still-executing process (true memory
  // fork), the second read is HIGHER than the first, and the marker string
  // matches what we set before forking (proving it's the SAME process, not
  // a fresh one that happened to start a new counter from 0).
  const cloneCheckScript = `echo "--- clone read 1 ---"; cat /tmp/live_counter.txt; sleep 2; echo "--- clone read 2 (should be higher, same marker) ---"; cat /tmp/live_counter.txt`;
  const check = await fork.run(cloneCheckScript);
  const checkResult = await check.wait();
  console.log("[spike] CLONE counter (should climb + keep the same marker):\\n", checkResult.stdout, checkResult.stderr);

  // Confirm the ORIGINAL sandbox is still independently running too.
  const origAfter = await sandbox.run(`cat /tmp/live_counter.txt`);
  const origAfterResult = await origAfter.wait();
  console.log("[spike] original's counter (should also be climbing, independently):", origAfterResult.stdout, origAfterResult.stderr);

  const cloneHasMarker = checkResult.stdout.includes(marker);
  const cloneClimbed = (() => {
    const nums = [...checkResult.stdout.matchAll(/(\d+)\s+PID-marker/g)].map((m) => parseInt(m[1], 10));
    return nums.length >= 2 && nums[1] > nums[0];
  })();

  console.log(`
=== RESULT ===
fork_pause_ms = ${forkMs.toFixed(1)}
clone's counter carried the marker forward: ${cloneHasMarker ? "YES" : "NO / CHECK OUTPUT ABOVE"}
clone's counter kept climbing after the fork (live process, not frozen): ${cloneClimbed ? "YES" : "NO / CHECK OUTPUT ABOVE"}

Decision:
  - If both are YES and fork_pause_ms is a few seconds or less: GO. Build Rehearsal.
  - If either is NO or fork failed: STOP, message the team, we pivot off memory-fork.
`);

  // Cleanup (best effort). Only delete things THIS run created:
  // - always delete the fork (it's throwaway, made fresh each run)
  // - only delete the base sandbox if we created it ourselves this run;
  //   never delete a pre-existing sandbox passed in via ARCHIL_SANDBOX_ID,
  //   or every re-run destroys the one you're trying to reuse.
  try {
    await fork.stop?.();
    await fork.delete?.();
    if (!EXISTING_SANDBOX_ID) {
      await sandbox.stop?.();
      await sandbox.delete?.();
    } else {
      console.log("[spike] leaving pre-existing sandbox alone (not deleting it).");
    }
  } catch (e) {
    console.warn("[spike] cleanup warning (non-fatal):", e);
  }
}

main().catch((e) => {
  console.error("[spike] FAILED:", e);
  console.error(`
If this is a method-not-found error, the SDK shape differs from what's
assumed here. Open https://docs.archil.com/sdks/typescript and
https://docs.archil.com/compute/sandboxes/introduction side by side with
this file and fix the client.sandboxes.create / sandbox.run / sandbox.fork
calls to match. Don't rewrite the test's logic — just the API surface.
`);
  process.exit(1);
});
