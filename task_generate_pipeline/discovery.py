"""Retrieve pinned public task briefs and let an Agent discover a group seed."""

import hashlib
import copy
import json
import math
import os
import re
import shutil
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import ProxyHandler, Request, build_opener
from urllib.error import HTTPError

from agent import AccountUnavailable, NeedsReview, stage
from common import digest, file_sha, load, save, relative
from contracts import SUBMISSION_BACKEND
from sources import COMPLETE, extension_matches, public_shape, resolve_sources


HOSTS = {'api.github.com', 'raw.githubusercontent.com', 'huggingface.co'}
PROVIDERS = {
    'TB-Science': ('github', 'harbor-framework/terminal-bench-science', 'main'),
    'MLE-bench': ('github', 'openai/mle-bench', 'main'),
    'NatureBench': ('huggingface', 'FrontisAI/NatureBench', '27e2dc304464257d2884fa6996bc8ca5d4a2256b'),
}
SLUGS = {'TB-Science': 'tbscience', 'MLE-bench': 'mlebench', 'NatureBench': 'naturebench'}
TERMS = {
    '数学': 'mathematics mathematical operations research integer linear programming combinatorial',
    '电气工程': 'electrical electricity power grid optimal flow dispatch voltage generator',
    '化学': 'chemistry chemical molecular material simulation parameter inference',
    '天文学': 'astronomy astronomical stellar cosmology star parameter inference',
    '大气科学': 'atmospheric atmosphere climate weather meteorological parameter inference',
    '生态学': 'ecology ecological species biodiversity animal plant environmental',
    '生物学': 'biology biological cell microscopy organism',
    '控制科学与工程': 'control policy decision scheduling multi agent resource allocation',
    '优化': 'optimization minimize maximize objective feasible constraint assignment mixed integer linear program',
    '统计／反演': 'statistics inverse inference estimate parameter uncertainty regression calibration',
    '分类': 'classification classify label supervised prediction',
    '分割／检测': 'segmentation detection localization mask image object',
}


