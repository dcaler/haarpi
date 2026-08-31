"""One pass answers every comment in kind — the contract `haarpi next` now derives its chain from.

Rework used to be scaled to the HEAVIEST need in an annotation set: one "add a section" comment
sent the whole set to a verb that could not carry an in-place edit, and the edits beside it were
dropped with no reply. These pin the redesign — a section ask and a prose edit are answered in
the SAME pass, at their own anchors, and nothing a reviewer wrote leaves without an answer.

Runnable two ways:
    pytest tests/test_sequential_rework.py
    python tests/test_sequential_rework.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

docx = pytest.importorskip("docx")

from rabbithole import redline, revise                     # noqa: E402
from rabbithole.summarize import Section                   # noqa: E402


def _doc(path: Path, headings: list[str], title: str = "Literature Review: T") -> Path:
    d = docx.Document()
    d.add_heading(title, level=1)
    d.add_heading("Narrative Review", level=2)
    for h in headings:
        d.add_heading(h, level=2)
        d.add_paragraph(f"Body of {h} [@k1].")
    d.add_heading("Annotated Bibliography", level=2)
    d.add_paragraph("Someone, A. (2020). A paper.")
    d.save(str(path))
    return path


def _rows(path: Path) -> list[tuple[str, str]]:
    """(style, accepted text) per paragraph — insertions read as accepted."""
    from haarpi.redline import flatten_paragraph
    return [(p.style.name if p.style is not None else "", flatten_paragraph(p._p))
            for p in docx.Document(str(path)).paragraphs]


# ── the styling regression ────────────────────────────────────────────────────

def test_a_grafted_body_paragraph_is_prose_not_a_heading(tmp_path):
    """The insert helper used to CLONE the previous paragraph's properties, seeded from the
    section's own heading — so every body paragraph rendered as a Heading 2, and each one fed
    the next. A whole grafted section arrived looking like a stack of headings."""
    src = _doc(tmp_path / "d.docx", ["Alpha", "Beta"])
    out = tmp_path / "out.docx"
    redline.apply_edits(src, out, [{"para": 3, "op": "insert_section", "heading": "Grafted",
                                    "paras": ["First new para.", "Second new para.",
                                              "Third new para."]}])
    rows = _rows(out)
    styles = {t: s for s, t in rows}
    assert styles["Grafted"].lower().replace(" ", "") == "heading2"
    for body in ("First new para.", "Second new para.", "Third new para."):
        assert "heading" not in styles[body].lower(), f"{body!r} rendered as {styles[body]!r}"


def test_two_sections_grafted_in_one_pass_do_not_interleave(tmp_path):
    """Both anchor to the SAME following heading, so they stack in call order. Computing an
    offset against the pre-graft document instead put the second section's paragraphs inside
    the first section's body."""
    src = _doc(tmp_path / "d.docx", ["Alpha", "Beta"])
    out = tmp_path / "out.docx"
    redline.apply_edits(src, out, [
        {"para": 3, "op": "insert_section", "heading": "One", "paras": ["1a.", "1b."]},
        {"para": 3, "op": "insert_section", "heading": "Two", "paras": ["2a.", "2b."]},
    ])
    texts = [t for _s, t in _rows(out)]
    order = [texts.index(x) for x in ("One", "1a.", "1b.", "Two", "2a.", "2b.")]
    assert order == sorted(order), f"interleaved: {texts}"
    assert texts.index("2b.") < texts.index("Beta")


def test_a_graft_never_lands_inside_the_bibliography(tmp_path):
    """The boundary test is H2-only. A looser heading test stopped at the bibliography's own
    sub-headings, which would splice a narrative section into the reference list."""
    src = _doc(tmp_path / "d.docx", ["Alpha", "Beta"])
    out = tmp_path / "out.docx"
    redline.apply_edits(src, out, [{"para": 5, "op": "insert_section",
                                    "heading": "Last", "paras": ["Tail."]}])
    texts = [t for _s, t in _rows(out)]
    assert texts.index("Last") < texts.index("Annotated Bibliography")
    assert texts.index("Tail.") < texts.index("Annotated Bibliography")


# ── a mixed set costs neither of its members ─────────────────────────────────

def test_an_edit_and_a_section_are_applied_in_the_same_pass(tmp_path):
    src = _doc(tmp_path / "d.docx", ["Alpha", "Beta"])
    out = tmp_path / "out.docx"
    applied = redline.apply_edits(src, out, [
        {"para": 3, "op": "replace", "text": "Body of Alpha, revised [@k1]."},
        {"para": 3, "op": "insert_section", "heading": "Grafted", "paras": ["New."]},
    ])
    assert applied["replace"] == 1 and applied["insert_section"] == 1
    texts = [t for _s, t in _rows(out)]
    assert "Grafted" in texts
    assert any("revised" in t for t in texts)


