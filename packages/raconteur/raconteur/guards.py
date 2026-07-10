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

_REFERENCES_RE = re.compile(r"^\d*\.?\s*references?\b", re.IGNORECASE)
_ABSTRACT_RE = re.compile(r"^\d*\.?\s*abstract\b", re.IGNORECASE)


def is_references(heading: str) -> bool:
    return bool(_REFERENCES_RE.match(heading))


def section_kind(heading: str) -> str:
    """Classify a heading → 'abstract' | 'litrev' | 'methods' | 'results' | 'references' | 'other'.

    Order matters: results before methods, because "experimental design" and "model
    evaluation" each hit both keyword sets and the later-stage kind should win.
    """
    if is_references(heading):
        return "references"
    if _ABSTRACT_RE.match(heading):
        return "abstract"
    h = heading.lower()
    if any(kw in h for kw in RESULTS_KW):
        return "results"
    if any(kw in h for kw in CODE_KW):
        return "methods"
    if any(kw in h for kw in LITREV_KW):
        return "litrev"
    return "other"


def expects_citations(kind: str) -> bool:
    """Does the bibliography ground this kind of section?

    Methods and Results are grounded in the writeup and the results files. Demanding a
    citation floor there is a category error. An abstract summarises rather than cites,
    and References are not prose at all.
    """
    return kind in ("litrev", "other")


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
        return section_kind(self.heading)

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

    def __str__(self) -> str:
        return (f"citekeys resolved {self.citekeys_resolved}/{self.citekeys_total} · "
                f"uncited body paragraphs {self.uncited} · sparse {self.sparse} · "
                f"sections {self.sections}")


def metrics(markdown: str, known: set[str]) -> Metrics:
    """One line that says, mechanically, whether the deliverable met the bar."""
    paras = parse_paragraphs(markdown)
    keys = set(all_citekeys(markdown))
    return Metrics(
        citekeys_resolved=len(keys & known),
        citekeys_total=len(keys),
        uncited=len(uncited_paragraphs(paras)),
        sparse=len(sparse_paragraphs(paras)),
        sections=len({p.section for p in paras if p.section >= 0}),
    )
