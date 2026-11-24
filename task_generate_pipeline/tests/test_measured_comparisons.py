"""Publication must not silently equate measured references with reported SOTA."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comparisons import MEASURED, STRICT, LEGACY_SEMANTICS, audit_metadata
from contracts import agent_contract
from reference import NatureGym
from agent import safe_error_text


def fixture():
    rows = [{'method': name, 'method_role': role, 'result': value, 'calibration_id': name,
             'reference_origin': 'measured', 'literature_sota': False,
             'information_boundary': 'solver_inputs_only', 'comparison_conditions_id': 'frozen-fold-0',
             'is_sota': role is None, 'baseline_roles': [] if role is None else ['zero']}
            for name, role, value in [('actual-strong', None, 0.9), ('actual-weak', 'baseline', 0.7)]]
    meta = {'comparison_policy': {'mode': MEASURED, 'version': 1, 'literature_sota': False,
                                 'legacy_is_sota_semantics': LEGACY_SEMANTICS},
            'evaluation_comparisons': [{'item_id': 'E1', 'comparison_results': [
                {'instances': ['a'], 'results': rows}]}]}
    calibration = [{'id': x['calibration_id'], 'method': x['method'], 'value': x['result'],
                    'conditions': 'Fixture: same frozen fold, metric and protocol, actual execution receipt'}
                   for x in rows]
    return meta, calibration


class MeasuredComparisons(unittest.TestCase):
    def test_explicit_profile_keeps_original_guides_but_changes_admissibility(self):
        ref = NatureGym()
        p = agent_contract(ref, MEASURED)['comparison_policy']
        self.assertEqual(p['mode'], MEASURED)
        self.assertEqual(p['metadata_guide'], ref.files['metadata_guide.md'].decode())
        self.assertIn('oracle', p['rule'])
        self.assertFalse(p['strict_upstream_complete'])
        meta, observations = fixture()
        result = audit_metadata(meta, observations, MEASURED)
        self.assertEqual(len(result['linked_observations']), 2)
        self.assertFalse(result['strict_upstream_complete'])
        with self.assertRaises(ValueError):
            audit_metadata(meta, observations, STRICT)

    def test_rejects_unmeasured_oracle_mismatched_and_unlabeled_claims(self):
        for mutation in ('unlabeled', 'oracle', 'score', 'split', 'paper_method', 'duplicate', 'bool'):
            with self.subTest(mutation=mutation):
                meta, obs = fixture()
                rows = meta['evaluation_comparisons'][0]['comparison_results'][0]['results']
                if mutation == 'unlabeled': del meta['comparison_policy']
                elif mutation == 'oracle': rows[0]['information_boundary'] = 'private_answers'
                elif mutation == 'score': rows[0]['result'] = 1.0
                elif mutation == 'split': rows[1]['comparison_conditions_id'] = 'another-fold'
                elif mutation == 'paper_method': rows[0]['method_role'] = 'paper_method'
                elif mutation == 'duplicate': rows[1]['calibration_id'] = rows[0]['calibration_id']
                elif mutation == 'bool': rows[0]['result'] = True
                with self.assertRaises(ValueError): audit_metadata(meta, obs, MEASURED)

    def test_error_diagnostics_redact_credentials(self):
        text = safe_error_text('https://alice:secret@127.0.0.1\nAuthorization: bearer secret\nrequest timed out')
        self.assertNotIn('secret', text)
        self.assertIn('request timed out', text)


if __name__ == '__main__':
    unittest.main()
