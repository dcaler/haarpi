from __future__ import annotations
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
import httpx
from .config import GlobalConfig

_RETRIES = 3
_BACKOFF = 5


class Brain:
    def __init__(self, cfg: GlobalConfig, coordinator: str | None = None):
        self._url = cfg.ollama_url
        self._coord = coordinator or cfg.coordinator_model
        self._worker_model = cfg.worker_model

    def coordinator(self, prompt: str, system: str = "", num_ctx: int = 32768) -> str:
        return self._call(self._coord, prompt, system, num_ctx, temperature=0.4)

    def worker(self, prompt: str, system: str = "", num_ctx: int = 8192) -> str:
        return self._call(self._worker_model, prompt, system, num_ctx, temperature=0.1)

    def worker_map(self, jobs: list[tuple[str, str]], num_ctx: int = 8192) -> list[str]:
        """Run multiple worker calls in parallel. Each job is (system, prompt)."""
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = [ex.submit(self.worker, prompt, system, num_ctx) for system, prompt in jobs]
        out = []
        for f in futs:
            try:
                out.append(f.result())
            except Exception as e:
                print(f"[warn] worker failed: {e}", file=sys.stderr)
                out.append("")
        return out

    def _call(self, model: str, prompt: str, system: str, num_ctx: int, temperature: float) -> str:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        for attempt in range(1, _RETRIES + 1):
            try:
                buf = ""
                with httpx.stream(
                    "POST",
                    f"{self._url}/api/chat",
                    json={
                        "model": model,
                        "messages": messages,
                        "stream": True,
                        "options": {"temperature": temperature, "num_ctx": num_ctx},
                    },
                    timeout=httpx.Timeout(2400.0, connect=60.0),
                ) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        chunk = json.loads(line)
                        buf += chunk.get("message", {}).get("content", "")
                        if chunk.get("done"):
                            break
                return buf
            except Exception as exc:
                if attempt == _RETRIES:
                    raise
                print(f"[warn] ollama attempt {attempt}/{_RETRIES}: {exc}", file=sys.stderr)
                time.sleep(_BACKOFF * attempt)

        return ""
