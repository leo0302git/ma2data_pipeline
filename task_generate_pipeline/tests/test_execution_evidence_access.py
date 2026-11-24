"""The verifier must read real per-control reports while retaining its file boundary."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import readable


class ExecutionEvidenceAccess(unittest.TestCase):
    def test_actual_previously_denied_native_reports_are_readable(self):
        for path in (
            'evidence/static-check.json', 'evidence/build-evaluator.log',
            'verification/reference/format/score.json',
            'verification/reference/format/run.log.runtime.json',
            'verification/active-balance-violation/score/score.json',
            'verification/bus-inventory-join-mismatch/score/run.log',
            'verification/feasible-higher-cost/score/run.log.runtime.json',
            'verification/missing-summary/format/score.json',
            'verification/relocated/score/score.json',
            'verification/relocated/score/run.log.runtime.json',
            'verification/source-audit-receipt.json', 'verification/receipt.json',
            'verification/make-cases.log.runtime.json',
            'task/evaluation/assets/assessment/method_program.py',
        ):
            with self.subTest(path=path): self.assertTrue(readable(path))

    def test_report_rule_does_not_expose_account_files_or_submission_payloads(self):
        for path in (
            'private/accounts.json', 'private/codex/auth.json',
            'verification/reference/submission/solution.json',
            'verification/inputs/submissions/reference/solution.json',
            'verification/reference/score/secret.env',
            'verification/reference/score/accounts.json',
            'verification/../private/codex/auth.json',
        ):
            with self.subTest(path=path): self.assertFalse(readable(path))


if __name__ == '__main__':
    unittest.main()
