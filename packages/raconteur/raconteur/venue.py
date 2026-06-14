"""Venue analysis: brainstorm → web research → multi-pass synthesis → docx."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from .brain import Brain
from .config import ProjectConfig, GlobalConfig, VenueConfig
from .context import load_litreview
from .naming import parse, today
from .render import to_docx
from .revise import read_text, build_revision_context
from .notify import send_email

# ── shared system ─────────────────────────────────────────────────────────────

_SYSTEM = (
    "You are an expert academic publishing advisor. "
    "You help researchers identify and evaluate venues for scholarly papers."
)

# ── brainstorm (coordinator) ──────────────────────────────────────────────────

_BRAINSTORM_SYSTEM = (
    "You identify candidate academic publication venues for research papers. "
    "Respond with ONLY a JSON object — no markdown, no explanation."
)

_BRAINSTORM_PROMPT = """\
Identify candidate academic publication venues for this research paper.

Description: {description}
Topic: {topic}
Focus: {focus}
{litrev_context}
Generate 8–12 candidate venues (a mix of journals and conferences) appropriate for \
this paper. Include venues at different tiers — field-leading, strong specialist, \
and broad/general.

For each candidate return:
- "name": full venue name
- "abbreviation": common abbreviation
- "type": "journal" or "conference"
- "homepage_url": most likely homepage URL
- "openalex_query": for journals — full name string to search OpenAlex
- "wikicfp_query": for conferences — 2–4 word search query for WikiCFP
- "why_candidate": one sentence on why this venue fits this paper

Return ONLY a valid JSON object with key "candidates" containing the list."""

# ── draft analysis (coordinator) ──────────────────────────────────────────────

_DRAFT_PROMPT = """\
Write a complete venue analysis document for an academic paper.
Today's date: {today}
Submission window: {today} through {deadline_end} (8 months).

Paper profile:
{paper_profile}

Web research results:
{web_content}

Write a venue analysis in markdown with exactly these sections, in order:

## Research question
One sentence capturing the central question this paper answers.

## Core novelty claim
2–3 sentences: what is genuinely new and for whom.

## Paper profile
Bulleted summary: method, scale, case study (if any), primary contribution, \
target audience, non-audience.

## Venue shortlist

### Tier 1
The 1–2 best-fit venues. For each:
**VENUE NAME**
- *Why:* specific reason this venue fits this paper's contribution and audience.
- *Risk:* what could cause rejection.
- *Mitigation or timing:* how to manage the risk, or when to submit.

### Tier 2
2–3 strong alternatives. Same structure.

### Tier 3
1–2 broad or fallback venues. Same structure.

## Conference opportunities
Table with columns: Conference | Submission deadline | Conference date | Proceedings | Notes

Include ONLY conferences with submission deadlines between {today} and {deadline_end}.
Below the table, list 2–3 conferences worth watching that are outside the window \
or have unconfirmed dates — note them as "On the radar."

If web research found no confirmed deadlines for a conference, note the deadline \
as "not confirmed — check cfp." Do not omit a conference just because its deadline \
is unverified.

