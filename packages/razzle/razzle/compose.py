"""razzle.compose — author a deck spec from the paper's narrative + figures + claims (the LLM).

The one-pager is the talk's spine; razzle RE-PRESENTS it, it does not re-argue. The model writes the
STRUCTURE (which slides, what each says, which figure it shows) as JSON, grounded in the narrative and
the real claims — never inventing a number. The output is parsed, validated, and NORMALISED (a valid
role per slide, a title slide leading, figure refs that actually exist); a bad parse degrades to a
minimal title-only deck rather than crashing. The result is exactly what `render_deck` consumes.

WHAT A SLIDE IS HERE. A slide is a projected image with a claim over it — not a document. The budgets
below are hard because the failure they prevent is the only failure that matters in practice: prose
migrating onto the screen, where the audience reads it instead of listening. So the title carries the
slide's CLAIM (not its topic), bullets are fragments and there are at most three, and a slide that can
show something shows it. There are NO speaker notes: notes are where the essay goes when the slide
refuses it, and their absence is the point — what will not fit is spoken, not written.

WHAT THE NOTES PANE IS FOR INSTEAD. A talk always has more slides than the paper has figures, and the
bullet slides that result are the deck's weakest. So a slide with nothing to show may name what it
WOULD show: an `illustration`, one line briefing a picture that does not exist yet — a schematic, a
diagram, a musical example, a photograph. It renders into the notes as a production TODO for whoever
draws it, never as a sentence for the speaker. It is a request for art, not a script.
"""

from __future__ import annotations

import json
import re

from razzle import formats as _formats

# `split` is text and a figure side by side — the workhorse, because it lets a slide make its
# point AND show the evidence for it instead of alternating between the two.
# `acknowledgements` is in the set so a spec that already carries one survives normalisation — but
# it is NOT offered to the model: the closing thanks are facts (who funded this, whose logos go up),
# stamped deterministically by `gather.apply_acknowledgements`.
_ROLES = {"title", "figure", "split", "content", "acknowledgements"}
MAX_BULLETS = 3          # per slide; more than this is a document, not a slide
MAX_BULLET_WORDS = 9     # a fragment, not a sentence
MAX_TITLE_WORDS = 9      # the claim, stated once

_SYS = ("You turn a finished paper into a conference slide deck. You output JSON only — never prose. "
        "The narrative is the talk's spine: re-present it, don't re-argue. Ground every slide in the "
        "narrative and the claims, and never state a number that is not in the claims. "
        "You are writing SLIDES, not a handout: the speaker says the sentences, the slide shows the "
        "claim and the evidence. Text on a slide competes with the speaker for the room's attention, "
        "so every word has to earn its place.")

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
  {{"role": "figure",  "title": "...", "figure": "<id>"}},
  {{"role": "split",   "title": "...", "bullets": ["...", "..."], "figure": "<id>"}},
  {{"role": "content", "title": "...", "bullets": ["...", "..."], "illustration": "..."}}
]}}

HOW TO BUILD IT — these are budgets, not suggestions. A slide that breaks them is a worse slide.

TITLE — at most {max_title_words} words, and it states the slide's CLAIM, not its topic.
  "Three regimes: freeze, settle, churn"          <- a claim; the audience learns something
  "Results of the parameter sweep"                <- a topic; says nothing, wastes the line
  Write it as the sentence you would say out loud if you could only say one.

BULLETS — at most {max_bullets} per slide, at most {max_bullet_words} words each, and NO slide is
required to have any. Fragments, not sentences: strip "we find that", "this shows", articles,
and every clause that only sets up the next one. If a point needs a sentence to survive, it is
something you SAY while the slide shows the evidence — do not write it down.
  "Churn never stops"                             <- keep
  "The simulation shows that churn never stops, which means stillness is not recovery"  <- speak it

SHOW, DON'T LIST — prefer `split` (a point beside its figure) and `figure` (the figure IS the
slide) over `content`. Use `content` only where there is genuinely nothing to show: a definition,
a contribution list, the closing claim. Every figure in AVAILABLE FIGURES must appear on at least
one slide, and a figure may be shown more than once when the talk returns to it to make a new
point — that is a normal move in a talk, not repetition.

ARC — open with ONE title slide, then motivation -> question -> approach -> results -> takeaway,
following the spine. One idea per slide. Never a number that is not in the claims.

NUMBERS — the talk's results slides carry the real ones. At least one slide must state a figure
from KEY CLAIMS verbatim ("advantage +0.176 in the band", "mean -0.04 across the sweep"). A
results slide that says only "positive inside the band" has thrown away the finding. Never a
number that is not in KEY CLAIMS.

