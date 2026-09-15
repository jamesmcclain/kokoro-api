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

Every chain, preset or inline, goes through the same validator, so a preset
can never do anything an inline chain couldn't, and vice versa.

Streaming model
---------------
The server streams Kokoro output to the speaker one chunk (roughly one
sentence) at a time, and effects must not break that. Each request gets a
fresh EffectProcessor with its own plugin instances; time-based stages
(reverb, phaser, flanger, delay, ...) keep their internal state across
chunks, so an LFO sweep or reverb tail carries over a chunk boundary with no
seam. When synthesis ends, flush() feeds silence through the chain to let
reverb/delay tails ring out, then trims the trailing near-silence.

Pitch shift is the one exception: pedalboard's PitchShift does not work
reliably when fed audio in pieces (it withholds or zeroes output until it
has buffered a large amount). Kokoro's chunks are split at sentence
boundaries, which are natural pauses, so pitch stages process each chunk
independently. That is inaudible in practice and keeps time-to-first-audio
unchanged.
"""

import json
import math
import os

import numpy as np
import pedalboard as pb

MAX_STAGES = 16

# Longest tail flush() will ever generate, regardless of settings. Keeps a
# delay with feedback near 1.0 from turning a short utterance into a long
# one. Trailing silence is trimmed afterward, so short tails cost nothing
# audible.
MAX_TAIL_SECONDS = 4.0

# Samples below this magnitude at the end of a flushed tail are trimmed.
_TAIL_SILENCE_THRESHOLD = 1e-4  # -80 dBFS

# Kokoro renders at 24 kHz, so Nyquist is 12 kHz. Filter cutoffs are capped
# a little below that.
_MAX_FILTER_HZ = 11500.0


# --- Stage schema ---------------------------------------------------------
#
# type -> {param: (default, min, max)}. Every parameter is optional; omitted
# parameters take the default. Unknown types and unknown parameters are
# rejected rather than ignored, so a typo surfaces as a 400 instead of a
# silently different sound.
STAGE_SPECS = {
    "highpass": {
        "cutoff_hz": (80.0, 20.0, _MAX_FILTER_HZ),
    },
    "lowpass": {
        "cutoff_hz": (8000.0, 100.0, _MAX_FILTER_HZ),
    },
    "eq": {  # peaking (bell) filter
        "frequency_hz": (1000.0, 20.0, _MAX_FILTER_HZ),
        "gain_db": (0.0, -24.0, 24.0),
        "q": (0.707, 0.1, 10.0),
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
    "pitch": {
        "semitones": (0.0, -12.0, 12.0),
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
    "reverb": {
        "room_size": (0.5, 0.0, 1.0),
        "damping": (0.5, 0.0, 1.0),
        "wet": (0.33, 0.0, 1.0),
        "dry": (0.8, 0.0, 1.0),
        "width": (1.0, 0.0, 1.0),
    },
    "delay": {
        "seconds": (0.25, 0.01, 2.0),
        "feedback": (0.3, 0.0, 0.9),
        "mix": (0.3, 0.0, 1.0),
    },
    "bitcrush": {
        "bits": (8.0, 1.0, 16.0),
    },
    # Sample-and-hold downsampling (the "8x" half of a classic bit crusher).
    "downsample": {
        "factor": (4.0, 1.0, 48.0),
    },
    "distortion": {
        "drive_db": (20.0, 0.0, 60.0),
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

def _signed(value):
    return f"+{value:g}" if value > 0 else f"{value:g}"


def stage_label(stage):
    t = stage["type"]
    if t == "highpass":
        return f"HP {stage['cutoff_hz']:g}Hz"
    if t == "lowpass":
        return f"LP {stage['cutoff_hz']:g}Hz"
    if t == "eq":
        return f"EQ {stage['frequency_hz']:g}Hz {_signed(stage['gain_db'])}dB"
    if t == "low_shelf":
        return f"Low shelf {stage['frequency_hz']:g}Hz {_signed(stage['gain_db'])}dB"
    if t == "high_shelf":
        return f"High shelf {stage['frequency_hz']:g}Hz {_signed(stage['gain_db'])}dB"
    if t == "compressor":
        return f"Comp {stage['threshold_db']:g}dB {stage['ratio']:g}:1"
    if t == "gain":
        return f"Gain {_signed(stage['db'])}dB"
    if t == "pitch":
        return f"Pitch {_signed(stage['semitones'])} st"
    if t in ("phaser", "chorus", "flanger"):
        return f"{t.capitalize()} {stage['rate_hz']:.2f}Hz"
    if t == "reverb":
        return f"Reverb {stage['wet'] * 100:.0f}%"
    if t == "delay":
        return f"Delay {stage['seconds'] * 1000:.0f}ms"
    if t == "bitcrush":
        return f"Crush {stage['bits']:g}-bit"
    if t == "downsample":
        return f"Downsample {stage['factor']:g}x"
    if t == "distortion":
        return f"Drive {stage['drive_db']:g}dB"
    return t


# --- Presets --------------------------------------------------------------
#
# The first three mirror the built-ins shown in the Marmalade TTS app.
BUILTIN_PRESETS = {
    "8-bit": [
        {"type": "lowpass", "cutoff_hz": 3446},
        {"type": "bitcrush", "bits": 7},
        {"type": "downsample", "factor": 8},
    ],
    "ai": [
        {"type": "pitch", "semitones": 1.5},
        {"type": "phaser", "rate_hz": 0.4},
        {"type": "flanger", "rate_hz": 0.3},
        {"type": "reverb", "room_size": 0.3, "wet": 0.3},
    ],
    "audiobook": [
        {"type": "highpass", "cutoff_hz": 85},
        {"type": "compressor", "threshold_db": -22, "ratio": 3},
        {"type": "eq", "frequency_hz": 2500, "gain_db": 2, "q": 1.0},
        {"type": "reverb", "room_size": 0.15, "wet": 0.1, "dry": 1.0},
    ],
    "radio": [
        {"type": "highpass", "cutoff_hz": 400},
        {"type": "lowpass", "cutoff_hz": 4000},
        {"type": "compressor", "threshold_db": -24, "ratio": 6},
        {"type": "distortion", "drive_db": 8},
        {"type": "gain", "db": -4},
    ],
    "telephone": [
        {"type": "highpass", "cutoff_hz": 300},
        {"type": "lowpass", "cutoff_hz": 3400},
        {"type": "downsample", "factor": 3},
        {"type": "compressor", "threshold_db": -20, "ratio": 4},
    ],
    "megaphone": [
        {"type": "highpass", "cutoff_hz": 500},
        {"type": "lowpass", "cutoff_hz": 3000},
        {"type": "eq", "frequency_hz": 1500, "gain_db": 6, "q": 1.2},
        {"type": "distortion", "drive_db": 18},
        {"type": "gain", "db": -8},
    ],
    "robot": [
        {"type": "flanger", "rate_hz": 0.05, "depth": 0.1,
         "center_delay_ms": 4, "feedback": 0.8, "mix": 0.6},
        {"type": "bitcrush", "bits": 10},
        {"type": "compressor", "threshold_db": -18, "ratio": 4},
    ],
    "cathedral": [
        {"type": "highpass", "cutoff_hz": 100},
        {"type": "reverb", "room_size": 0.95, "damping": 0.3, "wet": 0.45, "dry": 0.7},
    ],
    "echo": [
        {"type": "delay", "seconds": 0.3, "feedback": 0.35, "mix": 0.35},
    ],
    "chipmunk": [
        {"type": "pitch", "semitones": 7},
        {"type": "highpass", "cutoff_hz": 150},
    ],
    "giant": [
        {"type": "pitch", "semitones": -5},
        {"type": "low_shelf", "frequency_hz": 150, "gain_db": 4},
        {"type": "reverb", "room_size": 0.4, "wet": 0.15, "dry": 0.9},
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
        key = str(name).strip().lower()
        if not key:
            raise EffectError(f"presets file {path} contains an empty preset name")
        presets[key] = validate_chain(stages, where=f"preset '{name}'")
    return presets


class EffectRegistry:
    """Holds the validated presets. User presets override built-ins with
    the same (case-insensitive) name."""

    def __init__(self, user_presets_path=None):
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

    def resolve(self, effect):
        """Turns a request's 'effect' value into an EffectChain, or None for
        no effect. Raises EffectError for anything invalid."""
        if effect is None:
            return None
        if isinstance(effect, str):
            key = effect.strip().lower()
            if key in ("", "none"):
                return None
            stages = self._presets.get(key)
            if stages is None:
                raise EffectError(f"unknown effect preset '{effect}'")
            return EffectChain(stages, name=key)
        stages = validate_chain(effect)
        return EffectChain(stages, name=None) if stages else None

    def describe(self):
        """JSON-ready description for GET /effects."""
        return {
            "presets": [
                {
                    "name": name,
                    "source": self._sources[name],
                    "summary": [stage_label(s) for s in self._presets[name]],
                    "stages": self._presets[name],
                }
                for name in self.names()
            ],
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
    use calls new_processor() to get its own stateful plugin instances."""

    def __init__(self, stages, name=None):
        self.stages = stages
        self.name = name

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


