# raster.trundlr IS the shared client — aliased so monkeypatching raster.trundlr._api
# (as the queue tests do) patches the one real implementation in haarpi.trundlr.
import sys

from haarpi import trundlr as _core

sys.modules[__name__] = _core
