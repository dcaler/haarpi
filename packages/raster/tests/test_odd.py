"""Tests for `raster odd` — the ODD protocol, written at the END of the build.

An ODD describes what a model IS, well enough that another modeller could rebuild it, and
it is the appendix a paper about an ABM carries. So it is a summary of what was BUILT, not
a design document: a design document written beforehand describes what was intended.

The prose comes from the model, so these tests cover the two halves the model does not
touch — the evidence gathered mechanically out of the built tree, and the gate that keeps
the whole thing away from projects that are not agent-based models.
"""

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from raster import odd
from raster.config import Config
from raster.spec import Project

MODEL_PY = '''
"""The lattice and its scheduler."""
import random


class Chord:
    """One agent: a pitch-class set at a lattice position."""

    def __init__(self, pcs, position, tolerance):
        self.pcs = pcs
        self.position = position
        self.tolerance = tolerance
        self.satisfied = False


class Lattice:
    """The 1D sliding window the chords occupy."""

    def __init__(self, size, radius):
        self.size = size
        self.radius = radius

    def step(self):
        """One round: evaluate satisfaction, then relocate the unsatisfied."""
        for c in self.cells:
            c.position = random.choice(self.empty())
'''

DESIGN_MD = """# Chords — Design

## 1. Aim
Translate Schelling segregation into a one-dimensional audio space.

## 3. Key concepts / domain model
Segregation emerges from local preference alone; no chord optimises globally.
"""


def make_project(tmp_path, kind="abm", with_inputs=False) -> Project:
    code = tmp_path / "code"
    (code / "chords").mkdir(parents=True, exist_ok=True)
    (code / "chords" / "model.py").write_text(textwrap.dedent(MODEL_PY))
    (code / "designdocs").mkdir(exist_ok=True)
    (code / "designdocs" / "DESIGN.md").write_text(DESIGN_MD)
    (code / "configs").mkdir(exist_ok=True)
    (code / "configs" / "base.yaml").write_text("size: 64\nradius: 6\ntolerance: 0.3\n")
    (code / "tests").mkdir(exist_ok=True)
    (code / "tests" / "test_overlap.py").write_text(
        '"""Pitch-class overlap matches the calibrated distance."""\nFLOOR = 0.2\n')
    if with_inputs:
        (code / "inputs").mkdir(exist_ok=True)
        (code / "inputs" / "beethoven5.mid").write_bytes(b"MThd")
    ry = {"project": "Chords", "package": "chords", "brief": "recover a phrase"}
    if kind:
        ry["kind"] = kind
    return Project(root=tmp_path, code=code, cfg=Config(), ry=ry, spec={"modules": []})


# ── the gate ─────────────────────────────────────────────────────────────────

def test_an_abm_is_declared_never_inferred(tmp_path):
    """Guessing from whether the code says "agent" would put a protocol on a project with
    no business carrying one. An ODD is a claim about what the model IS."""
    assert odd.is_abm(make_project(tmp_path)) is True
    assert odd.is_abm(make_project(tmp_path, kind="")) is False
    assert odd.is_abm(make_project(tmp_path, kind=None)) is False


def test_a_non_abm_project_writes_nothing(tmp_path, capsys):
    proj = make_project(tmp_path, kind="")
    import types
    args = types.SimpleNamespace(dir=str(tmp_path), out=None, dry_run=False, force=False)
    import raster.odd as m
    m.load_project = lambda d=None: proj                      # noqa: ARG005
    assert m.run_odd(args) == 0
    assert not list((tmp_path / "code" / "output").glob("*_odd_ra.md")) \
        if (tmp_path / "code" / "output").is_dir() else True


# ── element 2: entities and their state variables ────────────────────────────

def test_entities_are_the_classes_and_what_they_remember(tmp_path):
    """Element 2 IS this question. State variables are read from assignments to `self.x` in
    __init__, which is where one is declared in practice — reading the class body alone
    would miss every variable that is configured per instance."""
    ents = {q: (d, a) for q, d, a in odd.entities(make_project(tmp_path).code, "chords")}
    assert "model.Chord" in ents and "model.Lattice" in ents
    doc, attrs = ents["model.Chord"]
    assert doc.startswith("One agent")
    assert attrs == ["pcs", "position", "tolerance", "satisfied"]


# ── element 3: what advances the model ───────────────────────────────────────

def test_the_scheduler_is_found_by_the_name_every_abm_gives_it(tmp_path):
    got = odd.scheduling(make_project(tmp_path).code, "chords")
    assert ("model.step", "One round: evaluate satisfaction, then relocate the unsatisfied.") in got


# ── element 4: stochasticity is answered by the code, not by a document ──────

def test_randomness_is_reported_as_the_sites_that_draw_it(tmp_path):
    """A design concept the built code answers better than any document: the model is
    stochastic exactly where it draws. Reported as sites so the protocol can say what is
    random and what is not, rather than asserting either."""
    sites = odd.stochasticity(make_project(tmp_path).code, "chords")
    assert any("random.choice" in src for _f, _n, src in sites)
    assert all(f.endswith(".py") for f, _n, _s in sites)


