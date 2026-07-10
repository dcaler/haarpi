"""raster's run log — shared implementation in haarpi.runlog, stamped [raster]."""

from haarpi import runlog as _core
from haarpi.runlog import fmt_secs


def log(msg: str) -> None:
    _core.log(msg, tool="raster")


__all__ = ["log", "fmt_secs"]
