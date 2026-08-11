#!/usr/bin/env python3
"""Check GET /speaker once. Instant, does not change server state.

Prints exactly one terse line, e.g.:
    FREE
    BUSY estimated_seconds_until_free=4.2

Call this at most once per wait -- do not loop it. See the skill's Hard
Rules for why.

Exit codes:
    0 -> free
    1 -> busy
    3 -> network/other error
"""
import sys

import requests

BASE_URL = "http://10.0.2.2:5001"


def main() -> int:
    try:
        r = requests.get(f"{BASE_URL}/speaker", timeout=10)
    except requests.RequestException as e:
        print(f"ERROR request failed: {e}")
        return 3

    body = r.json()
    if body.get("busy"):
        print(f"BUSY estimated_seconds_until_free={body.get('estimated_seconds_until_free')}")
        return 1
    print("FREE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
