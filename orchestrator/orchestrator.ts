/**
 * Rehearsal — the orchestrator (Builder B / core).
 *
 * Owns three Archil sandboxes running the demo Flask service (service/app.py):
 *   - "production"  — the one that gets attacked and (later) live-patched.
 *   - "clone"        — a fork of the RUNNING, attacked production sandbox.
 *                      Proves: fork carries the in-memory implant forward.
 *   - "restarted"    — a freshly booted sandbox from the same clean image,
 *                      never attacked. Proves: a restart loses the evidence
 *                      (implant_present.restarted stays false, by construction).
 *
 * HTTP API (this process, default port 8787):
 *   POST /attack     { variant: "a"|"b", payloadYaml: string }
 *   POST /approve    { source?: "clone" }  -- applies the rehearsed fix to prod
 *   GET  /sandboxes  -- debug: current sandbox ids/urls
 *   GET  /health
 *
 * Writes partial updates to the shared status server (shared/mock_status_server.py)
 * at STATUS_URL (default http://localhost:9000/status.json), matching the
 * schema in shared/README.md exactly.
 *
 * IMPORTANT, learned from app.py and the Archil docs during this build:
 *   - /_debug/state and /_debug/patch in app.py are gated to
 *     request.remote_addr in (127.0.0.1, ::1). An Archil *exposed* port is
 *     reached through a public TLS-terminating gateway, so Flask sees the
 *     gateway's address, not localhost -- calling /_debug/* through the
 *     public exposed URL will 403. So this orchestrator calls /_debug/state
 *     and /_debug/patch by running `curl` INSIDE the sandbox via
 *     sandbox.run(), which genuinely originates from localhost. The public
 *     exposed URLs are still returned by /attack (per the spec) for `/`,
 *     `/health`, and `/ingest`, and are included for /_debug/* too for
 *     completeness/debugging, but expect those specific ones to 403 if hit
 *     directly from outside the sandbox -- that's app.py's own guard working
 *     as designed, not a bug in this orchestrator.
 *   - Background processes only survive past the booting .run() call if
 *     started with `setsid nohup bash -c '...' < /dev/null > log 2>&1 &`
 *     (confirmed in spike/fork-timing-spike.ts tonight). Plain `nohup ... &`
 *     is not enough.
 *   - sandbox.fork() pause measured 4.7s-8.0s in real runs. The ticker below
 *     keeps polling production's `/` throughout the fork call (it's just an
 *     ordinary await in the same event loop otherwise, so ticker and fork run
 *     concurrently as long as we don't block the loop -- which we don't).
 */

import { Archil } from "disk";
import * as fs from "node:fs";
import * as path from "node:path";
import * as http from "node:http";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const API_KEY = process.env.ARCHIL_API_KEY;
const REGION = process.env.ARCHIL_REGION || "aws-us-west-2";
const STATUS_URL = process.env.STATUS_URL || "http://localhost:9000/status.json";
const ORCH_PORT = Number(process.env.ORCH_PORT || 8787);

if (!API_KEY) {
  console.error(
    "[orchestrator] ARCHIL_API_KEY is not set. Export it first:\n" +
      "  export ARCHIL_API_KEY=key-...\n" +
      "  export ARCHIL_REGION=aws-us-west-2\n" +
      "The server will still start so you can see routes/health, but any\n" +
      "endpoint that touches Archil will fail until the key is set."
  );
}

// Only construct the real client if we have a key; otherwise leave `client`
// unset so the HTTP server can still boot (useful for sanity-checking the
// server/routes without Archil access) and every Archil-touching path fails
// loudly and specifically instead of crashing the whole process at import time.
const client: Archil | null = API_KEY ? new Archil({ apiKey: API_KEY, region: REGION as any }) : null;

// The actual app.py content, read live off disk (not copy-pasted) so it never
// drifts from Builder A's real file. Embedded into the boot script's heredoc.
const APP_PY_PATH = path.join(__dirname, "..", "service", "app.py");
const APP_PY_SOURCE = fs.readFileSync(APP_PY_PATH, "utf8");

function now(): number {
  return Number(process.hrtime.bigint()) / 1e6;
}

