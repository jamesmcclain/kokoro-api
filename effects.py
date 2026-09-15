"""
effects.py — Post-synthesis audio effects for the Kokoro REST API.

An *effect* is an ordered chain of *stages* (high-pass, compressor, reverb,
pitch shift, ...). A request names an effect in one of two ways:

  "effect": "audiobook"                         a preset, by name
  "effect": [{"type": "highpass", "cutoff_hz": 85},
             {"type": "reverb", "wet": 0.1}]    an inline chain

Presets are just inline chains with a name. Built-in presets live in
BUILTIN_PRESETS below; operators can add or override presets without
rebuilding the image by mounting a JSON file (see load_user_presets()).
Preset names are matched case-insensitively, and spaces or underscores
count as hyphens, so "Vintage radio", "vintage_radio" and "vintage-radio"
all name the same preset.

Every chain, preset or inline, goes through the same validator, so a preset
can never do anything an inline chain couldn't, and vice versa.

Streaming model
---------------
The server streams Kokoro output to the speaker one chunk (roughly one
sentence) at a time, and effects must not break that. Each request gets a
fresh EffectProcessor with its own stage instances; time-based stages
(reverb, phaser, flanger, delay, tremolo, ring, monotone, ...) keep their
internal state across chunks, so an LFO sweep or reverb tail carries over a
chunk boundary with no seam. When synthesis ends, flush() drains any
stage that buffers audio (monotone) and feeds silence through the chain to
let reverb/delay tails ring out, then trims the trailing near-silence.

Two stages are special:

* pitch — pedalboard's PitchShift does not work reliably when fed audio in
  pieces (it withholds or zeroes output until it has buffered a large
  amount). Kokoro's chunks are split at sentence boundaries, which are
  natural pauses, so pitch stages process each chunk independently. That is
  inaudible in practice and keeps time-to-first-audio unchanged.

* tempo — processes no audio at all. It scales the speed Kokoro synthesizes
  at (see EffectChain.effective_speed), which sounds far better than
  time-stretching finished audio and keeps duration estimates correct.
"""

import json
import math
import os
import re
import signal
import subprocess
import sys

import numpy as np

# pedalboard is imported lazily (see _pedalboard()) so that a CPU that
# can't run its native code still gets the numpy-only stages.

MAX_STAGES = 16

# Longest tail flush() will ever generate, regardless of settings. Keeps a
# delay with feedback near 1.0 from turning a short utterance into a long
# one. Trailing silence is trimmed afterward, so short tails cost nothing
# audible.
MAX_TAIL_SECONDS = 4.0

# Samples below this magnitude at the end of a flushed tail are trimmed.
_TAIL_SILENCE_THRESHOLD = 1e-4  # -80 dBFS

# A tail that is still audible when MAX_TAIL_SECONDS runs out fades over
# this long instead of stopping with a click.
_TAIL_FADE_SECONDS = 0.1

# Kokoro renders at 24 kHz, so Nyquist is 12 kHz. Filter cutoffs are capped
# a little below that.
_MAX_FILTER_HZ = 11500.0

# The server's own limits on Kokoro's speed parameter; tempo stages are
# clamped into this range after multiplying.
_MIN_SPEED = 0.1
_MAX_SPEED = 3.0


def _pedalboard():
    import pedalboard
    return pedalboard


# --- Stage schema ---------------------------------------------------------
#
# type -> {param: (default, min, max)}. Every parameter is optional; omitted
# parameters take the default. Unknown types and unknown parameters are
# rejected rather than ignored, so a typo surfaces as a 400 instead of a
# silently different sound.
STAGE_SPECS = {
    # pedalboard's high/low-pass filters are first order (6 dB/octave).
    "highpass": {
        "cutoff_hz": (80.0, 20.0, _MAX_FILTER_HZ),
    },
    "lowpass": {
        "cutoff_hz": (8000.0, 100.0, _MAX_FILTER_HZ),
    },
    # Band-pass: two cascaded high-pass and two cascaded low-pass filters
    # (12 dB/octave each side). low_hz must be below high_hz.
    "band": {
        "low_hz": (300.0, 20.0, _MAX_FILTER_HZ),
        "high_hz": (3400.0, 100.0, _MAX_FILTER_HZ),
    },
    "eq": {  # peaking (bell) filter, labeled "Mid"
        "frequency_hz": (1000.0, 20.0, _MAX_FILTER_HZ),
        "gain_db": (0.0, -24.0, 24.0),
        "q": (1.0, 0.1, 10.0),
    },
    "bass": {  # low shelf with a musical default corner
        "gain_db": (0.0, -24.0, 24.0),
        "frequency_hz": (150.0, 20.0, 1000.0),
    },
    "treble": {  # high shelf with a musical default corner
        "gain_db": (0.0, -24.0, 24.0),
        "frequency_hz": (4000.0, 1000.0, _MAX_FILTER_HZ),
    },
    "low_shelf": {
        "frequency_hz": (200.0, 20.0, _MAX_FILTER_HZ),
        "gain_db": (0.0, -24.0, 24.0),
        "q": (0.707, 0.1, 10.0),
    },
    "high_shelf": {
        "frequency_hz": (4000.0, 20.0, _MAX_FILTER_HZ),
        "gain_db": (0.0, -24.0, 24.0),
        "q": (0.707, 0.1, 10.0),
    },
    "compressor": {
        "threshold_db": (-20.0, -60.0, 0.0),
        "ratio": (4.0, 1.0, 20.0),
        "attack_ms": (5.0, 0.1, 100.0),
        "release_ms": (100.0, 1.0, 1000.0),
    },
    "gain": {
        "db": (0.0, -24.0, 24.0),
    },
    "volume": {  # linear multiplier, labeled "Vol 1.4×"
        "factor": (1.0, 0.0, 4.0),
    },
    "pitch": {
        "semitones": (0.0, -12.0, 12.0),
    },
    # Changes speaking rate without changing pitch, by scaling the speed
    # Kokoro synthesizes at. Multiplies with the request's "speed".
    "tempo": {
        "factor": (1.0, 0.25, 4.0),
    },
    # Flattens intonation to one fixed pitch (LPC vocoder resynthesis).
    "monotone": {
        "frequency_hz": (120.0, 50.0, 400.0),
    },
    "phaser": {
        "rate_hz": (0.5, 0.01, 20.0),
        "depth": (0.5, 0.0, 1.0),
        "center_hz": (1300.0, 50.0, 10000.0),
        "feedback": (0.0, -0.95, 0.95),
        "mix": (0.5, 0.0, 1.0),
    },
    "chorus": {
        "rate_hz": (1.0, 0.01, 20.0),
        "depth": (0.25, 0.0, 1.0),
        "center_delay_ms": (7.0, 1.0, 50.0),
        "feedback": (0.0, -0.95, 0.95),
        "mix": (0.5, 0.0, 1.0),
    },
    # A flanger is a chorus with a very short, swept delay and feedback.
    # Kept as its own type so presets and chips read naturally.
    "flanger": {
        "rate_hz": (0.3, 0.01, 20.0),
        "depth": (0.5, 0.0, 1.0),
        "center_delay_ms": (2.0, 0.5, 10.0),
        "feedback": (0.5, -0.95, 0.95),
        "mix": (0.5, 0.0, 1.0),
    },
    "tremolo": {  # amplitude LFO
        "rate_hz": (4.0, 0.1, 20.0),
        "depth": (0.5, 0.0, 1.0),
    },
    "ring": {  # ring modulator
        "frequency_hz": (60.0, 1.0, 2000.0),
        "mix": (1.0, 0.0, 1.0),
    },
    "reverb": {
        "room_size": (0.5, 0.0, 1.0),
        "damping": (0.5, 0.0, 1.0),
        "wet": (0.33, 0.0, 1.0),
        "dry": (0.8, 0.0, 1.0),
        "width": (1.0, 0.0, 1.0),
    },
    "delay": {  # labeled "Echo"
        "seconds": (0.25, 0.01, 2.0),
        "feedback": (0.3, 0.0, 0.9),
        "mix": (0.3, 0.0, 1.0),
    },
    # Bit depth reduction, optionally with sample-and-hold downsampling
    # ("Crush 7-bit 8×").
    "bitcrush": {
        "bits": (8.0, 1.0, 16.0),
        "downsample": (1.0, 1.0, 48.0),
    },
    # Sample-and-hold downsampling on its own.
    "downsample": {
        "factor": (4.0, 1.0, 48.0),
    },
    # Raw tanh drive with no level compensation: louder as drive rises.
    "distortion": {
        "drive_db": (20.0, 0.0, 60.0),
    },
    # Level-compensated tanh saturation: drive changes grit, not loudness.
    "overdrive": {
        "drive": (10.0, 0.0, 60.0),
    },
}

