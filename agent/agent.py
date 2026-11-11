#!/usr/bin/env python3
"""
Rehearsal — the investigating agent.

Reads REAL state from the target service (service/app.py), builds a REAL
search query from what it found, calls a REAL (or mock-mode) Keenable
search, forms a real hypothesis from a real candidate result, and TESTS
that hypothesis for real by patching the live target and replaying a real
exploit payload against /ingest, checking /_debug/state afterward. Results
(confirmed/refuted) are decided by what actually happens, not scripted.

Usage:
    python3 agent.py --target http://localhost:8080 \
        --status http://localhost:9000/status.json \
        --payload ../service/payloads/payload-a.yaml

If --payload is omitted, it looks for any *.yaml file under
service/payloads/ next to this repo; if none exist yet (the other builder's
piece not landed), it falls back to test_payloads/fallback_payload.yaml,
a payload this agent wrote itself for self-testing, clearly marked as such.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

import requests

import keenable_client

HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg: str) -> None:
    print(msg, flush=True)


class StatusReporter:
    """Thin wrapper posting partial updates to the shared status server.
    Matches the schema in rehearsal/shared/README.md exactly."""

    def __init__(self, status_url: str | None):
        self.status_url = status_url
        self.searches = 0
        self.hypotheses: list[dict] = []

    def _post(self, patch: dict) -> None:
        if not self.status_url:
            return
        try:
            requests.post(self.status_url, json=patch, timeout=5)
        except Exception as e:  # noqa: BLE001
            log(f"[status] WARNING: could not reach shared status server ({e})")

    def record_search(self) -> None:
        self.searches += 1
        self._post({"searches": self.searches})

    def record_hypothesis(self, url: str, claim: str, status: str) -> None:
        self.hypotheses.append({"url": url, "claim": claim, "status": status})
        self._post({"hypotheses": self.hypotheses})

    def record_replay_result(self, result: str) -> None:
        self._post({"exploit_replay_after_patch": result})

    def record_phase(self, phase: str) -> None:
        self._post({"phase": phase})


def get_state(target: str) -> dict:
    r = requests.get(f"{target}/_debug/state", timeout=60)
    r.raise_for_status()
    return r.json()


def reset_target(target: str) -> dict:
    r = requests.post(f"{target}/_debug/reset", timeout=60)
    r.raise_for_status()
    return r.json()


def patch_target(target: str) -> dict:
    r = requests.post(f"{target}/_debug/patch", timeout=60)
    r.raise_for_status()
    return r.json()


def ingest(target: str, raw_yaml: str) -> tuple[int, dict]:
    r = requests.post(
        f"{target}/ingest",
        data=raw_yaml.encode("utf-8"),
        headers={"Content-Type": "text/plain"},
        timeout=60,
    )
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        body = {}
    return r.status_code, body


def find_payload_path(explicit: str | None) -> str:
    if explicit:
        return explicit
    service_payloads = os.path.join(HERE, "..", "service", "payloads")
    candidates = sorted(glob.glob(os.path.join(service_payloads, "*.yaml")))
    if candidates:
        return candidates[0]
    fallback = os.path.join(HERE, "test_payloads", "fallback_payload.yaml")
    log(
        "[payload] No files found under service/payloads/ yet — using this "
        f"agent's own self-test fallback payload at {fallback} (clearly "
        "marked, for pipeline testing only, not the real exploit)."
    )
    return fallback


def build_query(pyyaml_version: str) -> str:
    # Fallback only (used if the LLM is unreachable). Still built from real state.
    return f"PyYAML {pyyaml_version} yaml.load arbitrary code execution vulnerability fix"


# ---------------------------------------------------------------------------
# LLM (free, keyless public endpoint by default; set LLM_URL/LLM_MODEL/LLM_KEY
# to use a paid OpenAI-compatible provider instead).
# ---------------------------------------------------------------------------
LLM_URL = os.environ.get("LLM_URL", "https://text.pollinations.ai/openai")
LLM_MODEL = os.environ.get("LLM_MODEL", "openai")
LLM_KEY = os.environ.get("LLM_KEY")


def llm(prompt: str) -> str:
    import re
    if os.environ.get("LLM_DISABLED") == "1":
        raise RuntimeError("LLM explicitly disabled; using deterministic fallback")
    headers = {"Content-Type": "application/json"}
    if LLM_KEY:
        headers["Authorization"] = f"Bearer {LLM_KEY}"
    last = None
    for _ in range(3):
        r = requests.post(LLM_URL, headers=headers, timeout=20,
                          json={"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}]})
        if r.status_code in (402, 429):  # free tier: 1 reply / ~45s per IP
            m = re.search(r"[Rr]etry after (\d+)", r.text)
            wait = int(m.group(1)) + 2 if m else 47
            log(f"    [llm] rate limited, waiting {wait}s")
            time.sleep(min(wait, 60))
            last = r.text[:200]
            continue
        r.raise_for_status()
        return r.json()["choices"][0]["message"].get("content") or ""
    raise RuntimeError(f"LLM unavailable: {last}")


def parse_json(text: str):
    import re
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except ValueError:
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except ValueError:
                return None
    return None


def diagnose(state: dict) -> dict:
    """The LLM reads the REAL /_debug/state and decides what's wrong + what to search."""
    prompt = (
        "You are a security engineer. Below is the live in-memory debug state of a running web service "
        "that was just compromised through its YAML ingest endpoint. From this state ALONE, identify the "
        "vulnerable library, its installed version, the vulnerability, and any sign of a persistent "
        "in-memory implant. Then write one web search query to find how to fix it. Do NOT invent CVE "
        "identifiers: never put a CVE number in any field unless it appears in the state.\n\nSTATE:\n"
        + json.dumps(state, indent=1)
        + '\n\nReply with strict JSON only: {"library": str, "version": str, "vulnerability": str, '
          '"evidence": str (cite state fields), "query": str}'
    )
    d = parse_json(llm(prompt))
    if not isinstance(d, dict) or not d.get("query"):
        raise RuntimeError("diagnosis parse failed")
    return d


