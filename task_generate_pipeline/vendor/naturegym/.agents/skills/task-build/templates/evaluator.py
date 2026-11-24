#!/usr/bin/env python3
"""Fixed task evaluator; task-specific logic belongs in metrics.py/rubric.py.

Scoring requires eight runtime arguments; no environment or cwd defaults.
--format-only requires only --submission-dir, checks the public
submission contract and records diagnostics without running assessment hooks.
--tool-records-dir supplies trusted snapshots only to hooks that consume them.
--max-workers limits explicit context.map jobs; other work remains sequential.
Execution arguments are used only when running submitted code. Private scratch
is temporary; optional --result-file saves the private report also printed to stdout.
Exit 0: complete valid evaluation, not necessarily goal success; 1: submission
failure/invalidity; 2: evaluation failure. Linux candidate execution requires
runtime-prepared static permissions and a separate non-root UID/GID.
"""
import argparse
from concurrent.futures import Future, ThreadPoolExecutor, wait
from copy import copy
import threading
import time
import signal
import subprocess
from contextlib import ExitStack, redirect_stdout
import errno
import importlib.util
import json
import math
import os
import re
import stat
from pathlib import Path
import sys
import tempfile
import traceback

EVAL_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = EVAL_DIR.parent


def nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def relative_path(value):
    return (nonempty_string(value) and not Path(value).is_absolute()
            and '\\' not in value and '..' not in Path(value).parts and value != '.')


def image_resource_path(value):
    """A specific absolute Linux image path, not a component or system root."""
    return (nonempty_string(value) and value.startswith('/') and not value.startswith('//')
            and '\\' not in value and len(Path(value).parts) >= 3
            and all(part not in {'.', '..', ''} for part in value[1:].split('/')))


def contained_path(root, relative):
    if not relative_path(relative):
        raise ValueError('Invalid relative resource path')
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Path leaves its declared resource root')
    return path


def paths_intersect(path, tree):
    return path == tree or path.is_relative_to(tree) or tree.is_relative_to(path)


def data_path(data_root, relative):
    if not relative_path(relative):
        raise ValueError('Invalid relative data path')
    root = PACKAGE_DIR if Path(relative).is_relative_to(Path('evaluation/assets')) else data_root
    return contained_path(root, relative)


def submission_path(root: Path, instance_name: str, relative: str) -> Path:
    """Resolve within the named instance, without relocating its root via a link."""
    instance_root = root.resolve() / instance_name
    try:
        path = (instance_root / relative).resolve()
    except RuntimeError as exc:
        raise SubmissionError('Cannot resolve submitted artifact path',
                              public_message='A submitted artifact path cannot be resolved.') from exc
    except OSError as exc:
        if exc.errno != errno.ELOOP:
            raise  # Permission/I/O faults are not evidence of a submission defect.
        raise SubmissionError('Cannot resolve submitted artifact path',
                              public_message='A submitted artifact path cannot be resolved.') from exc
    if not path.is_relative_to(instance_root):
        raise SubmissionError('Submitted artifact leaves its declared instance directory',
                              public_message='A submitted artifact must remain inside its instance directory.')
    return path


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def read_json(path):
    def reject(value):
        raise ValueError('Non-finite JSON number')
    document = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_object,
                          parse_constant=reject)
    json.dumps(document, allow_nan=False)  # Also catches overflowing floats.
    return document


def string_list(value, nonempty=False):
    return (isinstance(value, list) and (bool(value) or not nonempty)
            and all(nonempty_string(v) for v in value) and len(set(value)) == len(value))


def submission_instances(task):
    """Validate only the public instance names and required artifact mappings."""
    instances = task.get('instances')
    if not isinstance(instances, list) or not instances:
        raise ValueError('instances must be a nonempty list')
    names, result = set(), []
    for instance in instances:
        if not isinstance(instance, dict):
            raise ValueError('Invalid instance record')
        name = instance.get('name')
        if (not nonempty_string(name) or name in {'.', '..'} or '/' in name
                or '\\' in name or name in names):
            raise ValueError('Instance names must be unique path components')
        names.add(name)
        deliverables = instance.get('deliverables')
        if (not isinstance(deliverables, dict) or not deliverables
                or any(not nonempty_string(k) or not relative_path(v)
                       for k, v in deliverables.items())):
            raise ValueError(f'Invalid deliverables mapping for {name}')
        result.append({'name': name, 'deliverables': dict(deliverables)})
    return result


