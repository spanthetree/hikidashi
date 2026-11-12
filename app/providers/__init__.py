"""Hosted AI providers (#116): the client the hosted stages and the API switch
(#125, app/ai) run on.

    keys      API keys that print masked, from env or `_FILE`
    presets   each provider: its URL, what it can do, its default models
    client    chat, transcription and the model list, with errors sorted by
              what they mean for the reels
    limits    requests in flight, per minute, and what the provider says
    schema    the stages' JSON schemas for strict mode, and checked here

`test` is Test connection: one tiny request that says whether the key and the
model work.

Stdlib only, so the server image runs the hosted stages with no
faster-whisper, tesseract, Ollama or torch.
"""

from __future__ import annotations

from dataclasses import dataclass

from .client import Client, Outage, ProviderError, RequestFailed, Unusable, Wait
from .presets import NEEDS

# The smallest valid FLAC there is would need an encoder; a second of silence
# as WAV is 32 kB and every Whisper endpoint takes it.
_SILENCE_RATE = 16_000


def _silence_wav(seconds: float = 1.0) -> bytes:
    import struct

    n = int(_SILENCE_RATE * seconds)
    data = b"\0\0" * n
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, _SILENCE_RATE, _SILENCE_RATE * 2, 2, 16)
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )


def _pixel_png() -> bytes:
    """A 1x1 white PNG."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
        + chunk(b"IEND", b"")
    )


@dataclass
class Check:
    ok: bool
    said: str  # OK, key rejected, model not available to this key, quota exhausted, …
    detail: str = ""


def test(client: Client, stage: str, model: str) -> Check:
    """Test connection for one stage: the key and the model, with one tiny
    request of the kind the stage makes."""
    need = NEEDS[stage]
    try:
        if need == "asr":
            client.transcribe(
                model, _silence_wav(), filename="silence.wav", content_type="audio/wav"
            )
        else:
            from .client import image_part

            user: str | list[dict[str, object]] = "Reply with OK."
            if need == "vision":
                user = [
                    {"type": "text", "text": "Reply with OK."},
                    image_part(_pixel_png(), "image/png"),
                ]
            client.chat(model, "You are a connection test.", user, max_tokens=16)
    except Unusable as exc:
        said = {
            "key": "key rejected",
            "model": "model not available to this key",
            "quota": "quota exhausted",
            "address": "no API at that address",
        }.get(exc.why, "cannot be used")
        return Check(False, said, str(exc))
    except Wait as exc:
        return Check(False, "quota exhausted" if exc.daily else "rate-limited", str(exc))
    except Outage as exc:
        return Check(False, "unreachable", str(exc))
    except RequestFailed as exc:
        # A cap hit by a tiny request still proves the key and the model.
        if "token cap" in str(exc):
            return Check(True, "OK")
        return Check(False, "request failed", str(exc))
    return Check(True, "OK")


__all__ = [
    "Check",
    "Client",
    "Outage",
    "ProviderError",
    "RequestFailed",
    "Unusable",
    "Wait",
    "test",
]
