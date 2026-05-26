"""The "brain": all LLM calls go through here, behind two roles.

  * coordinator() — judgement-heavy, low-volume work (query strategy, synthesis).
    This is the A/B-swappable target: local Ollama 27B *or* the Claude API.
  * worker()      — small, high-volume work (per-paper annotation, scoring).
    Always local Ollama, so the expensive token work stays free even when the
    coordinator is Claude.

worker_map() runs many worker() calls concurrently (the "swarm"). Real
parallelism requires Ollama configured with OLLAMA_NUM_PARALLEL>=N. The big
coordinator model runs one-at-a-time ("heavy profile"); small worker models
can fill remaining VRAM for concurrent requests ("swarm profile" — see README).
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from .config import BrainConfig, GlobalConfig

_OLLAMA_TIMEOUT = httpx.Timeout(2400.0, connect=60.0)  # 60s connect tolerates cold model loads


class Brain:
    def __init__(self, brain_cfg: BrainConfig, gc: GlobalConfig,
                 backend_override: str | None = None):
        self.cfg = brain_cfg
        self.gc = gc
        self.backend = backend_override or brain_cfg.backend
        self._anthropic = None
        if self.backend == "claude":
            self._init_claude()

    # ── coordinator (heavy) ──────────────────────────────────────────────
    def coordinator(self, prompt: str, system: str = "", num_ctx: int = 16384) -> str:
        if self.backend == "claude":
            return self._claude(prompt, system)
        return self._ollama(self.cfg.coordinator_model, prompt, system,
                            num_ctx=num_ctx, temperature=0.2)

    # ── worker (small, local, parallel) ──────────────────────────────────
    def worker(self, prompt: str, system: str = "", num_ctx: int = 8192) -> str:
        return self._ollama(self.cfg.worker_model, prompt, system,
                            num_ctx=num_ctx, temperature=0.1)

    def worker_map(self, jobs: list[tuple[str, str]], num_ctx: int = 8192) -> list[str]:
        """Run worker() over (system, prompt) jobs concurrently. Order preserved."""
        n = max(1, int(self.cfg.worker_parallel))
        results: list[str] = [""] * len(jobs)
        if n == 1:
            for i, (sysmsg, prompt) in enumerate(jobs):
                try:
                    results[i] = self.worker(prompt, sysmsg, num_ctx=num_ctx)
                except Exception as e:  # noqa: BLE001
                    print(f"  [warn] worker job {i} failed: {e}", file=sys.stderr)
            return results
        with ThreadPoolExecutor(max_workers=n) as pool:
            futs = {pool.submit(self.worker, prompt, sysmsg, num_ctx): i
                    for i, (sysmsg, prompt) in enumerate(jobs)}
            for fut in futs:
                i = futs[fut]
                try:
                    results[i] = fut.result()
                except Exception as e:  # noqa: BLE001
                    print(f"  [warn] worker job {i} failed: {e}", file=sys.stderr)
        return results

    # ── embeddings (always local) ────────────────────────────────────────
    def embed(self, text: str, max_chars: int = 1500) -> list[float]:
        if not text or not text.strip():
            raise ValueError("embed() called with empty text")
        limit = max(1, min(max_chars, len(text)))
        transient = 0  # retries for model-loading / network blips
        while True:
            try:
                r = httpx.post(f"{self.gc.ollama_url}/api/embeddings",
                               json={"model": self.cfg.embed_model, "prompt": text[:limit]},
                               timeout=120)
            except httpx.HTTPError as e:
                transient += 1
                if transient <= 4:
                    time.sleep(3 * transient)
                    continue
                raise RuntimeError(f"embedding network error: {e}") from e
            if r.status_code == 200:
                return r.json()["embedding"]
            body = r.text.lower()
            if r.status_code == 500 and "context" in body and "length" in body and limit > 10:
                limit //= 2          # input too long -> shrink and retry
                continue
            if r.status_code >= 500 and transient < 4:
                transient += 1        # model loading / transient -> back off and retry
                time.sleep(3 * transient)
                continue
            raise RuntimeError(f"embedding failed ({r.status_code}): {r.text[:200]}")

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        n = max(1, int(self.cfg.worker_parallel))
        out: list[list[float]] = [[] for _ in texts]
        with ThreadPoolExecutor(max_workers=n) as pool:
            futs = {pool.submit(self.embed, t or " "): i for i, t in enumerate(texts)}
            for fut in futs:
                i = futs[fut]
                try:
                    out[i] = fut.result()
                except Exception:  # noqa: BLE001
                    out[i] = []  # unembeddable doc -> 0 similarity, never tanks the batch
        return out

    # ── backends ─────────────────────────────────────────────────────────
    def _ollama(self, model: str, prompt: str, system: str,
                num_ctx: int, temperature: float, retries: int = 3) -> str:
        import json as _json
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {"model": model, "messages": messages, "stream": True,
                   "options": {"temperature": temperature, "num_ctx": num_ctx}}
        last = None
        for attempt in range(1, retries + 1):
            try:
                parts: list[str] = []
                with httpx.stream("POST", f"{self.gc.ollama_url}/api/chat",
                                  json=payload, timeout=_OLLAMA_TIMEOUT) as r:
                    r.raise_for_status()
                    for line in r.iter_lines():
                        if not line:
                            continue
                        chunk = _json.loads(line)
                        parts.append(chunk.get("message", {}).get("content", ""))
                        if chunk.get("done"):
                            break
                return "".join(parts).strip()
            except Exception as e:  # noqa: BLE001
                last = e
                print(f"  [retry {attempt}/{retries}] ollama {model}: {e}", file=sys.stderr)
                time.sleep(5 * attempt)
        raise RuntimeError(f"ollama call failed after {retries} attempts: {last}")

    def _init_claude(self) -> None:
        if not self.gc.have_anthropic:
            raise RuntimeError(
                "brain backend is 'claude' but no Anthropic API key is configured "
                "(set ANTHROPIC_API_KEY or add it to ~/.config/rabbithole/config.toml)."
            )
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError(
                "anthropic SDK not installed. Run: pip install 'rabbithole[claude]'"
            ) from e
        self._anthropic = anthropic.Anthropic(api_key=self.gc.anthropic_api_key)

    def _claude(self, prompt: str, system: str, retries: int = 3) -> str:
        last = None
        for attempt in range(1, retries + 1):
            try:
                msg = self._anthropic.messages.create(
                    model=self.cfg.claude_model,
                    max_tokens=8192,
                    system=system or "You are a careful research assistant.",
                    messages=[{"role": "user", "content": prompt}],
                )
                return "".join(b.text for b in msg.content if b.type == "text").strip()
            except Exception as e:  # noqa: BLE001
                last = e
                print(f"  [retry {attempt}/{retries}] claude: {e}", file=sys.stderr)
                time.sleep(5 * attempt)
        raise RuntimeError(f"claude call failed after {retries} attempts: {last}")