def load_task(format_only=False):
    task = read_json(PACKAGE_DIR / 'task.json')
    if (not isinstance(task, dict) or type(task.get('schema_version')) is not int
            or task['schema_version'] != 1):
        raise ValueError('Invalid task schema_version')
    if format_only:
        return {'schema_version': 1, 'instances': submission_instances(task)}
    rows = task.get('evaluation_items')
    if not isinstance(rows, list) or not rows:
        raise ValueError('evaluation_items must be a nonempty list')
    items = {}
    for item in rows:
        if (not isinstance(item, dict) or not nonempty_string(item.get('id'))
                or item['id'] in items or not nonempty_string(item.get('name'))
                or item.get('method') not in ('program', 'rubric') or 'methods' in item):
            raise ValueError('Invalid evaluation item')
        result = item.get('result')
        if (not isinstance(result, dict) or result.get('type') not in
                {'number', 'boolean'}
                or not nonempty_string(result.get('description'))):
            raise ValueError('Invalid item result definition')
        if item.get('category') not in {'validity', 'goal_achievement'}:
            raise ValueError('Item category must be validity or goal_achievement')
        if item['category'] == 'goal_achievement' and result['type'] != 'number':
            raise ValueError('Goal achievement requires one numerical raw score')
        if item.get('scope') not in {'instance', 'joint'}:
            raise ValueError('Item scope must be instance or joint')
        for role_field in ('deliverable_roles', 'input_roles', 'reference_roles'):
            if not string_list(item.get(role_field), role_field == 'deliverable_roles'):
                raise ValueError('Item resource roles must be explicit lists')
        for bound in ('min', 'max'):
            if bound in result and not finite_number(result[bound]):
                raise ValueError('Invalid result bound')
        if 'min' in result and 'max' in result and result['min'] >= result['max']:
            raise ValueError('Invalid result range')
        if (item['method'] == 'rubric' and item['category'] == 'goal_achievement'
                and not {'min', 'max'} <= result.keys()):
            raise ValueError('Rubric goal results require complete final min/max bounds')
        if result.get('direction') not in {None, 'higher_is_better', 'lower_is_better', 'target'}:
            raise ValueError('Invalid numerical direction')
        if item['method'] == 'rubric' and result['type'] != 'number':
            raise ValueError('Rubric items require numerical scores')
        items[item['id']] = item
    if not any(i['category'] == 'goal_achievement' for i in items.values()):
        raise ValueError('Task requires at least one goal-achievement item')
    instances = task.get('instances')
    if not isinstance(instances, list) or not instances:
        raise ValueError('instances must be a nonempty list')
    names, used = set(), set()
    for instance in instances:
        if not isinstance(instance, dict):
            raise ValueError('Invalid instance record')
        name = instance.get('name')
        if (not nonempty_string(name) or name in {'.', '..'} or '/' in name
                or '\\' in name or name in names):
            raise ValueError('Instance names must be unique path components')
        names.add(name)
        for field in ('solver_data', 'inputs', 'references', 'deliverables'):
            paths = instance.get(field)
            if (not isinstance(paths, dict) or (field == 'deliverables' and not paths)
                    or any(not nonempty_string(k) or not relative_path(v) for k, v in paths.items())):
                raise ValueError(f'Invalid {field} mapping for {name}')
            if field == 'solver_data' and any(
                    not Path(v).is_relative_to(Path('input/data')) for v in paths.values()):
                raise ValueError('Solver data must map to input/data')
        ids = instance.get('evaluation_items')
        if not string_list(ids, True) or not set(ids) <= set(items):
            raise ValueError('Invalid instance evaluation-item inventory')
        for iid in ids:
            for role_field, mapping in (('deliverable_roles', 'deliverables'), ('input_roles', 'inputs'), ('reference_roles', 'references')):
                if items[iid]['scope'] == 'instance' and not set(items[iid][role_field]) <= set(instance[mapping]):
                    raise ValueError(f'{iid}: unknown {mapping} role in {name}')
        used.update(ids)
    if used != set(items):
        raise ValueError('Every evaluation item must apply to at least one instance')
    for item in rows:
        if item['category'] == 'goal_achievement':
            ids = item.get('validity_item_ids')
            if not string_list(ids) or any(
                    iid not in items or items[iid]['category'] != 'validity' for iid in ids):
                raise ValueError('Goal validity_item_ids must reference validity items')
            members = {i['name'] for i in instances if item['id'] in i['evaluation_items']}
            for iid in ids:
                if not any(i['name'] in members and iid in i['evaluation_items'] for i in instances):
                    raise ValueError('Referenced validity item has no applicable instance')
        elif 'validity_item_ids' in item:
            raise ValueError('Only goal-achievement items declare validity_item_ids')
    records = task.get('tool_records', {})
    if (not isinstance(records, dict)
            or any(not nonempty_string(key) or not relative_path(path)
                   or not Path(path).parts or re.match(r'^[A-Za-z]:', path)
                   for key, path in records.items())):
        raise ValueError('tool_records must map dependency IDs to safe relative paths')
    for item, participants in result_scopes(task):
        for field, mapping in (('deliverable_roles', 'deliverables'), ('input_roles', 'inputs'), ('reference_roles', 'references')):
            available = {role for instance in participants for role in instance[mapping]}
            if not set(item[field]) <= available:
                raise ValueError(f'{item["id"]}: required role absent from assessment scope')
    execution = task.get('execution')
    if 'execution' in task:
        if not isinstance(execution, dict) or set(execution) != {'read_only'}:
            raise ValueError('execution requires a read_only object')
        access = execution['read_only']
        if (not isinstance(access, dict) or set(access) != {'inputs', 'resources'}
                or not string_list(access['inputs']) or not string_list(access['resources'])):
            raise ValueError('execution.read_only requires inputs and resources lists')
        if any(not relative_path(p) or not Path(p).is_relative_to(Path('evaluation/assets'))
               for p in access['inputs']):
            raise ValueError('Evaluation input exceptions must be under evaluation/assets')
        if any(not image_resource_path(path) for path in access['resources']):
            raise ValueError('Execution resources must be specific absolute image paths')
        references = [Path(path) for instance in instances for path in instance['references'].values()
                      if Path(path).is_relative_to(Path('evaluation/assets'))]
        if any(paths_intersect(Path(path), reference)
               for path in access['inputs'] for reference in references):
            raise ValueError('Execution input grants overlap protected reference mappings')
    return task


def validate_rubric_score(score, value):
    return (finite_number(value) and score['min'] <= value <= score['max']
            and value in {float(anchor) for anchor in score['anchors']})


def rubric_scorers(item):
    return {component['scorer'] for component in item['components']}


