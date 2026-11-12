# Third-party software, models and services

Hikidashi's own code is under the [PolyForm Noncommercial License 1.0.0](LICENSE).
What it runs, downloads and calls is not: each piece below keeps its own
license, and that license, not Hikidashi's, says what you may do with it.

Hikidashi is **not affiliated with, endorsed by or sponsored by Instagram or
Meta**. "Instagram" is a trademark of Meta Platforms, Inc., used here only to
say what the app works with.

## Models

**No model weights are in this repository or in the Docker images.** Each is
downloaded the first time it is needed, from where its authors publish it,
into `data/models` (or Ollama's own store), under its own terms. Running a
model means accepting those terms; read them before you rely on them,
especially for anything commercial.

| Model | Used for | Fetched from | License |
|---|---|---|---|
| `google/embeddinggemma-300m` | search vectors (embed) | Hugging Face, via fastembed | [Gemma Terms of Use](https://ai.google.dev/gemma/terms) and the [Gemma Prohibited Use Policy](https://ai.google.dev/gemma/prohibited_use_policy) |
| `distil-small.en` (cpu profile) | transcripts (ASR) | Hugging Face, `Systran/faster-distil-whisper-small.en` | MIT |
| `distil-large-v3` (cuda profile) | transcripts (ASR) | Hugging Face, `Systran/faster-distil-whisper-large-v3` | MIT |
| `qwen2.5:3b` (cpu profile) | tags and recipes | the Ollama library | [Qwen Research License](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct/blob/main/LICENSE): **noncommercial** |
| `qwen2.5:7b` (cuda profile) | tags and recipes | the Ollama library | Apache-2.0 |
| Tesseract's English data (`eng`, `osd`) | on-screen text (OCR) | Debian's `tesseract-ocr` package, in the worker image | Apache-2.0 |

Hikidashi uses no reranker
([decision record 0011](docs/adr/0011-hybrid-search-rank-fusion.md)).

## Programs in the images

The images are built from `python:3.12-slim` (Debian) and carry Debian's own
packages under their own licenses. The ones Hikidashi calls:

| Program | Image | License |
|---|---|---|
| [FFmpeg](https://ffmpeg.org/legal.html) (Debian's build) | server, worker | GPL-2.0-or-later, as Debian builds it. Hikidashi runs it as a separate program and does not link to it. Its source is [Debian's `ffmpeg` source package](https://sources.debian.org/src/ffmpeg/) for the version in the image |
| [Tesseract](https://github.com/tesseract-ocr/tesseract) | worker | Apache-2.0 |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | server | Unlicense (public domain). Run as a separate program |
| [Ollama](https://github.com/ollama/ollama) | its own `ollama/ollama` image, not Hikidashi's | MIT |

The `hikidashi-worker-cuda` image also carries NVIDIA's cuBLAS and cuDNN
runtime libraries (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`), redistributed
under the [NVIDIA software license agreements](https://docs.nvidia.com/cuda/eula/)
that come with them.

## Libraries

The Python packages are pinned in [`uv.lock`](uv.lock) and the front end's in
[`frontend/package-lock.json`](frontend/package-lock.json); each package's
license is in its own metadata. They are permissive (MIT, BSD, Apache-2.0,
PSF, Unlicense and the like) apart from a few under MPL-2.0 (certifi, tqdm, pathspec),
used unmodified. The main ones:

| Library | License |
|---|---|
| FastAPI, Starlette, Pydantic | MIT, BSD-3-Clause, MIT |
| Uvicorn | BSD-3-Clause |
| fastembed, ONNX Runtime | Apache-2.0, MIT |
| faster-whisper, CTranslate2 | MIT |
| sqlite-vec | MIT or Apache-2.0 |
| argon2-cffi | MIT |
| Svelte, SvelteKit, Paraglide JS | MIT |
| Leaflet, Leaflet.markercluster | BSD-2-Clause, MIT |

The interface fonts (Audiowide, DotGothic16, Exo 2, Jost, Orbitron, Pixelify
Sans, Press Start 2P, Rajdhani, Righteous) are under the
[SIL Open Font License 1.1](https://openfontlicense.org), bundled from
Fontsource.
The lettering in the wordmark and the social preview
(`docs/brand/`) is drawn from Zen Maru Gothic and Zen Kaku Gothic New, under
the same license, as outlines: the fonts themselves are not included.

## Services

Hikidashi calls these only when you use the features that need them
([architecture](docs/architecture.md#context)). Each has a usage policy that
is yours to follow.

| Service | When | Terms |
|---|---|---|
| Instagram, through yt-dlp | fetching the reels you saved | [Instagram's Terms of Use](https://help.instagram.com/581066165581870). Downloading is for your own saves, in your own library |
| OpenStreetMap's map tiles (the default) | the map view | [Tile Usage Policy](https://operations.osmfoundation.org/policies/tiles/); map data © OpenStreetMap contributors, [ODbL](https://www.openstreetmap.org/copyright). For heavy use, set `HIKIDASHI_TILES` to another provider |
| Nominatim (`public`, or your own server) | looking up places | [Nominatim Usage Policy](https://operations.osmfoundation.org/policies/nominatim/) for the public server: at most one request a second, which Hikidashi keeps to, and an identifying User-Agent, which it sends |

## Demo media

The clips and photos in the demo library and the README recordings are freely
licensed; each is credited in [docs/demo/CREDITS.md](docs/demo/CREDITS.md).
