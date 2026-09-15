---
name: kokoro-api
description: Use this skill whenever the user wants text spoken aloud (e.g. "say this out loud", "read this to me", "speak this text", "give a verbal update"), when an agent wants to narrate a long multi-step task such as writing code, or when the user explicitly asks for a .wav file of synthesized speech. Covers one-off spoken announcements, ordered multi-turn narration and read-throughs through an unguaranteed queue, non-blocking speaker-availability and queue-length checks, handling a busy speaker, and synchronous WAV download. Supports both American and British English voices, and optional audio effects (presets such as audiobook, radio, 8-bit, or a custom chain).
---

# Kokoro Speak Guide

## Overview

This skill converts text to speech through a REST API. The server defaults to
`10.0.2.2` and port `5001`, but every script accepts `--host` (a hostname or an
IP address) and `--port` to point at a different server, e.g.
`--host 192.168.1.33` or `--host 192.168.1.42 --port 5001`. Set `$KOKORO_HOST`
and/or `$KOKORO_PORT` once to change the default for a whole session instead of
passing the flags on every call. If the user names a specific machine, or if
the default host is unreachable, use `--host` (and `--port` if needed).

The host has one speaker. A server-side mutex controls the speaker. Playback is
always asynchronous. Every call returns immediately, before the audio plays.

The server gives you two ways to send speech. The first way is direct. If you
send a direct request while the speaker is in use, the server rejects it and
does not queue the request. The rejected text is not spoken. You must send it
again later.

The second way is the queue. The queue accepts text and plays it in order,
after the audio that is already in front of it. The queue gives you no
guarantee. You cannot learn whether your text was spoken.

Two utterances never overlap, whichever way you use.

**Use `scripts/*.py`, not raw `curl`.** These scripts use the `requests`
library. They take text as an argument or as `--file`, so you never escape
quotes or newlines by hand. Each script prints one terse status line. This keeps
tool output small and saves tokens over many calls.  All five scripts need
`requests` (`pip install requests` if it is missing).

**Three disciplines. Use exactly one per task.**

**Discipline 2 (the Ordered Queue) is the default for any narration or
read-through.** Reach for Discipline 1 only when you have a specific,
articulable reason the text must go through the direct endpoint instead --
for example, the user is waiting right now for this exact utterance and a
queued, unobservable delivery would defeat the point. "It's shorter" or "it's
just one line" are not such a reason; short narration still belongs in the
queue.

| Situation | Discipline |
|-----------|-----------|
| Narrating a long task, or reading long text aloud (default choice) | 2 — Ordered queue |
| One short thing the user must hear right now, with a stated reason it can't be queued | 1 — One-off speech |
| The user explicitly asked for an audio file | 3 — WAV download |

Do not use Discipline 3 to make sound. Do not use Discipline 1 or 2 to make a
file. Do not use Discipline 2 to avoid a busy speaker. Do not reach for
Discipline 1 out of habit -- if you can't state why the queue won't do, use
the queue.

## Scripts in 30 Seconds

**Speak directly (asynchronous, plays on the speaker):**
```bash
python3 scripts/speak.py "Deployment is complete." --voice am_michael
```
Prints one of:
| Line | Meaning |
|------|---------|
| `OK 202 spoken estimated_duration_seconds=<n>` | Accepted. Playback runs in the background. |
| `BUSY 409 not_spoken estimated_seconds_until_free=<n>` | The speaker was busy. No audio played. |
| `BAD_REQUEST 400 <e> [valid_voices=...]` | Bad request (unknown voice, bad speed, unsupported field). |

Exit code matches: `0` accepted, `1` busy, `2` bad request, `3` network error.

**Queue speech in order, without a guarantee (Discipline 2):**
```bash
python3 scripts/queue_ordered_speech_no_guarantee.py "The tests now pass." --voice am_onyx
```
Prints one of:
| Line | Meaning |
|------|---------|
| `QUEUED queue_entries=<n> queue_seconds=<n>` | The server accepted the text into the queue. |
| `REJECTED queue_full queue_entries=<n> queue_seconds=<n>` | The queue is full. This text is lost. |
| `BAD_REQUEST 400 <e> [valid_voices=...]` | Bad request. |

