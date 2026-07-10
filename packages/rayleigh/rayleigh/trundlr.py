# rayleigh.trundlr IS the shared client — aliased to the one real implementation
# in haarpi.trundlr (same pattern as raster.trundlr).
import sys

from haarpi import trundlr as _core

sys.modules[__name__] = _core
