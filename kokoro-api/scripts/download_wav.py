#!/usr/bin/env python3
"""Discipline 3 only: synchronous WAV download via POST /speak, play=false.

Never touches the speaker, never returns 409, produces no sound. Blocks
until synthesis completes -- synthesis time grows with text length.

Usage:
    download_wav.py "Hello as a file." --out output.wav [--voice af_heart] [--speed 1.0]
    download_wav.py --file /tmp/long.txt --out output.wav
    download_wav.py "Hello." --out output.wav --host 192.168.1.33

The server host defaults to $KOKORO_HOST (or 10.0.2.2 if that is unset) and
the port defaults to $KOKORO_PORT (or 5001). Override either per-call with
--host/--port, or export the environment variables once for a whole session.

Prints one terse line. Exit codes:
    0 -> wrote a WAV file
    2 -> 400, bad request (no file written)
    3 -> network/other error, or server returned a non-WAV body
"""
import argparse
import os
import sys

import requests

DEFAULT_HOST = os.environ.get("KOKORO_HOST", "10.0.2.2")
DEFAULT_PORT = os.environ.get("KOKORO_PORT", "5001")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("text", nargs="?", help="Text to synthesize")
    ap.add_argument("--file", help="Read text from this file instead")
    ap.add_argument("--out", required=True, help="Output .wav path")
    ap.add_argument("--voice", default=None)
    ap.add_argument("--speed", type=float, default=None)
    ap.add_argument("--max-time", type=float, default=300)
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

    payload = {"text": text, "play": False}
    if args.voice:
        payload["voice"] = args.voice
    if args.speed is not None:
        payload["speed"] = args.speed

    try:
        r = requests.post(f"{base_url}/speak", json=payload, timeout=args.max_time)
    except requests.RequestException as e:
        print(f"ERROR request failed: {e}")
        return 3

    if r.status_code == 400:
        try:
            err = r.json().get("error", "unknown error")
        except ValueError:
            err = r.text[:200]
        print(f"BAD_REQUEST 400 {err}")
        return 2

    if r.status_code != 200 or not r.content.startswith(b"RIFF"):
        try:
            detail = r.json()
        except ValueError:
            detail = r.text[:200]
        print(f"ERROR {r.status_code} not_a_wav {detail}")
        return 3

    with open(args.out, "wb") as f:
        f.write(r.content)
    print(f"OK wrote {args.out} bytes={len(r.content)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
