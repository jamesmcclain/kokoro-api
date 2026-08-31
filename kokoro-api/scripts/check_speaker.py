#!/usr/bin/env python3
"""Check GET /speaker once. Instant, does not change server state.

Prints exactly one terse line, e.g.:
    FREE queue_entries=0 queue_seconds=0.0
    BUSY queue_entries=3 estimated_seconds_until_free=41.7

`queue_entries` counts utterances still waiting behind whatever is playing,
so BUSY with queue_entries=0 means one utterance and nothing after it. The
seconds figure covers the whole pipeline: current audio plus every queued
entry. It never reports whether any particular queued entry was played --
nothing does.

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
    entries = body.get("queue_entries", 0)
    seconds = body.get("estimated_seconds_until_free")
    if body.get("busy"):
        print(f"BUSY queue_entries={entries} estimated_seconds_until_free={seconds}")
        return 1
    print(f"FREE queue_entries={entries} queue_seconds={seconds}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
