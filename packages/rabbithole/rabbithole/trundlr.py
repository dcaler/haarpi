"""rabbitHole's trundlr binding — the shared client, constructed from GlobalConfig.

parseNplan uses this to queue a gather → collect → revise → comment chain after
reading reviewer annotations. See haarpi.trundlr for the client itself.
"""

from __future__ import annotations

from haarpi.trundlr import TrundlrError

from .config import GlobalConfig

import haarpi.trundlr as _core


class TrundlrClient(_core.TrundlrClient):
    def __init__(self, gc: GlobalConfig) -> None:
        if not gc.trundlr_url:
            raise TrundlrError("no trundlr_url configured ([trundlr] url in config.toml)")
        super().__init__(gc.trundlr_url, runner_resource_id=gc.trundlr_runner_resource_id)


__all__ = ["TrundlrClient", "TrundlrError"]
