"""
server.py — Minimal REST API around Kokoro-82M that plays synthesized speech
out of the host's speakers (via a PulseAudio socket mounted into the
container).

The host has exactly one speaker, so all playback is serialized behind a
speaker mutex: at most one utterance can be playing at a time. A /speak
request that asks for playback while the speaker is busy is REJECTED
immediately (409 Conflict) rather than queued; the error response includes
an estimate of how many seconds remain until the speaker is free, and the
same estimate can be polled at any time via GET /speaker.

Queueing is strictly opt-in and lives on its own endpoint,
POST /queue_with_no_guarantee_of_playing. Nothing about /speak changes:
it never queues, never waits, and still 409s the moment the speaker isn't
idle. The queue endpoint offers no guarantee that an accepted utterance is
ever actually spoken, and — deliberately — no way for a caller to find
out. Accepted text cannot be cancelled or removed; it either exits through
the speaker or is dropped, and the caller is told neither. The only
failure a queue caller ever observes is the up-front rejection when the
queue is already full (503).

Wherever this server reports "time until the speaker is free", that number
is the sum of the WHOLE pipeline: whatever is playing now plus every
queued entry behind it. With an empty queue that is exactly the old
single-utterance figure, so existing callers of GET /speaker and of the
409 body keep their previous semantics unchanged.

There is no async/sync switch: the execution mode is determined entirely
by what the request is for. Playback ("play": true, the default) is
asynchronous per force — the request is validated, the speaker is claimed,
and 202 Accepted is returned before synthesis even starts; rendering and
playback then happen in a background thread, and the speaker mutex is
released only once playback has actually finished draining. WAV download
("play": false) is synchronous per force — the request blocks until
synthesis completes and the response body is the WAV bytes (e.g. `curl ...
-o out.wav`); it never touches the speaker, so it neither takes the mutex
nor can it fail with 409.

Endpoints:
  GET  /health   -> {"status": "ok"}
  GET  /effects  -> {
                      "presets": [{"name", "source", "summary", "stages"}, ...],
                      "stage_types": {type: {param: {"default","min","max"}}},
                      "max_stages": <integer>
                    }
                  Everything a caller needs to pick a preset or build an
                  inline chain. "summary" is a short human-readable label
                  per stage (e.g. "HP 85Hz", "Comp -22dB 3:1"). "source" is
                  "built-in" or "user" (see KOKORO_EFFECTS_FILE below).
  GET  /speaker  -> {
                      "busy": true|false,
                      "queue_entries": <integer>,
                      "estimated_seconds_until_free": <number>
                    }
                  Approximately how long until the speaker is free, counting
                  the current utterance AND everything queued behind it.
                  "queue_entries" is how many queued utterances are still
                  waiting (it excludes the one currently playing), so
                  busy=true with queue_entries=0 is the pre-queue state:
                  one utterance playing, nothing behind it.
                  0.0 with busy=false means the speaker is free right now.
                  The estimate is derived from the word-count/RTF heuristic
                  described below, so it can undershoot: if it reaches 0.0
                  while "busy" is still true, playback is expected to end
                  imminently but hasn't yet — trust "busy", not the number,
                  for the question "can I speak right now?".
  POST /speak     body: {
                     "text": "...",           (required)
                     "voice": "af_heart",     (optional, default af_heart)
                     "speed": 1.0,            (optional, default 1.0, range 0.1-3.0)
                     "effect": "audiobook",   (optional, default none; see Effects)
                     "play": true             (optional, default true)
                   }
                  play=true — speak through the host speaker,
                  asynchronously: if the speaker is free, it is claimed
                  and 202 Accepted is returned immediately, before
                  synthesis starts. If the speaker is busy, 409 Conflict
                  is returned with "estimated_seconds_until_free" and
                  nothing is synthesized.

                  The 202 response includes "estimated_duration_seconds":
                  an estimate of total time until the utterance has
                  finished playing. This is the sum of (1) an estimated
                  playback duration, based on word count and a typical
                  speaking rate, and (2) an estimated rendering duration —
                  how long Kokoro itself will take to synthesize the audio
                  — derived from a running average of actual measured
                  render times on this server. Neither component is an
                  exact measurement of this specific request (the audio
                  doesn't exist yet when the response is sent), so treat
                  the total as a ballpark, not a guarantee. This same
                  estimate is what backs GET /speaker while the utterance
                  is playing.

                  play=false — render the text and return the WAV bytes,
                  synchronously: the response is the audio itself
                  (Content-Type: audio/wav), available only once synthesis
                  has finished. No speaker involvement, no mutex, no 409.

                  The former "async" and "return_audio" fields have been
                  removed; a request that still sends either gets a 400
                  explaining the new contract, rather than a silent
                  reinterpretation of what the caller meant.

  POST /queue_with_no_guarantee_of_playing
                  body: {
                     "text": "...",           (required)
                     "voice": "af_heart",     (optional, default af_heart)
                     "speed": 1.0,            (optional, default 1.0, range 0.1-3.0)
                     "effect": "audiobook"    (optional, default none; see Effects)
                   }
                  Appends the utterance to the playback queue and returns
                  202 immediately. The endpoint name is the contract: there
                  is NO guarantee the text is ever spoken, and no way to
                  ask. A queued entry cannot be removed once accepted.
                  Synthesis failures are logged and swallowed; the caller
                  is not told. The 202 body reports the state of the queue
                  after the append ("queue_entries", "queue_seconds") purely
                  so a caller can pace itself — those numbers say nothing
                  about whether any earlier submission was heard.

                  503 is the only failure a caller can act on: the queue is
                  full (by entry count or by total queued seconds) and this
                  text was NOT accepted. There is no "play" field here;
                  sending one is a 400, since this endpoint always plays
                  and never returns audio.

Effects:
  "effect" applies post-synthesis audio processing, in either form:
    "effect": "8-bit"                               a preset (case-insensitive;
                                                    "Vintage radio" also
                                                    matches "vintage-radio")
    "effect": [{"type": "highpass", "cutoff_hz": 85},
               {"type": "reverb", "wet": 0.1}]      an inline chain
  Omitting it, null, "none", or [] all mean no effect. Invalid values are a
  400 with the reason (and "valid_effects" for an unknown preset name).
  GET /effects lists presets and every stage type with its parameter
  ranges. Effects never change the speaker, queue, or play/download
  contracts above; reverb and delay tails are included in the audio and in
  the duration estimates.

  Operators can add presets, or override built-ins by name, without
  rebuilding the image: put {"name": [stage, ...], ...} in a JSON file at
  KOKORO_EFFECTS_FILE (default /config/effects.json; run.sh mounts
  ./effects.json there if it exists). The file is validated at startup, and
  a malformed one stops the server rather than failing request by request.
"""

