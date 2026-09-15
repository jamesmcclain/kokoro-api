---
name: kokoro-api
description: Use this skill whenever the user wants text spoken aloud (e.g. "say this out loud", "read this to me", "speak this text", "give a verbal update"), when an agent wants to narrate a long multi-step task such as writing code, or when the user explicitly asks for a .wav file of synthesized speech. The default is the ordered speech queue. Covers narration and read-throughs through an unguaranteed queue, direct speech when the user asks for it, synchronous WAV download, American and British English voices, and optional audio effects (presets such as audiobook, radio, 8-bit, or a custom chain).
---

# Kokoro Speech

## Overview

This skill sends text to a text-to-speech server. The server plays the speech
on the speaker of its host, or returns a WAV file.

The server has three disciplines. **Discipline 1 (the queue) is the default.**

| Discipline | Use it when | Instructions |
|------------|-------------|--------------|
| **1 — Queue (default)** | Always, unless the user tells you otherwise | This file |
| 2 — Direct speech | The user tells you not to use the queue, or to speak a text directly | `references/direct-speech.md` |
| 3 — WAV download | The user asks for an audio file | `references/wav-download.md` |

**Use Discipline 1.** Do not select Discipline 2 or 3 from your own judgment.
Use them only when the user tells you to. Short text, one sentence, or an
urgent status update is not a reason to leave Discipline 1.

Read a reference file only when you need it:

| Need | File |
|------|------|
| All voice names | `references/voices.md` |
| Audio effects (only when the user asks for an effect) | `references/effects.md` |
| An error or an unexpected output line | `references/troubleshooting.md` |

## Setup

- All scripts are in `scripts/`. All scripts need the `requests` package. If
  it is missing, run `pip install requests`.
- The default server is `10.0.2.2:5001`. If the user names a different
  server, add `--host NAME` (and `--port N`) to every script call.
- You can also export `KOKORO_HOST` and `KOKORO_PORT` one time per session.
- Use the scripts. Do not use `curl`.
- If the text contains quotes or newlines, write it to a file. Then use
  `--file PATH` instead of the text argument.

## Discipline 1 — Queue (Default)

The queue plays texts in the order that it receives them. You send a text and
continue your work. You never wait for the speaker.

### The speaker check: one time per task

> **Run `scripts/check_speaker.py` one time per task, before the first queue call.**
> **After that, do not run `check_speaker.py` again in the same task.**
> **Each queue call prints the queue status. Use that output instead.**

- Do not run `check_speaker.py` before each queue call.
- Do not run `check_speaker.py` after a queue call.
- Do not run `check_speaker.py` to learn if a text was spoken. It cannot tell you.

If `check_speaker.py` prints `ERROR`, read `references/troubleshooting.md`.

### Procedure

1. Run `python3 scripts/check_speaker.py` one time. This is the only speaker
   check for the task.
2. Do your real work. The work has priority over the narration.
3. Write one block of text. A block is one to three sentences.
4. If the last queue call printed `queue_entries` greater than 2, skip this
   block. Go back to step 2.
5. Run the queue script one time with the block:
   ```bash
   python3 scripts/queue_ordered_speech_no_guarantee.py "The tests now pass." --voice am_onyx
   ```
6. Read the output line. Then go back to step 2.

Stop when the task ends, when the text ends, or when the user says stop.

### Output of the queue script

| Output line starts with | Meaning | Your action |
|-------------------------|---------|-------------|
| `QUEUED` | The server accepted the text. | Go back to step 2. |
| `REJECTED queue_full` | The queue is full. The text is lost. | Skip the next several blocks. |
| `BAD_REQUEST 400` | The request is incorrect. | Correct it from the message. Then send it again. |
| `ERROR` | The server is not reachable. | Read `references/troubleshooting.md`. |

Each `QUEUED` and `REJECTED` line gives two numbers:

- `queue_entries` is the number of texts that wait.
- `queue_seconds` is the estimated time to play all of them.

These two numbers are the queue status. You do not need `check_speaker.py`
to get them. The line ends with `no_speaker_check_needed`. This word is a
reminder of the speaker check rule.

Exit codes: `0` queued, `1` queue full, `2` bad request, `3` network error.

### Pace

- If `queue_entries` is more than 2, skip the next block.
- If `queue_seconds` is more than 60, skip the next one or two blocks.
- If `queue_seconds` increases over several calls, send fewer blocks.
- Send one block for each important result. Do not send a block for each file
  or each command.
- If you skip a narration block, do not send it later.
- If you read a document aloud and a block is lost, send that block again later.

### What the queue does not promise

CAUTION: The queue gives no guarantee that a text is spoken.

- `QUEUED` means accepted. It does not mean spoken.
- You cannot learn if a text was spoken. No script can tell you.
- You cannot remove a text from the queue.

Thus, obey these rules:

- Send only text that stays true later. "The build finished" stays true. "I
  now start the tests" becomes false while it waits.
- If the user tells you that a text must be heard, use Discipline 2 for it.

### How to prepare a block

- Write plain words. Remove markdown symbols such as `#`, `*`, `_`,
  backticks, list markers, and `>`.
- Keep the text of a link. Remove the URL.
- Do not read code, tables, or URLs word for word. Summarize them, or skip
  them and say so.
- Write abbreviations in full. For example, "e.g." becomes "for example".

### When the user steers

The user can say "slow down", "change the voice", "narrate less", or "skip
the tables". Apply the instruction from the next block. Continue to apply it.

- Use `--speed 0.1`-`3.0` for speed.
- Use `--voice NAME` for the voice.
- Do not restart the read-through.
- Text that is already in the queue does not change.

## Voices

Pick a voice by name. The server finds the language from the name.

| Voice | Use it for |
|-------|-----------|
| `af_heart` | Default female voice |
| `am_santa` | Default male voice |
| `bf_emma` | British female voice |
| `bm_george` | British male voice |

Use one voice for a whole task, unless the user tells you to change it. For
all voice names, read `references/voices.md`.

## Hard Rules (All Disciplines)

1. Run one script per tool call. Do not put two script runs in one command.
2. Do not use `sleep`. Do not write `while` or `until` loops.
3. Do not run the same status script two times in a row.
4. In Discipline 1, run `check_speaker.py` one time per task only.
5. Send only the documented options: text or `--file`, `--voice`,
   `--speed`, `--effect`, `--effect-file`, `--host`, and `--port`.
6. Do not add an effect unless the user asks for one.

## Quick Reference

| Task | Command |
|------|---------|
| Speaker check (one time per task) | `python3 scripts/check_speaker.py` |
| Queue a block (default) | `python3 scripts/queue_ordered_speech_no_guarantee.py "..." --voice am_onyx` |
| Queue a block from a file | `python3 scripts/queue_ordered_speech_no_guarantee.py --file /tmp/block.txt` |
| Direct speech (only if the user tells you) | See `references/direct-speech.md` |
| WAV file (only if the user asks) | See `references/wav-download.md` |
| Effects (only if the user asks) | See `references/effects.md` |
