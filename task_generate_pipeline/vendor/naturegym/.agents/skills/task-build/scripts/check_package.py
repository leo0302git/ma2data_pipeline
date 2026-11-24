#!/usr/bin/env python3
"""Static checks for one discovery task package; no package code is executed.

Usage: python check_package.py <task_package_path> [--json]
Exit 0: all static checks pass; exit 1: failures or blocked checks.
Results cover only the stated portions of existing task-verify checks, not their
complete verdicts. Scientific correctness and runtime testing remain separate.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import math
import os
import re
import runpy
from pathlib import Path
from pathlib import PurePosixPath
import sys
import tomllib
from typing import Any

TEMPLATE = Path(__file__).resolve().parent.parent / 'templates/evaluator.py'
REQUIRED = (
    'input/README.md', 'input/data_description.md',
    'evaluation/evaluator.py', 'evaluation/metrics.py',
    'task.source.toml', 'task.json', 'metadata.json',
    'environment/Dockerfile', 'environment/packages.json',
    'environment/check_resources.sh', 'environment/check_resources.eval.sh',
)
SCOPES = {
    'C0.1': 'Required files, package identity and dependency manifests',
    'C0.2': 'Manifest, mapped resource paths and trusted-record collection contract',
    'C0.3': 'Required metadata fields and types',
    'C0.4': 'Scoring code syntax, template differences, interfaces and obvious stubs',
    'C1.2': 'Instance names in task.json and metadata.json',
    'C1.3': 'Evaluation item applicability, comparison references and rubric contracts',
    'C1.9': 'Data description required sections and nonempty bodies only',
    'C3.3': 'README required sections and nonempty bodies only',
}
DOCUMENT_SECTIONS = (
    ('input/data_description.md', 'C1.9', (
        'Directory Structure', 'Dataset Overview', 'File Formats & Schemas', 'Special Notes',
    )),
    ('input/README.md', 'C3.3', (
        'Scientific Problem', 'Task Objective', 'Instances and Inputs',
        'Evaluation', 'Deliverables', 'Submission Interface',
    )),
)


def markdown_sections(text: str) -> dict[str, list[str]]:
    """Collect level-two sections, ignoring headings inside fenced code blocks."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    fence: str | None = None
    for line in text.splitlines():
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence is not None:
            if (marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence)
                    and not marker[2].strip()):
                fence = None
            elif current is not None:
                sections[current].append(line)
            continue
        if marker:
            fence = marker[1]
            continue
        heading = re.match(r'^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$', line)
        if heading:
            level = len(heading[1])
            if level <= 2:
                current = None
                if level == 2:
                    title = re.sub(r'^\d+(?:\.\d+)*[.)]?\s+', '', heading[2])
                    current = ' '.join(title.casefold().split())
                    sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return sections


def reject_constant(value: str) -> None:
    raise ValueError(f'Non-finite JSON value: {value}')


def finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f'Non-finite JSON value: {value}')
    return number


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def named(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def positive_number(value: Any) -> bool:
    return (type(value) in (int, float) and value > 0
            and (type(value) is int or math.isfinite(value)))


def program_references(results: list, spec: dict, target=None) -> None:
    """Check selected source references and their strict improvement interval."""
    direction = spec.get('direction')
    if direction not in ('higher_is_better', 'lower_is_better', 'target'):
        raise ValueError('program metrics require higher_is_better, lower_is_better or target')
    if direction == 'target':
        if type(target) not in (int, float) or not math.isfinite(target):
            raise ValueError('target comparisons require a finite numerical target')
        if ('min' in spec and target < spec['min']) or ('max' in spec and target > spec['max']):
            raise ValueError('target is outside the theoretical bounds')
    elif target is not None:
        raise ValueError('target is only valid for target comparisons')
    if not 1 <= len(results) <= 3:
        raise ValueError('retain at most three selected program results')
    selected = {'sota': [], 'zero': [], 'representative': []}
    sign = 1 if direction == 'higher_is_better' else -1
    for row in results:
        if not isinstance(row, dict):
            raise ValueError('program reference must be an object')
        value = row.get('result')
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('program references must be finite numbers')
        roles = row.get('baseline_roles', [])
        if (not isinstance(roles, list) or any(role not in ('zero', 'representative') for role in roles)
                or len(set(roles)) != len(roles)):
            raise ValueError('baseline_roles must uniquely name zero and/or representative')
        if row.get('is_sota') is True:
            if roles:
                raise ValueError('SOTA cannot also be a baseline')
            selected['sota'].append(value)
        else:
            if row.get('is_sota') is not False or not roles:
                raise ValueError('retain only the selected SOTA and labelled baselines')
            for role in roles:
                selected[role].append(value)
        if ('min' in spec and value < spec['min']) or ('max' in spec and value > spec['max']):
            raise ValueError('program reference is outside its theoretical bounds')
    if len(selected['sota']) != 1 or len(selected['zero']) != 1 or len(selected['representative']) > 1:
        raise ValueError('require one SOTA, one zero baseline and at most one representative')
    sota, zero = selected['sota'][0], selected['zero'][0]
    if direction == 'target':
        # Exact binary-float distances preserve ordering even across overflow.
        from fractions import Fraction
        distances = [abs(Fraction(row['result']) - Fraction(target)) for row in results]
        sota_distance = abs(Fraction(sota) - Fraction(target))
        if sota_distance >= abs(Fraction(zero) - Fraction(target)):
            raise ValueError('SOTA must be strictly closer to target than the zero baseline')
        if any(distance < sota_distance for distance in distances):
            raise ValueError('a retained baseline is closer to target than the declared SOTA')
        return
    if sign * sota <= sign * zero:
        raise ValueError('SOTA must strictly improve on the zero baseline')
    if any(sign * row['result'] > sign * sota for row in results):
        raise ValueError('a retained baseline improves on the declared SOTA')


def runtime_upper_bound(value: Any) -> int | float | None:
    """Metadata hours are a positive number or a finite, bounded range."""
    if positive_number(value):
        return value
    if isinstance(value, str):
        match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)\s*[-–]\s*([0-9]+(?:\.[0-9]+)?)',
                             value.strip())
        if match:
            lower, upper = map(float, match.groups())
            if positive_number(lower) and positive_number(upper) and lower <= upper:
                return upper
    return None


