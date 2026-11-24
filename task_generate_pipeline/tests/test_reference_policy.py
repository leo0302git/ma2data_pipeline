"""Prevent another omission of the upstream comparison-evidence requirements."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contracts import agent_contract
from reference import NatureGym


class ReferencePolicy(unittest.TestCase):
    def test_author_and_verifier_contract_contains_exact_reported_result_rules(self):
        ref = NatureGym()
        policy = agent_contract(ref)['comparison_policy']
        self.assertEqual(policy['mode'], 'strict_upstream_reported')
        self.assertEqual(policy['metadata_guide'], ref.files['metadata_guide.md'].decode())
        selection, consistency = policy['level3_comparison_sections']
        self.assertIn('Program Reference Selection', selection)
        self.assertIn('reported', selection)
        self.assertIn('Comparison Evidence', consistency)
        self.assertIn('is_sota: true', consistency)
        self.assertIn('Aggregating Reported Results', policy['level2_validity_section'])
        self.assertIn('no', policy['rule'].lower())

    def test_missing_guide_cannot_silently_restore_old_incomplete_contract(self):
        ref = NatureGym()
        del ref.files['metadata_guide.md']
        with self.assertRaises(KeyError):
            agent_contract(ref)


if __name__ == '__main__':
    unittest.main()