import collections
import io
import os
import subprocess
import threading
import time

import numpy as np
import soundfile as sf
from flask import Flask, request, send_file, jsonify
from kokoro import KPipeline

from effects import EffectError, EffectRegistry

app = Flask(__name__)

# Kokoro voice names encode their language as the first letter (e.g.
# "af_heart" -> American, "bf_emma" -> British), which is also the
# lang_code KPipeline needs. Callers only ever specify a voice, never a
# lang_code, so we derive it here and keep one pipeline per lang_code,
# built lazily on first use and cached for the lifetime of the process.
# The American pipeline is always built eagerly at startup (it's the
# default voice's language and is baked into the Docker image); other
# lang_codes are only built the first time a voice needs them, so a
# server that never receives a British-voice request never pays for a
# second pipeline.
_pipelines = {}
_pipelines_lock = threading.Lock()


def _get_pipeline(voice):
    """Returns the KPipeline for the given voice's language, creating and
    caching it on first use. Not synthesis-safe to call concurrently for
    a *new* lang_code until the lock is released, but callers only ever
    read from the dict after that, which is safe."""
    lang_code = voice[0]
    pipeline = _pipelines.get(lang_code)
    if pipeline is not None:
        return pipeline
    with _pipelines_lock:
        pipeline = _pipelines.get(lang_code)
        if pipeline is None:
            pipeline = KPipeline(lang_code=lang_code)
            _pipelines[lang_code] = pipeline
        return pipeline


