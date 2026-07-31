"""raconteur.voice — moved to haarpi.voice, the shared voice-measurement library.

Both tools that train a voice profile (rabbitHole and raconteur) need this measurement code,
so it now lives in haarpi (see DESIGN_style_engine.md). This module re-exports it verbatim, so
every `voice.*` call site and test in raconteur keeps working unchanged.
"""

from __future__ import annotations

from haarpi.voice import *  # noqa: F401,F403
from haarpi.voice import (  # noqa: F401 — names the star import skips (underscore-prefixed)
    _dedupe, _markup_files, _rate, _tidy, _words,
    log,
)