# Stage types whose output keeps sounding after the input stops. flush()
# only generates a tail when a chain contains at least one of these.
_TAIL_TYPES = frozenset({"reverb", "delay", "chorus", "flanger", "phaser"})


class EffectError(ValueError):
    """Raised for any invalid effect specification. The message is safe to
    return to an API caller verbatim."""


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def canonical_name(name):
    """Preset lookup key: lowercase, with runs of spaces/underscores turned
    into a hyphen ("Vintage radio" -> "vintage-radio")."""
    return re.sub(r"[\s_]+", "-", str(name).strip().lower())


def validate_stage(stage, where="stage"):
    """Returns a fully populated copy of one stage (all defaults filled in),
    or raises EffectError."""
    if not isinstance(stage, dict):
        raise EffectError(f"{where} must be an object with a 'type' field")
    stage_type = stage.get("type")
    spec = STAGE_SPECS.get(stage_type)
    if spec is None:
        raise EffectError(
            f"{where}: unknown type {stage_type!r}; valid types are "
            + ", ".join(sorted(STAGE_SPECS))
        )
    unknown = sorted(set(stage) - set(spec) - {"type"})
    if unknown:
        raise EffectError(
            f"{where} ({stage_type}): unknown parameter(s) {unknown}; valid "
            f"parameters are {sorted(spec)}"
        )
    result = {"type": stage_type}
    for name, (default, low, high) in spec.items():
        value = stage.get(name, default)
        if not _is_number(value) or not math.isfinite(value):
            raise EffectError(f"{where} ({stage_type}): '{name}' must be a number")
        if not (low <= value <= high):
            raise EffectError(
                f"{where} ({stage_type}): '{name}' must be between {low:g} and {high:g}"
            )
        result[name] = float(value)
    if stage_type == "band" and result["low_hz"] >= result["high_hz"]:
        raise EffectError(f"{where} (band): 'low_hz' must be below 'high_hz'")
    return result


def validate_chain(stages, where="effect"):
    """Returns a validated, fully populated list of stages, or raises
    EffectError."""
    if not isinstance(stages, list):
        raise EffectError(f"{where} must be a preset name or a list of stages")
    if len(stages) > MAX_STAGES:
        raise EffectError(f"{where} has {len(stages)} stages; the maximum is {MAX_STAGES}")
    return [validate_stage(s, f"{where}[{i}]") for i, s in enumerate(stages)]


# --- Human-readable stage labels ("chips") --------------------------------
#
# Worded like the Marmalade TTS app's chips where the two overlap.

def _signed(value):
    return f"+{value:g}" if value > 0 else f"{value:g}"


