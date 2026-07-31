---
name: kokoro-api
description: Use this skill whenever the user wants text spoken aloud (e.g. "say this out loud", "read this to me", "speak this text", "give a verbal update"), when an agent wants to give periodic spoken status updates during a longer multi-step task, or when the user explicitly asks for a .wav file of synthesized speech. Covers one-off spoken announcements, multi-turn read-throughs of longer text with user steering between calls, non-blocking speaker-availability checks via GET /speaker, handling 409 speaker-busy responses, and synchronous WAV download. Supports both American and British English voices.
---

# Kokoro Speak Guide

## Overview

This skill converts text to speech through a REST API at `http://10.0.2.2:5001`.

The host has one speaker. A server-side mutex controls the speaker. Playback is
always asynchronous. `POST /speak` returns `202` before the audio plays. If you
send a request while the speaker is in use, the server returns `409` and does not
queue the request. So two utterances can never overlap, but a rejected utterance
is not spoken. Watch for `409` responses and retry. Use `GET /speaker` to find
out when the speaker will be free.

**Three disciplines. Pick exactly one per task.**

| Situation | Discipline |
|-----------|-----------|
| One short thing to say | 1 — One-off speech |
| Reading long text aloud, or narrating a multi-step task | 2 — Multi-turn speech |
| The user explicitly asked for an audio file | 3 — WAV download |

Do not use Discipline 3 to make sound. Do not use Discipline 1 or 2 to make a file.

## API in 30 Seconds

**Speak (asynchronous; plays on the speaker):**
```bash
curl -s -X POST http://10.0.2.2:5001/speak \
  -H "Content-Type: application/json" \
  -d '{"text": "Deployment is complete.", "voice": "am_michael"}'
```
| Code | Meaning | Body contains |
|------|---------|---------------|
| 202 | The server accepted the request. Playback runs in the background. | `estimated_duration_seconds` — total time until playback ends |
| 409 | The speaker is busy. The server did not synthesize any audio. | `estimated_seconds_until_free` |
| 400 | The request is invalid (unknown voice, bad speed value, or unsupported field). | `error`, and sometimes `valid_voices` |

**Check the speaker (instant; does not change server state):**
```bash
curl -s http://10.0.2.2:5001/speaker
# -> {"busy": true|false, "estimated_seconds_until_free": <number>}
```
Trust the `busy` field. The `estimated_seconds_until_free` number is an estimate. It can
show `0.0` while `busy` is still `true`, when playback is about to end but has not
ended yet.

**Fields:** `text` (required), `voice`, `speed` (0.1 to 3.0),
`play` (default `true`; set it to `false` only for Discipline 3).

`play` is the only mode switch. `true` plays the audio (asynchronous). `false`
downloads a WAV file (synchronous). Send only the fields listed above.

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
  Wait between tool calls, not inside one. Send a quick command, read its
  output, end the call, then decide what to do next. A call tha blocks
  removes the window where the user can steer the task.
- Send one utterance per tool call. Do not batch several `/speak` requests
  into one shell command or loop.
- Do not treat a `409` response as spoken text. The server dropped the text.
  You must resend it later, or the user never hears it.
- To wait for the speaker, check `GET /speaker` one time (or read the
  `estimated_*` number from the last response). Then stop checking and do
  other work. Check again only later, on a separate tool call.
- Do not busy-wait. Busy-wait also means calling `GET /speaker` more than
  once for the same wait. Call it one time. If `busy` is `true`, stop
  calling `/speaker` and do useful work instead: prepare the next block of
  text, continue the surrounding task, or read a steering message from the
  user. Call `/speaker` again only later, on a separate tool call, after
  that work is done — never twice in a row just to see if the state
  changed. If truly no other work exists, end your turn instead of calling
  `/speaker` again in the same turn.

## Discipline 1 — One-off Speech

Use this discipline for a single short announcement that the user must hear.

1. Send `POST /speak` with the text (see the command above).
2. If the server returns `202`, the task is done. Do nothing further —
   playback finishes on its own.
3. If the server returns `409`, read `estimated_seconds_until_free` and note
   it. Do not check `/speaker` right away. Do something else useful first,
   or end your turn. Then resend the same text on a later tool call. If you
   are not sure whether the speaker is free, call `GET /speaker` first and
   resend only when `busy` is `false`. Repeat until the server returns `202`.

## Discipline 2 — Multi-turn Speech

Use this discipline to read a document aloud block by block, or to narrate
progress during a long task. Send multiple `/speak` calls, one block per
call, and use the speaker check to set the pace.

**Per-block loop:**