# Build the American pipeline eagerly at startup so the default voice
# pays no first-request latency penalty.
_pipelines["a"] = KPipeline(lang_code="a")

DEFAULT_VOICE = "af_heart"
SAMPLE_RATE = 24000

# Loaded once at startup; raises (and so stops the server) if the operator's
# presets file is malformed.
EFFECTS_FILE = os.environ.get("KOKORO_EFFECTS_FILE", "/config/effects.json")
EFFECTS = EffectRegistry(EFFECTS_FILE)

# Rough average speaking rate for Kokoro's English voices at speed=1.0,
# used only to produce an *estimate* of audio duration before synthesis
# has actually happened. This is a heuristic, not a measurement: real
# duration depends on punctuation/pauses, the specific voice, and how the
# text tokenizes into phonemes. Treat the estimate as a ballpark, not a
# guarantee.
WORDS_PER_MINUTE = 165

# American and British English voices (see
# https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md). Voices
# from other languages aren't included here since this server only ever
# builds pipelines for lang_codes it recognizes via the voice name's
# first letter, and non-English g2p isn't exercised/supported by this
# server. Callers just pick a voice; _get_pipeline() figures out whether
# it needs the American ('a') or British ('b') pipeline from the voice
# name itself, so there's nothing lang-related for a caller to specify.
# A handful of these (see Dockerfile) are baked into the image at build
# time for zero-network-latency use; the rest still work, they just
# download their voice pack from Hugging Face on first use.
VALID_VOICES = frozenset({
    # American English
    "af_heart", "af_alloy", "af_aoede", "af_bella", "af_jessica", "af_kore",
    "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael",
    "am_onyx", "am_puck", "am_santa",
    # British English
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
})

# --- Rendering-time estimation -------------------------------------------
#
# estimated_duration_seconds (returned to the caller) is meant to be "how
# long from now until this utterance has finished playing" — which is
# rendering time (Kokoro actually synthesizing the audio) PLUS playback
# time (the audio playing at real speed), not playback time alone.
#
# We don't know rendering time in advance, so we track it as a running
# "real-time factor" (RTF): seconds of rendering per second of resulting
# audio. RTF_ESTIMATE starts at a seeded guess and is refined via an
# exponential moving average using the actual measured render time of
# every synthesis this process performs (played or not), so the estimate
# adapts to the host's real hardware/load over the server's lifetime.
_rtf_lock = threading.Lock()
_rtf_estimate = 0.3  # seed guess: render time ~= 30% of audio duration
_RTF_SMOOTHING = 0.3  # weight given to each new measurement


def _get_rtf_estimate():
    with _rtf_lock:
        return _rtf_estimate


def _update_rtf_estimate(render_seconds, audio_duration_seconds):
    global _rtf_estimate
    if audio_duration_seconds <= 0:
        return
    measured_rtf = render_seconds / audio_duration_seconds
    with _rtf_lock:
        _rtf_estimate = (
            (1 - _RTF_SMOOTHING) * _rtf_estimate + _RTF_SMOOTHING * measured_rtf
        )


def _estimate_total_duration(text, speed, chain=None):
    """Estimated seconds from 'now' until an utterance of this text has
    finished playing: estimated playback time (word count at a typical
    speaking rate, scaled by speed, plus any effect tail such as reverb)
    plus estimated rendering time (the rolling RTF measured on this
    server, which already absorbs effect processing cost). A ballpark, not
    a guarantee."""
    word_count = len(text.split())
    if chain is not None:
        speed = chain.effective_speed(speed)
    estimated_playback = (word_count / WORDS_PER_MINUTE) * 60 / speed
    if chain is not None:
        estimated_playback += chain.tail_seconds
    estimated_rendering = estimated_playback * _get_rtf_estimate()
    return round(estimated_playback + estimated_rendering, 1)


