# Subagent Sign-Off

Use this procedure only when your instructions tell you to do the kokoro-api
sign-off. This procedure is for a subagent.

The main agent is the agent that talks to the user in the main chat session.
The main agent started you and gave you your task.

## What the sign-off is

The sign-off is one short spoken message. You send it through the queue one
time, as your last action before you end. You speak it in a random voice that
is different from the voices of the main agent.

The message has two parts:

1. **Who you are.** Give your task, your function, or a different short name
   that the user can recognize.
2. **What you did.** Give the result that you completed, the fact that you
   learned, or the part that you contributed.

## Procedure

1. Complete your assigned task. Do the sign-off after all other work.
2. Find the line `Voices of the main agent:` in your instructions.
3. Run the random voice script. Give each voice of the main agent with
   `--exclude`:
   ```bash
   python3 scripts/random_voice.py --exclude af_heart,am_onyx
   ```
   If the line is `none` or is missing, use `--exclude af_heart`. The server
   uses `af_heart` when a call has no `--voice`.
4. Read the output line:
   - If it starts with `VOICE`, use that voice. Go to step 5.
   - If it starts with `BAD_REQUEST`, correct the voice name from the
     `valid_voices` list. Then do step 3 again.
   - If it starts with `NO_VOICE`, do not do the sign-off. Go to step 7.
5. Write the message. Use two or three sentences and 40 words or fewer.
6. Run the queue script one time with the message and the voice:
   ```bash
   python3 scripts/queue_ordered_speech_no_guarantee.py "This is the test agent. I ran the parser tests. All 212 tests pass." --voice bm_george
   ```
7. Give your final answer to the main agent.

## Rules

- Exclude only the voices of the main agent. Your voice can be the same as
  the voice of a different subagent.
- Do not run `check_speaker.py` for the sign-off. The main agent does the
  speaker check for the session.
- Send the sign-off one time only. If the output is `REJECTED queue_full`, do
  not send it again.
- Do not queue other text, unless your instructions tell you to.
- Do not add an effect, unless your instructions tell you to.
- Use the past tense. The message can wait in the queue. "I corrected the
  parser" stays true. "I now finish" becomes false.
- Write plain words. Do not read code, file paths, or URLs aloud.

## Examples

- "This is the research agent for the voice list. I found eleven voices in the
  image. Two of them were missing from the documentation."
- "This is the code review agent. I read the queue script. I found no errors."
- "This is the agent for the effects test. I did a test of the radio preset.
  The preset operates correctly on this server."
