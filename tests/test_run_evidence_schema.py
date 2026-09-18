import ctypes
import subprocess
import sys
import unittest
from pathlib import Path


class _DummyFunc:
    def __init__(self):
        self.argtypes = None
        self.restype = None
    def __call__(self, *args, **kwargs):
        return 0


class _DummyDLL:
    def __init__(self):
        self._funcs = {}
    def __getattr__(self, name):
        return self._funcs.setdefault(name, _DummyFunc())


if not hasattr(ctypes, "WinDLL"):
    ctypes.WinDLL = lambda *args, **kwargs: _DummyDLL()
for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
    if not hasattr(subprocess, name):
        setattr(subprocess, name, 0)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tts_common


class RunEvidenceSchemaRegression(unittest.TestCase):
    def test_meta_accepts_broken_release_binary_dictionary(self):
        evidence = tts_common.RunEvidence.__new__(tts_common.RunEvidence)
        evidence.id = "regression"
        evidence.started_at = "2026-09-18T00:00:00+00:00"
        evidence.directory = ROOT / "unused-log"
        evidence.out = ROOT / "unused.wav"
        server = {"path": r"C:\\x\\chatterbox-server.exe", "bytes": 123, "sha256": "a" * 64}
        bake = {"path": r"C:\\x\\chatterbox-bake.exe", "bytes": 456, "sha256": "b" * 64}
        evidence.summary = {
            "binaries": {server["path"]: server, bake["path"]: bake},
            "variant": "nano",
            "status": "running",
        }
        meta = evidence.meta()
        self.assertEqual(server["sha256"], meta["binary_sha256"])
        self.assertEqual("nano", meta["variant"])


if __name__ == "__main__":
    unittest.main()