class PublicTasks:
    def __init__(self, cache, proxy=None, workers=6, fetcher=None):
        self.cache = Path(cache).resolve()
        self.cache.mkdir(parents=True, exist_ok=True)
        self.workers = max(1, min(8, workers))
        self.opener = build_opener(ProxyHandler({'http': proxy, 'https': proxy})) if proxy else build_opener()
        self.fetcher = fetcher
        self.entries = []
        self.provider_status = {}

    def fetch(self, url, limit=512000):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username or parsed.password:
            raise ValueError('Public seed retrieval only permits the configured benchmark hosts')
        target = self.cache / 'http' / (digest(url) + '.bin')
        if target.exists():
            content = target.read_bytes()
        else:
            if self.fetcher:
                content = self.fetcher(url)
            else:
                for attempt in range(3):
                    try:
                        with self.opener.open(Request(url, headers={'User-Agent': 'task-generate-seed-discovery/1.0'}), timeout=25) as response:
                            if urlparse(response.geturl()).hostname not in HOSTS:
                                raise ValueError('Unexpected benchmark redirect host')
                            content = response.read(limit + 1)
                        break
                    except OSError as exc:
                        if attempt == 2 or isinstance(exc, HTTPError) and exc.code in {400, 401, 403, 404}:
                            raise
                        time.sleep(.5 * (attempt + 1))
            if len(content) > limit:
                raise ValueError('Benchmark reference exceeds the bounded download size')
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_suffix('.tmp')
            temp.write_bytes(content)
            temp.replace(target)
        if len(content) > limit:
            raise ValueError('Cached reference exceeds the requested size budget')
        return content

    def json(self, url, limit=24000000):
        return json.loads(self.fetch(url, limit))

    def document(self, entry):
        path = self.cache / relative(entry['document'])
        if not path.resolve().is_relative_to(self.cache):
            raise ValueError('Benchmark document leaves the private cache')
        return path

    def specs(self, benchmark):
        kind, repo, revision = PROVIDERS[benchmark]
        if kind == 'github':
            if not re.fullmatch(r'[a-f0-9]{40}', revision):
                revision = self.json(f'https://api.github.com/repos/{repo}/commits/{revision}')['sha']
            tree = self.json(f'https://api.github.com/repos/{repo}/git/trees/{revision}?recursive=1')
            if tree.get('truncated'):
                raise ValueError('GitHub tree is truncated; cannot claim complete search coverage')
            paths = [x['path'] for x in tree['tree'] if x['type'] == 'blob']
            if benchmark == 'TB-Science':
                paths = [p for p in paths if p.startswith('tasks/') and p.endswith('/instruction.md')]
            else:
                paths = [p for p in paths if p.startswith('mlebench/competitions/') and p.endswith('/description.md')]
            return [(benchmark, repo, revision, p, f'https://raw.githubusercontent.com/{repo}/{revision}/{quote(p, safe="/")}') for p in paths]
        records = [json.loads(s) for s in self.fetch(f'https://huggingface.co/datasets/{repo}/resolve/{revision}/manifest.jsonl', 4000000).decode().splitlines() if s.strip()]
        paths = [f'tasks/{x["case_id"]}/problem/README.md' for x in records]
        return [(benchmark, repo, revision, p, f'https://huggingface.co/datasets/{repo}/resolve/{revision}/{quote(p, safe="/")}') for p in paths]

    def retrieve(self, spec):
        benchmark, repo, revision, path, url = spec
        raw = self.fetch(url, 192 * 1024)
        text = raw.decode('utf-8-sig')
        if len(text.strip()) < 40 or '\0' in text:
            raise ValueError('Missing or non-text benchmark task brief')
        task_id = path.split('/')[-3] if benchmark == 'NatureBench' else path.split('/')[-2]
        uid = SLUGS[benchmark] + '/' + task_id + '-' + digest(path)[:8]
        heading = next((s.lstrip('# ').strip() for s in text.splitlines() if s.startswith('#')), task_id)
        document = self.cache / 'documents' / (digest(url) + '.md')
        document.parent.mkdir(parents=True, exist_ok=True)
        document.write_bytes(raw)
        if benchmark == 'NatureBench':
            configuration_url = url.split('/problem/')[0] + '/metadata.json'
        else:
            configuration_url = url.rsplit('/', 1)[0] + ('/task.toml' if benchmark == 'TB-Science' else '/config.yaml')
        return {'id': uid, 'benchmark': benchmark, 'task_id': task_id, 'title': heading[:300],
                'repository': repo, 'revision': revision, 'path': path, 'url': url,
                'configuration_url': configuration_url,
                'document': document.relative_to(self.cache).as_posix(), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}

    def refresh(self, force=False):
        index = self.cache / 'index.json'
        if index.exists() and not force:
            data = load(index)
            self.entries, self.provider_status = data['entries'], data['providers']
            for row in self.entries:
                if file_sha(self.document(row)) != row['sha256']:
                    raise ValueError('Cached benchmark task changed after indexing')
            return self
        specs = []
        for benchmark in PROVIDERS:
            try:
                requested = self.specs(benchmark)
                self.provider_status[benchmark] = {'requested': len(requested), 'retrieved': 0, 'errors': []}
                specs.extend(requested)
            except Exception as exc:
                self.provider_status[benchmark] = {'requested': None, 'retrieved': 0, 'errors': [str(exc)[:400]]}
        self.entries = []
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            pending = {pool.submit(self.retrieve, s): s for s in specs}
            for future in as_completed(pending):
                source = pending[future]
                try:
                    row = future.result()
                    self.entries.append(row)
                    self.provider_status[row['benchmark']]['retrieved'] += 1
                except Exception as exc:
                    self.provider_status[source[0]]['errors'].append({'path': source[3], 'reason': str(exc)[:300]})
        self.entries.sort(key=lambda row: row['id'])
        if not self.entries:
            raise ValueError('No original benchmark task briefs could be retrieved')
        save(index, {'schema': 1, 'providers': self.provider_status, 'entries': self.entries})
        return self

    def workspace(self, root, group):
        documents, rows, bag = {}, [], {}
        for entry in self.entries:
            path = f'benchmarks/task-{digest(entry["id"])[:20]}.md'
            dest = root / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.document(entry), dest)
            text = dest.read_text(encoding='utf-8-sig')
            documents[entry['id']] = path
            tokens = re.findall(r'[a-z][a-z0-9_-]{2,}', (entry['task_id'] + ' ' + entry['path'] + ' ' + text).lower())
            bag[entry['id']] = Counter(tokens)
            rows.append({k: entry[k] for k in ('id', 'benchmark', 'title', 'task_id', 'path', 'url', 'revision')} | {'document': path})
        frequency = Counter(t for counts in bag.values() for t in counts)
        query = set((' '.join(TERMS.get(x, x) for x in group)).lower().split())
        scored = [(sum(math.log1p(counts.get(t, 0)) * math.log(1 + len(rows)/(1 + frequency[t])) for t in query), uid) for uid, counts in bag.items()]
        scores = dict((uid, score) for score, uid in scored)
        ranked = []
        for benchmark in PROVIDERS:
            ranked.extend(sorted((r for r in rows if r['benchmark'] == benchmark), key=lambda x: scores[x['id']], reverse=True)[:6])
        for page in range(0, len(rows), 40):
            save(root / f'benchmarks/index-{page//40:03d}.json', rows[page:page+40])
        return documents, ranked


