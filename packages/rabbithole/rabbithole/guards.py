"""Deterministic guards — the polestar, made mechanical.

rabbitHole exists to give raconteur a broad AND deep foundation of verifiable knowledge.
Breadth, depth, and verifiability are all checkable in Python; none of them should be
delegated to an LLM's judgement, and everything in this module is a pure function so the
checks can be unit-tested and run over an existing document as a fixture.

The division of labour, applied everywhere in the codebase:

    Python decides THAT something is wrong, precisely, and states it as an imperative.
    The LLM decides only what cannot be computed — whether an idea belongs, whether an
    edit answers a comment.

Three families:

  VERIFIABILITY — a claim severed from what grounds it. A dropped [@citekey], a dropped
    equation, an unresolvable key. Each looks founded and is not, which the polestar calls
    "worse than useless". These run on every path.

  BREADTH — the corpus is the foundation the argument rests on, not a menu it selects
    from. Disposition (every source cited or explicitly rejected), accretion (each
    paragraph after a section's first brings in a new source), triangulation (a claim on
    one source is a lead; two or three that agree, qualify or conflict is a foundation).
    These run on SYNTHESIS ONLY — see the scoping rule below.

  MINIMALITY — a redline is surgical. The set of sentences a reviser touched is computed,
    not estimated, so "you rewrote sentences the comment did not bear on" stops being an
    LLM judgement call.

Scoping rule (important): breadth guards must NEVER run on the redline path. A comment
like "explain consonance" would otherwise cause the reviser to inject citations into
unrelated paragraphs to satisfy accretion — breadth demands new keys, minimality forbids
collateral change, and both are correct. Different passes, different guard sets. Comments
that genuinely need breadth are routed to the corpus chain instead.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

# ── primitives ────────────────────────────────────────────────────────────────
# Canonical here so redline/summarize/revise share one definition of "a citation",
# "a sentence", and "an opaque atom". Historically each module had its own.

# A pandoc citation tag, e.g. [@schelling1971] or the grouped [@a; @b].
CITE_TAG_RE = re.compile(r"\[@[^\]\s]+\]")

# A citation written in author-year narrative form instead of a [@citekey] tag. Invisible
# to the citekey-keyed bibliography, so it silently unverifies the claim it supports.
AUTHOR_YEAR_RE = re.compile(r"[A-Z][a-z]+(?:\s+et al\.|’s|'s)?\s*\((?:19|20)\d\d")

# An opaque non-text atom (an equation, a hyperlink, a footnote reference) standing in for
# a docx element the LLM must carry through verbatim but must never author. See redline.
SENTINEL_RE = re.compile(r"⟦[a-z]+:\d+⟧")


def all_citekeys(text: str) -> list[str]:
    """Every individual citekey, splitting grouped citations like [@a; @b; @c].

    The naive [@([^\\]]+)] capture treats a grouped bracket as one key, so any source cited
    only inside a multi-citation bracket would be dropped from locate and the bibliography.
    """
    keys: list[str] = []
    for grp in re.findall(r"\[@([^\]]+)\]", text):
        for part in grp.split(";"):
            k = part.strip().lstrip("@").strip()
            if k:
                keys.append(k)
    return keys


def sentence_units(text: str) -> list[str]:
    """Split into sentence units, each carrying its trailing whitespace, so that
    ``"".join(sentence_units(t)) == t``.

    Losslessness is the point: it lets a diff preserve an unchanged sentence byte-for-byte,
    so its [@citekey] tags and its equations survive a revision untouched.
    """
    if not text:
        return []
    toks = re.split(r"(?<=[.!?])(\s+)", text)
    units: list[str] = []
    i = 0
    while i < len(toks):
        unit = toks[i]
        if i + 1 < len(toks):  # the captured whitespace separator after this sentence
            unit += toks[i + 1]
        if unit:
            units.append(unit)
        i += 2
    return units


def sentinels(text: str) -> list[str]:
    return SENTINEL_RE.findall(text)


# ── findings ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Finding:
    """One guard failure, phrased so it can be handed straight to a reviser.

    ``imperative`` is what the model is told to do about it — never a question. ``where``
    locates it for a human reading the run log. ``section`` locates it for the machine: a
    repair re-drafts one section, not the whole review, so the finding has to say which one.
    ``None`` means the finding is about the narrative as a whole.
    """
    kind: str
    where: str
    imperative: str
    section: int | None = None

    def __str__(self) -> str:
        return f"{self.where}: {self.imperative}"


def by_section(findings: list[Finding]) -> dict[int, list[Finding]]:
    """Group section-scoped findings for repair. Narrative-wide findings are dropped —
    the caller handles those (they have no single section to re-draft)."""
    out: dict[int, list[Finding]] = {}
    for f in findings:
        if f.section is not None and f.section >= 0:
            out.setdefault(f.section, []).append(f)
    return out


# ── narrative structure ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Paragraph:
    section: int            # index of the enclosing "## " section; -1 = preamble
    index: int              # paragraph position within its section, 0-based
    text: str
    keys: tuple[str, ...]   # citekeys, in order of appearance

    @property
    def distinct(self) -> frozenset[str]:
        return frozenset(self.keys)

    def snippet(self, n: int = 160) -> str:
        s = " ".join(self.text.split())
        return s[:n] + ("…" if len(s) > n else "")


def parse_paragraphs(narrative: str) -> list[Paragraph]:
    """Body paragraphs of a markdown narrative, tagged with their enclosing section.

    Heading lines are stripped from a block; a block left empty was heading-only and is not
    a paragraph. A ``## `` heading opens a new section.
    """
    out: list[Paragraph] = []
    section = -1
    pos = 0
    for block in re.split(r"\n\s*\n", narrative):
        heads = [ln for ln in block.splitlines() if ln.lstrip().startswith("#")]
        prose = "\n".join(ln for ln in block.splitlines()
                          if not ln.lstrip().startswith("#")).strip()
        if any(h.lstrip().startswith("## ") for h in heads):
            section += 1
            pos = 0
        if not prose:
            continue
        out.append(Paragraph(section, pos, prose, tuple(all_citekeys(prose))))
        pos += 1
    return out


# ── VERIFIABILITY ─────────────────────────────────────────────────────────────

def uncited_paragraphs(paras: list[Paragraph]) -> list[Finding]:
    """A paragraph with no citation states ideas it cannot ground. The oldest guard."""
    return [
        Finding("uncited", f"section {p.section} para {p.index}",
                f'This paragraph cites no source: "{p.snippet()}" — state the source(s) '
                f'for its ideas as [@citekey] tags from the digest, or merge it into an '
                f'adjacent paragraph that already carries the evidence. Do not keep a '
                f'transition- or conclusion-only paragraph.', section=p.section)
        for p in paras if not p.keys
    ]


def unresolved_keys(text: str, known: set[str]) -> list[Finding]:
    """A [@citekey] with no corpus entry behind it. Looks founded; isn't."""
    bad = sorted(set(all_citekeys(text)) - known)
    return [
        Finding("unresolved-key", "narrative",
                f"These [@citekey] tags match no source in the corpus — replace each with a "
                f"key from the digest or remove the claim: {', '.join('[@' + k + ']' for k in bad)}.")
    ] if bad else []


