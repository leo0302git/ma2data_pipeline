"""Missing-group discovery uses original documents and preserves frozen mappings."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import load, save, digest, file_sha
from discovery import PublicTasks, discover_missing
from operations import audit_preservation
from pipeline import build_plan, main
from sources import COMPLETE, read_seeds


BRIEF = '# Integer optimization\nFind a feasible assignment minimizing the supplied linear cost. Submit every variable; independently check bounds, integer domains, constraints and cost.'


class ChoiceAgent:
    def __init__(self, read=True):
        self.read = read
        self.calls = 0
    def call(self, name, context, log):
        self.calls += 1
        assert context['submission_backend']['mode'] == 'file_artifacts'
        candidate = context['ranked_candidates'][0]
        if self.read and self.calls == 1:
            return {'status': 'continue', 'reason': 'Inspect source and original task',
                    'read': ['source-profiles.json', candidate['document']], 'write': [], 'result_json': '{}'}
        choice = {'status': 'selected', 'package_unit': 'one_source', 'candidate_id': candidate['id'],
                  'reason': 'Same mathematical optimization discipline and independently checked assignment objective.',
                  'core_assessment': 'Preserve the full encoded linear optimization problem; submit one value per variable and independently verify feasibility and objective.',
                  'minimum_source_requirements': ['Complete model coefficients, variable domains, row sides and objective'],
                  'adaptations': ['Replace the original instance with the supplied source; keep every encoded constraint.']}
        return {'status': 'done', 'reason': 'Selected from actual brief', 'read': [], 'write': [], 'result_json': json.dumps(choice)}


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog = self.root / 'seeds.json'
        save(self.catalog, {'schema': 1, 'seeds': []})
        self.corpus = PublicTasks(self.root / 'cache')
        doc = self.corpus.cache / 'brief.md'
        doc.write_bytes(BRIEF.encode('utf-8'))
        self.corpus.entries = [{'id': 'tbscience/integer', 'benchmark': 'TB-Science', 'task_id': 'integer',
            'title': 'Integer optimization', 'repository': 'harbor-framework/terminal-bench-science',
            'revision': 'a' * 40, 'path': 'tasks/mathematics/integer/instruction.md',
            'url': 'https://raw.githubusercontent.com/harbor-framework/terminal-bench-science/' + 'a'*40 + '/tasks/integer/instruction.md',
            'document': 'brief.md', 'sha256': hashlib.sha256(doc.read_bytes()).hexdigest(), 'bytes': doc.stat().st_size}]
        self.corpus.provider_status = {'TB-Science': {'requested': 1, 'retrieved': 1, 'errors': []}}
        self.rows = [{'source_id': str(i), 'resource_id': 'resource-' + str(i), 'domain': '数学', 'category': '优化',
                      'download_status': COMPLETE, 'paths': [f'case{i}.mps.gz']} for i in range(2)]

    def discover(self, agent, bindings=None):
        return discover_missing(self.rows, self.catalog, bindings if bindings is not None else {}, agent,
                                self.root / 'work', cache=self.root / 'cache', corpus=self.corpus)

    def test_group_selection_reads_original_and_freezes_one_seed_for_all_members(self):
        bindings = {}
        agent = ChoiceAgent()
        self.catalog.chmod(0o644)
        mode = self.catalog.stat().st_mode & 0o777
        result = self.discover(agent, bindings)
        self.assertEqual(self.catalog.stat().st_mode & 0o777, mode)
        self.assertEqual(result['added'], 1)
        seeds = read_seeds(self.catalog)
        seed = next(iter(seeds.values()))
        self.assertEqual(seed['documents'][0]['text'], BRIEF)
        self.assertEqual(seed['revision'], 'a' * 40)
        self.assertIn('ONE inventory source per task package', seed['contract'])
        plan = build_plan(self.rows, seeds, bindings, None, self.root)
        self.assertEqual(len(plan['jobs']), 2)
        self.assertEqual({j['seed_sha256'] for j in plan['jobs']}, {digest(seed)})
        self.assertEqual(agent.calls, 2)
        self.rows = [row | {'paths': ['new-encoding.parquet']} for row in self.rows]
        another = ChoiceAgent()
        self.assertEqual(self.discover(another, bindings)['added'], 0)
        self.assertEqual(another.calls, 0)
        later = build_plan(self.rows, seeds, bindings, None, self.root)
        self.assertEqual(len(later['jobs']), 2)
        self.assertEqual({job['seed_sha256'] for job in later['jobs']}, {digest(seed)})

    def test_discovered_binding_passes_preservation_audit(self):
        bindings = {}
        self.assertEqual(self.discover(ChoiceAgent(), bindings)['added'], 1)
        bindings_path = self.root / 'bindings.json'
        save(bindings_path, {'groups': bindings})
        anchor = self.root / 'anchor.txt'
        anchor.write_text('preserved artifact', encoding='utf-8')
        baseline = {
            'schema': 1, 'kind': 'pipeline-preservation', 'project_root': str(self.root),
            'artifacts': [{'kind': 'file', 'path': str(anchor), 'sha256': file_sha(anchor)}],
            'registries': [{'seeds': str(self.catalog), 'bindings': str(bindings_path), 'frozen_groups': {}}],
        }
        self.assertEqual(audit_preservation(baseline, self.root)['checked']['shared_seed_registry'], 1)

        catalog = load(self.catalog)
        catalog['seeds'][0]['contract'] += ' Changed assessment.'
        save(self.catalog, catalog)
        with self.assertRaisesRegex(ValueError, 'Registry seed hash'):
            audit_preservation(baseline, self.root)

    def test_agent_must_read_selected_original_document(self):
        result = self.discover(ChoiceAgent(read=False))
        self.assertEqual(result['added'], 0)
        self.assertEqual(result['groups'][0]['status'], 'needs_review')
        self.assertEqual(load(self.catalog)['seeds'], [])

    def test_rejected_unread_selection_does_not_block_later_valid_model_attempt(self):
        self.discover(ChoiceAgent(read=False))
        self.assertFalse(list((self.root/'work').glob('group-*/selection.json')))
        agent = ChoiceAgent()
        self.assertEqual(self.discover(agent)['added'], 1)
        self.assertEqual(agent.calls, 2)

    def test_embedded_original_document_is_integrity_checked(self):
        self.discover(ChoiceAgent())
        data = load(self.catalog)
        data['seeds'][0]['documents'][0]['text'] += '\nchanged'
        save(self.catalog, data)
        with self.assertRaises(ValueError):
            read_seeds(self.catalog)

    def test_public_fetch_rejects_non_benchmark_hosts(self):
        with self.assertRaises(ValueError):
            self.corpus.fetch('https://example.org/not-a-benchmark')

    def test_selected_package_configuration_is_retained_at_pinned_url(self):
        metadata = b'# Original package configuration\n[verifier]\ntimeout_sec = 600\n'
        url = self.corpus.entries[0]['url'].rsplit('/', 1)[0] + '/task.toml'
        self.corpus.entries[0]['configuration_url'] = url
        self.corpus.fetcher = lambda requested: metadata
        self.discover(ChoiceAgent())
        docs = next(iter(read_seeds(self.catalog).values()))['documents']
        self.assertEqual(docs[1]['url'], url)
        self.assertEqual(docs[1]['sha256'], hashlib.sha256(metadata).hexdigest())

    def test_configuration_retry_reuses_same_model_choice_without_another_call(self):
        self.corpus.entries[0]['configuration_url'] = self.corpus.entries[0]['url'].rsplit('/', 1)[0] + '/task.toml'
        def fail(url):
            raise OSError('Temporary reference network failure')
        self.corpus.fetcher = fail
        agent = ChoiceAgent()
        self.assertEqual(self.discover(agent)['added'], 0)
        self.assertEqual(agent.calls, 2)
        self.corpus.fetcher = lambda url: b'# Original config\n[verifier]\ntimeout_sec = 600\n'
        agent.call = lambda *args: self.fail('A configuration retry must reuse its evidence-bound selection')
        retried = self.discover(agent)
        self.assertEqual(retried['added'], 1)
        self.assertTrue(retried['groups'][0]['reused_selection'])

    def test_changed_source_evidence_invalidates_saved_selection(self):
        self.corpus.entries[0]['configuration_url'] = self.corpus.entries[0]['url'].rsplit('/', 1)[0] + '/task.toml'
        self.corpus.fetcher = lambda url: (_ for _ in ()).throw(OSError('network unavailable'))
        self.discover(ChoiceAgent())
        self.rows[0]['paths'] = ['changed.mps.gz']
        agent = ChoiceAgent()
        self.discover(agent)
        self.assertEqual(agent.calls, 2)

    def test_cli_preserves_discovery_gap_in_each_source_result(self):
        inventory = self.root / 'inventory.csv'
        inventory.write_text('数据源ID,资源ID,本体学科,本体任务,下载状态,服务器路径\na,b,数学,优化,' + COMPLETE + ',a.mps.gz\n', encoding='utf-8')
        output = self.root / 'plan.json'
        gap = 'Source does not establish independent target data; inspected all original task indexes'
        report = {'groups': [{'sources': ['a'], 'status': 'needs_review', 'reason': gap}], 'providers': {}, 'added': 0}
        args = ['pipeline.py', 'plan', '--inventory', str(inventory), '--seeds', str(self.catalog),
                '--bindings', str(self.root/'bindings.json'), '--output', str(output),
                '--account-pool', str(self.root/'unused.json'), '--model', 'unused']
        with patch('pipeline.Codex'), patch('discovery.discover_missing', return_value=report), patch.object(sys, 'argv', args):
            self.assertEqual(main(), 0)
        self.assertEqual(load(output)['results'][0]['reason'], gap)


    def test_incomplete_provider_and_corrupt_cache_remain_visible(self):
        row = self.corpus.entries[0]
        corpus = PublicTasks(self.root / 'network-cache', fetcher=lambda u: BRIEF.encode())
        def specs(provider):
            if provider == 'MLE-bench':
                raise OSError('Unavailable provider fixture')
            return [(provider, row['repository'], row['revision'], row['path'], row['url'])] if provider == 'TB-Science' else []
        corpus.specs = specs
        corpus.refresh()
        self.assertEqual(len(corpus.entries), 1)
        self.assertTrue(corpus.provider_status['MLE-bench']['errors'])
        corpus.document(corpus.entries[0]).write_text('corrupted', encoding='utf-8')
        with self.assertRaises(ValueError):
            PublicTasks(corpus.cache).refresh()

    def test_preview_does_not_discover_or_write_bindings(self):
        inventory = self.root / 'inventory.csv'
        inventory.write_text('数据源ID,资源ID,本体学科,本体任务,下载状态,服务器路径\na,b,数学,优化,' + COMPLETE + ',a.mps.gz\n', encoding='utf-8')
        bindings = self.root / 'bindings.json'
        args = ['pipeline.py', 'plan', '--inventory', str(inventory), '--seeds', str(self.catalog),
                '--bindings', str(bindings), '--output', str(self.root/'plan.json'), '--preview']
        with patch.object(PublicTasks, 'refresh', side_effect=AssertionError('Preview must be offline')), patch.object(sys, 'argv', args):
            self.assertEqual(main(), 0)
        self.assertFalse(bindings.exists())