Exit code matches: `0` queued, `1` queue full, `2` bad request, `3` network
error. The two numbers describe the queue after the server made its decision.
They are the number of entries that wait, and the estimated time for the whole
queue to play. Use them for pace only.

**Read the speaker status (instant, changes no server state):**
```bash
python3 scripts/check_speaker.py
# -> FREE queue_entries=0 queue_seconds=0.0
# -> BUSY queue_entries=3 estimated_seconds_until_free=41.7
```
Exit code `0` means free, `1` means busy. `estimated_seconds_until_free` covers
the audio that plays now and every entry behind it. `queue_entries` counts the
entries that wait behind the current audio. Both numbers are estimates. A busy
speaker can report `0.0` seconds when playback is about to end. Trust the word
`FREE` or `BUSY` more than the number.

**List the effect presets (instant, changes no server state):**
```bash
python3 scripts/list_effects.py
# -> audiobook: HP 85Hz, Comp -22dB 3:1, EQ 2500Hz +2dB, Reverb 10%
# -> radio: HP 400Hz, LP 4000Hz, Comp -24dB 6:1, Drive 8dB, Gain -4dB
```
Add `--stages` to also list each stage type and its parameter ranges. Exit
code `0` means listed, `3` means a network error.

**Options common to `speak.py`, `queue_ordered_speech_no_guarantee.py`, and
`download_wav.py`:** positional `TEXT` or `--file PATH`, plus `--voice NAME`,
`--speed 0.1`-`3.0`, and one of `--effect NAME` or `--effect-file PATH` (see
Voice Effects). Send nothing else in the request payload -- the server
accepts only these fields. All five scripts, including `check_speaker.py` and
`list_effects.py`, also take `--host` and `--port` to target a non-default
server; these are local script options, not sent to the server.

## Choosing a Voice

The server supports American English and British English voices. Pick any voice
by name. You do not need to specify a language or region. The server selects the
correct language engine from the voice name itself.

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
(male). The server has these two voice packs cached. Use another British voice
only when the user names one, or when `bf_emma` and `bm_george` do not fit the
context. The first request for any other British voice takes longer. The server
must download its voice pack first.

## Voice Effects

An effect changes the sound of a voice after synthesis. Examples are a
compressed audiobook sound, a radio sound, or an 8-bit game sound. Effects
work the same way in all three disciplines. An effect does not change how the
speaker, the queue, or the WAV download behave.

**Use an effect only when the user asks for one.** Do not add an effect to
make narration more interesting. The default is no effect.

1. Run `python3 scripts/list_effects.py` one time to get the preset names.
2. Add `--effect NAME` to the script call. Preset names are not case-sensitive.
3. Use the same effect for a whole narration or read-through, unless the user
   asks you to change it. This is the same rule as for the voice.

Built-in presets: `8-bit`, `ai`, `audiobook`, `cathedral`, `chipmunk`, `echo`,
`giant`, `megaphone`, `radio`, `robot`, `telephone`. The server operator can
add presets or replace built-in presets. Thus, `list_effects.py` is the
correct list, not this paragraph.

If no preset fits the request, build a custom chain. A chain is a JSON list of
stages. The server applies the stages in order. Each stage has a `type` and
optional parameters. Run `list_effects.py --stages` to get each type and the
range of each parameter. Write the chain to a file, then pass
`--effect-file PATH`:

```bash
cat > /tmp/chain.json <<'JSON'
[{"type": "pitch", "semitones": -3},
 {"type": "highpass", "cutoff_hz": 200},
 {"type": "reverb", "room_size": 0.6, "wet": 0.25}]
JSON
python3 scripts/queue_ordered_speech_no_guarantee.py "The build finished." --effect-file /tmp/chain.json
```

