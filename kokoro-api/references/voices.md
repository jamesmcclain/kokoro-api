# Voices

The server supports American English and British English voices. The first
letter of the name gives the language. You do not give a language.

## Fast voices

The server keeps these voices in its cache. They start with no delay:

```
af_heart, af_river, af_alloy, af_nicole, am_santa, am_michael, am_onyx,
bf_emma, bm_george
```

Other voices work too. The first request for another voice is slow, because
the server downloads it one time.

## All voices

```
American female: af_heart, af_alloy, af_aoede, af_bella, af_jessica, af_kore,
                 af_nicole, af_nova, af_river, af_sarah, af_sky
American male:   am_adam, am_echo, am_eric, am_fenrir, am_liam, am_michael,
                 am_onyx, am_puck, am_santa
British female:  bf_alice, bf_emma, bf_isabella, bf_lily
British male:    bm_daniel, bm_fable, bm_george, bm_lewis
```

## Rules

- The default female voice is `af_heart`. The default male voice is `am_santa`.
- If the user asks for a British voice, use `bf_emma` or `bm_george`.
- If the user names a different British voice, use that voice.
- Use one voice for a whole task, unless the user tells you to change it.