def author_year_prose(text: str) -> list[Finding]:
    ay = sorted(set(AUTHOR_YEAR_RE.findall(text)))
    return [
        Finding("author-year", "narrative",
                f"Citations must be [@citekey] tags from the evidence list, not author-year "
                f"prose — an author-year citation is invisible to the bibliography. Rewrite "
                f"these as [@citekey]: {', '.join(ay[:6])}.")
    ] if ay else []


def dropped_citekeys(old: str, new: str) -> list[Finding]:
    lost = set(all_citekeys(old)) - set(all_citekeys(new))
    return [
        Finding("dropped-citekey", "paragraph",
                "Restore these [@citekey] tags dropped from the original (unless a comment "
                "asked to remove that source): "
                + ", ".join(f"[@{k}]" for k in sorted(lost)) + ".")
    ] if lost else []


def duplicate_citekeys(citekeys: dict[int, str]) -> list[Finding]:
    """Two corpus entries under one citekey. The key→index map keeps only one of them, so
    the other is uncitable and the bibliography may render the wrong record — typically the
    poorer one (no DOI, no year). A dedup failure upstream, visible only here."""
    seen: dict[str, list[int]] = {}
    for i, k in citekeys.items():
        seen.setdefault(k, []).append(i)
    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    return [
        Finding("duplicate-citekey", "corpus",
                f"Corpus entries {v} share the citekey [@{k}] — only one can be cited or "
                f"appear in the bibliography, and which one wins is arbitrary. De-duplicate "
                f"the corpus (the richer record, with a DOI, should survive).")
        for k, v in sorted(dupes.items())
    ]