# --- Speaker mutex ---------------------------------------------------------
#
# The host has one speaker, and this API's contract is fail-fast rather
# than queue: at most one utterance may hold the speaker at a time, and a
# playback request made while the speaker is held is rejected immediately
# (409) instead of blocking behind the current utterance.
#
# _speaker_mutex is claimed *synchronously in the request handler* (a
# non-blocking acquire, so two racing requests can't both win) before the
# 202 is sent, and released by the background thread only after playback
# has fully finished (paplay has drained and exited), not merely after
# synthesis. It is deliberately NOT used to serialize synthesis itself:
# play=false renders never touch it.
#
# _speaker_busy_until is the estimate backing "how long until the speaker
# is free": the monotonic time at which the current utterance is
# *expected* to finish, set from the same word-count/RTF heuristic that
# produces estimated_duration_seconds. Because it's a heuristic, the
# remaining-time figure can reach 0 while playback is still draining;
# "busy" (i.e. whether the mutex is actually held) is the ground truth.
_speaker_mutex = threading.Lock()
_speaker_state_lock = threading.Lock()  # guards _speaker_busy_until
_speaker_busy_until = 0.0  # time.monotonic() at which the speaker should free up


def _try_claim_speaker(estimated_duration_seconds):
    """Attempts to claim the speaker without blocking. On success, records
    when it's expected to be free again and returns True; the caller (or a
    thread the caller spawns) is then responsible for _release_speaker().
    Returns False if some other utterance currently holds it, or if queued
    utterances are still waiting.

    The queue check matters: without it, a /speak request could win the
    mutex in the gap between two queued utterances and cut the line. A
    caller that wanted fail-fast semantics gets them — 409 — and the
    accompanying estimate covers the whole queue, so the caller isn't told
    to retry in three seconds only to be rejected again by the next queued
    entry. Momentarily holding the mutex to look at the queue is harmless:
    the queue worker acquires it blocking, so it just waits."""
    global _speaker_busy_until
    if not _speaker_mutex.acquire(blocking=False):
        return False
    with _queue_lock:
        queue_is_pending = bool(_queue)
    if queue_is_pending:
        _speaker_mutex.release()
        return False
    with _speaker_state_lock:
        _speaker_busy_until = time.monotonic() + estimated_duration_seconds
    return True


def _release_speaker():
    global _speaker_busy_until
    with _speaker_state_lock:
        _speaker_busy_until = 0.0
    _speaker_mutex.release()


def _speaker_status():
    """Returns (busy, queue_entries, estimated_seconds_until_free).

    The seconds figure is the sum of the whole pipeline: the remainder of
    whatever is playing now, plus the estimates of every entry still
    queued behind it. With an empty queue this reduces exactly to the
    old single-utterance number.

    "busy" means the speaker is not idle for any reason — playing, or with
    work queued that hasn't started yet. A caller asking "can I speak right
    now?" gets the truthful answer from this flag, not from the estimate,
    which is a heuristic and can reach 0.0 early."""
    with _queue_lock:
        queue_entries = len(_queue)
        queued_seconds = _queued_seconds
    playing = _speaker_mutex.locked()
    if not playing and queue_entries == 0:
        return False, 0, 0.0
    remaining = 0.0
    if playing:
        with _speaker_state_lock:
            remaining = _speaker_busy_until - time.monotonic()
    total = max(remaining, 0.0) + queued_seconds
    return True, queue_entries, max(round(total, 1), 0.0)


