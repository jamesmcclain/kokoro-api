---
name: kokoro-api
description: Use this skill whenever the user wants text spoken aloud (e.g. "say this out loud", "read this to me", "speak this text", "give a verbal update"), when an agent wants to give periodic spoken status updates during a longer multi-step task, or when the user explicitly asks for a .wav file of synthesized speech. Covers one-off spoken announcements, multi-turn read-throughs of longer text with user steering between calls, non-blocking speaker-availability checks, handling a busy speaker, and synchronous WAV download. Supports both American and British English voices.
---

# Kokoro Speak Guide

## Overview

This skill converts text to speech through a REST API at `http://10.0.2.2:5001`.

The host has one speaker. A server-side mutex controls the speaker. Playback is
always asynchronous. Speaking returns immediately, before the audio plays. If you
send a request while the speaker is in use, the server rejects it and does not
queue the request. So two utterances can never overlap, but a rejected utterance
is not spoken. Watch for rejections and retry. Check speaker status to find
out when the speaker will be free.

**Use `scripts/*.py`, not raw `curl`.** They use the `requests` library, take
text as an argument or `--file` (so you never hand-escape quotes or
newlines), and print one terse status line instead of a raw JSON blob --
this keeps tool output small and saves tokens over many-call read-throughs.
All three scripts need `requests` installed (`pip install requests` if it's
missing) and require no arguments beyond what's shown below.

**Three disciplines. Pick exactly one per task.**

| Situation | Discipline |
|-----------|-----------|
| One short thing to say | 1 — One-off speech |
| Reading long text aloud, or narrating a multi-step task | 2 — Multi-turn speech |
| The user explicitly asked for an audio file | 3 — WAV download |

Do not use Discipline 3 to make sound. Do not use Discipline 1 or 2 to make a file.

## Scripts in 30 Seconds

**Speak (asynchronous; plays on the speaker):**
```bash
python3 scripts/speak.py "Deployment is complete." --voice am_michael
```
Prints one of:
| Line | Meaning |
|------|---------|
| `OK 202 spoken estimated_duration_seconds=<n>` | Accepted. Playback runs in the background. |
| `BUSY 409 not_spoken estimated_seconds_until_free=<n>` | The speaker was busy. No audio played. |
| `BAD_REQUEST 400 <error> [valid_voices=...]` | Bad request (unknown voice, bad speed, unsupported field). |

Exit code matches: `0` accepted, `1` busy, `2` bad request, `3` network error.

**Check the speaker (instant; does not change server state):**
```bash
python3 scripts/check_speaker.py
# -> FREE
# -> BUSY estimated_seconds_until_free=<n>
```
Exit code `0` means free, `1` means busy. Trust this over any stale timer
you're holding. `estimated_seconds_until_free` is an estimate -- it can show
`0.0` while still busy, when playback is about to end but hasn't yet.

**Options common to `speak.py` and `download_wav.py`:** positional `TEXT` or
`--file PATH`, plus `--voice NAME` and `--speed 0.1`-`3.0`. Don't pass
anything else -- the server only accepts these fields (and `play`, which the
scripts set for you).

## Choosing a Voice

The server supports American English and British English voices. Pick any voice
by name. You do not need to specify a language or region — the server selects
the correct language engine from the voice name itself.

**Default male voice: `am_santa`.** **Default female voice: `af_heart`.**

Fastest voices (no download delay, because the server has these voice packs
already cached):
```
af_heart, af_river, af_alloy, af_nicole, am_santa, am_michael, am_onyx,
bf_emma, bm_george
```

All valid voices:

```
American Female: af_heart, af_alloy, af_aoede, af_bella, af_jessica, af_kore,
                 af_nicole, af_nova, af_river, af_sarah, af_sky
American Male:   am_adam, am_echo, am_eric, am_fenrir, am_liam, am_michael,
                 am_onyx, am_puck, am_santa

British Female:  bf_alice, bf_emma, bf_isabella, bf_lily
British Male:    bm_daniel, bm_fable, bm_george, bm_lewis
```

If the user asks for a British voice, pick `bf_emma` (female) or `bm_george`
(male) by default, because the server has those two voice packs cached. Use
another British voice only if the user names one, or if `bf_emma`/`bm_george`
do not fit the context. The first request for any other British voice takes
longer, because the server must download its voice pack first.

## Hard Rules (all disciplines)

- Do not run `sleep`. Do not write shell `while` or `until` polling loops.
  Wait between tool calls, not inside one. Run a script, read its one-line
  output, end the call, then decide what to do next. A call that blocks
  removes the window where the user can steer the task.
- Send one utterance per tool call. Do not batch several `speak.py` runs
  into one shell command or loop.
- Do not treat a busy response as spoken text. The server dropped the text.
  You must resend it later, or the user never hears it.
- To wait for the speaker, run `check_speaker.py` one time (or read the
  `estimated_*` number from the last `speak.py` response). Then stop checking
  and do other work. Check again only later, on a separate tool call.
- Do not busy-wait. Busy-wait also means calling `check_speaker.py` more than
  once for the same wait. Call it one time. If it reports `BUSY`, stop
  calling it and do useful work instead: prepare the next block of text,
  continue the surrounding task, or read a steering message from the user.
  Call it again only later, on a separate tool call, after that work is
  done — never twice in a row just to see if the state changed. If truly no
  other work exists, end your turn instead of calling it again in the same
  turn.

## Discipline 1 — One-off Speech

Use this discipline for a single short announcement that the user must hear.

