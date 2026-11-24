"""Image fingerprints and explicit adoption in the paper's existing build record."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile

if __package__:
    from .batch_history import archive_outputs
    from .process_control import cleanup_images, cleanup_pending, CLEANUP_PENDING_EXIT
else:
    from batch_history import archive_outputs
    from process_control import cleanup_images, cleanup_pending, CLEANUP_PENDING_EXIT

MARKER = 'NATUREGYM_IMAGE='


def inspect_image(image: str) -> dict:
    result = subprocess.run(['docker', 'image', 'inspect', image], capture_output=True,
                            text=True, check=True, timeout=15)
    return json.loads(result.stdout)[0]


def atomic_json(path: Path, data: dict) -> None:
    handle, staging = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(handle, 'w') as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
        os.replace(staging, path)
    finally:
        if os.path.exists(staging):
            os.unlink(staging)


def read_record(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f'Expected an object in {path}')
    rows = data.get('environment_images', [])
    if not isinstance(rows, list) or any(
        not isinstance(r, dict) or r.get('role') not in {'shared', 'solver', 'evaluator', 'sidecar'}
        or not isinstance(r.get('image_id'), str) or not r['image_id']
        or not isinstance(r.get('report'), str) or not r['report']
        or r['role'] == 'sidecar' and not r.get('service') for r in rows
    ):
        raise ValueError(f'Invalid environment_images in {path}')
    pending = data.get('environment_image_cleanup', [])
    if not isinstance(pending, list) or any(
        not isinstance(row, dict) or any(not isinstance(row.get(field), str) or not row[field]
                                        for field in ('image_id', 'reason', 'report'))
        for row in pending
    ):
        raise ValueError(f'Invalid environment_image_cleanup in {path}')
    return data


def update_cleanup_record(data: dict, outcome: dict) -> None:
    if 'environment_image_cleanup' not in data:
        return
    pending = {row['image_id']: row for row in data['environment_image_cleanup']}
    for row in outcome['skipped'] + outcome['errors']:
        if row['resource'] in pending:
            pending[row['resource']]['reason'] = row.get('reason') or row['error']
    removed = set(outcome['removed'])
    data['environment_image_cleanup'] = [row for image, row in pending.items() if image not in removed]


def fingerprint(dockerfile: Path, context: Path, build_args: dict | None = None,
                platform: str | None = None) -> str | None:
    """Hash local build inputs and resolved bases; unsupported inputs disable reuse."""
    dockerfile, context = dockerfile.resolve(), context.resolve()
    digest = hashlib.sha256()
    variables = dict(build_args or {})
    stages: set[str] = set()
    files: set[Path] = {dockerfile}

    def add(value: object) -> None:
        digest.update(json.dumps(value, sort_keys=True).encode() + b'\0')

    def expand(value: str, *, strict: bool = True) -> str:
        value = re.sub(r'\$\{(\w+)\}|\$(\w+)', lambda m: str(variables.get(m[1] or m[2], m[0])), value)
        if strict and '$' in value:
            raise ValueError('Unresolved Dockerfile variable')
        return value

    def base(value: str) -> None:
        if value.lower() in stages or value == 'scratch' or value.isdigit():
            return
        info = inspect_image(value)
        add([value, info['Id'], info.get('Os'), info.get('Architecture'), info.get('Variant')])

    try:
        add({'args': variables, 'platform': platform or os.environ.get('DOCKER_DEFAULT_PLATFORM')})
        for ignore in (context / '.dockerignore', dockerfile.with_name(dockerfile.name + '.dockerignore')):
            if ignore.exists():
                files.add(ignore)
        text = re.sub(r'\\\s*\n', ' ', dockerfile.read_text())
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            parts = line.strip().split(None, 1)
            instruction, value = parts[0], parts[1] if len(parts) > 1 else ''
            instruction = instruction.upper()
            if '<<' in value:
                return None
            if '--mount=' in value and any(not re.search(r'(?:^|,)type=(?:cache|tmpfs)(?:,|$)', mount)
                                           for mount in re.findall(r'--mount=(\S+)', value)):
                return None
            if instruction == 'ARG':
                name, _, default = value.partition('=')
                if name.strip() not in variables and default:
                    variables[name.strip()] = expand(default, strict=False)
            elif instruction == 'ENV':
                tokens = shlex.split(value)
                if tokens and '=' not in tokens[0]:
                    tokens = [tokens[0] + '=' + ' '.join(tokens[1:])]
                for part in tokens:
                    key, sep, val = part.partition('=')
                    if not sep:
                        return None
                    variables[key] = expand(val, strict=False)
            elif instruction == 'FROM':
                tokens = shlex.split(expand(value))
                while tokens and tokens[0].startswith('--'):
                    add(tokens.pop(0))
                base(tokens[0])
                if len(tokens) > 2 and tokens[1].lower() == 'as':
                    stages.add(tokens[2].lower())
            elif instruction in {'COPY', 'ADD'}:
                value = expand(value).strip()
                options = []
                while value.startswith('--'):
                    option, _, value = value.partition(' ')
                    options.append(option)
                    value = value.lstrip()
                other = next((o[7:] for o in options if o.startswith('--from=')), None)
                if other is not None:
                    base(other)
                    continue
                sources = json.loads(value) if value.startswith('[') else shlex.split(value, comments=True)
                for source in sources[:-1]:
                    if '://' in source or Path(source).is_absolute() or '..' in Path(source).parts:
                        return None
                    matches = [context] if source in {'.', './'} else list(context.glob(source))
                    if not matches:
                        return None
                    for path in matches:
                        files.add(path)
                        if path.is_dir() and not path.is_symlink():
                            files.update(path.rglob('*'))
        pending = list(files)
        while pending:
            path = pending.pop()
            if not path.is_symlink():
                continue
            target = path.resolve(strict=True)
            if not target.is_relative_to(context):
                return None
            targets = {target, *target.rglob('*')} if target.is_dir() else {target}
            pending.extend(targets - files)
            files.update(targets)
        for path in sorted(files):
            if not path.resolve().is_relative_to(context):
                return None
            # Reports created by checks must not silently become part of a reusable build.
            if re.fullmatch(r'verify_result(?:\.eval)?(?:\.\d+)?\.txt', path.name):
                return None
            mode = path.lstat().st_mode
            add([str(path.relative_to(context)), stat.S_IMODE(mode), stat.S_IFMT(mode)])
            if path.is_symlink():
                add(os.readlink(path))
                continue
            if path.is_file():
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
                digest.update(b'\0')
        return digest.hexdigest()
    except (OSError, ValueError, RuntimeError, KeyError, IndexError, subprocess.SubprocessError):
        return None


def key(row: dict) -> tuple[str, str | None]:
    return row['role'], row.get('service')


def check_fingerprint(context: Path, role: str) -> str:
    environment = context.parents[1] if role == 'sidecar' else context
    paths = [environment / name for name in (
        'packages.json', 'packages.eval.json', 'check_resources.sh', 'check_resources.eval.sh',
        'docker-compose.source.yaml', 'docker-compose.eval.source.yaml')]
    paths += sorted(environment.glob('sidecar/*/packages.json'))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(environment)).encode() + b'\0')
        digest.update(path.read_bytes() if path.is_file() else b'<absent>')
        digest.update(b'\0')
    return digest.hexdigest()


def select(record: Path, role: str, service: str | None, dockerfile: Path,
           context: Path, *, paper_id: str, no_cache: bool = False,
           rebuild: bool = False,
           build_args: dict | None = None, platform: str | None = None) -> dict:
    value = fingerprint(dockerfile, context, build_args, platform)
    selected = dict(role=role, dockerfile=str(dockerfile.resolve()), context=str(context.resolve()),
                    build_fingerprint=value, build_args=build_args or {}, platform=platform,
                    check_fingerprint=check_fingerprint(context, role), image_action='built')
    if service:
        selected['service'] = service
    for row in read_record(record).get('environment_images', []):
        if key(row) != (role, service) or rebuild or no_cache or value is None or row.get('build_fingerprint') != value:
            continue
        try:
            info = inspect_image(row['image_id'])
            labels = (info.get('Config') or {}).get('Labels') or {}
            if (info['Id'] == row['image_id'] and labels.get('naturegym.paper_id') == paper_id
                    and labels.get('naturegym.temporary') == 'check'):
                selected.update(image_action='reused', image_id=row['image_id'], tag=row.get('tag'))
                break
        except subprocess.CalledProcessError as exc:
            if 'No such' not in (exc.stderr or ''):
                raise
    return selected


def finish_build(selected: dict, tag: str) -> dict:
    selected.update(tag=tag, image_id=inspect_image(tag)['Id'])
    value = fingerprint(Path(selected['dockerfile']), Path(selected['context']),
                        selected['build_args'], selected['platform'])
    if selected['build_fingerprint'] is not None and value != selected['build_fingerprint']:
        raise ValueError('Build inputs changed during construction; check the current inputs again')
    selected['build_fingerprint'] = value
    return selected


def archive_reports(record: Path, paper: Path, reports: list[Path]) -> None:
    paper = paper.resolve()
    data = read_record(record)
    archived = archive_outputs(*(p.parent.resolve() / p.name for p in reports))
    changed = False
    for row in data.get('environment_images', []) + data.get('environment_image_cleanup', []):
        source = (paper / row['report']).absolute()
        if source in archived:
            row['report'] = os.path.relpath(archived[source], paper)
            changed = True
    if changed:
        atomic_json(record, data)


def adopt(record: Path, paper: Path, report: Path, paper_id: str) -> dict:
    paper = paper.resolve()
    if not record.is_file():
        raise ValueError('Adoption requires the existing filtering/build record')
    text = report.read_text()
    metadata = next((line[len(MARKER):] for line in text.splitlines() if line.startswith(MARKER)), None)
    status = next((line.partition(':')[2].strip() for line in text.splitlines() if line.startswith('status:')), None)
    if metadata is None or status not in {'all passed', 'selected roles passed'}:
        raise ValueError('Adoption requires a complete successful environment report')
    result = json.loads(metadata)
    role = result['role']
    required = {'solver', 'evaluator'} if role == 'shared' else {result.get('service', role)}
    if not required <= set(result.get('checked_roles', [])):
        raise ValueError('The report does not cover all roles of this image')
    info = inspect_image(result['image_id'])
    labels = (info.get('Config') or {}).get('Labels') or {}
    if (info['Id'] != result['image_id'] or labels.get('naturegym.paper_id') != paper_id
            or labels.get('naturegym.temporary') != 'check' or not labels.get('naturegym.run_id')):
        raise ValueError('Image ownership does not match this paper')
    current = fingerprint(Path(result['dockerfile']), Path(result['context']),
                          result.get('build_args'), result.get('platform'))
    if current is None:
        raise ValueError('Build inputs cannot be fingerprinted; image not adopted')
    if current != result['build_fingerprint']:
        raise ValueError('Build inputs changed after verification')
    if result.get('check_fingerprint') != check_fingerprint(Path(result['context']), role):
        raise ValueError('Check configuration changed after verification')
    data = read_record(record)
    row = {k: result[k] for k in ('role', 'image_id', 'tag', 'build_fingerprint')}
    if role == 'sidecar':
        row['service'] = result['service']
    row['report'] = os.path.relpath(report.resolve(), paper)
    rows = data.get('environment_images', [])
    if role in {'solver', 'evaluator'}:
        # A former shared image can continue serving the other, unchanged role.
        for existing in rows:
            if existing['role'] == 'shared':
                existing['role'] = 'solver' if role == 'evaluator' else 'evaluator'
    replaced = lambda r: key(r) == key(row) or role == 'shared' and r['role'] in {'solver', 'evaluator'}
    previous = [r for r in rows if replaced(r)]
    data['environment_images'] = [r for r in rows if not replaced(r)] + [row]
    retained = {r['image_id'] for r in data['environment_images']}
    old = list(dict.fromkeys(r['image_id'] for r in previous if r['image_id'] not in retained))
    if old or 'environment_image_cleanup' in data:
        pending = {r['image_id']: r for r in data.get('environment_image_cleanup', [])}
        for image in old:
            pending.setdefault(image, dict(image_id=image, reason='Replacement cleanup not completed',
                                          report=row['report']))
            pending[image]['report'] = row['report']
        data['environment_image_cleanup'] = [r for image, r in pending.items() if image not in retained]
    atomic_json(record, data)
    cleanup = cleanup_images(labels['naturegym.run_id'], paper_id, image_ids=[], registered_ids=old) if old else None
    if cleanup:
        data = read_record(record)
        update_cleanup_record(data, cleanup)
        atomic_json(record, data)
        if cleanup_pending(cleanup):
            text = re.sub(r'^cleanup_status:.*$', 'cleanup_status: pending', text, flags=re.M)
            report.write_text(text)
        with report.open('a') as stream:
            stream.write('\n===== ADOPTION CLEANUP =====\n' + json.dumps(cleanup) + '\n')
    return dict(adopted=row, cleanup=cleanup)


def final_cleanup(record: Path, run_id: str, paper_id: str) -> dict | None:
    data = read_record(record)
    ids = list(dict.fromkeys(r['image_id'] for r in
                            data.get('environment_images', []) + data.get('environment_image_cleanup', [])))
    outcome = cleanup_images(run_id, paper_id, registered_ids=ids)
    if outcome:
        # Re-read to preserve current stage records; never clear unrelated fields.
        data = read_record(record)
        before = json.dumps(data, sort_keys=True)
        if 'environment_images' in data:
            data['environment_images'] = [r for r in data['environment_images']
                                          if r['image_id'] not in outcome['removed']]
        update_cleanup_record(data, outcome)
        if json.dumps(data, sort_keys=True) != before:
            atomic_json(record, data)
    return outcome


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('select', 'finish', 'archive', 'adopt'))
    parser.add_argument('--record', type=Path, required=True)
    parser.add_argument('--paper-dir', type=Path, required=True)
    parser.add_argument('--role')
    parser.add_argument('--service')
    parser.add_argument('--dockerfile', type=Path)
    parser.add_argument('--context', type=Path)
    parser.add_argument('--metadata', type=Path)
    parser.add_argument('--tag')
    parser.add_argument('--no-cache', action='store_true')
    parser.add_argument('--rebuild', action='store_true')
    parser.add_argument('reports', type=Path, nargs='*')
    args = parser.parse_intermixed_args()
    paper_id = os.environ.get('NATUREGYM_PAPER_ID') or args.paper_dir.resolve().name
    if args.action == 'select':
        print(json.dumps(select(args.record, args.role, args.service, args.dockerfile, args.context,
                                paper_id=paper_id, no_cache=args.no_cache, rebuild=args.rebuild)))
    elif args.action == 'finish':
        atomic_json(args.metadata, finish_build(json.loads(args.metadata.read_text()), args.tag))
    elif args.action == 'archive':
        archive_reports(args.record, args.paper_dir, args.reports)
    elif args.action == 'adopt':
        if len(args.reports) != 1:
            parser.error('adopt requires one report')
        result = adopt(args.record, args.paper_dir, args.reports[0], paper_id)
        print(json.dumps(result))
        raise SystemExit(CLEANUP_PENDING_EXIT if cleanup_pending(result['cleanup']) else 0)


if __name__ == '__main__':
    main()