def dropped_sentinels(old: str, new: str) -> list[Finding]:
    """An equation severed from the claim it verifies — the same defect as a dropped
    citekey, and treated identically. A number is the most verifiable thing a claim can
    carry; "substantially improves" cannot be checked against source text, "ρ=0.95" can."""
    lost = [s for s in sentinels(old) if s not in set(sentinels(new))]
    return [
        Finding("dropped-equation", "paragraph",
                f"These placeholders stand for equations in the original and must appear, "
                f"unaltered, in your output: {', '.join(sorted(set(lost)))}. Keep each one "
                f"in the sentence whose claim it supports. Never retype an equation as prose "
                f"and never invent a new placeholder.")
    ] if lost else []


def invented_sentinels(old: str, new: str) -> list[Finding]:
    """A placeholder the original never had. rabbitHole cannot author an equation, so a
    made-up sentinel resolves to nothing and would silently vanish on write."""
    made_up = sorted(set(sentinels(new)) - set(sentinels(old)))
    return [
        Finding("invented-equation", "paragraph",
                f"These placeholders do not exist in the original: {', '.join(made_up)}. "
                f"Use only the placeholders you were given, exactly as written, and never "
                f"create one.")
    ] if made_up else []


# ── BREADTH ───────────────────────────────────────────────────────────────────

def accretion_violations(paras: list[Paragraph]) -> list[Finding]:
    """Each paragraph after a section's first must bring in at least one source not yet
    used in that section — otherwise the evidence base does not grow and the section is a
    single idea restated, not an argument built by accretion."""
    out: list[Finding] = []
    seen_by_section: dict[int, set[str]] = {}
    for p in paras:
        seen = seen_by_section.setdefault(p.section, set())
        if p.index > 0 and p.keys and not (p.distinct - seen):
            out.append(Finding(
                "accretion", f"section {p.section} para {p.index}",
                f'This paragraph introduces no source the section has not already used '
                f'({", ".join("[@" + k + "]" for k in sorted(p.distinct))}): '
                f'"{p.snippet()}" — bring in a new source from the digest that bears on '
                f'this idea and connect it to what the section has already established.',
                section=p.section))
        seen |= p.distinct
    return out


def triangulation_violations(paras: list[Paragraph], min_sources: int = 2) -> list[Finding]:
    """A claim resting on one source is a lead; two or three that agree, qualify, or
    conflict is a foundation. Weaving is citation-dense by construction — you cannot
    compare, qualify, or connect sources you have not cited."""
    return [
        Finding("triangulation", f"section {p.section} para {p.index}",
                f'This paragraph rests on a single source ([@{p.keys[0]}]): "{p.snippet()}" '
                f'— a claim supported by one source is a lead, not a foundation. Bring in at '
                f'least {min_sources} sources from the digest that agree with, qualify, or '
                f'conflict with it, and set them against each other.', section=p.section)
        for p in paras if 0 < len(p.distinct) < min_sources
    ]


# Roughly one source per three sentences of claim-making. A flat "cite N sources" penalises
# a short paragraph and lets a long one coast; scaling with length asks the same density of
# both. Three is deliberately lenient — a woven paragraph usually runs denser.
_SENTENCES_PER_SOURCE = 3


