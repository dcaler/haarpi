from __future__ import annotations
import json
import sys
import time
from .log import log
from concurrent.futures import ThreadPoolExecutor
import httpx
from .config import GlobalConfig

_RETRIES = 3
_BACKOFF = 5


class Brain:
    _CHARS_PER_TOKEN = 4
    _RESERVE_FRACTION = 0.35   # leave room for the model's own answer

    def __init__(self, cfg: GlobalConfig, coordinator: str | None = None, think: bool = False):
        self._url = cfg.ollama_url
        self._coord = coordinator or cfg.coordinator_model
        self._worker_model = cfg.worker_model
        self._think = think

    def coordinator(self, prompt: str, system: str = "", num_ctx: int = 16384) -> str:
        return self._call(self._coord, prompt, system, num_ctx, temperature=0.4)

    def worker(self, prompt: str, system: str = "", num_ctx: int = 4096) -> str:
        return self._call(self._worker_model, prompt, system, num_ctx, temperature=0.1)

    def worker_map(self, jobs: list[tuple[str, str]], num_ctx: int = 4096) -> list[str]:
        """Run multiple worker calls in parallel. Each job is (system, prompt)."""
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = [ex.submit(self.worker, prompt, system, num_ctx) for system, prompt in jobs]
        out = []
        for f in futs:
            try:
                out.append(f.result())
            except Exception as e:
                log(f"[warn] worker failed: {e}")
                out.append("")
        return out

    def _check_context(self, prompt: str, system: str, num_ctx: int, model: str) -> None:
        """Warn when a prompt will overflow num_ctx.

        Ollama's response to an over-length prompt is to silently discard the head —
        no error, no log. Evidence at the top of the prompt becomes invisible to the
        model while the prompt's own rules argue downstream of a hard cut. This does
        not truncate or fail; it makes the invisible visible.
        """
        est = (len(prompt) + len(system)) // self._CHARS_PER_TOKEN
        budget = int(num_ctx * (1 - self._RESERVE_FRACTION))
        if est <= budget:
            return
        caller = "?"
        try:  # name the call site — "which prompt is too big" is the only useful part
            import traceback
            from pathlib import Path
            for fr in reversed(traceback.extract_stack()[:-2]):
                if "raconteur" in fr.filename and "brain.py" not in fr.filename:
                    caller = f"{Path(fr.filename).name}:{fr.lineno} in {fr.name}()"
                    break
        except Exception:
            pass
        log(f"[warn] prompt ~{est:,} tokens exceeds the {budget:,}-token budget of "
            f"num_ctx={num_ctx:,} ({model}). Ollama will DISCARD the beginning of this "
            f"prompt — evidence at the top will be invisible to the model. "
            f"Called from {caller}.")

    def _call(self, model: str, prompt: str, system: str, num_ctx: int, temperature: float) -> str:
        self._check_context(prompt, system, num_ctx, model)
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict = {
            "model": model,
            "messages": messages,
            "stream": True,
            "think": self._think,
            "options": {"temperature": temperature, "num_ctx": num_ctx},
        }

        for attempt in range(1, _RETRIES + 1):
            try:
                buf = ""
                stats: dict = {}
                with httpx.stream(
                    "POST",
                    f"{self._url}/api/chat",
                    json=payload,
                    timeout=httpx.Timeout(2400.0, connect=60.0),
                ) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        chunk = json.loads(line)
                        buf += chunk.get("message", {}).get("content", "")
                        if chunk.get("done"):
                            stats = {
                                "prompt_tok": chunk.get("prompt_eval_count", 0),
                                "gen_tok":    chunk.get("eval_count", 0),
                                "gen_s":      chunk.get("eval_duration", 0) / 1e9,
                            }
                            break

                if stats.get("gen_s"):
                    tps = stats["gen_tok"] / stats["gen_s"]
                    print(
                        f"[raconteur] {stats['gen_tok']} tok @ {tps:.1f} tok/s"
                        f"  (prompt {stats['prompt_tok']} tok)",
                        file=sys.stderr,
                    )

                return buf

            except Exception as exc:
                if attempt == _RETRIES:
                    raise
                log(f"[warn] ollama attempt {attempt}/{_RETRIES}: {exc}")
                time.sleep(_BACKOFF * attempt)

        return ""
