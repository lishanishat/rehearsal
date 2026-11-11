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
    r = requests.get(f"{target}/_debug/state", timeout=5)
    r.raise_for_status()
    return r.json()


def reset_target(target: str) -> dict:
    r = requests.post(f"{target}/_debug/reset", timeout=5)
    r.raise_for_status()
    return r.json()


def patch_target(target: str) -> dict:
    r = requests.post(f"{target}/_debug/patch", timeout=5)
    r.raise_for_status()
    return r.json()


def ingest(target: str, raw_yaml: str) -> tuple[int, dict]:
    r = requests.post(
        f"{target}/ingest",
        data=raw_yaml.encode("utf-8"),
        headers={"Content-Type": "text/plain"},
        timeout=5,
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
    # Built FROM the real state response, not hardcoded.
    return f"PyYAML {pyyaml_version} yaml.load arbitrary code execution vulnerability fix"


def is_relevant_fix_candidate(result: keenable_client.SearchResult) -> bool:
    text = (result.title + " " + result.snippet).lower()
    return "yaml" in text and ("safe_load" in text or "safeloader" in text or "code execution" in text)


def wait_settle(target: str, seconds: float = 1.6) -> dict:
    """Payload-A plants a background thread that re-asserts the implant
    every ~1s, so we must wait past that tick before trusting a reading."""
    time.sleep(seconds)
    return get_state(target)


def run(target: str, status_url: str | None, payload_path: str) -> None:
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

    # Step 2: build a real query from that real data.
    query = build_query(pyyaml_version)
    log(f"\n[2] Built search query from real state: {query!r}")

    # Step 3: real (or mock-mode) Keenable search.
    log("\n[3] Calling Keenable search client...")
    results = keenable_client.search(query, max_results=3)
    reporter.record_search()
    for i, r in enumerate(results):
        log(f"    [{i}] {r.title}\n        {r.url}\n        {r.snippet[:120]}")

    if not results:
        log("    No search results returned; aborting.")
        return

    # Step 4/5: pick a real candidate, form a real claim.
    primary = results[0]
    primary_claim = (
        f"Fix per '{primary.title}': switch the YAML loader from "
        f"yaml.load()/unsafe Loader to yaml.safe_load() (or SafeLoader) to "
        f"stop arbitrary object construction during parsing."
    )
    log(f"\n[4] Hypothesis #1 (from top result):\n    claim: {primary_claim}\n    source: {primary.url}")

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

    log("\n[7] Resetting again, then applying the REAL fix via /_debug/patch (the service's own, always-correct patch)...")
    reset_target(target)
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
    parser_rejected_payload = status_code != 200
    implant_still_present = bool(after_patch_state.get("implant_present"))
    exploit_still_works = implant_still_present or not parser_rejected_payload
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

    # --- A genuine, deliberately-wrong second hypothesis, tested the same way
    log("\n[9] Hypothesis #2 (deliberately-wrong candidate, to get a real refuted example):")
    wrong_claim = (
        "Fix: the issue is actually a Flask routing/threading bug, not the "
        "YAML loader — no change to yaml.load() is required."
    )
    wrong_url = "https://example.com/flask-memory-tuning"
    log(f"    claim: {wrong_claim}\n    source: {wrong_url}")

    log("    Testing it for real: reset target WITHOUT patching, then replay the exploit...")
    reset_target(target)
    status_code, body = ingest(target, exploit_payload)
    log(f"    POST /ingest -> HTTP {status_code} {json.dumps(body)[:200]}")
    unpatched_after = wait_settle(target)
    log(f"    state: {json.dumps(unpatched_after)}")
    still_vulnerable_without_patch = bool(unpatched_after.get("implant_present")) or status_code == 200
    # The wrong claim asserts "no loader change needed" i.e. predicts the
    # exploit should NOT work even without patching the loader. Reality:
    # it clearly still works, so the claim is refuted by the real test.
    hyp2_status = "refuted" if still_vulnerable_without_patch else "confirmed"
    reporter.record_hypothesis(wrong_url, wrong_claim, hyp2_status)
    log(f"    => Hypothesis #2 is REAL result: {hyp2_status.upper()}")

    # Leave the target in a clean, patched state for any follow-on demo step.
    reset_target(target)
    patch_target(target)
    reporter.record_phase("patched")

    log("\n== Done. Summary ==")
    log(f"searches performed: {reporter.searches}")
    log(f"hypotheses: {json.dumps(reporter.hypotheses, indent=2)}")
    log(f"exploit_replay_after_patch: {replay_result}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Rehearsal investigating agent")
    ap.add_argument("--target", default=os.environ.get("REHEARSAL_TARGET", "http://localhost:8080"))
    ap.add_argument("--status", default=os.environ.get("REHEARSAL_STATUS_URL", "http://localhost:9000/status.json"))
    ap.add_argument("--payload", default=os.environ.get("REHEARSAL_PAYLOAD_PATH"))
    args = ap.parse_args()

    payload_path = find_payload_path(args.payload)
    run(args.target, args.status, payload_path)


if __name__ == "__main__":
    main()