def test_a_deterministic_model_yields_no_sites(tmp_path):
    proj = make_project(tmp_path)
    (proj.code / "chords" / "model.py").write_text("class A:\n    def step(self):\n        pass\n")
    assert odd.stochasticity(proj.code, "chords") == []


# ── element 6: absence is the answer ─────────────────────────────────────────

def test_no_inputs_directory_is_itself_the_finding(tmp_path):
    """"The model uses no input data" is a real and correct ODD answer, and one the
    protocol asks for explicitly — not a gap to be filled."""
    assert odd.input_data(make_project(tmp_path).code) == []


def test_an_inputs_directory_is_listed(tmp_path):
    got = odd.input_data(make_project(tmp_path, with_inputs=True).code)
    assert len(got) == 1 and "beethoven5.mid" in got[0]


# ── the evidence handed to each pass ─────────────────────────────────────────

def test_every_slice_of_evidence_is_populated_or_says_it_is_not(tmp_path):
    ev = odd._evidence(make_project(tmp_path))
    for key in ("design", "entities", "scheduling", "stochasticity", "configs",
                "inputs", "contracts", "frozen"):
        assert ev[key].strip(), f"{key} came back empty rather than saying so"
    assert "Chord" in ev["entities"] and "tolerance: 0.3" in ev["configs"]
    assert "no input data" in ev["inputs"]


def test_the_model_is_never_shown_the_implementation_tree(tmp_path):
    """The tree is the incidental implementation of the design and the worst of the
    available sources — the same reason `handoff` distils rather than forwards."""
    ev = odd._evidence(make_project(tmp_path))
    blob = "\n".join(ev.values())
    assert "import random" not in blob            # the file's body never travels
    assert "def __init__" not in blob


# ── assembly ─────────────────────────────────────────────────────────────────

def test_the_document_carries_all_seven_elements(tmp_path, monkeypatch):
    monkeypatch.setattr(odd, "_ask", lambda p, prompt, label: f"<{label}>")
    text = odd.build_odd(make_project(tmp_path))
    assert "## 4. Design concepts" in text
    assert "<overview>" in text and "<concepts>" in text and "<details>" in text
    assert text.startswith("# Chords — ODD Protocol")


def test_the_eleven_design_concepts_are_all_named(tmp_path, monkeypatch):
    """A concept the protocol asks for and the document skips is one the reader must assume
    the modeller forgot. "There is no learning in this model" is a complete answer."""
    seen = {}
    monkeypatch.setattr(odd, "_ask",
                        lambda p, prompt, label: seen.setdefault(label, prompt) and "")
    odd.build_odd(make_project(tmp_path))
    for concept in odd.DESIGN_CONCEPTS:
        assert f"### {concept}" in seen["concepts"], concept
    assert len(odd.DESIGN_CONCEPTS) == 11


def test_each_pass_gets_only_what_its_elements_need(tmp_path, monkeypatch):
    """Three disjoint slices, so no prompt carries the whole project."""
    seen = {}
    monkeypatch.setattr(odd, "_ask",
                        lambda p, prompt, label: seen.setdefault(label, prompt) and "")
    odd.build_odd(make_project(tmp_path))
    assert set(seen) == {"overview", "concepts", "details"}
    assert "frozen" not in seen["overview"].lower() or "FLOOR" not in seen["overview"]
    assert "FLOOR = 0.2" in seen["details"]         # the pinned constants reach submodels
    assert "random.choice" in seen["concepts"]      # stochasticity reaches design concepts


def test_the_output_lands_in_the_ra_revision_chain(tmp_path):
    p = odd.default_odd_path(make_project(tmp_path))
    assert p.parent.name == "output" and p.name.endswith("_odd_ra.md")
    assert p.parent.parent.name == "code"


# ── completeness is arithmetic, not judgement ────────────────────────────────

def test_a_missing_element_is_named(tmp_path):
    """Whether a section is GOOD is a judgement; whether it is THERE is arithmetic. A
    reader navigates an ODD by these headings, so an element quietly merged into its
    neighbour is one the reader concludes the modeller never considered."""
    partial = "# M — ODD Protocol\n\n## 1. Purpose and patterns\n\ntext\n"
    missing = odd.check_complete(partial)
    assert "2. Entities, state variables, and scales" in missing
    assert "7. Submodels" in missing
    assert not any(m.startswith("design concept") for m in missing), \
        "no point listing eleven concepts when element 4 itself is absent"


def test_a_skipped_design_concept_is_named(tmp_path):
    text = ("## 1. Purpose and patterns\n## 2. Entities, state variables, and scales\n"
            "## 3. Process overview and scheduling\n## 4. Design concepts\n"
            + "".join(f"### {c}\n" for c in odd.DESIGN_CONCEPTS if c != "Learning")
            + "## 5. Initialisation\n## 6. Input data\n## 7. Submodels\n")
    assert odd.check_complete(text) == ["design concept: Learning"]


def test_a_complete_protocol_reports_nothing(tmp_path):
    text = "".join(f"## {t}\n" for t, _ in odd.ELEMENTS) + \
           "".join(f"### {c}\n" for c in odd.DESIGN_CONCEPTS)
    assert odd.check_complete(text) == []