class ObservedAgent:
    def __init__(self, agent):
        self.agent, self.reads = agent, set()

    def call(self, name, context, event_log):
        response = self.agent.call(name, context, event_log)
        self.reads.update(x for x in response.get('read', []) if isinstance(x, str))
        return response


def discover_missing(rows, catalog_path, bindings, agent, folder, *, cache, proxy=None,
                     data_root=None, source_profiles=None, corpus=None):
    """Append evidence-backed group adaptations; never rewrite an existing binding."""
    from sources import read_seeds
    known = read_seeds(catalog_path)
    groups = defaultdict(list)
    for row in rows:
        if row['download_status'] == COMPLETE and row['paths']:
            groups[(row['domain'], row['category'])].append(row)
    missing = [(group, members) for group, members in groups.items()
               if json.dumps(group, ensure_ascii=False, separators=(',', ':')) not in bindings
               and not any(list(group) in s['groups'] and any(extension_matches(r, s) for r in members) for s in known.values())]
    if not missing:
        return {'groups': [], 'providers': {}, 'added': 0}
    corpus = corpus or PublicTasks(cache, proxy).refresh()
    reports, additions = [], []
    profile_map = {x['source_id']: x for x in (load(source_profiles) if source_profiles else [])}
    for group, members in sorted(missing, key=lambda x: (-len(x[1]), x[0])):
        root = folder / ('group-' + digest(group)[:12])
        root.mkdir(parents=True, exist_ok=True)
        documents, ranked = corpus.workspace(root, group)
        evidence = []
        for row in members:
            info = {'source_id': row['source_id'], 'filenames': [Path(p).name for p in row['paths']]}
            if row['source_id'] in profile_map:
                info['profile'] = copy.deepcopy(profile_map[row['source_id']])
                for item in info['profile'].get('files', []):
                    if 'solve' in item:
                        item['solve'] = {k: v for k, v in item['solve'].items()
                                         if k in {'status', 'seconds', 'value_valid'}}
            elif data_root and Path(data_root).is_dir():
                try:
                    files = resolve_sources(row, Path(data_root).resolve(), str(data_root))
                    info['profile'] = [{'name': p.name, 'bytes': p.stat().st_size, 'sha256': file_sha(p), 'shape': public_shape(p),
                                        'text_head': p.read_bytes()[:6000].decode('utf-8', 'replace') if p.suffix.lower() in {'.py', '.m', '.csv', '.tab', '.txt'} else None} for p in files]
                except (OSError, ValueError) as exc:
                    info['profile_error'] = str(exc)
            evidence.append(info)
        save(root / 'source-profiles.json', evidence)
        observed = ObservedAgent(agent)
        context = {'group': list(group), 'source_count': len(members), 'source_profiles_path': 'source-profiles.json',
                   'package_unit': 'one_source', 'group_count_is_coverage_only': True,
                   'submission_backend': SUBMISSION_BACKEND,
                   'ranked_candidates': ranked, 'corpus_count': len(corpus.entries), 'provider_coverage': corpus.provider_status,
                   'full_index_pattern': 'benchmarks/index-*.json', 'selection_rule': 'Inspect actual original task documents; one seed per supplied group; preserve existing bindings.'}
        result_row = {'group': list(group), 'sources': [r['source_id'] for r in members]}
        try:
            fingerprint = digest({'context': context, 'evidence': evidence,
                'documents': [{k: e.get(k) for k in ('id', 'sha256', 'revision', 'configuration_url')}
                              for e in corpus.entries],
                'skill_sha256': file_sha(Path(__file__).parent / 'skills/seed-discover/SKILL.md')})
            checkpoint = root / 'selection.json'
            previous = load(checkpoint) if checkpoint.exists() else {}
            fresh = previous.get('fingerprint') != fingerprint
            if previous.get('fingerprint') == fingerprint:
                choice = previous['choice']
                observed.reads.update(previous['reads'])
                result_row['reused_selection'] = True
            else:
                choice = stage(observed, 'seed-discover', context, root,
                               lambda p: p == 'source-profiles.json' or p.startswith('benchmarks/'), lambda p: False,
                               root / 'events.jsonl', turns=6)
            if choice.get('status') != 'selected':
                if fresh and choice.get('status') == 'no_match' and isinstance(choice.get('reason'), str) and choice['reason'].strip():
                    save(checkpoint, {'fingerprint': fingerprint, 'choice': choice, 'reads': sorted(observed.reads)})
                raise NeedsReview(choice.get('reason') or 'No defensible task transfer found in the retrieved benchmark corpus')
            entry = next((r for r in corpus.entries if r['id'] == choice.get('candidate_id')), None)
            if entry is None or documents[entry['id']] not in observed.reads:
                raise NeedsReview('Seed discovery must read the selected original task brief from the retrieved corpus')
            core = choice.get('core_assessment')
            requirements = choice.get('minimum_source_requirements')
            adaptations = choice.get('adaptations')
            reason = choice.get('reason')
            if (not isinstance(core, str) or not 40 <= len(core) <= 6000
                    or choice.get('package_unit') != 'one_source'
                    or not isinstance(requirements, list) or not requirements
                    or not isinstance(adaptations, list) or not isinstance(reason, str) or not reason.strip()
                    or any(not isinstance(x, str) or not x.strip() or len(x) > 2000 for x in requirements + adaptations)):
                raise NeedsReview('Seed discovery omitted the assessment contract or concrete source requirements')
            if fresh:
                save(checkpoint, {'fingerprint': fingerprint, 'choice': choice, 'reads': sorted(observed.reads)})
            raw = corpus.document(entry).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == entry['sha256']
            original_documents = [{'url': entry['url'], 'sha256': entry['sha256'], 'text': raw.decode('utf-8')}]
            if entry.get('configuration_url'):
                config = corpus.fetch(entry['configuration_url'], 64 * 1024)
                original_documents.append({'url': entry['configuration_url'],
                                           'sha256': hashlib.sha256(config).hexdigest(), 'text': config.decode('utf-8')})
            suffixes = sorted({''.join(Path(p).suffixes[-2:]).lower() for r in members for p in r['paths']})
            seed = {'id': entry['id'] + '--' + digest(group)[:8], 'benchmark': entry['benchmark'],
                    'revision': entry['revision'], 'url': entry['url'], 'groups': [list(group)], 'file_suffixes': suffixes,
                    'contract': 'Generation unit: ONE inventory source per task package; group counts describe coverage only.\n' + core + '\nMinimum source requirements for that individual source: ' + '; '.join(requirements)
                                + '\nDocumented adaptations: ' + '; '.join(adaptations),
                    'documents': original_documents,
                    'upstream_id': entry['id'], 'selection_reason': reason}
            additions.append(seed)
            result_row.update(status='selected', seed_id=seed['id'], upstream_id=entry['id'], reason=reason,
                              original_document_sha256=entry['sha256'], model_document_reads=sorted(observed.reads))
        except AccountUnavailable:
            raise
        except (OSError, ValueError, TypeError, KeyError) as exc:
            result_row.update(status='needs_review', reason=str(exc))
        reports.append(result_row)
        save(folder / 'discovery-report.json', {'groups': reports, 'providers': corpus.provider_status, 'added': len(additions)})
    if additions:
        catalog = load(catalog_path)
        ids = {r['id'] for r in catalog['seeds']}
        if any(r['id'] in ids for r in additions):
            raise ValueError('Discovered seed ID collision; existing catalog is unchanged')
        catalog['seeds'].extend(additions)
        original_stat = catalog_path.stat()
        save(catalog_path, catalog)
        catalog_path.chmod(original_stat.st_mode & 0o777)
        if hasattr(os, 'chown') and getattr(os, 'geteuid', lambda: None)() == 0:
            os.chown(catalog_path, original_stat.st_uid, original_stat.st_gid)
        enriched = read_seeds(catalog_path)
        for record in reports:
            if record['status'] == 'selected':
                key = json.dumps(record['group'], ensure_ascii=False, separators=(',', ':'))
                if key in bindings:
                    raise ValueError('A binding appeared during discovery; do not replace it')
                seed = enriched[record['seed_id']]
                bindings[key] = {'seed_id': seed['id'], 'seed_sha256': digest(seed), 'reason': record['reason']}
    return {'groups': reports, 'providers': corpus.provider_status, 'added': len(additions)}
