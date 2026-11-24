"""A long Scope paragraph must not be mislabeled as unusable scientific data."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import NeedsReview
from common import load
from pipeline import scope_task


def scope():
    return dict.fromkeys(('goal','preserved','changes','evaluation_unit','public_data',
                          'private_data','submission','scoring'), 'Complete original scientific requirements') | {'evidence_ids':['D1']}


class ScopeFeedback(unittest.TestCase):
    def test_oversize_is_reported_exactly_and_model_corrects_without_host_truncation(self):
        bad = scope() | {'changes': 'x'*4092}
        good = scope()
        calls = []
        def stage(agent, name, context, *args):
            calls.append(context)
            return bad if len(calls)==1 else good
        with tempfile.TemporaryDirectory() as temp, patch('pipeline.stage', side_effect=stage):
            p = Path(temp)
            result = scope_task(None, {'source_evidence':[{'id':'D1'}]}, p, p/'events.jsonl')
            self.assertEqual(result, good)
            feedback = calls[1]['scope_validation_feedback']
            self.assertIn('4092 characters exceeds 4000', feedback['errors'][0])
            self.assertEqual(len(feedback['previous_result']['changes']), 4092)
            self.assertEqual(load(p/'task-plan.json'), good)

    def test_real_evidence_gap_is_not_retried_or_rewritten(self):
        with tempfile.TemporaryDirectory() as temp, patch('pipeline.stage', side_effect=NeedsReview('Actual observations absent')) as stage:
            p = Path(temp)
            with self.assertRaisesRegex(NeedsReview, 'Actual observations absent'):
                scope_task(None, {'source_evidence':[{'id':'D1'}]}, p, p/'events.jsonl')
            self.assertEqual(stage.call_count, 1)
            self.assertFalse((p/'task-plan.json').exists())


if __name__ == '__main__':
    unittest.main()
