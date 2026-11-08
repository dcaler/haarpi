"""The bench is a dev tool outside the packages; its modules import as top-level `bench`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