1. Prepare the next block. Use one to three sentences, about 30 seconds of
   speech. Remove markdown symbols (headings, `*`, `_`, backticks, list
   markers, `>`, link syntax — keep the link text). Do not read code blocks,
   tables, or URLs word for word. Summarize them or skip them, and say that
   you are doing so. Expand abbreviations ("e.g." becomes "for example").
2. Check `curl -s http://10.0.2.2:5001/speaker`.
   - If `busy` is `false`, go to step 3.
   - If `busy` is `true`, do not sleep and do not call `/speaker` again
     right away. Make one check, then stop, and do useful work in separate
     tool calls: prepare later blocks, continue the surrounding task, or
     read any steering message from the user. Call `/speaker` one time
     again, later, after that work is done. If there is genuinely nothing
     else to do, end your turn instead of calling `/speaker` again in the
     same turn.
3. Send the block with `POST /speak`. Read the response code.
   - If the code is `202`, note `estimated_duration_seconds`. Use this
     number as a local timer for how much preparation work fits before the
     next check. Return to step 1 for the next block.
   - If the code is `409`, another request claimed the speaker between your
     check and your send, or your check was stale. No audio played. Do
     useful work first, the same as `busy: true` in step 2, then resend
     this same block.
   - If the code is `400`, fix the request based on the `error` field, then
     resend this block.
4. Repeat until the text ends, or the user says stop.

**Steering:** one call per block exists so the user can interrupt between
calls. At any point the user may say "skip the appendix," "slow down,"
"switch voices," or "summarize the tables instead." Apply each instruction
starting with the next block, and keep applying it. Map an instruction to
the `speed` field, the `voice` field, or to how you select and prepare the
remaining text. Do not stop or restart the read-through unless the user
says stop, or asks a question that you must answer first.

**Status narration variant:** for progress updates during other work, check
`/speaker` once after each meaningful step. If the speaker is free, speak a
one-line update. If the speaker is busy, skip this update. Do not wait for
it and do not queue it — continue the task. Fast steps get most updates
skipped. Slow steps get updates. Narration must never block the actual work.

**Escaping quotes and newlines:** hand-built JSON breaks when the text
contains a quote or a newline. Build the request body with Python instead:
```bash
python3 -c 'import json;print(json.dumps({"text":open("/tmp/block.txt").read(),"voice":"am_onyx"}))' > /tmp/body.json
curl -s -X POST http://10.0.2.2:5001/speak -H "Content-Type: application/json" -d @/tmp/body.json
```
Keep one voice for the whole read-through, unless the user asks you to
change it. Do not add pauses. The busy and free cycle sets the pace.

## Discipline 3 — Synchronous WAV Download

Use this discipline only when the user explicitly asks for an audio file. It
never plays sound, because it never touches the speaker. Do not use it in
place of Discipline 1 or 2 to avoid a busy speaker.

```bash
curl -s -X POST http://10.0.2.2:5001/speak \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello as a file.", "voice": "af_heart", "play": false}' \
  --max-time 300 -o output.wav
```

- This request blocks until synthesis completes. The response body is the WAV
  file.
- This discipline does not use the speaker. It works even while another
  utterance is playing, it never returns `409`, and it produces no sound.
- If synthesis fails, the response body is a small JSON error, not audio. If
  the output file is small or does not start with `RIFF`, read the file to
  find the error.

## Troubleshooting

- **`409 speaker is busy`**: this is normal, not an error. No audio played.
  Wait by checking `/speaker` (never `sleep`), then resend the request.
- **`400 unknown voice`**: the response lists `valid_voices`. Pick one from
  that list, or omit the `voice` field to use the default.
- **Any other `400`**: the `error` field states the exact fault. Fix the
  request, using only the documented fields, then resend it.
- **`/speaker` shows `busy: true` with `0.0` seconds**: the estimate fell
  short. Playback is about to end. Check again shortly, and trust `busy`.
- **The first request for a voice that is not on the fast list is slow**:
  this is expected. The server downloads the voice pack once, then caches
  it for later requests.
- **A WAV download seems to hang**: synthesis time increases with text
  length. Split the text into smaller pieces, or raise `--max-time`.

## Quick Reference

| Task | Command |
|------|---------|
| Speak (asynchronous; can return 409) | `curl -s -X POST http://10.0.2.2:5001/speak -H "Content-Type: application/json" -d '{"text": "...", "voice": "am_michael"}'` |
| Check whether the speaker is free | `curl -s http://10.0.2.2:5001/speaker` |
| Get a WAV file (synchronous; only on explicit request) | add `"play": false`, and save the output with `-o out.wav` |
| Health check | `curl http://10.0.2.2:5001/health` |
