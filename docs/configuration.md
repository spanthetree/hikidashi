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
| `COMPOSE_FILE` | `docker-compose.yml` | Docker: `docker-compose.yml:docker-compose.server-only.yml` leaves the worker container out, for a server whose heavy stages run on another machine. Not read by Unraid's Compose Manager: see [its stack folders](install-unraid.md#the-alternative-compose-manager) |
| `HIKIDASHI_MODE` | *(auto)* | `uv` \| `docker` for `./hikidashi`. Unset, it detects |
| `HIKIDASHI_STOP_TIMEOUT` | `10` | uv: seconds `./hikidashi stop` waits before it kills the server and worker |
| `OLLAMA_URL` | `http://localhost:11434` | uv: the Ollama the worker tags and finds recipes with. `hikidashi worker --ollama-url` overrides it |
| `HIKIDASHI_OLLAMA_URL` | the `ollama` service | Docker: the same, for the `worker` container only. To tag with another machine's GPU, run a [remote worker](remote-workers.md) there |
| `HIKIDASHI_GEOCODER` | *(empty)* | A Nominatim server to look places up on, or `public` for OpenStreetMap's. Empty: nothing is looked up. See [the map](using.md#places-and-the-map) |
| `HIKIDASHI_GEOCODER_DELAY` | `1` | Seconds between lookups. The public server asks for 1; your own can take 0 |
| `HIKIDASHI_LOCATE_EVERY` | `300` | Seconds between the server's looks for new places to put on the map. `0`: only `./hikidashi places` looks |
| `HIKIDASHI_TILES` | OpenStreetMap's | Map tiles, as `https://tiles.example.com/{z}/{x}/{y}.png` |
| `HIKIDASHI_TILES_ATTRIBUTION` | OpenStreetMap's | The credit shown on the map, as HTML. Empty for your own tiles unless set |
| `HIKIDASHI_VERSION` | `latest` | Docker: the release of the published images, as `0.19.0`. A tag nobody published, as `dev`, keeps your own `--build` images |
| `HIKIDASHI_WORKER_IMAGE` | `hikidashi-worker`; `hikidashi-worker-cuda` in `docker-compose.worker.yml` | Docker: the worker image, `hikidashi-worker-cuda` for a GPU |
| `HIKIDASHI_EXTRAS` | *(empty)*; `--extra cuda` in `docker-compose.worker.yml` | Docker builds only. `--extra cuda` adds the GPU libraries to the worker image; set to nothing for the CPU one |