def load_rubrics(task):
    """Validate rubric definitions without importing task-specific code."""
    defined = {i['id']: i for i in task['evaluation_items']}
    declared = {key for key, i in defined.items() if i['method'] == 'rubric'}
    if not declared:
        return {}
    document = read_json(PACKAGE_DIR / 'evaluation/rubric.json')
    if (not isinstance(document, dict) or type(document.get('schema_version')) is not int
            or document['schema_version'] != 1):
        raise ValueError('Invalid rubric schema')
    rows = document.get('items')
    if not isinstance(rows, list):
        raise ValueError('rubric items must be a list')
    items = {}
    for row in rows:
        if (not isinstance(row, dict)
                or set(row) != {'id', 'basis', 'components', 'aggregation'}
                or row.get('id') not in declared
                or row['id'] in items
                or not nonempty_string(row.get('basis'))
):
            raise ValueError('Invalid rubric item or resource roles')
        components = row.get('components')
        if not isinstance(components, list) or not components:
            raise ValueError('Rubric requires scored components')
        component_ids = set()
        for component in components:
            if (not isinstance(component, dict)
                    or set(component) != {'id', 'scorer', 'criterion', 'score', 'weight'}
                    or not nonempty_string(component['id']) or component['id'] in component_ids
                    or not nonempty_string(component['criterion'])
                    or component['scorer'] not in ('rule', 'llm')
                    or not finite_number(component['weight']) or component['weight'] <= 0):
                raise ValueError('Invalid rubric component')
            component_ids.add(component['id'])
            validate_scale(component['score'])
        aggregation = row.get('aggregation')
        if (not isinstance(aggregation, dict)
                or set(aggregation) - {'target_range'} != {'method', 'description'}
                or aggregation['method'] not in {'sum', 'normalized_weighted', 'custom'}
                or not nonempty_string(aggregation['description'])):
            raise ValueError('Invalid rubric aggregation')
        if 'target_range' in aggregation:
            target = aggregation['target_range']
            if (not isinstance(target, dict) or set(target) != {'min', 'max'}
                    or not finite_number(target['min']) or not finite_number(target['max'])
                    or target['min'] >= target['max']):
                raise ValueError('Invalid rubric target_range')
        if aggregation['method'] == 'sum' and any(c['weight'] != 1 for c in components):
            raise ValueError('Sum rubric uses component points, not additional weights')
        items[row['id']] = {**defined[row['id']], **row}
    if set(items) != declared:
        raise ValueError('Rubric inventory differs from task.json')
    return items


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, EVAL_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def validate_scale(score):
    if (not isinstance(score, dict) or set(score) != {'min', 'max', 'anchors'}
            or not finite_number(score.get('min'))
            or not finite_number(score.get('max')) or score['min'] >= score['max']):
        raise ValueError('Invalid numerical score scale')
    mapping = score.get('anchors')
    if not isinstance(mapping, dict) or not mapping or any(not nonempty_string(v) for v in mapping.values()):
        raise ValueError('Rubric needs score anchors')
    values = [float(k) for k in mapping]
    if (len(set(values)) != len(values) or any(not math.isfinite(v) or not score['min'] <= v <= score['max'] for v in values)
            or score['min'] not in values or score['max'] not in values):
        raise ValueError('Anchors must cover both bounds')


class EvaluationInterrupted(BaseException):
    """External termination: unwind cleanup without starting further items."""


class SubmissionError(Exception):
    """Confirmed submitted-content or submitted-program failure."""
    def __init__(self, *args, public_message=None):
        super().__init__(*args)
        self.public_message = public_message


class ExecutionError(RuntimeError):
    """Execution infrastructure or an unclassified program failure."""
    def __init__(self, *args, public_message=None):
        super().__init__(*args)
        self.public_message = public_message


class EvaluationError(RuntimeError):
    """Required evaluation information is missing, invalid or unavailable."""
    def __init__(self, *args, public_message=None):
        super().__init__(*args)
        self.public_message = public_message


def exception_record(exc, public_message=None):
    record = {'code': type(exc).__name__, 'message': str(exc) or repr(exc),
              'traceback': ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))}
    for key in ('returncode', 'stdout', 'stderr'):
        value = getattr(exc, key, None)
        if value is not None:
            record[key] = value.decode(errors='replace') if isinstance(value, bytes) else value
    if public_message is not None:
        record['public_message'] = public_message
    return record


def public_scoring_message(exc: BaseException, submission_errors: tuple = ()) -> str:
    """Use an explicitly reviewed message; never fall back to raw diagnostics."""
    message = getattr(exc, 'public_message', None)
    if nonempty_string(message):
        return message
    if isinstance(exc, submission_errors):
        return 'Submission does not meet the task requirements.'
    return 'Evaluation could not be completed.'


def public_format_message(exc=None, submission_errors=()):
    """Use explicit public explanations, not unreviewed raw diagnostics."""
    message = getattr(exc, 'public_message', None)
    if nonempty_string(message):
        return message
    if exc is not None and isinstance(exc, submission_errors):
        return 'Submission does not meet the required format.'
    return 'Format checking could not be completed.'


def initialize_format_checks(report, instances):
    for instance in instances:
        report['format_checks'][instance['name']] = {
            role: {'status': 'evaluation_failed', 'error': {
                'code': 'InterruptedEvaluation', 'message': 'Format checking has not completed',
                'public_message': 'Format checking has not completed.'}}
            for role in instance['deliverables']}


def check_formats(instances, output, module, report, submission_errors):
    """Use the same public submission checks in both evaluator modes."""
    for instance in instances:
        checks = report['format_checks'][instance['name']]
        for role, relative in instance['deliverables'].items():
            try:
                path = submission_path(output, instance['name'], relative)
                if not path.exists():
                    raise SubmissionError(f'Missing deliverable: {instance["name"]}/{role} ({path})',
                                          public_message=f'Missing required artifact: {instance["name"]}/{relative}.')
                public_instance = {'name': instance['name'],
                                   'deliverables': dict(instance['deliverables'])}
                # Hook/module diagnostics are private, including in format-only runs.
                with redirect_stdout(sys.stderr):
                    if module.validate_format(public_instance, role, path) is not None:
                        raise ValueError('validate_format must return None')
                checks[role] = {'status': 'completed', 'error': None}
            except (Exception, SystemExit) as exc:
                checks[role] = {
                    'status': 'submission_failed' if isinstance(exc, submission_errors) else 'evaluation_failed',
                    'error': exception_record(exc, public_format_message(exc, submission_errors))}


def format_only_report(args):
    """Record format checks privately, without scoring definitions or resources."""
    report = {'format_checks': {}, 'evaluation_results': [], 'error': None}
    destination, safe_destination = None, False
    try:
        destination = args.result_file.resolve() if args.result_file is not None else None
        output = args.submission_dir.resolve()
        if destination is not None and (destination == output or destination.is_relative_to(output)
                or destination == PACKAGE_DIR or destination.is_relative_to(PACKAGE_DIR)):
            raise ValueError('Format result path must be outside the task package and submission directory')
        safe_destination = destination is not None
        task = load_task(format_only=True)
        initialize_format_checks(report, task['instances'])
        with redirect_stdout(sys.stderr):
            module = load_module('task_metrics', 'metrics.py')
        if not callable(getattr(module, 'validate_format', None)):
            raise ValueError('Missing metrics hook: validate_format')
        submission_errors = (SubmissionError, module.SubmissionError)
        check_formats(task['instances'], output, module, report, submission_errors)
        code = result_exit_code(report, {})
    except (Exception, SystemExit, EvaluationInterrupted) as exc:
        report['error'] = exception_record(exc, public_format_message())
        code = 2
    if safe_destination:
        try:
            atomic_report(destination, report)
        except (Exception, SystemExit, EvaluationInterrupted) as exc:
            report['error'] = exception_record(exc, public_format_message())
            code = 2
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return code