- Stage types: `highpass`, `lowpass`, `eq`, `low_shelf`, `high_shelf`,
  `compressor`, `gain`, `pitch`, `phaser`, `chorus`, `flanger`, `reverb`,
  `delay`, `bitcrush`, `downsample`, `distortion`.
- Parameters that you omit get their default values.
- A pitch stage changes the pitch only. It does not change the speed. Use
  `--speed` for the speed.
- Reverb and delay add a tail of sound after the last word. The duration
  estimates include this tail.
- If the server rejects the effect, the script prints `BAD_REQUEST 400` with
  the exact fault. For an unknown preset name, the line also contains
  `valid_effects=[...]`.

## Hard Rules (all disciplines)

- Do not run `sleep`. Do not write shell `while` or `until` polling loops.
  Wait between tool calls, not inside one. Run a script, read its one-line
  output, end the call, then decide what to do next. A call that blocks
  removes the window where the user can steer the task.
- Send one utterance per tool call. Do not batch several script runs into one
  shell command or loop.
- Do not treat a `BUSY` or `REJECTED` response as spoken text. The server
  removed the text. Only Discipline 1 lets you send it again.
- Do not busy-wait. Busy-wait means running `check_speaker.py` twice in a row to
  see whether the state changed. Run it one time, then do useful work in a
  separate tool call. Read the status again only later, after that work is done.
- In Discipline 2, do not call `check_speaker.py` before each submission. Call
  it once, at the start of the task, to confirm the server is up and working.
  After that, every call to `queue_ordered_speech_no_guarantee.py` already
  returns `queue_entries` and `queue_seconds` in its own response -- read those
  to pace the next submission instead of polling the speaker separately.
- Use one voice for a whole narration or read-through, unless the user asks you
  to change it.

## Discipline 1 — One-off Speech

Use this discipline only when you can state a concrete reason the queue does
not fit: the user is waiting right now for this specific utterance, and a
queued, unobservable delivery would defeat the point. If you cannot state that
reason, use Discipline 2 instead, even for a single short line.

1. Run `python3 scripts/speak.py "<text>" [--voice ...] [--speed ...]`.
2. If it prints `OK 202 ...`, the task is done. Do nothing more. Playback
   finishes on its own.
3. If it prints `BUSY 409 ...`, record `estimated_seconds_until_free`. Do not
   read the speaker status right away. Do something else useful first, or end
   your turn. Then send the same text again on a later tool call. If you do not
   know whether the speaker is free, run `check_speaker.py` first. Send the text
   again only when that script prints `FREE`. Repeat until `speak.py` prints
   `OK 202`.

CAUTION: A `BUSY` response means the user did not hear that text. Do not put it
into the queue instead. The queue cannot tell you whether it played.

## Discipline 2 — Ordered Queue (default)

Use this discipline for narration in general: a long agentic task while you
work, reading a long document aloud block by block, or any other multi-turn
speech where nothing forces a synchronous, unqueued response. This is the
default discipline -- reach past it only under the terms in Discipline 1.

The queue takes text, holds it in arrival order, and plays each entry after the
audio in front of it. You submit and continue working. You never wait for the
speaker, and you never handle a busy speaker.

Before the first submission of a task, run `check_speaker.py` once to confirm
the server is reachable and working. You do not need to check again during the
task: each call to `queue_ordered_speech_no_guarantee.py` reports its own
`queue_entries` and `queue_seconds` after submission, and that is what you use
to pace the next one (see the per-block loop below).

```bash
python3 scripts/queue_ordered_speech_no_guarantee.py "Step three of six is complete." --voice am_onyx
```

### What the queue does not promise

CAUTION: The queue gives you no guarantee. Read these facts before you use it.

- You cannot learn whether your text was spoken. The server reports nothing
  after it accepts the text. This is intended behavior, not a fault.
