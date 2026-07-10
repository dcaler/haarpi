"""The offline brain: every programmatic LLM call in the pipeline goes through here.

Offline-first is a defining HAARPi goal — this module speaks only to a local
Ollama over stdlib HTTP. No cloud SDK is imported here, ever; a tool that
offers a cloud escape hatch (rabbitHole's optional Claude coordinator) keeps it
in its own binding.

Two surfaces:

  * chat()  — one streaming /api/chat call (ported from raster's generation:
    per-chunk timeout, heartbeat, first-token latency, tri-state think).
  * Brain   — the coordinator/worker roles layer (ported from the
    rabbitHole/raconteur generation) plus local embeddings.

Context policy, both lessons kept:
  * num_ctx omitted  -> pick_num_ctx() sizes the window to the prompt (the KV
    cache is LINEAR in the window, so the window — not the model size — usually
    decides whether a model fits VRAM), warning loudly at the cap.
  * num_ctx explicit -> honored exactly, with the invisible-truncation warning:
    Ollama silently discards the head of an over-length prompt, so a synthesis
    of 84 sources can silently become a synthesis of the last 45.

Retry policy, both philosophies kept: the transport default is retries=0 —
for expensive build tasks the right retry re-composes the prompt at the task
level rather than resending it blind. The Brain role methods pass retries=3,
the judgement-call default the litreview/paper loops have always used.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .runlog import fmt_secs, log


def _env_int(name: str, default: int) -> int:
    """HAARPI_<NAME>, falling back to RASTER_<NAME> (transition compat with the
    standalone installs on the runner box), then the default."""
    for prefix in ("HAARPI_", "RASTER_"):
        v = os.environ.get(prefix + name)
        if v:
            return int(v)
    return default


OLLAMA_TIMEOUT = _env_int("OLLAMA_TIMEOUT", 1800)   # per-chunk read gap, not total
KEEPALIVE = os.environ.get("HAARPI_KEEPALIVE") or os.environ.get("RASTER_KEEPALIVE") or "30m"
HEARTBEAT_SECS = 300

# Context-window sizing. The KV cache is LINEAR in the context window, not the
# parameter count — see pick_num_ctx.
CHARS_PER_TOKEN = _env_int("CHARS_PER_TOKEN", 4)
OUTPUT_HEADROOM_TOKENS = _env_int("OUTPUT_HEADROOM_TOKENS", 4096)
MIN_NUM_CTX = _env_int("MIN_NUM_CTX", 4096)
MAX_NUM_CTX = _env_int("MAX_NUM_CTX", 32768)

_RETRY_BACKOFF_SECS = 5
_RESERVE_FRACTION = 0.35   # explicit-num_ctx budget: leave room for the answer


def estimate_tokens(chars: int) -> int:
    """Cheap char->token estimate (ceil, ~4 chars/token). Deliberately rough — we only
    need the right power-of-two bucket for num_ctx, not an exact count."""
    return -(-max(chars, 0) // CHARS_PER_TOKEN)


def pick_num_ctx(prompt_chars: int, output_tokens: int = OUTPUT_HEADROOM_TOKENS) -> int:
    """Smallest power-of-two context window that holds the prompt PLUS room for the reply.

    The KV cache is LINEAR in this window (a key+value vector per layer per attention
    head for every token), so it can dwarf the model weights: an 8B model — ~5 GB of Q4
    weights — at a 32k default context needs ~32 GB of KV and spills layers to CPU on an
    8 GB card, collapsing generation to <1 tok/s. Size to NEED, round UP (a window
    smaller than prompt+output silently truncates), clamp to MAX_NUM_CTX. Hitting the
    clamp means the prompt itself overflows the budget — the caller logs loudly, never
    a silent truncation here."""
    need = estimate_tokens(prompt_chars) + max(output_tokens, 0)
    ctx = MIN_NUM_CTX
    while ctx < need and ctx < MAX_NUM_CTX:
        ctx *= 2
    return min(ctx, MAX_NUM_CTX)


def normalize_host(raw: str) -> str:
    """Coerce a bind-style host (e.g. '0.0.0.0:11434') into a usable client URL."""
    raw = (raw or "").strip().rstrip("/")
    if "://" not in raw:
        raw = "http://" + raw
    parts = urlsplit(raw)
    host = parts.hostname or "127.0.0.1"
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    port = parts.port or 11434
    return urlunsplit((parts.scheme or "http", f"{host}:{port}", parts.path, "", ""))


def _warn_explicit_ctx(prompt_chars: int, num_ctx: int, model: str, tool: str) -> None:
    """The invisible-truncation warning for an explicitly-sized window.

    Ollama's response to an over-length prompt is to silently discard the head — no
    error, no log. Evidence at the top of the prompt becomes invisible to the model.
    This does not truncate or fail; it makes the invisible visible."""
    est = prompt_chars // CHARS_PER_TOKEN
    budget = int(num_ctx * (1 - _RESERVE_FRACTION))
    if est <= budget:
        return
    caller = "?"
    try:  # name the call site — "which prompt is too big" is the only useful part
        import traceback
        for fr in reversed(traceback.extract_stack()[:-2]):
            if "brain.py" not in fr.filename and "site-packages" not in fr.filename:
                caller = f"{Path(fr.filename).name}:{fr.lineno} in {fr.name}()"
                break
    except Exception:  # noqa: BLE001
        pass
    print(f"  [WARN] prompt ~{est:,} tokens exceeds the {budget:,}-token budget of "
          f"num_ctx={num_ctx:,} ({model}). Ollama will DISCARD the beginning of this "
          f"prompt — evidence at the top will be invisible to the model. "
          f"Called from {caller}.", file=sys.stderr, flush=True)


def _stream_once(host: str, model: str, payload: dict, label: str, tool: str,
                 timeout: int) -> str:
    req = urllib.request.Request(
        f"{host.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    chunks: list[str] = []
    think_chars = 0
    start = last_beat = time.monotonic()
    first_content_at = None
    final: dict = {}
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for line in resp:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("error"):
                raise RuntimeError(f"ollama error: {obj['error']}")
            msg = obj.get("message", {})
            piece = msg.get("content", "")
            think_chars += len(msg.get("thinking", "") or "")
            if piece and first_content_at is None:
                first_content_at = time.monotonic()
                log(f"  ollama {model}: first OUTPUT token after "
                    f"{fmt_secs(first_content_at - start)} "
                    f"(prompt eval + {think_chars} chars of reasoning)", tool)
            chunks.append(piece)
            now = time.monotonic()
            if now - last_beat >= HEARTBEAT_SECS:
                chars = sum(len(c) for c in chunks)
                phase = "thinking" if first_content_at is None else "writing"
                log(f"  ollama {model}: {phase}… {chars} output + {think_chars} "
                    f"reasoning chars in {fmt_secs(now - start)}", tool)
                last_beat = now
            if obj.get("done"):
                final = obj
                break
    text = "".join(chunks)
    dur = time.monotonic() - start
    n_tok = final.get("eval_count")
    tps = f"{n_tok / dur:.1f} tok/s" if n_tok and dur else "?"
    log(f"← ollama {model} {label}: done in {fmt_secs(dur)} — {len(text)} output chars, "
        f"{think_chars} reasoning chars, {n_tok or '?'} tokens ({tps})", tool)
    return text


def chat(host: str, model: str, messages: list, *, label: str = "",
         think: bool | None = None, num_ctx: int | None = None,
         temperature: float = 0.1, keep_alive: str | None = None,
         retries: int = 0, tool: str = "haarpi",
         timeout: int | None = None) -> str:
    """One streaming chat call. Streaming keeps the socket fed with tokens so the
    per-chunk timeout applies to the gap between chunks, not the whole (possibly
    long, cold) generation; a heartbeat keeps a long run visibly alive."""
    host = normalize_host(host)
    timeout = timeout or OLLAMA_TIMEOUT
    prompt_chars = sum(len(m.get("content", "")) for m in messages)

    if num_ctx is None:
        num_ctx = pick_num_ctx(prompt_chars)
        need = estimate_tokens(prompt_chars) + OUTPUT_HEADROOM_TOKENS
        if need > MAX_NUM_CTX:
            # the reply shares the window, so prompt+output must FIT or the context
            # silently truncates (mysterious quality drop, no error). Say so loudly.
            log(f"  WARNING: prompt+output (~{need} tok) exceeds the num_ctx cap "
                f"{MAX_NUM_CTX} — the window will TRUNCATE. Trim the prompt or raise "
                f"HAARPI_MAX_NUM_CTX if the card has the VRAM (KV cache is linear in "
                f"num_ctx).", tool)
    else:
        _warn_explicit_ctx(prompt_chars, num_ctx, model, tool)

    log(f"→ ollama {model} {label}: requesting (prompt {prompt_chars} chars "
        f"~{estimate_tokens(prompt_chars)} tok, num_ctx={num_ctx}), "
        f"per-chunk timeout {timeout}s …", tool)

    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": True,
        "keep_alive": keep_alive or KEEPALIVE,
        "options": {"temperature": temperature, "num_ctx": num_ctx},
    }
    if think is not None:                 # omit -> model default; set only to force off/on
        payload["think"] = think
        log(f"  ollama {model}: think={think}", tool)

    last: Exception | None = None
    for attempt in range(1, max(retries, 0) + 2):
        try:
            return _stream_once(host, model, payload, label, tool, timeout)
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt > retries:
                raise
            log(f"  [retry {attempt}/{retries}] ollama {model}: {e}", tool)
            time.sleep(_RETRY_BACKOFF_SECS * attempt)
    raise RuntimeError(f"ollama call failed: {last}")  # unreachable; keeps type-checkers calm


# ── the roles layer ──────────────────────────────────────────────────────────

class Brain:
    """coordinator/worker roles over chat(), plus local embeddings.

    * coordinator() — judgement-heavy, low-volume work (synthesis, planning).
    * worker()      — small, high-volume work (per-paper annotation, scoring).
    worker_map() runs many worker() calls concurrently (the swarm); real
    parallelism requires Ollama configured with OLLAMA_NUM_PARALLEL>=N.
    """

    def __init__(self, url: str, coordinator_model: str, worker_model: str, *,
                 embed_model: str = "",
                 coordinator_temperature: float = 0.2,
                 worker_temperature: float = 0.1,
                 worker_parallel: int = 1,
                 think: bool | None = None,
                 tool: str = "haarpi"):
        self.url = url
        self.coordinator_model = coordinator_model
        self.worker_model = worker_model
        self.embed_model = embed_model
        self.coordinator_temperature = coordinator_temperature
        self.worker_temperature = worker_temperature
        self.worker_parallel = max(1, int(worker_parallel))
        self.think = think
        self.tool = tool

    def _messages(self, prompt: str, system: str) -> list[dict]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    def coordinator(self, prompt: str, system: str = "", num_ctx: int | None = 16384,
                    think: bool | None = None, retries: int = 3) -> str:
        return chat(self.url, self.coordinator_model, self._messages(prompt, system),
                    think=self.think if think is None else think, num_ctx=num_ctx,
                    temperature=self.coordinator_temperature, retries=retries,
                    tool=self.tool)

    def worker(self, prompt: str, system: str = "", num_ctx: int | None = 8192,
               think: bool | None = None, retries: int = 3) -> str:
        return chat(self.url, self.worker_model, self._messages(prompt, system),
                    think=self.think if think is None else think, num_ctx=num_ctx,
                    temperature=self.worker_temperature, retries=retries,
                    tool=self.tool)

    def worker_map(self, jobs: list[tuple[str, str]], num_ctx: int | None = 8192,
                   desc: str = "") -> list[str]:
        """Run worker() over (system, prompt) jobs concurrently. Order preserved;
        a failed job yields "" and never tanks the batch. If `desc` is given,
        print a live `desc: done/total` counter to stderr."""
        n = self.worker_parallel
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
        else:
            with ThreadPoolExecutor(max_workers=n) as pool:
                futs = {pool.submit(self.worker, prompt, sysmsg, num_ctx=num_ctx): i
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
            body = json.dumps({"model": self.embed_model,
                               "prompt": text[:limit]}).encode()
            req = urllib.request.Request(
                f"{self.url.rstrip('/')}/api/embeddings", data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    return json.loads(resp.read())["embedding"]
            except urllib.error.HTTPError as e:
                detail = (e.read() or b"").decode(errors="replace").lower()
                if e.code == 500 and "context" in detail and "length" in detail and limit > 10:
                    limit //= 2          # input too long -> shrink and retry
                    continue
                if e.code >= 500 and transient < 4:
                    transient += 1        # model loading / transient -> back off and retry
                    time.sleep(3 * transient)
                    continue
                raise RuntimeError(f"embedding failed ({e.code}): {detail[:200]}") from None
            except (urllib.error.URLError, OSError) as e:
                transient += 1
                if transient <= 4:
                    time.sleep(3 * transient)
                    continue
                raise RuntimeError(f"embedding network error: {e}") from e

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = [[] for _ in texts]
        with ThreadPoolExecutor(max_workers=self.worker_parallel) as pool:
            futs = {pool.submit(self.embed, t or " "): i for i, t in enumerate(texts)}
            for fut in futs:
                i = futs[fut]
                try:
                    out[i] = fut.result()
                except Exception:  # noqa: BLE001
                    out[i] = []  # unembeddable doc -> 0 similarity, never tanks the batch
        return out
