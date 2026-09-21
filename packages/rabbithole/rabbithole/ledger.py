"""The run's accountability artifacts: output/refs.bib and work/disposition.json.

Both used to be written by `report` alone. `revise` produces a document too — the redline
path regenerates the whole annotated bibliography, and a graft splices new sections carrying
new citations — but it wrote neither, so the bibliography and the ledger silently described
the *previous* run. A review whose narrative cites a source its own refs.bib has never heard
of is the failure this module exists to make impossible: the artifacts are refreshed on every
path that emits a document, from one implementation.

`reconcile` is the check that was missing entirely. `guards.metrics(...).unresolved` compares
the narrative against the CORPUS, which is the wrong denominator for the question a reader
actually has — refs.bib is what the .docx and every downstream consumer bind. A narrative can
be perfectly resolved against the corpus and still cite thirteen keys that no bibliography
entry defines.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import guards

# A bibtex entry opener: @type{key,
_BIB_ENTRY = re.compile(r"^@(\w+)\s*\{\s*([^,\s]+)\s*,", re.MULTILINE)

# The annotated bibliography restates every citekey it annotates. Counting those as
# "cited" would make any reconciliation vacuously clean, so the narrative is taken to
# END at that heading — in markdown ("## Annotated Bibliography") or in docx body text,
# where the heading arrives as a bare line.
_BIBLIO_HEADING = re.compile(r"^\s*(?:#{1,6}\s*)?Annotated Bibliography\s*$",
                             re.MULTILINE | re.IGNORECASE)


def narrative_only(text: str) -> str:
    """Everything before the annotated bibliography. Unchanged if there is no such heading."""
    m = _BIBLIO_HEADING.search(text or "")
    return text[:m.start()] if m else (text or "")


def bib_keys(bib_text: str) -> set[str]:
    """Every citekey defined in a refs.bib. Empty set for empty/missing input."""
    return {m.group(2) for m in _BIB_ENTRY.finditer(bib_text or "")}


def _year_stem(key: str) -> str:
    """A citekey with any trailing 4-digit year removed: gerdesCOMMONSIM2014 -> gerdesCOMMONSIM."""
    return re.sub(r"(19|20)\d{2}$", "", key)


def near_miss_keys(cited: set[str], known: set[str]) -> dict[str, list[str]]:
    """Cited keys that match a known key except for the trailing year.

    `gerdesCOMMONSIM2014` vs `gerdesCOMMONSIM2023`, `jafferCan` vs `jafferCan2020` — one paper
    cited under two spellings. Reported, never auto-rewritten: two genuinely different papers
    by the same author on the same project would collide under any automatic rule, and a
    silently rewritten citation is a worse defect than a flagged one.
    """
    by_stem: dict[str, list[str]] = {}
    for k in known:
        by_stem.setdefault(_year_stem(k), []).append(k)
    out: dict[str, list[str]] = {}
    for k in cited:
        if k in known:
            continue
        candidates = sorted(by_stem.get(_year_stem(k), []))
        if candidates:
            out[k] = candidates
    return out


def _stem_year(key: str) -> tuple[str, str]:
    m = re.search(r"(19|20)\d{2}$", key)
    return (key[:m.start()], m.group(0)) if m else (key, "")


def repair_map(cited: set[str], known: set[str]) -> tuple[dict, list]:
    """Unknown citekeys that resolve to EXACTLY ONE known key, and those that resolve to none.

    A model asked to cite from a list writes the keys from memory, and the mistakes are
    systematic rather than random. elephantRoom's minted review carried eight keys refs.bib had
    never heard of, ~7% of everything it cited, and six were mangled versions of real ones:

        amendolaEnergy                          -> amendolaEnergy2024      (year dropped)
        hoekstraCreatingAgentBasedEnergy2017    -> hoekstraCreating2017    (title words added)

    Those six are mechanically recoverable and there is no reason to ship them broken. The
    other two matched nothing at all and are not repairable by any rule — they are returned
    separately, because a fabricated citation is a different problem from a mistyped one and
    only a person can decide what the sentence should have rested on.

    ONLY UNAMBIGUOUS MATCHES ARE REPAIRED. Two real papers by one author can share a stem, and
    a silently rewritten citation pointing at the wrong paper is worse than a flagged broken
    one — which is why `near_miss_keys` reports rather than rewrites. A single candidate is
    not that case.
    """
    by_stem: dict[str, list[str]] = {}
    by_lower: dict[str, list[str]] = {}
    for k in known:
        by_stem.setdefault(_year_stem(k), []).append(k)
        by_lower.setdefault(k.lower(), []).append(k)

    fixes: dict[str, str] = {}
    unmatched: list[str] = []
    for bad in sorted(cited - known):
        stem, year = _stem_year(bad)
        cands = set(by_stem.get(stem, []))            # same stem, any year
        cands |= set(by_lower.get(bad.lower(), []))   # case only
        if not cands:
            # One stem is a prefix of the other AND the years agree: a key written with more
            # (or fewer) title words than Better BibTeX chose. The year has to match, or
            # `grimaudClimate2011` would capture `grimaudClimate2019`.
            for k in known:
                kstem, kyear = _stem_year(k)
                if not year or kyear != year:
                    continue
                if kstem.lower().startswith(stem.lower()) or stem.lower().startswith(kstem.lower()):
                    cands.add(k)
        cands.discard(bad)
        if len(cands) == 1:
            fixes[bad] = cands.pop()
        elif not cands:
            unmatched.append(bad)
    return fixes, unmatched


def apply_repairs(text: str, fixes: dict) -> str:
    """Rewrite `[@bad]` to `[@good]`. Only inside a citation tag — never bare prose."""
    for bad, good in fixes.items():
        text = re.sub(r"\[@" + re.escape(bad) + r"\]", f"[@{good}]", text)
    return text


@dataclass
class Reconciliation:
    """How the narrative, the corpus and refs.bib line up. All three, not two."""
    cited: set[str] = field(default_factory=set)
    corpus: set[str] = field(default_factory=set)
    bib: set[str] = field(default_factory=set)
    bib_missing: bool = False          # refs.bib absent or unreadable

    @property
    def unbibbed(self) -> list[str]:
        """Cited in the narrative, defined by no refs.bib entry. The reader-facing defect."""
        if self.bib_missing:
            return []
        return sorted(self.cited - self.bib)

    @property
    def orphaned(self) -> list[str]:
        """Defined in refs.bib, cited nowhere. Stale entries from an earlier corpus."""
        if self.bib_missing:
            return []
        return sorted(self.bib - self.cited)

    @property
    def near_misses(self) -> dict[str, list[str]]:
        return near_miss_keys(self.cited, self.corpus | self.bib)

    @property
    def clean(self) -> bool:
        return not self.unbibbed and not self.near_misses


def read_bib(paths) -> str | None:
    """output/refs.bib as text, or None if the project has none."""
    p = paths.output / "refs.bib"
    try:
        return p.read_text(encoding="utf-8") if p.exists() else None
    except OSError:
        return None


def reconcile(narrative: str, corpus_keys: set[str], bib_text: str | None) -> Reconciliation:
    """Three-way reconciliation. `bib_text=None` means refs.bib could not be read."""
    return Reconciliation(
        cited=set(guards.all_citekeys(narrative)),
        corpus=set(corpus_keys),
        bib=bib_keys(bib_text or ""),
        bib_missing=bib_text is None,
    )


def reconciliation_findings(r: Reconciliation) -> list[guards.Finding]:
    """Reconciliation defects as guard Findings, so they travel the existing machinery."""
    out: list[guards.Finding] = []
    if r.unbibbed:
        listing = ", ".join(f"[@{k}]" for k in r.unbibbed[:12])
        more = f" … and {len(r.unbibbed) - 12} more" if len(r.unbibbed) > 12 else ""
        out.append(guards.Finding(
            "unbibbed-key", "narrative",
            f"{len(r.unbibbed)} citekey(s) are cited in the narrative but defined by no "
            f"refs.bib entry. The .docx and every downstream consumer bind refs.bib, so these "
            f"citations do not resolve for a reader: {listing}{more}"))
    if r.near_misses:
        pairs = "; ".join(f"[@{k}] ~ {'/'.join('[@' + c + ']' for c in v)}"
                          for k, v in sorted(r.near_misses.items())[:8])
        out.append(guards.Finding(
            "citekey-drift", "narrative",
            f"{len(r.near_misses)} citekey(s) differ from a known key only in the trailing "
            f"year — the same paper cited under two spellings: {pairs}"))
    return out


def print_reconciliation(r: Reconciliation, *, prefix: str = "  ") -> None:
    """Say it loudly. Silence used to look identical to health."""
    if r.bib_missing:
        print(f"{prefix}[bib] refs.bib not written this run — reconciliation skipped.",
              file=sys.stderr)
        return
    if r.clean:
        print(f"{prefix}[bib] {len(r.cited)} cited key(s) all resolve in refs.bib"
              + (f"; {len(r.orphaned)} unused entr(y/ies)." if r.orphaned else "."))
        return
    print(f"\n{prefix}{'!' * 58}", file=sys.stderr)
    for f in reconciliation_findings(r):
        print(f"{prefix}[{f.kind}] {f.imperative}", file=sys.stderr)
    if r.orphaned:
        print(f"{prefix}[bib] {len(r.orphaned)} refs.bib entr(y/ies) cited nowhere: "
              f"{', '.join(r.orphaned[:8])}"
              + (" …" if len(r.orphaned) > 8 else ""), file=sys.stderr)
    print(f"{prefix}{'!' * 58}\n", file=sys.stderr)


# ──────────────────────────────────────────────────────────────────────────
# The artifacts
# ──────────────────────────────────────────────────────────────────────────
def export_bibtex(cfg, gc, paths, citekeys: dict[int, str], corpus: list) -> Path | None:
    """Fetch BibTeX from Zotero, align citekeys with the narrative, write output/refs.bib."""
    from .summarize import _patch_bibtex_keys   # local: summarize imports this module
    if not gc.have_zotero:
        return None
    collection_key = cfg.zotero.get("collection_key", "")
    if not collection_key:
        return None
    from . import zotero as _zotero
    try:
        zc = _zotero.ZoteroClient(gc)
        bib_text = zc.collection_bibtex(collection_key)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] BibTeX export failed ({e}); refs.bib not written.", file=sys.stderr)
        return None
    from .corpus import unambiguous
    dois, title_years, titles = [], [], []
    for i, c in enumerate(corpus):
        ck = citekeys.get(i)
        if not ck:
            continue
        if getattr(c, "doi_key", None):
            dois.append((c.doi_key, ck))
        tk = getattr(c, "title_key", None)
        if tk:
            titles.append((tk, ck))
            if getattr(c, "year", None):
                title_years.append(((tk, str(c.year)), ck))
    # Ambiguity-safe in this direction too: two corpus records under one title would
    # otherwise stamp the last one's key onto the other's block in refs.bib.
    bib_text = _patch_bibtex_keys(bib_text, unambiguous(dois), unambiguous(titles),
                                  unambiguous(title_years))
    out = paths.output / "refs.bib"
    out.write_text(bib_text, encoding="utf-8")
    return out


def write_disposition(paths, corpus: list, citekeys: dict[int, str],
                      narrative: str, rejected: dict[str, str],
                      *, verb: str = "report",
                      reconciliation: Reconciliation | None = None) -> Path:
    """Persist what happened to every curated source. The polestar, auditable after the run.

    An unplaced source — neither cited nor rejected — is the defect the ledger exists to make
    impossible to miss. Silence used to look identical to a decision.

    `verb` and `generated_at` are provenance: a ledger that cannot say which run wrote it
    cannot be caught being stale, and staleness is exactly how this went wrong.
    """
    corpus_keys = set(citekeys.values())
    d = guards.disposition(narrative, corpus_keys, rejected)
    title_by_key = {citekeys[i]: c.title for i, c in enumerate(corpus) if i in citekeys}
    payload: dict = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "generated_by": verb,
        "metrics": guards.metrics(narrative, corpus_keys, rejected).__dict__,
        "cited": sorted(d.cited),
        "rejected": {k: rejected[k] for k in sorted(d.rejected)},
        "unplaced": {k: title_by_key.get(k, "") for k in sorted(d.unplaced)},
    }
    if reconciliation is not None:
        payload["bibliography"] = {
            "entries": len(reconciliation.bib),
            "unbibbed": reconciliation.unbibbed,
            "orphaned": reconciliation.orphaned,
            "near_misses": reconciliation.near_misses,
        }
    out = paths.work / "disposition.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def refresh(cfg, gc, paths, corpus: list, citekeys: dict[int, str],
            narrative: str, rejected: dict[str, str] | None = None,
            *, verb: str) -> tuple[Path | None, Path, Reconciliation]:
    """Rewrite refs.bib and disposition.json for the document just produced, and reconcile.

    Every path that emits a document calls this — `report` and both `revise` sub-paths.
    Returns (bib_path, disposition_path, reconciliation).
    """
    bib_path = export_bibtex(cfg, gc, paths, citekeys, corpus)
    existing = paths.output / "refs.bib"
    bib_text: str | None
    if bib_path is not None:
        bib_text = bib_path.read_text(encoding="utf-8")
    elif existing.exists():
        # Export was unavailable (no Zotero, or it failed). Reconcile against whatever
        # bibliography the project actually has rather than skipping the check — a stale
        # refs.bib is precisely the case worth reporting.
        bib_text = existing.read_text(encoding="utf-8")
    else:
        bib_text = None
    rec = reconcile(narrative, set(citekeys.values()), bib_text)
    disp_path = write_disposition(paths, corpus, citekeys, narrative, rejected or {},
                                  verb=verb, reconciliation=rec)
    return bib_path, disp_path, rec


# ──────────────────────────────────────────────────────────────────────────
# The synthesis trace
# ──────────────────────────────────────────────────────────────────────────
def write_synthesis_trace(paths, trace: dict) -> Path:
    """Persist what the synthesis SAW and CHOSE, per section and per source.

    Until this existed, a source that died between `work/annotations/` and the draft left no
    trace at all. The only way to diagnose one was to reverse-engineer `work/located/` and
    notice that sources which reached the review carry drafted prose with a [@key] tag while
    sources that did not carry a semicolon-joined blob of raw annotation fields. That is not
    an audit trail; it is an accident that happened to be legible.

    disposition.json says what happened to each source. This says WHY: which sections
    shortlisted it, how strongly it matched, which sections were offered it and what they said
    when they declined.
    """
    out = paths.work / "synthesis.json"
    out.write_text(json.dumps(trace, indent=2, ensure_ascii=False, default=str),
                   encoding="utf-8")
    return out