- You cannot remove an entry from the queue. An accepted entry either plays
  later, or the server drops it. You never learn which one happened.
- `QUEUED` means accepted only. It does not mean spoken.
- `REJECTED queue_full` is the only failure you ever see. The text is lost.
- The two numbers in the output are estimates. They drift.

### Rules for this discipline

1. Queue only text that stays true at any later time. Queued audio arrives late.
   A sentence such as "The build finished" stays true. A sentence such as "I now
   start the tests" becomes false while it waits.
2. Do not use the queue to avoid a `BUSY` response from Discipline 1.
3. Do not read the speaker status to learn whether an entry played. The status
   gives you pace only. The queue is unobservable on purpose.
4. Use one discipline per task. A direct `speak.py` request fails with `BUSY`
   while your own queue plays, and you cannot clear the queue to make room.
5. Never queue text that the user must hear. Use Discipline 1 for that text.

### Judgment about queue length

The server sets a maximum queue length. The maximum is deliberately not
published to you. Do not treat `REJECTED queue_full` as the point where you must
stop. Stop earlier, from your own judgment.

**Submit only when `queue_entries` is 2 or less.** This is the working limit.

Also use these limits:

- Read `queue_seconds` in every result. If it grows across your calls, the
  speaker falls behind your work. Queue less.
- Keep `queue_seconds` small enough that the last entry still describes recent
  work. About 60 seconds is a reasonable ceiling for most tasks.
- Narrate milestones, not steps. One entry per meaningful result is enough. Do
  not narrate every file you edit or every command you run.
- If the queue is too long, skip the update. Do not save it and do not send it
  later. A skipped update costs nothing.
- Never submit in a loop, and never submit without reading the last result.

### Per-block loop

A block is one unit of speech: a milestone of your task, or a piece of a
document. Use one to three sentences, about 30 seconds of speech.

Before block 1, run `check_speaker.py` once for the whole task (see above).
From then on, pace yourself from each submission's own response -- do not call
`check_speaker.py` again inside this loop.

1. Do the real work, or prepare the next block of the document. The real task is
   the priority. Narration never blocks it.
