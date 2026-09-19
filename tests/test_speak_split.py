import tempfile
import unittest
from pathlib import Path
import tts_speak
import tts_split


class SplitSpeakTests(unittest.TestCase):
    def test_two_sentences_per_chunk(self):
        text = "One sentence. Two sentence. Three sentence. Four sentence."
        chunks = tts_split.chunk_sentences(tts_split.split_sentences(text))
        self.assertEqual(
            [
                "One sentence. Two sentence.",
                "Three sentence. Four sentence.",
            ],
            chunks,
        )

    def test_write_chunks_numbers_files(self):
        text = "Alpha goes first. Bravo goes second. Charlie stays last."
        with tempfile.TemporaryDirectory() as raw:
            outdir = Path(raw)
            paths = tts_split.write_chunks(text, outdir)
            self.assertEqual(["1.txt", "2.txt"], [p.name for p in paths])
            self.assertEqual("Alpha goes first. Bravo goes second.\n", paths[0].read_text(encoding="utf-8"))
            self.assertEqual("Charlie stays last.\n", paths[1].read_text(encoding="utf-8"))
            self.assertEqual(paths, tts_speak.numbered_txt(outdir))

    def test_speak_cli_requires_variant(self):
        with self.assertRaises(SystemExit) as raised:
            tts_speak.main(["tts_speak.py", "chunks"])
        self.assertIn("nano|turbo|v3", str(raised.exception))

    def test_speak_cli_rejects_unknown_variant(self):
        with self.assertRaises(SystemExit) as raised:
            tts_speak.main(["tts_speak.py", "nano2", "chunks"])
        self.assertIn("nano|turbo|v3", str(raised.exception))

    def test_not_ready_names_the_requested_model(self):
        self.assertIn("tts_turbo.py", tts_speak.not_ready("turbo"))
        self.assertIn("<language>", tts_speak.not_ready("v3"))
        self.assertNotIn("<language>", tts_speak.not_ready("nano"))


if __name__ == "__main__":
    unittest.main()
