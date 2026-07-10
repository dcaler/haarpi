"""raconteur's brain binding — the shared offline roles layer (haarpi.brain).

Keeps raconteur's tuning where it differs from the family defaults: a warmer
coordinator (0.4 — prose drafting wants more variance than evidence synthesis),
a smaller worker window (4096), and a fixed 4-way worker swarm. The think flag
stays instance-level and is always sent, as before."""

from __future__ import annotations

from haarpi import brain as _core

from .config import GlobalConfig


class Brain(_core.Brain):
    def __init__(self, cfg: GlobalConfig, coordinator: str | None = None, think: bool = False):
        super().__init__(
            cfg.ollama_url,
            coordinator or cfg.coordinator_model,
            cfg.worker_model,
            coordinator_temperature=0.4,
            worker_parallel=4,
            think=think,
            tool="raconteur",
        )

    def coordinator(self, prompt: str, system: str = "", num_ctx: int = 16384) -> str:
        return super().coordinator(prompt, system, num_ctx=num_ctx)

    def worker(self, prompt: str, system: str = "", num_ctx: int = 4096) -> str:
        return super().worker(prompt, system, num_ctx=num_ctx)

    def worker_map(self, jobs: list[tuple[str, str]], num_ctx: int = 4096) -> list[str]:
        return super().worker_map(jobs, num_ctx=num_ctx)