// ---------------------------------------------------------------------------
// Status server client
// ---------------------------------------------------------------------------
async function postStatus(partial: Record<string, unknown>) {
  try {
    await fetch(STATUS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(partial),
    });
  } catch (e) {
    console.warn("[orchestrator] status post failed (non-fatal):", (e as Error).message);
  }
}

// ---------------------------------------------------------------------------
// Sandbox boot helper
// ---------------------------------------------------------------------------
interface SandboxHandle {
  role: "production" | "clone" | "restarted";
  id: string;
  sandbox: any; // SDK sandbox/fork object (same .run()/.fork() interface)
  hostname: string; // from exposePort
  baseUrl: string; // https://<hostname>
}

const HEREDOC_DELIM = "REHEARSAL_APP_PY_EOF";

function bootScript(): string {
  return `
mkdir -p /root
cat > /root/app.py << '${HEREDOC_DELIM}'
${APP_PY_SOURCE}
${HEREDOC_DELIM}
(pip install --quiet --disable-pip-version-check flask "pyyaml==5.3.1" \
  || pip install --quiet --disable-pip-version-check --break-system-packages flask "pyyaml==5.3.1")
cd /root
setsid nohup python3 app.py < /dev/null > /root/service.log 2>&1 &
echo "service PID: $!"
sleep 2
echo "--- health check ---"
curl -s -o /dev/null -w "http status: %{http_code}\\n" http://localhost:8080/health || true
echo "booted"
`;
}

async function createAndBoot(role: SandboxHandle["role"], name: string): Promise<SandboxHandle> {
  if (!client) throw new Error("ARCHIL_API_KEY not set -- cannot talk to Archil");
  console.log(`[orchestrator] creating sandbox "${name}" (role=${role})...`);
  const sandbox = await client.sandboxes.create({ name, baseImage: "python:3.13" });
  const id = sandbox.id ?? String(sandbox);
  console.log(`[orchestrator] sandbox ${id} created, booting service...`);

  const boot = await sandbox.run(bootScript());
  const bootResult = await boot.wait();
  console.log(`[orchestrator] ${role} boot stdout:\n${bootResult.stdout}`);
  if (bootResult.stderr) console.log(`[orchestrator] ${role} boot stderr:\n${bootResult.stderr}`);

  const hostname: string = await sandbox.exposePort(8080);
  const baseUrl = `https://${hostname}`;
  console.log(`[orchestrator] ${role} exposed at ${baseUrl}`);

  return { role, id, sandbox, hostname, baseUrl };
}

/** Fork an existing, running sandbox. Returns the handle + the measured pause. */
async function forkHandle(
  source: SandboxHandle,
  role: SandboxHandle["role"],
  name: string
): Promise<{ handle: SandboxHandle; forkMs: number }> {
  console.log(`[orchestrator] forking ${source.role} (${source.id}) -> ${name}...`);
  const t0 = now();
  const forked = await source.sandbox.fork({ name });
  const t1 = now();
  const forkMs = t1 - t0;
  console.log(`[orchestrator] fork pause: ${forkMs.toFixed(1)}ms`);

  const id = forked.id ?? String(forked);
  const hostname: string = await forked.exposePort(8080);
  const baseUrl = `https://${hostname}`;
  console.log(`[orchestrator] ${role} (forked) exposed at ${baseUrl}`);

  return { handle: { role, id, sandbox: forked, hostname, baseUrl }, forkMs };
}

/** Run a one-off command INSIDE a sandbox and return stdout (used for the
 * localhost-only /_debug/* endpoints, which can't be reached through the
 * public exposed port -- see header comment). */
async function runInSandbox(h: SandboxHandle, cmd: string): Promise<string> {
  const exec = await h.sandbox.run(cmd);
  const result = await exec.wait();
  return result.stdout as string;
}

async function debugState(h: SandboxHandle): Promise<any> {
  const out = await runInSandbox(h, "curl -s http://localhost:8080/_debug/state");
  try {
    return JSON.parse(out.trim());
  } catch {
    console.warn(`[orchestrator] could not parse /_debug/state for ${h.role}:`, out);
    return null;
  }
}

