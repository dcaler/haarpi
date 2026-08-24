"""rabbitHole.steering — the litreview config-steering helpers.

Planning moved to haarpi.planner (`haarpi next` is the sole planner); what stayed in rabbitHole
is writing litreview's own numbered config so the next gather/report is aimed at the reviewer's
ask. These pin the focus-append logic that both writers share.

Runnable two ways:
    pytest tests/test_steering.py
    python tests/test_steering.py
"""

from __future__ import annotations

from rabbithole import steering


def test_append_focus_chains_onto_an_existing_line():
    class _Cfg:
        focus = "existing focus"
    cfg = _Cfg()
    steering._append_focus(cfg, "", "added")
    assert cfg.focus == "existing focus; added"


def test_append_focus_handles_an_empty_focus():
    class _Cfg:
        focus = ""
    cfg = _Cfg()
    steering._append_focus(cfg, "only")
    assert cfg.focus == "only"


def test_append_focus_skips_empty_additions():
    class _Cfg:
        focus = "base"
    cfg = _Cfg()
    steering._append_focus(cfg, "", None or "", "kept")
    assert cfg.focus == "base; kept"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    raise SystemExit(1 if failures else 0)


def test_append_focus_does_not_repeat_a_clause_it_already_carries():
    """The focus is cumulative and reaches every later gather verbatim, so a repeat re-weights
    retrieval toward whatever got said twice."""
    class _Cfg:
        focus = "carbon taxes; Expand coverage of: consumption smoothing"
    cfg = _Cfg()
    steering._append_focus(cfg, "consumption smoothing")
    assert cfg.focus == "carbon taxes; Expand coverage of: consumption smoothing"


def test_a_section_query_arriving_twice_is_appended_once():
    """The real duplication: `_write_gap_config` passes a section task's query both prefixed
    (via gather_topics) and bare (via extra_focus), which doubled every cycle's directives."""
    class _Cfg:
        focus = "carbon taxes"
    cfg = _Cfg()
    query = "nation-level supply chain dependency reduction"
    steering._append_focus(cfg, f"Expand coverage of: {query}", query)
    assert cfg.focus == f"carbon taxes; Expand coverage of: {query}"
    assert cfg.focus.count("supply chain") == 1


def test_append_focus_still_adds_a_genuinely_new_clause():
    class _Cfg:
        focus = "carbon taxes; Expand coverage of: consumption smoothing"
    cfg = _Cfg()
    steering._append_focus(cfg, "border carbon adjustments")
    assert cfg.focus.endswith("; border carbon adjustments")


# ── factual corrections (the writeback) ──────────────────────────────────────

def test_sub_term_matches_across_the_separators_a_name_picks_up():
    """One project carried the same model name as hyphen, en-dash and space forms."""
    sub = steering._sub_term
    for variant in ("Dosi-Stiglitz-Keynes", "Dosi–Stiglitz–Keynes", "Dosi Stiglitz Keynes",
                    "dosi-stiglitz-keynes"):
        text, n = sub(f"grounded in the {variant} framework", "Dosi-Stiglitz-Keynes", "DSK")
        assert (text, n) == ("grounded in the DSK framework", 1), variant


def test_sub_term_never_fires_inside_a_longer_word():
    assert steering._sub_term("Dosi-Stiglitz-Keynesian models", "Dosi-Stiglitz-Keynes", "DSK")[1] == 0


def test_sub_term_counts_every_occurrence():
    text = "The DSK model. Later, the DSK model again, and the DSK model."
    assert steering._sub_term(text, "DSK", "Dystopian Schumpeter-meeting-Keynes")[1] == 3


def test_apply_correction_rewrites_the_config_and_the_draft(tmp_path, monkeypatch):
    """The brief and the config drive gather; the draft is what the reviewer reads back. A
    correction that reaches only one of them leaves the error live in the other."""
    from rabbithole import config as rhconfig

    class _Cfg:
        topic = "the Dosi-Stiglitz-Keynes family"
        focus = "Dosi-Stiglitz-Keynes calibration"
        research_prompt = "ground it in Dosi–Stiglitz–Keynes work"
        domain_anchor = ""
        exclude_topics = ""

    out = tmp_path / "litReview" / "output"
    out.mkdir(parents=True)
    draft = out / "260821_x_litreview_ra.md"
    draft.write_text("The Dosi-Stiglitz-Keynes model. Again: Dosi Stiglitz Keynes.\n",
                     encoding="utf-8")
    theirs = out / "260821_x_litreview_ra_DCR.md"          # a human's copy — never touched
    theirs.write_text("The Dosi-Stiglitz-Keynes model.\n", encoding="utf-8")

    saved = {}
    cfg = _Cfg()
    monkeypatch.setattr(rhconfig, "load_project", lambda d: cfg)
    monkeypatch.setattr(rhconfig, "next_project_file", lambda d: tmp_path / "litrev_2.yaml")
    monkeypatch.setattr(rhconfig, "save_project_to", lambda c, fp: saved.setdefault("fp", fp))
    monkeypatch.setattr(rhconfig, "project_paths",
                        lambda d: type("P", (), {"output": out})())

    counts = steering.apply_correction(str(tmp_path), "Dosi-Stiglitz-Keynes", "DSK")

    assert counts["litrev_2.yaml"] == 3                    # topic + focus + research_prompt
    assert counts["260821_x_litreview_ra.md"] == 2
    assert "DSK" in cfg.topic and "Dosi" not in cfg.focus
    assert "Dosi" not in draft.read_text()
    assert "Dosi-Stiglitz-Keynes" in theirs.read_text(), "a human's markup is never rewritten"


def test_apply_correction_reports_nothing_when_the_term_is_absent():
    """Silence would hide the useful signal: the reviewer named a term the project does not hold."""
    from rabbithole import config as rhconfig
    import types
    cfg = types.SimpleNamespace(topic="unrelated", focus="", research_prompt="",
                                domain_anchor="", exclude_topics="")
    orig = rhconfig.load_project
    rhconfig.load_project = lambda d: cfg
    try:
        assert steering.apply_correction("/nonexistent", "Nowhere Term", "X") == {}
    finally:
        rhconfig.load_project = orig
