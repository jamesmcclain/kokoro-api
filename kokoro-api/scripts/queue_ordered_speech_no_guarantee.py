#!/usr/bin/env python3
"""Discipline 4 only: append one utterance to the unguaranteed playback queue.

Submits to POST /queue_with_no_guarantee_of_playing and returns at once. The
text is played in arrival order, after whatever is already ahead of it.

There is no guarantee that the text is ever spoken, and no way to find out.
An accepted entry cannot be removed. The only failure you will ever see is
`REJECTED queue_full`, reported here, at submission time. Do not check the
speaker afterwards to infer whether an entry played -- it cannot tell you.

The two numbers describe the queue AFTER the server decided, and exist only
so you can pace your next submission. They say nothing about earlier ones.

Usage:
    queue_ordered_speech_no_guarantee.py "Step three of six is complete."
    queue_ordered_speech_no_guarantee.py --file /tmp/update.txt --voice am_onyx

Exit codes:
    0  -> 202, accepted into the queue (NOT a promise that it plays)
    1  -> 503, queue full, this text is lost
    2  -> 400, bad request
    3  -> network/other error
"""
import argparse
import sys

import requests

BASE_URL = "http://10.0.2.2:5001"
ENDPOINT = "/queue_with_no_guarantee_of_playing"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("text", nargs="?", help="Text to queue")
    ap.add_argument("--file", help="Read text from this file instead")
    ap.add_argument("--voice", default=None)
    ap.add_argument("--speed", type=float, default=None)
    args = ap.parse_args()

    if args.file:
        text = open(args.file, encoding="utf-8").read()
    elif args.text:
        text = args.text
    else:
        ap.error("provide TEXT or --file")

    payload = {"text": text}
    if args.voice:
        payload["voice"] = args.voice
    if args.speed is not None:
        payload["speed"] = args.speed

    try:
        r = requests.post(f"{BASE_URL}{ENDPOINT}", json=payload, timeout=10)
    except requests.RequestException as e:
        print(f"ERROR request failed: {e}")
        return 3

    body = r.json() if r.content else {}
    entries = body.get("queue_entries")
    seconds = body.get("queue_seconds")

    if r.status_code == 202:
        print(f"QUEUED queue_entries={entries} queue_seconds={seconds}")
        return 0
    if r.status_code == 503:
        print(f"REJECTED queue_full queue_entries={entries} queue_seconds={seconds}")
        return 1
    if r.status_code == 400:
        err = body.get("error", "unknown error")
        voices = body.get("valid_voices")
        extra = f" valid_voices={voices}" if voices else ""
        print(f"BAD_REQUEST 400 {err}{extra}")
        return 2

    print(f"ERROR {r.status_code} {body}")
    return 3


if __name__ == "__main__":
    sys.exit(main())