def classify_sources(results) -> list:
    """One LLM call: for each real search result, what does it actually recommend?
    Returns a list aligned to results of {"testable": bool, "claim": str}.
    'testable' = the source recommends replacing the unsafe loader with safe_load/SafeLoader,
    which is exactly what this service's live /_debug/patch does, so we can test it for real."""
    items = "\n".join(f"[{i}] {r.title}\n    {r.snippet[:400]}" for i, r in enumerate(results, 1))
    prompt = (
        "Web search results about fixing a PyYAML yaml.load() code-execution compromise:\n\n" + items +
        "\n\nFor EACH result, state in one sentence the fix it recommends (the claim), and whether that fix is "
        "'switch the loader to yaml.safe_load / SafeLoader' (testable=true) or something else / nothing concrete "
        '(testable=false). Reply with strict JSON only: {"items": [{"claim": str, "testable": bool}, ...] '
        "one per result, in order}"
    )
    d = parse_json(llm(prompt))
    if not isinstance(d, dict) or not isinstance(d.get("items"), list):
        raise RuntimeError("source classification parse failed")
    return d["items"]


def is_relevant_fix_candidate(result: keenable_client.SearchResult) -> bool:
    text = (result.title + " " + result.snippet).lower()
    return "yaml" in text and ("safe_load" in text or "safeloader" in text)


def wait_settle(target: str, seconds: float = 1.6) -> dict:
    """Payload-A plants a background thread that re-asserts the implant
    every ~1s, so we must wait past that tick before trusting a reading."""
    time.sleep(seconds)
    return get_state(target)


