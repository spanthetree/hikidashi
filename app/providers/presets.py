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
    strict        whether a json_schema is sent strict: True, False, or the
                  models it is strict for (Groq: the gpt-oss models only)
    segments      ASR models that answer `verbose_json` with each segment's
                  `no_speech_prob`, which the music guard needs (asr/hosted.py)
    languages     ASR models that take a `languages` list rather than one
                  `language`, and report `languages` back (gpt-transcribe)
    extra         per model, more to send with every chat request: a
                  reasoning model asked not to think at length, say
    upload        the largest audio file it takes, in bytes
    tokens_param  what it calls the output cap
    recommended   per capability, models worth trying besides the default
    needs_key     False for a server of your own, which takes none
    url_hint      what to type for a base URL that is the setting's
    self_hosted   it runs on your own machine: nothing leaves your network

Free tiers, model names and data terms change often. Each preset carries the
date it was checked; docs/operation.md has the table, with sources. A preset
whose model names could not be checked has no defaults: its models are
chosen from the provider's own list (`hikidashi ai models`, or the page).

Stdlib only, and no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# What a stage needs of a provider.
STAGES = ("asr", "ocr", "tag", "recipe")
NEEDS = {"asr": "asr", "ocr": "vision", "tag": "chat", "recipe": "chat"}

COMPATIBLE = "compatible"  # any OpenAI-compatible URL: a LAN server, LiteLLM, a proxy
OLLAMA = "ollama"  # an Ollama of your own, on its OpenAI-compatible /v1

MB = 1 << 20


@dataclass(frozen=True)
class Preset:
    name: str
    label: str
    base_url: str  # "" when the setting gives it: a LAN server, a Cloudflare account
    key_env: str
    can: frozenset[str]
    models: dict[str, str] = field(default_factory=dict)
    structured: str = "json_schema"  # json_schema | json_object | none
    strict: bool | frozenset[str] = True
    segments: frozenset[str] = frozenset()
    languages: frozenset[str] = frozenset()
    extra: dict[str, dict[str, object]] = field(default_factory=dict)
    upload: int = 25 * MB
    tokens_param: str = "max_completion_tokens"
    # Requests at once, and a minute, before its own limits say otherwise.
    in_flight: int = 4
    per_minute: int = 60
    free: str = ""  # one line: the free tier, if any
    terms: str = ""  # one line: what it does with what it is sent
    checked: str = ""  # YYYY-MM-DD
    recommended: dict[str, tuple[str, ...]] = field(default_factory=dict)
    needs_key: bool = True
    url_hint: str = ""
    self_hosted: bool = False

    def does(self, stage: str) -> bool:
        return NEEDS[stage] in self.can

    def model(self, stage: str) -> str | None:
        return self.models.get(NEEDS[stage])

    @property
    def needs_base_url(self) -> bool:
        return not self.base_url

    def is_strict(self, model: str) -> bool:
        return self.strict if isinstance(self.strict, bool) else model in self.strict


CHECKED = "2026-11-12"