1. Run `python3 scripts/speak.py "<text>" [--voice ...] [--speed ...]`.
2. If it prints `OK 202 ...`, the task is done. Do nothing further —
   playback finishes on its own.
3. If it prints `BUSY 409 ...`, note `estimated_seconds_until_free`. Do not
   check the speaker right away. Do something else useful first, or end your
   turn. Then resend the same text on a later tool call. If you are not sure
   whether the speaker is free, run `check_speaker.py` first and resend only
   when it prints `FREE`. Repeat until `speak.py` prints `OK 202`.

## Discipline 2 — Multi-turn Speech

Use this discipline to read a document aloud block by block, or to narrate
progress during a long task. Run `speak.py` multiple times, one block per
call, and use the speaker check to set the pace.

**Per-block loop:**

1. Prepare the next block. Use one to three sentences, about 30 seconds of
   speech. Remove markdown symbols (headings, `*`, `_`, backticks, list
   markers, `>`, link syntax — keep the link text). Do not read code blocks,
   tables, or URLs word for word. Summarize them or skip them, and say that
   you are doing so. Expand abbreviations ("e.g." becomes "for example").
2. Run `python3 scripts/check_speaker.py`.
   - If it prints `FREE`, go to step 3.
   - If it prints `BUSY`, do not sleep and do not call it again right away.
     Make one check, then stop, and do useful work in separate tool calls:
     prepare later blocks, continue the surrounding task, or read any
     steering message from the user. Call `check_speaker.py` one time again,
     later, after that work is done. If there is genuinely nothing else to
     do, end your turn instead of calling it again in the same turn.
3. Save the block text to a file, then run
   `python3 scripts/speak.py --file <path> [--voice ...] [--speed ...]`.
   Read the one-line result.
   - If it prints `OK 202 ...`, note `estimated_duration_seconds`. Use this
     number as a local timer for how much preparation work fits before the
     next check. Return to step 1 for the next block.
   - If it prints `BUSY 409 ...`, another request claimed the speaker between
     your check and your send, or your check was stale. No audio played. Do
     useful work first, the same as `BUSY` in step 2, then resend this same
     block.
   - If it prints `BAD_REQUEST 400 ...`, fix the request based on the
     printed error, then resend this block.
4. Repeat until the text ends, or the user says stop.

**Steering:** one call per block exists so the user can interrupt between
calls. At any point the user may say "skip the appendix," "slow down,"
"switch voices," or "summarize the tables instead." Apply each instruction
starting with the next block, and keep applying it. Map an instruction to
`--speed`, `--voice`, or to how you select and prepare the remaining text.
Do not stop or restart the read-through unless the user says stop, or asks a
question that you must answer first.

**Status narration variant:** for progress updates during other work, run
`check_speaker.py` once after each meaningful step. If it prints `FREE`,
speak a one-line update with `speak.py`. If it prints `BUSY`, skip this
update. Do not wait for it and do not queue it — continue the task. Fast
steps get most updates skipped. Slow steps get updates. Narration must never
block the actual work.

**Text with quotes or newlines:** write the block to a file and pass
`--file <path>` instead of putting the text on the command line. The scripts
read the file as-is with Python's own file I/O, so there is no shell
escaping to get wrong.

Keep one voice for the whole read-through, unless the user asks you to
change it. Do not add pauses. The busy and free cycle sets the pace.

## Discipline 3 — Synchronous WAV Download

Use this discipline only when the user explicitly asks for an audio file. It
never plays sound, because it never touches the speaker. Do not use it in
place of Discipline 1 or 2 to avoid a busy speaker.

```bash
python3 scripts/download_wav.py "Hello as a file." --voice af_heart --out output.wav
```

- This call blocks until synthesis completes.
- This discipline does not use the speaker. It works even while another
  utterance is playing, it never reports busy, and it produces no sound.
- If synthesis fails, `download_wav.py` prints `BAD_REQUEST` or `ERROR` and
  does not write a file. Read the printed message for the cause.
- For long text, raise the timeout with `--max-time <seconds>` (default 300).

## Troubleshooting

- **`BUSY 409`**: this is normal, not an error. No audio played. Wait by
  running `check_speaker.py` (never `sleep`), then resend the request.
- **`BAD_REQUEST 400` with `valid_voices`**: pick one from that list, or omit
  `--voice` to use the default.
- **Any other `BAD_REQUEST 400`**: the printed error states the exact fault.
  Fix the request, using only the documented options, then resend it.
- **`check_speaker.py` prints `BUSY` with `0.0` seconds**: the estimate fell
  short. Playback is about to end. Check again shortly, and trust the
  `FREE`/`BUSY` word over the number.
- **The first request for a voice that is not on the fast list is slow**:
  this is expected. The server downloads the voice pack once, then caches
  it for later requests.
- **`download_wav.py` seems to hang**: synthesis time increases with text
  length. Split the text into smaller pieces, or raise `--max-time`.
- **`ModuleNotFoundError: requests`**: run `pip install requests` in the
  environment these scripts execute in.

## Quick Reference

| Task | Command |
|------|---------|
| Speak (asynchronous; can report busy) | `python3 scripts/speak.py "..." --voice am_michael` |
| Check whether the speaker is free | `python3 scripts/check_speaker.py` |
| Get a WAV file (synchronous; only on explicit request) | `python3 scripts/download_wav.py "..." --voice af_heart --out out.wav` |
| Speak text from a file (avoids shell escaping) | `python3 scripts/speak.py --file /tmp/block.txt --voice am_onyx` |
