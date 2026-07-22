"""Deterministic guards — the polestar, made mechanical.

raconteur exists to produce a grounded, verifiable manuscript: every substantive claim
traceable to the material it was given (litreview, methods writeup, results, one-pager),
and every ``[@citekey]`` resolvable against ``refs.bib``. A Methods paragraph describing an
algorithm the writeup never mentions, or a Background paragraph that cites nothing, is a
defect — not a matter of taste.

The division of labour, applied everywhere in the codebase:

    Python decides THAT something is wrong, precisely, and states it as an imperative.
    The LLM decides only what cannot be computed — whether prose reads as synthesis rather
    than a list, whether a claim is actually supported.

Two families:

  VERIFIABILITY — a claim severed from what grounds it. A dropped [@citekey], an
    unresolvable key, a dropped equation. Each looks founded and is not. These run on the
    DRAFT path, where fuller grounding is the goal.

  MINIMALITY — a redline is surgical. The set of sentences a reviser touched is computed,
    not estimated, so "you rewrote sentences the comment did not bear on" stops being an
    LLM judgement call. These run on the REDLINE path.

Scoping rule (important): density guards must NEVER run on the redline path. A comment like
"tighten this sentence" would otherwise cause the reviser to inject citations into the
paragraph to satisfy a citation floor — density demands more sources, minimality forbids
collateral change, and both are correct. Different passes, different guard sets. Every guard
below is tagged with its PHASE.

Second scoping rule: the citation floor is gated on SECTION KIND. A Methods or Results
paragraph is grounded in the methods writeup and the results files, not in the bibliography.
Demanding citations there is wrong.

Everything here is a pure function over text and the parsed bib: no I/O, no LLM, no docx.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

# ── primitives ────────────────────────────────────────────────────────────────
# Canonical here so paper/focus/redline share one definition of "a citation",
# "a sentence", "a section kind", and "an opaque atom".

# A pandoc citation tag, e.g. [@schelling1971] or the grouped [@a; @b].
CITE_TAG_RE = re.compile(r"\[@[^\]\s]+\]")

# A citation written in author-year narrative form instead of a [@citekey] tag. Invisible
# to the citekey-keyed bibliography, so it silently unverifies the claim it supports.
AUTHOR_YEAR_RE = re.compile(r"[A-Z][a-z]+(?:\s+et al\.|’s|'s)?\s*\((?:19|20)\d\d")

# An opaque non-text atom (an equation, a footnote reference, a drawing) standing in for a
# docx element the LLM must carry through verbatim but must never author. See redline.py.
SENTINEL_RE = re.compile(r"⟦[a-z]+:\d+⟧")

# Any digit — the cheapest mechanical proxy for "this paragraph reports a value".
NUMERAL_RE = re.compile(r"\d")


def all_citekeys(text: str) -> list[str]:
    """Every individual citekey, splitting grouped citations like [@a; @b; @c].

    The naive ``[@([^\\]]+)]`` capture treats a grouped bracket as one key, so any source
    cited only inside a multi-citation bracket would be dropped from the bibliography.
    """
    keys: list[str] = []
    for grp in re.findall(r"\[@([^\]]+)\]", text):
        for part in grp.split(";"):
            k = part.strip().lstrip("@").strip()
            if k:
                keys.append(k)
    return keys


from haarpi.text import sentence_units  # noqa: F401 — the one shared splitter


def sentinels(text: str) -> list[str]:
    return SENTINEL_RE.findall(text)


# ── section kinds ─────────────────────────────────────────────────────────────
# One definition, imported back by paper.py and focus.py. A section's kind decides which
# material grounds it, and therefore which guards may run on it.

LITREV_KW = {"background", "related", "literature", "prior", "review", "introduction"}
CODE_KW = {"method", "approach", "implement", "model", "framework",
           "algorithm", "system", "pipeline", "design"}
RESULTS_KW = {"result", "evaluation", "experiment", "finding",
              "outcome", "performance", "validation", "empirical"}
# Discussion/Conclusion: NOT a section_kind (it stays "other", so its citation floor holds),
# only a context selector — a Discussion connects its findings back to the literature, so it
# needs the litreview to cite against. Kept out of section_kind deliberately: were it a kind,
# it would have to be one that still demands citations, and "other" already does that.
DISCUSSION_KW = {"discussion", "conclusion", "concluding"}

_REFERENCES_RE = re.compile(r"^\d*\.?\s*references?\b", re.IGNORECASE)
_ABSTRACT_RE = re.compile(r"^\d*\.?\s*abstract\b", re.IGNORECASE)
_ACKNOWLEDGEMENTS_RE = re.compile(r"^\d*\.?\s*acknowledge?ments?\b", re.IGNORECASE)


def is_references(heading: str) -> bool:
    return bool(_REFERENCES_RE.match(heading))


def is_abstract(heading: str) -> bool:
    return bool(_ABSTRACT_RE.match(heading))


def is_acknowledgements(heading: str) -> bool:
    return bool(_ACKNOWLEDGEMENTS_RE.match(heading))


def section_kind(heading: str) -> str:
    """Classify a heading → 'abstract' | 'acknowledgements' | 'litrev' | 'methods' |
    'results' | 'references' | 'other'.

    Order matters: results before methods, because "experimental design" and "model
    evaluation" each hit both keyword sets and the later-stage kind should win.
    """
    if is_references(heading):
        return "references"
    if _ABSTRACT_RE.match(heading):
        return "abstract"
    if _ACKNOWLEDGEMENTS_RE.match(heading):
        return "acknowledgements"
    h = heading.lower()
    if any(kw in h for kw in RESULTS_KW):
        return "results"
    if any(kw in h for kw in CODE_KW):
        return "methods"
    if any(kw in h for kw in LITREV_KW):
        return "litrev"
    return "other"


def budget_kind(heading: str) -> str:
    """Classify a heading for BUDGET purposes — as ``section_kind``, but Introduction and
    Background are told apart and a Conclusion is named.

    ``section_kind`` pools both into "litrev" because for citation and redline purposes
    they behave alike. For allocation they do not: an Introduction is motivation, preview
    and roadmap — inherently brief — while a Background must give the reader every
    literature they need. Sharing one share made them compete, so whichever had more
    subsections took more of it, and collapsing a section's subsections cut its own budget.
    """
    if _is_conclusion(heading):
        return "conclusion"
    k = section_kind(heading)
    if k != "litrev":
        return k
    # "Introduction" names itself; everything else in the litrev family is background.
    return "intro" if "introduction" in heading.lower() else "litrev"


def expects_citations(kind: str) -> bool:
    """Does the bibliography GROUND this kind of section — i.e. is a paragraph without a
    citation a defect here?

    Methods and Results are grounded in the writeup and the results files, not in the
    literature. A Methods section may still cite — when the method is an offshoot of prior
    work, its provenance belongs in the text — but citing is not its JOB, so an uncited
    paragraph there is not a defect. That distinction is the difference between a floor and
    a permission; see ``may_cite``.

    A Conclusion restates what the paper has already argued and pointed forward from. The
    claims were made, and cited, where they were first established; requiring citations
    again is what turns two paragraphs of restatement into a third Discussion — which is
    what it became, three drafts running.

    An abstract summarises rather than cites, References are not prose, and
    Acknowledgements credit people rather than literature.
    """
    return kind in ("litrev", "intro", "other")


def may_cite(kind: str) -> bool:
    """Is the literature RELEVANT to this kind of section, whether or not it must cite?

    Decides what context a section is handed, which is a different question from whether an
    uncited paragraph is a defect. Everything except the pure boilerplate may cite.
    """
    return kind not in ("references", "acknowledgements", "abstract")


def _is_body(p: "Paragraph") -> bool:
    """Front matter (title block, metadata) precedes the first ``## `` heading and carries
    section -1. It is not body prose, and a citation floor misfires on it — found by running
    this battery against a real rabbitHole document whose metadata block was flagged uncited.
    """
    return p.section >= 0


# ── findings ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Finding:
    """One guard failure, phrased so it can be handed straight to a reviser.

    ``imperative`` is what the model is told to do about it — never a question. ``where``
    locates it for a human reading the run log. ``section`` locates it for the machine: a
    repair re-drafts one section, not the whole paper, so the finding has to say which one.
    ``None`` means the finding is about the manuscript as a whole.
    """
    kind: str
    where: str
    imperative: str
    section: int | None = None

    def __str__(self) -> str:
        return f"{self.where}: {self.imperative}"


def by_section(findings: list[Finding]) -> dict[int, list[Finding]]:
    """Group section-scoped findings for repair. Manuscript-wide findings are dropped —
    the caller handles those (they have no single section to re-draft)."""
    out: dict[int, list[Finding]] = {}
    for f in findings:
        if f.section is not None and f.section >= 0:
            out.setdefault(f.section, []).append(f)
    return out


# ── manuscript structure ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Paragraph:
    section: int            # index of the enclosing "## " section; -1 = preamble
    index: int              # paragraph position within its section, 0-based
    text: str
    keys: tuple[str, ...]   # citekeys, in order of appearance
    heading: str = ""       # enclosing "## " heading text

    @property
    def distinct(self) -> frozenset[str]:
        return frozenset(self.keys)

    @property
    def kind(self) -> str:
        # budget_kind, not section_kind: the citation FLOOR differs between a Discussion
        # and a Conclusion, and between an Introduction and a Background, and section_kind
        # pools each pair into one label.
        return budget_kind(self.heading)

    def snippet(self, n: int = 160) -> str:
        s = " ".join(self.text.split())
        return s[:n] + ("…" if len(s) > n else "")


def parse_paragraphs(markdown: str) -> list[Paragraph]:
    """Body paragraphs of a markdown manuscript, tagged with their enclosing section.

    Heading lines are stripped from a block; a block left empty was heading-only and is not
    a paragraph. A ``## `` heading opens a new section. Paragraphs inside a References
    section are excluded — a bibliography entry is not prose and every guard below would
    misfire on it.
    """
    out: list[Paragraph] = []
    section = -1
    heading = ""
    pos = 0
    for block in re.split(r"\n\s*\n", markdown):
        lines = block.splitlines()
        heads = [ln for ln in lines if ln.lstrip().startswith("#")]
        prose = "\n".join(ln for ln in lines if not ln.lstrip().startswith("#")).strip()
        for h in heads:
            if h.lstrip().startswith("## "):
                section += 1
                heading = h.lstrip()[3:].strip()
                pos = 0
        if not prose or is_references(heading):
            continue
        out.append(Paragraph(section, pos, prose, tuple(all_citekeys(prose)), heading))
        pos += 1
    return out


# ── VERIFIABILITY (phase: draft) ──────────────────────────────────────────────

def unresolved_keys(text: str, known: set[str]) -> list[Finding]:
    """A [@citekey] with no refs.bib entry behind it. Looks founded; isn't.

    PHASE: draft + redline. A dangling key renders as literal "[@smith2020]" in the .docx —
    catch it before pandoc does.
    """
    bad = sorted(set(all_citekeys(text)) - known)
    return [
        Finding("unresolved-key", "manuscript",
                f"These [@citekey] tags match no entry in refs.bib — replace each with a key "
                f"from the bibliography or remove the claim: "
                f"{', '.join('[@' + k + ']' for k in bad)}.")
    ] if bad else []


def author_year_prose(text: str) -> list[Finding]:
    """PHASE: draft. "Smith et al. (2020) found…" written *instead of* a [@key] is an
    uncitable claim — invisible to the bibliography."""
    ay = sorted(set(AUTHOR_YEAR_RE.findall(text)))
    return [
        Finding("author-year", "manuscript",
                f"Citations must be [@citekey] tags from the bibliography, not author-year "
                f"prose — an author-year citation is invisible to the bibliography. Rewrite "
                f"these as [@citekey]: {', '.join(ay[:6])}.")
    ] if ay else []


def uncited_paragraphs(paras: list[Paragraph]) -> list[Finding]:
    """A paragraph with no citation states ideas it cannot ground.

    PHASE: draft. GATED on section kind — a Methods or Results paragraph is grounded in the
    writeup, not the bibliography.
    """
    return [
        Finding("uncited", f"{p.heading!r} para {p.index}",
                f'This paragraph cites no source: "{p.snippet()}" — state the source(s) for '
                f'its ideas as [@citekey] tags from the bibliography, or merge it into an '
                f'adjacent paragraph that already carries the evidence.', section=p.section)
        for p in paras if _is_body(p) and expects_citations(p.kind) and not p.keys
    ]


# Three is deliberately lenient — a woven paragraph usually runs denser.
_SENTENCES_PER_SOURCE = 3


def sparse_paragraphs(paras: list[Paragraph],
                      sentences_per_source: int = _SENTENCES_PER_SOURCE) -> list[Finding]:
    """A long paragraph resting on few sources is assertion with a citation attached.

    PHASE: draft. GATED on section kind. This and ``uncited_paragraphs`` are the mechanical
    floor that replaces the LLM critique check "lists rather than synthesises".
    """
    out: list[Finding] = []
    for p in paras:
        if not _is_body(p) or not expects_citations(p.kind) or not p.keys:
            continue
        n_sents = len(sentence_units(p.text))
        want = max(1, -(-n_sents // sentences_per_source))  # ceil
        if len(p.distinct) < want:
            out.append(Finding(
                "sparse-paragraph", f"{p.heading!r} para {p.index}",
                f'{n_sents} sentences of argument rest on only {len(p.distinct)} source(s) '
                f'({", ".join("[@" + k + "]" for k in sorted(p.distinct))}): "{p.snippet()}" '
                f'— ground it in at least {want} sources from the bibliography, or cut the '
                f'claims the evidence does not reach.', section=p.section))
    return out


_CITE_GROUP_RE = re.compile(r"\[@[^\]]+\]")


def padded_citations(paras: list[Paragraph]) -> list[Finding]:
    """The same source list re-cited sentence after sentence inside one paragraph.

    PHASE: draft. ``sparse_paragraphs`` sets a FLOOR on how many sources a paragraph rests
    on, and a model that cannot find more sources satisfies it the only other way available:
    by appending the sources it already has to every remaining sentence. css2026 shipped 48
    bracket groups of which 26 were this — one three-key group repeated eight times in a
    single subsection, its keys shuffled between occurrences so the text looked varied.

    A citation earns its place by introducing a source to its paragraph. Re-citing the same
    set adds no grounding, and the reader, who cannot see the guard being satisfied, reads
    it as padding.
    """
    out: list[Finding] = []
    for p in paras:
        if not _is_body(p):
            continue
        seen: set[frozenset[str]] = set()
        repeats = 0
        for m in _CITE_GROUP_RE.finditer(p.text):
            keys = frozenset(k.strip(" ;@") for k in m.group(0)[1:-1].split(";"))
            if keys in seen:
                repeats += 1
            seen.add(keys)
        if repeats:
            out.append(Finding(
                "padded-citation", f"{p.heading!r} para {p.index}",
                f"This paragraph re-cites the same source list {repeats} more time(s) after "
                f'first citing it: "{p.snippet()}" — keep the first citation of each source '
                f"and delete the repeats. Re-ordering the keys inside the brackets does not "
                f"make it a different citation.", section=p.section))
    return out


def unnumbered_results_paragraphs(paras: list[Paragraph]) -> list[Finding]:
    """A Results paragraph reporting no value at all.

    PHASE: draft. Only meaningful when results content was actually provided — the caller
    gates on that. "Performance improves substantially" cannot be checked against the
    results files; "accuracy rose to 0.94" can. A number is the most verifiable thing a
    claim can carry.
    """
    return [
        Finding("unnumbered-result", f"{p.heading!r} para {p.index}",
                f'This Results paragraph reports no numeric value: "{p.snippet()}" — cite the '
                f'concrete figures from the results content (means, effect sizes, counts, '
                f'p-values), or cut the claim.', section=p.section)
        for p in paras
        if p.kind == "results" and not NUMERAL_RE.search(p.text)
    ]


# ── THE AUTHOR'S OWN SENTENCES ────────────────────────────────────────────────

# Eight consecutive words, reproduced exactly, are not a coincidence. Below five, a match
# says nothing — the author and the tool are writing about the same paper, in the same
# terms, and a shared four-word phrase is the topic talking, not the pen.
_ECHO_SHINGLE = 8
_ECHO_MIN = 5

_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


def _words(text: str) -> list[str]:
    return [w.casefold() for w in _WORD_RE.findall(text)]


def echoed_spans(text: str, spans: dict[str, str]) -> list[Finding]:
    """The author's own sentence, retyped into the tool's prose beside itself.

    ``dropped_sentinels`` catches a span that vanished and ``invented_sentinels`` catches one
    conjured from nothing — and a draft can satisfy both, and the once-each count as well,
    and still ruin the paragraph: keep ⟦a:1⟧ exactly where it was told to, and ALSO retype
    what it contains into the prose around it. Expanded, the author reads his own sentence
    twice, back to back. He did not write it twice.

    The instruction not to is already in the prompt ("read them, never retype them"), which
    is precisely why this is a guard: the prompt asked, and the draft did it anyway.

    PHASE: redline. Fails the beat closed.
    """
    if not spans:
        return []
    prose = _words(SENTINEL_RE.sub(" ", text))
    if not prose:
        return []
    haystack = f" {' '.join(prose)} "
    out: list[Finding] = []
    for key, words in spans.items():
        ws = _words(words)
        n = min(_ECHO_SHINGLE, len(ws))
        if n < _ECHO_MIN:
            continue
        for i in range(len(ws) - n + 1):
            shingle = " ".join(ws[i:i + n])
            if f" {shingle} " in haystack:
                out.append(Finding(
                    "echoed-span", key,
                    f'You retyped the author\'s own words into your own prose: '
                    f'"…{shingle}…". {key} already carries that sentence, in its place. '
                    f'Write AROUND it — cut every word of it from your text and let the '
                    f'placeholder speak. The author does not want to read his sentence '
                    f'twice.'))
                break
    return out


# ── FIGURES ───────────────────────────────────────────────────────────────────

FIGURE_MD_RE = re.compile(r"!\[(?P<caption>[^\]]*)\]\((?P<path>[^)\s]+)[^)]*\)")
FIGURE_NUM_RE = re.compile(r"^\s*Figure\s+(\d+)\s*[:.]", re.IGNORECASE)
FIGURE_REF_RE = re.compile(r"\bFig(?:ure)?\.?\s*(\d+)", re.IGNORECASE)

_MIN_CAPTION_WORDS = 8


def figure_findings(text: str, expect: int | None = None,
                    start: int = 1) -> list[Finding]:
    """A figure the prose never introduces is a figure the reader is never told to look at.

    Three things a figure owes its reader, all checkable:
      * a NUMBER, so the text can refer to it ("Figure 1: …");
      * an INTRODUCTION in the prose ("Figure 1 shows …") — without one the reader meets an
        image with no idea why it is there or what to see in it;
      * a caption INFORMATIVE enough to interpret the figure: the axes, the encoding, what
        to look for. "Recovery landscape showing optimal distance" names no axis and no
        colour, and a reader cannot read the plot from it.

    ``expect`` is how many figures the DOCUMENT already holds. On a fresh draft nobody knows
    yet — the writer chooses, and ``None`` says so. On a re-cut the images are already
    embedded in the .docx and the count is not the writer's to choose: a re-cut that writes
    no figure markdown leaves them unnumbered, uncaptioned and unintroduced. Without
    ``expect`` this guard would call that clean, because it only ever inspected figures the
    prose DECLARED — never the ones the document HAD.

    ``start`` is the number this text's FIRST figure carries. Figures are numbered across
    the document, but sections are drafted one at a time, so a guard that always counted
    from 1 pronounced every section correct and let css2026 ship two Figure 1s — the lattice
    diagram in Methods and the recovery landscape in Results — with three prose references
    pointing at the wrong plot. The caller knows the offset: it is how many figures the
    outline places in the sections that precede this one.
    """
    figs = list(FIGURE_MD_RE.finditer(text))
    if expect is not None and len(figs) != expect:
        return [Finding(
            "figure-count", "figures",
            f"This document already contains {expect} embedded figure(s), but your text "
            f"declares {len(figs)}. Write exactly {expect} caption line(s) — "
            f"![Figure N: what it plots, on what axes, what the colours mean](path) — in the "
            f"order the figures appear, and introduce each in the prose before it. Adding or "
            f"removing a figure is not something a re-cut can do.")]
    if not figs:
        return []
    prose = FIGURE_MD_RE.sub(" ", text)          # the text WITHOUT the caption lines
    referenced = {int(n) for n in FIGURE_REF_RE.findall(prose)}

    out: list[Finding] = []
    for i, m in enumerate(figs, start=start):
        caption = (m.group("caption") or "").strip()
        num = FIGURE_NUM_RE.match(caption)
        if not num:
            out.append(Finding(
                "unnumbered-figure", f"figure {i}",
                f'Number this figure: its caption must begin "Figure {i}: " so the text can '
                f'refer to it. Got: "{caption[:60]}".'))
            continue
        n = int(num.group(1))
        if n != i:
            out.append(Finding(
                "misnumbered-figure", f"figure {i}",
                f'This is figure {i} in the document\'s order of appearance but its caption '
                f'says "Figure {n}". Renumber it to "Figure {i}: " and change every prose '
                f"reference to it accordingly."))
        body = caption[num.end():].strip()
        if len(body.split()) < _MIN_CAPTION_WORDS:
            out.append(Finding(
                "thin-caption", f"figure {i}",
                f'Caption "{caption[:60]}" is not enough to read the figure by. Say what is '
                f'plotted, on which axes, and what the colours mean — everything a reader '
                f'needs to interpret it without the surrounding text.'))
        if n not in referenced:
            out.append(Finding(
                "unintroduced-figure", f"figure {i}",
                f'Nothing in the prose introduces Figure {n}. Add a sentence saying what the '
                f'reader should see in it ("Figure {n} shows …") before the figure appears.'))
    return out


# ── VOICE ─────────────────────────────────────────────────────────────────────

def style_findings(text: str, signature: dict) -> list[Finding]:
    """Where the draft does not sound like the author — decided by counting, not by taste.

    "Match this author's voice" is not a check, it is a wish. These are checks:

      * a transition, hedge or intensifier the author has NEVER used in tens of thousands of
        words of their own published prose. The candidate sets are CLOSED classes, which is
        what makes this sound — a domain term cannot be mistaken for a style tic, and the
        earlier attempt to learn what an author "never writes" by contrasting corpora
        produced `jaccard distance` and `harmonic similarity`, i.e. the vocabulary of the
        paper itself.
      * sentences that run to a length the author does not write.

    Phrased positively, always: the finding names what the author DOES write. A model told
    not to write "moreover" writes "furthermore"; a model told the author writes "however",
    "thus" and "overall" reaches for one of those.
    """
    from . import voice

    if not signature or not (text or "").strip():
        return []

    out: list[Finding] = []
    for label, key, candidates in (
        ("transition", "connectives", voice.CONNECTIVES),
        ("hedge", "hedges", voice.HEDGES),
        ("intensifier", "intensifiers", voice.INTENSIFIERS),
    ):
        palette = signature.get(key) or {}
        if not palette:
            continue
        for phrase in voice.outside_palette(text, palette, candidates):
            his = ", ".join(list(palette)[:5])
            out.append(Finding(
                "off-voice", f"{label} {phrase!r}",
                f'Replace the {label} "{phrase}" — this author has never once written it. '
                f'He writes: {his}.'))

    mean = signature.get("sentence_words_mean")
    if mean:
        units = [u for u in sentence_units(text) if len(u.split()) >= 4]
        if len(units) >= 3:
            got = sum(len(u.split()) for u in units) / len(units)
            lo, hi = mean * 0.6, mean * 1.5
            if got < lo or got > hi:
                out.append(Finding(
                    "off-voice", "rhythm",
                    f"Sentences here average {got:.0f} words; this author's average "
                    f"{mean} (range {signature.get('sentence_words_p10', '?')}–"
                    f"{signature.get('sentence_words_p90', '?')}). "
                    f"{'Combine some — they are clipped.' if got < lo else 'Break some up.'}"))
    return out


# ── VERIFIABILITY (phase: redline) ────────────────────────────────────────────

def dropped_citekeys(old: str, new: str) -> list[Finding]:
    """PHASE: redline. A reviser silently lost a citation."""
    lost = set(all_citekeys(old)) - set(all_citekeys(new))
    return [
        Finding("dropped-citekey", "paragraph",
                "Restore these [@citekey] tags dropped from the original (unless a comment "
                "asked to remove that source): "
                + ", ".join(f"[@{k}]" for k in sorted(lost)) + ".")
    ] if lost else []


def dropped_sentinels(old: str, new: str) -> list[Finding]:
    """An equation severed from the claim it verifies — the same defect as a dropped
    citekey, and treated identically.

    PHASE: redline. Fails the edit closed.
    """
    lost = [s for s in sentinels(old) if s not in set(sentinels(new))]
    return [
        Finding("dropped-equation", "paragraph",
                f"These placeholders stand for equations in the original and must appear, "
                f"unaltered, in your output: {', '.join(sorted(set(lost)))}. Keep each one in "
                f"the sentence whose claim it supports. Never retype an equation as prose and "
                f"never invent a new placeholder.")
    ] if lost else []


def invented_sentinels(old: str, new: str) -> list[Finding]:
    """A placeholder the original never had. raconteur cannot author an equation, so a
    made-up sentinel resolves to nothing and would silently vanish on write.

    PHASE: redline. Fails the edit closed.
    """
    made_up = sorted(set(sentinels(new)) - set(sentinels(old)))
    return [
        Finding("invented-equation", "paragraph",
                f"These placeholders do not exist in the original: {', '.join(made_up)}. Use "
                f"only the placeholders you were given, exactly as written, and never create "
                f"one.")
    ] if made_up else []


# ── MINIMALITY (phase: redline) ───────────────────────────────────────────────

def touched_indices(old: str, new: str) -> set[int]:
    """Indices of the OLD paragraph's sentences that a revision changed or deleted.

    With an indexed-sentence reviser this is known exactly; this function recovers it from
    two blobs of prose for the fallback path and for auditing an existing document.
    """
    a, b = sentence_units(old), sentence_units(new)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    out: set[int] = set()
    for tag, i1, i2, _j1, _j2 in sm.get_opcodes():
        if tag != "equal":
            out.update(range(i1, i2))
    return out


def minimal_edit_violation(touched: set[int], anchored: set[int],
                           n_sentences: int) -> list[Finding]:
    """A reviser may touch the sentences the comment anchors to. Touching the rest is
    collateral damage — it discards grounding the comment never asked to change.

    PHASE: redline. This is the mechanical answer to collateral drift.

    Inactive only when the reviewer selected the WHOLE paragraph (or no range resolved):
    then every sentence is anchored and there is nothing to over-reach into. Anything short
    of that — even "all but one sentence" — leaves a sentence the comment does not bear on.
    """
    if not anchored or len(anchored) >= n_sentences:
        return []
    extra = sorted(touched - anchored)
    if not extra:
        return []
    return [Finding(
        "minimal-edit", "paragraph",
        f"You rewrote sentence(s) {', '.join(str(i + 1) for i in extra)}, which the comment "
        f"does not bear on. The comment anchors to sentence(s) "
        f"{', '.join(str(i + 1) for i in sorted(anchored))}. Restore the others word for "
        f"word — every rewritten sentence loses the grounding it carried.")]


# ── the polestar, as a number ─────────────────────────────────────────────────

@dataclass
class Metrics:
    citekeys_resolved: int
    citekeys_total: int
    uncited: int
    sparse: int
    sections: int
    words: int = 0
    budget: int = 0

    def __str__(self) -> str:
        # Length was absent from this line while a 6,975-word manuscript shipped against a
        # 5,000-word cap. A tally that cannot say "too long" is not a tally.
        length = f" · words {self.words}"
        if self.budget:
            length += f"/{self.budget}{' OVER' if self.words > self.budget else ''}"
        return (f"citekeys resolved {self.citekeys_resolved}/{self.citekeys_total} · "
                f"uncited body paragraphs {self.uncited} · sparse {self.sparse} · "
                f"sections {self.sections}{length}")


def metrics(markdown: str, known: set[str], budget: int = 0) -> Metrics:
    """One line that says, mechanically, whether the deliverable met the bar."""
    paras = parse_paragraphs(markdown)
    keys = set(all_citekeys(markdown))
    return Metrics(
        citekeys_resolved=len(keys & known),
        citekeys_total=len(keys),
        uncited=len(uncited_paragraphs(paras)),
        sparse=len(sparse_paragraphs(paras)),
        sections=len({p.section for p in paras if p.section >= 0}),
        words=word_count(markdown),
        budget=budget,
    )


# ── STRUCTURE (phase: outline) ────────────────────────────────────────────────
# An outline is a structural contract, and until now nothing checked it: outline.py
# imported no guards at all and relied on two LLM critique passes to mark their own
# homework. A 1.1 → 1.3 numbering gap therefore shipped through both passes and the
# human gate, and the draft invented a §1.2 to fill it — 4.5 GPU-hours to discover a
# defect a contiguity check finds in milliseconds. Structure is cheap to fix in an
# outline and expensive to fix in a manuscript; that asymmetry is why these run here.

# "Figure: <caption> (<path>)" — the outline's placement line. The outline is the sole
# authority on where a figure goes; the draft renders only what its own section names.
# A figure APPENDED to the bullet that introduces it, rather than standing as a bullet of
# its own. A figure is not a paragraph, and giving it a bullet meant every count that
# matters — beats, the word plan, the shortfall check, paragraph conformance — had to
# remember to exclude it. Three of them forgot: the css2026 outline's plan comment priced
# five figures as paragraphs and reported 5,410 words where the paper owed 4,596.
#
# DOUBLE square brackets, and the boundary is what makes the parse exact rather than clever.
# Real captions contain parentheses ("(blue = closer to the phrase)"), so without a delimiter
# the path has to be guessed at — by file extension, or by being the last bracket on the
# line. With "]]" closing it, the path is simply the last parenthesised group before the end.
#
# Not single brackets: "[@citekey]" is one, and the form would be ambiguous the moment it
# reached a document carrying citations — which the draft is. Not braces either: those are
# inert today only because outline text is always a substituted VALUE and never a
# str.format TEMPLATE, and resting a format on that is a landmine for whoever next writes
# `text.format(...)`. "[[" is unused by markdown, by pandoc, and by this pipeline.
FIGURE_APPENDED_RE = re.compile(
    r"\[\[\s*Figure\s*(?P<num>\d+)?\s*[:.]\s*(?P<caption>.*?)\s*"
    r"\(`?(?P<path>[^`()]+)`?\)\s*\]\]",
    re.IGNORECASE | re.DOTALL)


def appended_figures(line: str) -> list[re.Match]:
    """Figures riding on a bullet. Empty for a bullet that introduces none."""
    return [m for m in FIGURE_APPENDED_RE.finditer(line)]


OUTLINE_FIGURE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?Figure\s*(?P<num>\d+)?\s*[:.]\s*(?P<caption>.+?)\s*"
    r"\(`?(?P<path>[^`)]+)`?\)\s*$",
    re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class Heading:
    """One outline heading: its level, its text, and the number it declares (if any)."""
    level: int
    text: str
    number: tuple[int, ...]        # (1, 2) for "1.2 …"; () when unnumbered
    line: int
    beats: int = 0                 # non-heading, non-figure content lines beneath it
    figures: int = 0


_NUMBER_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s")


def parse_outline(markdown: str) -> list[Heading]:
    """The outline's heading tree, with each heading's beat and figure counts.

    Beats are what a subsection promises to argue; a heading with none is a container
    (``## 2. Methodology``, ``### 2.1 The Model``) and a heading with some is a leaf that
    the draft will turn into prose. The distinction is what the word budget is spent on,
    so it has to be structural rather than guessed from bullet counts.
    """
    heads: list[Heading] = []
    cur: dict | None = None
    for i, raw in enumerate(markdown.splitlines(), start=1):
        line = raw.rstrip()
        if line.lstrip().startswith("#"):
            s = line.lstrip()
            level = len(s) - len(s.lstrip("#"))
            text = s[level:].strip()
            m = _NUMBER_RE.match(text)
            num = tuple(int(x) for x in m.group(1).split(".")) if m else ()
            cur = {"level": level, "text": text, "number": num, "line": i,
                   "beats": 0, "figures": 0}
            heads.append(cur)          # type: ignore[arg-type]
            continue
        if cur is None or not line.strip():
            continue
        if OUTLINE_FIGURE_RE.match(line):
            # A figure standing alone on its line: still read, never counted as a beat.
            cur["figures"] += 1
        else:
            # A bullet is one beat however many figures it carries. That is the whole point
            # of appending them: the count cannot be got wrong by forgetting to exclude one.
            cur["beats"] += 1
            cur["figures"] += len(appended_figures(line))
    return [Heading(**h) for h in heads]      # type: ignore[arg-type]


def leaves(heads: list[Heading]) -> list[Heading]:
    """Headings the draft will write prose for: those carrying beats of their own.

    A container's budget belongs to its children, not to itself — charging it a share
    is how a uniform allocation quietly hands Methods 41% of a paper for having seven
    subsections rather than for having the most to say.
    """
    return [h for h in heads
            if h.beats and not (is_references(h.text) or is_acknowledgements(h.text))]


def ancestor_kind(heads: list[Heading], leaf: Heading, for_budget: bool = False) -> str:
    """A subsection's kind is its SECTION's kind, not its own name's.

    ``for_budget`` classifies with ``budget_kind`` instead, which tells an Introduction
    from a Background and a Conclusion from a Discussion — two distinctions allocation
    needs and citation floors do not.

    "Sonic Art and Audio Translation of Visual Models" contains "model" and classifies as
    methods on its own; it is a subsection of the Introduction. "Qualitative Assessment"
    classifies as "other" and demands citations; it is Methods. A subsection inherits what
    grounds it from the section it sits in — the only place that information exists.
    """
    # A Conclusion is a Conclusion even when the outline mis-levels it as a ### under
    # Discussion — which happens, and would otherwise charge it to Discussion's budget.
    if _is_conclusion(leaf.text):
        return "conclusion"
    top = None
    for h in heads:
        if h.line > leaf.line:
            break
        if h.level <= 2:
            top = h
    classify = budget_kind if for_budget else section_kind
    if top is None or top is leaf:
        return "conclusion" if _is_conclusion(leaf.text) else classify(leaf.text)
    if _is_conclusion(top.text):
        return "conclusion"
    return classify(top.text)


def numbering_gaps(heads: list[Heading]) -> list[Finding]:
    """Declared section numbers must run 1..N with no holes, at every level.

    PHASE: outline. The defect this exists for: an outline numbered 1.1, 1.3 reads to a
    drafting model as a missing 1.2, and it will helpfully invent one.
    """
    out: list[Finding] = []
    seen: dict[tuple[int, ...], list[int]] = {}
    for h in heads:
        if h.number:
            seen.setdefault(h.number[:-1], []).append(h.number[-1])
    for parent, kids in seen.items():
        label = ".".join(str(x) for x in parent) if parent else "top level"
        for want, got in enumerate(kids, start=1):
            if want != got:
                prefix = (".".join(str(x) for x in parent) + ".") if parent else ""
                out.append(Finding(
                    "numbering-gap", f"section {prefix}{got}",
                    f"Renumber the subsections under {label} so they run consecutively "
                    f"from 1: expected {prefix}{want} but found {prefix}{got}. A gap reads "
                    f"as a missing section and the draft will invent one to fill it."))
                break
    return out


def heading_levels(heads: list[Heading]) -> list[Finding]:
    """No level skips, and no heading that is neither container nor leaf.

    PHASE: outline. A ``##`` followed by ``####`` loses a tier in the Word render, and a
    heading with neither beats nor children promises the draft nothing to write.
    """
    out: list[Finding] = []
    for i, h in enumerate(heads):
        if i and h.level > heads[i - 1].level + 1:
            out.append(Finding(
                "heading-skip", f"line {h.line}: {h.text[:48]!r}",
                f"This heading jumps from level {heads[i-1].level} to {h.level}. Use the "
                f"next level down ({heads[i-1].level + 1}) so the hierarchy renders as "
                f"nested Word headings rather than a flattened list."))
        has_child = i + 1 < len(heads) and heads[i + 1].level > h.level
        # References and Acknowledgements carry no argument of their own by design: the
        # bibliography is generated at write time and the CRediT list passes through verbatim.
        boilerplate = is_references(h.text) or is_acknowledgements(h.text)
        if not h.beats and not has_child and h.level > 1 and not boilerplate:
            out.append(Finding(
                "empty-heading", f"line {h.line}: {h.text[:48]!r}",
                f"This heading has neither bullets of its own nor subsections beneath it. "
                f"Give it 3–5 bullets saying what it argues, or remove it."))
    return out


# The share of the BODY word budget each section carries, by what the section is FOR.
# Not uniform, and not arithmetic: an Introduction states the motivation, previews the
# result and lays out the paper — inherently brief, ~2 paragraphs. A Background must give
# the reader every literature they need to follow the work, so it can and should be longer.
# Results carries the contribution. Derived per section rather than per KIND: Introduction
# and Background sharing one "litrev" share meant whichever had more subsections took more
# of it, so collapsing a section's subsections cut its own budget.
# Overridable per project (ProjectConfig.section_shares). Sums to 1.0.
DEFAULT_SECTION_SHARES: dict[str, float] = {
    "intro":      0.075,     # motivation, preview, roadmap
    "litrev":     0.150,     # Background — the literature the reader needs
    "methods":    0.225,     # what was built, and how
    "results":    0.250,     # the contribution
    "other":      0.225,     # Discussion — what it means
    "conclusion": 0.075,     # restate, point forward
}

# The abstract is NOT in the shares and is not charged to the body budget: no venue counts
# it against the paper's length. Where a CFP states its own limit that wins; this is the
# fallback when it says nothing.
DEFAULT_ABSTRACT_WORDS = 225

# A paragraph of academic prose, and therefore what one outline bullet expands to. Anchored
# on the author's own figure: an Introduction is ~300 words across 2 paragraphs. The size
# flexes to fill its subsection — this is the divisor that turns a word allocation into a
# bullet count, not a target in itself.
WORDS_PER_PARAGRAPH = 150

# A subsection is a heading plus an argument, and an argument needs more than one paragraph.
# At one bullet a subsection is a heading tax: it spends a heading to say one thing. Two is
# the floor the author set, and it is what makes a subsection's cost derivable rather than
# assumed — a subsection costs MIN_BULLETS_PER_SUBSECTION * WORDS_PER_PARAGRAPH, so how many
# a section affords falls out of its word share instead of a static per-leaf constant.
MIN_BULLETS_PER_SUBSECTION = 2
PARAGRAPH_BAND = (100, 200)

# Where in a venue's stated range the paper should land. A range is not a ceiling: a CFP
# that says 3,000–5,000 is describing the shape of a paper it wants, and both ends carry
# information. Writing to the maximum leaves nothing for a reference list that runs longer
# than estimated; writing to the minimum submits a paper that looks thin next to its peers.
TARGET_FRACTION = 0.5


def word_target(word_min: int | None, word_max: int | None,
                fraction: float = TARGET_FRACTION) -> int:
    """The whole-document length the paper should AIM AT, from the venue's range.

    A single limit is a ceiling and stays one — with no floor there is no range to sit
    inside, and the target is the limit. With both ends the target sits ``fraction`` of the
    way up, so the writer is handed a number to hit rather than a bound to duck under. The
    difference is not cosmetic: a budget derived from a ceiling is satisfied by any short
    paper, which is how a Results section gets written at half its share and nothing
    objects.
    """
    lo, hi = word_min or 0, word_max or 0
    if not hi:
        return lo
    if not lo or lo >= hi:
        return hi
    return round(lo + (hi - lo) * fraction)


def prose_budget(total_words: int) -> int:
    """The BODY words the manuscript should come out at. No reserves.

    Nothing is deducted, because nothing that would be deducted is counted in the first
    place: ``word_count`` excludes section labels, figure captions and ``[@citekey]`` tags;
    the bibliography is rendered by pandoc at write time and never appears in the markdown
    being measured; and the abstract, the title and the author block are all outside the
    body. Deducting reserves for them charged the budget twice for material that costs it
    nothing — 707 words of a 4,200-word target on SchellingChords, a section and a half.

    The target is a target for BODY PROSE and that is the whole of it. What else a rendered
    document weighs is not this function's business and is not modelled here.
    """
    return max(0, total_words)


def abstract_words(venue_limit: int | None = None) -> int:
    """How long the abstract should be. The venue's own limit wins where it states one."""
    return venue_limit or DEFAULT_ABSTRACT_WORDS


def bullets_for(words: int, per_paragraph: int = WORDS_PER_PARAGRAPH) -> int:
    """How many bullets a subsection's word allocation affords.

    One bullet is one paragraph in the manuscript — that is the contract phase two writes
    to and the draft is held to. So a bullet count is not a stylistic choice: four bullets
    under a 200-word subsection is a 50-word paragraph, and that is computable before a
    word of prose exists.
    """
    return max(1, round(words / per_paragraph)) if words > 0 else 0


def subsection_words() -> int:
    """What one subsection costs: its minimum bullets, at a paragraph each."""
    return MIN_BULLETS_PER_SUBSECTION * WORDS_PER_PARAGRAPH


def leaf_allowance(budget: int, shares: dict[str, float] | None = None,
                   per_leaf: int | None = None) -> dict[str, int]:
    """How many subsections each section can afford at a writable length.

    ``per_leaf`` defaults to ``subsection_words()`` — derived, not asserted. It used to be a
    static 280, which agreed with nothing else in the pipeline: the skeleton's own plan
    table said a 4,000-word paper afforded 27 paragraphs while this said 14 subsections, and
    the author was shown both numbers one rung apart.
    """
    per_leaf = per_leaf or subsection_words()
    sh = shares or DEFAULT_SECTION_SHARES
    return {k: max(1, round(budget * v / per_leaf)) for k, v in sh.items()}


_CONCLUSION_RE = re.compile(r"^\d*\.?\s*(conclusion|concluding)", re.IGNORECASE)


def _is_conclusion(heading: str) -> bool:
    return bool(_CONCLUSION_RE.match(heading))


def outline_figures(heads: list[Heading], markdown: str) -> list[tuple[str, str]]:
    """Every figure the outline places, as (path, enclosing heading), in document order."""
    out: list[tuple[str, str]] = []
    cur = ""
    for raw in markdown.splitlines():
        s = raw.lstrip()
        if s.startswith("#"):
            cur = s[len(s) - len(s.lstrip("#")):].strip()
            continue
        m = OUTLINE_FIGURE_RE.match(raw)
        if m:
            out.append((m.group("path").strip(), cur))
            continue
        # A figure riding on the bullet that introduces it. Both forms are read, so an
        # outline written either way still places its figures; only the new one is asked for.
        for am in appended_figures(raw):
            out.append((am.group("path").strip(), cur))
    return out


def figure_placement(markdown: str, heads: list[Heading],
                     expected: dict[str, str] | None = None) -> list[Finding]:
    """Every available figure placed exactly once, at a real path, numbered in order.

    PHASE: outline. ``expected`` maps path → origin ('results' | 'author'). A results
    figure belongs with the finding it shows; an author figure belongs where the author
    put it, and neither may be invented, duplicated or silently dropped.
    """
    placed = outline_figures(heads, markdown)
    out: list[Finding] = []

    seen: dict[str, int] = {}
    for path, where in placed:
        seen[path] = seen.get(path, 0) + 1
    for path, n in seen.items():
        if n > 1:
            out.append(Finding(
                "figure-repeated", path,
                f"This figure is placed {n} times. Place each figure exactly once, in the "
                f"subsection whose argument it carries."))

    if expected is not None:
        for path in expected:
            if path not in seen:
                out.append(Finding(
                    "figure-unplaced", path,
                    f"This figure is available but the outline never places it. Append "
                    f'"Figure: <caption> ({path})" to the BULLET that introduces it, so the '
                    f"figure and the paragraph that points at it cannot be separated — or "
                    f"say nothing about it at all."))
        for path in seen:
            if path not in expected:
                out.append(Finding(
                    "figure-invented", path,
                    f"No such figure exists. Place only figures from the available list; "
                    f"do not invent a path."))

    for i, (path, _) in enumerate(placed, start=1):
        m = next((mm for mm in OUTLINE_FIGURE_RE.finditer(markdown)
                  if mm.group("path").strip() == path), None)
        if m and m.group("num") and int(m.group("num")) != i:
            out.append(Finding(
                "figure-misnumbered", path,
                f'This is figure {i} in order of appearance but its line says "Figure '
                f'{m.group("num")}". Number figures from 1, in the order they appear.'))
    return out


def required_sections(markdown: str, required: str) -> list[Finding]:
    """Sections the venue's CFP demands. The field existed and nothing ever enforced it.

    PHASE: outline. ``required`` is the venue's free-text list; each comma- or
    semicolon-separated item must appear as a heading.
    """
    if not (required or "").strip():
        return []
    heads = [h.text.lower() for h in parse_outline(markdown)]
    out: list[Finding] = []
    for item in (x.strip() for x in re.split(r"[;,]", required) if x.strip()):
        if not any(item.lower() in h for h in heads):
            out.append(Finding(
                "missing-required-section", item,
                f'This venue requires a "{item}" section and the outline has none. '
                f"Add it as a heading."))
    return out


def bullet_budget(markdown: str, budget: int,
                  shares: dict[str, float] | None = None,
                  rates: dict[str, int] | None = None) -> list[Finding]:
    """Subsections whose bullet count does not fit the words they are given. PHASE: outline.

    One bullet is one paragraph in the manuscript. That makes a bullet count arithmetic
    rather than taste: a 200-word subsection carrying four bullets is asking for 50-word
    paragraphs, and one carrying a single bullet is asking for a 200-word one. Both are
    computable the moment the bullets exist, and neither was checked — the rule lived only
    in a prompt.
    """
    if budget <= 0:
        return []
    heads = parse_outline(markdown)
    out: list[Finding] = []
    counts: dict[str, list[Heading]] = {}
    for leaf in leaves(heads):
        # The document TITLE is not a subsection. The author block sits beneath it as
        # ordinary prose, which parse_outline reads as beats — so the title came back
        # carrying four "bullets" and was charged a Discussion's share.
        if leaf.level < 2:
            continue
        counts.setdefault(ancestor_kind(heads, leaf, for_budget=True), []).append(leaf)
    for kind, kids in counts.items():
        # The abstract sits outside the body budget but is not unbounded: it has its own
        # target, so its bullet count is checkable too. A 225-word abstract carrying three
        # bullets is asking for 75-word paragraphs.
        words = (abstract_words() if kind == "abstract"
                 else round(budget * (shares or DEFAULT_SECTION_SHARES).get(kind, 0)))
        if not words:
            continue
        # WHAT A BULLET IS WORTH is the section's own, pinned at the skeleton and gated at
        # every rung since. Dividing the share by the subsection count instead told four
        # approved Background subsections they were 75 words a paragraph and to halve their
        # bullets — arguing with the plan rather than checking against it. Rates arrive
        # keyed by heading; this loop groups by KIND, so they are mapped the same way.
        by_kind = {budget_kind(k): v for k, v in (rates or {}).items()}
        rate = by_kind.get(kind, 0)
        each = rate * MIN_BULLETS_PER_SUBSECTION if rate else words // max(1, len(kids))
        want = bullets_for(each)
        lo, hi = PARAGRAPH_BAND
        for leaf in kids:
            if not leaf.beats:
                continue                      # an empty heading is heading_levels' business
            per = rate if rate else each // leaf.beats
            if per < lo or per > hi:
                out.append(Finding(
                    "bullet-count", leaf.text,
                    f'"{leaf.text}" has {leaf.beats} bullet(s) for {each} words — '
                    f"{per} words a paragraph, outside {lo}–{hi}. Use about {want}: one "
                    f"bullet becomes one paragraph in the manuscript."))
    return out


def skeleton_conformance(outline_md: str, skeleton_md: str) -> list[Finding]:
    """The outline reproduces the approved skeleton's headings. PHASE: outline.

    Phase one's structure is gated by the author. Phase two adds beats to it and may not
    rename a section, add one, drop one, or change its level — but nothing checked, so the
    contract lived in a prompt and a redline could be discarded in silence. Same shape of
    failure as a co-author's name surviving only in prose.
    """
    return outline_conformance(outline_md, skeleton_md)


def outline_findings(markdown: str, budget: int = 0,
                     expected_figures: dict[str, str] | None = None,
                     required: str = "",
                     shares: dict[str, float] | None = None,
                     skeleton: str = "",
                     rates: dict[str, int] | None = None) -> list[Finding]:
    """The whole outline battery, in the order a reader would want them fixed.

    No ``numbering_gaps``: headings carry no numbers now — the .docx style supplies them —
    so it can never fire, and a guard that cannot fire is one whose silence means nothing.

    Structure is not re-litigated here: it is an approved contract phase two may not alter,
    so a finding naming a fix the reader is forbidden to make only teaches them to ignore
    the battery. ``skeleton.findings`` owns it, at the rung where merging a heading is free.
    """
    heads = parse_outline(markdown)
    return (skeleton_conformance(markdown, skeleton)
            + heading_levels(heads)
            + bullet_budget(markdown, budget, shares, rates)
            + figure_placement(markdown, heads, expected_figures)
            + required_sections(markdown, required))


# ── CONFORMANCE + LENGTH (phase: draft) ───────────────────────────────────────
# The outline is the human-approved contract. Nothing checked that the draft honoured it,
# so a 1.1 → 1.3 numbering gap let the draft invent a "### 1.2 Tonal Stability Hierarchies"
# out of nothing and ship it. And no guard anywhere measured length: a 6,975-word manuscript
# went out against a 5,000-word cap with a clean tally line, because every section was
# individually legal and nobody summed them.

def _heading_texts(markdown: str, min_level: int = 3) -> list[str]:
    out = []
    for raw in markdown.splitlines():
        s = raw.lstrip()
        if not s.startswith("#"):
            continue
        level = len(s) - len(s.lstrip("#"))
        text = s[level:].strip()
        if level >= min_level and text:
            out.append(text)
    return out


def _norm_heading(h: str) -> str:
    """Compare headings on their words, not their numbering or punctuation — "3.1. Recovery
    Landscape" and "3.1 Recovery Landscape" are the same section."""
    return re.sub(r"[^a-z0-9]+", " ", h.lower()).strip()


def _conformance_headings(markdown: str) -> list[str]:
    """Every heading a draft is accountable for: the subsections, plus any level-2 section
    that has no subsections of its own.

    Comparing subsections alone made childless sections invisible. "## 6. Conclusion" is a
    whole section with its bullets directly under it; the css2026 draft simply stopped after
    5.4 and the conformance check reported clean, because a section with nothing at level 3
    contributed nothing to either side of the comparison.
    """
    out: list[str] = []
    pending: str | None = None       # a level-2 heading not yet seen to have children
    for raw in markdown.splitlines():
        s = raw.lstrip()
        if not s.startswith("#"):
            continue
        level = len(s) - len(s.lstrip("#"))
        text = s[level:].strip()
        if not text:
            continue
        if level <= 2:
            if pending is not None:
                out.append(pending)
            pending = text if level == 2 else None
        else:
            pending = None
            out.append(text)
    if pending is not None:
        out.append(pending)
    return out


def outline_conformance(draft_md: str, outline_md: str) -> list[Finding]:
    """Sections the draft invented or dropped relative to its outline.

    PHASE: draft. The outline is what the author approved; a draft that adds a section has
    written something nobody agreed to, and one that drops a section has quietly cut the
    argument. Both are computable and neither was checked.
    """
    if not outline_md.strip():
        return []
    want = [h for h in _conformance_headings(outline_md)
            if not (is_references(h) or is_acknowledgements(h) or is_abstract(h))]
    got = [h for h in _conformance_headings(draft_md)
           if not (is_references(h) or is_acknowledgements(h) or is_abstract(h))]
    want_n = {_norm_heading(h): h for h in want}
    got_n = {_norm_heading(h): h for h in got}

    out: list[Finding] = []
    for key, h in got_n.items():
        if key not in want_n:
            out.append(Finding(
                "invented-section", h,
                f'The outline has no "{h}" section. Remove it and fold anything worth '
                f"keeping into the subsection the outline does name — the outline is what "
                f"the author approved."))
    for key, h in want_n.items():
        if key not in got_n:
            out.append(Finding(
                "dropped-section", h,
                f'The outline calls for a "{h}" subsection and the draft has none. Write '
                f"it, following that subsection's bullets."))
    return out


def word_count(markdown: str, front_matter: str = "") -> int:
    """Prose words: headings, figure caption lines and citekey tags do not count as writing.

    The TITLE is free because it is a heading and headings never count. The AUTHORS block
    is free too — no venue counts a title or a byline, however inclusive its rule — but it
    is ordinary text, so pass it as ``front_matter`` when measuring a WRITTEN document.

    In-pipeline this does not arise: every guard measures the assembled manuscript, and the
    block is rendered later, in ``paper._write``. That is an invariant of ordering rather
    than of construction, so anything measuring a file on disk must say so explicitly.
    """
    text = FIGURE_MD_RE.sub(" ", markdown)
    if front_matter.strip():
        for line in front_matter.splitlines():
            if line.strip():
                text = text.replace(line, " ", 1)
    text = CITE_TAG_RE.sub(" ", text)
    text = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    return len(text.split())


def over_budget(markdown: str, budget: int, tolerance: float = 0.05) -> list[Finding]:
    """The whole-document length check no section-local guard can make.

    PHASE: draft. Every section can sit inside its own band and the sum still fail — that is
    exactly what happened: 19 subsections each legal at 150–300 words, 6,975 words total,
    40% over a 5,000-word cap, reported as clean.
    """
    if budget <= 0:
        return []
    n = word_count(markdown)
    if n <= budget * (1 + tolerance):
        return []
    return [Finding(
        "over-budget", "manuscript",
        f"This manuscript runs {n} words of prose against a budget of {budget}. Cut "
        f"{n - budget} words. Tighten the longest subsections first; do not drop a "
        f"[@citekey], a figure, or a subsection the outline names.")]


def abstract_length(markdown: str, limit: int | None = None) -> list[Finding]:
    """The abstract against the limit the abstract was asked for. PHASE: draft.

    The abstract is drafted by its own prompt, after the body, and it is the one block of
    prose ``section_lengths`` deliberately skips — so its stated limit was the only thing
    holding it and nothing checked whether it held. css2026's came back at 358 words against
    the 225 it was asked for, in 44 sentences averaging 8 words, and shipped.
    """
    want = abstract_words(limit)
    if not want:
        return []
    body = abstract_body(markdown)
    n = word_count(body) if body else 0
    if not n or n <= round(want * 1.1):
        return []
    return [Finding(
        "abstract-long", "Abstract",
        f"The abstract runs {n} words against a limit of {want}. Cut {n - want} words. "
        f"Keep the contribution, the result, and the limitation; drop implications the "
        f"paper does not demonstrate.")]


# The bold stars are optional because a RELEASE has lost them: release_markdown renders
# the accepted .docx body as plain paragraphs, so the assembler's **Abstract** label comes
# back as the bare line "Abstract". The submission wrapper read a release, found no label,
# and shipped "Placeholder abstract" behind a TODO on a paper whose abstract was written,
# measured and approved three rungs up. A line that says only "abstract" is the label.
_ABSTRACT_LABEL_RE = re.compile(r"^\s*(?:\*\*)?\s*abstract\s*:?\s*(?:\*\*)?\s*$",
                                re.IGNORECASE | re.MULTILINE)


def abstract_body(markdown: str) -> str:
    """The abstract's prose, however the assembler labelled it.

    ``_assemble`` writes the abstract as a BOLD LABEL under the title — ``**Abstract**`` —
    not as a ``## Abstract`` heading, because a numbered-heading reference doc would
    otherwise number it "1. Abstract". So a check that looked for it among the document's
    sections found nothing and reported clean, which is how a 358-word abstract passed a
    guard written to catch it. Both spellings are accepted; a hand-edited manuscript may
    legitimately carry either.
    """
    for heading, body in _sections_with_bodies(markdown):
        if is_abstract(heading):
            return body
    m = _ABSTRACT_LABEL_RE.search(markdown)
    if not m:
        return ""
    rest = markdown[m.end():]
    stop = re.search(r"^##\s", rest, re.MULTILINE)
    return rest[:stop.start()] if stop else rest


_ECHO_MIN_WORDS = 8


def echoed_sentences(markdown: str) -> list[Finding]:
    """A sentence written once and pasted into a second section. PHASE: draft.

    Sections are drafted one at a time against a shared context, and each is handed what the
    earlier ones said — which is what keeps a Discussion anchored to its Results, and also
    what lets it quote them. css2026 repeated five sentences of eight words or more verbatim
    across Results, Discussion and Conclusion, and restated the same three numbers in all
    three, spending roughly 150 words of a manuscript already over its cap saying nothing
    twice.

    Restating a FINDING in the Discussion is legitimate; restating the SENTENCE is not.
    """
    where: dict[str, list[str]] = {}
    for heading, body in _sections_with_bodies(markdown):
        if is_references(heading) or is_acknowledgements(heading):
            continue
        for s in sentence_units(FIGURE_MD_RE.sub(" ", body)):
            key = " ".join(s.lower().split())
            if len(key.split()) >= _ECHO_MIN_WORDS:
                where.setdefault(key, []).append(heading)
    out: list[Finding] = []
    for key, heads in where.items():
        if len(heads) < 2:
            continue
        out.append(Finding(
            "echoed-sentence", " / ".join(dict.fromkeys(heads)),
            f'This sentence appears verbatim in {len(heads)} places '
            f'({", ".join(dict.fromkeys(heads))}): "{key[:120]}" — keep it where it is '
            f"first earned and rewrite or cut the other(s). A finding may be restated; a "
            f"sentence may not."))
    return out


def authorship(markdown: str, expected: str, anonymized: bool = False) -> list[Finding]:
    """The author block is present, and absent where it must be. PHASE: draft.

    Two failures, opposite and both silent. An anonymized venue that receives a named
    manuscript is a desk reject on a rule the CFP stated plainly. A named venue that
    receives an unnamed manuscript looks like a tooling accident, because it is one.
    """
    names = [ln for ln in expected.splitlines() if ln.strip()][:1]
    if anonymized:
        leaked = [n for n in names if n and n in markdown]
        if leaked:
            return [Finding(
                "identity-leak", "front matter",
                "This venue requires an anonymized submission and the manuscript names "
                "its authors. Remove the author block, and any self-identifying phrasing "
                "with it.")]
        return []
    if not expected.strip():
        return []
    if names and names[0] not in markdown:
        return [Finding(
            "missing-authors", "front matter",
            "The manuscript does not carry the author block recorded for this project. "
            "It is rendered under the title at write time — check the project's author "
            "list with `haarpi authors`.")]
    return []


def under_budget(markdown: str, budget: int, tolerance: float = 0.10) -> list[Finding]:
    """The other half of a range. PHASE: draft.

    A venue asking for 3,000–5,000 words is not satisfied by 3,100 any more than by 5,900,
    and a budget checked in one direction only is satisfied by any paper short enough. The
    css2026 draft came in comfortably under its ceiling with Results written at 17% of the
    manuscript against a 30% share — under-length in exactly the place the paper's
    contribution lives, and nothing measured it.
    """
    if budget <= 0:
        return []
    n = word_count(markdown)
    if n >= budget * (1 - tolerance):
        return []
    return [Finding(
        "under-budget", "manuscript",
        f"This manuscript runs {n} words of prose against a target of {budget}. Add "
        f"{budget - n} words of substance — not padding. Expand the subsections furthest "
        f"below their band first; do not add new claims, values, or sources.")]


def section_lengths(draft_md: str, outline_md: str, budget: int,
                    shares: dict[str, float] | None = None,
                    rates: dict[str, int] | None = None) -> list[Finding]:
    """Sections written outside the band their share affords. PHASE: draft.

    ``section_band`` already computes each section's band and the draft prompt is handed
    it, but nothing checked the result, so the bands were advisory. They are what keeps the
    paper's shape honest: without enforcement Introduction and Background ran to 36% of a
    manuscript budgeted for 28% while Results took 17% of a 30% share. The whole-document
    total can be perfectly legal while the paper is about the wrong thing.

    It must be ``section_band`` and not ``section_target``. Reading the band off the SHARE
    here while the draft prompt reads it off the BULLETS is the contradiction
    ``section_band`` exists to remove, reintroduced one level up: the two agree only while
    an outline's bullet count happens to match its section's share, and the whole point of
    bullets-as-truth is that the author may make them disagree.

    (On css2026 they happened to agree — Background's four bullets and its 15% share both
    give 480–720 — so this was a latent inconsistency there rather than the cause of that
    draft's 1,046-word Background. It is still two answers to one question.)
    """
    if budget <= 0 or not outline_md.strip():
        return []
    planned = {h for h, _ in _sections_with_bodies(outline_md)}
    out: list[Finding] = []
    for heading, body in _sections_with_bodies(draft_md):
        if heading not in planned:
            continue
        out += section_size(heading, body,
                            section_band(heading, outline_md, budget, shares, rates=rates))
    return out


def paragraph_conformance(draft_md: str, outline_md: str,
                          tolerance: int = 1) -> list[Finding]:
    """Subsections whose paragraph count does not match their bullet count. PHASE: draft.

    One bullet is one paragraph. That is the contract phase two writes to and the whole
    reason a bullet count can be derived from a word allocation — but it lived only in a
    prompt, so a bullet silently folded into its neighbour, or expanded into three, was
    invisible. Both change the paper's shape without changing any heading, which is
    precisely the kind of drift the section bands were too coarse to see.

    ``tolerance`` allows one paragraph either way: a subsection that opens with a framing
    sentence, or closes by handing off to the next, is not a defect.
    """
    if not outline_md.strip():
        return []
    want: dict[str, int] = {}
    for leaf in leaves(parse_outline(outline_md)):
        if leaf.beats:
            want[_norm_heading(leaf.text)] = leaf.beats
    if not want:
        return []

    out: list[Finding] = []
    for heading, body in _subsections_with_bodies(draft_md):
        key = _norm_heading(heading)
        if key in want:
            out += paragraph_count(heading, body, want[key], tolerance)
    return out


def _subsections_with_bodies(markdown: str) -> list[tuple[str, str]]:
    """Level-3+ subsections as (heading, its prose up to the next heading of any level)."""
    out: list[tuple[str, list[str]]] = []
    for raw in markdown.splitlines():
        s = raw.lstrip()
        level = len(s) - len(s.lstrip("#")) if s.startswith("#") else 0
        if level >= 3 and s[level:].strip():
            out.append((s[level:].strip(), []))
        elif level and level <= 2:
            out.append(("", []))          # a new section closes the previous subsection
        elif out:
            out[-1][1].append(raw)
    return [(h, "\n".join(b)) for h, b in out if h]


def _sections_with_bodies(markdown: str) -> list[tuple[str, str]]:
    """Level-2 sections as (heading, everything under it up to the next level-2)."""
    out: list[tuple[str, list[str]]] = []
    for raw in markdown.splitlines():
        s = raw.lstrip()
        level = len(s) - len(s.lstrip("#")) if s.startswith("#") else 0
        if level == 2 and s[2:].strip():
            out.append((s[2:].strip(), []))
        elif out:
            out[-1][1].append(raw)
    return [(h, "\n".join(b)) for h, b in out]


def kind_leaf_counts(outline_md: str) -> dict[str, int]:
    """Section KIND → how many subsections the whole outline gives it.

    The denominator ``section_target`` divides a share by. Introduction and Background are
    both "litrev": charging each of them the full 14% share hands that kind 28% of the
    paper, and the shares stop summing to the document. This is the arithmetic behind the
    defect observed by hand — Introduction and Background took 36% of a manuscript
    budgeted for 28% — which the per-section band would otherwise have called correct.
    """
    heads = parse_outline(outline_md)
    counts: dict[str, int] = {}
    for leaf in leaves(heads):
        k = ancestor_kind(heads, leaf)
        counts[k] = counts.get(k, 0) + 1
    return counts


def section_words(heading: str, budget: int,
                  shares: dict[str, float] | None = None) -> int:
    """This SECTION's slice of the body budget. Furniture gets none.

    The Abstract is drafted by its own prompt against its own limit, the Acknowledgements
    are a CRediT passthrough the tool may not write, and the References are rendered by
    pandoc. None of them spends body prose. Falling through to the "other" share gave each
    of them 22.5% of the budget, so the word plan the author approves at the skeleton stage
    claimed 2,700 words of material that will never be written — three of its nine rows.
    """
    if budget <= 0 or is_abstract(heading) or is_references(heading) \
            or is_acknowledgements(heading):
        return 0
    sh = shares or DEFAULT_SECTION_SHARES
    return round(budget * sh.get(budget_kind(heading), sh.get("other", 0.2)))


def section_target(heading: str, budget: int,
                   shares: dict[str, float] | None = None,
                   tolerance: float = 0.2) -> tuple[int, int]:
    """The word band for a SECTION, from the venue budget and the section's purpose.

    The band stops at the section. It used to divide the share by the subsection count and
    hand each subsection a target, which produced every allocation bug this file records:
    a Methods with seven subsections got a thinner band than one with three, and collapsing
    a section's subsections CUT its budget. How the section's words distribute across its
    subsections is the outline's business — one bullet, one paragraph — not a band's.

    Returns (low, high); (0, 0) when the venue states no limit and length is the writer's
    call — writing to an assumed length is its own failure.
    """
    total = section_words(heading, budget, shares)
    if not total:
        return (0, 0)
    return (round(total * (1 - tolerance)), round(total * (1 + tolerance)))


def section_bullets(outline_md: str) -> dict[str, int]:
    """Normalised section heading → how many bullets the outline puts under it.

    Counts the section's own beats plus every descendant's, which is the number of
    PARAGRAPHS the draft owes for that section. The title is excluded: an author block
    under the ``# `` heading parses as beats, and charging them to a section made the
    document's byline part of its word budget.
    """
    heads = parse_outline(outline_md)
    out: dict[str, int] = {}
    current: str | None = None
    for h in heads:
        if h.level < 2:
            continue
        if h.level == 2:
            current = _norm_heading(h.text)
        if current is None:
            continue
        out[current] = out.get(current, 0) + h.beats
    return out


def section_band(heading: str, outline_md: str = "", budget: int = 0,
                 shares: dict[str, float] | None = None,
                 tolerance: float = 0.2,
                 rates: dict[str, int] | None = None) -> tuple[int, int]:
    """The word band for a SECTION. Bullets are the truth wherever the outline supplies them.

    The share-based band and the bullet count are two independent answers to the same
    question, and when they disagree the drafter is handed a contradiction it cannot
    satisfy: css2026's Methods was told to write 1,080 words and one paragraph per bullet
    over three bullets, so it wrote three 360-word paragraphs — obeying both instructions
    and violating ``PARAGRAPH_BAND`` in the only way left to it. Nine paragraphs of that
    manuscript ran over 200 words, one to 369.

    So the arithmetic runs one way only. Shares decide how many BULLETS a subsection gets,
    at the outline stage, where the author gates the result. Once gated, the bullets decide
    the words, and a section that wants more words has to earn them with another bullet the
    author has seen. ``section_target`` remains the fallback for a section the outline says
    nothing about.

    Furniture is exempt. An Acknowledgements section carries its CRediT role list as
    bullets — three of them on css2026 — and charging those to prose demanded 450 words of a
    36-word passthrough the tool is forbidden to draft at all.
    """
    if is_references(heading) or is_acknowledgements(heading) or is_abstract(heading):
        return (0, 0)
    n = section_bullets(outline_md).get(_norm_heading(heading), 0) if outline_md else 0
    if not n:
        return section_target(heading, budget, shares, tolerance)
    # WORDS PER BULLET is the section's own, pinned at the skeleton and carried on every
    # release since. Assuming a flat WORDS_PER_PARAGRAPH here reads Results — gated at 166
    # a bullet — as 900 words against a contract of 996, and makes Methods and Results
    # indistinguishable to the drafter when the rate is the only thing telling them apart.
    by_head = {_norm_heading(k): v for k, v in (rates or {}).items()}
    total = n * by_head.get(_norm_heading(heading), WORDS_PER_PARAGRAPH)
    return (round(total * (1 - tolerance)), round(total * (1 + tolerance)))


def section_size(heading: str, body: str, band: tuple[int, int]) -> list[Finding]:
    """One section measured against one band. PHASE: draft.

    Split out of ``section_lengths`` so the SECTION loop can run it too. Length used to be
    checked only once the whole manuscript existed, which is the most expensive moment to
    discover it: the section repair is a well-posed rewrite of one section against its own
    outline, while the manuscript repair is the same rewrite with the paper already
    assembled around it. css2026's Background reached 1,046 words against a 540 ceiling
    without ever being told, because nothing measured a section until every section was
    written.
    """
    low, high = band
    if not low:
        return []
    n = word_count(body)
    if n < low:
        return [Finding(
            "section-thin", heading,
            f'"{heading}" runs {n} words against {low}–{high} for its share of the '
            f"paper. Develop it by about {low - n} words, following its outline "
            f"bullets — this section is under-written relative to its importance.")]
    if n > high:
        return [Finding(
            "section-fat", heading,
            f'"{heading}" runs {n} words against {low}–{high} for its share of the '
            f"paper. Tighten it by about {n - high} words so the space goes to the "
            f"sections carrying the contribution.")]
    return []


def paragraph_count(heading: str, body: str, bullets: int,
                    tolerance: int = 1) -> list[Finding]:
    """One subsection's paragraph count against its bullet count. PHASE: draft.

    The other half of what the section loop could not see. A section can sit inside its
    band with every paragraph inside ``PARAGRAPH_BAND`` and still be the wrong shape, and
    conversely a section with four paragraphs where the outline gave three bullets will
    overrun its band no matter how well-behaved each paragraph is — which is exactly how
    css2026's Background reached 260-word paragraphs while obeying every rule it was given.
    """
    if bullets <= 0:
        return []
    got = len([b for b in re.split(r"\n\s*\n", body.strip())
               if b.strip() and not b.lstrip().startswith(("#", "!["))])
    if abs(got - bullets) <= tolerance:
        return []
    return [Finding(
        "paragraph-count", heading,
        f'"{heading}" has {got} paragraph(s) for {bullets} outline bullet(s). '
        f"Write one paragraph per bullet: "
        + ("split the paragraph that covers two bullets."
           if got < bullets else
           "merge the paragraphs that cover one bullet between them."))]


def wide_paragraphs(paras: list["Paragraph"]) -> list[Finding]:
    """A paragraph carrying more argument than a paragraph holds.

    PHASE: draft. The upper half of ``PARAGRAPH_BAND``, which until now existed only in
    the outline's arithmetic and was never once checked against finished prose. A 369-word
    paragraph is not a style preference — it is a subsection that was handed more words
    than it had bullets to spend them on.
    """
    lo, hi = PARAGRAPH_BAND
    out: list[Finding] = []
    for p in paras:
        if not _is_body(p):
            continue
        n = len(p.text.split())
        if n > hi:
            out.append(Finding(
                "wide-paragraph", f"{p.heading!r} para {p.index}",
                f'This paragraph runs {n} words against a {lo}–{hi} band: "{p.snippet()}" '
                f"— split it into {max(2, round(n / WORDS_PER_PARAGRAPH))} paragraphs, each "
                f"making one point, or cut it to {hi}.", section=p.section))
    return out


def section_leaf_counts(outline_md: str) -> dict[str, int]:
    """Section heading → how many subsections the outline gives it. The denominator for
    ``section_target``, taken from the approved outline rather than guessed."""
    heads = parse_outline(outline_md)
    counts: dict[str, int] = {}
    for h in leaves(heads):
        top = None
        for c in heads:
            if c.line > h.line:
                break
            if c.level <= 2:
                top = c
        key = top.text if top is not None else h.text
        counts[key] = counts.get(key, 0) + 1
    return counts
