"""razzle.compose — author a deck spec from the paper's narrative + figures + claims (the LLM).

The one-pager is the talk's spine; razzle RE-PRESENTS it, it does not re-argue. The model writes the
STRUCTURE (which slides, what each says, which figure it shows) as JSON, grounded in the narrative and
the real claims — never inventing a number. The output is parsed, validated, and NORMALISED (a valid
role per slide, a title slide leading, figure refs that actually exist); a bad parse degrades to a
minimal title-only deck rather than crashing. The result is exactly what `render_deck` consumes.
"""

from __future__ import annotations

import json
import re

from razzle import formats as _formats

_ROLES = {"title", "figure", "content"}

_SYS = ("You turn a finished paper into a conference slide deck. You output JSON only — never prose. "
        "The narrative is the talk's spine: re-present it, don't re-argue. Ground every slide in the "
        "narrative and the claims, and never state a number that is not in the claims.")

_PROMPT = """Turn this paper into a slide deck: a {fmt}{mins}, at most {max_slides} slides.

NARRATIVE (the SPINE — the one-pager): use this for the deck's arc and slide order — motivation →
question → approach → results → takeaway. It decides WHICH slides and in what sequence.
{narrative}

FULL PAPER (the SUBSTANCE): draw the actual claims, framing, key citations and secondary results
from here to fill the slides the spine calls for. Re-present it; do not re-argue or add beyond it.
{manuscript}

AVAILABLE FIGURES (a figure slide shows exactly ONE, referenced by id):
{figures}

KEY CLAIMS / NUMBERS (use verbatim; invent nothing):
{claims}

Output ONLY JSON:
{{"slides": [
  {{"role": "title",   "title": "...", "subtitle": "..."}},
  {{"role": "figure",  "title": "...", "figure": "<id>", "citation": "...", "notes": "..."}},
  {{"role": "content", "title": "...", "bullets": ["...", "..."], "notes": "..."}}
]}}
Rules: open with ONE title slide; one idea per slide; terse bullets; the detail goes in `notes`
(speaker notes); a figure slide names an EXISTING figure id; never a number not in the claims.
A figure slide's MESSAGE is its `title` — write no prose caption; `citation` is a bare source
reference only (e.g. "[Kramers 1940]") or omit it."""


def _parse(reply: str) -> dict:
    m = re.search(r"\{.*\}", reply or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def _normalise(slides, figure_ids: set[str]) -> list[dict]:
    out: list[dict] = []
    for s in slides or []:
        if not isinstance(s, dict):
            continue
        role = s.get("role") if s.get("role") in _ROLES else "content"
        slide = {"role": role, "title": str(s.get("title", "")).strip()}
        for k in ("subtitle", "citation", "notes"):
            if s.get(k):
                slide[k] = str(s[k]).strip()
        if s.get("bullets"):
            slide["body"] = [str(b).strip() for b in s["bullets"] if str(b).strip()]
        if role == "figure":
            if s.get("figure") in figure_ids:
                slide["figure"] = s["figure"]
            else:
                slide["role"] = "content"          # a figure we don't have → a plain slide
        out.append(slide)
    if not out or out[0]["role"] != "title":       # a talk always opens on a title slide
        out.insert(0, {"role": "title", "title": (out[0]["title"] if out else "Untitled talk")})
    return out


def compose(brain, narrative: str, figures: list[dict], claims: str = "", *,
            manuscript: str = "", fmt: str = "longtalk", max_slides: int | None = None) -> list[dict]:
    """Author the deck spec, sized to the presentation `fmt` (razzle.formats — 1 slide/minute).
    `narrative` (the one-pager) is the spine; `manuscript` (the full paper) is the substance the
    slides draw their real claims from; `figures` is [{id, caption}] of what the pool holds; `claims`
    are the real numbers verbatim. `max_slides` overrides the format's budget. Returns a normalised
    list of slides for `render_deck`."""
    budget = max_slides or _formats.slide_budget(fmt) or 15   # poster/unknown → a sane default
    mins = _formats.minutes(fmt)
    fig_lines = "\n".join(f"- {f['id']}: {f.get('caption', '')}" for f in (figures or [])) or "(none)"
    ids = {f["id"] for f in (figures or [])}
    reply = brain.coordinator(
        _PROMPT.format(fmt=fmt, mins=(f" (~{mins} minutes)" if mins else ""), max_slides=budget,
                       narrative=(narrative or "")[:6000],
                       manuscript=(manuscript or "(not available — compose from the spine)")[:9000],
                       figures=fig_lines, claims=(claims or "(none provided)")[:4000]), _SYS)
    return _normalise(_parse(reply).get("slides", []), ids)[:budget]