def _build_plugin(stage, sample_rate):
    t = stage["type"]
    if t == "highpass":
        return pb.HighpassFilter(cutoff_frequency_hz=stage["cutoff_hz"])
    if t == "lowpass":
        return pb.LowpassFilter(cutoff_frequency_hz=stage["cutoff_hz"])
    if t == "eq":
        return pb.PeakFilter(stage["frequency_hz"], stage["gain_db"], stage["q"])
    if t == "low_shelf":
        return pb.LowShelfFilter(stage["frequency_hz"], stage["gain_db"], stage["q"])
    if t == "high_shelf":
        return pb.HighShelfFilter(stage["frequency_hz"], stage["gain_db"], stage["q"])
    if t == "compressor":
        return pb.Compressor(
            threshold_db=stage["threshold_db"],
            ratio=stage["ratio"],
            attack_ms=stage["attack_ms"],
            release_ms=stage["release_ms"],
        )
    if t == "gain":
        return pb.Gain(gain_db=stage["db"])
    if t == "pitch":
        return pb.PitchShift(semitones=stage["semitones"])
    if t == "phaser":
        # Positional: (rate_hz, depth, center frequency, feedback, mix).
        return pb.Phaser(
            stage["rate_hz"], stage["depth"], stage["center_hz"],
            stage["feedback"], stage["mix"],
        )
    if t in ("chorus", "flanger"):
        # Positional: (rate_hz, depth, center delay ms, feedback, mix).
        return pb.Chorus(
            stage["rate_hz"], stage["depth"], stage["center_delay_ms"],
            stage["feedback"], stage["mix"],
        )
    if t == "reverb":
        return pb.Reverb(
            room_size=stage["room_size"],
            damping=stage["damping"],
            wet_level=stage["wet"],
            dry_level=stage["dry"],
            width=stage["width"],
        )
    if t == "delay":
        return pb.Delay(
            delay_seconds=stage["seconds"],
            feedback=stage["feedback"],
            mix=stage["mix"],
        )
    if t == "bitcrush":
        return pb.Bitcrush(bit_depth=stage["bits"])
    if t == "downsample":
        return pb.Resample(
            target_sample_rate=sample_rate / stage["factor"],
            quality=pb.Resample.Quality.ZeroOrderHold,
        )
    if t == "distortion":
        return pb.Distortion(drive_db=stage["drive_db"])
    raise EffectError(f"unhandled stage type {t!r}")  # pragma: no cover


