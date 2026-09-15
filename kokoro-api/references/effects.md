# Audio Effects

An effect changes the sound of a voice. Examples are a radio sound or an
8-bit game sound. Effects work in all three disciplines. An effect does not
change how the queue, the speaker, or the download work.

**Use an effect only when the user asks for one.** The default is no effect.

## Use a preset

1. Run `python3 scripts/list_effects.py` one time. It prints each preset name
   and its stages.
2. Add `--effect NAME` to the script call:
   ```bash
   python3 scripts/queue_ordered_speech_no_guarantee.py "The build finished." --effect radio
   ```
3. Use the same effect for the whole task, unless the user tells you to
   change it.

Facts about preset names:

- Upper case and lower case are equal.
- Spaces, underscores, and hyphens are equal. Thus, `"vintage radio"` and
  `vintage-radio` are the same preset.
- The server operator can add presets. The output of `list_effects.py` is the
  correct list.

Built-in presets: `8-bit`, `ai`, `audiobook`, `broadcaster`, `cathedral`,
`cave`, `chipmunk`, `cyborg`, `deep`, `dragon`, `echo`, `ethereal`, `giant`,
`glitch`, `intercom`, `megaphone`, `monotone-female`, `monotone-male`,
`podcast`, `radio`, `robot`, `stadium`, `telephone`, `trailer`, `underwater`,
`vintage-radio`, `walkie-talkie`.

## Build a custom chain

If no preset agrees with the request, build a chain. A chain is a JSON list
of stages. The server applies the stages in the list order.

1. Run `python3 scripts/list_effects.py --stages` one time. It prints each
   stage type with its parameters and their limits.
2. Write the chain to a file:
   ```bash
   cat > /tmp/chain.json <<'JSON'
   [{"type": "pitch", "semitones": -3},
    {"type": "highpass", "cutoff_hz": 200},
    {"type": "reverb", "room_size": 0.6, "wet": 0.25}]
   JSON
   ```
3. Add `--effect-file /tmp/chain.json` to the script call.

Do not use `--effect` and `--effect-file` in the same call.

## Stage types

`highpass`, `lowpass`, `band`, `eq`, `bass`, `treble`, `low_shelf`,
`high_shelf`, `compressor`, `gain`, `volume`, `pitch`, `tempo`, `monotone`,
`phaser`, `chorus`, `flanger`, `tremolo`, `ring`, `reverb`, `delay`,
`bitcrush`, `downsample`, `distortion`, `overdrive`.

Facts about the stages:

- A parameter that you do not give gets its default value.
- A `pitch` stage changes the pitch only. The `semitones` parameter uses
  semitones. The preset list shows cents. Thus, `Pitch +150` is
  `"semitones": 1.5`.
- A `tempo` stage changes the speed only. It multiplies the `--speed` value.
- A `monotone` stage removes the melody of the speech. All speech gets one
  pitch, the `frequency_hz` value.
- In a `band` stage, `low_hz` must be less than `high_hz`.
- Reverb and delay add sound after the last word. The time estimates include
  this sound.

## Errors

- If `list_effects.py` shows `[UNAVAILABLE: ...]` after a preset name, the
  server cannot run that preset. Do not use it. Tell the user, and offer an
  available preset.

- If the output contains `valid_effects=[...]`, the preset name is unknown.
  Use a name from that list.
- If the message says `crash on this server's CPU`, the effect needs a stage
  type that the server disabled. Use a different effect, or no effect.
- If the output starts with `BAD_REQUEST 400`, the message gives the incorrect
  stage or parameter. Correct it. Then send the text again.
- If the output starts with `BAD_REQUEST local cannot read --effect-file`, the
  file is missing or is not valid JSON. The script sent nothing. Write the
  file again.