# --- Unguaranteed playback queue -------------------------------------------
#
# POST /queue_with_no_guarantee_of_playing appends here. A single worker
# thread drains it, one utterance at a time, taking the same speaker mutex
# that /speak takes — so queued and direct playback can never overlap, and
# arrival order is playback order.
#
# The "no guarantee" in the endpoint name is load-bearing and is enforced
# here by omission: nothing in this section records per-entry outcomes,
# exposes an entry id, or offers a way to remove an accepted entry. A
# synthesis failure is logged and the worker moves on. Callers cannot
# distinguish "spoken", "dropped", or "still waiting", and that is the
# intended contract, not a gap to be filled in later.
#
# Because nothing can be cancelled, admission control is the only place
# where the system can protect itself, so it is deliberately conservative
# and bounded two ways: by entry count, and by total projected seconds
# (one 20-minute submission is a far worse hostage than eight short ones).
MAX_QUEUE_ENTRIES = 8
MAX_QUEUE_SECONDS = 300.0

_queue = collections.deque()
_queued_seconds = 0.0  # sum of the estimates of the entries in _queue
_queue_lock = threading.Condition()


def _queue_snapshot():
    """(entries, seconds) for the entries still waiting. Excludes whatever
    the worker has already pulled off and started playing — that portion is
    accounted for by _speaker_busy_until instead."""
    with _queue_lock:
        return len(_queue), round(_queued_seconds, 1)


def _try_enqueue(text, voice, speed, chain):
    """Appends one utterance if there is room. Returns (accepted, entries,
    seconds) describing the queue after the decision. Rejection is the only
    signal this endpoint ever gives a caller, so it happens here, up front,
    while the caller is still listening."""
    global _queued_seconds
    estimated_duration = _estimate_total_duration(text, speed, chain)
    with _queue_lock:
        full = (
            len(_queue) >= MAX_QUEUE_ENTRIES
            or _queued_seconds + estimated_duration > MAX_QUEUE_SECONDS
        )
        if not full:
            _queue.append((text, voice, speed, chain, estimated_duration))
            _queued_seconds += estimated_duration
            _queue_lock.notify()
        return (not full), len(_queue), round(_queued_seconds, 1)


def _queue_worker():
    """Single consumer. Waits for an entry, claims the speaker (blocking —
    unlike /speak, waiting is the whole point here), plays it, releases.

    The entry is popped only after the mutex is held, so it stays counted
    in _queued_seconds until the moment it becomes the utterance that
    _speaker_busy_until describes. That hand-off is what keeps
    'seconds until free' from double-counting or dropping the entry as it
    transitions from waiting to playing.

    Every failure is caught and logged: this loop must never exit, or the
    queue would silently stop draining while still accepting entries."""
    global _queued_seconds, _speaker_busy_until
    while True:
        try:
            with _queue_lock:
                while not _queue:
                    _queue_lock.wait()
            _speaker_mutex.acquire()
            try:
                with _queue_lock:
                    text, voice, speed, chain, estimated_duration = _queue.popleft()
                    _queued_seconds = max(_queued_seconds - estimated_duration, 0.0)
                with _speaker_state_lock:
                    _speaker_busy_until = time.monotonic() + estimated_duration
                try:
                    _run_pipeline(text, voice, speed, play=True, chain=chain)
                except Exception as e:
                    app.logger.error("queued synthesis failed for voice=%s: %s", voice, e)
            finally:
                _release_speaker()
        except Exception as e:  # pragma: no cover - the loop must survive anything
            app.logger.error("queue worker iteration failed: %s", e)


def _open_paplay_stream():
    """Starts a single long-lived `paplay` process reading raw float32
    PCM from stdin, so audio chunks can be written to it as Kokoro
    produces them instead of waiting for the whole utterance to render
    first. Returns the Popen handle, or None if paplay can't be started
    (e.g. missing binary), in which case the caller should skip playback
    entirely rather than fail synthesis."""
    try:
        return subprocess.Popen(
            [
                "paplay",
                "--raw",
                "--format=float32le",
                f"--rate={SAMPLE_RATE}",
                "--channels=1",
            ],
            stdin=subprocess.PIPE,
        )
    except FileNotFoundError as e:
        app.logger.warning("paplay not available: %s", e)
        return None


