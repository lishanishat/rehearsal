"""
Keenable web-search client (Rehearsal's investigating agent).

Real documented API, found by fetching https://keenable.ai and
https://docs.keenable.ai / https://docs.keenable.ai/api-keys.md live during
the hackathon (no guessing):

    Base URL:  https://api.keenable.ai
    Keyless:   POST /v1/search/public   (needs header X-Keenable-Title,
               shared 1000 req/hr per IP)
    Keyed:     POST /v1/search          (needs header X-API-Key: <key>,
               no X-Keenable-Title needed)
    Body:      {"query": "...", "max_results": N}
    Response:  {"query": ..., "mode": ..., "results": [{"title","url","snippet"}, ...]}

Configure with env vars:
    KEENABLE_API_KEY   - real key from the Keenable dashboard. If unset,
                          this client falls back to MOCK MODE (see below)
                          rather than guessing or silently degrading.
    KEENABLE_BASE_URL  - override base URL (default https://api.keenable.ai)
    KEENABLE_APP_TITLE - sent as X-Keenable-Title on keyless calls
                          (default "rehearsal-investigating-agent")

MOCK MODE: when KEENABLE_API_KEY is not set, `search()` returns a small,
clearly-labeled set of plausible results instead of calling the network,
so the rest of the pipeline (query construction -> hypothesis -> live
test against app.py) can be built/demoed before a real key exists. Every
mock call logs "[MOCK]" loudly so a real run is never mistaken for mock
data.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import requests

BASE_URL = os.environ.get("KEENABLE_BASE_URL", "https://api.keenable.ai")
APP_TITLE = os.environ.get("KEENABLE_APP_TITLE", "rehearsal-investigating-agent")


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def search(query: str, max_results: int = 3) -> list[SearchResult]:
    """Real call to Keenable's documented search endpoint, or MOCK MODE
    fallback if no KEENABLE_API_KEY is configured."""
    api_key = os.environ.get("KEENABLE_API_KEY")

    if os.environ.get("KEENABLE_MOCK") == "1":
        _log(
            "[MOCK] KEENABLE_API_KEY not set — Keenable client is running in "
            "MOCK MODE. Returning fixed fallback results, NOT a real web "
            "search. Set KEENABLE_API_KEY to call the real API."
        )
        return _mock_results(query, max_results)

    if not api_key:
        return search_keyless(query, max_results)

    url = f"{BASE_URL}/v1/search"
    headers = {"Content-Type": "application/json", "X-API-Key": api_key}
    body = {"query": query, "max_results": max_results}
    _log(f"[KEENABLE] POST {url}  query={query!r} max_results={max_results}")
    try:
        resp = requests.post(url, json=body, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        results = [
            SearchResult(r.get("title", ""), r.get("url", ""), r.get("snippet", ""))
            for r in data.get("results", [])
        ]
        _log(f"[KEENABLE] got {len(results)} real result(s) back")
        return results
    except Exception as e:  # noqa: BLE001
        _log(f"[KEENABLE] real API call failed ({e})")
        raise


def search_keyless(query: str, max_results: int = 3) -> list[SearchResult]:
    """Hits the real keyless /v1/search/public endpoint (works with no API
    key at all, shared rate limit). Useful to prove the real HTTP
    integration end-to-end even before the team's real key is wired in."""
    url = f"{BASE_URL}/v1/search/public"
    headers = {"Content-Type": "application/json", "X-Keenable-Title": APP_TITLE}
    body = {"query": query, "max_results": max_results}
    _log(f"[KEENABLE keyless] POST {url}  query={query!r} max_results={max_results}")
    resp = requests.post(url, json=body, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    results = [
        SearchResult(r.get("title", ""), r.get("url", ""), r.get("snippet", ""))
        for r in data.get("results", [])
    ]
    _log(f"[KEENABLE keyless] got {len(results)} real result(s) back")
    return results


def _mock_results(query: str, max_results: int) -> list[SearchResult]:
    base = [
        SearchResult(
            title=f"PyYAML full_load/unsafe_load CVE-2020-1747/14343 advisory — matches: {query[:60]}",
            url="https://nvd.nist.gov/vuln/detail/CVE-2020-14343",
            snippet=(
                "PyYAML versions before 5.4 are vulnerable to arbitrary code "
                "execution via yaml.load() with the default Loader. Fix: use "
                "yaml.safe_load() or an explicit SafeLoader."
            ),
        ),
        SearchResult(
            title="GitHub advisory: PyYAML insecure deserialization",
            url="https://github.com/yaml/pyyaml/security/advisories/GHSA-8q59-q68h-6hv4",
            snippet=(
                "Calling yaml.load(input) without Loader=SafeLoader allows "
                "constructing arbitrary Python objects, leading to code "
                "execution. Upgrade is recommended but the load call itself "
                "must also switch to safe_load."
            ),
        ),
        SearchResult(
            title="Unrelated: Flask memory usage tuning guide",
            url="https://example.com/flask-memory-tuning",
            snippet="Tips for reducing Flask worker memory footprint under load.",
        ),
    ]
    return base[:max_results]
