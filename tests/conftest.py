"""Tests run on Windows, or on Linux with a ctypes.WinDLL stub on PYTHONPATH (no server, no screen)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
