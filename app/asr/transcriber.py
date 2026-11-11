"""Turning one mp4 into text.

ffmpeg decodes the audio track to 16 kHz mono PCM on a pipe — no temp files —
and faster-whisper transcribes it with the VAD filter on. The filter is what
keeps music-only reels empty: Whisper fed a song with no speech will happily
invent lyrics, and those would then be searchable.

The filter lets through a second of music with a voice-like band, and Whisper
writes its stock phrases there: "Thank you.", "you", "Oh". Their no-speech
probability is low, so its own guard keeps them; `is_noise` drops them after.

`extract_audio` is stdlib only. faster-whisper and numpy are imported when a
`Whisper` is built, so the worker and its tests load without them.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from app.profiles import Profile

# Bumping this invalidates every transcript and schedules a re-transcription.
ASR_V = 2

SAMPLE_RATE = 16_000


class AudioError(Exception):
    """The file could not be decoded. Retrying may help: it might be mid-write."""


class NoAudio(Exception):
    """The file has no audio track. Permanent; there is nothing to transcribe."""


def extract_audio(path: Path, *, timeout: int = 300) -> bytes:
    """Decode a media file's audio to 16 kHz mono signed 16-bit PCM."""
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                "-f",
                "s16le",
                "-",
            ],
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioError("ffmpeg timed out") from exc
    except OSError as exc:
        raise AudioError(f"ffmpeg not runnable: {exc}") from exc

    err = proc.stderr.decode("utf-8", "replace")
    # A video with no audio stream maps nothing to the output and fails with
    # "does not contain any stream" (or, on newer builds, "matches no streams").
    if proc.returncode != 0 and (
        "does not contain any stream" in err or "matches no streams" in err
    ):
        raise NoAudio("no audio track")
    if proc.returncode != 0:
        first = next((ln for ln in err.splitlines() if ln.strip()), "ffmpeg failed")
        raise AudioError(first.strip()[:200])
    if not proc.stdout:
        raise NoAudio("empty audio track")
    return proc.stdout


def join_segments(texts: list[str]) -> str | None:
    """Segments into one transcript. None when nothing was said."""
    text = " ".join(t.strip() for t in texts if t and t.strip())
    return text or None


# Measured on a library of 227 transcripts (#95). Every one with less speech
# than this was a scrap of a word or two, and the shortest real sentence had
# 2.7 s. Speech runs at two or three words a second, and singing at one.
MIN_SPEECH = 2.5  # seconds of audio the VAD filter kept
MIN_RATE = 0.5  # words a second of it: one word over 13 s of crowd noise is not speech

# What Whisper says when there is nothing to say, by sentence, lowercase and
# without punctuation.
STOCK = frozenset(
    {
        "thank you",
        "thank you so much",
        "thank you very much",
        "thank you for watching",
        "thanks",
        "thanks for watching",
        "please subscribe",
        "you",
        "oh",
        "okay",
        "bye",
        "see you",
        "see you next time",
        "i don't know",
        "i think that's it",
    }
)


def sentences(text: str) -> list[str]:
    parts = (re.sub(r"[^\w' ]+", "", p).strip().lower() for p in re.split(r"[.!?,;]+", text))
    return [" ".join(p.split()) for p in parts if p.strip()]


def is_noise(text: str, speech: float) -> bool:
    """Whether a transcript is Whisper filling silence, not anything said.
    `speech` is how many seconds of the audio the VAD filter passed on."""
    if speech < MIN_SPEECH or len(text.split()) / speech < MIN_RATE:
        return True
    # A run of stock phrases on a clip of crowd noise (#95): more than half.
    said = sentences(text)
    return sum(s in STOCK for s in said) * 2 > len(said)


def preload_cuda_libs() -> int:
    """Load cuBLAS and cuDNN from the `cuda` extra's pip wheels, if installed.

    CTranslate2 dlopens them by soname, and the wheels put them somewhere the
    loader never looks. Loading them first, globally, satisfies that lookup
    without anyone having to set LD_LIBRARY_PATH. Returns how many loaded.
    """
    import ctypes

    try:
        import nvidia
    except ImportError:
        return 0  # system CUDA, or none; nothing to do
    loaded = 0
    # cuBLAS before cuDNN: cudnn's sublibraries link against it.
    for pkg in ("cublas", "cudnn"):
        for base in nvidia.__path__:
            for lib in sorted((Path(base) / pkg / "lib").glob("lib*.so*")):
                try:
                    ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
                    loaded += 1
                except OSError:
                    pass  # an optional sublibrary; CTranslate2 will say
    return loaded


class Whisper:
    """One loaded model, safe to call from `profile.asr_workers` threads at once."""

    def __init__(self, profile: Profile, models_dir: Path | None = None):
        if profile.device == "cuda":
            preload_cuda_libs()
        from faster_whisper import WhisperModel

        self.profile = profile
        self.model = WhisperModel(
            profile.asr_model,
            device=profile.device,
            compute_type=profile.asr_compute,
            num_workers=profile.asr_workers,
            cpu_threads=profile.cpu_threads,
            download_root=str(models_dir) if models_dir else None,
        )
        self._warm_up()

    def _warm_up(self) -> None:
        """Run the model once, so a missing CUDA library fails here, loudly.

        Loading the model touches no GPU kernels. Without this, a broken cuBLAS
        surfaces one item at a time and marks the entire queue as failed.
        Noise rather than silence, and no VAD, so the encoder really runs.
        """
        import numpy as np

        noise = np.random.default_rng(0).uniform(-0.1, 0.1, SAMPLE_RATE).astype(np.float32)
        segments, _ = self.model.transcribe(
            noise, language="en", vad_filter=False, beam_size=1, without_timestamps=True
        )
        list(segments)

    def __call__(self, pcm: bytes) -> str | None:
        import numpy as np

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, info = self.model.transcribe(
            audio,
            language="en",  # both distil models are English-only
            vad_filter=True,
            # The distil checkpoints were trained without previous-text
            # conditioning; turning it on invites repetition loops.
            condition_on_previous_text=False,
        )
        # `segments` is lazy: transcription actually happens while iterating.
        text = join_segments([s.text for s in segments])
        if text is None or is_noise(text, info.duration_after_vad):
            return None
        return text
