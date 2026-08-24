import unittest
from pathlib import Path

from asr_live import LiveASR


class LiveASRTests(unittest.TestCase):
    def test_event_appends_newly_finalized_text(self):
        asr = LiveASR(Path("parakeet.dll"), Path("eou.gguf"))
        first = asr._event("feed", {"text": "hello", "eou": 0, "eob": 0, "events": []})
        second = asr._event("feed", {"text": "world", "eou": 1, "eob": 0, "events": [{"type": "eou"}]})
        self.assertEqual(first["text"], "hello")
        self.assertEqual(second["fragment"], "world")
        self.assertEqual(second["text"], "hello world")
        self.assertTrue(second["eou"])
        self.assertFalse(second["eob"])

    def test_cut_event_preserves_tag(self):
        asr = LiveASR(Path("parakeet.dll"), Path("eou.gguf"))
        event = asr._event("cut", {"text": "tail", "eou": 0, "eob": 0, "events": []}, "ptt")
        self.assertEqual(event["source"], "cut")
        self.assertEqual(event["tag"], "ptt")
        self.assertEqual(event["text"], "tail")


if __name__ == "__main__":
    unittest.main()