# ── the review's sections are read from the document, not from a stale markdown ──

def test_sections_are_recovered_from_the_docx_without_its_wrappers(tmp_path):
    src = _doc(tmp_path / "d.docx", ["Alpha", "Beta"])
    secs, heads = revise._sections_from_docx(src)
    assert [s.heading for s in secs] == ["Alpha", "Beta"]
    assert [t for _s, t in _rows(src)][heads[0]] == "Alpha"
    assert all("Narrative Review" != s.heading for s in secs)
    assert all("Annotated Bibliography" != s.heading for s in secs)
    assert secs[0].text == "Body of Alpha [@k1]."


def test_a_section_ask_is_recognised_the_way_the_planner_decomposes_it():
    assert revise._is_section_ask("I'd like to add an entire section on household impacts.")
    assert not revise._is_section_ask("Tighten this sentence.")


# ── the load-bearing block is refreshed, not carried ─────────────────────────

_BLOCK = ("## Most load-bearing sources (top 5% of 40)\n\n"
          "*Read this first to judge whether the corpus is right.*\n\n"
          "- **Someone 2020** [@k1] — carries the argument.\n")


def test_the_load_bearing_block_is_inserted_above_the_narrative(tmp_path):
    src = _doc(tmp_path / "d.docx", ["Alpha"])
    assert redline.replace_top_sources(src, _BLOCK)["top_sources"] == 1
    texts = [t for _s, t in _rows(src)]
    head = next(t for t in texts if t.startswith("Most load-bearing sources"))
    assert texts.index(head) < texts.index("Narrative Review")
    assert texts.index(head) > texts.index("Literature Review: T")


def test_a_second_refresh_replaces_the_block_rather_than_stacking_one(tmp_path):
    src = _doc(tmp_path / "d.docx", ["Alpha"])
    redline.replace_top_sources(src, _BLOCK)
    summary = redline.replace_top_sources(
        src, _BLOCK.replace("Someone 2020", "Other 2021").replace("@k1", "@k2"))
    assert summary["had_existing_block"] is True
    texts = [t for _s, t in _rows(src)]
    assert sum(t.startswith("Most load-bearing sources") for t in texts) == 1
    assert any("Other 2021" in t for t in texts)
    assert not any("Someone 2020" in t and "carries" in t for t in texts)


def test_refreshing_does_not_disturb_the_narrative_or_the_bibliography(tmp_path):
    src = _doc(tmp_path / "d.docx", ["Alpha", "Beta"])
    before = [t for _s, t in _rows(src)]
    redline.replace_top_sources(src, _BLOCK)
    after = [t for _s, t in _rows(src)]
    for original in before:
        assert original in after, f"lost or altered: {original!r}"


# ── no comment leaves without an answer ──────────────────────────────────────

def test_every_outcome_the_loop_can_record_produces_a_reply(tmp_path, monkeypatch):
    """The failure this closes: three comments were acted on, two were silently no-ops, and the
    document came back with zero replies — indistinguishable, to the reviewer, from success."""
    written: dict[str, str] = {}

    def _fake_add_replies(path, replies, author="rabbitHole", initials="rH"):
        written.update(replies)
        return len(replies)

    monkeypatch.setattr("haarpi.redline.add_replies", _fake_add_replies)
    outcomes = {
        "1": "edited",
        "2": "grafted:Household distributional equity",
        "3": "section_covered",
        "4": "corpus:sources",
        "5": "corpus:table",
        "6": "skipped",
        "7": "override:dropped a citation",
        "8": "cited_section:12",
    }
    revise._reply_to_comments(tmp_path / "x.docx", outcomes,
                              {"tier": "gap_fill", "queued": True, "needs_report": False})
    missing = set(outcomes) - set(written)
    assert not missing, f"no reply written for comment(s) {sorted(missing)}"
    assert "Household distributional equity" in written["2"]
    assert all(v.startswith("rabbitHole:") for v in written.values())


# ── a correction is total, and bounded at the bibliography ───────────────────

def _doc_with_term(path, term: str):
    d = docx.Document()
    d.add_heading("Literature Review: T", level=1)
    d.add_heading("Narrative Review", level=2)
    d.add_heading("Alpha", level=2)
    d.add_paragraph(f"The {term} framework predicts collapse [@k1].")
    d.add_paragraph(f"Later work extends the {term} model [@k2].")
    d.add_heading("Annotated Bibliography", level=2)
    d.add_paragraph("Someone, A. (2020). A paper.")
    d.add_paragraph(f"p.4: \u201cwe adopt the {term} framework\u201d")
    d.save(str(path))
    return path