def instance_groups(task, item):
    members = [i['name'] for i in task['instances'] if item['id'] in i['evaluation_items']]
    if item['scope'] == 'instance':
        if 'instance_groups' in item:
            raise ValueError('Per-instance items must omit instance_groups')
        return [[name] for name in members]
    groups = item.get('instance_groups')
    if (not isinstance(groups, list) or not groups
            or any(not string_list(group, True) for group in groups)
            or len({tuple(sorted(group)) for group in groups}) != len(groups)
            or {name for group in groups for name in group} != set(members)):
        raise ValueError('Joint instance_groups must cover exactly the referring instances without duplicate groups')
    return groups


def result_scopes(task):
    instances = {i['name']: i for i in task['instances']}
    for item in task['evaluation_items']:
        for names in instance_groups(task, item):
            yield item, [instances[name] for name in names]


def goal_accepted(item, instances, rows):
    members = {i['name'] for i in instances}
    prerequisites = [row for row in rows if row['item_id'] in item['validity_item_ids']
                     and members.intersection(row['instances'])]
    if any(row['status'] not in {'completed', 'submission_failed'} for row in prerequisites):
        raise EvaluationError('Required validity judgment is incomplete',
                              public_message='A required assessment could not be completed.')
    return all(row['status'] == 'completed' and bool(row['value']) for row in prerequisites)


def validate_value(item, value):
    spec = item['result']
    if not (finite_number(value) if spec['type'] == 'number' else type(value) is bool):
        raise ValueError('Invalid final result type or non-finite score')
    if item['category'] == 'validity' and spec['type'] == 'number' and value not in (0, 1):
        raise ValueError('Numerical validity must be 0 or 1')
    if any(key in spec and (value < spec[key] if key == 'min' else value > spec[key]) for key in ('min', 'max')):
        if item['method'] == 'program' and item['category'] == 'goal_achievement':
            raise SubmissionError(f'Program result {value!r} outside declared theoretical range',
                                  public_message='The submitted result is outside the metric\'s permitted range.')
        raise ValueError('Result outside declared range')
    return value


def check_private_directory(path):
    """Reject replaceable private-output paths without opening their permissions."""
    for directory in (path, *path.parents):
        try:
            info = directory.lstat()
        except FileNotFoundError:
            continue
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.geteuid()}
                or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)):
            raise ExecutionError('Private output directory has an untrusted or replaceable path')