def _run_pipeline(text, voice, speed, play, chain=None):
    """Synthesizes with Kokoro chunk-by-chunk. If play is True, each
    chunk is streamed to a live `paplay` process as soon as it's
    produced, so playback of chunk N starts while chunk N+1 is still
    being rendered — this is what actually reduces time-to-first-phoneme,
    as opposed to concatenating everything and playing it only once the
    entire utterance has been synthesized. When play is True, this
    function does not return until paplay has drained and exited, i.e.
    until the speaker is genuinely done — callers rely on that to know
    when it's safe to release the speaker mutex.

    Callers that pass play=True must already hold the speaker mutex;
    this function itself neither claims nor releases it.

    If chain is given, each chunk passes through a fresh EffectProcessor
    before it is played or kept, and the effect tail (reverb, delay) is
    flushed after the last chunk, so the speaker and the WAV both get it.

    Measures total wall-clock render time and feeds it into the rolling
    RTF estimate used for future duration estimates. Returns the
    concatenated audio array (still needed for return_audio and for the
    duration measurement). Raises RuntimeError if synthesis produced no
    audio."""
    paplay_proc = _open_paplay_stream() if play else None
    pipeline = _get_pipeline(voice)
    processor = chain.new_processor(SAMPLE_RATE) if chain is not None else None
    if chain is not None:
        # Tempo stages change the rate Kokoro speaks at, not the audio.
        speed = chain.effective_speed(speed)

    start = time.time()
    audio_chunks = []

    def emit(samples):
        nonlocal paplay_proc
        if len(samples) == 0:
            return
        audio_chunks.append(samples)
        if paplay_proc is not None:
            try:
                paplay_proc.stdin.write(samples.tobytes())
            except (BrokenPipeError, OSError) as e:
                app.logger.warning("paplay write failed: %s", e)
                paplay_proc = None

    try:
        for _, _, audio in pipeline(text, voice=voice, speed=speed):
            if audio is None:
                continue
            samples = np.asarray(audio, dtype=np.float32)
            if processor is not None:
                samples = processor.process(samples)
            emit(samples)
        if processor is not None and audio_chunks:
            emit(processor.flush())
    finally:
        if paplay_proc is not None:
            try:
                paplay_proc.stdin.close()
                paplay_proc.wait()
            except (BrokenPipeError, OSError) as e:
                app.logger.warning("paplay close/wait failed: %s", e)
    render_seconds = time.time() - start

    if not audio_chunks:
        raise RuntimeError("synthesis produced no audio")

    full_audio = np.concatenate(audio_chunks)
    audio_duration_seconds = len(full_audio) / SAMPLE_RATE
    _update_rtf_estimate(render_seconds, audio_duration_seconds)
    return full_audio


def _synthesize_and_play(text, voice, speed, chain):
    """Runs in a background thread with the speaker mutex already held by
    the request handler that spawned it: synthesizes with Kokoro,
    streaming each chunk to paplay as it's produced. Releases the speaker
    mutex once playback has fully finished (or failed) — this release
    must happen on every path, otherwise the speaker would be stuck busy
    forever. Any failure here is only logged, since the HTTP response has
    already been sent."""
    try:
        _run_pipeline(text, voice, speed, play=True, chain=chain)
    except Exception as e:
        app.logger.error("synthesis failed for voice=%s: %s", voice, e)
    finally:
        _release_speaker()


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/effects", methods=["GET"])
def effects():
    """Presets (built-in and operator-defined) and the stage schema for
    building inline chains."""
    return jsonify(EFFECTS.describe())