class EffectProcessor:
    """Stateful, single-use, single-thread. Feed chunks through process()
    in order, then call flush() exactly once."""

    def __init__(self, chain, sample_rate):
        self._chain = chain
        self._sample_rate = sample_rate
        self._plugins = [
            (s["type"] == "pitch", _build_plugin(s, sample_rate))
            for s in chain.stages
        ]

    def _run(self, audio):
        x = np.asarray(audio, dtype=np.float32).reshape(1, -1)
        for independent, plugin in self._plugins:
            if x.shape[1] == 0:
                break
            # Pitch stages process each chunk on its own (see module
            # docstring); everything else carries state across chunks.
            x = plugin(x, self._sample_rate, reset=independent)
        # Final safety: effects with gain (EQ boosts, reverb, drive) can
        # push past full scale, and paplay would wrap rather than clip.
        return np.clip(x[0], -1.0, 1.0).astype(np.float32, copy=False)

    def process(self, audio):
        return self._run(audio)

    def flush(self):
        tail_seconds = self._chain.tail_seconds
        if tail_seconds <= 0:
            return np.zeros(0, dtype=np.float32)
        silence = np.zeros(int(tail_seconds * self._sample_rate), dtype=np.float32)
        tail = self._run(silence)
        audible = np.flatnonzero(np.abs(tail) > _TAIL_SILENCE_THRESHOLD)
        if audible.size == 0:
            return np.zeros(0, dtype=np.float32)
        return tail[: audible[-1] + 1]
