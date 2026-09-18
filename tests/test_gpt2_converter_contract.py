import ast
import unittest
from pathlib import Path


class GPT2ConverterContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Path(__file__).resolve().parents[2] / "chatterbox.cpp"
        cls.converter = cls.engine / "scripts/convert-t3-gpt2-to-gguf.py"
        cls.native = cls.engine / "src/t3_gpt2.cpp"
        cls.source = cls.converter.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_known_non_inference_tensors_are_explicit(self):
        skip = None
        for node in self.tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SKIP" for t in node.targets):
                skip = ast.literal_eval(node.value)
                break
        self.assertEqual({"tfmr.wte.weight", "text_head.weight"}, skip)

    def test_unknown_tensor_check_remains_strict(self):
        self.assertIn("name not in SKIP and map_name(name) is None", self.source)
        self.assertIn("GPT2 T3 conversion incomplete", self.source)

    def test_native_inference_uses_speech_head_not_text_head(self):
        native = self.native.read_text(encoding="utf-8")
        self.assertIn("model.speech_head", native)
        self.assertNotIn("model.text_head", native)


if __name__ == "__main__":
    unittest.main()