def stage_label(stage):
    t = stage["type"]
    if t == "highpass":
        return f"HP {stage['cutoff_hz']:g}Hz"
    if t == "lowpass":
        return f"LP {stage['cutoff_hz']:g}Hz"
    if t == "band":
        return f"Band {stage['low_hz']:g}-{stage['high_hz']:g}"
    if t == "eq":
        return f"Mid {stage['frequency_hz']:g}Hz {_signed(stage['gain_db'])}"
    if t == "bass":
        return f"Bass {_signed(stage['gain_db'])}"
    if t == "treble":
        return f"Treble {_signed(stage['gain_db'])}"
    if t == "low_shelf":
        return f"Low shelf {stage['frequency_hz']:g}Hz {_signed(stage['gain_db'])}dB"
    if t == "high_shelf":
        return f"High shelf {stage['frequency_hz']:g}Hz {_signed(stage['gain_db'])}dB"
    if t == "compressor":
        return f"Comp {stage['threshold_db']:g}dB {stage['ratio']:g}:1"
    if t == "gain":
        return f"Gain {_signed(stage['db'])}dB"
    if t == "volume":
        return f"Vol {stage['factor']:g}×"
    if t == "pitch":
        # Cents, like Marmalade ("Pitch +150" is 1.5 semitones).
        return f"Pitch {_signed(round(stage['semitones'] * 100))}"
    if t == "tempo":
        return f"Tempo {stage['factor']:g}×"
    if t == "monotone":
        return f"Monotone {stage['frequency_hz']:g}Hz"
    if t in ("phaser", "chorus", "flanger", "tremolo"):
        return f"{t.capitalize()} {stage['rate_hz']:.2f}Hz" if t != "tremolo" \
            else f"Tremolo {stage['rate_hz']:.1f}Hz"
    if t == "ring":
        return f"Ring {stage['frequency_hz']:g}Hz"
    if t == "reverb":
        return f"Reverb {stage['room_size'] * 100:.0f}"
    if t == "delay":
        return f"Echo {stage['seconds'] * 1000:.0f}ms"
    if t == "bitcrush":
        return f"Crush {stage['bits']:g}-bit {stage['downsample']:g}×"
    if t == "downsample":
        return f"Downsample {stage['factor']:g}×"
    if t == "distortion":
        return f"Drive {stage['drive_db']:g}dB"
    if t == "overdrive":
        return f"Overdrive {stage['drive']:g}"
    return t


# --- Presets --------------------------------------------------------------

def _reverb(amount):
    """Marmalade-style "Reverb N" (0-100): one knob that grows the room and
    the wet level together. The chip label shows N back."""
    n = amount / 100.0
    return {
        "type": "reverb",
        "room_size": n,
        "wet": round(0.05 + 0.45 * n, 3),
        "dry": round(1.0 - 0.3 * n, 3),
    }


def _pitch(cents):
    return {"type": "pitch", "semitones": cents / 100.0}


def _mid(hz, db):
    return {"type": "eq", "frequency_hz": hz, "gain_db": db}


_ECHO = {"type": "delay", "seconds": 0.3, "feedback": 0.35, "mix": 0.3}
_LONG_ECHO = {"type": "delay", "seconds": 0.45, "feedback": 0.4, "mix": 0.3}

# Everything from "8-bit" through "walkie-talkie" that appears in the
# Marmalade TTS app mirrors that app's built-in of the same name, stage for
# stage and in the same order. Marmalade doesn't publish every parameter
# (e.g. what "Echo" means), so those use sensible values here. "cathedral",
# "echo", "giant", "radio" and "robot" are extras specific to this server.
BUILTIN_PRESETS = {
    "8-bit": [
        {"type": "lowpass", "cutoff_hz": 3446},
        {"type": "bitcrush", "bits": 7, "downsample": 8},
    ],
    "ai": [
        _pitch(150),
        {"type": "phaser", "rate_hz": 0.4},
        {"type": "flanger", "rate_hz": 0.3},
        _reverb(30),
    ],
    "audiobook": [
        {"type": "highpass", "cutoff_hz": 85},
        {"type": "compressor", "threshold_db": -22, "ratio": 3},
        _mid(2500, 2),
        _reverb(10),
    ],
    "broadcaster": [
        {"type": "highpass", "cutoff_hz": 90},
        _mid(300, -3),
        {"type": "compressor", "threshold_db": -18, "ratio": 3},
        _mid(3000, 3),
        {"type": "treble", "gain_db": 3},
        {"type": "bass", "gain_db": 2},
    ],
    "cave": [
        _reverb(80),
        _ECHO,
    ],
    "chipmunk": [
        _pitch(900),
    ],
    "cyborg": [
        {"type": "ring", "frequency_hz": 60},
        {"type": "band", "low_hz": 300, "high_hz": 3400},
        {"type": "overdrive", "drive": 6},
    ],
    "deep": [
        _pitch(-400),
        {"type": "bass", "gain_db": 6},
    ],
    "dragon": [
        _reverb(45),
        _pitch(-649),
        _mid(1058, -2),
        {"type": "overdrive", "drive": 7},
        {"type": "chorus", "rate_hz": 0.25},
        {"type": "tempo", "factor": 0.85},
    ],
    "ethereal": [
        {"type": "highpass", "cutoff_hz": 250},
        _pitch(120),
        _reverb(70),
        {"type": "tremolo", "rate_hz": 3.0},
        {"type": "treble", "gain_db": 3},
    ],
    "glitch": [
        {"type": "bitcrush", "bits": 8, "downsample": 3},
        {"type": "ring", "frequency_hz": 120},
        {"type": "band", "low_hz": 400, "high_hz": 3000},
        {"type": "volume", "factor": 1.4},
    ],
    "intercom": [
        {"type": "band", "low_hz": 450, "high_hz": 2500},
        {"type": "overdrive", "drive": 18},
        _mid(1500, 4),
        _reverb(30),
        {"type": "volume", "factor": 0.9},
    ],
    "megaphone": [
        {"type": "band", "low_hz": 500, "high_hz": 4000},
        {"type": "overdrive", "drive": 30},
        {"type": "volume", "factor": 0.8},
    ],
    "monotone-female": [
        {"type": "monotone", "frequency_hz": 160},
    ],
    "monotone-male": [
        {"type": "monotone", "frequency_hz": 90},
    ],
    "podcast": [
        {"type": "highpass", "cutoff_hz": 80},
        {"type": "bass", "gain_db": 3},
        {"type": "compressor", "threshold_db": -20, "ratio": 3},
        _mid(250, -2),
        {"type": "treble", "gain_db": 2},
    ],
    "stadium": [
        _reverb(90),
        _LONG_ECHO,
    ],
    "telephone": [
        {"type": "band", "low_hz": 300, "high_hz": 3400},
        {"type": "overdrive", "drive": 5},
        {"type": "volume", "factor": 1.3},
    ],
    "trailer": [
        _pitch(-250),
        {"type": "bass", "gain_db": 5},
        {"type": "compressor", "threshold_db": -18, "ratio": 4},
        _mid(2500, 2),
        _reverb(22),
    ],
    "underwater": [
        {"type": "lowpass", "cutoff_hz": 700},
        {"type": "chorus", "rate_hz": 0.3},
        _pitch(-80),
        {"type": "tremolo", "rate_hz": 1.5},
        {"type": "volume", "factor": 1.4},
    ],
    "vintage-radio": [
        {"type": "highpass", "cutoff_hz": 400},
        {"type": "lowpass", "cutoff_hz": 4000},
        _mid(1000, 12),
        {"type": "overdrive", "drive": 8},
        {"type": "compressor", "threshold_db": -26, "ratio": 3},
        {"type": "tremolo", "rate_hz": 4.0},
        _reverb(8),
        {"type": "volume", "factor": 1.3},
    ],
    "walkie-talkie": [
        {"type": "highpass", "cutoff_hz": 400},
        {"type": "lowpass", "cutoff_hz": 5000},
        {"type": "overdrive", "drive": 25},
        {"type": "bitcrush", "bits": 11, "downsample": 1},
        {"type": "compressor", "threshold_db": -32, "ratio": 6},
        {"type": "volume", "factor": 1.5},
    ],
    # --- extras, not in Marmalade ---
    "cathedral": [
        {"type": "highpass", "cutoff_hz": 100},
        {"type": "reverb", "room_size": 0.95, "damping": 0.3, "wet": 0.45, "dry": 0.7},
    ],
    "echo": [
        {"type": "delay", "seconds": 0.3, "feedback": 0.35, "mix": 0.35},
    ],
    "giant": [
        {"type": "pitch", "semitones": -5},
        {"type": "low_shelf", "frequency_hz": 150, "gain_db": 4},
        {"type": "reverb", "room_size": 0.4, "wet": 0.15, "dry": 0.9},
    ],
    "radio": [
        {"type": "highpass", "cutoff_hz": 400},
        {"type": "lowpass", "cutoff_hz": 4000},
        {"type": "compressor", "threshold_db": -24, "ratio": 6},
        {"type": "distortion", "drive_db": 8},
        {"type": "gain", "db": -4},
    ],
    "robot": [
        {"type": "flanger", "rate_hz": 0.05, "depth": 0.1,
         "center_delay_ms": 4, "feedback": 0.8, "mix": 0.6},
        {"type": "bitcrush", "bits": 10},
        {"type": "compressor", "threshold_db": -18, "ratio": 4},
    ],
}


