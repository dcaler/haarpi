"""Frozen tests for the `audit` verb (DESIGN_corpus_audit.md). GPU-free: the brain and the
Zotero client are mocked. These pin the word-sense contract and the quarantine mechanics before
any live model run.

The verb's law: quarantine is a MOVE between Zotero collections, never a delete; and it fires
only on a CONFIDENT false-friend — a wrong keep costs a line in a review, a wrong drop costs a
cross-disciplinary paper, so the bias is always toward keep.
"""
from __future__ import annotations

import json

from rabbithole import audit
from rabbithole.models import Candidate
from rabbithole.zotero import ZoteroClient


# ── brain / zotero stand-ins ──────────────────────────────────────────────────

class SeqBrain:
    """Returns scripted coordinator replies in order (one per item judged). An Exception in
    the script is raised when reached, to exercise the fail-safe."""
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def coordinator(self, prompt, system="", **kw):
        self.calls += 1
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeZotero:
    """Records every move; never touches the network."""
    def __init__(self, ok=True):
        self.moves = []          # (item_key, from_key, to_key)
        self._ok = ok

    def move_item_between_collections(self, item, from_key, to_key):
        key = item.get("data", {}).get("key") or item.get("key")
        self.moves.append((key, from_key, to_key))
        return self._ok


def _ff(term, its, rev, conf):
    return (f'{{"verdict":"FALSE-FRIEND","term":"{term}","its_sense":"{its}",'
            f'"review_sense":"{rev}","confidence":{conf}}}')


def _transfer(conf=8):
    return f'{{"verdict":"TRANSFER","confidence":{conf}}}'


def _item(key, title, abstract="", keywords=()):
    # A judge-item: the fields the word-sense check reads.
    return {"key": key, "label": f"{key} 2020", "title": title,
            "abstract": abstract, "keywords": list(keywords)}


def _zitem(key, collections=("PROJ",), version=5):
    # A raw Zotero item, for the move layer.
    return {"key": key, "version": version,
            "data": {"key": key, "collections": list(collections), "version": version}}


# ── the word-sense judgment ───────────────────────────────────────────────────

def test_judge_item_reads_a_false_friend_verdict():
    v = audit.judge_item(SeqBrain([_ff("docking", "ligand-receptor binding",
                                       "model-to-model alignment", 9)]),
                         "agent-based modelling", "docking of adaptive agents",
                         key="AV", label="Vina 2016",
                         title="AutoDock Vina", abstract="molecular docking of ligands")
    assert v.kind == "false_friend" and v.confidence == 9.0
    assert v.term == "docking" and "ligand" in v.its_sense


def test_judge_item_reads_a_transfer_verdict():
    v = audit.judge_item(SeqBrain([_transfer()]), "topic", "focus",
                         key="X", label="X 2020", title="A paper that genuinely informs")
    assert v.kind == "transfer"


def test_judge_item_fails_safe_to_keep_on_a_bad_reply():
    # unparseable reply -> keep (transfer), never a spurious quarantine
    v = audit.judge_item(SeqBrain(["not json at all"]), "t", "f",
                         key="X", label="X 2020", title="T")
    assert v.kind == "transfer" and v.confidence == 0.0


# ── audit_corpus: bias to keep, and the cache ─────────────────────────────────

def test_only_confident_false_friends_are_flagged():
    items = [_item("AV", "AutoDock Vina", "docking of ligands"),
             _item("PH", "phyloseq microbiome", "reproducible amplicon analysis"),
             _item("OK", "Adaptive agents that dock", "model alignment")]
    brain = SeqBrain([_ff("docking", "ligand binding", "agent alignment", 9),   # flag
                      _ff("reproducibility", "wet-lab", "simulation", 4),        # low conf -> keep
                      _transfer()])                                              # keep
    flagged, verdicts = audit.audit_corpus(brain, "ABM", "agent docking", items,
                                           min_confidence=7.0)
    assert [v.key for v in flagged] == ["AV"]          # only the confident false-friend
    assert len(verdicts) == 3                          # everything was judged


