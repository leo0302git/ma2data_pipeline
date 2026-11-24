from pathlib import Path
import json
import tempfile
import unittest

from agent import stage, NeedsReview


def action(value, writes=None, status='done'):
    return {'status': status, 'reason': 'exact scientific statement', 'read': [],
            'write': writes or [], 'result_json': value}


class StructuredReplyTest(unittest.TestCase):
    def test_format_correction_has_a_reserved_call_after_the_last_action(self):
        calls = []
        class Agent:
            def call(self, *args):
                calls.append(1)
                return action('{"broken":' if len(calls) == 1 else '{"scientific_gap":"preserved"}')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = stage(Agent(), 'task-scope', {}, root, lambda p: False, lambda p: False, root / 'events', turns=1)
        self.assertEqual(result, {'scientific_gap': 'preserved'})
        self.assertEqual(len(calls), 2)

    def test_nonfinite_values_and_duplicate_keys_require_explicit_model_correction(self):
        for bad in ('{"score":NaN}', '{"score":Infinity}', '{"score":1,"score":2}'):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                calls = []
                class Agent:
                    def call(self, *args):
                        calls.append(1)
                        return action(bad if len(calls) == 1 else '{"score":null,"reason":"measurement missing"}')
                result = stage(Agent(), 'task-scope', {}, root, lambda p: False, lambda p: False, root / 'events')
                self.assertIsNone(result['score'])
                self.assertEqual(len(calls), 2)

    def test_malformed_json_is_corrected_once_without_partial_edits_or_content_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = '{"goal":"line one\nline two","evidence_ids":["source-1"]}'
            calls = []

            class Agent:
                def call(self, name, context, log):
                    calls.append(context)
                    if len(calls) == 1:
                        return action(bad, [{'path': 'task/result.txt', 'content': 'must not be applied'}])
                    assert not (root / 'task/result.txt').exists()
                    feedback = json.loads(context['read_results']['action_feedback'])
                    assert feedback['previous_result_json'] == bad
                    assert 'Strict JSON parsing failed' in feedback['error']
                    return action(json.dumps({'goal': 'line one\nline two', 'evidence_ids': ['source-1']}),
                                  [{'path': 'task/result.txt', 'content': 'valid complete action'}])

            result = stage(Agent(), 'task-scope', {}, root, lambda p: False,
                           lambda p: p == 'task/result.txt', root / 'events')
            self.assertEqual(result, {'goal': 'line one\nline two', 'evidence_ids': ['source-1']})
            self.assertEqual((root / 'task/result.txt').read_text(), 'valid complete action')
            self.assertEqual(len(calls), 2)

    def test_repeated_malformed_json_stops_without_an_unbounded_retry(self):
        calls = []

        class Agent:
            def call(self, name, context, log):
                calls.append(context)
                return action('{"bad":')

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(NeedsReview, 'after bounded correction'):
                stage(Agent(), 'task-verify', {}, root, lambda p: False, lambda p: False, root / 'events')
        self.assertEqual(len(calls), 2)

    def test_real_scientific_gap_is_not_retried_or_reclassified_as_json_failure(self):
        calls = []

        class Agent:
            def call(self, name, context, log):
                calls.append(context)
                result = action('{malformed but irrelevant}', status='needs_review')
                result['reason'] = 'The source contains no independently established labels'
                return result

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(NeedsReview, 'no independently established labels'):
                stage(Agent(), 'task-scope', {}, root, lambda p: False, lambda p: False, root / 'events')
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