@app.route("/speaker", methods=["GET"])
def speaker():
    """Approximately how long until the speaker is free, counting the
    current utterance plus everything queued behind it. busy=false means
    it's free right now; busy=true with a 0.0 estimate means the pipeline
    overran its estimate and should finish imminently."""
    busy, queue_entries, seconds = _speaker_status()
    return jsonify({
        "busy": busy,
        "queue_entries": queue_entries,
        "estimated_seconds_until_free": seconds,
    })


def _validate_speech_params(text, voice, speed):
    """Shared parameter validation for both playback endpoints. Returns a
    (body, status) tuple to return to the caller, or None if the
    parameters are acceptable. Kept in one place so the queue endpoint
    can't drift into accepting something /speak rejects."""
    if not text:
        return {"error": "missing 'text' field"}, 400
    if voice not in VALID_VOICES:
        return {
            "error": f"unknown voice '{voice}'",
            "valid_voices": sorted(VALID_VOICES),
        }, 400
    if not isinstance(speed, (int, float)) or not (0.1 <= speed <= 3.0):
        return {"error": "'speed' must be a number between 0.1 and 3.0"}, 400
    return None


def _effect_label(chain):
    """What a 202 body reports under "effect": the preset name, "custom"
    for an inline chain, or null."""
    if chain is None:
        return None
    return chain.name or "custom"


def _resolve_effect(payload):
    """Returns (chain, None) on success — chain is None for "no effect" — or
    (None, (body, status)) for a 400. Shared by both playback endpoints and
    by WAV download so they can't disagree about what's valid."""
    try:
        return EFFECTS.resolve(payload.get("effect")), None
    except EffectError as e:
        body = {"error": str(e)}
        if isinstance(payload.get("effect"), str):
            body["valid_effects"] = EFFECTS.names()
        return None, (body, 400)


@app.route("/queue_with_no_guarantee_of_playing", methods=["POST"])
def queue_with_no_guarantee_of_playing():
    """Appends an utterance to the playback queue, or rejects it because
    the queue is full. That rejection is the last thing the caller will
    ever learn about this text: an accepted entry cannot be removed, its
    outcome is never reported, and no endpoint exists to ask.

    Validation is still synchronous — a malformed request is the caller's
    bug and it gets told about that immediately, which is a different
    thing from being told whether the audio played."""
    payload = request.get_json(force=True, silent=True) or {}
    text = payload.get("text")
    voice = payload.get("voice", DEFAULT_VOICE)
    speed = payload.get("speed", 1.0)

    if "play" in payload:
        return jsonify({
            "error": "this endpoint has no 'play' field: it always plays "
                     "through the speaker and never returns audio. Use "
                     "POST /speak with play=false to download a WAV.",
        }), 400
    invalid = _validate_speech_params(text, voice, speed)
    if invalid is not None:
        body, status = invalid
        return jsonify(body), status

    chain, invalid = _resolve_effect(payload)
    if invalid is not None:
        body, status = invalid
        return jsonify(body), status

    accepted, queue_entries, queue_seconds = _try_enqueue(text, voice, speed, chain)
    if not accepted:
        return jsonify({
            "error": "queue is full",
            "queue_entries": queue_entries,
            "queue_seconds": queue_seconds,
        }), 503
    return jsonify({
        "status": "queued",
        "voice": voice,
        "effect": _effect_label(chain),
        "queue_entries": queue_entries,
        "queue_seconds": queue_seconds,
    }), 202


