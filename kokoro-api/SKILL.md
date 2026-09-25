---
name: kokoro-api
description: Use this skill when the user wants text spoken aloud (for example, "say this out loud", "read this to me", "speak this text", "give a verbal update"). Also use it when an agent narrates a long task with many steps, such as a code change, or when the user asks for a .wav file of speech. All speech goes through the ordered speech queue. The skill covers narration and read-throughs through a queue with no guarantee of playback, WAV download, American and British English voices, optional audio effects (presets such as audiobook, radio, and 8-bit, or a custom chain), and an optional spoken sign-off from subagents in a random voice.
---

# Kokoro Speech

## Overview

This skill sends text to a text-to-speech server. The server plays the speech
on the speaker of its host. The server can also return a WAV file.

> **If you are a subagent and your instructions tell you to do the kokoro-api
> sign-off, read `references/subagent-sign-off.md`. Obey only that file.**

This skill has two disciplines. **Discipline 1 (the queue) is the default.**

| Discipline | Use it when | Instructions |
|------------|-------------|--------------|
| **1 — Queue (default)** | Always, unless the user asks for an audio file | This file |
| 2 — WAV download | The user asks for an audio file | `references/wav-download.md` |

**Use Discipline 1 for all speech.** It is the only discipline that plays text
on the speaker. Use Discipline 2 only when the user asks for an audio file. Do
not select Discipline 2 from your own judgment. A short text, one sentence, or
an urgent status update is not a reason to leave Discipline 1.

If the user tells you to speak a text "directly", "now", or "without the
queue", use the queue. Tell the user one time that all speech goes through the
queue. Also tell the user that the queue cannot show if a text played.

Read a reference file only when you need it:

| Need | File |
|------|------|
| All voice names | `references/voices.md` |
| Audio effects (only when the user asks for an effect) | `references/effects.md` |
| An error or an unexpected output line | `references/troubleshooting.md` |
| The subagent sign-off (only when your instructions tell you to) | `references/subagent-sign-off.md` |

## Setup

- All scripts are in `scripts/`. All scripts need the `requests` package. If
  `requests` is missing, run `pip install requests`.
- The default server is `10.0.2.2:5001`. If the user names a different server,
  add `--host NAME` to each script call. If the port is not 5001, also add
  `--port N`.
- You can also export `KOKORO_HOST` and `KOKORO_PORT` one time for each session.
- Use the scripts. Do not use `curl`.
- If the text contains quotes or line breaks, write the text to a file. Then
  use `--file PATH` in place of the text argument.

## The Speaker Check: One Time per Session

> **Run `scripts/check_speaker.py` one time per session, before your first queue call.**
> **Do not run it again in the same session. This rule applies to all tasks in the session.**

A session is one conversation with the user, from its start to its end.

The speaker check has two functions. It shows that the server is reachable. It
also shows the state of the speaker at the start of the session. After that,
each queue call prints the queue status. Thus, a second speaker check gives you
no new information.

- A new task in the same session does not need a new speaker check.
- A new request from the user does not need a new speaker check.
- Do not run the speaker check before a queue call or after a queue call.
- Do not run the speaker check to find out if a text played. It cannot tell you.
- Do not run the speaker check to find the time until the queue is empty. The
  queue output gives you that time.

An unusual condition can make a second speaker check necessary. Such a
condition is rare, and it is clear when it occurs. If you are not sure that
such a condition exists, do not run the speaker check again.

Output of `check_speaker.py`:

| Output line starts with | Meaning | Your action |
|-------------------------|---------|-------------|
| `FREE` | No audio plays, and the queue is empty. | Start the queue procedure. |
| `BUSY` | Audio plays, or texts wait in the queue. | Start the queue procedure. Use the queue output to set your pace. |
| `ERROR` | The server is not reachable. | Read `references/troubleshooting.md`. |

`FREE` and `BUSY` do not change the procedure. The number of seconds is only
an estimate.

## Discipline 1 — Queue (Default)

The queue plays texts in the order that it receives them. You send a text and
continue your work. You do not wait for the speaker.

### Procedure

1. If you did not do the speaker check in this session, run
   `python3 scripts/check_speaker.py`. If you did it before in this session,
   go to step 2.
2. Do your real work. The work has priority over the narration.
3. Write one block of text. A block is one to three sentences.
4. If the last queue call printed a `queue_entries` value more than 2, skip
   this block. Then go back to step 2.
5. Run the queue script one time with the block:
   ```bash
   python3 scripts/queue_ordered_speech_no_guarantee.py "The tests now pass." --voice am_onyx
   ```
6. Read the output line. Then go back to step 2.

Stop the procedure when one of these conditions occurs:

- The task ends.
- The text ends.
- The user tells you to stop.

### Output of the queue script