## Recommendation
**Primary target: [VENUE NAME].**
[2–3 sentences: why this venue above all others, given the paper's specific contribution \
and audience. Be direct and give concrete reasoning, not just "good fit."]

**Fallback: [VENUE NAME].**
[1–2 sentences: when to use this instead and why.]

If a compelling two-stage plan exists (conference + journal), describe it here \
under a ### Two-stage plan subheading.

Write in a direct, analytical register. Explain reasoning; do not just list attributes. \
Output only the markdown document — no preamble or closing remarks."""

# ── critique (coordinator) ────────────────────────────────────────────────────

_CRITIQUE_PROMPT = """\
Critique this venue analysis. Identify every specific problem to fix.

Paper profile:
{paper_profile}

Venue analysis:
{analysis}

Check for:
1. Recommendation section missing, or recommendation not backed by explicit reasoning \
(not just "good fit" — must state why this venue over the alternatives)
2. Fallback recommendation missing or without stated conditions for use
3. Conference table: deadlines listed outside {today}–{deadline_end} window \
without being marked "On the radar"; or confirmed deadlines missing from table
4. Format specifications (page limits, word limits, citation style) claimed without \
being supported by the web research content — these must be flagged as unverified
5. Tier assignments inconsistent with the paper's stated audience and contribution type
6. Paper profile section that mischaracterises the contribution type, method, or audience
7. Venue profiles that give generic reasons ("broad scope", "high impact") rather than \
paper-specific reasons

Output a numbered list of specific, actionable problems. One line each. \
Skip checks with no issues. No preamble."""

# ── revise (coordinator) ──────────────────────────────────────────────────────

_REVISE_PROMPT = """\
Revise this venue analysis to fix every problem in the critique.

Paper profile:
{paper_profile}

Current analysis:
{analysis}

Problems to fix:
{critique}

Fix every listed problem. Preserve what is already correct. Maintain the same \
section structure (Research question, Core novelty claim, Paper profile, Venue \
shortlist [Tier 1/2/3], Conference opportunities, Recommendation). \
Output only the revised markdown document — no preamble."""

# ── user-annotation revision (coordinator) ────────────────────────────────────

_USER_REVISE_PROMPT = """\
Revise this venue analysis based on the researcher's annotations.

Paper profile:
{paper_profile}

Current venue analysis:
{analysis}

Researcher's annotations:
{revisions}

Instructions:
- Incorporate all tracked insertions
- Remove all tracked deletions
- Address each reviewer comment with substantive revision to the relevant section
- The Recommendation section must always be present with explicit reasoning
- Maintain the same section structure (Research question, Core novelty claim, \
Paper profile, Venue shortlist [Tier 1/2/3], Conference opportunities, Recommendation)
- Output only the revised markdown document — no preamble."""

# ── recommendation extraction (worker) ───────────────────────────────────────

_EXTRACT_REC_PROMPT = """\
Extract the primary recommended venue and its format details from this venue analysis.

Return ONLY valid JSON with exactly these keys (null for any unknown value):
{{"venue": "full venue name", "type": "journal or conference", \
"page_limit": null, "word_limit": null, "citation_style": "", \
"abstract_limit": null, "columns": 1}}

Do not guess format details that are not stated in the text. Use null, not zero.

Venue analysis:
{analysis}"""

# ── helpers ───────────────────────────────────────────────────────────────────

def _strip_fence(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.split("\n")[:-1])
    return raw.strip()


def _deadline_end() -> str:
    from .web import _add_months
    d = _add_months(date.today(), 8)
    return d.strftime("%Y-%m-%d")


def _paper_profile_block(cfg: ProjectConfig, litrev: str) -> str:
    parts = [
        f"Title: {cfg.title}" if cfg.title else "",
        f"Description: {cfg.description}",
        f"Topic: {cfg.topic}" if cfg.topic else "",
        f"Focus: {cfg.focus}" if cfg.focus else "",
    ]
    block = "\n".join(p for p in parts if p)
    if litrev:
        snippet = litrev[:2000] + ("\n\n[truncated]" if len(litrev) > 2000 else "")
        block += f"\n\nLiterature review context:\n{snippet}"
    return block


def _brainstorm_candidates(
    brain: Brain, cfg: ProjectConfig, litrev: str
) -> list[dict]:
    litrev_context = ""
    if litrev:
        snippet = litrev[:3000] + ("\n\n[truncated]" if len(litrev) > 3000 else "")
        litrev_context = f"Literature review context:\n{snippet}\n"

    print("[raconteur] brainstorming venue candidates…", file=sys.stderr)
    raw = brain.coordinator(
        _BRAINSTORM_PROMPT.format(
            description=cfg.description,
            topic=cfg.topic or "",
            focus=cfg.focus or "",
            litrev_context=litrev_context,
        ),
        system=_BRAINSTORM_SYSTEM,
        num_ctx=8192,
    )
    try:
        data = json.loads(_strip_fence(raw))
        candidates = data.get("candidates", data) if isinstance(data, dict) else data
        if not isinstance(candidates, list):
            candidates = []
    except Exception as e:
        print(f"[warn] could not parse brainstorm JSON: {e}", file=sys.stderr)
        candidates = []

    print(f"[raconteur] {len(candidates)} venue candidates identified", file=sys.stderr)
    return candidates


def _web_research(candidates: list[dict], email: str) -> str:
    """Fetch web data for each candidate; return formatted string for LLM consumption."""
    from . import web

    email = email or ""
    sections: list[str] = []

    for c in candidates:
        name = c.get("name", "unknown")
        vtype = c.get("type", "")
        why = c.get("why_candidate", "")
        section_lines = [f"### {name} ({c.get('abbreviation', '')})", f"Type: {vtype}"]
        if why:
            section_lines.append(f"Candidate rationale: {why}")

        if vtype == "journal":
            query = c.get("openalex_query") or name
            print(f"[web] querying OpenAlex: {query!r}", file=sys.stderr)
            meta = web.openalex_source(query, email)
            if meta:
                section_lines.append("OpenAlex metadata:")
                if meta.get("display_name"):
                    section_lines.append(f"  Display name: {meta['display_name']}")
                if meta.get("h_index") is not None:
                    section_lines.append(f"  h-index: {meta['h_index']}")
                if meta.get("impact_factor") is not None:
                    section_lines.append(f"  2yr impact factor: {meta['impact_factor']:.3f}")
                if meta.get("apc_usd") is not None:
                    section_lines.append(f"  APC: USD {meta['apc_usd']}")
                elif meta.get("is_oa"):
                    section_lines.append("  Open access: yes")
                if meta.get("in_doaj"):
                    section_lines.append("  In DOAJ: yes")
                if meta.get("homepage_url"):
                    homepage = meta["homepage_url"]
                else:
                    homepage = c.get("homepage_url", "")
            else:
                homepage = c.get("homepage_url", "")
                section_lines.append("OpenAlex: no match found")

            if homepage:
                print(f"[web] fetching homepage: {homepage}", file=sys.stderr)
                page_text = web.fetch_page_text(homepage, email, max_chars=2000)
                if page_text:
                    section_lines.append(f"Homepage text ({homepage}):")
                    section_lines.append(f"  {page_text[:1500]}")

        elif vtype == "conference":
            query = c.get("wikicfp_query") or name
            print(f"[web] searching WikiCFP: {query!r}", file=sys.stderr)
            conf_results = web.wikicfp_conference(query, email)
            if conf_results:
                section_lines.append(f"WikiCFP results ({len(conf_results)} found in deadline window):")
                for r in conf_results[:3]:
                    entry_lines = [f"  Event: {r.get('name', '')}"]
                    if r.get("full_name"):
                        entry_lines.append(f"    Full name: {r['full_name']}")
                    if r.get("submission_deadline"):
                        entry_lines.append(f"    Submission deadline: {r['submission_deadline']}")
                    if r.get("notification_date"):
                        entry_lines.append(f"    Notification: {r['notification_date']}")
                    if r.get("conference_dates"):
                        entry_lines.append(f"    Conference dates: {r['conference_dates']}")
                    if r.get("location"):
                        entry_lines.append(f"    Location: {r['location']}")
                    if r.get("homepage"):
                        entry_lines.append(f"    Homepage: {r['homepage']}")
                    section_lines.extend(entry_lines)
            else:
                section_lines.append("WikiCFP: no matching conferences found in deadline window")

            homepage = c.get("homepage_url", "")
            if homepage:
                print(f"[web] fetching conference homepage: {homepage}", file=sys.stderr)
                page_text = web.fetch_page_text(homepage, email, max_chars=1500)
                if page_text:
                    section_lines.append(f"Homepage text ({homepage}):")
                    section_lines.append(f"  {page_text[:1200]}")

        sections.append("\n".join(section_lines))

    return "\n\n".join(sections) if sections else "No web content retrieved."


def _draft_analysis(
    brain: Brain, paper_profile: str, web_content: str
) -> str:
    today_str = date.today().strftime("%Y-%m-%d")
    deadline = _deadline_end()
    print("[raconteur] drafting venue analysis…", file=sys.stderr)
    return brain.coordinator(
        _DRAFT_PROMPT.format(
            today=today_str,
            deadline_end=deadline,
            paper_profile=paper_profile,
            web_content=web_content,
        ),
        system=_SYSTEM,
        num_ctx=12288,
    )


def _critique_revise(brain: Brain, analysis: str, paper_profile: str) -> str:
    today_str = date.today().strftime("%Y-%m-%d")
    deadline = _deadline_end()

    print("[raconteur] critiquing venue analysis…", file=sys.stderr)
    critique = brain.coordinator(
        _CRITIQUE_PROMPT.format(
            paper_profile=paper_profile,
            analysis=analysis,
            today=today_str,
            deadline_end=deadline,
        ),
        system=_SYSTEM,
        num_ctx=8192,
    )
    print(f"[raconteur] critique findings:\n{critique}", file=sys.stderr)

    print("[raconteur] revising venue analysis…", file=sys.stderr)
    revised = brain.coordinator(
        _REVISE_PROMPT.format(
            paper_profile=paper_profile,
            analysis=analysis,
            critique=critique,
        ),
        system=_SYSTEM,
        num_ctx=12288,
    )
    return revised


def _find_venue_user_revision(paper_dir: Path, short_title: str) -> Path | None:
    """Find a user-annotated venue docx (chain contains 'venue', last != 'ra')."""
    candidates = []
    for p in paper_dir.glob("*.docx"):
        result = parse(p, short_title)
        if result is None:
            continue
        _, chain, _ = result
        lc = [x.lower() for x in chain]
        if "venue" in lc and lc[-1] != "ra":
            candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _write(project_dir: Path, cfg: ProjectConfig, paper_dir: Path, text: str) -> str:
    """Write venue_analysis.md (fixed) and dated docx. Returns the md text."""
    md_path = paper_dir / "venue_analysis.md"
    md_path.write_text(text, encoding="utf-8")
    print(f"[raconteur] wrote {md_path.relative_to(project_dir)}", file=sys.stderr)

    docx_name = f"{today()}_{cfg.short_title}_venue_ra.docx"
    docx_src = paper_dir / f"{today()}_{cfg.short_title}_venue_ra.md"
    docx_src.write_text(text, encoding="utf-8")
    docx = to_docx(docx_src)
    docx_src.unlink(missing_ok=True)
    if docx:
        print(f"[raconteur] wrote {docx.relative_to(project_dir)}", file=sys.stderr)
    else:
        print(f"[raconteur] docx render failed; md is at {md_path.name}", file=sys.stderr)

    return text


def _prompt_yaml_update(project_dir: Path, cfg: ProjectConfig, brain: Brain, analysis: str) -> None:
    """Extract recommendation and optionally update raconteur.yaml."""
    import sys as _sys

    if not _sys.stdin.isatty():
        return

    print("[raconteur] extracting recommendation…", file=sys.stderr)
    raw = brain.worker(
        _EXTRACT_REC_PROMPT.format(analysis=analysis),
        num_ctx=4096,
    )
    try:
        rec = json.loads(_strip_fence(raw))
    except Exception:
        rec = {}

    venue_name = rec.get("venue", "")
    if not venue_name:
        print("[raconteur] could not extract venue recommendation — check venue_analysis.md manually", file=sys.stderr)
        return

    print(f"\nRecommendation: {venue_name}")
    answer = input("Accept this venue and update raconteur.yaml? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        print("No update made. Annotate the docx and re-run 'raconteur venue' to revise.")
        return

    cfg.venue = VenueConfig(
        name=venue_name,
        page_limit=rec.get("page_limit") or None,
        word_limit=rec.get("word_limit") or None,
        citation_style=rec.get("citation_style") or "",
        columns=int(rec.get("columns") or 1),
        abstract_limit=rec.get("abstract_limit") or None,
    )
    cfg.save(project_dir)
    print(f"[raconteur] updated raconteur.yaml with venue: {venue_name}")


# ── fresh run ─────────────────────────────────────────────────────────────────

def _venue_fresh(
    project_dir: Path, cfg: ProjectConfig, brain: Brain, paper_dir: Path, gcfg: GlobalConfig
) -> str:
    litrev = load_litreview(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    paper_profile = _paper_profile_block(cfg, litrev)

    # Pass 1: brainstorm candidates
    candidates = _brainstorm_candidates(brain, cfg, litrev)

    # Step 2: web research
    email = gcfg.notify_to or ""
    print("[raconteur] researching venues on the web…", file=sys.stderr)
    web_content = _web_research(candidates, email)

    # Pass 2: draft full analysis (web content + paper profile → markdown)
    draft = _draft_analysis(brain, paper_profile, web_content)

    # Passes 3–4: one critique → revise cycle
    final = _critique_revise(brain, draft, paper_profile)

    return _write(project_dir, cfg, paper_dir, final)


# ── user-annotation revision ──────────────────────────────────────────────────

def _venue_revise(
    project_dir: Path, cfg: ProjectConfig, brain: Brain, paper_dir: Path, user_rev: Path
) -> str:
    litrev = load_litreview(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    paper_profile = _paper_profile_block(cfg, litrev)

    analysis_text = read_text(user_rev)
    revision_notes = build_revision_context(user_rev)

    if not revision_notes:
        print(
            "[warn] no comments or track changes found — generating fresh analysis instead",
            file=sys.stderr,
        )
        return _venue_fresh(project_dir, cfg, brain, paper_dir, GlobalConfig.load())

    print("[raconteur] revising venue analysis from annotations…", file=sys.stderr)
    revised = brain.coordinator(
        _USER_REVISE_PROMPT.format(
            paper_profile=paper_profile,
            analysis=analysis_text,
            revisions=revision_notes,
        ),
        system=_SYSTEM,
        num_ctx=12288,
    )
    return _write(project_dir, cfg, paper_dir, revised)


# ── entry point ───────────────────────────────────────────────────────────────

def run(project_dir: Path) -> None:
    if not ProjectConfig.exists(project_dir):
        print("[error] no paper/raconteur.yaml found — run 'raconteur init' first", file=sys.stderr)
        raise SystemExit(1)

    cfg = ProjectConfig.load(project_dir)
    gcfg = GlobalConfig.load()
    paper_dir = project_dir / "paper"
    paper_dir.mkdir(exist_ok=True)

    if not cfg.description:
        print("[error] no research description — run 'raconteur init' first", file=sys.stderr)
        raise SystemExit(1)

    brain = Brain(gcfg, coordinator=cfg.brain.coordinator_model)

    user_rev = _find_venue_user_revision(paper_dir, cfg.short_title)
    if user_rev:
        print(f"[raconteur] found venue revision: {user_rev.name}", file=sys.stderr)
        final_text = _venue_revise(project_dir, cfg, brain, paper_dir, user_rev)
    else:
        final_text = _venue_fresh(project_dir, cfg, brain, paper_dir, gcfg)

    _prompt_yaml_update(project_dir, cfg, brain, final_text)

    send_email(
        f"raconteur venue done: {cfg.short_title}",
        f"Venue analysis complete for '{cfg.title or cfg.short_title}'.\nProject: {project_dir}",
        gcfg,
    )
