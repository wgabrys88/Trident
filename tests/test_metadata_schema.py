import unittest
from tts_metadata import find_binary_record, normalize_binary_records


class MetadataSchemaTests(unittest.TestCase):
    def setUp(self):
        self.server = {"path": r"C:\\x\\chatterbox-server.exe", "bytes": 123, "sha256": "a" * 64}
        self.bake = {"path": r"C:\\x\\chatterbox-bake.exe", "bytes": 456, "sha256": "b" * 64}

    def test_canonical_list_shape_is_accepted(self):
        canonical = [self.server, self.bake]
        self.assertEqual(canonical, normalize_binary_records(canonical))
        self.assertEqual(self.bake, find_binary_record(canonical, "chatterbox-bake.exe"))

    def test_non_list_shape_fails_loudly(self):
        with self.assertRaises(TypeError):
            normalize_binary_records({self.server["path"]: self.server})

    def test_invalid_record_fails_loudly(self):
        with self.assertRaises(TypeError):
            normalize_binary_records([self.server, "invalid"] )


if __name__ == "__main__":
    unittest.main()
