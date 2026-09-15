# Discipline 2 — Direct Speech

Use this discipline only when the user tells you to. For example, the user
says "do not queue this" or "say this now". In all other cases, use
Discipline 1 (the queue) in `SKILL.md`.

## How direct speech works

- If the speaker is free, the server plays the text at once.
- If the speaker is busy, the server rejects the text. The server does not
  keep it. The user does not hear it.
- The script returns before the audio plays.
- Texts in the queue also make the speaker busy.

## Procedure

1. Run the direct speech script:
   ```bash
   python3 scripts/speak.py "Deployment is complete." --voice am_michael
   ```
2. If the output starts with `OK 202`, the task is done.
3. If the output starts with `BUSY 409`, the user did not hear the text.
   Do other work first, or end your turn.
4. After that, run `python3 scripts/check_speaker.py` one time.
5. If it prints `FREE`, send the same text again with `speak.py`.
6. If it prints `BUSY`, go back to step 3.

CAUTION: Do not send a `BUSY` text to the queue. The queue cannot tell you
if the user heard it.

CAUTION: Do not use direct speech and the queue in the same task. Your own
queue makes the speaker busy, and you cannot clear the queue.

## Output of `speak.py`

| Output line starts with | Meaning |
|-------------------------|---------|
| `OK 202 spoken estimated_duration_seconds=N` | The server accepted the text. It plays in the background. |
| `BUSY 409 not_spoken estimated_seconds_until_free=N` | The speaker was busy. No audio played. |
| `BAD_REQUEST 400` | The request is incorrect. The message gives the cause. |
| `ERROR` | The server is not reachable. |

Exit codes: `0` accepted, `1` busy, `2` bad request, `3` network error.

## Output of `check_speaker.py`

| Output line | Meaning |
|-------------|---------|
| `FREE queue_entries=0 queue_seconds=0.0` | The speaker is free now. |
| `BUSY queue_entries=N estimated_seconds_until_free=N` | The speaker plays audio, or texts wait in the queue. |

The seconds value is an estimate. A busy speaker can show `0.0` when the
playback is almost complete. The words `FREE` and `BUSY` are correct. The
number can be incorrect.

Exit codes: `0` free, `1` busy, `3` network error.

## Options

`speak.py` accepts the same options as the queue script: text or
`--file PATH`, `--voice`, `--speed`, `--effect`, `--effect-file`, `--host`,
and `--port`.
