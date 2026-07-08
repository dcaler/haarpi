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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

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
    def coordinator(self, prompt: str, system: str = "", num_ctx: int = 16384,
                    think: bool = True) -> str:
        # think defaults ON: the coordinator does judgement-heavy work (synthesis,
        # planning, substantive critique) where a reasoning model's scratchpad
        # changes the answer. Mechanical coordinator calls (the lint pass) opt out
        # with think=False. No-op on non-reasoning models and the Claude backend.
        if self.backend == "claude":
            return self._claude(prompt, system)
        return self._ollama(self.cfg.coordinator_model, prompt, system,
                            num_ctx=num_ctx, temperature=0.2, think=think)

    # ── worker (small, local, parallel) ──────────────────────────────────
    def worker(self, prompt: str, system: str = "", num_ctx: int = 8192) -> str:
        # Never think: workers do high-volume, near-deterministic scoring where a
        # scratchpad is pure waste (and turned a 0.5s score into ~120s on the M60s).
        return self._ollama(self.cfg.worker_model, prompt, system,
                            num_ctx=num_ctx, temperature=0.1, think=False)

    def worker_map(self, jobs: list[tuple[str, str]], num_ctx: int = 8192,
                   desc: str = "") -> list[str]:
        """Run worker() over (system, prompt) jobs concurrently. Order preserved.

        If `desc` is given, print a live `desc: done/total` progress counter to
        stderr as jobs complete — useful for the long relevance-scoring swarm,
        which is otherwise silent except for retry warnings."""
        n = max(1, int(self.cfg.worker_parallel))
        total = len(jobs)
        results: list[str] = [""] * total
        done = 0

        def _tick() -> None:
            if desc:
                print(f"\r  {desc}: {done}/{total}", end="", file=sys.stderr, flush=True)

        if n == 1:
            for i, (sysmsg, prompt) in enumerate(jobs):
                try:
                    results[i] = self.worker(prompt, sysmsg, num_ctx=num_ctx)
                except Exception as e:  # noqa: BLE001
                    print(f"  [warn] worker job {i} failed: {e}", file=sys.stderr)
                done += 1
                _tick()
            if desc:
                print("", file=sys.stderr)
            return results
        with ThreadPoolExecutor(max_workers=n) as pool:
            futs = {pool.submit(self.worker, prompt, sysmsg, num_ctx): i
                    for i, (sysmsg, prompt) in enumerate(jobs)}
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    results[i] = fut.result()
                except Exception as e:  # noqa: BLE001
                    print(f"  [warn] worker job {i} failed: {e}", file=sys.stderr)
                done += 1
                _tick()
        if desc:
            print("", file=sys.stderr)
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

    # ── context budget ───────────────────────────────────────────────────
    # Ollama silently discards the head of a prompt that exceeds num_ctx. Nothing errors,
    # nothing logs, and the model answers confidently from whatever survived. A 31k-token
    # evidence digest sent at num_ctx=16384 lost its first two-thirds, so a "curated
    # synthesis of 84 sources" was in fact a synthesis of the last 45 — invisibly, for
    # months. Silent truncation of the evidence is the worst failure this tool can have:
    # the output looks founded and is not. So: estimate, and say so.
    #
    # ~4 chars/token is crude but the failure it catches is a 2x-4x overrun, not a 5% one.
    _CHARS_PER_TOKEN = 4
    _RESERVE_FRACTION = 0.35   # leave room for the model's own answer

    def _check_context(self, prompt: str, system: str, num_ctx: int, model: str) -> None:
        est = (len(prompt) + len(system)) // self._CHARS_PER_TOKEN
        budget = int(num_ctx * (1 - self._RESERVE_FRACTION))
        if est <= budget:
            return
        caller = "?"
        try:  # name the call site — "which prompt is too big" is the only useful part
            import traceback
            for fr in reversed(traceback.extract_stack()[:-2]):
                if "rabbithole" in fr.filename and "brain.py" not in fr.filename:
                    caller = f"{Path(fr.filename).name}:{fr.lineno} in {fr.name}()"
                    break
        except Exception:  # noqa: BLE001
            pass
        print(f"  [WARN] prompt ~{est:,} tokens exceeds the {budget:,}-token budget of "
              f"num_ctx={num_ctx:,} ({model}). Ollama will DISCARD the beginning of this "
              f"prompt — evidence at the top will be invisible to the model. "
              f"Called from {caller}.", file=sys.stderr, flush=True)

    # ── backends ─────────────────────────────────────────────────────────
    def _ollama(self, model: str, prompt: str, system: str,
                num_ctx: int, temperature: float, retries: int = 3,
                think: bool = False) -> str:
        import json as _json
        self._check_context(prompt, system, num_ctx, model)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        # think: reasoning models (e.g. qwen3.x) emit a hidden chain-of-thought before
        # the answer. Worth it for coordinator judgement work, ruinous for the worker
        # swarm (a one-token score became ~120s on Tesla M60s). Set per call by the
        # role; ignored by non-reasoning models (llama3.x).
        payload = {"model": model, "messages": messages, "stream": True, "think": think,
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