def atomic_report(path, report):
    if path is None:
        return
    encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    check_private_directory(path.parent)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    check_private_directory(path.parent)
    fd, name = tempfile.mkstemp(prefix='.score-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(encoded)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def result_exit_code(report, definitions):
    rows = report['evaluation_results']
    if report['error'] or any(r['status'] != 'completed' and r['status'] != 'submission_failed' for r in rows):
        return 2
    checks = [c for roles in report['format_checks'].values() for c in roles.values()]
    if any(c['status'] == 'evaluation_failed' for c in checks):
        return 2
    if (any(c['status'] == 'submission_failed' for c in checks)
            or any(r['status'] == 'submission_failed' or r.get('accepted') is False or
                   (definitions[r['item_id']]['category'] == 'validity' and not r['value']) for r in rows)):
        return 1
    return 0


class Context(dict):
    """Private per-run services, with lazy shared computations and artifact checks."""
    def cached(self, key, compute):
        # Publish one Future before computing; unrelated keys do not share a lock.
        with self['_cache_lock']:
            entry = self['_cache'].get(key)
            owner = entry is None
            if owner:
                future = Future()
                self['_cache'][key] = (threading.get_ident(), future)
            else:
                creator, future = entry
                if creator == threading.get_ident() and not future.done():
                    raise ExecutionError('Recursive computation for the same cache key')
        if owner:
            try:
                future.set_result(compute())
            except BaseException as exc:
                future.set_exception(exc)
        return future.result()

    def map(self, function, jobs) -> list:
        """Call function(job, child_context); return values in input order.

        Independent jobs use private work paths and candidate identities. All
        jobs finish before the first input-order exception is re-raised. Nested
        maps are rejected; submitted programs must use the child context.
        """
        if self.get('_in_map'):
            raise ExecutionError('Nested context.map calls are not supported')
        jobs = list(jobs)
        if not jobs:
            return []
        runner = self['_runner']
        count = min(runner.args.max_workers, len(jobs))
        cancelled = self['_cancelled']
        batch = Path(tempfile.mkdtemp(prefix='jobs-', dir=self['work_dir']))
        results = [Future() for _ in jobs]
        pending = iter(enumerate(jobs))
        lock = threading.Lock()

        def consume(slot):
            while not cancelled.is_set():
                with lock:
                    pair = next(pending, None)
                if pair is None:
                    return
                index, job = pair
                try:
                    with ExitStack() as stack:
                        child = Context(self)
                        child['_in_map'] = True
                        child['work_dir'] = batch / str(index)
                        child['work_dir'].mkdir()
                        args = copy(runner.args)
                        # Deployment reserves the whole UID range; no UID discovery.
                        args.exec_uid += slot
                        child['_runner'] = CandidateRunner(runner.task, args, stack,
                                                           runner.protected, cancelled,
                                                           runner.protected_trees,
                                                           runner.access_state)
                        value = function(job, child)
                    results[index].set_result(value)
                except BaseException as exc:
                    if isinstance(exc, (EvaluationInterrupted, KeyboardInterrupt)):
                        cancelled.set()
                    results[index].set_exception(exc)

        if count == 1:
            consume(0)
        else:
            pool = ThreadPoolExecutor(max_workers=count)
            try:
                workers = [pool.submit(consume, slot) for slot in range(count)]
                wait(workers)
                for worker in workers:
                    worker.result()
            except BaseException:
                cancelled.set()
                raise
            finally:
                pool.shutdown(wait=True, cancel_futures=True)
        if cancelled.is_set():
            raise EvaluationInterrupted('Evaluation cancelled')
        return [result.result() for result in results]

    def artifact(self, instance_name, role):
        instance = self['_instances'][instance_name]
        record = self['_report']['format_checks'][instance_name][role]
        if record['status'] != 'completed':
            cls = SubmissionError if record['status'] == 'submission_failed' else ExecutionError
            raise cls(f"Required artifact {instance_name}/{role}: {record['error']['message']}",
                      public_message=record['error']['public_message'])
        return submission_path(self['submission_dir'], instance_name, instance['deliverables'][role])

    def resource(self, instance_name, role, reference=False):
        """Resolve an instance resource; this does not grant candidate access."""
        field = 'references' if reference else 'inputs'
        path = data_path(self['data_root'], self['_instances'][instance_name][field][role])
        if not path.exists():
            raise FileNotFoundError(f'Required {field} resource missing: {path}')
        return path

    def tool_record(self, dependency_id):
        """Resolve one declared trusted snapshot on demand, without changing it.

        Task hooks validate its task-specific format, correspondence and scope.
        The external record root is not inspected until a consumer calls here.
        """
        records = self['_tool_records']
        if dependency_id not in records:
            raise EvaluationError(f'No trusted record mapping for {dependency_id}')
        root = self['tool_records_dir']
        if root is None:
            raise EvaluationError('Trusted tool records require --tool-records-dir')
        try:
            root = root.resolve(strict=True)
            if not root.is_dir():
                raise ValueError('Trusted tool record root must be a directory')
            path = contained_path(root, records[dependency_id])
            if not path.is_file() and not path.is_dir():
                raise ValueError('Trusted tool record is missing or is not a file/directory')
        except (OSError, RuntimeError, ValueError) as exc:
            raise EvaluationError(f'Cannot read trusted record for {dependency_id}: {exc}') from exc
        return path

    def candidate_directory(self):
        self['_runner'].initialize()
        return self['_runner'].directory

    def prepare_submission_inputs(self, writer):
        """Write authorized dynamic inputs privately, then return a read-only tree.

        writer(directory) is trusted task code. Keep answers and tool records
        elsewhere; pass the returned path explicitly in the submission command.
        The tree survives mapped jobs and is removed at evaluation exit.
        """
        return self['_runner'].prepare_inputs(writer)

    def prepare_submission_work(self, writer):
        """Populate a fresh candidate-owned working copy using trusted task code."""
        return self['_runner'].prepare_inputs(writer, writable=True)

    def run_submission(self, command, *, timeout, env=None):
        """Run the hook's explicit command; input arguments are not added here."""
        return self['_runner'].run(command, timeout=timeout, env=env)


class CandidateRunner:
    """Linux UID boundary with task-owned runtime preparation.

    Static data/submission permissions are prepared before read-only mounting.
    This launcher prepares its scratch parent, grants declared image resources,
    and seals freshly generated inputs; it never chmods static package/data.
    Grants are task-wide, not per-instance. Task hooks pass explicit arguments.
    Use dedicated runtime identities and a trusted evaluation view: this is not
    a network/whole-host sandbox or protection from a concurrent root Solver.
    """
    def __init__(self, task, args, stack, protected=(), cancelled=None, protected_trees=(),
                 access_state=None):
        self.task, self.args, self.stack = task, args, stack
        self.protected = protected
        self.protected_trees = protected_trees
        self.directory = None
        self.cancelled = cancelled if cancelled is not None else threading.Event()
        self.access_state = access_state if access_state is not None else {
            'lock': threading.Lock(), 'prepared': False, 'input_stack': stack}

    @staticmethod
    def read_only_entries(root, internal_links=False):
        """Preflight one owned tree before changing any permissions."""
        entries = []
        pending = [root]
        while pending:
            path = pending.pop()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                if not internal_links or path == root or not path.resolve(strict=True).is_relative_to(root):
                    raise ExecutionError('Authorized contents contain an unsafe symbolic link')
                continue
            if (info.st_uid != 0 or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                    or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)):
                raise ExecutionError('Authorized contents must be root-owned regular files/directories without hard links')
            entries.append((path, info))
            if stat.S_ISDIR(info.st_mode):
                pending.extend(path.iterdir())
        return entries

    def seal_read_only(self, entries):
        # Seal children before publishing the root; do not grant the candidate ownership.
        for path, info in reversed(entries):
            mode = 0o550 if stat.S_ISDIR(info.st_mode) or info.st_mode & 0o111 else 0o440
            if info.st_gid != self.args.exec_gid:
                os.chown(path, 0, self.args.exec_gid)
            if stat.S_IMODE(info.st_mode) != mode:
                os.chmod(path, mode)

    def check_traversal(self, path):
        """Existing ancestors must be trusted and traversable, not chmod targets."""
        for parent in path.parents:
            if not parent.exists():
                continue
            info = parent.stat()
            if info.st_uid != 0 or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX):
                raise ExecutionError('Candidate path has an untrusted or replaceable ancestor')
            if not (info.st_mode & 0o001 or (info.st_gid == self.args.exec_gid and info.st_mode & 0o010)):
                raise ExecutionError('Prepare existing candidate ancestor traversal before execution')

    def prepare_access(self):
        with self.access_state['lock']:
            if self.access_state['prepared']:
                return
            a = self.args
            base = a.exec_work_dir.resolve()
            if any(paths_intersect(base, p) for p in (*self.protected, *self.protected_trees)):
                raise ExecutionError('Execution work path overlaps fixed contents or trusted records')
            resources = [Path(path) for path in self.task['execution']['read_only']['resources']]
            plans = []
            for path in resources:
                root = path.resolve(strict=True)
                if (root != path or paths_intersect(root, base)
                        or any(paths_intersect(root, p) for p in (*self.protected, *self.protected_trees))):
                    raise ExecutionError('Execution image resource aliases or overlaps protected contents')
                self.check_traversal(root)
                plans.append((root, self.read_only_entries(root, internal_links=True)))
            # Static mounted inputs must already be readable; do not change their metadata.
            inputs = [data_path(a.data_root, p)
                      for p in self.task['execution']['read_only']['inputs']]
            references = [data_path(a.data_root, p) for instance in self.task['instances']
                          for p in instance['references'].values()
                          if Path(p).is_relative_to(Path('evaluation/assets'))]
            if any(paths_intersect(path, reference) for path in inputs for reference in references):
                raise ExecutionError('Execution input aliases or contains protected references')
            if any(not path.exists() for path in inputs):
                raise ExecutionError('Declared execution input is missing')
            self.check_traversal(base)
            missing = []
            parent = base
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for parent in reversed(missing):
                parent.mkdir(mode=0o710 if parent == base else 0o711)
                os.chown(parent, 0, a.exec_gid)
                os.chmod(parent, 0o710 if parent == base else 0o711)
            if base.stat().st_uid != 0:
                raise ExecutionError('Execution scratch parent must be trusted-owned')
            os.chown(base, 0, a.exec_gid)
            os.chmod(base, 0o710)
            self.check_traversal(base)
            for root, entries in plans:
                self.seal_read_only(entries)
            self.access_state['prepared'] = True

    def prepare_inputs(self, writer, writable=False):
        self.initialize()
        temporary = tempfile.TemporaryDirectory(
            prefix='work-' if writable else 'inputs-',
            dir=self.directory if writable else self.args.exec_work_dir)
        if writable:
            root = Path(self.stack.enter_context(temporary))
        else:
            # Shared cached paths outlive individual jobs; register once with the run.
            with self.access_state['lock']:
                root = Path(self.access_state['input_stack'].enter_context(temporary))
        writer(root)
        if root.is_symlink() or root.resolve() != root:
            raise ExecutionError('Generated input directory was replaced')
        entries = self.read_only_entries(root)
        if writable:
            for path, info in reversed(entries):
                os.chown(path, self.args.exec_uid, self.args.exec_gid)
                os.chmod(path, 0o700 if stat.S_ISDIR(info.st_mode) or info.st_mode & 0o111 else 0o600)
        else:
            self.seal_read_only(entries)
        return root

    def initialize(self):
        if self.directory is not None:
            return
        a = self.args
        if ('execution' not in self.task or sys.platform != 'linux' or os.geteuid() != 0
                or a.exec_uid is None or a.exec_gid is None or a.exec_uid <= 0 or a.exec_gid <= 0
                or a.exec_uid >= 2**32 - 1 or a.exec_gid >= 2**32 - 1
                or a.exec_work_dir is None):
            raise ExecutionError('Candidate execution requires Linux, a privileged launcher, non-root --exec-uid/--exec-gid and prepared --exec-work-dir')
        if self.identity_processes():
            raise ExecutionError('Execution UID already has running processes; use a dedicated idle identity')
        a.exec_work_dir = a.exec_work_dir.resolve()
        self.prepare_access()
        base = a.exec_work_dir.resolve(strict=True)
        directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='candidate-', dir=base)))
        os.chown(directory, a.exec_uid, a.exec_gid)
        os.chmod(directory, 0o700)
        self.directory = directory

    def identity_processes(self):
        found = []
        for entry in Path('/proc').iterdir():
            if entry.name.isdigit():
                try:
                    ids = next(line for line in (entry / 'status').read_text().splitlines() if line.startswith('Uid:')).split()[1:]
                    if str(self.args.exec_uid) in ids:
                        state = next(line for line in (entry / 'status').read_text().splitlines() if line.startswith('State:'))
                        if 'Z' not in state.split()[1]:
                            found.append(int(entry.name))
                except (OSError, StopIteration):
                    pass
        return found

    def launch(self, command, directory, timeout, env):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('A finite positive execution timeout is required')
        if self.cancelled.is_set():
            raise EvaluationInterrupted('Evaluation cancelled')
        # A fresh isolated interpreter sets no_new_privs after UID/GID switching.
        # No Python preexec_fn may run in a multithreaded evaluator. Tool-specific
        # loader variables reach only the target, never this trusted bootstrap.
        bootstrap = """import ctypes,json,os,sys
fd = int(sys.argv[1])
try:
    os.set_inheritable(fd, False)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'Cannot set no_new_privs')
    target_env = json.loads(os.environ.pop('_TASK_EXEC_ENV'))
    os.execvpe(sys.argv[2], sys.argv[2:], target_env)
except BaseException as exc:
    os.write(fd, json.dumps({'type': type(exc).__name__, 'message': str(exc)[:512]}).encode())
    sys.exit(127)
"""
        read_fd, write_fd = os.pipe()
        try:
            proc = subprocess.Popen([sys.executable, '-I', '-S', '-c', bootstrap,
                                     str(write_fd), *command],
                                    cwd=directory, env={'_TASK_EXEC_ENV': json.dumps(env)},
                                    user=self.args.exec_uid,
                                    group=self.args.exec_gid, extra_groups=[], close_fds=True,
                                    pass_fds=(write_fd,), start_new_session=True,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True)
        except BaseException:
            os.close(read_fd)
            raise
        finally:
            os.close(write_fd)
        deadline = time.monotonic() + timeout
        try:
            while True:
                if self.cancelled.is_set():
                    raise EvaluationInterrupted('Evaluation cancelled')
                remaining = deadline - time.monotonic()
                try:
                    stdout, stderr = proc.communicate(timeout=max(0, min(remaining, 0.1)))
                    failure = os.read(read_fd, 4096)
                    if failure:
                        detail = json.loads(failure)
                        exc = ExecutionError(f"Candidate launcher {detail['type']}: {detail['message']}",
                                             public_message='The submitted program could not be started.')
                        exc.stdout, exc.stderr = stdout, stderr
                        raise exc
                    return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
                except subprocess.TimeoutExpired as exc:
                    if time.monotonic() >= deadline:
                        expired = subprocess.TimeoutExpired(command, timeout, output=exc.output,
                                                            stderr=exc.stderr)
                        expired.public_message = 'The submitted program exceeded its execution time limit.'
                        raise expired from exc
        finally:
            os.close(read_fd)
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            proc.stdout.close()
            proc.stderr.close()
            # Descendants may have created new sessions; this UID is exclusive.
            for _ in range(5):
                remaining = self.identity_processes()
                if not remaining:
                    break
                for pid in remaining:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                time.sleep(0.02)

    def run(self, command, timeout, env=None):
        if env:
            # Judge credentials must never enter a submitted program's environment.
            if any(k.startswith('JUDGE_') for k in env):
                raise ExecutionError('Private Judge configuration cannot enter candidate environment')
        self.initialize()
        clean = {'PATH': os.environ.get('PATH') or os.defpath, 'HOME': str(self.directory),
                 'TMPDIR': str(self.directory), 'XDG_CACHE_HOME': str(self.directory)}
        if env:
            clean.update(env)
        result = self.launch(command, self.directory, timeout, clean)
        if result.returncode:
            exc = ExecutionError(f'Candidate exited {result.returncode}; classify a confirmed submission defect in task hooks',
                                 public_message=f'The submitted program exited with code {result.returncode}.')
            exc.returncode, exc.stdout, exc.stderr = result.returncode, result.stdout, result.stderr
            raise exc
        return result