def test_the_cache_prevents_re_judging():
    items = [_item("AV", "AutoDock Vina", "docking of ligands")]
    cache = {}
    b1 = SeqBrain([_ff("docking", "ligand binding", "agent alignment", 9)])
    audit.audit_corpus(b1, "ABM", "agent docking", items, cache=cache)
    assert b1.calls == 1 and "AV" in cache
    b2 = SeqBrain([])                                   # any call would IndexError
    flagged, _ = audit.audit_corpus(b2, "ABM", "agent docking", items, cache=cache)
    assert b2.calls == 0                                # served from cache
    assert [v.key for v in flagged] == ["AV"]          # and the verdict survived the round-trip


# ── live progress + resumable checkpointing (a corpus is hundreds of slow items) ──

def test_format_progress_spells_out_a_quarantine_and_a_keep():
    ff = audit.Verdict(key="AV", label="Vina 2016", kind="false_friend", confidence=9.0,
                       term="docking", its_sense="ligand binding", review_sense="agent alignment")
    line = audit.format_progress(1, 188, ff, cached=False, min_confidence=7.0)
    assert "1/188" in line and "Vina 2016" in line
    assert "QUARANTINE" in line and "docking" in line
    assert "ligand binding" in line and "agent alignment" in line     # the sense-clash is shown
    tr = audit.Verdict(key="X", label="X 2020", kind="transfer", confidence=8.0)
    keep = audit.format_progress(2, 188, tr, cached=True)
    assert "transfers" in keep and "cached" in keep and "QUARANTINE" not in keep
    weak = audit.format_progress(3, 188, audit.Verdict("Y", "Y 2020", "false_friend", confidence=4.0,
                                                       term="model"), cached=False, min_confidence=7.0)
    assert "weak false-friend" in weak and "QUARANTINE" not in weak    # below threshold = a keep


def test_audit_corpus_reports_every_item_and_checkpoints_only_fresh_ones():
    items = [_item(k, "T") for k in ("A", "B", "C", "D")]
    cache = {"B": audit._to_cache(audit.Verdict("B", "B 2020", "transfer", confidence=8))}  # pre-cached
    brain = SeqBrain([_transfer(), _transfer(), _transfer()])          # A, C, D judged fresh
    seen, saves = [], []
    audit.audit_corpus(brain, "t", "f", items, cache=cache,
                       progress=lambda i, n, v, c, dt: seen.append((i, n, v.key, c)),
                       checkpoint=lambda c: saves.append(len(c)), checkpoint_every=2)
    assert [s[0] for s in seen] == [1, 2, 3, 4] and seen[0][1] == 4    # one call per item, i and total
    assert seen[1] == (2, 4, "B", True)                               # B reported as cached
    assert brain.calls == 3                                            # only the three uncached judged
    assert len(saves) == 1                                            # checkpoint after the 2nd FRESH item, not the cached one


def test_sig_is_stable_and_question_specific():
    # the cache/checkpoint signature must be identical across processes (no builtin hash(), which is
    # salted per run) or _load_cache discards the cache every time and resume never works.
    import subprocess, sys
    code = "from rabbithole import audit; print(audit._sig('ABM','agent docking'))"
    outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout
            for _ in range(2)}
    assert len(outs) == 1                                             # same value in two fresh processes
    assert audit._sig("ABM", "agent docking") != audit._sig("ABM", "other focus")  # question-specific


# ── perform_audit: move only the flagged, log the reasons, respect dry-run ────

def test_perform_audit_moves_only_the_flagged_and_logs_reasons(tmp_path):
    raw = [_zitem("AV"), _zitem("OK")]
    brain = SeqBrain([_ff("docking", "ligand-receptor binding", "agent alignment", 9),
                      _transfer()])
    zc = FakeZotero()
    summary = audit.perform_audit(zc, brain, "ABM", "agent docking",
                                  project_key="PROJ", quarantine_key="QUAR",
                                  items=raw, outdir=tmp_path)
    assert zc.moves == [("AV", "PROJ", "QUAR")]         # exactly the false-friend, one direction
    log = (tmp_path / "audit_quarantine.md").read_text()
    assert "AV" in log and "docking" in log and "ligand-receptor binding" in log
    assert summary["moved"] == ["AV"]