def run(target: str, status_url: str | None, payload_path: str) -> bool:
    reporter = StatusReporter(status_url)
    reporter.record_phase("diagnosing")

    log(f"== Rehearsal investigating agent ==")
    log(f"target:  {target}")
    log(f"status:  {status_url or '(none configured)'}")
    log(f"payload: {payload_path}")

    with open(payload_path, "r") as f:
        exploit_payload = f.read()

    # Step 1: get REAL state from the target.
    log("\n[1] GET /_debug/state on the live target...")
    state = get_state(target)
    log(f"    -> {json.dumps(state)}")
    pyyaml_version = state.get("libraries", {}).get("pyyaml", "unknown")
    log(f"    real installed PyYAML version reported by target: {pyyaml_version!r}")

    # Step 2: the LLM diagnoses from the real state and writes the query.
    log("\n[2] LLM diagnosing from the real state...")
    try:
        diag = diagnose(state)
        query = diag["query"]
        log(f"    diagnosis: {diag.get('library')} {diag.get('version')} - {diag.get('vulnerability')}")
        log(f"    evidence:  {diag.get('evidence')}")
    except Exception as e:  # noqa: BLE001
        query = build_query(pyyaml_version)
        log(f"    LLM unavailable ({e}); falling back to a query built from the real version")
    log(f"    search query: {query!r}")

    # Step 3: real (or mock-mode) Keenable search.
    log("\n[3] Calling Keenable search client...")
    results = keenable_client.search(query, max_results=4)
    reporter.record_search()
    for i, r in enumerate(results):
        log(f"    [{i}] {r.title}\n        {r.url}\n        {r.snippet[:120]}")

    if not results:
        log("    No search results returned; aborting.")
        reporter.record_phase("blocked")
        return False

    # Step 4: what does each REAL source actually claim?
    log("\n[4] Reading what each source recommends...")
    try:
        items = classify_sources(results)
    except Exception as e:  # noqa: BLE001
        log(f"    LLM unavailable ({e}); using keyword check instead")
        items = [{"claim": r.snippet[:160], "testable": is_relevant_fix_candidate(r)} for r in results]
    testable = [(r, (items[i] if i < len(items) else {})) for i, r in enumerate(results)]
    testable = [(r, it) for r, it in testable if isinstance(it, dict) and it.get("testable") is True]
    for i, it in enumerate(items):
        log(f"    [{i}] testable={bool(it.get('testable'))}  claim: {it.get('claim', '')[:140]}")
    if not testable:
        log("    No source recommended a fix this service can rehearse; aborting.")
        reporter.record_phase("blocked")
        return False
    primary, primary_item = testable[0]
    primary_claim = primary_item.get("claim") or f"Switch to yaml.safe_load (per '{primary.title}')"
    log(f"\n    Hypothesis #1 (from {primary.url}):\n    claim: {primary_claim}")

    # --- Confirm/refute hypothesis #1 for real -------------------------
    log("\n[5] Establishing a clean baseline on the target (/_debug/reset)...")
    reset_target(target)
    baseline = get_state(target)
    log(f"    baseline: {json.dumps(baseline)}")

    log("\n[6] Confirming the target is actually vulnerable before any fix: replaying exploit payload on the UNPATCHED target...")
    status_code, body = ingest(target, exploit_payload)
    log(f"    POST /ingest -> HTTP {status_code} {json.dumps(body)[:200]}")
    pre_patch_state = wait_settle(target)
    log(f"    state after unpatched replay: {json.dumps(pre_patch_state)}")
    # Two independent real signals of "the unsafe loader executed attacker
    # object construction": (a) the in-memory implant marker got set, or
    # (b) /ingest returned 200 "parsed" at all (unsafe yaml.load() accepted
    # and ran the !!python/object/apply tag; a payload's own internal logic
    # could still fail afterward for unrelated reasons without that
    # disproving the parser-level vulnerability).
    really_vulnerable = bool(pre_patch_state.get("implant_present")) or status_code == 200
    log(f"    really vulnerable pre-patch? {really_vulnerable} (implant_present={pre_patch_state.get('implant_present')}, ingest_status={status_code})")
    if not really_vulnerable:
        reporter.record_phase("blocked")
        log("    Baseline did not reproduce the vulnerability; refusing to confirm a fix.")
        return False

    log("\n[7] Applying the loader fix to the COMPROMISED target, without resetting evidence...")
    patch_target(target)
    patched_state = get_state(target)
    log(f"    state after patch: {json.dumps(patched_state)}")

    log("\n[8] Testing hypothesis #1 for real: replaying the SAME exploit payload against the PATCHED target...")
    status_code, body = ingest(target, exploit_payload)
    log(f"    POST /ingest -> HTTP {status_code} {json.dumps(body)[:200]}")
    after_patch_state = wait_settle(target)
    log(f"    state after patched replay: {json.dumps(after_patch_state)}")

    # After patch: safe_load() rejects the !!python/object/apply tag outright
    # at parse time (HTTP 500 "could not determine a constructor..."), so no
    # object construction/exec ever runs. Real signal = implant marker absent
    # AND the parser itself refused the payload (non-200 or an explicit
    # constructor-tag error), not just "the response happened to be 200".
    parser_rejected_payload = status_code == 500 and "could not determine a constructor" in body.get("error", "")
    benign_code, _ = ingest(target, "rehearsal: healthy\n")
    implant_still_present = bool(after_patch_state.get("implant_present"))
    exploit_still_works = implant_still_present or not parser_rejected_payload or benign_code != 200
    replay_result = "succeeded" if exploit_still_works else "failed"
    log(
        f"    exploit replay after patch: {replay_result} "
        f"(implant_present={implant_still_present}, parser_rejected_payload={parser_rejected_payload}) "
        "(succeeded = still vulnerable, failed = fix held)"
    )
    reporter.record_replay_result(replay_result)

    hyp1_status = "refuted" if exploit_still_works else "confirmed"
    reporter.record_hypothesis(primary.url, primary_claim, hyp1_status)
    log(f"\n    => Hypothesis #1 is REAL result: {hyp1_status.upper()}")

    # --- Every other real source that made a testable claim gets the same
    # real test, so the dashboard shows each source's actual outcome. (The
    # result can differ from #1: e.g. payload-a's in-memory beacon thread
    # survives a loader swap, which REFUTES "just switch to safe_load".)
    for n, (src, it) in enumerate(testable[1:3], start=2):
        claim = it.get("claim") or f"Switch to yaml.safe_load (per '{src.title}')"
        log(f"\n[9.{n}] Hypothesis #{n} (from {src.url}):\n    claim: {claim}")
        code_n, body_n = ingest(target, exploit_payload)
        st_n = wait_settle(target)
        works = bool(st_n.get("implant_present")) or not (
            code_n == 500 and "could not determine a constructor" in body_n.get("error", "")
        ) or benign_code != 200
        status_n = "refuted" if works else "confirmed"
        log(f"    replay -> HTTP {code_n}, implant_present={st_n.get('implant_present')} => {status_n.upper()}")
        reporter.record_hypothesis(src.url, claim, status_n)

    # Preserve the measured outcome; only the orchestrator may mark production patched.
    success = all(h["status"] == "confirmed" for h in reporter.hypotheses)
    reporter.record_phase("rehearsed" if success else "blocked")

    log("\n== Done. Summary ==")
    log(f"searches performed: {reporter.searches}")
    log(f"hypotheses: {json.dumps(reporter.hypotheses, indent=2)}")
    log(f"exploit_replay_after_patch: {replay_result}")
    return success


def main() -> None:
    ap = argparse.ArgumentParser(description="Rehearsal investigating agent")
    ap.add_argument("--target", default=os.environ.get("REHEARSAL_TARGET", "http://localhost:8080"))
    ap.add_argument("--status", default=os.environ.get("REHEARSAL_STATUS_URL", "http://localhost:9000/status.json"))
    ap.add_argument("--payload", default=os.environ.get("REHEARSAL_PAYLOAD_PATH"))
    args = ap.parse_args()

    payload_path = find_payload_path(args.payload)
    try:
        success = run(args.target.rstrip("/"), args.status, payload_path)
    except Exception as exc:
        StatusReporter(args.status).record_phase("blocked")
        log(f"[agent] Failed: {exc}")
        sys.exit(1)
    sys.exit(0 if success else 2)


if __name__ == "__main__":
    main()