PRESETS: dict[str, Preset] = {
    p.name: p
    for p in (
        Preset(
            name="openai",
            label="OpenAI",
            base_url="https://api.openai.com/v1",
            key_env="OPENAI_API_KEY",
            can=frozenset({"asr", "chat", "vision"}),
            # whisper-1, the gpt-4o transcribe models, gpt-4o-mini and
            # gpt-4.1-mini all shut down between 2027-01-14 and 2027-02-26;
            # these are OpenAI's named replacements.
            models={"asr": "gpt-transcribe", "chat": "gpt-6-luna", "vision": "gpt-6-luna"},
            segments=frozenset({"whisper-1"}),
            languages=frozenset({"gpt-transcribe", "gpt-live-transcribe"}),
            # A reasoning model; its default effort is medium, which a
            # one-sentence summary does not need.
            extra={"gpt-6-luna": {"reasoning_effort": "none"}},
            per_minute=500,
            free="none; paid per use",
            terms="API data not used for training by default; kept up to 30 days "
            "for abuse monitoring",
            checked=CHECKED,
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
            # Strict (constrained) json_schema only on the gpt-oss models;
            # best effort on the rest, and checked here either way.
            strict=frozenset({"openai/gpt-oss-20b", "openai/gpt-oss-120b"}),
            segments=frozenset({"whisper-large-v3", "whisper-large-v3-turbo"}),
            extra={
                "openai/gpt-oss-20b": {"reasoning_effort": "low"},
                "openai/gpt-oss-120b": {"reasoning_effort": "low"},
            },
            per_minute=20,
            in_flight=2,
            free="yes: whisper-large-v3-turbo at 20 a minute, 2,000 a day, 7,200 audio "
            "seconds an hour, 28,800 a day, 25 MB files",
            terms="does not train on API data",
            checked=CHECKED,
        ),
        Preset(
            name="mistral",
            label="Mistral",
            base_url="https://api.mistral.ai/v1",
            key_env="MISTRAL_API_KEY",
            can=frozenset({"asr", "chat", "vision"}),
            strict=False,
            tokens_param="max_tokens",
            free="Experiment plan, rate-limited; check whether it trains on free-plan data",
            terms="paid plans: no training on your data; the free plan's terms differ",
            checked=CHECKED,
        ),
        Preset(
            name="gemini",
            label="Google Gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            key_env="GEMINI_API_KEY",
            # No /audio/transcriptions on its OpenAI-compatible API.
            can=frozenset({"chat", "vision"}),
            strict=False,
            tokens_param="max_tokens",
            free="yes, generous, from Google AI Studio",
            terms="free tier: Google uses what is sent to improve its products, and "
            "people may read it; paid (a billing account): it does not",
            checked=CHECKED,
        ),
        Preset(
            name="openrouter",
            label="OpenRouter",
            base_url="https://openrouter.ai/api/v1",
            key_env="OPENROUTER_API_KEY",
            can=frozenset({"chat", "vision"}),
            structured="json_object",
            tokens_param="max_tokens",
            free="yes: models named :free, with small daily limits",
            terms="each model's upstream provider's own; free models may log prompts",
            checked=CHECKED,
        ),
        Preset(
            name="cerebras",
            label="Cerebras",
            base_url="https://api.cerebras.ai/v1",
            key_env="CEREBRAS_API_KEY",
            can=frozenset({"chat"}),
            structured="json_object",
            tokens_param="max_tokens",
            free="yes, rate-limited per minute and per day",
            terms="see Cerebras's own terms",
            checked=CHECKED,
        ),
        Preset(
            name="github",
            label="GitHub Models",
            base_url="https://models.github.ai/inference",
            key_env="GITHUB_MODELS_TOKEN",
            can=frozenset({"chat", "vision"}),
            structured="json_object",
            per_minute=10,
            in_flight=1,
            free="yes, with a GitHub account; low per-minute and per-day limits",
            terms="GitHub's terms for GitHub Models",
            checked=CHECKED,
        ),
        Preset(
            name="cloudflare",
            label="Cloudflare Workers AI",
            # https://api.cloudflare.com/client/v4/accounts/<account id>/ai/v1
            base_url="",
            key_env="CLOUDFLARE_API_TOKEN",
            # Its OpenAI-compatible layer is chat and embeddings only: its
            # Whisper is on its own API.
            can=frozenset({"chat"}),
            structured="json_object",
            tokens_param="max_tokens",
            free="a daily free allowance",
            terms="Cloudflare does not train on Workers AI inputs",
            checked=CHECKED,
        ),
        Preset(
            name="huggingface",
            label="Hugging Face Inference Providers",
            base_url="https://router.huggingface.co/v1",
            key_env="HF_TOKEN",
            can=frozenset({"chat", "vision"}),
            structured="json_object",
            tokens_param="max_tokens",
            free="monthly free credits",
            terms="the inference provider's own, which Hugging Face routes to",
            checked=CHECKED,
        ),
        Preset(
            name=OLLAMA,
            label="Ollama (self-hosted)",
            base_url="",  # the setting's own: where your Ollama is
            url_hint="http://host.docker.internal:11434/v1",
            key_env="HIKIDASHI_OLLAMA_API_KEY",
            needs_key=False,
            self_hosted=True,
            # No speech to text: transcription needs another provider, or the
            # workers. Vision for on-screen text needs a vision model.
            can=frozenset({"chat", "vision"}),
            # What the GPU worker's cuda profile runs (app/profiles.py), and a
            # vision model of the same family.
            models={"chat": "qwen2.5:7b", "vision": "qwen2.5vl:7b"},
            recommended={
                "chat": ("qwen2.5:7b", "qwen2.5:3b", "qwen2.5:14b", "llama3.1:8b"),
                "vision": ("qwen2.5vl:7b", "qwen2.5vl:3b", "llama3.2-vision:11b", "gemma3:12b"),
            },
            # Its structured outputs constrain generation to the schema; the
            # reply is checked here either way.
            strict=False,
            tokens_param="max_tokens",
            # One GPU: one request at a time is the fastest it goes.
            in_flight=1,
            per_minute=600,
            upload=100 * MB,
            free="your own machine",
            terms="nothing leaves your network",
            checked=CHECKED,
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
            needs_key=False,
            checked=CHECKED,
        ),
    )
}


def get(name: str) -> Preset:
    """A preset by name. Raises ValueError naming the ones there are."""
    try:
        return PRESETS[name]
    except KeyError:
        raise ValueError(f"unknown provider {name!r}; use one of {', '.join(PRESETS)}") from None