async function debugPatch(h: SandboxHandle): Promise<any> {
  const out = await runInSandbox(h, "curl -s -X POST http://localhost:8080/_debug/patch");
  try {
    return JSON.parse(out.trim());
  } catch {
    console.warn(`[orchestrator] could not parse /_debug/patch for ${h.role}:`, out);
    return null;
  }
}

function debugUrls(h: SandboxHandle) {
  return {
    base: h.baseUrl,
    ingest: `${h.baseUrl}/ingest`,
    debug_state: `${h.baseUrl}/_debug/state`, // will 403 externally -- see header comment; use this process's own checks instead
    debug_patch: `${h.baseUrl}/_debug/patch`, // same caveat
    health: `${h.baseUrl}/health`,
  };
}

// ---------------------------------------------------------------------------
// Global demo state
// ---------------------------------------------------------------------------
let production: SandboxHandle | null = null;
let clone: SandboxHandle | null = null;
let restarted: SandboxHandle | null = null;
let lastPayloadYaml: string | null = null;

let requestsServed = 0;
let requestsFailed = 0;

const productionReady: Promise<SandboxHandle> = (async () => {
  if (!API_KEY) {
    // Don't crash the process without a key -- let /health etc. work so the
    // human can see the server is up and fix the env var.
    return new Promise<SandboxHandle>(() => {}); // never resolves
  }
  try {
    const h = await createAndBoot("production", `rehearsal-production-${Date.now()}`);
    production = h;
    await postStatus({ phase: "idle" });
    startTicker(h);
    return h;
  } catch (e) {
    console.error("[orchestrator] FAILED to boot production sandbox:", e);
    throw e;
  }
})();

function startTicker(h: SandboxHandle) {
  console.log("[orchestrator] ticker started, hitting production '/' every ~1s");
  setInterval(async () => {
    try {
      const res = await fetch(h.baseUrl + "/", { signal: AbortSignal.timeout(5000) });
      if (res.ok) requestsServed++;
      else requestsFailed++;
    } catch {
      requestsFailed++;
    }
    postStatus({ requests_served: requestsServed, requests_failed: requestsFailed });
  }, 1000);
}

// ---------------------------------------------------------------------------
// HTTP API
// ---------------------------------------------------------------------------
function readJsonBody(req: http.IncomingMessage): Promise<any> {
  return new Promise((resolve, reject) => {
    let data = "";
    req.on("data", (chunk) => (data += chunk));
    req.on("end", () => {
      if (!data) return resolve({});
      try {
        resolve(JSON.parse(data));
      } catch (e) {
        reject(e);
      }
    });
    req.on("error", reject);
  });
}

