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

Discipline 1 (the queue, the default): run this ONE time per task, before
the first queue call. Do not run it again in that task. Each queue call
prints the queue status itself.

Discipline 2 (direct speech, only when the user asks for it): run this one
time after a BUSY result, and only after other work. Never run it two times
in a row, and never in a loop.

Usage:
    check_speaker.py
    check_speaker.py --host 192.168.1.33
    check_speaker.py --host 192.168.1.42 --port 5001

The server host defaults to $KOKORO_HOST (or 10.0.2.2 if that is unset) and
the port defaults to $KOKORO_PORT (or 5001). Override either per-call with
--host/--port, or export the environment variables once for a whole session.

Exit codes:
    0 -> free
    1 -> busy
    3 -> network/other error
"""
import argparse
import os
import sys

import requests

DEFAULT_HOST = os.environ.get("KOKORO_HOST", "10.0.2.2")
DEFAULT_PORT = os.environ.get("KOKORO_PORT", "5001")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Server hostname or IP (default: {DEFAULT_HOST}, or $KOKORO_HOST)",
    )
    ap.add_argument(
        "--port",
        default=DEFAULT_PORT,
        help=f"Server port (default: {DEFAULT_PORT}, or $KOKORO_PORT)",
    )
    args = ap.parse_args()
    base_url = f"http://{args.host}:{args.port}"

    try:
        r = requests.get(f"{base_url}/speaker", timeout=10)
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
