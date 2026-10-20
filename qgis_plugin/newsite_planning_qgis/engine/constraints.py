"""Compatibility import for the shared v7 production core."""
import sys
from pathlib import Path
_core=str(Path(__file__).resolve().parents[1]/"core")
if _core not in sys.path:sys.path.insert(0,_core)
from constraints import *
