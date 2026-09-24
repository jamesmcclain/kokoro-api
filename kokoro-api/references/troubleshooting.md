# Troubleshooting

## The server is not reachable

The output starts with `ERROR request failed`. The script cannot connect to
the server.

1. Ask the user for the name or IP address of the server.
2. Add `--host NAME` to each script call.
3. If the port is not 5001, also add `--port N`.
4. To set the server for the whole session, export `KOKORO_HOST` and
   `KOKORO_PORT`.

## Script errors

| Output or error | Cause | Action |
|-----------------|-------|--------|
| `ModuleNotFoundError: requests` | The `requests` package is missing. | Run `pip install requests`. |
| `BAD_REQUEST 400` with `valid_voices` | The voice name is unknown. | Use a name from the list, or remove `--voice`. |
| `BAD_REQUEST 400` with `valid_effects` | The preset name is unknown. | Use a name from the list, or remove `--effect`. |
| `BAD_REQUEST 400` with `crash on this server's CPU` | The server disabled a stage type that the effect needs. | Use a different effect, or remove `--effect`. |
| `BAD_REQUEST 400` (other) | A parameter is incorrect. | Correct the parameter from the message. Then send the text again. |
| `BAD_REQUEST local cannot read --effect-file` | The chain file is missing or is not valid JSON. | Write the file again. The script sent nothing. |

## Queue results (Discipline 1)

| Result | Cause | Action |
|--------|-------|--------|
| `REJECTED queue_full` | You sent texts faster than the speaker plays them. | Skip the next several blocks. The text is lost. |
| `queue_entries` stays more than 2 | You send too many blocks. | Send fewer blocks. Write shorter sentences. |
| A text in the queue does not play | The queue gives no guarantee. | You cannot find this fault. Do not try to find it. |

Do not run `check_speaker.py` to find a queue fault. It cannot find one.

## Direct speech results (Discipline 2)

| Result | Cause | Action |
|--------|-------|--------|
| `BUSY 409` | The speaker plays other audio. This is not an error. | Obey the procedure in `direct-speech.md`. |
| `BUSY` with `0.0` seconds | The estimate was too short. The playback is almost complete. | Run `check_speaker.py` again later, after other work. |

## Other symptoms

| Symptom | Cause | Action |
|---------|-------|--------|
| The first request for a voice is slow | The server downloads that voice one time. | Wait. The next requests are fast. |
| `download_wav.py` does not stop | A long text needs a long synthesis. | Divide the text, or increase `--max-time`. |
