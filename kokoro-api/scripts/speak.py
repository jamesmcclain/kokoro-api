#!/usr/bin/env python3
"""Discipline 2 only: send one text to POST /speak (asynchronous, plays on
the speaker). Use this only when the user tells you to speak directly. The
default is Discipline 1: queue_ordered_speech_no_guarantee.py.

Prints exactly one terse line to stdout -- never a raw JSON dump -- so a
caller reading tool output doesn't burn tokens on formatting.

Usage:
    speak.py "Deployment is complete." [--voice am_michael] [--speed 1.0]
    speak.py --file /tmp/block.txt [--voice bf_emma]
    speak.py "Chapter one." --effect audiobook
    speak.py "Beep boop." --effect-file /tmp/chain.json
    speak.py "Hello." --host 192.168.1.33
    speak.py "Hello." --host 192.168.1.42 --port 5001

The server host defaults to $KOKORO_HOST (or 10.0.2.2 if that is unset) and
the port defaults to $KOKORO_PORT (or 5001). Override either per-call with
--host/--port, or export the environment variables once for a whole session.

Exit codes:
    0  -> 202, accepted (spoke or will speak)
    1  -> 409, speaker busy, nothing was spoken
    2  -> 400, bad request
    3  -> network/other error
"""
import argparse
import json
import os
import sys

import requests

DEFAULT_HOST = os.environ.get("KOKORO_HOST", "10.0.2.2")
DEFAULT_PORT = os.environ.get("KOKORO_PORT", "5001")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("text", nargs="?", help="Text to speak")
    ap.add_argument("--file", help="Read text from this file instead")
    ap.add_argument("--voice", default=None)
    ap.add_argument("--speed", type=float, default=None)
    effect_group = ap.add_mutually_exclusive_group()
    effect_group.add_argument(
        "--effect",
        default=None,
        help="Effect preset name (see list_effects.py), e.g. audiobook",
    )
    effect_group.add_argument(
        "--effect-file",
        default=None,
        help="JSON file holding an inline effect chain (a list of stages)",
    )
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
    if args.effect:
        payload["effect"] = args.effect
    elif args.effect_file:
        try:
            with open(args.effect_file, encoding="utf-8") as f:
                payload["effect"] = json.load(f)
        except (OSError, ValueError) as e:
            print(f"BAD_REQUEST local cannot read --effect-file: {e}")
            return 2

    try:
        r = requests.post(f"{base_url}/speak", json=payload, timeout=10)
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
        effects = body.get("valid_effects")
        extra += f" valid_effects={effects}" if effects else ""
        print(f"BAD_REQUEST 400 {err}{extra}")
        return 2

    print(f"ERROR {r.status_code} {body}")
    return 3


if __name__ == "__main__":
    sys.exit(main())
