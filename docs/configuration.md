# Configuration

Every setting, with its default. [Back to the README](../README.md)

Copy `.env.example` to `.env` and change what you need; every value has a
working default. Compose reads `.env` under Docker, and `./hikidashi` passes it
to uv under uv. A variable set in the shell wins in both. A bare
`uv run hikidashi …` doesn't read it unless you add `UV_ENV_FILE=.env`.

On Unraid and TrueNAS the same settings are fields in the template or the YAML
([Unraid](install-unraid.md), [TrueNAS SCALE](install-truenas.md)).

| Variable | Default | Meaning |
|----------|---------|---------|
| `HIKIDASHI_DATA` | `./data` | Docker: the host folder mounted at `/data`, which holds all of the below. On a local disk: on Unraid a pool (`/mnt/cache/appdata/hikidashi`), never `/mnt/user`. See [Where the data lives](pipeline.md#where-the-data-lives) |
| `HIKIDASHI_USER` | `0:0` (root) | Docker: the user the containers run as, such as `99:100` on Unraid or `568:568` on TrueNAS. A library written as root first needs `chown -R` to it |
| `HIKIDASHI_DB` | `data/hikidashi.sqlite3` | Keep on local block storage. SQLite over NFS can corrupt |
| `HIKIDASHI_MEDIA` | `data/media` | Thumbnails and video. Bulk storage is fine |
| `HIKIDASHI_IMPORTS` | `data/imports` | Where uploaded export ZIPs land |
| `HIKIDASHI_PROFILE` | `auto` | The worker's models: `auto` \| `cpu` \| `cuda`. `hikidashi worker --profile` overrides it |
| `HIKIDASHI_MODELS` | `data/models` | Whisper and embedding weights, downloaded on first use |
| `HIKIDASHI_LOGS` | `data/logs` | The server's and the workers' logs (`server.log`, `worker.log`, and under Docker `server-worker.log`), rotated at 1 MB |
| `HIKIDASHI_RUN` | `data/run` | The uv pidfiles, the pipeline lock and the worker's wake-up file |
| `HIKIDASHI_HOST` | `0.0.0.0` | Where the server listens |
| `HIKIDASHI_PORT` | `8080` | |
| `HIKIDASHI_AUTH` | `none` | `none` \| `local` \| `proxy`. See [Accounts](accounts.md) |
| `HIKIDASHI_ORIGIN` | *(empty)* | The public address, as `https://reels.example.com`, behind a proxy that rewrites Host |
| `HIKIDASHI_PROXY_HEADER` | `Remote-User` | `proxy` mode: the header naming the user |
| `HIKIDASHI_TRUSTED_PROXIES` | `127.0.0.1/32,::1/128` | `proxy` mode: the addresses that header is believed from |
| `HIKIDASHI_PROXY_CREATES` | *(empty)* | `proxy` mode: `1` makes an account for a name that has none |
| `HIKIDASHI_LOCAL_STAGES` | uv: `all`; Docker: `asr,ocr,tag,recipe` | The stages the worker runs: `all`, `none`, or a list (`asr,ocr`). Under Docker, the `worker` container's. See [The worker](pipeline.md#which-stages-run-where) |
| `HIKIDASHI_SERVER_STAGES` | `enrich,embed` | Docker: the stages the `server-worker` container runs |
| `HIKIDASHI_WORKER_NAME` | the host's name; Docker: `hikidashi-worker` | The worker's name in status. Each worker on one server needs its own |
| `HIKIDASHI_SERVER_WORKER_NAME` | `hikidashi-server-worker` | Docker: the `server-worker` container's name in status |
| `HIKIDASHI_UNLOAD_AFTER` | `300` | Seconds with no work before the worker unloads Whisper and the embedder |
| `HIKIDASHI_WORKER_CPUS` | `2` | Docker only: the cores each worker may use. `0`: no limit |
| `HIKIDASHI_WORKER_TOKEN` | *(empty)* | A remote worker: the worker token it presents, made by an admin on the Pipeline page or with `hikidashi token make --worker`; unset, it asks at the terminal, hidden. The server takes none from its own environment, and warns if this is set there. See [remote workers](remote-workers.md) |
| `HIKIDASHI_SERVER` | *(empty)* | A remote worker: the server it works for, as `http://nas.lan:8080`. Set, `transcribe`, `ocr`, `tag`, `recipe` and `pipeline` claim from that server too |
| `HIKIDASHI_CACHE` | `data/cache` | A remote worker: where it keeps the media it downloads, up to 2 GB |
| `HIKIDASHI_ASR_PROVIDER`, `HIKIDASHI_OCR_PROVIDER`, `HIKIDASHI_LLM_PROVIDER` | *(empty)* | Hosted AI: the server's API's provider per stage (`LLM` is tags, the game pass and recipes): `openai`, `groq`, `mistral`, `gemini`, `openrouter`, `cerebras`, `github`, `cloudflare`, `huggingface` or `compatible`. Wins over the settings page and locks the field there; never turns the switch on. See [hosted AI](operation.md#hosted-ai) |
| `HIKIDASHI_ASR_MODEL`, `HIKIDASHI_OCR_MODEL`, `HIKIDASHI_LLM_MODEL` | the preset's default | Hosted AI: a model ID |
| `HIKIDASHI_ASR_LANGUAGE` | *(empty)* | Hosted AI: force a transcription language, as `ja`; empty, the model detects it |
| `OPENAI_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `CEREBRAS_API_KEY`, `GITHUB_MODELS_TOKEN`, `CLOUDFLARE_API_TOKEN`, `HF_TOKEN` | *(empty)* | Hosted AI: the server's key for each provider, or `<NAME>_FILE` naming a file that holds it. Never sent to a worker, and never shown |
| `HIKIDASHI_OPENAI_BASE_URL`, `HIKIDASHI_OPENAI_API_KEY` | *(empty)* | Hosted AI: an OpenAI-compatible server of your own (Speaches, vLLM, LiteLLM, LocalAI), for the `compatible` provider |
| `HIKIDASHI_HOSTED_BUDGET` | *(empty)* | Hosted AI: USD a month for the server's API (or a hosted worker's); required to turn it on |
| `HIKIDASHI_SECRET_KEY` | *(empty)*: `data/secret.key` | Encrypts the API keys saved on the settings page. The same in every container on a library |
| `HIKIDASHI_API_TOKEN` | *(empty)* | `hikidashi ai --server`: an API token with `read:ai` and `write:ai`; unset, it asks |
| `COMPOSE_FILE` | `docker-compose.yml` | Docker: `docker-compose.yml:docker-compose.server-only.yml` leaves the worker container out, for a server whose heavy stages run on another machine. Not read by Unraid's Compose Manager: see [its stack folders](install-unraid.md#the-alternative-compose-manager) |
| `HIKIDASHI_MODE` | *(auto)* | `uv` \| `docker` for `./hikidashi`. Unset, it detects |
| `HIKIDASHI_STOP_TIMEOUT` | `10` | uv: seconds `./hikidashi stop` waits before it kills the server and worker |
| `OLLAMA_URL` | `http://localhost:11434` | uv: the Ollama the worker tags and finds recipes with, and the server answers Ask with when neither a hosted API nor a self-hosted provider is set (ADR 0021). `hikidashi worker --ollama-url` overrides it for the worker |
| `HIKIDASHI_OLLAMA_URL` | the `ollama` service | Docker: the same, for the `worker` and `app` containers. To tag with another machine's GPU, run a [remote worker](remote-workers.md) there |
| `HIKIDASHI_RECIPE_FETCH` | `on` | `off`: never fetch the rest of an incomplete recipe from the page its caption links to ([ADR 0020](adr/0020-fetch-incomplete-recipes.md)) |
| `HIKIDASHI_GEOCODER` | *(empty)* | A Nominatim server to look places up on, or `public` for OpenStreetMap's. Empty: nothing is looked up. See [the map](using.md#places-and-the-map) |
| `HIKIDASHI_GEOCODER_DELAY` | `1` | Seconds between lookups. The public server asks for 1; your own can take 0 |
| `HIKIDASHI_LOCATE_EVERY` | `300` | Seconds between the server's looks for new places to put on the map. `0`: only `./hikidashi places` looks |
| `HIKIDASHI_TILES` | OpenStreetMap's | Map tiles, as `https://tiles.example.com/{z}/{x}/{y}.png` |
| `HIKIDASHI_TILES_ATTRIBUTION` | OpenStreetMap's | The credit shown on the map, as HTML. Empty for your own tiles unless set |
| `HIKIDASHI_METRICS` | `off` | `on`: Prometheus metrics at `/metrics` ([monitoring](operation.md#monitoring)) |
| `HIKIDASHI_METRICS_TOKEN` | *(empty)* | A bearer token every scrape must send, or `HIKIDASHI_METRICS_TOKEN_FILE`. Empty: only addresses in `HIKIDASHI_METRICS_ALLOW` may scrape |
| `HIKIDASHI_METRICS_ALLOW` | loopback and private ranges | Without a token: the addresses that may scrape, as CIDRs, comma-separated |
| `HIKIDASHI_METRICS_PORT` | *(empty)* | Serve `/metrics` on this port instead of the main one, to leave it unpublished |
| `HIKIDASHI_METRICS_REFRESH` | `30` | Seconds between the collector's reads of the database; a scrape between them is answered from memory |
| `HIKIDASHI_METRICS_PER_USER` | *(empty)* | `on`: a `user` label on hosted AI metrics. Off, every user's API is `api="user"`, summed |
| `HIKIDASHI_SYSLOG` | *(empty)* | Also send the log to syslog: `udp://host:514`, `tcp://host:514` or `unix:///dev/log` ([logging](operation.md#logging)) |
| `HIKIDASHI_SYSLOG_FORMAT` | `rfc5424` | `rfc5424`, or `rfc3164` for older receivers |
| `HIKIDASHI_SYSLOG_FACILITY` | `local0` | The syslog facility |
| `HIKIDASHI_LOKI_URL` | *(empty)* | Also push the log to Loki, as `http://loki:3100` |
| `HIKIDASHI_LOKI_TOKEN`, `HIKIDASHI_LOKI_USER`, `HIKIDASHI_LOKI_PASSWORD` | *(empty)* | Loki's bearer token, or basic auth; each also as `*_FILE` |
| `HIKIDASHI_LOKI_TENANT` | *(empty)* | Loki's `X-Scope-OrgID` |
| `HIKIDASHI_LOKI_LABELS` | *(empty)* | Extra static labels, as `env=home,site=nas` |
| `HIKIDASHI_LOG_LEVEL` | `info` | What is sent to syslog and Loki; the log file keeps its own |
| `HIKIDASHI_LOG_FORMAT` | `text` | `json`: one JSON object a line on stdout, syslog and Loki |
| `HIKIDASHI_VERSION` | `latest` | Docker: the release of the published images, as `1.2.3`. A tag nobody published, as `dev`, keeps your own `--build` images |
| `HIKIDASHI_WORKER_IMAGE` | `hikidashi-worker`; `hikidashi-worker-cuda` in `docker-compose.worker.yml` | Docker: the worker image, `hikidashi-worker-cuda` for a GPU |
| `HIKIDASHI_EXTRAS` | *(empty)*; `--extra cuda` in `docker-compose.worker.yml` | Docker builds only. `--extra cuda` adds the GPU libraries to the worker image; set to nothing for the CPU one |