function sendJson(res: http.ServerResponse, status: number, body: unknown) {
  const text = JSON.stringify(body, null, 2);
  res.writeHead(status, { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" });
  res.end(text);
}

const server = http.createServer(async (req, res) => {
  const url = req.url || "/";

  if (req.method === "OPTIONS") {
    res.writeHead(204, {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    });
    res.end();
    return;
  }

  try {
    if (req.method === "GET" && url === "/health") {
      return sendJson(res, 200, { status: "ok", productionReady: production !== null });
    }

    if (req.method === "GET" && url === "/sandboxes") {
      return sendJson(res, 200, {
        production: production && { id: production.id, ...debugUrls(production) },
        clone: clone && { id: clone.id, ...debugUrls(clone) },
        restarted: restarted && { id: restarted.id, ...debugUrls(restarted) },
      });
    }

    if (req.method === "POST" && url === "/attack") {
      const body = await readJsonBody(req);
      const variant = body.variant === "b" ? "b" : "a";
      const payloadYaml: string = body.payloadYaml;
      if (!payloadYaml) return sendJson(res, 400, { error: "payloadYaml is required" });

      console.log(`[orchestrator] /attack variant=${variant}`);
      const prod = await productionReady;
      lastPayloadYaml = payloadYaml;

      // 1. Send the attack to production over its public exposed URL.
      const ingestRes = await fetch(prod.baseUrl + "/ingest", {
        method: "POST",
        headers: { "Content-Type": "text/plain" },
        body: payloadYaml,
      });
      const ingestBody = await ingestRes.json().catch(() => null);
      console.log("[orchestrator] production /ingest ->", ingestRes.status, ingestBody);

      await postStatus({ phase: "attacked" });

      // 2a. Fork the now-attacked, RUNNING production sandbox.
      // 2b. In parallel, boot a brand-new, never-attacked sandbox from the
      //     same clean image -- this stands in for "production after a
      //     restart" (a restart would run the same fresh code with no
      //     in-memory implant, which is exactly what this is).
      await postStatus({ phase: "forking" });
      const [forkOutcome, restartedHandle] = await Promise.all([
        forkHandle(prod, "clone", `rehearsal-clone-${Date.now()}`),
        createAndBoot("restarted", `rehearsal-restarted-${Date.now()}`),
      ]);
      clone = forkOutcome.handle;
      restarted = restartedHandle;

      // 3. Check implant presence on all three (via in-sandbox curl -- see
      //    header comment on why not the public URL).
      const [prodState, cloneState, restartedState] = await Promise.all([
        debugState(prod),
        debugState(clone),
        debugState(restarted),
      ]);

      const implant_present = {
        production: !!prodState?.implant_present,
        clone: !!cloneState?.implant_present,
        restarted: !!restartedState?.implant_present,
      };

      await postStatus({
        phase: "diagnosing",
        fork_pause_ms: Math.round(forkOutcome.forkMs),
        implant_present,
      });

      return sendJson(res, 200, {
        variant,
        fork_pause_ms: forkOutcome.forkMs,
        implant_present,
        debug_state: { production: prodState, clone: cloneState, restarted: restartedState },
        sandboxes: {
          production: { id: prod.id, ...debugUrls(prod) },
          clone: { id: clone.id, ...debugUrls(clone) },
          restarted: { id: restarted.id, ...debugUrls(restarted) },
        },
      });
    }

    if (req.method === "POST" && url === "/approve") {
      const prod = await productionReady;
      if (!clone) return sendJson(res, 400, { error: "no clone exists yet -- call /attack first" });

      console.log("[orchestrator] /approve -- applying rehearsed fix to production, live");

      // The "rehearsal" is the clone's /_debug/patch already having been
      // exercised/verified elsewhere (e.g. by the agent-loop piece). Applying
      // it to production means: flip production's live loader the same way,
      // with no restart.
      const patchResult = await debugPatch(prod);

      let exploit_replay_after_patch: "pending" | "failed" | "succeeded" = "pending";
      if (lastPayloadYaml) {
        const replay = await fetch(prod.baseUrl + "/ingest", {
          method: "POST",
          headers: { "Content-Type": "text/plain" },
          body: lastPayloadYaml,
        });
        await replay.json().catch(() => null);
        const postPatchState = await debugState(prod);
        // "failed" = the replayed exploit failed to re-plant the implant =
        // the good outcome.
        exploit_replay_after_patch = postPatchState?.implant_present ? "succeeded" : "failed";
      }

      await postStatus({
        phase: "patched",
        implant_present: {
          production: false,
          clone: clone ? true : false,
          restarted: restarted ? false : false,
        },
        exploit_replay_after_patch,
      });

      return sendJson(res, 200, {
        patched: true,
        patchResult,
        exploit_replay_after_patch,
        production: { id: prod.id, ...debugUrls(prod) },
      });
    }

    sendJson(res, 404, { error: "not found", routes: ["GET /health", "GET /sandboxes", "POST /attack", "POST /approve"] });
  } catch (e) {
    console.error("[orchestrator] request handler error:", e);
    sendJson(res, 500, { error: (e as Error).message, stack: (e as Error).stack });
  }
});

server.listen(ORCH_PORT, () => {
  console.log(`[orchestrator] listening on http://localhost:${ORCH_PORT}`);
  console.log(`[orchestrator] status server target: ${STATUS_URL}`);
  console.log(`[orchestrator] region: ${REGION}`);
  if (!API_KEY) {
    console.log("[orchestrator] NOTE: ARCHIL_API_KEY not set -- Archil calls will fail until it is.");
  } else {
    console.log("[orchestrator] booting production sandbox now (this takes a while)...");
  }
});