2. Write the block. Use plain words. Remove markdown symbols (headings, `*`,
   `_`, backticks, list markers, `>`, link syntax). Keep the link text. Do not
   read code blocks, tables, or URLs word for word. Summarize them or skip them,
   and say that you do this. Expand abbreviations ("e.g." becomes "for
   example").
3. If the previous submission's response reported `queue_entries` greater than
   2, do not submit yet. Return to step 1 and do more real work instead. For
   narration, skip this block instead of holding it. Otherwise, go to step 4.
4. Run `queue_ordered_speech_no_guarantee.py` one time with the block, and read
   the `queue_entries`/`queue_seconds` it reports back.
   - `QUEUED` with a small `queue_seconds`: return to step 1.
   - `QUEUED` with a large `queue_seconds`: return to step 1, and skip the next
     one or two blocks.
   - `REJECTED queue_full`: return to step 1 and skip several blocks. This text
     is lost. For a document read-through, send this same block again later.
   - `BAD_REQUEST 400 ...`: correct the request from the printed error, then
     send the block again.
5. Repeat until the task ends, until the text ends, or until the user says stop.

**Text with quotes or newlines:** write the block to a file. Then pass
`--file <path>` instead of the positional text argument. The scripts read the
file with Python file I/O, so no shell escaping can go wrong.

**Steering:** one call per block exists so the user can interrupt between calls.
At any point the user can say "skip the appendix", "slow down", "switch voices",
"narrate less", or "summarize the tables instead". Apply each instruction from
the next block onward, and keep applying it. Map an instruction to `--speed`, to
`--voice`, or to how you select and prepare the remaining text. Do not stop or
restart the read-through. Stop only when the user says stop, or asks a question
that you must answer first.

CAUTION: A steering instruction cannot reach text that is already queued. The
queue plays entries that you wrote before the instruction arrived. Keep the
queue short, and the delay stays small.

Do not add pauses. The queue sets the pace.

## Discipline 3 — Synchronous WAV Download

Use this discipline only when the user explicitly asks for an audio file. It
never plays sound, because it never touches the speaker.

```bash
python3 scripts/download_wav.py "Hello as a file." --voice af_heart --out output.wav
```

- This call blocks until synthesis is complete.
- This discipline does not use the speaker. It works while other audio plays, it
  never reports busy, and it produces no sound.
- If synthesis fails, `download_wav.py` prints `BAD_REQUEST` or `ERROR` and
  writes no file. Read the printed message for the cause.
- For long text, increase the timeout with `--max-time <seconds>` (default 300).

## Troubleshooting

- **`BUSY 409`**: this is normal, not an error. No audio played. Wait by running
  `check_speaker.py` (never `sleep`), then send the request again.
- **`REJECTED queue_full`**: the queue reached its maximum length. The text is
  lost and you cannot recover it. Continue your task and skip the next several
  blocks. You submitted faster than the speaker plays.
- **A queued line never plays**: you cannot detect this, and you must not try.
  Use Discipline 1 for text that the user must hear.
- **`BAD_REQUEST 400` with `valid_voices`**: pick a voice from that list, or
  omit `--voice` to use the default.
- **`BAD_REQUEST 400` with `valid_effects`**: pick a preset from that list, or
  omit `--effect` to use no effect.
- **`BAD_REQUEST local cannot read --effect-file`**: the file is missing or
  does not contain valid JSON. Write the chain again, then send the request
  again. The script did not send this request to the server.
- **Any other `BAD_REQUEST 400`**: the printed error states the exact fault.
  Correct the request with the documented options only, then send it again.
- **`check_speaker.py` prints `BUSY` with `0.0` seconds**: the estimate fell
  short. Playback ends soon. Read the status again later, and trust the word
  `FREE` or `BUSY` more than the number.
- **`queue_entries` stays above 2**: you queue faster than the speaker plays.
  Queue fewer blocks and use shorter sentences.
- **The first request for a voice that is not on the fast list is slow**: this
  is expected. The server downloads the voice pack one time, then caches it.
- **`download_wav.py` seems to hang**: synthesis time grows with text length.
  Divide the text into smaller pieces, or increase `--max-time`.
- **`ModuleNotFoundError: requests`**: run `pip install requests` in the
  environment that runs these scripts.
- **`ERROR request failed: ...` / connection refused or timed out**: the
  default host (`10.0.2.2:5001`, or your `$KOKORO_HOST`/`$KOKORO_PORT`) is not
  reachable. Confirm the hostname or IP with the user, then pass `--host`
  (and `--port` if it's non-standard) on every script call, or export
  `KOKORO_HOST`/`KOKORO_PORT` for the rest of the session.

## Quick Reference

| Task | Command |
|------|---------|
| Speak directly (can report busy) | `python3 scripts/speak.py "..." --voice am_michael` |
| Queue a block (no guarantee) | `python3 scripts/queue_ordered_speech_no_guarantee.py "..." --voice am_onyx` |
| Read the speaker status and queue length | `python3 scripts/check_speaker.py` |
| Get a WAV file (on explicit request only) | `python3 scripts/download_wav.py "..." --voice af_heart --out out.wav` |
| Queue text from a file (avoids shell escaping) | `python3 scripts/queue_ordered_speech_no_guarantee.py --file /tmp/block.txt --voice am_onyx` |
| List effect presets | `python3 scripts/list_effects.py` |
| List stage types for a custom chain | `python3 scripts/list_effects.py --stages` |
| Speak with an effect preset | add `--effect audiobook` to any of the three request scripts |
| Speak with a custom effect chain | add `--effect-file /tmp/chain.json` to any of the three request scripts |
| Target a different server | add `--host <name-or-ip> [--port <n>]` to any of the above, or export `KOKORO_HOST`/`KOKORO_PORT` |
