# Discipline 3 — WAV Download

Use this discipline only when the user asks for an audio file. This
discipline does not use the speaker. It makes no sound.

## Procedure

1. Run the download script:
   ```bash
   python3 scripts/download_wav.py "Hello as a file." --voice af_heart --out output.wav
   ```
2. If the output starts with `OK wrote`, the file is ready.
3. If the output starts with `BAD_REQUEST` or `ERROR`, the script wrote no
   file. Read the message for the cause.

## Facts about the download

- The script waits until the server completes the synthesis.
- A long text needs more time. If the text is long, add `--max-time SECONDS`.
  The default is 300.
- If the text is very long, divide it into smaller files.
- The download works while the speaker plays other audio. It never reports
  that the speaker is busy.

## Output of `download_wav.py`

| Output line starts with | Meaning |
|-------------------------|---------|
| `OK wrote PATH bytes=N` | The script wrote the WAV file. |
| `BAD_REQUEST 400` | The request is incorrect. The message gives the cause. |
| `BAD_REQUEST local` | The `--effect-file` is incorrect. The script sent nothing. |
| `ERROR` | The server is not reachable, or the server sent no WAV data. |

Exit codes: `0` file written, `2` bad request, `3` network or server error.

## Options

`download_wav.py` needs `--out PATH`. It also accepts text or `--file PATH`,
`--voice`, `--speed`, `--effect`, `--effect-file`, `--max-time`, `--host`,
and `--port`.
