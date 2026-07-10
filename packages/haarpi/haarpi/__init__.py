"""haarpi — shared core for the ra* research pipeline.

Common machinery the four stage tools (rabbitHole, raster, rayleigh, raconteur)
each grew independently: the trundlr client, notify, run logging, the document
revision naming chain, and pandoc rendering. The umbrella CLI (init / status /
queue / parseNplan) also lives here.
"""

__version__ = "0.1.0"
