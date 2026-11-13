"""Transcription on a hosted provider (#116).

    upload      ffmpeg encodes the reel's audio as 16 kHz mono FLAC, which is
                what the local path decodes to, losslessly packed: a minute is
                about a megabyte, so a reel is well under the usual 25 MB.
                Anything still over the provider's limit is cut into parts,
                each sent on its own, and the parts joined.
    music       hosted Whisper has no VAD filter, and on a song it writes
                lyrics. Asked for `verbose_json`, it says how likely each
                segment is to be no speech at all: a segment with
                `no_speech_prob` over NO_SPEECH, or decoded with an average
                log-probability under MIN_LOGPROB, is dropped, and the rest
                go through `is_noise` with the seconds the kept segments span
                standing in for the VAD's. A model that gives no segments
                (the gpt-4o transcribe models) gets `is_noise` over the whole
                clip only, which keeps a song's lyrics: the settings say so.
    language    a multilingual model detects it, unless HIKIDASHI_ASR_LANGUAGE
                (or the setting) names one. Stored as ISO 639-1 in
                item.asr_language.

The profile it stamps is `provider:model`.

Stdlib only: ffmpeg is a subprocess.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.db import Row
from app.providers.client import Client, Transcript, Usage

from .transcriber import AudioError, NoAudio, Said, is_noise, join_segments, said

NO_SPEECH = 0.6  # Whisper's own threshold for a silent window
MIN_LOGPROB = -1.0  # below this, Whisper itself treats a window as a failed decode

# Whisper's language names, as verbose_json reports them, to ISO 639-1. The
# ones a library is likely to hold; any other is kept as the model said it.
LANGUAGES = {
    "english": "en",
    "japanese": "ja",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "portuguese": "pt",
    "dutch": "nl",
    "korean": "ko",
    "chinese": "zh",
    "russian": "ru",
    "arabic": "ar",
    "hindi": "hi",
    "turkish": "tr",
    "polish": "pl",
    "swedish": "sv",
    "norwegian": "no",
    "danish": "da",
    "finnish": "fi",
    "greek": "el",
    "hebrew": "he",
    "indonesian": "id",
    "malay": "ms",
    "thai": "th",
    "vietnamese": "vi",
    "tagalog": "tl",
    "ukrainian": "uk",
    "czech": "cs",
    "romanian": "ro",
    "hungarian": "hu",
    "catalan": "ca",
}


def language_code(said: str | None) -> str | None:
    if not said:
        return None
    s = said.strip().lower()
    return s if len(s) == 2 else LANGUAGES.get(s, s[:16])


def encode(path: Path, *, start: float | None = None, length: float | None = None) -> bytes:
    """The audio of a media file (or of `length` seconds of it from `start`)
    as 16 kHz mono FLAC."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.2f}"]
    cmd += ["-i", str(path)]
    if length is not None:
        cmd += ["-t", f"{length:.2f}"]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "flac", "-f", "flac", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=300)
    except subprocess.TimeoutExpired as exc:
        raise AudioError("ffmpeg timed out") from exc
    except OSError as exc:
        raise AudioError(f"ffmpeg not runnable: {exc}") from exc
    err = proc.stderr.decode("utf-8", "replace")
    if proc.returncode != 0 and (
        "does not contain any stream" in err or "matches no streams" in err
    ):
        raise NoAudio("no audio track")
    if proc.returncode != 0:
        first = next((ln for ln in err.splitlines() if ln.strip()), "ffmpeg failed")
        raise AudioError(first.strip()[:200])
    if len(proc.stdout) < 100:  # a FLAC header and no frames
        raise NoAudio("empty audio track")
    return proc.stdout


def guard(t: Transcript, duration: float | None = None, *, offset: float = 0.0) -> Said | None:
    """The transcript worth keeping, or None: the music guard. `duration`,
    the clip's, for a model that reports neither segments nor its own.
    `offset`, in seconds, is where this part starts in the clip."""
    if t.segments is None:
        whole = t.text.strip()
        if not whole:
            return None
        return None if is_noise(whole, t.duration or duration or 0.0) else Said(whole)
    kept = [
        s
        for s in t.segments
        if float(s.get("no_speech_prob") or 0.0) <= NO_SPEECH
        and float(s.get("avg_logprob") or 0.0) >= MIN_LOGPROB
    ]
    heard = said(
        (
            offset + float(s.get("start") or 0),
            offset + float(s.get("end") or 0),
            str(s.get("text") or ""),
        )
        for s in kept
    )
    if heard is None:
        return None
    speech = sum(max(0.0, float(s.get("end") or 0) - float(s.get("start") or 0)) for s in kept)
    return None if is_noise(heard.text, speech) else heard


@dataclass
class Heard:
    said: Said | None
    language: str | None = None
    usage: Usage = field(default_factory=Usage)


class Hosted:
    """One provider's transcription model, as the API's ASR stage runs it."""

    def __init__(self, client: Client, model: str, *, language: str | None = None):
        self.client = client
        self.model = model
        self.language = language or None
        self.segments = model in client.preset.segments

    @property
    def label(self) -> str:
        return f"{self.client.preset.name}:{self.model}"

    def __call__(self, path: Path, duration: float | None) -> Heard:
        """Transcribe one media file. Raises AudioError, NoAudio, and the
        provider's errors."""
        audio = encode(path)
        limit = self.client.preset.upload
        if len(audio) <= limit:
            t = self._send(audio)
            return Heard(guard(t, duration), language_code(self.language or t.language), t.usage)
        return self._in_parts(path, duration, len(audio), limit)

    def _send(self, audio: bytes) -> Transcript:
        return self.client.transcribe(
            self.model,
            audio,
            filename="audio.flac",
            content_type="audio/flac",
            language=self.language,
            segments=self.segments,
        )

    def _in_parts(self, path: Path, duration: float | None, size: int, limit: int) -> Heard:
        """Too big for one request: cut by time into parts that fit, with a
        tenth to spare, and join them."""
        if not duration:
            from app.enrich.fetcher import probe_duration

            duration = probe_duration(path)
        if not duration:
            raise AudioError("too big to send in one part, and its length is unknown")
        parts = int(size / (limit * 0.9)) + 1
        step = duration / parts
        heard: list[Said] = []
        languages, usage = [], Usage()
        for i in range(parts):
            t = self._send(encode(path, start=i * step, length=step))
            usage.add(t.usage)
            if (kept := guard(t, step, offset=i * step)) is not None:
                heard.append(kept)
                languages.append(t.language)
        spoken = next((lang for lang in languages if lang), None)
        text = join_segments([h.text for h in heard])
        # Timed only if every part was: a gap would put later lines in the wrong place.
        timed = all(h.segments for h in heard)
        whole = (
            Said(text, tuple(s for h in heard for s in h.segments) if timed else ())
            if text
            else None
        )
        return Heard(whole, language_code(self.language or spoken), usage)


def transcribe_one(row: Row, media_dir: Path, asr: Hosted) -> Heard:
    """One reel on a provider. Raises AudioError and the provider's errors;
    a clip with no audio is no speech."""
    try:
        return asr(media_dir / row["media_path"], row["duration"])
    except NoAudio:
        return Heard(None)