@app.route("/speak", methods=["POST"])
def speak():
    payload = request.get_json(force=True, silent=True) or {}
    text = payload.get("text")
    voice = payload.get("voice", DEFAULT_VOICE)
    speed = payload.get("speed", 1.0)
    # The single mode switch: play=true (default) means asynchronous
    # playback through the host speaker; play=false means synchronous
    # WAV download. There is no independent async knob.
    play = payload.get("play", True)

    # Validation that's cheap and immediate, done synchronously in both
    # modes so the caller always gets parameter errors right away.
    if "async" in payload:
        return jsonify({
            "error": "the 'async' field has been removed: playback "
                     "(play=true) is always asynchronous and WAV download "
                     "(play=false) is always synchronous. Drop the field "
                     "and choose the mode via 'play'.",
        }), 400
    if "return_audio" in payload:
        return jsonify({
            "error": "the 'return_audio' field has been removed: "
                     "play=false always returns the WAV bytes, and "
                     "play=true never can (the audio doesn't exist yet "
                     "when its 202 response is sent). Drop the field and "
                     "choose the mode via 'play'.",
        }), 400
    invalid = _validate_speech_params(text, voice, speed)
    if invalid is not None:
        body, status = invalid
        return jsonify(body), status
    if not isinstance(play, bool):
        return jsonify({"error": "'play' must be a boolean"}), 400
    chain, invalid = _resolve_effect(payload)
    if invalid is not None:
        body, status = invalid
        return jsonify(body), status

    if play:
        # Asynchronous playback. The speaker must be claimed *now*, in
        # the request handler, so that a busy speaker turns into an
        # immediate failure instead of overlapping audio or an invisible
        # queue. The non-blocking acquire is the arbiter when two
        # requests race.
        estimated_duration = _estimate_total_duration(text, speed, chain)
        if not _try_claim_speaker(estimated_duration):
            _, _, seconds_until_free = _speaker_status()
            return jsonify({
                "error": "speaker is busy",
                "estimated_seconds_until_free": seconds_until_free,
            }), 409

        # From here on the speaker is ours; _synthesize_and_play releases
        # it when playback finishes. If the thread somehow fails to start,
        # release immediately so the speaker can't leak into a stuck-busy
        # state.
        try:
            threading.Thread(
                target=_synthesize_and_play,
                args=(text, voice, speed, chain),
                daemon=True,
            ).start()
        except Exception:
            _release_speaker()
            raise
        return jsonify({
            "status": "accepted",
            "voice": voice,
            "effect": _effect_label(chain),
            "played": True,
            "estimated_duration_seconds": estimated_duration,
        }), 202

    # Synchronous WAV download. Blocks until synthesis completes, then
    # returns the audio bytes. Never touches the speaker, so it neither
    # takes the mutex nor competes with playback.
    try:
        full_audio = _run_pipeline(text, voice, speed, play=False, chain=chain)
    except RuntimeError:
        return jsonify({"error": "synthesis produced no audio"}), 500

    buf = io.BytesIO()
    sf.write(buf, full_audio, SAMPLE_RATE, format="WAV")
    buf.seek(0)
    return send_file(buf, mimetype="audio/wav", download_name="speech.wav")


def _warm_up():
    """Runs one throwaway synthesis (no playback, so no speaker mutex)
    before the server starts accepting requests. The Dockerfile already
    bakes model weights and voice packs into the image at build time, but
    a fresh container still pays a first-inference tax when the process
    actually starts (e.g. lazy kernel/thread-pool init). Paying that cost
    here means the first real /speak request doesn't have to."""
    try:
        start = time.time()
        _run_pipeline("Warming up.", DEFAULT_VOICE, 1.0, play=False)
        app.logger.info("warm-up synthesis completed in %.2fs", time.time() - start)
    except Exception as e:
        app.logger.warning("warm-up synthesis failed (non-fatal): %s", e)


if __name__ == "__main__":
    _warm_up()
    # The queue worker is the only thread that ever plays queued audio, so
    # it must be running before the queue endpoint can accept anything.
    # Daemon: a queue that hasn't drained must not keep the process alive
    # on shutdown — undelivered entries are, by this endpoint's contract,
    # exactly as unobservable as delivered ones.
    threading.Thread(target=_queue_worker, daemon=True).start()
    # Threaded so GET /speaker can be answered while a playback thread is
    # running (Flask's dev server is threaded by default, but be explicit:
    # the speaker-busy contract depends on concurrent request handling).
    app.run(host="0.0.0.0", port=5001, threaded=True)
