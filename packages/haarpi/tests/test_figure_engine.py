"""haarpi.figure — the shared diagram-as-code figure engine (see DESIGN_figure_engine.md).

Figures are revision-chain artifacts resolved through haarpi.naming — no side index. These cover the
deterministic emitters, the chain-named pool, the resolve precedence (release > hand-edit > draft),
and the clobber guard (the engine only ever writes `_ra`, so a hand-edit is safe by construction).
"""

from __future__ import annotations

import shutil

import pytest

from haarpi import figure, naming, project

_HAS_DOT = shutil.which("dot") is not None


# ── deterministic emitters ─────────────────────────────────────────────────────

def test_stage_dag_is_the_real_pipeline_graph():
    spec = figure.stage_dag(project.DEFAULT_STAGES)
    src = spec.source
    assert spec.format == "dot" and spec.id == "stageLadder"
    assert "digraph stageLadder" in src
    assert "// caption: The HAARPi stage pipeline." in src        # provenance rides with the source
    # the reordered edges we built earlier show up verbatim
    assert '"litreview" -> "design"' in src
    assert '"design" -> "build"' in src
    assert '"design" -> "experiments"' in src


def test_experiment_dag_links_experiments_to_their_outputs():
    exps = [{"id": "E1", "title": "price sweep",
             "outputs": [{"kind": "figure", "caption": "response curve"}]},
            {"id": "E2", "question": "does friction bind?", "outputs": []}]
    src = figure.experiment_dag(exps).source
    assert '"E1" -> "E1_o0"' in src
    assert "figure: response curve" in src
    assert "does friction bind?" in src           # E2 falls back to the question


def test_module_graph_chains_the_build_modules():
    spec = {"modules": [{"id": "M0", "name": "core", "tasks": [{"id": "t1"}, {"id": "t2"}]},
                        {"id": "M1", "name": "sweep", "tasks": [{"id": "t3"}]}]}
    src = figure.module_graph(spec).source
    assert '"M0" -> "M1"' in src                                  # modules advance in sequence
    assert "M0: core (2 tasks)" in src and "M1: sweep (1 task)" in src


# ── chain-named pool + naming resolution ───────────────────────────────────────

def test_write_figure_names_a_tool_draft_on_the_chain(tmp_path):
    figure.write_figure(tmp_path, "demo", figure.stage_dag(project.DEFAULT_STAGES))
    figs = list((tmp_path / "figures").glob("*_stageLadder_ra.dot"))
    assert len(figs) == 1                                         # {ds}_demo_stageLadder_ra.dot
    ds, chain, ext = naming.parse(figs[0], "demo")
    assert chain == ["stageLadder", "ra"] and not naming.is_release(chain)


@pytest.mark.skipif(not _HAS_DOT, reason="graphviz `dot` not installed")
def test_render_produces_real_svg_and_png_export(tmp_path):
    out = figure.write_figure(tmp_path, "demo", figure.stage_dag(project.DEFAULT_STAGES))
    svg = out["svg"]
    assert svg is not None
    assert "<svg" in svg.read_text()
    # PNG is a derived export from the canonical SVG
    png = figure.resolve(tmp_path, "demo", "stageLadder", "png", width=800)
    assert png is not None and png.suffix == ".png" and png.stat().st_size > 0


def test_rerender_reuses_the_datestamp_not_a_pile_of_drafts(tmp_path):
    figure.write_figure(tmp_path, "demo", figure.stage_dag(project.DEFAULT_STAGES), render_svg=False)
    figure.write_figure(tmp_path, "demo", figure.stage_dag(project.DEFAULT_STAGES), render_svg=False)
    assert len(list((tmp_path / "figures").glob("*_stageLadder_ra.dot"))) == 1   # overwrote, not piled