def load_user_presets(path):
    """Loads operator-defined presets from a JSON file shaped like
    {"name": [stage, ...], ...}. Returns {} if the file doesn't exist.
    Raises EffectError on anything malformed: a bad preset file is a
    deployment mistake and should stop the server from starting, not be
    discovered one 400 at a time."""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise EffectError(f"cannot read presets file {path}: {e}") from e
    if not isinstance(raw, dict):
        raise EffectError(f"presets file {path} must contain a JSON object")
    presets = {}
    for name, stages in raw.items():
        key = canonical_name(name)
        if not key:
            raise EffectError(f"presets file {path} contains an empty preset name")
        presets[key] = validate_chain(stages, where=f"preset '{name}'")
    return presets


class EffectRegistry:
    """Holds the validated presets. User presets override built-ins with
    the same name (compared via canonical_name).

    unsupported: stage types that don't run on this machine (see
    probe_stage_types()). Chains that use them are refused with a clear
    message, and /effects marks the affected presets unavailable."""

    def __init__(self, user_presets_path=None, unsupported=()):
        self.unsupported = frozenset(unsupported)
        self._presets = {}
        self._sources = {}
        for name, stages in BUILTIN_PRESETS.items():
            self._presets[name] = validate_chain(stages, where=f"preset '{name}'")
            self._sources[name] = "built-in"
        for name, stages in load_user_presets(user_presets_path).items():
            self._presets[name] = stages
            self._sources[name] = "user"

    def names(self):
        return sorted(self._presets)

    def _blocked(self, stages):
        return sorted({s["type"] for s in stages} & self.unsupported)

    def _require_supported(self, stages, what):
        blocked = self._blocked(stages)
        if blocked:
            raise EffectError(
                f"{what} uses stage type(s) {blocked}, which crash on this "
                "server's CPU and are disabled; choose another effect"
            )

    def resolve(self, effect):
        """Turns a request's 'effect' value into an EffectChain, or None for
        no effect. Raises EffectError for anything invalid."""
        if effect is None:
            return None
        if isinstance(effect, str):
            key = canonical_name(effect)
            if key in ("", "none"):
                return None
            stages = self._presets.get(key)
            if stages is None:
                raise EffectError(f"unknown effect preset '{effect}'")
            self._require_supported(stages, f"effect preset '{key}'")
            return EffectChain(stages, name=key)
        stages = validate_chain(effect)
        self._require_supported(stages, "effect")
        return EffectChain(stages, name=None) if stages else None

    def available_names(self):
        return [n for n in self.names() if not self._blocked(self._presets[n])]

    def describe(self):
        """JSON-ready description for GET /effects."""
        return {
            "presets": [
                {
                    "name": name,
                    "source": self._sources[name],
                    "summary": [stage_label(s) for s in self._presets[name]],
                    "stages": self._presets[name],
                    "available": not self._blocked(self._presets[name]),
                    "unavailable_stages": self._blocked(self._presets[name]),
                }
                for name in self.names()
            ],
            "unsupported_stage_types": sorted(self.unsupported),
            "stage_types": {
                t: {
                    p: {"default": d, "min": lo, "max": hi}
                    for p, (d, lo, hi) in spec.items()
                }
                for t, spec in STAGE_SPECS.items()
            },
            "max_stages": MAX_STAGES,
        }