def score_rubric(item, instances, context, module):
    evidence = module.prepare_evidence(item, instances, context)
    if (not isinstance(evidence, list) or not evidence or any(not isinstance(e, dict)
            or set(e) != {'id', 'source', 'content'} or any(not nonempty_string(e[k]) for k in e) for e in evidence)
            or len({e['id'] for e in evidence}) != len(evidence)):
        raise ValueError('Evidence needs unique IDs, source locators and actual content')
    judgments = []
    for component in item['components']:
        scorer = component['scorer']
        selected = {**item, 'component': component}
        row = (module.score_rule(selected, instances, context, evidence) if scorer == 'rule'
               else load_module('task_judge', 'judge.py').score_item(selected, evidence,
                     context['work_dir'] / f"judge-{len(judgments)}.json"))
        if (not isinstance(row, dict) or row.get('item_id') != item['id']
                or row.get('component_id') != component['id']
                or not validate_rubric_score(component['score'], row.get('raw_score'))
                or not nonempty_string(row.get('reason')) or not string_list(row.get('evidence'), True)
                or not set(row['evidence']) <= {e['id'] for e in evidence}):
            raise ValueError('Invalid or unsupported rubric component judgment')
        judgments.append(row)
        context['_rubric_details']['components'].append(
            {key: row[key] for key in ('component_id', 'raw_score', 'reason')})
        (context['work_dir'] / 'rubric.json').write_text(json.dumps(
            {'item_id': item['id'], 'evidence': evidence, 'components': judgments, 'raw_score': None},
            ensure_ascii=False, indent=2, allow_nan=False))
    aggregation = item['aggregation']
    method = aggregation['method']
    if method == 'sum':
        value = sum(row['raw_score'] for row in judgments)
    elif method == 'normalized_weighted':
        weight = sum(c['weight'] for c in item['components'])
        value = sum(c['weight'] * ((r['raw_score'] - c['score']['min']) /
                    (c['score']['max'] - c['score']['min'])) for c, r in zip(item['components'], judgments)) / weight
    else:
        value = module.aggregate(item, judgments)
    target = aggregation.get('target_range')
    if target is not None and method != 'custom':
        lo, hi = 0, 1
        if method == 'sum':
            lo = sum(c['score']['min'] for c in item['components'])
            hi = sum(c['score']['max'] for c in item['components'])
        fraction = (value - lo) / (hi - lo)
        value = (1 - fraction) * target['min'] + fraction * target['max']
    value = validate_value(item, value)
    if target is not None and not target['min'] <= value <= target['max']:
        raise ValueError('Rubric result outside target_range')
    details = {'item_id': item['id'], 'evidence': evidence, 'components': judgments, 'raw_score': value}
    (context['work_dir'] / 'rubric.json').write_text(json.dumps(details, ensure_ascii=False, indent=2, allow_nan=False))
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--format-only', action='store_true',
                        help='Check only the public submission format; no scoring or resource access')
    parser.add_argument('--submission-dir', type=Path, required=True)
    parser.add_argument('--result-file', type=Path,
                        help='Optional private JSON report file; the report is always printed to stdout')
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--exec-work-dir', type=Path)
    parser.add_argument('--exec-uid', type=int)
    parser.add_argument('--exec-gid', type=int)
    parser.add_argument('--data-root', type=Path)
    parser.add_argument('--tool-records-dir', type=Path,
                        help='Private trusted snapshot root; inspected only when a hook consumes records')
    parser.add_argument('--max-workers', type=int,
                        help='Positive concurrent context.map job limit; unused without map work')
    args = parser.parse_args()
    if not args.format_only:
        missing = [name for name in ('work_dir', 'exec_work_dir', 'exec_uid', 'exec_gid',
                                    'data_root', 'tool_records_dir', 'max_workers')
                   if getattr(args, name) is None]
        if missing:
            parser.error('normal scoring requires ' + ', '.join('--' + name.replace('_', '-') for name in missing))
    if args.max_workers is not None and args.max_workers < 1:
        parser.error('--max-workers must be a positive integer')
    cancelled = threading.Event()
    def interrupted(signum, frame):
        cancelled.set()
        raise EvaluationInterrupted(f'Evaluation interrupted by signal {signum}')
    signal.signal(signal.SIGINT, interrupted)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, interrupted)
    if args.format_only:
        return format_only_report(args)
    report = {'format_checks': {}, 'evaluation_results': [], 'error': None}
    destination = args.result_file.resolve() if args.result_file is not None else None
    safe_destination = False
    try:
        if destination is not None:
            check_private_directory(destination.parent)
            safe_destination = not destination.exists()
        output = args.submission_dir.resolve()
        if destination is not None and (destination == output or destination.is_relative_to(output)):
            safe_destination = False
            raise ValueError('Result path must be outside the submission directory')
        task = load_task()
        rubrics = load_rubrics(task)
        args.data_root = args.data_root.resolve()
        args.work_dir = args.work_dir.resolve()
        check_private_directory(args.work_dir)
        # Protect the supplied snapshot tree even if no item reads it.
        # Non-strict path resolution checks aliases without requiring its existence.
        args.tool_records_dir = args.tool_records_dir.resolve()
        protected_trees = (args.tool_records_dir,)
        if destination is not None and paths_intersect(destination, args.tool_records_dir):
            safe_destination = False
            raise ValueError('Result path overlaps trusted tool records')
        public_paths = [(args.data_root / 'input').resolve(), (PACKAGE_DIR / 'input').resolve()]
        public_paths += [data_path(args.data_root, p) for instance in task['instances']
                         for p in instance['solver_data'].values()]
        if any(paths_intersect(args.tool_records_dir, path) for path in [output, *public_paths]):
            safe_destination = False
            raise ValueError('Trusted tool records overlap submissions or public task data')
        if any(paths_intersect(path.resolve(), args.tool_records_dir)
               for path in (args.work_dir, args.exec_work_dir)):
            raise ValueError('Runtime work path overlaps trusted tool records')
        if (paths_intersect(args.work_dir.resolve(), args.exec_work_dir.resolve())
                or (destination is not None and any(paths_intersect(destination, path.resolve())
                       for path in (args.work_dir, args.exec_work_dir)))):
            safe_destination = False
            raise ValueError('Result, private work and candidate work paths must not overlap')
        # Locations are free; reject collisions with actual inputs, code or artifacts.
        protected = [PACKAGE_DIR / 'task.json', PACKAGE_DIR / 'metadata.json']
        protected += [EVAL_DIR / name for name in ('evaluator.py', 'metrics.py')]
        if rubrics:
            protected += [EVAL_DIR / name for name in ('rubric.json', 'rubric.py')]
        if any('llm' in rubric_scorers(row) for row in rubrics.values()):
            protected.append(EVAL_DIR / 'judge.py')
        protected += [data_path(args.data_root, p) for i in task['instances'] for f in ('solver_data', 'inputs', 'references') for p in i[f].values()]
        protected += [data_path(args.data_root, p) for p in task.get('execution', {}).get('read_only', {}).get('inputs', [])]
        image_resources = [Path(p).resolve() for p in task.get('execution', {}).get('read_only', {}).get('resources', [])]
        # Collision checks must not turn one malformed submission into a global
        # initialization failure. Validate containment per artifact below.
        for instance in task['instances']:
            for relative in instance['deliverables'].values():
                path = output / instance['name'] / relative
                protected.append(path)
                try:
                    protected.append(path.resolve())
                except (OSError, RuntimeError):
                    pass  # The affected format check records the resolution error.
        if destination is not None and any(paths_intersect(destination, p) for p in protected + image_resources):
            safe_destination = False
            raise ValueError('Result path would overwrite a task resource or submitted artifact')
        safe_destination = destination is not None
        if any(paths_intersect(args.work_dir.resolve(), p) for p in protected + image_resources):
            raise ValueError('Runtime work path would modify a task resource or submitted artifact')
        initialize_format_checks(report, task['instances'])
        definitions = {i['id']: i for i in task['evaluation_items']}
        scopes = sorted(result_scopes(task), key=lambda pair: pair[0]['category'] != 'validity')
        report['evaluation_results'] = [dict(item_id=item['id'], instances=[i['name'] for i in instances],
             status='evaluation_failed', value=None, error={'code': 'InterruptedEvaluation', 'message': 'Evaluation has not completed',
             'public_message': 'Evaluation has not completed.'},
             **({'accepted': None} if item['category'] == 'goal_achievement' else {}),
             **({'rubric_details': {'components': []}} if item['method'] == 'rubric' else {}))
             for item, instances in scopes]
        with redirect_stdout(sys.stderr):
            module = load_module('task_metrics', 'metrics.py')
        hooks = ['validate_format', 'prepare']
        if any(item['method'] == 'program' for item in task['evaluation_items']):
            hooks.append('evaluate')
        for hook in hooks:
            if not callable(getattr(module, hook, None)):
                raise ValueError(f'Missing metrics hook: {hook}')
        submission_errors = (SubmissionError, module.SubmissionError)
        args.work_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        check_private_directory(args.work_dir)
        with redirect_stdout(sys.stderr), ExitStack() as stack:
            scratch = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='evaluation-', dir=args.work_dir)))
            context = Context(submission_dir=output, package_dir=PACKAGE_DIR, work_dir=scratch,
                              data_root=args.data_root,
                              _cache={}, _cache_lock=threading.Lock(),
                              tool_records_dir=args.tool_records_dir, _tool_records=task.get('tool_records', {}),
                              _cancelled=cancelled, _report=report,
                              _instances={i['name']: i for i in task['instances']})
            private_paths = (*protected_trees, args.work_dir.resolve(), output, PACKAGE_DIR)
            if destination is not None:
                private_paths += (destination,)
            context['_runner'] = CandidateRunner(task, args, stack, protected, cancelled, private_paths)
            check_formats(task['instances'], output, module, report, submission_errors)
            atomic_report(destination, report)
            rubric_module = None
            for index, (item, instances) in enumerate(scopes):
                row = report['evaluation_results'][index]
                local = Context(context)
                local['work_dir'] = scratch / str(index)
                local['work_dir'].mkdir()
                if item['method'] == 'rubric':
                    local['_rubric_details'] = row['rubric_details']
                try:
                    if item['category'] == 'goal_achievement':
                        row['accepted'] = goal_accepted(item, instances, report['evaluation_results'])
                        if not row['accepted']:
                            row.update(status='completed', value=None, error=None)
                            atomic_report(destination, report)
                            continue
                    for instance in instances:
                        for role in set(item['deliverable_roles']) & set(instance['deliverables']):
                            local.artifact(instance['name'], role)
                        for key, reference in (('input_roles', False), ('reference_roles', True)):
                            for role in set(item[key]) & set(instance['references' if reference else 'inputs']):
                                local.resource(instance['name'], role, reference)
                    prepared = module.prepare(item, instances, local)
                    if not isinstance(prepared, dict):
                        raise ValueError('prepare must return task-specific context fields')
                    if set(prepared) & set(local):
                        raise ValueError('prepare cannot replace evaluator context services')
                    local.update(prepared)
                    if item['method'] == 'rubric':
                        if rubric_module is None:
                            rubric_module = load_module('task_rubric', 'rubric.py')
                        value = score_rubric(rubrics[item['id']], instances, local, rubric_module)
                    else:
                        value = module.evaluate(item, instances, local)
                    row.update(status='completed', value=validate_value(item, value), error=None)
                except (Exception, SystemExit) as exc:
                    if item['category'] == 'goal_achievement':
                        row['accepted'] = False if isinstance(exc, submission_errors) else None
                    row.update(status='submission_failed' if isinstance(exc, submission_errors) else 'evaluation_failed',
                               value=None, error=exception_record(exc, public_scoring_message(exc, submission_errors)))
                atomic_report(destination, report)
        code = result_exit_code(report, definitions)
    except (Exception, SystemExit, EvaluationInterrupted) as exc:
        # Acceptance set before scoring is provisional until the item completes.
        for row in report['evaluation_results']:
            if row['status'] == 'evaluation_failed' and 'accepted' in row:
                row['accepted'] = None
        report['error'] = exception_record(exc, public_scoring_message(exc))
        code = 2
    if safe_destination:
        atomic_report(destination, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return code


if __name__ == '__main__':
    sys.exit(main())
