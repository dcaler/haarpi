"""Release grammar: the chain records whose turn it is; a release has no author tokens."""

from pathlib import Path

from haarpi import naming


def test_release_name_bare_and_infixed():
    t = naming.today()
    assert naming.release_name("myproj", "docx") == f"{t}_myproj.docx"
    assert naming.release_name("myproj", "md", infix="litreview") == f"{t}_myproj_litreview.md"


def test_parse_accepts_empty_chain():
    got = naming.parse(Path("260715_myproj.docx"), "myproj")
    assert got == ("260715", [], "docx")


def test_is_release_rule():
    assert naming.is_release([])                            # bare
    assert naming.is_release(["litreview"])                 # deliverable word only
    assert not naming.is_release(["ra"])                    # tool draft — in play
    assert not naming.is_release(["litreview", "ra", "DCR"])
    assert not naming.is_release(["onepager", "RA"])        # case-insensitive


def _touch(d, name, mtime):
    p = d / name
    p.write_text("x")
    import os
    os.utime(p, (mtime, mtime))
    return p


def test_find_latest_release_ignores_in_flight_files(tmp_path):
    _touch(tmp_path, "260710_myproj_litreview_ra.docx", 100)          # fresh draft
    _touch(tmp_path, "260712_myproj_litreview_ra_DCR.docx", 200)      # markup in flight
    rel = _touch(tmp_path, "260711_myproj_litreview.docx", 150)       # the release
    got = naming.find_latest_release(tmp_path, "myproj", chain_includes="litreview")
    assert got == rel                                                  # newest RELEASE, not newest file


def test_find_latest_release_none_when_nothing_gated(tmp_path):
    _touch(tmp_path, "260710_myproj_litreview_ra.docx", 100)
    assert naming.find_latest_release(tmp_path, "myproj") is None


def test_existing_chain_helpers_unchanged(tmp_path):
    # the * quantifier must not disturb chained parsing
    got = naming.parse(Path("260710_myproj_litreview_ra_DCR.docx"), "myproj")
    assert got == ("260710", ["litreview", "ra", "DCR"], "docx")
    assert naming.minor_name("myproj", ["ra", "DCR"], "docx", "260710") \
        == "260710_myproj_ra_DCR_ra.docx"


def test_a_venue_slug_may_carry_a_year():
    """A slug names the INSTANCE — next year's CSS is a different conference, with a
    different deadline and a different committee. Letters only, and
    `260714_Chords_css2026_outline_ra.docx` fails to parse: which does not merely lose the
    venue, it makes the file invisible to the gate, the redline and the release, in silence.
    """
    from pathlib import Path

    from haarpi import naming

    p = Path("260714_Chords_css2026_outline_ra_DCR.docx")
    assert naming.parse(p, "Chords") == ("260714", ["css2026", "outline", "ra", "DCR"], "docx")
    assert naming.venue_of(p, "Chords") == "css2026"


def test_a_chain_token_still_cannot_start_with_a_digit():
    """The datestamp is the only all-numeric field; a bare number in the chain is a typo,
    not a venue."""
    from pathlib import Path

    from haarpi import naming
    assert naming.parse(Path("260714_Chords_2026_ra.docx"), "Chords") is None
