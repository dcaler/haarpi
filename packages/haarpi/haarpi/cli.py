"""haarpi umbrella CLI — cross-stage verbs for a research project.

Planned verbs (built as core extraction lands):
  init        one interview -> haarpi.yaml + stage scaffolds + trundlr project
  status      which stages are complete, missing, or stale
  queue       submit the forward pipeline to trundlr
  parseNplan  read the newest annotated gate document, classify, queue rework
"""

import sys


def main() -> int:
    print("haarpi: umbrella verbs land as core extraction proceeds "
          "(init, status, queue, parseNplan). The stage tools are installed: "
          "rabbitHole, raconteur, raster, rayleigh.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
