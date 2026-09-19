import unittest
from tts import VARIANTS
from tts_common import parse_args
from tts_settings import WEIGHT_TYPES

class CliContractTests(unittest.TestCase):
    def test_weight_types_remain_cli_choices(self):
        self.assertEqual(("f32", "f16", "q4_0"), WEIGHT_TYPES)

    def test_conversion_flags_select_runtime_gguf(self):
        args = parse_args(VARIANTS["nano"], ["tts.py", "--t3-weight-type", "f16", "--s3-weight-type", "q4_0", "hello"])
        self.assertEqual("f16", args.t3_weight_type)
        self.assertEqual("q4_0", args.s3_weight_type)
        self.assertEqual("hello", args.text)

    def test_generation_knobs_remain_exposed(self):
        args = parse_args(VARIANTS["nano"], ["tts.py", "--seed", "7", "--temperature", "0.5", "hello"])
        self.assertEqual("7", args.knobs["seed"])
        self.assertEqual("0.5", args.knobs["temperature"])

    def test_v3_requires_language(self):
        with self.assertRaises(SystemExit):
            parse_args(VARIANTS["v3"], ["tts.py", "hello"])
        args = parse_args(VARIANTS["v3"], ["tts.py", "--min-p", "0.1", "hello", "pl"])
        self.assertEqual("pl", args.language)
        self.assertEqual("0.1", args.knobs["min-p"])

if __name__ == "__main__":
    unittest.main()
