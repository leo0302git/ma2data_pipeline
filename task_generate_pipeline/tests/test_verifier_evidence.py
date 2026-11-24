"""Keep measured exceptional outcomes visible without exposing fixture answers."""

import json
import tempfile
import unittest
from pathlib import Path

from common import save
from contracts import SUPPLEMENTAL_REPORTS, diagnostics
from pipeline import readable


class SupplementalEvidenceTests(unittest.TestCase):
    def test_reviewer_receives_actual_supplemental_reports_without_fixture_answers(self):
        with tempfile.TemporaryDirectory() as temp:
            attempt = Path(temp)
            native = {"exit_code": 2, "evaluation_results": [{
                "item_id": "E1", "instances": ["routing_01"],
                "status": "evaluation_failed", "value": None, "accepted": None}]}
            for name in SUPPLEMENTAL_REPORTS:
                save(attempt / name, native)
            excluded = ("verification/inputs/reference.assignment.json",
                        "verification/inputs/submissions/probe/routing_01/assignment.json")
            for name in excluded:
                save(attempt / name, {"private_answer": "never-inline-fixture-vector"})
            supplied = diagnostics(attempt)
            for name in SUPPLEMENTAL_REPORTS:
                self.assertTrue(readable(name), name)
                self.assertEqual(json.loads(supplied[name]), native)
            for name in excluded:
                self.assertFalse(readable(name), name)
                self.assertNotIn(name, supplied)
            self.assertNotIn("never-inline-fixture-vector", json.dumps(supplied))


if __name__ == "__main__":
    unittest.main()
