#!/usr/bin/env python3
"""List effect presets from GET /effects. Instant, changes no server state.

Prints one terse line per preset, e.g.:
    audiobook: HP 85Hz, Comp -22dB 3:1, EQ 2500Hz +2dB, Reverb 10%
    whisper (user): HP 1000Hz, Gain -6dB

With --stages, also prints every stage type and its parameter ranges, one
line per type, for building an inline chain for --effect-file:
    reverb: room_size=0.5[0..1] damping=0.5[0..1] wet=0.33[0..1] ...

Usage:
    list_effects.py
    list_effects.py --stages
    list_effects.py --host 192.168.1.33

The server host defaults to $KOKORO_HOST (or 10.0.2.2 if that is unset) and
the port defaults to $KOKORO_PORT (or 5001). Override either per-call with
--host/--port, or export the environment variables once for a whole session.

Exit codes:
    0 -> listed
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
        "--stages",
        action="store_true",
        help="Also list stage types and parameter ranges for inline chains",
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

    try:
        r = requests.get(f"{base_url}/effects", timeout=10)
        body = r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"ERROR request failed: {e}")
        return 3
    if r.status_code != 200:
        print(f"ERROR {r.status_code} {body}")
        return 3

    for preset in body.get("presets", []):
        tag = " (user)" if preset.get("source") == "user" else ""
        print(f"{preset['name']}{tag}: {', '.join(preset.get('summary', []))}")

    if args.stages:
        print(f"-- stage types (max {body.get('max_stages')} stages per chain) --")
        for stage_type, params in sorted(body.get("stage_types", {}).items()):
            parts = [
                f"{name}={p['default']:g}[{p['min']:g}..{p['max']:g}]"
                for name, p in params.items()
            ]
            print(f"{stage_type}: {' '.join(parts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
