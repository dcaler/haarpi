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


def test_pipeline_verbs_need_a_manifest(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["next"]) == 2
    assert "haarpi init" in capsys.readouterr().err


def test_doctor_reports_path_state(capsys):
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "haarpi stack" in out and "rabbitHole" in out


def test_doctor_resolves_the_haarpi_shim_symlink(capsys, tmp_path, monkeypatch):
    # The one exposed `haarpi` binary is a shim OUTSIDE the venv pointing INTO
    # it; doctor must classify by the real target, not the shim's own path.
    prefix = tmp_path / "venv"
    (prefix / "bin").mkdir(parents=True)
    real = prefix / "bin" / "haarpi"
    real.write_text("#!/bin/sh\n")
    shim = tmp_path / "localbin" / "haarpi"
    shim.parent.mkdir()
    shim.symlink_to(real)

    monkeypatch.setattr(cli.sys, "prefix", str(prefix))
    monkeypatch.setattr(cli.shutil, "which",
                        lambda name: str(shim) if name == "haarpi" else None)
    assert cli.main(["doctor"]) == 0
    haarpi_line = next(l for l in capsys.readouterr().out.splitlines()
                       if str(shim) in l)
    assert "this stack" in haarpi_line and "OTHER" not in haarpi_line