def test_dry_run_judges_and_logs_but_moves_nothing(tmp_path):
    raw = [_zitem("AV")]
    brain = SeqBrain([_ff("docking", "ligand binding", "agent alignment", 9)])
    zc = FakeZotero()
    summary = audit.perform_audit(zc, brain, "ABM", "agent docking",
                                  project_key="PROJ", quarantine_key="QUAR",
                                  items=raw, outdir=tmp_path, dry_run=True)
    assert zc.moves == []                               # nothing moved
    assert summary["moved"] == [] and summary["flagged"] == ["AV"]   # but it was flagged + logged
    assert (tmp_path / "audit_quarantine.md").exists()


def test_a_brain_error_leaves_the_item_in_the_corpus(tmp_path):
    raw = [_zitem("AV")]
    zc = FakeZotero()
    audit.perform_audit(zc, SeqBrain([RuntimeError("model down")]), "ABM", "x",
                        project_key="PROJ", quarantine_key="QUAR",
                        items=raw, outdir=tmp_path)
    assert zc.moves == []                               # fail-safe: never quarantine on an error


# ── release: move an item back to this project's collection ───────────────────

def test_release_moves_an_item_back_to_the_project(tmp_path):
    zc = FakeZotero()
    item = _zitem("AV", collections=("QUAR",))
    ok = audit.release_item(zc, quarantine_key="QUAR", project_key="PROJ", item=item)
    assert ok and zc.moves == [("AV", "QUAR", "PROJ")]  # reversed direction


# ── the standalone-safety prune of the cached corpus ──────────────────────────

def test_prune_corpus_json_drops_only_the_quarantined_by_dedup_key(tmp_path):
    keep = Candidate(title="Adaptive agents that dock", doi="10.1/keep")
    drop = Candidate(title="AutoDock Vina", doi="10.1/vina")
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps([keep.to_dict(), drop.to_dict()]), encoding="utf-8")
    n = audit.prune_corpus_json(p, {drop.dedup_key})
    assert n == 1
    left = [Candidate.from_dict(d).title for d in json.loads(p.read_text())]
    assert left == ["Adaptive agents that dock"]           # the false-friend is gone


def test_prune_corpus_json_is_a_noop_without_a_file_or_keys(tmp_path):
    assert audit.prune_corpus_json(tmp_path / "absent.json", {"x"}) == 0
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps([Candidate(title="A", doi="10.1/a").to_dict()]), encoding="utf-8")
    assert audit.prune_corpus_json(p, set()) == 0          # nothing flagged -> untouched


# ── the Zotero move primitive (unit) ──────────────────────────────────────────

class FakeHTTP:
    def __init__(self, status=204):
        self.status = status
        self.patched = []

    def patch(self, url, json=None, headers=None):
        self.patched.append((url, json, headers))
        class R:  # noqa: N801
            status_code = self.status
        return R()


def _bare_client(http):
    zc = ZoteroClient.__new__(ZoteroClient)   # bypass __init__ (no gc, no network)
    zc.prefix = "P"
    zc._client = http
    return zc


def test_move_item_between_collections_rewrites_the_array_and_patches():
    http = FakeHTTP()
    zc = _bare_client(http)
    item = {"key": "AV", "version": 7,
            "data": {"key": "AV", "collections": ["PROJ", "OTHER"], "version": 7}}
    assert zc.move_item_between_collections(item, "PROJ", "QUAR") is True
    url, body, headers = http.patched[0]
    assert url == "P/items/AV"
    assert set(body["collections"]) == {"OTHER", "QUAR"}     # PROJ removed, QUAR added, OTHER kept
    assert headers["If-Unmodified-Since-Version"] == "7"     # optimistic-concurrency guard


def test_move_is_idempotent_when_already_moved():
    http = FakeHTTP()
    zc = _bare_client(http)
    item = {"key": "AV", "version": 7,
            "data": {"key": "AV", "collections": ["QUAR"], "version": 7}}
    assert zc.move_item_between_collections(item, "PROJ", "QUAR") is True
    assert http.patched == []                                # nothing to do -> no PATCH


if __name__ == "__main__":
    import traceback, inspect, tempfile, pathlib
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in fns:
        kw = {}
        if "tmp_path" in inspect.signature(fn).parameters:
            kw["tmp_path"] = pathlib.Path(tempfile.mkdtemp())
        try:
            fn(**kw); print(f"  PASS  {fn.__name__}")
        except Exception:  # noqa: BLE001
            failures += 1; print(f"  FAIL  {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    raise SystemExit(1 if failures else 0)