| Output line starts with | Meaning | Your action |
|-------------------------|---------|-------------|
| `QUEUED` | The server accepted the text. | Go back to step 2. |
| `REJECTED queue_full` | The queue is full. The text is lost. | Skip the next several blocks. |
| `BAD_REQUEST 400` | The request is not correct. | Correct the request from the message. Then send it again. |
| `ERROR` | The server is not reachable. | Read `references/troubleshooting.md`. |

Each `QUEUED` line and each `REJECTED` line gives two numbers:

- `queue_entries` is the number of texts that wait.
- `queue_seconds` is the estimated time to play all of these texts.

These two numbers are the queue status. You do not need the speaker check to
get them. Each line ends with `no_speaker_check_needed`. This word reminds you
of the speaker check rule.

Exit codes: `0` queued, `1` queue full, `2` bad request, `3` network error.

### Pace

- If `queue_entries` is more than 2, skip the next block.
- If `queue_seconds` is more than 60, skip the next one or two blocks.
- If `queue_seconds` increases over several calls, send fewer blocks.
- Send one block for each important result. Do not send a block for each file
  or for each command.
- If you skip a narration block, do not send it later.
- If you read a document aloud and a block is lost, send that block again later.

### What the queue does not promise

CAUTION: Do not report a queued text as spoken. The queue gives no guarantee
that a text plays.

- `QUEUED` means that the server accepted the text. It does not mean that the
  text played.
- No script can tell you if a text played.
- You cannot remove a text from the queue.

Thus, obey these rules:

- Send only text that stays true later. "The build finished" stays true. "I
  now start the tests" becomes false while it waits in the queue.
- If the user tells you that a text must be heard, queue it one time. Then
  tell the user that the queue cannot show if the text played.

### How to prepare a block

- Write plain words. Remove markdown symbols, for example `#`, `*`, `_`,
  backticks, list markers, and `>`.
- Keep the text of a link. Remove the URL.
- Do not read code, tables, or URLs word for word. Summarize them, or skip
  them and say so.
- Write abbreviations in full. For example, "e.g." becomes "for example".

### Changes from the user

The user can tell you to "slow down", "change the voice", "narrate less", or
"skip the tables". Apply the instruction to the next block and to all blocks
after it.

- To change the speed, use `--speed` with a value from 0.1 to 3.0.
- To change the voice, use `--voice NAME`.
- Do not start the read-through again.
- Text that is already in the queue does not change.

## Voices

Select a voice by its name. The server gets the language from the first letter
of the name.

| Voice | Use it for |
|-------|-----------|
| `af_heart` | Default female voice |
| `am_santa` | Default male voice |
| `bf_emma` | British female voice |
| `bm_george` | British male voice |

Use one voice for all of a task. Change the voice only when the user tells you
to. For all voice names, read `references/voices.md`.

## Subagent Sign-Off (Optional)

A subagent can speak one short sign-off message before it ends. The message
tells the user which subagent it is and what it did. The subagent speaks in a
random voice that is different from your voices.

Use the sign-off only when the user tells you to.

To request the sign-off, add these three lines to the instructions of each
subagent:

```text
When you complete your task, do the kokoro-api subagent sign-off.
Obey references/subagent-sign-off.md in the kokoro-api skill.
Voices of the main agent: af_heart, am_onyx
```

In the last line, obey these rules:

- Give each voice that you used in this session.
- List only your own voices. Do not list the voices of subagents. Two
  subagents can use the same voice.
- If you queued text without `--voice`, include `af_heart`. The server uses
  `af_heart` as its default voice.
- If you did not queue text in this session, write `none`.

The subagent does not do a speaker check.

## Hard Rules (All Disciplines)

1. Run one script in each tool call. Do not put two script runs in one command.
2. Do not use `sleep`. Do not write `while` loops or `until` loops.
3. Do not run the same status script two times in sequence.
4. Run `check_speaker.py` one time per session. Do not run it again in the
   same session, unless an unusual condition makes it necessary. A subagent
   does not run it for the sign-off.
5. Send only the options that this skill documents.
6. Do not add an effect unless the user asks for one.

## Quick Reference

| Task | Command |
|------|---------|
| Speaker check (one time per session) | `python3 scripts/check_speaker.py` |
| Queue a block (default) | `python3 scripts/queue_ordered_speech_no_guarantee.py "..." --voice am_onyx` |
| Queue a block from a file | `python3 scripts/queue_ordered_speech_no_guarantee.py --file /tmp/block.txt` |
| Random voice for a subagent sign-off | `python3 scripts/random_voice.py --exclude af_heart,am_onyx` |
| WAV file (only when the user asks) | See `references/wav-download.md` |
| Effects (only when the user asks) | See `references/effects.md` |
