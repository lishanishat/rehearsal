#!/usr/bin/env python3
"""
make_request.py — POST one of the Rehearsal demo payloads to /ingest.

Usage:
    python3 make_request.py <base_url> <variant_letter>

Example:
    python3 make_request.py http://127.0.0.1:8080 a
    python3 make_request.py http://127.0.0.1:8080 b

<base_url> is the service's base URL (no trailing path), e.g.
http://127.0.0.1:8080. <variant_letter> is "a" or "b" (case-insensitive),
selecting payload-a.yaml or payload-b.yaml in this same directory.

This script only POSTs a local file's bytes to the URL you give it — it does
not read any other files, make any other network calls, or do anything
beyond that single request.
"""
import sys
import pathlib
import urllib.request


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    base_url, variant = sys.argv[1], sys.argv[2].strip().lower()
    if variant not in ("a", "b"):
        print(f"error: variant must be 'a' or 'b', got {sys.argv[2]!r}")
        return 2

    payload_path = pathlib.Path(__file__).resolve().parent / f"payload-{variant}.yaml"
    if not payload_path.exists():
        print(f"error: payload file not found: {payload_path}")
        return 2

    raw = payload_path.read_bytes()
    url = base_url.rstrip("/") + "/ingest"

    req = urllib.request.Request(
        url,
        data=raw,
        method="POST",
        headers={"Content-Type": "application/x-yaml"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            print(f"POST {url} -> {resp.status}")
            print(body)
    except urllib.error.HTTPError as e:
        # /ingest may legitimately return 500 even on a "successful" exploit,
        # since the exec() payload's return value isn't a normal YAML
        # document — the side effect (planting the marker) already happened
        # by the time the request fails, so this is expected, not an error.
        body = e.read().decode("utf-8", errors="replace")
        print(f"POST {url} -> {e.code}")
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
