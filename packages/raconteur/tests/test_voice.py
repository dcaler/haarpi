"""The author's voice, measured from what they DO write.

The first design learned what the author NEVER writes, by contrasting their corpus against
the tool's. It produced three thousand "nevers", led by `jaccard distance`, `harmonic
similarity` and `chord distance metrics` — the vocabulary of the paper itself. The author had
never written those because he had never written about music. The method could not tell style
from topic, and banning a paper's own words in the name of its author's voice is a
spectacular way to fail.

Everything counted here is CLOSED-CLASS — connectives, hedges, intensifiers, rhythm — where a
domain term cannot intrude. There are only so many ways to say "however".
"""

from __future__ import annotations

from raconteur import guards, voice

# A paragraph in the author's register: long sentences, his connectives, his hedges.
HIS = (
    "We conduct the analysis in two steps. First, pre- and post-stepdown groups are analyzed "
    "for significant differences in installed price, and the results suggest that the rebate "
    "is largely passed through to consumers. Second, we examine whether the effect can be "
    "attributed to the policy itself, such as through the timing of the stepdown, or whether "
    "it may instead reflect underlying trends in the market.\n\n"
    "However, the analysis is limited in that it cannot account for every source of "
    "unobserved heterogeneity. For example, installers may adjust their prices in "
    "anticipation of a stepdown, and this could bias the estimates. Overall, the evidence "
    "indicates that pass-through is substantial, though the magnitude likely varies across "
    "market segments."
)

# What a language model writes when it has nothing to say.
SLOP = (
    "Moreover, these findings are remarkably significant. Furthermore, it seems the model "
    "works. Notably, this underscores the pivotal nature of the result."
)


# A signature needs a corpus, not a paragraph: rhythm is refused below 40 sentences, which
# is correct — a sentence-length distribution from 115 words is noise with a mean.
CORPUS = "\n\n".join([HIS] * 12)


def _sig(text: str = CORPUS) -> dict:
    return voice.signature(text, clean=False)


# ── the palette ──────────────────────────────────────────────────────────────

def test_the_palette_is_what_he_uses():
    sig = _sig()
    assert "however" in sig["connectives"]
    assert "for example" in sig["connectives"]
    assert "overall" in sig["connectives"]
    assert "moreover" not in sig["connectives"], "he does not write it, so it is not his"


def test_a_domain_word_can_never_enter_the_palette():
    """The whole reason this is sound. `harmonic similarity` is not a candidate, because the
    candidate set is closed and contains no nouns."""
    sig = voice.signature(
        "The jaccard distance between chords measures harmonic similarity. " * 40,
        clean=False)
    for palette in ("connectives", "hedges", "intensifiers"):
        assert not any("jaccard" in k or "harmonic" in k for k in sig[palette])


def test_outside_palette_finds_the_markers_he_never_uses():
    sig = _sig()
    got = voice.outside_palette(SLOP, sig["connectives"], voice.CONNECTIVES)
    assert "moreover" in got and "notably" in got
    assert "however" not in got, "he writes this one"


# ── rhythm ───────────────────────────────────────────────────────────────────

def test_rhythm_measures_sentences_and_their_spread():
    r = voice.rhythm(" ".join([HIS] * 8))
    assert 15 <= r["sentence_words_mean"] <= 40
    assert r["sentence_words_p10"] < r["sentence_words_mean"] < r["sentence_words_p90"]


def test_paragraph_shape_is_only_claimed_when_paragraphs_exist():
    """Zotero's flat index returns nine papers as nine blobs of 288 sentences each. A figure
    computed from that is a fact about the indexer."""
    blob = " ".join([HIS.replace("\n\n", " ")] * 30)      # one enormous paragraph
    assert "sentences_per_paragraph" not in voice.rhythm(blob)


# ── the guard ────────────────────────────────────────────────────────────────

def test_the_guard_names_what_he_writes_instead():
    """Positive, always. A model told not to write "moreover" writes "furthermore"; a model
    told he writes "however" and "thus" reaches for one of those."""
    findings = guards.style_findings(SLOP, _sig())
    kinds = {f.kind for f in findings}
    assert kinds == {"off-voice"}

    moreover = next(f for f in findings if "moreover" in f.imperative)
    assert "never once written it" in moreover.imperative
    assert "He writes:" in moreover.imperative
    assert "however" in moreover.imperative


def test_the_guard_catches_a_hedge_he_never_uses():
    imperatives = " ".join(f.imperative for f in guards.style_findings(SLOP, _sig()))
    assert "seems" in imperatives


def test_an_empty_palette_makes_no_claim():
    """No evidence, no finding. The corpus shows which intensifiers he uses; if it shows
    none, we cannot say he never writes "remarkably" — we can only say we do not know."""
    sig = _sig()
    assert sig["intensifiers"] == {}, "this fixture happens to contain none"
    assert not any("remarkably" in f.imperative
                   for f in guards.style_findings(SLOP, sig))

    with_evidence = voice.signature(CORPUS + " The effect is highly substantial. " * 4,
                                    clean=False)
    assert with_evidence["intensifiers"]
    assert any("remarkably" in f.imperative
               for f in guards.style_findings(SLOP, with_evidence))


def test_clipped_rhythm_is_a_finding():
    clipped = ("The model works well. We ran the experiment. The result was clear. "
               "The effect was strong. We report it here.")
    findings = guards.style_findings(clipped, _sig())
    assert any("Combine some" in f.imperative for f in findings)


def test_his_own_prose_passes_his_own_guard():
    """The floor under all of it: the author's writing must not be flagged as unlike the
    author's writing."""
    assert guards.style_findings(HIS, _sig()) == []


def test_no_signature_means_no_findings():
    assert guards.style_findings(SLOP, {}) == []


# ── the block the drafter receives ───────────────────────────────────────────

def test_exemplars_are_never_truncated_mid_passage():
    """The bug this whole rethink began from: the profile was capped at 2,000 characters with
    the verbatim excerpts at the END of the file, so the excerpts were exactly what got cut.
    Every draft was styled from a DESCRIPTION of the author's prose and not one sentence of
    the prose itself."""
    exemplars = ["First exemplar. " * 20, "Second exemplar. " * 20]
    block = voice.style_block(_sig(), exemplars, budget=900)
    assert "HIS PROSE" in block
    assert "First exemplar." in block
    assert not block.rstrip().endswith("exemplar"), "no half-quoted passage"


def test_the_block_leads_with_the_palette_and_the_prose():
    block = voice.style_block(_sig(), ["A passage of his own prose, at length. " * 6])
    assert block.index("TRANSITIONS HE USES") < block.index("HIS PROSE")
    assert "RHYTHM" in block


def test_an_exemplar_never_starts_mid_sentence():
    """A PDF layout block can begin where the previous column left off. Quoting "public
    reaction to the response. The first source…" at a model teaches it to start paragraphs in
    the middle of a thought."""
    corpus = ("public reaction to the response. " + "This continues the thought. " * 15
              + "\n\n" + "We conduct the analysis in two steps. " * 15)
    for ex in voice.pick_exemplars(corpus, n=2):
        assert ex[:1].isupper()
        assert not ex.startswith("public reaction")
