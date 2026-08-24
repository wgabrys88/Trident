import unittest

from ui_streaming import highlighted_progress, pcm16_lookahead, text_batches


class UIStreamingTests(unittest.TestCase):
    def test_text_batches_group_five_native_chunks(self):
        text = ("word " * 800).strip()
        batches = text_batches(text, 180, 300, 5)
        self.assertGreaterEqual(len(batches), 2)
        self.assertLessEqual(len(batches[0][0]), 180 + 4 * 300)
        self.assertTrue(all(len(part) <= 5 * 300 for part, _ in batches[1:]))
        self.assertEqual(batches[-1][1], len(text))

    def test_highlighted_progress_partitions_text(self):
        text = "abcdefghij"
        spans = highlighted_progress(text, 3, 7)
        self.assertEqual(spans, [("abc", "sent"), ("defg", "buffered"), ("hij", "pending")])

    def test_pcm16_lookahead_holds_one_chunk_ahead(self):
        seen = []

        def source():
            for item in (b"a" * 8, b"b" * 8, b"c" * 8):
                seen.append(item)
                yield item

        out = pcm16_lookahead(source(), sample_rate=4, min_seconds=1.0)
        self.assertEqual(next(out), b"a" * 8)
        self.assertEqual(seen, [b"a" * 8, b"b" * 8])
        self.assertEqual(list(out), [b"b" * 8, b"c" * 8])


if __name__ == "__main__":
    unittest.main()