def test_resolve_prefers_hand_edit_over_a_fresh_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(figure, "render", lambda spec, out: (out.write_text("<svg>gen</svg>"), out)[1])
    out = figure.write_figure(tmp_path, "demo", figure.stage_dag(project.DEFAULT_STAGES))
    ds = out["datestamp"]
    figdir = tmp_path / "figures"
    # the human polishes it in Inkscape → saves with their initials (the convention)
    edit = figdir / f"{ds}_demo_stageLadder_ra_DCR.svg"
    edit.write_text("<svg>inkscape</svg>")
    # even though the engine re-runs and refreshes the _ra draft, the hand-edit is authoritative…
    figure.write_figure(tmp_path, "demo", figure.stage_dag(project.DEFAULT_STAGES))
    assert figure.resolve(tmp_path, "demo", "stageLadder", "svg") == edit
    assert edit.read_text() == "<svg>inkscape</svg>"             # …and never clobbered


def test_resolve_prefers_a_release_over_everything(tmp_path):
    figdir = tmp_path / "figures"
    figdir.mkdir()
    (figdir / "260101_demo_stageLadder_ra.svg").write_text("<svg>draft</svg>")
    (figdir / "260101_demo_stageLadder_ra_DCR.svg").write_text("<svg>edit</svg>")
    (figdir / "260102_demo_stageLadder.svg").write_text("<svg>minted</svg>")     # token-free = release
    got = figure.resolve(tmp_path, "demo", "stageLadder", "svg")
    assert got is not None and got.read_text() == "<svg>minted</svg>"


# ── conceptual figures: the LLM authors DOT, validated ─────────────────────────

class _Brain:
    """A fake coordinator returning queued replies (last one repeats)."""
    def __init__(self, *replies):
        self._r = list(replies)
        self.calls = 0

    def coordinator(self, prompt, system="", **kw):
        self.calls += 1
        return self._r[min(self.calls - 1, len(self._r) - 1)]


def test_extract_dot_from_fenced_and_bare():
    assert "digraph G" in figure._extract_dot("```dot\ndigraph G { a->b }\n```")
    assert figure._extract_dot("prose then digraph X { a->b } and more").startswith("digraph X")
    assert figure._extract_dot("no diagram here at all") is None


def test_compose_authors_validated_conceptual_dot():
    b = _Brain("Here you go:\n```dot\ndigraph fw { rankdir=LR; A -> B; B -> C; }\n```")
    spec = figure.compose(b, "the analytical framework", "A leads to B leads to C", fig_id="fw")
    assert spec.format == "dot" and spec.kind == "schematic"
    assert "A -> B" in spec.source
    assert spec.provenance["mode"] == "conceptual" and "status" not in spec.provenance


@pytest.mark.skipif(not _HAS_DOT, reason="graphviz `dot` needed to validate/repair")
def test_compose_repairs_invalid_dot_then_stubs_on_failure():
    # first reply is invalid DOT (dangling edge), the repair reply is valid
    b = _Brain("```dot\ndigraph { A -> }\n```", "```dot\ndigraph { A -> B; }\n```")
    spec = figure.compose(b, "x", "", fig_id="fw")
    assert "A -> B" in spec.source and b.calls == 2 and "status" not in spec.provenance

    # unrecoverable → a labelled stub that still COMPILES (never a crash), flagged for the human
    stub = figure.compose(_Brain("I can't draw that."), "the framework", "", fig_id="fw")
    assert stub.provenance["status"] == "stub"
    assert figure._dot_validates(stub.source)


def test_list_ids_and_caption_from_the_pool(tmp_path):
    figure.write_figure(tmp_path, "demo", figure.stage_dag(project.DEFAULT_STAGES), render_svg=False)
    figure.write_figure(tmp_path, "demo", figure.experiment_dag([{"id": "E1", "outputs": []}]),
                        render_svg=False)
    assert set(figure.list_ids(tmp_path, "demo")) == {"stageLadder", "experimentDag"}
    assert figure.caption_of(tmp_path, "demo", "stageLadder") == "The HAARPi stage pipeline."
    assert figure.caption_of(tmp_path, "demo", "nope") == ""


def test_run_compose_goes_through_the_policy():
    class P:
        def brain(self):
            return _Brain("```dot\ndigraph p { X -> Y; }\n```")
        def context(self, request):
            return "X relates to Y"
        def log(self, msg):
            pass
    spec = figure.run_compose(P(), "draw the link", fig_id="p")
    assert "X -> Y" in spec.source and spec.provenance["mode"] == "conceptual"