class EffectChain:
    """An immutable, validated chain. Cheap to share between threads; each
    use calls new_processor() to get its own stateful stage instances."""

    def __init__(self, stages, name=None):
        self.stages = stages
        self.name = name

    def effective_speed(self, speed):
        """The speed Kokoro should synthesize at: the request's speed times
        every tempo stage's factor, clamped to the server's speed range."""
        for s in self.stages:
            if s["type"] == "tempo":
                speed *= s["factor"]
        return min(max(speed, _MIN_SPEED), _MAX_SPEED)

    @property
    def tail_seconds(self):
        """Upper bound on how long the chain rings after input stops. Used
        both for flush() length and for duration estimates."""
        tail = 0.0
        for s in self.stages:
            t = s["type"]
            if t == "reverb":
                tail = max(tail, 0.5 + 2.5 * s["room_size"])
            elif t == "delay":
                fb = s["feedback"]
                # Echoes until they decay ~60 dB.
                repeats = 1 if fb <= 0 else math.ceil(math.log(1e-3) / math.log(fb))
                tail = max(tail, s["seconds"] * (repeats + 1))
            elif t in _TAIL_TYPES:
                tail = max(tail, 0.1)
        return min(tail, MAX_TAIL_SECONDS)

    def new_processor(self, sample_rate):
        return EffectProcessor(self, sample_rate)


# --- Stage implementations ------------------------------------------------
#
# Every stage has process(x) -> y and flush() -> y, on 1-D float32 arrays.
# process() may return a different number of samples than it was given
# (only the monotone stage does, because it buffers); flush() returns
# whatever a stage is still holding.

_EMPTY = np.zeros(0, dtype=np.float32)


# Stage types implemented in numpy alone; everything else uses pedalboard.
_NUMPY_STAGE_TYPES = frozenset({
    "volume", "tempo", "monotone", "tremolo", "ring", "overdrive",
})


class _Stage:
    def process(self, x):
        raise NotImplementedError

    def flush(self):
        return _EMPTY


class _PedalboardStage(_Stage):
    """Wraps a pedalboard plugin (or Pedalboard) for streaming.

    pedalboard silently clears a plugin's internal state (reverb tail,
    delay line, LFO phase, filter memory) whenever a call hands it more
    samples than any earlier call did. Kokoro's chunks vary in length, and
    the flush tail is longer still, so feeding them straight through would
    randomly cut tails and put clicks at chunk boundaries. Instead, audio
    always goes in blocks of at most BLOCK samples, and the plugin is primed
    with one full block of silence up front so that size is established
    before any real audio arrives.

    Independent stages (pitch) are the exception: they are reset on purpose
    for every chunk and need the whole chunk at once.
    """

    BLOCK = 2048

    def __init__(self, plugin, sample_rate, independent=False):
        self._plugin = plugin
        self._sample_rate = sample_rate
        self._independent = independent
        if not independent:
            plugin(np.zeros((1, self.BLOCK), dtype=np.float32), sample_rate, reset=False)

    def process(self, x):
        if self._independent:
            return self._plugin(x.reshape(1, -1), self._sample_rate, reset=True)[0]
        out = [
            self._plugin(
                x[i:i + self.BLOCK].reshape(1, -1), self._sample_rate, reset=False
            )[0]
            for i in range(0, len(x), self.BLOCK)
        ]
        return np.concatenate(out) if len(out) > 1 else out[0]


class _IdentityStage(_Stage):
    def process(self, x):
        return x


class _VolumeStage(_Stage):
    def __init__(self, factor):
        self._factor = np.float32(factor)

    def process(self, x):
        return x * self._factor


class _OverdriveStage(_Stage):
    """tanh saturation, normalized so a typical speech peak (0.5) comes out
    at the same level whatever the drive: more drive means more grit and
    more compression, not simply more volume."""

    _REFERENCE = 0.5

    def __init__(self, drive):
        self._gain = 10 ** (drive / 20.0)
        self._norm = self._REFERENCE / math.tanh(self._gain * self._REFERENCE)

    def process(self, x):
        return (np.tanh(self._gain * x) * self._norm).astype(np.float32)


class _OscillatorStage(_Stage):
    """Base for stages driven by a sine LFO/carrier whose phase must stay
    continuous across chunks."""

    def __init__(self, frequency_hz, sample_rate):
        self._step = 2 * math.pi * frequency_hz / sample_rate
        self._phase = 0.0

    def _sine(self, n):
        phases = self._phase + self._step * np.arange(n)
        self._phase = (self._phase + self._step * n) % (2 * math.pi)
        return np.sin(phases)


class _TremoloStage(_OscillatorStage):
    def __init__(self, rate_hz, depth, sample_rate):
        super().__init__(rate_hz, sample_rate)
        self._depth = depth

    def process(self, x):
        # Gain swings between 1 and (1 - depth).
        lfo = 0.5 + 0.5 * self._sine(len(x))
        return (x * (1.0 - self._depth * lfo)).astype(np.float32)


class _RingStage(_OscillatorStage):
    def __init__(self, frequency_hz, mix, sample_rate):
        super().__init__(frequency_hz, sample_rate)
        self._mix = mix

    def process(self, x):
        carrier = self._sine(len(x))
        return (x * ((1.0 - self._mix) + self._mix * carrier)).astype(np.float32)


