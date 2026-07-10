"""Umbrella dispatch: `haarpi <tool> <args>` must run that tool's CLI."""

from haarpi import cli


def test_usage_on_no_args(capsys):
    assert cli.main([]) == 0
    assert "usage" in capsys.readouterr().out


def test_dispatch_reaches_the_tool(capsys):
    # raster's --version is a cheap, side-effect-free round trip through its argparse
    rc = cli.main(["raster", "--version"])
    assert rc == 0
    assert "raster" in capsys.readouterr().out.lower()


def test_dispatch_accepts_classic_spelling(capsys):
    rc = cli.main(["rabbitHole", "--help"])
    assert rc == 0
    assert "gather" in capsys.readouterr().out


def test_unknown_command_errors(capsys):
    assert cli.main(["frobnicate"]) == 2
    assert "unknown command" in capsys.readouterr().err


def test_planned_verbs_signpost(capsys):
    assert cli.main(["next"]) == 2
    assert "planner harness" in capsys.readouterr().out


def test_doctor_reports_path_state(capsys):
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "haarpi stack" in out and "rabbitHole" in out
