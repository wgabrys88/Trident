import unittest
from tts_metadata import find_binary_record, normalize_binary_records

class MetadataSchemaTests(unittest.TestCase):
    def setUp(self):
        self.server = {"path": r"C:\\x\\chatterbox-server.exe", "bytes": 123, "sha256": "a" * 64}
        self.bake = {"path": r"C:\\x\\chatterbox-bake.exe", "bytes": 456, "sha256": "b" * 64}

    def test_broken_release_dictionary_shape_is_accepted(self):
        broken = {self.server["path"]: self.server, self.bake["path"]: self.bake}
        records = normalize_binary_records(broken)
        self.assertEqual(2, len(records))
        self.assertEqual(self.server, find_binary_record(records, "chatterbox-server.exe"))

    def test_canonical_list_shape_is_accepted(self):
        canonical = [self.server, self.bake]
        self.assertEqual(canonical, normalize_binary_records(canonical))
        self.assertEqual(self.bake, find_binary_record(canonical, "chatterbox-bake.exe"))

    def test_invalid_shape_fails_loudly(self):
        with self.assertRaises(TypeError):
            normalize_binary_records("not metadata")

if __name__ == "__main__":
    unittest.main()
