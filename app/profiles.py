"""Hardware profiles: which model runs, and how, on the box we find ourselves on.

`HIKIDASHI_PROFILE` is `auto`, `cpu` or `cuda`. `auto` picks `cuda` when
CTranslate2 can see a GPU and `cpu` otherwise. The resolved name is what gets
stamped into `asr_profile`, so a transcript always records which model wrote it.

Stdlib only. CTranslate2 is imported lazily, so resolving `cpu` explicitly, and
the tests, need nothing installed.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

CHOICES = ("auto", "cpu", "cuda")


@dataclass(frozen=True)
class Profile:
    name: str  # cpu | cuda, never auto
    device: str
    asr_model: str
    asr_compute: str
    asr_workers: int  # transcriptions in flight at once
    tag_model: str  # an Ollama model tag, pulled on first use
    cpu_threads: int = 0  # per worker; 0 lets CTranslate2 choose


def cpus() -> int:
    """The cores this process may use. Under a container's CPU limit
    (`cpus:` in docker-compose.yml) that is the limit, which os.cpu_count()
    does not see: it counts the host's."""
    n = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota != "max":
            n = min(n or 1, max(1, math.ceil(int(quota) / int(period))))
    except (OSError, ValueError):
        pass  # no cgroup v2 here, or no limit
    return max(1, n or 1)


def _cpu(workers: int = 4) -> Profile:
    # Four workers share the cores between them rather than oversubscribing.
    threads = max(1, cpus() // workers)
    # 3B is the largest instruct model that tags a reel in seconds on CPU, and
    # the JSON schema keeps a small model from wandering off the taxonomy.
    return Profile(
        name="cpu",
        device="cpu",
        asr_model="distil-small.en",
        asr_compute="int8",
        asr_workers=workers,
        tag_model="qwen2.5:3b",
        cpu_threads=threads,
    )


def _cuda() -> Profile:
    # One worker: the GPU is the bottleneck, and two models would not fit
    # comfortably next to Ollama. The 7B at Q4 is ~5 GB, which leaves room for
    # Whisper on a 10 GB card.
    return Profile(
        name="cuda",
        device="cuda",
        asr_model="distil-large-v3",
        asr_compute="float16",
        asr_workers=1,
        tag_model="qwen2.5:7b",
    )


def cuda_devices() -> int:
    """How many GPUs CTranslate2 can use. 0 when it is missing or has no CUDA."""
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


def resolve(requested: str, *, devices: int | None = None) -> Profile:
    """Turn a requested profile into a concrete one. Raises ValueError."""
    requested = (requested or "auto").strip().lower()
    if requested not in CHOICES:
        raise ValueError(f"unknown profile {requested!r}; expected one of {', '.join(CHOICES)}")
    if requested == "cpu":
        return _cpu()
    if devices is None:
        devices = cuda_devices()
    if requested == "cuda":
        if not devices:
            # Asked for explicitly, so falling back quietly would hide a broken
            # driver behind a run that takes ten times longer than expected.
            raise ValueError("profile 'cuda' requested but no CUDA device is visible")
        return _cuda()
    return _cuda() if devices else _cpu()
