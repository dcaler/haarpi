"""Every verb that acts on a review must accept which review.

`audit` did not. Its `_review_arg(aud)` call sat at the bottom of cli.py *after*
`sys.exit(main())`, so it was unreachable and the parser never got the argument — while
gather, collect, report, build, graft, revise, ingest, refresh and mindmap all had it.
`haarpi rabbithole audit methods` died with "unrecognized arguments: methods", and nothing
caught it because a dead line is not a syntax error.

So the property is asserted over the whole parser rather than verb by verb: a new verb that
forgets the argument fails here, and one that legitimately has no review must say so by name.
"""
import argparse

import pytest

from rabbithole import cli, config


def _subparsers():
    """The real parser's subcommands, built the way main() builds them."""
    found = {}
    real = argparse.ArgumentParser.add_subparsers

    def spy(self, *a, **kw):
        action = real(self, *a, **kw)
        add = action.add_parser

        def wrapped(name, *aa, **kk):
            sp = add(name, *aa, **kk)
            found[name] = sp
            return sp

        action.add_parser = wrapped
        return action

    argparse.ArgumentParser.add_subparsers = spy
    try:
        with pytest.raises(SystemExit):
            cli.main(["--help"])
    finally:
        argparse.ArgumentParser.add_subparsers = real
    assert found, "no subcommands were discovered — the spy missed the parser"
    return found


# Verbs that act on the PROJECT, not on one review. Anything else must take a review.
PROJECT_LEVEL = {"init", "style"}


def test_every_review_verb_takes_a_review():
    missing = []
    for name, sp in _subparsers().items():
        if name in PROJECT_LEVEL:
            continue
        opts = {a.dest for a in sp._actions}
        if "review" not in opts:
            missing.append(name)
    assert not missing, (
        f"{sorted(missing)} act on a review but do not accept which one. Add "
        f"`_review_arg(...)` beside the parser — not at the end of the file, where "
        f"`audit`'s copy sat unreachable after `sys.exit(main())`.")


def test_audit_specifically_since_that_is_the_one_that_broke():
    sp = _subparsers()["audit"]
    review = next(a for a in sp._actions if a.dest == "review")
    assert set(review.choices) == set(config.REVIEW_KINDS)
    assert review.default == config.DEFAULT_KIND


def test_a_project_level_verb_is_named_not_merely_absent():
    """The allowlist is the point: a verb drops out of the check only by being declared."""
    names = set(_subparsers())
    assert PROJECT_LEVEL <= names, (
        f"{sorted(PROJECT_LEVEL - names)} is allowlisted but is not a verb any more — "
        f"drop it from PROJECT_LEVEL rather than leaving a hole in the check.")


def test_the_review_name_selects_the_directory():
    """What the argument is FOR: `audit methods` must reach methodsReview, not litReview."""
    import pathlib
    for kind, expect in ((config.DEFAULT_KIND, None), ("methods", "methodsReview")):
        argv = ["audit"] + ([kind] if kind != config.DEFAULT_KIND else [])
        with pytest.raises(FileNotFoundError) as ei:
            cli.main(argv + ["--dry-run"])
        if expect:
            assert expect in str(ei.value), f"`audit {kind}` did not reach {expect}"
