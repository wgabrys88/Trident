import json
import unittest
from pathlib import Path
import tts_settings

class SettingsContractTests(unittest.TestCase):
    def test_precision_policy_is_shared_with_engine(self):
        policy = Path(__file__).resolve().parents[2] / "chatterbox.cpp/scripts/precision_policy.json"
        self.assertEqual(("f32", "f16", "q4_0"), tts_settings.WEIGHT_TYPES)
        self.assertEqual(tuple(json.loads(policy.read_text(encoding="utf-8"))["weight_types"]), tts_settings.WEIGHT_TYPES)

    def test_fidelity_defaults_are_f32(self):
        for family in ("gpt2", "v3"):
            self.assertEqual("f32", tts_settings.CONVERSION_DEFAULTS[family]["t3-weight-type"])
            self.assertEqual("f32", tts_settings.CONVERSION_DEFAULTS[family]["s3-weight-type"])

if __name__ == "__main__":
    unittest.main()
