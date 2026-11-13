"""Canaries: planted bad claims that measure what the verifiers let through.

Each run plants its own, by code, from that project's own claims and papers (decided
2026-11-13), at about 6% of the claims per check, so what slips is measured on the field being
reviewed. The generators are bench X2/X3's, unchanged:

  number     a number in the claim changed (3 → 6, 0.45 → 7.45)
  direction  one direction word flipped (increases → decreases)
  foreign    another paper's claim, shown with this paper
  setting    the claim moved into another paper's setting ("…, in hospital wards.")
  neighbour  another claim from the same paper, over this claim's passages (step 4 only)
  cited      "X (2006) found that Y" turned into the claim "Y" (step 2b only)

A canary never reaches any output. What it tells us (`slips`) is read two ways at close: the
rate per kind is shown at the gate, and a kind that slips past both verifiers more than one time
in ten, even at the low end of its 95% interval, holds the release. No further check runs on the
real claims of a kind that slipped (2026-11-13: the lexical direction check it had caught only
correct claims on FirmPathways).
"""
from __future__ import annotations

import json
import math
import random
import re
from collections import Counter

SHARE = 0.06                    # canaries per check, as a share of the claims checked
MIN_PER_CHECK = 8
STEP2_KINDS = ("setting", "direction", "number", "foreign")
STEP4_KINDS = ("setting", "direction", "number", "foreign", "neighbour")
HOLD_RATE = 0.10                # a kind holds the release above this, at its lower bound
CONTEXT_SENTENCES = 2           # sentences either side of a cited canary's sentence

DIRECTION = [("increases", "decreases"), ("increase", "decrease"), ("increased", "decreased"),
             ("higher", "lower"), ("more", "less"), ("raises", "lowers"),
             ("positive", "negative"), ("improves", "worsens"), ("reduces", "increases"),
             ("greater", "smaller"), ("outweighs", "is outweighed by"),
             ("benefits", "harms"), ("faster", "slower"), ("larger", "smaller")]
_NUM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?!\w|\.\d)")
_SETTING = re.compile(r",?\s\bin ((?:the |a |an )?[a-z][^,.;()]{6,60}?)(?=[,.;]|$)")


def _bump(n: str) -> str:
    if "." in n:
        a, b = n.split(".")
        return f"{int(a) + 7}.{b}"
    v = int(n)
    return str(v + 3 if v < 10 else v * 2 + 1)


def _copy(c: dict, kind: str) -> dict:
    m = json.loads(json.dumps(c))
    m["canary"] = kind
    return m


def setting_of(claim: str) -> str:
    """The setting a claim names last ("… in SMEs in the nanomaterials field"), or ""."""
    hits = _SETTING.findall(claim or "")
    return hits[-1].strip() if hits else ""


def with_setting(claim: str, setting: str) -> str:
    s = setting.strip().rstrip(".")
    if len(s) > 1 and s[1].islower():
        s = s[0].lower() + s[1:]
    return claim.strip().rstrip(".") + f", in {s}."


def mutate(c: dict, kind: str, others: list[dict]) -> dict | None:
    """A planted bad copy of claim `c`, or None when this kind cannot be made from it.
    `others` are claims from other papers."""
    m = _copy(c, kind)
    if kind == "number":
        nums = [x for x in _NUM.findall(c["claim"]) if not 1500 <= float(x) <= 2100]
        if not nums:
            return None
        m["claim"] = c["claim"].replace(nums[0], _bump(nums[0]), 1)
        return m
    if kind == "direction":
        for a, b in DIRECTION:
            for x, y in ((a, b), (b, a)):
                pat = re.compile(rf"\b{re.escape(x)}\b", re.I)
                if pat.search(c["claim"]):
                    m["claim"] = pat.sub(y, c["claim"], count=1)
                    return m
        return None
    rng = random.Random(f"{c['id']}-{kind}")
    if kind == "foreign":
        pool = [o for o in others if o["claim"] != c["claim"]]
        if not pool:
            return None
        o = rng.choice(pool)
        m["claim"], m["quotes"] = o["claim"], o.get("quotes") or []
        m["foreign_from"] = o["id"]
        return m
    if kind == "setting":
        mine = setting_of(c["claim"]).lower()
        pool = sorted({s for o in others if (s := setting_of(o["claim"])) and s.lower() != mine})
        if not pool:
            return None
        s = rng.choice(pool)
        m["claim"], m["setting"] = with_setting(c["claim"], s), s
        return m
    return None


def neighbour(c: dict, same_paper: list[dict]) -> dict | None:
    """Another claim from the same paper over this one's passages: plausible, on topic, and not
    what the passages say."""
    pool = [o for o in same_paper if o["id"] != c["id"] and o["claim"] != c["claim"]]
    if not pool:
        return None
    m = _copy(c, "neighbour")
    m["claim"] = random.Random(f"{c['id']}-neighbour").choice(pool)["claim"]
    return m