def test_a_correction_reaches_every_paragraph_not_just_the_annotated_one(tmp_path):
    """The failure this closes: a wrong model name sat in six places with one comment on it.
    A span-local reviser fixed the one and left five, and the next cycle re-injected them."""
    src = _doc_with_term(tmp_path / "d.docx", "Dosi-Stiglitz-Keynes")
    out = redline.tracked_substitute(
        src, [{"wrong": "Dosi-Stiglitz-Keynes", "right": "Dystopian Schumpeter-meeting-Keynes"}])
    assert out["substitutions"] == 2 and out["paragraphs"] == 2
    texts = [t for _s, t in _rows(src)]
    assert not any("Dosi-Stiglitz-Keynes" in t for t in texts[:5])
    assert sum("Dystopian Schumpeter-meeting-Keynes" in t for t in texts) == 2


def test_a_correction_never_rewrites_a_quotation_in_the_bibliography(tmp_path):
    """The bibliography's claims are passages quoted from the sources. Making a quotation agree
    with the project's preferred term would falsify the one part meant to be verbatim."""
    src = _doc_with_term(tmp_path / "d.docx", "Dosi-Stiglitz-Keynes")
    redline.tracked_substitute(
        src, [{"wrong": "Dosi-Stiglitz-Keynes", "right": "Dystopian Schumpeter-meeting-Keynes"}])
    quoted = [t for _s, t in _rows(src) if t.startswith("p.4:")]
    assert quoted and "Dosi-Stiglitz-Keynes" in quoted[0], "a quoted passage was rewritten"


def test_a_correction_matching_nothing_is_reported_as_zero(tmp_path):
    src = _doc_with_term(tmp_path / "d.docx", "Dosi-Stiglitz-Keynes")
    out = redline.tracked_substitute(src, [{"wrong": "Keynes-Schumpeter", "right": "DSK"}])
    assert out["substitutions"] == 0 and out["per_term"] == {"Keynes-Schumpeter": 0}


def test_a_correction_lands_as_a_tracked_change(tmp_path):
    """It arrives as something the reviewer can reject, like every other edit."""
    src = _doc_with_term(tmp_path / "d.docx", "Dosi-Stiglitz-Keynes")
    redline.tracked_substitute(src, [{"wrong": "Dosi-Stiglitz-Keynes", "right": "DSK"}])
    import zipfile
    xml = zipfile.ZipFile(src).read("word/document.xml").decode()
    assert "<w:ins " in xml and "<w:del " in xml


def test_corrections_come_from_the_planners_ledger_not_a_fresh_guess(tmp_path):
    """`haarpi next` already decided wrong/right and applied it to the brief and the config. A
    reviser making its own call could disagree with what is already on disk."""
    from haarpi import project as hproject
    root = tmp_path / "proj"
    (root / "litReview").mkdir(parents=True)
    hproject.record_plan(root, {"type": "plan", "stage": "litreview",
                                "corrections": [{"wrong": "A B", "right": "C D"},
                                                {"wrong": "", "right": "x"}]})
    from rabbithole import config as rhconfig
    got = revise._pending_corrections(rhconfig.project_paths(str(root)))
    assert got == [{"wrong": "A B", "right": "C D"}]


def test_no_ledger_is_no_corrections_not_a_crash(tmp_path):
    from rabbithole import config as rhconfig
    (tmp_path / "litReview").mkdir()
    assert revise._pending_corrections(rhconfig.project_paths(str(tmp_path))) == []


# ── refresh picks the draft a reader would open ──────────────────────────────

def test_refresh_targets_the_newest_draft_annotated_or_not(tmp_path):
    from rabbithole import config as rhconfig, refresh
    out = tmp_path / "litReview" / "output"
    out.mkdir(parents=True)
    import time as _t
    for i, name in enumerate(["260810_p_litreview_ra.docx",
                              "260815_p_litreview_ra_DCR.docx",
                              "260820_p_litreview_ra_DCR_ra.docx"]):
        f = out / name
        _doc(f, ["Alpha"])
        _t.time()
        import os
        os.utime(f, (1000 + i * 100, 1000 + i * 100))
    got = refresh.latest_draft(rhconfig.project_paths(str(tmp_path)))
    assert got.name == "260820_p_litreview_ra_DCR_ra.docx"


def test_refresh_on_an_empty_output_dir_finds_nothing(tmp_path):
    from rabbithole import config as rhconfig, refresh
    (tmp_path / "litReview" / "output").mkdir(parents=True)
    assert refresh.latest_draft(rhconfig.project_paths(str(tmp_path))) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
