#!/usr/bin/env python3
"""Send one utterance to POST /speak (asynchronous, plays on the speaker).

Prints exactly one terse line to stdout -- never a raw JSON dump -- so a
caller reading tool output doesn't burn tokens on formatting.

Usage:
    speak.py "Deployment is complete." [--voice am_michael] [--speed 1.0]
    speak.py --file /tmp/block.txt [--voice bf_emma]

Exit codes:
    0  -> 202, accepted (spoke or will speak)
    1  -> 409, speaker busy, nothing was spoken
    2  -> 400, bad request
    3  -> network/other error
"""
import argparse
import sys

import requests

BASE_URL = "http://10.0.2.2:5001"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("text", nargs="?", help="Text to speak")
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
        r = requests.post(f"{BASE_URL}/speak", json=payload, timeout=10)
    except requests.RequestException as e:
        print(f"ERROR request failed: {e}")
        return 3

    body = r.json() if r.content else {}

    if r.status_code == 202:
        dur = body.get("estimated_duration_seconds")
        print(f"OK 202 spoken estimated_duration_seconds={dur}")
        return 0
    if r.status_code == 409:
        wait = body.get("estimated_seconds_until_free")
        print(f"BUSY 409 not_spoken estimated_seconds_until_free={wait}")
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
