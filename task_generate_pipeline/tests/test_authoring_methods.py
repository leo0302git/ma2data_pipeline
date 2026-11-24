from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from authoring_methods import MethodContractError, execute_methods, read_jobs
from common import load, save, tree_sha
from runtime import Docker, RuntimeUnavailable


class IsolatedMethodsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script = self.root / 'work/prepare.py'
        self.script.parent.mkdir()
        self.script.write_text('pass\n')
        self.helpers = self.script.parent / 'prepare_lib'
        self.helpers.mkdir()
        (self.helpers / 'finalize.py').write_text('pass\n')
        self.public, self.private, self.records, self.evidence = [self.root / p for p in
            ('staging/input/data', 'staging/evaluation/assets', 'staging/records', 'evidence')]
        for p in (self.public, self.private, self.records, self.evidence):
            p.mkdir(parents=True)
        (self.public / 'value.txt').write_text('7')
        (self.private / 'private.txt').write_text('not an input to either method')
        self.jobs = []
        for name in ('weak', 'strong'):
            p = self.helpers / 'methods' / name / 'main.py'
            p.parent.mkdir(parents=True)
            p.write_text('pass\n')
            self.jobs.append({'id': name, 'program': f'methods/{name}/main.py',
                              'timeout_seconds': 3, 'args': []})
        self.manifest = self.records / 'method-jobs.json'
        self.write_jobs()
        self.calls = []
        self.method_failure = False
        self.finalizer_action = None
        self.linked_output = False
        self.change_public = False

    def write_jobs(self):
        save(self.manifest, {'schema': 1, 'jobs': self.jobs})

    def container_run(self, entry, args, mounts, log, limit):
        self.calls.append((entry, args, mounts, limit))
        views = {target: (path, readonly) for path, target, readonly in mounts}
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text('test runner execution\n')
        code = 7 if self.method_failure and '/method-runs' not in views else 0
        save(log.with_suffix(log.suffix + '.runtime.json'), {'exit_code': code, 'kind': 'unit-test fake'})
        if '/method-runs' in views:
            self.assertTrue(views['/out/public'][1])
            self.assertTrue(views['/method-runs'][1])
            self.assertFalse(views['/out/private'][1])
            save(self.records / 'calibration.json', [])
            if self.finalizer_action:
                self.finalizer_action()
        else:
            self.assertEqual(set(views), {'/input', '/program', '/out'})
            self.assertEqual(views['/input'], (self.public, True))
            self.assertTrue(views['/program'][1])
            self.assertFalse(views['/out'][1])
            output = views['/out'][0]
            self.assertEqual(list(output.iterdir()), [])
            if self.linked_output:
                os.link(self.private / 'private.txt', output / 'forbidden-link.txt')
            else:
                save(output / 'result.json', {'value': int((views['/input'][0] / 'value.txt').read_text())})
            if self.change_public:
                (self.public / 'value.txt').write_text('changed externally')
        return code

    def execute(self, remaining=30):
        return execute_methods(self.script, self.public, self.private, self.records,
                               self.evidence, 'actual-image-id', self.container_run, lambda: remaining)

    def test_isolated_fresh_mounts_and_published_hash_bound_archive(self):
        public_before = tree_sha(self.public)
        result = self.execute()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(result['public_sha256'], public_before)
        self.assertEqual(tree_sha(self.private / 'host-method-runs'), result['archive_sha256'])
        outputs = [next(p for p, dest, _ in call[2] if dest == '/out') for call in self.calls[:2]]
        self.assertNotEqual(outputs[0], outputs[1])
        for row in result['jobs']:
            self.assertEqual(load(self.private / row['private_archive'] / 'output/result.json'), {'value': 7})

    def test_rejects_private_or_traversing_program_before_execution(self):
        for value in ('../../private/main.py', 'methods/strong/main.py', '/private/main.py'):
            with self.subTest(program=value):
                self.jobs[0]['program'] = value
                self.write_jobs()
                with self.assertRaises(ValueError):
                    self.execute()
        self.assertEqual(self.calls, [])

    def test_rejects_duplicate_ids_and_boolean_timeout(self):
        self.jobs[1]['id'] = 'weak'
        self.write_jobs()
        with self.assertRaises(MethodContractError):
            read_jobs(self.manifest, self.helpers)
        self.jobs[1]['id'] = 'strong'
        self.jobs[0]['timeout_seconds'] = True
        self.write_jobs()
        with self.assertRaises(MethodContractError):
            self.execute()
        self.assertEqual(self.calls, [])

    def test_insufficient_shared_budget_stops_before_any_method(self):
        with self.assertRaisesRegex(MethodContractError, 'no time'):
            self.execute(remaining=6)
        self.assertEqual(self.calls, [])

    def test_method_failure_does_not_finalize_or_publish(self):
        self.method_failure = True
        with self.assertRaisesRegex(MethodContractError, 'exit7'):
            self.execute()
        self.assertEqual(len(self.calls), 1)
        self.assertFalse((self.private / 'host-method-runs').exists())
        self.assertTrue((self.evidence / 'method-runs/weak/run.log.runtime.json').is_file())

    def test_finalizer_cannot_replace_the_declared_protocol(self):
        self.finalizer_action = lambda: save(self.manifest, {'schema': 1, 'jobs': []})
        with self.assertRaisesRegex(MethodContractError, 'declared protocol'):
            self.execute()
        self.assertFalse((self.private / 'host-method-runs').exists())

    def test_linked_outputs_are_rejected_before_publishing(self):
        self.linked_output = True
        with self.assertRaisesRegex(ValueError, 'private regular artifact'):
            self.execute()
        self.assertEqual(len(self.calls), 1)
        self.assertFalse((self.private / 'host-method-runs').exists())

    def test_legacy_preparation_without_manifest_is_unchanged(self):
        self.manifest.unlink()
        save(self.records / 'calibration.json', [{'previous': 'legacy record'}])
        self.assertIsNone(self.execute())
        self.assertEqual(self.calls, [])

    def test_budget_exhaustion_and_exception_restore_runner_limit(self):
        runner = Docker('base', 20, '4g', 2)
        seen = []

        def fail(*args):
            seen.append(runner.timeout)
            raise RuntimeUnavailable('expected failure')

        runner.run = fail
        with patch('runtime.time.monotonic', return_value=5):
            with self.assertRaises(RuntimeUnavailable):
                runner._run_budgeted('image', 'python', [], [], self.evidence / 'log', 10, 3)
        self.assertEqual(seen, [3])
        self.assertEqual(runner.timeout, 20)
        with patch('runtime.time.monotonic', return_value=11):
            with self.assertRaisesRegex(RuntimeUnavailable, 'budget exhausted'):
                runner._run_budgeted('image', 'python', [], [], self.evidence / 'log', 10)
        self.assertEqual(seen, [3])

    def test_public_mutation_is_detected(self):
        self.change_public = True
        with self.assertRaisesRegex(MethodContractError, 'Frozen public inputs'):
            self.execute()
        self.assertFalse((self.private / 'host-method-runs').exists())


if __name__ == '__main__':
    unittest.main()