def accepts_positional_call(function: ast.FunctionDef, count: int) -> bool:
    args = function.args
    positional = len(args.posonlyargs) + len(args.args)
    required = positional - len(args.defaults)
    return (required <= count and (args.vararg is not None or count <= positional)
            and all(default is not None for default in args.kw_defaults))


def obvious_stub(function: ast.FunctionDef) -> bool:
    body = list(function.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    statements = [statement for statement in body if not (
        isinstance(statement, ast.Pass)
        or (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)
            and statement.value.value is Ellipsis))]
    if len(statements) == 1 and isinstance(statements[0], ast.Raise):
        exception = statements[0].exc
        if isinstance(exception, ast.Call):
            exception = exception.func
        if isinstance(exception, ast.Name) and exception.id == 'NotImplementedError':
            return True
    # A no-op format check can be valid; a no-op scorer cannot return scores.
    return function.name == 'evaluate' and all(
        isinstance(statement, ast.Pass)
        or (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)
            and statement.value.value is Ellipsis)
        or (isinstance(statement, ast.Return) and (statement.value is None
            or (isinstance(statement.value, ast.Constant) and statement.value.value is None)))
        for statement in body)


class Checker:
    def __init__(self, package: Path) -> None:
        self.package = package.resolve()
        self.errors: dict[str, list[str]] = {cid: [] for cid in SCOPES}
        self.blocked: dict[str, list[str]] = {cid: [] for cid in SCOPES}
        self.rubric_items: dict[str, Any] = {}
        self.rubric_scorers: set[str] = set()
        self.program_items: set[str] = set()
        self.entries: dict[str, Any] = {}
        self.task_config: dict[str, Any] | None = None

    def fail(self, cid: str, detail: str) -> None:
        self.errors[cid].append(detail)

    def local(self, relative: str) -> Path:
        path = self.package / relative
        if not path.resolve().is_relative_to(self.package):
            raise ValueError(f'Path leaves package: {relative}')
        return path

    def load(self, relative: str, cid: str) -> dict[str, Any] | None:
        try:
            value = json.loads(self.local(relative).read_text(encoding='utf-8'),
                               parse_constant=reject_constant,
                               parse_float=finite_float,
                               object_pairs_hook=unique_object)
            if not isinstance(value, dict):
                raise ValueError('Expected a JSON object')
            return value
        except (OSError, ValueError, RuntimeError) as exc:
            self.fail(cid, f'{relative}: {exc}')
            return None

    def packages(self, relative: str) -> None:
        data = self.load(relative, 'C0.1')
        if data is None:
            return
        validator = runpy.run_path(str(Path(__file__).with_name('environment_runtime_verify.py')))
        try:
            validator['validate_package_manifest'](data)
        except ValueError as exc:
            self.fail('C0.1', f'{relative}: {exc}')

    def _compose_services(self) -> set[str] | None:
        """Read YAML service names; Docker validates full Compose semantics later."""
        try:
            import yaml
        except ImportError:
            self.blocked['C0.2'].append('Requires PyYAML to read Compose service names')
            return None
        compose = self.local('environment/docker-compose.source.yaml')
        try:
            config = yaml.safe_load(compose.read_text(encoding='utf-8'))
            services = config.get('services') if isinstance(config, dict) else None
            if not isinstance(services, dict) or not all(named(name) for name in services):
                raise ValueError('Compose services must be a mapping of service names')
        except (OSError, UnicodeError, ValueError, RuntimeError, yaml.YAMLError) as exc:
            self.fail('C0.2', f'environment/docker-compose.source.yaml: {exc}')
            return None
        return set(services)

    def collection_contract(self, task: dict[str, Any]) -> None:
        """Check trusted-record paths against the package collection declaration."""
        records = task.get('tool_records', {})
        if not isinstance(records, dict) or not records:
            return
        config = self.task_config
        if config is None:
            self.blocked['C0.2'].append('Requires a readable task.source.toml for tool-record collection checks')
            return

        verifier = config.get('verifier')
        collect = verifier.get('collect') if isinstance(verifier, dict) else None
        artifacts = config.get('artifacts')
        if not isinstance(collect, list) or not collect:
            self.fail('C0.2', 'task.json.tool_records requires task.source.toml [[verifier.collect]] entries')
            return
        if not isinstance(artifacts, list) or not artifacts:
            self.fail('C0.2', 'task.json.tool_records requires task.source.toml artifacts entries')
            return

        record_paths: dict[str, str] = {}
        for dependency_id, relative in records.items():
            if (not named(dependency_id) or not isinstance(relative, str)
                    or not relative.strip() or PurePosixPath(relative).is_absolute()
                    or '..' in PurePosixPath(relative).parts):
                self.fail('C0.2', f'task.json.tool_records.{dependency_id}: unsafe relative path')
                continue
            record_paths[relative] = dependency_id

        artifact_sources: list[tuple[PurePosixPath, str]] = []
        for index, artifact in enumerate(artifacts):
            if not isinstance(artifact, dict):
                self.fail('C0.2', f'task.source.toml artifacts[{index}] must be a table')
                continue
            source = artifact.get('source')
            service = artifact.get('service')
            if (not named(source) or not source.startswith('/')
                    or '..' in PurePosixPath(source).parts):
                self.fail('C0.2', f'task.source.toml artifacts[{index}].source must be an absolute safe path')
            else:
                source = PurePosixPath(source)
                if source == PurePosixPath('/'):
                    self.fail('C0.2', f'task.source.toml artifacts[{index}].source must name a file or directory')
                elif named(service):
                    artifact_sources.append((source, service))
            if not named(service):
                self.fail('C0.2', f'task.source.toml artifacts[{index}].service must be nonempty')

        collect_services: set[str] = set()
        for index, entry in enumerate(collect):
            if not isinstance(entry, dict):
                self.fail('C0.2', f'task.source.toml verifier.collect[{index}] must be a table')
                continue
            service = entry.get('service')
            command = entry.get('command')
            timeout = entry.get('timeout_sec')
            if not named(service) or not named(command):
                self.fail('C0.2', f'task.source.toml verifier.collect[{index}] needs service and command')
            elif type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
                self.fail('C0.2', f'task.source.toml verifier.collect[{index}].timeout_sec must be positive')
            else:
                collect_services.add(service)

        record_services: set[str] = set()
        for relative in record_paths:
            parts = PurePosixPath(relative).parts
            # Prefer the most specific exported path; a collected directory may
            # cover descendants. Phase 3 verifies the actual snapshot contents.
            matches = [(count, service) for source, service in artifact_sources
                       for count in range(1, len(parts) + 1)
                       if source.parts[-count:] == parts[:count]]
            exported = [(count, service) for count, service in matches
                        if service in collect_services]
            if not matches:
                self.fail('C0.2', f'task.json.tool_records path is not declared in task.source.toml artifacts: {relative}')
                continue
            if not exported:
                self.fail('C0.2', f'task.json.tool_records path has no collection command for its artifact service: {relative}')
                continue
            specificity = max(count for count, _ in exported)
            selected = {service for count, service in exported if count == specificity}
            if len(selected) != 1:
                self.fail('C0.2', f'task.json.tool_records path has ambiguous collection services: {relative}: {sorted(selected)}')
                continue
            record_services.update(selected)

        sidecar_services = record_services - {'main', 'evaluator'}
        if not sidecar_services:
            return
        services = self._compose_services()
        if services is None:
            return
        for service in sidecar_services:
            if service not in services:
                self.fail('C0.2', f'task.source.toml service is absent from docker-compose.source.yaml: {service}')
            if not self.local(f'environment/sidecar/{service}').is_dir():
                self.fail('C0.2', f'task.source.toml service has no sidecar build context: {service}')

    def structure(self) -> None:
        for relative in REQUIRED:
            try:
                if not self.local(relative).is_file():
                    raise ValueError('Required file is missing or not a regular file')
            except (OSError, ValueError, RuntimeError) as exc:
                self.fail('C0.1', f'{relative}: {exc}')
        try:
            with self.local('task.source.toml').open('rb') as handle:
                config = tomllib.load(handle)
            self.task_config = config
            identity = config.get('task')
            if config.get('schema_version') != '1.4':
                raise ValueError('schema_version must be "1.4"')
            if (not isinstance(identity, dict) or not named(identity.get('name'))
                    or not re.fullmatch(r'naturebench/[A-Za-z0-9][A-Za-z0-9._-]*', identity['name'])
                    or '..' in identity['name'] or not named(identity.get('description'))):
                raise ValueError('task needs a naturebench/<package-name> name and nonempty description')
        except (OSError, ValueError, RuntimeError) as exc:
            self.fail('C0.1', f'task.source.toml: {exc}')
        self.packages('environment/packages.json')
        try:
            separate = self.local('environment/Dockerfile.eval').exists()
            manifest = self.local('environment/packages.eval.json').exists()
            if separate != manifest:
                self.fail('C0.1', 'Dockerfile.eval and packages.eval.json must be provided together')
            if separate:
                if not self.local('environment/Dockerfile.eval').is_file():
                    self.fail('C0.1', 'Dockerfile.eval must be a regular file')
                self.packages('environment/packages.eval.json')
            validator = runpy.run_path(str(Path(__file__).with_name('environment_runtime_verify.py')))
            validator['validate_environment_layout'](self.local('environment'))
            for evaluator in (False, True):
                validator['read_startup_config'](self.local('environment'), evaluator=evaluator)
            sidecars = self.local('environment/sidecar')
            if sidecars.is_dir():
                for service in sidecars.iterdir():
                    self.packages(f'environment/sidecar/{service.name}/packages.json')
        except ImportError as exc:
            self.blocked['C0.1'].append(f'Compose startup checks require PyYAML: {exc}')
        except (OSError, ValueError, RuntimeError) as exc:
            self.fail('C0.1', f'Environment files: {exc}')

    def documents(self) -> None:
        for relative, cid, required in DOCUMENT_SECTIONS:
            try:
                text = self.local(relative).read_text(encoding='utf-8')
            except UnicodeError as exc:
                self.fail(cid, f'{relative}: document must use UTF-8 ({exc})')
                continue
            except (OSError, ValueError, RuntimeError) as exc:
                self.blocked[cid].append(f'Requires readable {relative}: {exc}')
                continue
            sections = markdown_sections(text)
            template = f'task-build/templates/{Path(relative).name}'
            for title in required:
                body = sections.get(title.casefold())
                if body is None:
                    self.fail(cid, f"{relative}: missing required section '{title}'; "
                              f'follow {template}')
                elif not any(line.strip() for line in body):
                    self.fail(cid, f"{relative}: empty required section '{title}'; "
                              f'fill its task-specific content using {template}')

    def manifest(self) -> dict[str, Any] | None:
        task = self.load('task.json', 'C0.2')
        if task is None:
            return None
        try:
            # Reuse the trusted skill template's validator, never the package's code.
            # Bytecode caching is disabled so this read-only check creates no files.
            previous = sys.dont_write_bytecode
            try:
                sys.dont_write_bytecode = True
                spec = importlib.util.spec_from_file_location('_package_contract', TEMPLATE)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            finally:
                sys.dont_write_bytecode = previous
            module.PACKAGE_DIR = self.package
            module.load_task()
            self.program_items = {item['id'] for item in task['evaluation_items']
                                  if item['method'] == 'program'}
            self.rubric_items = module.load_rubrics(task)
            self.rubric_scorers = {scorer for item in self.rubric_items.values()
                                   for scorer in module.rubric_scorers(item)}
            self.collection_contract(task)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            self.fail('C0.2', f'task.json: {exc}')
            return None
        access = task.get('execution', {}).get('read_only', {'inputs': [], 'resources': []})
        for relative in access['inputs']:
            try:
                if not self.local(relative).exists():
                    raise ValueError('Declared execution input is missing')
            except (OSError, ValueError, RuntimeError) as exc:
                self.fail('C0.2', f'{relative}: {exc}')
        # load_task validates absolute image resource paths. They need not exist
        # on this host; the built environment verifies their contents and access.
        for instance in task['instances']:
            for field in ('solver_data', 'inputs', 'references'):
                for role, relative in instance[field].items():
                    try:
                        path = self.local(relative)
                        if path.is_file():
                            with path.open('rb') as handle:
                                handle.read(1)
                        elif path.is_dir():
                            with os.scandir(path):
                                pass
                        else:
                            raise ValueError('Required resource is missing or not a file/directory')
                    except (OSError, ValueError, RuntimeError) as exc:
                        self.fail('C0.2', f"{instance['name']}.{field}.{role} ({relative}): {exc}")
        return task

    def metadata(self) -> dict | None:
        data = self.load('metadata.json', 'C0.3')
        if data is None:
            return None
        if not named(data.get('task_name')):
            self.fail('C0.3', 'metadata.json: task_name must be nonempty')
        source = data.get('source')
        if (not isinstance(source, dict) or not named(source.get('paper_id'))
                or not named(source.get('title'))):
            self.fail('C0.3', 'metadata.json: invalid private source provenance')
        if not named(data.get('goal')):
            self.fail('C0.3', 'metadata.json: goal must be nonempty')
        task_types = data.get('task_types')
        if (not isinstance(task_types, list) or not task_types
                or not all(named(value) for value in task_types)
                or len(set(task_types)) != len(task_types)):
            self.fail('C0.3', 'metadata.json: task_types must be a nonempty, unique list of labels')
        tooling = data.get('tooling_metadata')
        if 'tooling_metadata' not in data or (tooling is not None and (
                not isinstance(tooling, dict) or not isinstance(tooling.get('main_tools'), list))):
            self.fail('C0.3', 'metadata.json: invalid tooling_metadata')
        elif tooling is not None:
            for tool in tooling['main_tools']:
                if (not isinstance(tool, dict) or any(not named(tool.get(k)) for k in ('name', 'purpose', 'interface'))
                        or tool.get('required_by') not in ('solver', 'evaluator', 'both')
                        or not isinstance(tool.get('interfaces'), list) or not tool['interfaces']
                        or not all(value in ('library_api', 'cli', 'service_api', 'gui')
                                   for value in tool['interfaces'])
                        or len(set(tool['interfaces'])) != len(tool['interfaces'])
                        or 'gui_interaction_required' not in tool
                        or type(tool['gui_interaction_required']) not in (bool, type(None))
                        or (tool['gui_interaction_required'] is True and 'gui' not in tool['interfaces'])
                        or (tool['gui_interaction_required'] is False
                            and not any(value != 'gui' for value in tool['interfaces']))):
                    self.fail('C0.3', 'metadata.json: invalid tool definition')
        domain = data.get('domain_metadata')
        domains = domain.get('primary_domains') if isinstance(domain, dict) else None
        if (not isinstance(domains, list) or not domains or not all(named(v) for v in domains)
                or len(set(domains)) != len(domains) or any(v.strip().lower() == 'other' for v in domains)
                or not named(domain.get('sub_domain')) or not isinstance(domain.get('domain_tags'), list)
                or not 3 <= len(domain['domain_tags']) <= 5
                or not all(named(tag) for tag in domain['domain_tags'])):
            self.fail('C0.3', 'metadata.json: invalid domain_metadata; use specific primary_domains')
        compute = data.get('compute_resource_requirements')
        for role in ('solving', 'evaluation'):
            requirements = compute.get(role) if isinstance(compute, dict) else None
            prefix = f'metadata.json: compute_resource_requirements.{role}'
            if not isinstance(requirements, dict):
                self.fail('C0.3', f'{prefix} must be a complete configuration object')
                continue
            if not named(requirements.get('notes')):
                self.fail('C0.3', f'{prefix}.notes must identify sources and estimation basis')
            cpu = requirements.get('cpu_compute')
            if (not isinstance(cpu, dict) or type(cpu.get('quantity')) is not int
                    or cpu['quantity'] <= 0 or cpu.get('unit') not in (
                        'physical_core', 'logical_processor', 'processor', 'core_unspecified')):
                self.fail('C0.3', f'{prefix}.cpu_compute requires a positive CPU quantity and valid unit')
            elif cpu['unit'] == 'physical_core':
                count = cpu['quantity']
                grade = 'low' if count <= 32 else ('medium' if count <= 128 else 'high')
                if cpu.get('severity') != grade:
                    self.fail('C0.3', f'{prefix}.cpu_compute.severity must be {grade}')
            elif cpu.get('severity') not in ('low', 'medium', 'high'):
                # Other units need a physical-core grading basis in notes,
                # checked by semantic task verification rather than this count.
                self.fail('C0.3', f'{prefix}.cpu_compute.severity must be low, medium or high')
            memory = requirements.get('memory')
            if (not isinstance(memory, dict) or not positive_number(memory.get('quantity'))
                    or memory.get('unit') != 'GiB'):
                self.fail('C0.3', f'{prefix}.memory requires a positive finite GiB capacity')
            else:
                capacity = memory['quantity']
                grade = 'low' if capacity <= 64 else ('medium' if capacity <= 256 else 'high')
                if memory.get('severity') != grade:
                    self.fail('C0.3', f'{prefix}.memory.severity must be {grade}')
            gpu = requirements.get('gpu_compute')
            if (not isinstance(gpu, dict) or type(gpu.get('count')) is not int
                    or gpu['count'] < 0):
                self.fail('C0.3', f'{prefix}.gpu_compute requires a nonnegative integer count')
            else:
                count = gpu['count']
                grade = ('none' if count == 0 else 'low' if count == 1
                         else 'medium' if count <= 8 else 'high')
                if gpu.get('severity') != grade:
                    self.fail('C0.3', f'{prefix}.gpu_compute.severity must be {grade}')
                if count == 0:
                    if 'model' in gpu or 'memory_per_device' in gpu:
                        self.fail('C0.3', f'{prefix}.gpu_compute: omit model/memory_per_device with 0 GPUs')
                else:
                    per_device = gpu.get('memory_per_device')
                    if (not named(gpu.get('model')) or not isinstance(per_device, dict)
                            or not positive_number(per_device.get('quantity'))
                            or per_device.get('unit') not in ('GB', 'GiB')):
                        self.fail('C0.3', f'{prefix}.gpu_compute requires a model and positive per-card capacity')
            runtime = requirements.get('runtime')
            upper = runtime_upper_bound(runtime.get('quantity')) if isinstance(runtime, dict) else None
            if upper is None or runtime.get('unit') != 'h' or 'scope' in runtime:
                self.fail('C0.3', f'{prefix}.runtime requires elapsed hours or a bounded range, without scope')
            else:
                grade = ('under_1h' if upper < 1 else '1_to_4h' if upper < 4
                         else '4_to_12h' if upper < 12 else '12_to_24h' if upper <= 24
                         else 'over_24h')
                if runtime.get('severity') != grade:
                    self.fail('C0.3', f'{prefix}.runtime.severity must be {grade}')
        comparisons = data.get('evaluation_comparisons')
        if 'comparison_results' in data:
            self.fail('C0.3', 'metadata.json: comparisons belong in evaluation_comparisons item groups')
        if not isinstance(comparisons, list) or not comparisons:
            self.fail('C0.3', 'metadata.json: retained goal items need evaluation_comparisons')
            return data
        for entry in comparisons:
            if (not isinstance(entry, dict) or not named(entry.get('item_id'))
                    or not isinstance(entry.get('comparison_results'), list)):
                self.fail('C0.3', 'metadata.json: invalid comparison item group')
                continue
            for group in entry['comparison_results']:
                if (not isinstance(group, dict) or group.get('scope') not in ('instance', 'joint')
                        or not isinstance(group.get('instances'), list) or not group['instances']
                        or not all(named(n) for n in group['instances'])
                        or len(set(group['instances'])) != len(group['instances'])
                        or (group['scope'] == 'instance' and len(group['instances']) != 1)
                        or not isinstance(group.get('results'), list) or not group['results']):
                    self.fail('C0.3', 'metadata.json: invalid comparison scope group')
                    continue
                numeric_results = []
                for row in group['results']:
                    if (not isinstance(row, dict) or any(not named(row.get(k)) for k in ('method', 'source'))
                            or any(k not in row for k in ('method_role', 'variant', 'is_sota', 'conditions'))
                            or (row.get('conditions') is not None and not named(row['conditions']))
                            or row.get('method_role') not in ('paper_method', 'baseline', None)
                            or (row.get('variant') is not None and not named(row['variant']))
                            or type(row.get('is_sota')) not in (bool, type(None))
                            or not isinstance(row.get('baseline_roles', []), list)
                            or any(role not in ('zero', 'representative') for role in row.get('baseline_roles', []))
                            or len(set(row.get('baseline_roles', []))) != len(row.get('baseline_roles', []))
                            or type(row.get('result')) not in (int, float, str)
                            or (isinstance(row.get('result'), str) and not named(row['result']))
                            or (type(row.get('result')) in (int, float) and not math.isfinite(row['result']))):
                        self.fail('C0.3', 'metadata.json: invalid or missing corresponding comparison result')
                    elif type(row['result']) in (int, float):
                        numeric_results.append(row)
                if numeric_results and not any(row['is_sota'] is True for row in numeric_results):
                    self.fail('C0.3', 'metadata.json: numerical comparison groups require a numerical result marked is_sota: true')
        return data

    def local_imports(self, tree: ast.Module, source: Path, evaluation: Path) -> set[Path]:
        """Find statically named local imports without treating reference data as code."""
        found: set[Path] = set()
        for node in ast.walk(tree):
            modules: list[str] = []
            base = evaluation
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = source.parent
                    for _ in range(node.level - 1):
                        base = base.parent
                    if not base.is_relative_to(evaluation):
                        continue
                prefix = node.module or ''
                modules = [prefix] if prefix else []
                modules += ['.'.join(filter(None, (prefix, alias.name)))
                            for alias in node.names if alias.name != '*']
            for module in modules:
                parts = module.split('.')
                target = base.joinpath(*parts)
                candidates = [target.with_suffix('.py'), target / '__init__.py']
                candidates += [base.joinpath(*parts[:i]) / '__init__.py'
                               for i in range(1, len(parts))]
                for candidate in candidates:
                    if candidate.is_file():
                        found.add(candidate)
        return found

    def code(self) -> None:
        try:
            if self.local('evaluation/evaluator.py').read_bytes() != TEMPLATE.read_bytes():
                self.blocked['C0.4'].append('evaluation/evaluator.py differs from the shared template; review the diff, confirmed defect and repair checks')
        except (OSError, ValueError, RuntimeError) as exc:
            self.fail('C0.4', f'evaluation/evaluator.py: {exc}')
        trees: dict[str, ast.Module] = {}
        try:
            evaluation = self.local('evaluation')
        except (OSError, ValueError, RuntimeError) as exc:
            self.fail('C0.4', f'evaluation/: {exc}')
            return
        pending = [evaluation / 'evaluator.py', evaluation / 'metrics.py']
        if self.rubric_items:
            pending.append(evaluation / 'rubric.py')
        if 'llm' in self.rubric_scorers:
            pending.append(evaluation / 'judge.py')
            try:
                if self.local('evaluation/judge.py').read_bytes() != (TEMPLATE.parent / 'judge.py').read_bytes():
                    self.blocked['C0.4'].append('evaluation/judge.py differs from the shared template; review the diff, confirmed defect and repair checks')
            except (OSError, ValueError) as exc:
                self.fail('C0.4', f'Judge template: {exc}')
        visited: set[Path] = set()
        while pending:
            source = pending.pop()
            relative = str(source.relative_to(self.package))
            try:
                path = self.local(relative)
                resolved = path.resolve()
                if resolved in visited:
                    continue
                visited.add(resolved)
                tree = ast.parse(path.read_bytes(), filename=relative)
                compile(tree, relative, 'exec')  # Syntax validation only, no execution.
                trees[relative] = tree
                pending.extend(sorted(self.local_imports(tree, source, evaluation)))
            except (OSError, ValueError, RuntimeError, SyntaxError) as exc:
                self.fail('C0.4', f'{relative}: {exc}')
        tree = trees.get('evaluation/metrics.py')
        if tree is None:
            self.fail('C0.4', 'evaluation/metrics.py cannot be inspected')
            return
        definitions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
        if not isinstance(definitions.get('SubmissionError'), ast.ClassDef):
            self.blocked['C0.4'].append('metrics.py: SubmissionError class not directly declared; inspect any imported/assigned interface')
        required_hooks = [('validate_format', 3), ('prepare', 3)]
        if self.program_items:
            required_hooks.append(('evaluate', 3))
        for name, count in required_hooks:
            function = definitions.get(name)
            if not isinstance(function, ast.FunctionDef):
                self.blocked['C0.4'].append(f'metrics.py: {name} not directly declared; inspect any imported/assigned interface')
                continue
            if not accepts_positional_call(function, count):
                self.fail('C0.4', f'metrics.py: {name} must accept {count} positional arguments without extra required arguments')
            if obvious_stub(function):
                self.fail('C0.4', f'metrics.py: {name} is an unimplemented placeholder')

        if self.rubric_items:
            tree = trees.get('evaluation/rubric.py')
            functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)} if tree else {}
            required = [('prepare_evidence', 3)]
            if 'rule' in self.rubric_scorers:
                required.append(('score_rule', 4))
            if any(i['aggregation']['method'] == 'custom' for i in self.rubric_items.values()):
                required.append(('aggregate', 2))
            for name, count in required:
                function = functions.get(name)
                if function is None:
                    self.blocked['C0.4'].append(f'rubric.py: {name} not directly declared; inspect any imported/assigned interface')
                    continue
                if not accepts_positional_call(function, count):
                    self.fail('C0.4', f'rubric.py: {name} must accept {count} positional arguments without extra required arguments')
                if obvious_stub(function):
                    self.fail('C0.4', f'rubric.py: {name} is an unimplemented placeholder')

    def evaluation_contract(self, task: dict, metadata: dict) -> None:
        instances = {i['name']: set(i['evaluation_items']) for i in task['instances']}
        goals = {i['id'] for i in task['evaluation_items'] if i['category'] == 'goal_achievement'}
        items = {i['id']: i for i in task['evaluation_items']}
        weights = metadata.get('evaluation_weights')
        if (not isinstance(weights, dict) or set(weights) != goals
                or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in weights.values())
                or not math.isclose(sum(weights.values()), 1.0)):
            self.fail('C1.3', 'evaluation_weights must cover goal items only with positive weights summing to 1')
        try:
            scope_weights = metadata.get('evaluation_scope_weights', {})
            if not isinstance(scope_weights, dict) or set(scope_weights) - goals:
                raise ValueError('must be an object naming only goal items')
            for item_id, rows in scope_weights.items():
                if not isinstance(rows, list) or not rows:
                    raise ValueError(f'{item_id}: expected a nonempty scope list')
                participants = {name for name, ids in instances.items() if item_id in ids}
                expected = ({frozenset(group) for group in items[item_id]['instance_groups']}
                            if items[item_id]['scope'] == 'joint'
                            else {frozenset([name]) for name in participants})
                seen, values = set(), []
                for row in rows:
                    if not isinstance(row, dict) or set(row) != {'instances', 'weight'}:
                        raise ValueError(f'{item_id}: each scope requires instances and weight')
                    names = row['instances']
                    if (not isinstance(names, list) or not names or not all(named(name) for name in names)
                            or len(set(names)) != len(names) or not positive_number(row['weight'])):
                        raise ValueError(f'{item_id}: invalid instances or positive finite weight')
                    key = frozenset(names)
                    if key in seen or key not in expected:
                        raise ValueError(f'{item_id}: duplicate or unexpected scope')
                    seen.add(key)
                    values.append(row['weight'])
                if seen != expected or not math.isclose(math.fsum(values), 1.0, rel_tol=0, abs_tol=1e-9):
                    raise ValueError(f'{item_id}: cover every scope once with weights summing to 1')
        except (ValueError, OverflowError) as exc:
            self.fail('C1.3', f'evaluation_scope_weights: {exc}')
        entries = metadata.get('evaluation_comparisons', [])
        if not isinstance(entries, list):
            return
        seen_items = set()
        for entry in entries:
            if not isinstance(entry, dict) or not named(entry.get('item_id')):
                continue
            item_id = entry['item_id']
            if item_id not in goals:
                self.fail('C1.3', 'Comparison item must be a retained goal-achievement item')
                continue
            if item_id in seen_items:
                self.fail('C1.3', f'{item_id}: duplicate comparison item group')
            seen_items.add(item_id)
            groups = entry.get('comparison_results')
            if not isinstance(groups, list):
                continue
            if not groups:
                if item_id not in self.rubric_items:
                    self.fail('C1.3', f'{item_id}: an item without a rubric needs reported comparisons')
                # Scientific verification still requires reported comparisons for source-defined
                # numerical metrics; other rubrics need supported standards for every instance.
                continue
            scope = items[item_id]['scope']
            participants = frozenset(name for name, ids in instances.items() if item_id in ids)
            expected = ({frozenset(group) for group in items[item_id]['instance_groups']} if scope == 'joint'
                        else {frozenset([name]) for name in participants})
            seen_groups = set()
            for group in groups:
                if (not isinstance(group, dict) or not isinstance(group.get('instances'), list)
                        or not all(named(name) for name in group['instances'])):
                    continue
                names = frozenset(group['instances'])
                if group.get('scope') != scope:
                    self.fail('C1.3', f'{item_id}: comparison scope differs from task.json')
                if names in seen_groups:
                    self.fail('C1.3', f'{item_id}: duplicate comparison scope group')
                seen_groups.add(names)
                for name in names:
                    if name not in instances:
                        self.fail('C1.2', 'Comparison references an unknown instance')
                    elif item_id not in instances[name]:
                        self.fail('C1.3', f'{name}: comparison item is not applicable')
                if names not in expected:
                    self.fail('C1.3', f'{item_id}: comparison group does not match the assessment participants')
                if 'target' in group and (items[item_id].get('method') != 'program'
                                         or items[item_id]['result'].get('direction') != 'target'):
                    self.fail('C1.3', f'{item_id}: target is only valid for program target comparisons')
                if items[item_id].get('method') == 'program':
                    try:
                        program_references(group.get('results', []), items[item_id]['result'], group.get('target'))
                    except (ValueError, TypeError, KeyError, OverflowError) as exc:
                        self.fail('C1.3', f'{item_id} {sorted(names)}: {exc}')
            if seen_groups != expected:
                self.fail('C1.3', f'{item_id}: comparisons must cover every assessed instance or declared joint group')
        if seen_items != goals:
            self.fail('C1.3', 'evaluation_comparisons must cover all retained goal items')

    def run(self) -> dict[str, Any]:
        self.structure()
        self.documents()
        task = self.manifest()
        metadata = self.metadata()
        self.code()
        if task is not None and metadata is not None:
            self.evaluation_contract(task, metadata)
        else:
            for cid in ('C1.2', 'C1.3'):
                self.blocked[cid].append('Requires valid manifest and metadata')
        checks = [{'id': cid, 'scope': scope,
                   'status': 'failed' if self.errors[cid] else ('not_verified' if self.blocked[cid] else 'passed'),
                   'errors': self.errors[cid], 'unverified': self.blocked[cid]}
                  for cid, scope in SCOPES.items()]
        return {'package': str(self.package), 'passed': all(c['status'] == 'passed' for c in checks),
                'checks': checks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task_package_path', type=Path)
    parser.add_argument('--json', action='store_true', help='print the static check report as JSON')
    args = parser.parse_args()
    report = Checker(args.task_package_path).run()
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
    else:
        print(report['package'])
        for check in report['checks']:
            print(f"{check['id']} {check['status']}: {check['scope']}")
            for detail in check['errors'] + check['unverified']:
                print(f'  {detail}')
        print('Static checks passed' if report['passed'] else 'Static checks failed or incomplete')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
