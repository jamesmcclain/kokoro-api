#!/usr/bin/env python3
"""Subagent sign-off: select one random voice from the voices that are baked
into the server container. The script does not send a request to the server.

Give each voice of the main agent with --exclude. The script never selects an
excluded voice.

The script prints one short line. For example:
    VOICE bm_george
    NO_VOICE all_baked_voices_excluded
    BAD_REQUEST unknown voice(s): am_onix valid_voices=[...]

Usage:
    random_voice.py
    random_voice.py --exclude af_heart
    random_voice.py --exclude af_heart --exclude am_onyx
    random_voice.py --exclude af_heart,am_onyx

BAKED_VOICES must agree with the voice lists in the Dockerfile. If you change
the Dockerfile, change BAKED_VOICES too.

Exit codes:
    0 -> a voice was selected
    1 -> all baked voices are excluded
    2 -> an excluded name is not a valid voice
"""
import argparse
import random
import sys

# The voices that the Dockerfile bakes into the image (voices_a + voices_b).
BAKED_VOICES = (
    "af_heart", "af_river", "af_alloy", "af_nicole",
    "am_santa", "am_michael", "am_onyx", "am_adam", "am_echo",
    "bf_emma", "bm_george",
)

# All voices that the server accepts (VALID_VOICES in server.py). The script
# uses this list only to find typing errors in --exclude.
VALID_VOICES = frozenset({
    "af_heart", "af_alloy", "af_aoede", "af_bella", "af_jessica", "af_kore",
    "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael",
    "am_onyx", "am_puck", "am_santa",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="VOICE[,VOICE...]",
        help="A voice of the main agent. Repeat the option, or give a comma list.",
    )
    args = ap.parse_args()

    excluded = {
        name.strip().lower()
        for arg in args.exclude
        for name in arg.split(",")
        if name.strip()
    }

    unknown = sorted(excluded - VALID_VOICES)
    if unknown:
        print(
            f"BAD_REQUEST unknown voice(s): {', '.join(unknown)} "
            f"valid_voices={sorted(VALID_VOICES)}"
        )
        return 2

    available = [v for v in BAKED_VOICES if v not in excluded]
    if not available:
        print("NO_VOICE all_baked_voices_excluded")
        return 1

    print(f"VOICE {random.SystemRandom().choice(available)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