CITATION — every figure in AVAILABLE FIGURES is THIS paper's own work, so a slide that shows one
carries NO citation: naming someone else beside our own result misattributes it. Cite only on a
slide that has no figure, and then only as a bare source reference ("[Kramers 1940]"), never as a
prose caption.

CASE — write titles and bullets in sentence case ("Three regimes emerge across parameters"), not
Title Case. This is a talk, not a headline.

ILLUSTRATION — a `content` slide has nothing to show, which is what makes it the weak one. Where a
picture WOULD carry it, add `illustration`: one line describing the picture to draw — a schematic,
a diagram, a worked example, a photograph. Concrete enough to hand to an illustrator ("a piano roll
with the target phrase above a scrambled one"), and never a chart of data we do not have. Omit it
where a picture genuinely would not help. It is a request for art, not a sentence to be spoken.

There are NO speaker notes. Do not emit a `notes` field. What does not fit on the slide is what
the speaker says; it is not written down anywhere."""


def _parse(reply: str) -> dict:
    m = re.search(r"\{.*\}", reply or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text).lower())


def _drop_echoed_bullets(slide: dict) -> None:
    """Drop a bullet that only says the title again.

    The title already carries the slide's claim, so a bullet repeating it spends a line saying
    nothing — and it is a habit models fall into when a slide has one idea and three bullet slots
    to fill ("Tolerance manipulation enables harmonic modulation" over "Tolerance manipulation
    enables modulation"). The test is whether the bullet's words are a SUBSET of the title's: an
    exact-match test misses the near-repeat above, and anything looser starts throwing away bullets
    that share a noun with the title and go on to say something. A bullet that adds one word lives.
    """
    title = set(_words(slide.get("title", "")))
    if not title or not slide.get("body"):
        return
    kept = [b for b in slide["body"] if not (_words(b) and set(_words(b)) <= title)]
    if kept:
        slide["body"] = kept
    else:
        slide.pop("body")


def normalise(slides, figure_ids: set[str]) -> list[dict]:
    """Coerce the model's JSON into what `render_deck` consumes.

    The bullet BUDGET is enforced here; bullet WORDING is not. Dropping a fourth bullet loses a
    point the author can put back, and the slide is still a slide. Truncating a bullet to nine
    words produces a fragment that means something else — an enforcement worse than the problem.
    So counts are hard here and length is the prompt's job.

    `notes` is dropped wherever it appears: a deck has no speaker notes, and a model that emits
    them anyway must not have them reach the render. `illustration` survives only on a slide with
    no figure — a brief for a picture we already have is noise in the notes pane.

    A `citation` beside one of OUR OWN figures is dropped outright. Every id in `figure_ids` comes
    from this project's figure pool, so it is this paper's own result; the descriptor renders the
    citation in the caption strip under it, where a literature reference reads as "this figure is
    theirs". That is a misattribution of the authors' own work, and it is not a wording problem
    the prompt can be trusted with — a live deck shipped `[Schelling 1971]` under our own sweep.
    """
    out: list[dict] = []
    for s in slides or []:
        if not isinstance(s, dict):
            continue
        role = s.get("role") if s.get("role") in _ROLES else "content"
        slide = {"role": role, "title": str(s.get("title", "")).strip()}
        for k in ("subtitle", "citation", "illustration"):
            if s.get(k):
                slide[k] = str(s[k]).strip()
        # `bullets` is what the prompt asks a model for; `body` is what the renderer reads and
        # what a hand-authored spec.json already contains. Accept either — reading only `bullets`
        # would silently strip every bullet out of a spec written the other way.
        bullets = s.get("bullets") or s.get("body")
        if bullets:
            body = [str(b).strip() for b in bullets if str(b).strip()]
            if body:
                slide["body"] = body[:MAX_BULLETS]
        if role in ("figure", "split"):
            if s.get("figure") in figure_ids:
                slide["figure"] = s["figure"]
                slide.pop("citation", None)     # our own figure: a citation misattributes it
            else:
                # a figure we don't have: `split` keeps its bullets and becomes a plain slide;
                # a bare `figure` slide has nothing left but its title, which is still a claim
                slide["role"] = "content"
        if slide.get("figure"):
            slide.pop("illustration", None)     # it already shows something
        _drop_echoed_bullets(slide)
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
                       max_bullets=MAX_BULLETS, max_bullet_words=MAX_BULLET_WORDS,
                       max_title_words=MAX_TITLE_WORDS,
                       narrative=(narrative or "")[:6000],
                       manuscript=(manuscript or "(not available — compose from the spine)")[:9000],
                       figures=fig_lines, claims=(claims or "(none provided)")[:4000]), _SYS)
    return normalise(_parse(reply).get("slides", []), ids)[:budget]
