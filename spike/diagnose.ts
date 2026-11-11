/**
 * Quick diagnostic: peek inside an already-running sandbox to see why the
 * Flask service isn't answering on port 8080.
 *
 * Run:
 *   export ARCHIL_API_KEY=...
 *   export ARCHIL_REGION=aws-us-west-2
 *   export PROD_SANDBOX_ID=<id from curl localhost:8787/sandboxes>
 *   npx tsx diagnose.ts
 */
import { Archil } from "disk";

const API_KEY = process.env.ARCHIL_API_KEY;
const REGION = process.env.ARCHIL_REGION || "aws-us-west-2";
const SANDBOX_ID = process.env.PROD_SANDBOX_ID;

if (!API_KEY || !SANDBOX_ID) {
  console.error("Set ARCHIL_API_KEY and PROD_SANDBOX_ID first.");
  process.exit(1);
}

async function main() {
  const client = new Archil({ apiKey: API_KEY!, region: REGION as any });
  const sandbox = await client.sandboxes.get(SANDBOX_ID!);

  const checks = `
echo "--- starting it the PROVEN persistent way (setsid + nohup + stdin redirect) ---"
cd /root && setsid nohup python3 app.py < /dev/null > /root/service.log 2>&1 &
echo "launched, PID: $!"
sleep 2
echo "--- is it listening now? (from inside the sandbox) ---"
curl -s localhost:8080/health
echo
echo "--- log so far ---"
cat /root/service.log
`;
  const result = await (await sandbox.run(checks)).wait();
  console.log("=== STDOUT ===\n", result.stdout);
  console.log("=== STDERR ===\n", result.stderr);
}

main().catch((e) => {
  console.error("FAILED:", e);
  process.exit(1);
});