def well_formed(bad: dict, orig: dict) -> bool:
    """A direction flip must change one word and leave a sentence (X2b had garbled ones)."""
    if bad["canary"] != "direction":
        return True
    a, b = orig["claim"].split(), bad["claim"].split()
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1


def how_many(n_claims: int) -> int:
    return max(MIN_PER_CHECK, round(SHARE * n_claims))


def plant(kinds: tuple, claims: list[dict], k: int, seed: str,
          passages: dict[str, list] | None = None) -> list[dict]:
    """`k` canaries, the kinds taken in turn, each from a different real claim. With
    `passages` (claim id → its located passages), only claims that have them are used, and the
    canary carries them (a step-4 canary is judged against its original's passages)."""
    rng = random.Random(seed)
    order = sorted(claims, key=lambda c: c["id"])
    rng.shuffle(order)
    made, per = [], Counter()
    for c in order:
        if len(made) >= k:
            break
        if passages is not None and not passages.get(c["id"]):
            continue
        others = [o for o in claims if o["paper"] != c["paper"]]
        for kind in sorted(kinds, key=lambda x: (per[x], kinds.index(x))):
            if kind == "neighbour":
                bad = neighbour(c, [o for o in claims if o["paper"] == c["paper"]])
            else:
                bad = mutate(c, kind, others)
            if bad and well_formed(bad, c):
                bad["id"] = f"canary|{seed}|{len(made)}"
                bad["orig"] = c["id"]
                if passages is not None:
                    bad["passages"] = passages[c["id"]]
                made.append(bad)
                per[kind] += 1
                break
    return made


# ── cited canaries (step 2b) ────────────────────────────────────────────────────

def sentences(t: str):
    from .facts import _SENTENCE_END
    s = 0
    for m in _SENTENCE_END.finditer(t):
        yield s, m.start() + 1
        s = m.end()
    yield s, len(t)


def context_passage(full: str, start: int, end: int, k: int = CONTEXT_SENTENCES) -> dict:
    """[start, end) widened by `k` sentences each side: where a citation sits."""
    from .facts import _page_at, _passage_text
    spans = list(sentences(full))
    i = next((n for n, (a, b) in enumerate(spans) if b > start), 0)
    j = next((n for n, (a, b) in enumerate(spans) if b >= end), len(spans) - 1)
    a, b = spans[max(0, i - k)][0], spans[min(len(spans) - 1, j + k)][1]
    return {"page": _page_at(full, start), "start": a, "end": b, "text": _passage_text(full[a:b])}


def cited_candidates(papers: dict[str, dict]) -> list[dict]:
    """Sentences that report another work's result ("Raven (2006) found that X"), as the claim
    "X" over the real passage around them: the paper did not find X. A sentence citing one of
    the paper's own authors is left out (it would be a self-citation, not a planted error)."""
    from .facts import _ADOPTED, _REPORTED, _passage_text
    out = []
    for ck, p in sorted(papers.items()):
        full = p["text"]
        own = {str(a).split()[-1].lower() for a in p.get("authors") or [] if str(a).split()}
        for a, b in sentences(full):
            sent = full[a:b]
            m = _REPORTED.search(sent)
            if not m or _ADOPTED.search(sent) or len(sent[:m.start()].split()) > 3:
                continue
            rest = _passage_text(sent[m.end():]).lstrip("…").lstrip()   # a lowercase start gets "…"
            if not re.match(r"that\s", rest):
                continue
            rest = rest[5:].strip()
            if len(rest.split()) < 8:
                continue
            if any(n.lower() in own for n in re.findall(r"[A-Z][\w'’-]+", m.group(0))):
                continue
            out.append({"paper": ck, "canary": "cited", "claim": rest[0].upper() + rest[1:],
                        "passages": [context_passage(full, a, b)]})
    return out


def plant_cited(papers: dict[str, dict], k: int, seed: str) -> list[dict]:
    pool = cited_candidates(papers)
    random.Random(seed).shuffle(pool)
    out = []
    for c in pool[:k]:
        out.append(dict(c, id=f"canary|{seed}|{len(out)}"))
    return out


# ── what slipped, and what follows ──────────────────────────────────────────────

def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """The 95% interval for a rate of k in n."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def slips(kinds: dict[str, str], slipped: set[str]) -> dict[str, dict]:
    """Per kind: how many were planted, how many slipped past both verifiers, the interval,
    and whether the kind holds the release. `kinds` is canary id → kind."""
    out = {}
    for kind, n in sorted(Counter(kinds.values()).items()):
        k = sum(1 for cid, kd in kinds.items() if kd == kind and cid in slipped)
        lo, hi = wilson(k, n)
        out[kind] = {"planted": n, "slipped": k, "low": round(lo, 3), "high": round(hi, 3),
                     "holds": lo > HOLD_RATE}
    return out

