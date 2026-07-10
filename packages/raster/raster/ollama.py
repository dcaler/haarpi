"""raster's brain binding — the shared offline transport (haarpi.brain), logged
as [raster]. RASTER_* env knobs are still honored (core falls back to them)."""

from haarpi import brain as _core
from haarpi.brain import (  # noqa: F401 — the tests and build path use these
    CHARS_PER_TOKEN,
    KEEPALIVE,
    MAX_NUM_CTX,
    MIN_NUM_CTX,
    OLLAMA_TIMEOUT,
    OUTPUT_HEADROOM_TOKENS,
    estimate_tokens,
    normalize_host,
    pick_num_ctx,
)


def chat(host: str, model: str, messages: list, label: str = "",
         think: bool | None = None) -> str:
    # retries stay 0: raster's task layer re-composes the prompt on failure
    # rather than resending it blind (retry-loop context economics).
    return _core.chat(host, model, messages, label=label, think=think,
                      temperature=0.1, tool="raster")
