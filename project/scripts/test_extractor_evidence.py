"""CPU-only evidence contract tests; these are not extractor quality results."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from collections import Counter

from extraction_schema import parse_json_output, strict_json_output
from evaluate_event_extractor import evidence_summary, prf, span_text


class ExtractorEvidenceTests(unittest.TestCase):
    def output(self, evidence, causes=None):
        return json.dumps({"direction": "改善", "causes": causes or [], "evidence": evidence}, ensure_ascii=False)

    def test_one_to_three_nonempty_evidence_spans(self):
        for count in (1, 2, 3):
            self.assertIsNotNone(strict_json_output(self.output(["虚构原句"] * count)))
        for evidence in ([], [""], ["  "], [1], [None], ["原句"] * 4):
            self.assertIsNone(parse_json_output(self.output(evidence)))
            self.assertIsNone(strict_json_output(self.output(evidence)))

    def test_empty_causes_are_valid_but_blank_or_nonstring_items_are_not(self):
        self.assertIsNotNone(strict_json_output(self.output(["预计改善。"])))
        for causes in ([""], ["  "], [1]):
            self.assertIsNone(strict_json_output(self.output(["预计改善。"], causes)))

    def test_recovery_not_counted_as_strict_json(self):
        text = self.output(["预计改善。"])
        for wrapped in ("```json\n" + text + "\n```", "<think>推理</think>" + text, "答案：" + text):
            self.assertIsNotNone(parse_json_output(wrapped))
            self.assertIsNone(strict_json_output(wrapped))

    def test_extra_keys_never_strict(self):
        value = json.loads(self.output(["原句"]))
        value["confidence"] = .99
        self.assertIsNone(strict_json_output(json.dumps(value)))

    def test_zero_predictions_are_undefined_support_not_perfect(self):
        result = evidence_summary(0, 0, 0, 12)
        self.assertIsNone(result["support_precision"])
        self.assertEqual(result["sample_coverage"], 0)
        self.assertEqual(result["predicted_spans"], 0)
        self.assertIsNone(evidence_summary(0, 0, 0, 0)["sample_coverage"])

    def test_coverage_and_support_are_different_denominators(self):
        result = evidence_summary(3, 4, 2, 5)
        self.assertEqual(result["support_precision"], .75)
        self.assertEqual(result["sample_coverage"], .4)

    def test_only_line_endings_normalized_not_whitespace_or_case(self):
        body = "利润  增长\r\nABC。"
        self.assertIn(span_text("增长\nABC"), span_text(body))
        self.assertNotIn(span_text("利润 增长"), span_text(body))
        self.assertNotIn(span_text("abc"), span_text(body))
        self.assertEqual(span_text(" \r尾 "), " \n尾 ")

    def test_prf_preserves_undefined_denominators(self):
        result = prf(Counter())
        self.assertTrue(all(value is None for value in result.values()))
        result = prf(Counter(fn=2))
        self.assertIsNone(result["precision"])
        self.assertEqual(result["recall"], 0)
        self.assertEqual(result["f1"], 0)

    def test_help_does_not_load_gpu_dependencies(self):
        script = Path(__file__).with_name("evaluate_event_extractor.py")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, str(script), "--help"], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--output-dir", result.stdout)


if __name__ == "__main__":
    unittest.main()