class _MonotoneStage(_Stage):
    """Resynthesizes speech at one fixed pitch with an LPC vocoder.

    For each 40 ms frame (10 ms hop): estimate the vocal-tract envelope
    (24th-order LPC on the pre-emphasized frame) and how voiced the frame
    is (normalized autocorrelation peak in the 60-400 Hz range). Then drive
    the envelope filter with a mix of a pulse train at the target pitch
    (voiced part) and white noise (unvoiced part: s, f, t ...), match the
    frame's loudness, and overlap-add. The pulse train is laid on one
    global grid, so pitch periods line up across frames and chunks.

    Pure numpy, on purpose: scipy's bundled math library crashes with
    "Illegal instruction" on some arm64 CPUs and VMs. The LPC solve is a
    Levinson-Durbin recursion, and the all-pole synthesis filter (with the
    de-emphasis filter folded in) is applied in the frequency domain, one
    FFT per frame, instead of sample by sample. The FFT buffer is long
    enough for the filter's ringing to die out before it wraps around, and
    the wrapped part lands in a warm-up region that is thrown away.

    The overlap-add needs FRAME - HOP samples of look-ahead, so this stage
    delays audio by 30 ms and holds that much back until flush().
    """

    FRAME = 960
    HOP = 240
    ORDER = 24
    WARMUP = 480
    FFT_SIZE = 4096
    PRE_EMPHASIS = 0.97

    def __init__(self, frequency_hz, sample_rate):
        self._sr = sample_rate
        self._period = sample_rate / frequency_hz
        self._window = np.hanning(self.FRAME + 1)[:-1]  # periodic Hann
        # Periodic Hann at 75% overlap sums to a constant; undo it.
        self._ola_scale = self.HOP / self._window.sum()
        self._min_lag = int(sample_rate / 400)
        self._max_lag = int(sample_rate / 60)
        self._lag_bias = self.FRAME / (self.FRAME - np.arange(self._max_lag + 1))
        self._rng = np.random.default_rng(0)
        # z^-k on the FFT grid, for evaluating A(z) and the de-emphasis
        # filter's denominator at every bin.
        omega = 2 * np.pi * np.arange(self.FFT_SIZE // 2 + 1) / self.FFT_SIZE
        self._zinv = np.exp(-1j * np.outer(np.arange(self.ORDER + 1), omega))
        self._deemphasis = 1.0 - self.PRE_EMPHASIS * self._zinv[1]
        self._pending = []

        latency = self.FRAME - self.HOP
        # Stream coordinates include `latency` leading zeros, so the first
        # real sample is already fully covered by overlapping frames.
        self._in_buf = np.zeros(latency)
        self._in_off = 0
        self._acc = np.zeros(0)
        self._pos = 0  # start of the next frame, stream coordinates
        self._to_skip = latency
        self._real_in = 0
        self._emitted = 0

    @staticmethod
    def _levinson(r, order):
        """LPC coefficients a[0..order] (a[0] = 1) from autocorrelation r,
        or None if the recursion becomes unstable."""
        a = np.zeros(order + 1)
        a[0] = 1.0
        err = r[0]
        for i in range(1, order + 1):
            k = -np.dot(a[:i], r[i:0:-1]) / err
            a[1:i + 1] = a[1:i + 1] + k * a[i - 1::-1]
            err *= 1.0 - k * k
            if err <= 0 or abs(k) >= 1.0:
                return None
        return a

    def _synth_frame(self, frame, pre, start):
        n = self.FRAME
        w = self._window
        xw = frame * w
        energy = float(np.dot(xw, xw))
        if energy < n * 1e-8:  # about -80 dBFS: treat as silence
            return None

        # Voicing: biased autocorrelation, bias-corrected, normalized.
        spec = np.fft.rfft(xw, 2 * n)
        r = np.fft.irfft(np.abs(spec) ** 2)[: self._max_lag + 1] * self._lag_bias
        peak = r[self._min_lag:].max() / r[0] if r[0] > 0 else 0.0
        voicing = float(np.clip((peak - 0.3) / 0.4, 0.0, 1.0))

        # LPC envelope from the pre-emphasized frame.
        spec = np.fft.rfft(pre * w, 2 * n)
        rp = np.fft.irfft(np.abs(spec) ** 2)[: self.ORDER + 1]
        if rp[0] <= 0:
            return None
        rp[0] *= 1.0001  # white-noise correction for numerical stability
        a = self._levinson(rp, self.ORDER)
        if a is None:
            return None

        # Excitation over [start - WARMUP, start + FRAME).
        total = self.WARMUP + n
        begin = start - self.WARMUP
        k0 = math.ceil(begin / self._period)
        k1 = math.floor((start + n - 1) / self._period)
        pulses = np.zeros(total)
        if k1 >= k0:
            idx = np.round(np.arange(k0, k1 + 1) * self._period).astype(int) - begin
            idx = idx[(idx >= 0) & (idx < total)]
            pulses[idx] = math.sqrt(self._period)
        noise = self._rng.standard_normal(total)
        excitation = math.sqrt(voicing) * pulses + math.sqrt(1 - voicing) * noise

        # Synthesis filter 1 / (A(z) * (1 - 0.97 z^-1)), applied per bin.
        response = 1.0 / ((a @ self._zinv) * self._deemphasis)
        y = np.fft.irfft(np.fft.rfft(excitation, self.FFT_SIZE) * response, self.FFT_SIZE)
        y = y[self.WARMUP:total] * w

        y_energy = float(np.dot(y, y))
        if not math.isfinite(y_energy) or y_energy <= 0:
            return None
        return y * math.sqrt(energy / y_energy)

    def _run_frames(self):
        n, hop = self.FRAME, self.HOP
        end = self._in_off + len(self._in_buf)
        while self._pos + n <= end:
            i = self._pos - self._in_off
            frame = self._in_buf[i:i + n]
            pre = np.empty(n)
            pre[0] = frame[0] - self.PRE_EMPHASIS * (self._in_buf[i - 1] if i > 0 else 0.0)
            pre[1:] = frame[1:] - self.PRE_EMPHASIS * frame[:-1]
            y = self._synth_frame(frame, pre, self._pos)
            # _acc always starts at stream index self._pos.
            if len(self._acc) < n:
                self._acc = np.concatenate([self._acc, np.zeros(n - len(self._acc))])
            if y is not None:
                self._acc[:n] += y * self._ola_scale
            # Samples before pos + hop are final: no later frame reaches them.
            self._pending.append(self._acc[:hop])
            self._acc = self._acc[hop:]
            self._pos += hop
        drop = self._pos - self._in_off
        if drop > 0:
            self._in_buf = self._in_buf[drop:]
            self._in_off = self._pos

    def _collect(self, limit=None):
        out = np.concatenate(self._pending) if self._pending else np.zeros(0)
        self._pending = []
        if self._to_skip:
            skip = min(self._to_skip, len(out))
            out = out[skip:]
            self._to_skip -= skip
        if limit is not None:
            out = out[: max(limit - self._emitted, 0)]
        self._emitted += len(out)
        if len(out) == 0:
            return _EMPTY
        return out.astype(np.float32)

    def process(self, x):
        self._real_in += len(x)
        self._in_buf = np.concatenate([self._in_buf, np.asarray(x, dtype=np.float64)])
        self._run_frames()
        return self._collect()

    def flush(self):
        # Pad with silence so every real sample gets its full overlap.
        self._in_buf = np.concatenate([self._in_buf, np.zeros(self.FRAME)])
        self._run_frames()
        return self._collect(limit=self._real_in)


def _pb_chain(plugins):
    return plugins[0] if len(plugins) == 1 else _pedalboard().Pedalboard(plugins)


def _build_stage(stage, sample_rate):
    t = stage["type"]
    if t in _NUMPY_STAGE_TYPES:
        pb = None
    else:
        pb = _pedalboard()
    pedal = lambda plugin, **kw: _PedalboardStage(plugin, sample_rate, **kw)
    if t == "highpass":
        return pedal(pb.HighpassFilter(cutoff_frequency_hz=stage["cutoff_hz"]))
    if t == "lowpass":
        return pedal(pb.LowpassFilter(cutoff_frequency_hz=stage["cutoff_hz"]))
    if t == "band":
        lo, hi = stage["low_hz"], stage["high_hz"]
        return pedal(pb.Pedalboard([
            pb.HighpassFilter(cutoff_frequency_hz=lo),
            pb.HighpassFilter(cutoff_frequency_hz=lo),
            pb.LowpassFilter(cutoff_frequency_hz=hi),
            pb.LowpassFilter(cutoff_frequency_hz=hi),
        ]))
    if t == "eq":
        return pedal(pb.PeakFilter(stage["frequency_hz"], stage["gain_db"], stage["q"]))
    if t == "bass":
        return pedal(pb.LowShelfFilter(stage["frequency_hz"], stage["gain_db"], 0.707))
    if t == "treble":
        return pedal(pb.HighShelfFilter(stage["frequency_hz"], stage["gain_db"], 0.707))
    if t == "low_shelf":
        return pedal(pb.LowShelfFilter(stage["frequency_hz"], stage["gain_db"], stage["q"]))
    if t == "high_shelf":
        return pedal(pb.HighShelfFilter(stage["frequency_hz"], stage["gain_db"], stage["q"]))
    if t == "compressor":
        return pedal(pb.Compressor(
            threshold_db=stage["threshold_db"],
            ratio=stage["ratio"],
            attack_ms=stage["attack_ms"],
            release_ms=stage["release_ms"],
        ))
    if t == "gain":
        return pedal(pb.Gain(gain_db=stage["db"]))
    if t == "volume":
        return _VolumeStage(stage["factor"])
    if t == "pitch":
        # Each chunk independently; see module docstring.
        return pedal(pb.PitchShift(semitones=stage["semitones"]), independent=True)
    if t == "tempo":
        # Applied to Kokoro's synthesis speed; see EffectChain.effective_speed.
        return _IdentityStage()
    if t == "monotone":
        return _MonotoneStage(stage["frequency_hz"], sample_rate)
    if t == "phaser":
        # Positional: (rate_hz, depth, center frequency, feedback, mix).
        return pedal(pb.Phaser(
            stage["rate_hz"], stage["depth"], stage["center_hz"],
            stage["feedback"], stage["mix"],
        ))
    if t in ("chorus", "flanger"):
        # Positional: (rate_hz, depth, center delay ms, feedback, mix).
        return pedal(pb.Chorus(
            stage["rate_hz"], stage["depth"], stage["center_delay_ms"],
            stage["feedback"], stage["mix"],
        ))
    if t == "tremolo":
        return _TremoloStage(stage["rate_hz"], stage["depth"], sample_rate)
    if t == "ring":
        return _RingStage(stage["frequency_hz"], stage["mix"], sample_rate)
    if t == "reverb":
        return pedal(pb.Reverb(
            room_size=stage["room_size"],
            damping=stage["damping"],
            wet_level=stage["wet"],
            dry_level=stage["dry"],
            width=stage["width"],
        ))
    if t == "delay":
        return pedal(pb.Delay(
            delay_seconds=stage["seconds"],
            feedback=stage["feedback"],
            mix=stage["mix"],
        ))
    if t == "bitcrush":
        plugins = []
        if stage["downsample"] > 1:
            plugins.append(pb.Resample(
                target_sample_rate=sample_rate / stage["downsample"],
                quality=pb.Resample.Quality.ZeroOrderHold,
            ))
        plugins.append(pb.Bitcrush(bit_depth=stage["bits"]))
        return pedal(_pb_chain(plugins))
    if t == "downsample":
        return pedal(pb.Resample(
            target_sample_rate=sample_rate / stage["factor"],
            quality=pb.Resample.Quality.ZeroOrderHold,
        ))
    if t == "distortion":
        return pedal(pb.Distortion(drive_db=stage["drive_db"]))
    if t == "overdrive":
        return _OverdriveStage(stage["drive"])
    raise EffectError(f"unhandled stage type {t!r}")  # pragma: no cover


class EffectProcessor:
    """Stateful, single-use, single-thread. Feed chunks through process()
    in order, then call flush() exactly once."""

    def __init__(self, chain, sample_rate):
        self._chain = chain
        self._sample_rate = sample_rate
        self._stages = [_build_stage(s, sample_rate) for s in chain.stages]

    def _run(self, audio, first=0):
        x = np.asarray(audio, dtype=np.float32).reshape(-1)
        for stage in self._stages[first:]:
            if x.size == 0:
                break
            x = np.asarray(stage.process(x), dtype=np.float32).reshape(-1)
        return x

    @staticmethod
    def _finish(x):
        # Final safety: effects with gain (EQ boosts, reverb, drive) can
        # push past full scale, and paplay would wrap rather than clip.
        return np.clip(x, -1.0, 1.0).astype(np.float32, copy=False)

    def process(self, audio):
        return self._finish(self._run(audio))

    def flush(self):
        parts = []
        # 1. Let reverb/delay tails ring out.
        tail_seconds = self._chain.tail_seconds
        if tail_seconds > 0:
            silence = np.zeros(int(tail_seconds * self._sample_rate), dtype=np.float32)
            parts.append(self._run(silence))
        # 2. Drain buffering stages in order, once each: whatever stage i
        #    still holds goes through stages i+1..end before stage i+1
        #    drains in turn.
        for i, stage in enumerate(self._stages):
            held = stage.flush()
            if len(held):
                parts.append(self._run(held, first=i + 1))
        if not parts:
            return _EMPTY
        out = self._finish(np.concatenate(parts))
        if tail_seconds > 0:
            audible = np.flatnonzero(np.abs(out) > _TAIL_SILENCE_THRESHOLD)
            out = out[: audible[-1] + 1] if audible.size else _EMPTY
            fade = min(len(out), int(_TAIL_FADE_SECONDS * self._sample_rate))
            if fade:
                out = out.copy()
                out[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
        return out


# --- CPU support probe ----------------------------------------------------
#
# pedalboard ships prebuilt native code. On some CPUs part of that code
# uses instructions the processor lacks, and the whole Python process dies
# with SIGILL ("Illegal instruction") the first time the code runs; no
# exception is raised, so it can't be caught in-process. The probe runs
# every stage type in a child process instead: the child reports each
# type it finishes, and when it dies, the type it was working on is marked
# unsupported and a fresh child continues with the rest.

# A representative configuration per stage type. bitcrush is probed with
# downsampling on, so the resampler is exercised too.
_PROBE_OVERRIDES = {"bitcrush": {"downsample": 8}}

# Test hook: comma-separated stage types the probe child kills itself on,
# as if they had crashed. Lets the fallback path be tested anywhere.
_SIMULATE_CRASH_ENV = "KOKORO_EFFECTS_SIMULATE_CRASH"


def _probe_worker(stage_types):
    simulated = set(filter(None, os.environ.get(_SIMULATE_CRASH_ENV, "").split(",")))
    sample_rate = 24000
    tone = (0.3 * np.sin(np.arange(6000) * 0.06)).astype(np.float32)
    for stage_type in stage_types:
        print(f"START {stage_type}", flush=True)
        if stage_type in simulated:
            os.kill(os.getpid(), signal.SIGILL)
        stage = validate_stage({"type": stage_type, **_PROBE_OVERRIDES.get(stage_type, {})})
        processor = EffectChain([stage]).new_processor(sample_rate)
        processor.process(tone)
        processor.process(tone[:1000])
        processor.flush()
        print(f"OK {stage_type}", flush=True)


def probe_stage_types(timeout=120):
    """Returns (unsupported_types, details). details maps each unsupported
    type to how its child process ended."""
    remaining = list(STAGE_SPECS)
    unsupported = {}
    while remaining:
        try:
            proc = subprocess.run(
                [sys.executable, os.path.abspath(__file__), "--probe-worker", *remaining],
                capture_output=True, text=True, timeout=timeout,
            )
            output, code = proc.stdout, proc.returncode
        except subprocess.TimeoutExpired as e:
            output = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            code = "timeout"
        done = {line[3:] for line in output.splitlines() if line.startswith("OK ")}
        started = [line[6:] for line in output.splitlines() if line.startswith("START ")]
        remaining = [t for t in remaining if t not in done]
        if code == 0 and not remaining:
            break
        # The child died (or hung) on the last type it started. If it died
        # before starting any type, blame the first remaining one.
        culprit = started[-1] if started and started[-1] in remaining else remaining[0]
        if isinstance(code, int) and code < 0:
            try:
                how = signal.Signals(-code).name
            except ValueError:
                how = f"signal {-code}"
        else:
            how = f"exit {code}"
        unsupported[culprit] = how
        remaining.remove(culprit)
    return set(unsupported), unsupported


def cpu_report():
    """One line naming the CPU and which common SIMD extensions it has."""
    model, flags = "unknown", set()
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                key, _, value = line.partition(":")
                key = key.strip()
                if key in ("model name", "Model", "CPU part") and model == "unknown":
                    model = value.strip()
                elif key in ("flags", "Features") and not flags:
                    flags = set(value.split())
    except OSError:
        pass
    interesting = ["sse4_2", "avx", "avx2", "fma", "bmi2", "avx512f",
                   "asimd", "sve", "sve2", "sme"]
    have = [f for f in interesting if f in flags]
    missing = [f for f in interesting[:6] if flags and f not in flags]
    import platform
    return (f"cpu: {platform.machine()} {model}; has {' '.join(have) or 'none'}"
            + (f"; lacks {' '.join(missing)}" if missing else ""))


def self_test():
    """Printed during the image build. Always exits 0 unless effects.py
    itself is broken: an unsupported stage disables effects that use it,
    it doesn't stop the build."""
    registry = EffectRegistry()
    print(cpu_report())
    try:
        version = _pedalboard().__version__
    except Exception as e:  # noqa: BLE001 - report, don't fail
        version = f"import failed: {e}"
    print(f"pedalboard: {version}")
    unsupported, details = probe_stage_types()
    if not unsupported:
        print(f"effects self-test: all {len(STAGE_SPECS)} stage types work; "
              f"all {len(registry.names())} presets available")
        return
    registry = EffectRegistry(unsupported=unsupported)
    print("effects self-test: these stage types crash on this CPU and will be disabled:")
    for stage_type in sorted(details):
        print(f"  {stage_type}: {details[stage_type]}")
    available = registry.available_names()
    print(f"presets still available ({len(available)}/{len(registry.names())}): "
          + (", ".join(available) or "none"))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--probe-worker":
        _probe_worker(sys.argv[2:])
    elif sys.argv[1:] == ["--self-test"]:
        self_test()
    else:
        sys.exit("usage: effects.py --self-test")