def sparse_paragraphs(paras: list[Paragraph],
                      sentences_per_source: int = _SENTENCES_PER_SOURCE) -> list[Finding]:
    """A long paragraph resting on few sources is assertion with a citation attached.

    Catches what a flat per-paragraph floor misses: eight sentences of argument standing on
    two sources passes `triangulation_violations` and is still thin.
    """
    out: list[Finding] = []
    for p in paras:
        n_sents = len(sentence_units(p.text))
        want = max(1, -(-n_sents // sentences_per_source))  # ceil
        if p.keys and len(p.distinct) < want:
            out.append(Finding(
                "sparse-paragraph", f"section {p.section} para {p.index}",
                f'{n_sents} sentences of argument rest on only {len(p.distinct)} source(s) '
                f'({", ".join("[@" + k + "]" for k in sorted(p.distinct))}): "{p.snippet()}" '
                f'— ground it in at least {want} sources from the digest, or cut the claims '
                f'the evidence does not reach.', section=p.section))
    return out


def short_sections(paras: list[Paragraph], min_paragraphs: int = 3) -> list[Finding]:
    """A heading over one paragraph reporting one source's findings is an annotated-
    bibliography entry — the structural failure the synthesis rules exist to avoid. The rule
    ("develop each section by ACCRETION across at least three paragraphs") was prompt-only,
    so nothing counted the paragraphs. Now something does."""
    counts: dict[int, int] = {}
    for p in paras:
        counts[p.section] = counts.get(p.section, 0) + 1
    return [
        Finding("short-section", f"section {sec}",
                f"This section is {n} paragraph(s) long. A section develops its idea by "
                f"accretion across at least {min_paragraphs} paragraphs: the first puts a few "
                f"cited ideas on the table, each later one brings in NEW sources and connects "
                f"them to what is already established. Develop it, or merge it into the "
                f"section whose idea it belongs to.", section=sec)
        for sec, n in sorted(counts.items())
        if sec >= 0 and n < min_paragraphs
    ]


def thin_sections(paras: list[Paragraph], min_sources: int = 3) -> list[Finding]:
    """A section resting on fewer than `min_sources` distinct sources is a report on those
    sources, not a synthesis of an idea."""
    keys_by_section: dict[int, set[str]] = {}
    for p in paras:
        keys_by_section.setdefault(p.section, set()).update(p.distinct)
    return [
        Finding("thin-section", f"section {sec}",
                f"This section draws on only {len(keys)} source(s) "
                f"({', '.join('[@' + k + ']' for k in sorted(keys))}) — too few to weave an "
                f"idea. Either develop it with further sources from the digest, or merge it "
                f"into the section whose idea it belongs to.", section=sec)
        for sec, keys in sorted(keys_by_section.items())
        if sec >= 0 and 0 < len(keys) < min_sources
    ]


@dataclass
class Disposition:
    """Every curated source is either cited, or explicitly rejected with a reason.

    No threshold to argue about and no percentage to game: it forces a decision per source
    instead of letting silence do the work. Silence is how a 70-source corpus becomes a
    15-source narrative without anyone deciding to drop 55 sources.

    ``unplaced`` — neither cited nor rejected — is the defect. A rejection is a decision and
    is recorded in the run log; it is the escape valve that keeps a breadth requirement from
    forcing junk sources into paragraphs where they do not belong.
    """
    cited: set[str] = field(default_factory=set)
    rejected: set[str] = field(default_factory=set)
    unplaced: set[str] = field(default_factory=set)

    @property
    def total(self) -> int:
        return len(self.cited) + len(self.rejected) + len(self.unplaced)


def disposition(narrative: str, corpus_keys: set[str],
                rejected: dict[str, str] | None = None) -> Disposition:
    rejected = rejected or {}
    cited = set(all_citekeys(narrative)) & corpus_keys
    rej = (set(rejected) & corpus_keys) - cited
    return Disposition(cited=cited, rejected=rej,
                       unplaced=corpus_keys - cited - rej)


def unplaced_findings(d: Disposition, digest_by_key: dict[str, str],
                      limit: int = 25) -> list[Finding]:
    """The roster of sources that were never decided about, with their digest lines, so the
    reviser can place them — or reject them with a reason."""
    if not d.unplaced:
        return []
    listing = "\n".join(f"  {digest_by_key.get(k, '[@' + k + ']')}"
                        for k in sorted(d.unplaced)[:limit])
    more = (f"\n  … and {len(d.unplaced) - limit} more"
            if len(d.unplaced) > limit else "")
    return [Finding(
        "unplaced-source", "narrative",
        f"{len(d.unplaced)} curated source(s) are neither cited nor rejected. The corpus is "
        f"the foundation this review rests on, not a menu to select from — every source must "
        f"be decided about. For each, either weave it into the paragraph whose idea it bears "
        f"on, or reject it with a one-line reason:\n" + listing + more)]


# ── MINIMALITY ────────────────────────────────────────────────────────────────

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

    Inactive only when the reviewer selected the WHOLE paragraph (or no range resolved):
    then every sentence is anchored and there is nothing to over-reach into. Anything short
    of that — even "all but one sentence" — leaves a sentence the comment does not bear on,
    and rewriting it discards grounding nobody asked to change.
    """
    if not anchored or len(anchored) >= n_sentences:
        return []
    extra = sorted(touched - anchored)
    if not extra:
        return []
    return [Finding(
        "minimal-edit", "paragraph",
        f"You rewrote sentence(s) {', '.join(str(i + 1) for i in extra)}, which the "
        f"comment does not bear on. The comment anchors to sentence(s) "
        f"{', '.join(str(i + 1) for i in sorted(anchored))}. Restore the others word for "
        f"word — every rewritten sentence loses the grounding it carried.")]


# ── the polestar, as a number ─────────────────────────────────────────────────

@dataclass
class Metrics:
    sources_cited: int
    corpus_size: int
    rejected: int
    unplaced: int
    paragraphs: int
    mean_sources_per_para: float
    triangulated: int          # paragraphs citing >= 2 distinct sources
    unresolved: int

    def line(self) -> str:
        return (f"sources cited {self.sources_cited}/{self.corpus_size} · "
                f"rejected {self.rejected} · unplaced {self.unplaced} · "
                f"mean sources/para {self.mean_sources_per_para:.1f} · "
                f"triangulated {self.triangulated}/{self.paragraphs} · "
                f"unresolved keys {self.unresolved}")


def metrics(narrative: str, corpus_keys: set[str],
            rejected: dict[str, str] | None = None) -> Metrics:
    """The polestar as one printable line. Same computation the guards enforce — printed
    first, enforced later, so a regression is visible per run."""
    paras = parse_paragraphs(narrative)
    d = disposition(narrative, corpus_keys, rejected)
    n = len(paras) or 1
    return Metrics(
        sources_cited=len(d.cited), corpus_size=len(corpus_keys),
        rejected=len(d.rejected), unplaced=len(d.unplaced),
        paragraphs=len(paras),
        mean_sources_per_para=sum(len(p.distinct) for p in paras) / n,
        triangulated=sum(1 for p in paras if len(p.distinct) >= 2),
        unresolved=len(set(all_citekeys(narrative)) - corpus_keys),
    )


# ── batteries ─────────────────────────────────────────────────────────────────

def verifiability_battery(narrative: str, corpus_keys: set[str]) -> list[Finding]:
    """Runs on every path. A failure here means a claim that looks founded and is not.

    With no corpus supplied there is nothing to resolve keys against, so that check is
    skipped rather than condemning every citation in the narrative.
    """
    paras = parse_paragraphs(narrative)
    return (uncited_paragraphs(paras)
            + (unresolved_keys(narrative, corpus_keys) if corpus_keys else [])
            + author_year_prose(narrative))


def breadth_battery(narrative: str, corpus_keys: set[str],
                    digest_by_key: dict[str, str],
                    rejected: dict[str, str] | None = None,
                    min_sources_per_para: int = 2,
                    min_sources_per_section: int = 3) -> list[Finding]:
    """SYNTHESIS ONLY. Never call this from the redline path — see the module docstring."""
    paras = parse_paragraphs(narrative)
    d = disposition(narrative, corpus_keys, rejected)
    return (unplaced_findings(d, digest_by_key)
            + short_sections(paras)
            + accretion_violations(paras)
            + triangulation_violations(paras, min_sources_per_para)
            + sparse_paragraphs(paras)
            + thin_sections(paras, min_sources_per_section))


def as_critique(findings: list[Finding], header: str) -> str:
    """Render findings as an imperative block for a reviser prompt. Never a question."""
    if not findings:
        return ""
    body = "\n".join(f"- {f.imperative}" for f in findings)
    return f"{header}\n{body}"
