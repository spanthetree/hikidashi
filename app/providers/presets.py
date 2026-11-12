"""What each hosted provider is: where it is, what it can do, and its limits.

Most providers speak the OpenAI API: `POST /audio/transcriptions` for speech,
`POST /chat/completions` for chat, with images in the message for vision. So
one client (client.py) covers them, and a preset says how each one differs:

    base_url      where its OpenAI-compatible API is
    key_env       the environment variable its key comes from (and `_FILE`)
    can           what it can do: asr, chat, vision. A stage needs one:
                  asr needs asr, ocr needs vision, tag and recipe need chat
    models        the default model for each, which a setting may replace
    structured    how it answers in JSON: json_schema (strict, the schema
                  enforced while generating), json_object (any JSON, with the
                  schema in the prompt), or none (the schema in the prompt).
                  Every reply is checked against the schema here either way.
    segments      ASR models that answer `verbose_json` with each segment's
                  `no_speech_prob`, which the music guard needs (asr/hosted.py)
    upload        the largest audio file it takes, in bytes
    tokens_param  what it calls the output cap

Free tiers, model names and data terms change often. Each preset carries the
date it was checked; docs/operation.md has the table, with sources.

Stdlib only, and no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# What a stage needs of a provider.
STAGES = ("asr", "ocr", "tag", "recipe")
NEEDS = {"asr": "asr", "ocr": "vision", "tag": "chat", "recipe": "chat"}

COMPATIBLE = "compatible"  # any OpenAI-compatible URL: a LAN server, LiteLLM, a proxy

MB = 1 << 20


@dataclass(frozen=True)
class Preset:
    name: str
    label: str
    base_url: str
    key_env: str
    can: frozenset[str]
    models: dict[str, str] = field(default_factory=dict)
    structured: str = "json_schema"  # json_schema | json_object | none
    segments: frozenset[str] = frozenset()
    upload: int = 25 * MB
    tokens_param: str = "max_completion_tokens"
    # Requests at once, and a minute, before its own limits say otherwise.
    in_flight: int = 4
    per_minute: int = 60
    free: str = ""  # one line: the free tier, if any
    terms: str = ""  # one line: what it does with what it is sent
    checked: str = ""  # YYYY-MM-DD

    def does(self, stage: str) -> bool:
        return NEEDS[stage] in self.can

    def model(self, stage: str) -> str | None:
        return self.models.get(NEEDS[stage])


PRESETS: dict[str, Preset] = {
    p.name: p
    for p in (
        Preset(
            name="openai",
            label="OpenAI",
            base_url="https://api.openai.com/v1",
            key_env="OPENAI_API_KEY",
            can=frozenset({"asr", "chat", "vision"}),
            models={"asr": "whisper-1", "chat": "gpt-4.1-mini", "vision": "gpt-4.1-mini"},
            segments=frozenset({"whisper-1"}),
            per_minute=500,
            free="none; paid per use",
            terms="API data not used for training; kept up to 30 days for abuse monitoring",
            checked="2026-11-12",
        ),
        Preset(
            name="groq",
            label="Groq",
            base_url="https://api.groq.com/openai/v1",
            key_env="GROQ_API_KEY",
            can=frozenset({"asr", "chat", "vision"}),
            models={
                "asr": "whisper-large-v3-turbo",
                "chat": "openai/gpt-oss-20b",
                "vision": "meta-llama/llama-4-scout-17b-16e-instruct",
            },
            segments=frozenset({"whisper-large-v3", "whisper-large-v3-turbo"}),
            per_minute=30,
            in_flight=2,
            free="yes, rate-limited per minute and per day (requests, tokens, audio seconds)",
            terms="does not train on API data; not retained beyond processing by default",
            checked="2026-11-12",
        ),
        Preset(
            name=COMPATIBLE,
            label="OpenAI-compatible URL",
            base_url="",  # the setting's own
            key_env="HIKIDASHI_OPENAI_API_KEY",
            can=frozenset({"asr", "chat", "vision"}),
            # Whatever answers there: JSON mode is the most widely supported,
            # and the reply is checked against the schema here anyway.
            structured="json_object",
            # faster-whisper-server/Speaches, vLLM and LocalAI all answer
            # verbose_json; whether a model is listed here is the setting's.
            segments=frozenset(),
            upload=100 * MB,
            tokens_param="max_tokens",
            per_minute=600,
            free="your own server",
            terms="whatever runs at that address",
            checked="2026-11-12",
        ),
    )
}


def get(name: str) -> Preset:
    """A preset by name. Raises ValueError naming the ones there are."""
    try:
        return PRESETS[name]
    except KeyError:
        raise ValueError(f"unknown provider {name!r}; use one of {', '.join(PRESETS)}") from None
