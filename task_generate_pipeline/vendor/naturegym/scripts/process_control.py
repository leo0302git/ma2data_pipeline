"""Bounded subprocesses; Linux stage supervisors retain orphan ownership."""
from __future__ import annotations

from collections.abc import Sequence
import json
import os
import signal
import shutil
import sys
import subprocess
import threading
import time

CLEANUP_TIMEOUT = 120
CLEANUP_PENDING_EXIT = 3

if __package__:
    from .account_pool import account_fds
else:
    from account_pool import account_fds


def supervise(command):
    """Linux subreaper: detached grandchildren reparent here, never to PID 1.

    A supervisor per stage keeps ownership separate between concurrent papers.
    It cleans adopted descendants before returning the driver's exit status.
    """
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER, Linux >= 3.4
        raise OSError(ctypes.get_errno(), 'Cannot enable stage subreaper')
    stopping = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopping.set())
    process = subprocess.Popen(command, pass_fds=account_fds(os.environ))
    tracked = {}
    try:
        while process.poll() is None and not stopping.is_set():
            stopping.wait(.1)
        record_descendants(os.getpid(), tracked)
        if tracked:
            signal_descendants(tracked, signal.SIGTERM)
            time.sleep(.3)
            record_descendants(os.getpid(), tracked)
            signal_descendants(tracked, signal.SIGKILL)
        code = process.wait()
        # Reap adopted children after their direct parents were terminated.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    record_descendants(os.getpid(), tracked)
                    signal_descendants(tracked, signal.SIGKILL)
                    time.sleep(.05)
            except ChildProcessError:
                break
        return 130 if stopping.is_set() else code
    finally:
        if process.poll() is None:
            record_descendants(os.getpid(), tracked)
            signal_descendants(tracked, signal.SIGKILL)
            process.wait()


def process_snapshot():
    """PID -> (parent PID, birth identity); no command lines or credentials."""
    result = {}
    if os.path.isdir('/proc'):
        for name in os.listdir('/proc'):
            if not name.isdigit():
                continue
            try:
                with open(f'/proc/{name}/stat') as handle:
                    fields = handle.read().rsplit(')', 1)[1].split()
                result[int(name)] = (int(fields[1]), fields[19])
            except (OSError, IndexError, ValueError):
                pass
    else:
        output = subprocess.check_output(['ps', '-axo', 'pid=,ppid=,lstart='], text=True)
        for line in output.splitlines():
            fields = line.strip().split(None, 2)
            if len(fields) == 3:
                result[int(fields[0])] = (int(fields[1]), fields[2])
    return result


def record_descendants(pid, tracked):
    snapshot = process_snapshot()
    roots = {pid} | {p for p, identity in tracked.items()
                       if p in snapshot and snapshot[p][1] == identity}
    while True:
        children = {p for p, (parent, _) in snapshot.items() if parent in roots}
        if children <= roots:
            break
        roots |= children
    for child in roots - {pid}:
        if child in snapshot:
            tracked[child] = snapshot[child][1]


def signal_descendants(tracked, sig):
    snapshot = process_snapshot()
    for pid, identity in tracked.items():
        if pid in snapshot and snapshot[pid][1] == identity:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass


class StageTimeout(TimeoutError):
    pass


class ProcessControl:
    def __init__(self):
        self.stop = threading.Event()

    @staticmethod
    def terminate(process, descendants=None):
        # A driver and its agent belong to the same new session. Kill descendants
        # even if the direct child exits before its descendants do.
        descendants = descendants if descendants is not None else {}
        record_descendants(process.pid, descendants)
        signal_descendants(descendants, signal.SIGTERM)
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        # Keep the process group alive until the grace period ends; descendants
        # may ignore TERM even after their parent exits.
        time.sleep(.3)
        record_descendants(process.pid, descendants)
        signal_descendants(descendants, signal.SIGKILL)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Darwin can return EPERM for a group that disappeared after TERM.
            if process.poll() is None or sys.platform != 'darwin':
                raise
        finally:
            process.wait()

    def run(self, command, *, timeout=0, **kwargs):
        if self.stop.is_set():
            raise InterruptedError("Batch cancelled")
        if sys.platform == 'linux':
            command = [sys.executable, os.path.abspath(__file__), '--supervise', *command]
        kwargs['pass_fds'] = tuple(set(kwargs.get('pass_fds', ())) | set(account_fds(kwargs.get('env', os.environ))))
        process = subprocess.Popen(command, start_new_session=True, **kwargs)
        started = time.monotonic()
        descendants = {}
        checked = 0
        try:
            while True:
                if time.monotonic() - checked >= .2:
                    record_descendants(process.pid, descendants)
                    checked = time.monotonic()
                if self.stop.is_set():
                    raise InterruptedError("Batch cancelled")
                if timeout and time.monotonic() - started >= timeout:
                    raise StageTimeout(f"Stage exceeded {timeout:g} seconds")
                code = process.poll()
                if code is not None:
                    return code
                self.stop.wait(.1)
        except BaseException:
            self.terminate(process, descendants)
            raise


def _cleanup_run(args: list[str], deadline: float, timeout: float = 15) -> subprocess.CompletedProcess[str]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(args, CLEANUP_TIMEOUT)
    return subprocess.run(args, capture_output=True, text=True, check=True,
                          timeout=min(timeout, remaining))


def cleanup_pending(outcome: dict | None) -> bool:
    return bool(outcome and (outcome['errors'] or any(
        row.get('reason') in {'endpoints_present', 'container_reference', 'active_project'}
        for row in outcome['skipped'])))


def cleanup_objects(objects: list[tuple[str, str]], *, deadline: float | None = None) -> dict:
    """Remove registered invocation objects, confirming uncertain removals."""
    deadline = deadline if deadline is not None else time.monotonic() + CLEANUP_TIMEOUT
    outcome = dict(removed=[], skipped=[], errors=[])
    for kind, name in objects:
        if kind not in {'container', 'image'}:
            raise ValueError(f'Unsupported cleanup object: {kind}')
        try:
            try:
                _cleanup_run(['docker', kind, 'inspect', name], deadline)
            except subprocess.CalledProcessError as exc:
                if 'No such' in (exc.stderr or ''):
                    continue
                raise
            args = ['docker', 'rm', '-f', name] if kind == 'container' else ['docker', 'rmi', '--no-prune', name]
            try:
                _cleanup_run(args, deadline, timeout=max(.1, min(60, deadline - time.monotonic() - 5)))
            except subprocess.TimeoutExpired:
                try:
                    _cleanup_run(['docker', kind, 'inspect', name], deadline, timeout=5)
                except subprocess.CalledProcessError as exc:
                    if 'No such' not in (exc.stderr or ''):
                        raise
                else:
                    raise
            outcome['removed'].append(name)
        except (OSError, subprocess.SubprocessError) as exc:
            outcome['errors'].append(dict(resource=name, error=str(exc),
                                           detail=getattr(exc, 'stderr', '') or ''))
            if isinstance(exc, subprocess.TimeoutExpired):
                break
    return outcome


def project_is_active(project: str) -> bool:
    """Only an actual command-line reference counts; do not read credentials."""
    if os.path.isdir('/proc'):
        for pid in os.listdir('/proc'):
            if not pid.isdigit() or int(pid) == os.getpid():
                continue
            try:
                with open(f'/proc/{pid}/cmdline', 'rb') as handle:
                    args = handle.read().decode(errors='replace').split('\0')
                if any(arg == project or arg.startswith(project + '_') for arg in args):
                    return True
            except (OSError, ProcessLookupError):
                pass
        return False
    output = subprocess.check_output(['ps', '-axo', 'pid=,command='], text=True, timeout=5)
    return any(project in line.split()[1:] for line in output.splitlines()
               if line.split() and line.split()[0] != str(os.getpid()))


def cleanup_networks(run_id: str | None, paper_id: str | None = None, *,
                     project: str | None = None, deadline: float | None = None) -> dict | None:
    """Remove only labelled, unused check networks after their invocation ends."""
    if not run_id and not project:
        return None
    deadline = deadline if deadline is not None else time.monotonic() + CLEANUP_TIMEOUT
    labels = {'naturegym.temporary': 'check'}
    if run_id:
        labels['naturegym.run_id'] = run_id
    if paper_id:
        labels['naturegym.paper_id'] = paper_id
    if project:
        labels['com.docker.compose.project'] = project
    outcome = dict(removed=[], skipped=[], errors=[])
    resource = project or paper_id or run_id
    try:
        filters = [arg for key, value in labels.items() for arg in ('--filter', f'label={key}={value}')]
        ids = _cleanup_run(['docker', 'network', 'ls', '-q', '--no-trunc', *filters], deadline).stdout.split()
        for resource in dict.fromkeys(ids):
            try:
                details = json.loads(_cleanup_run(['docker', 'network', 'inspect', resource], deadline).stdout)[0]
            except subprocess.CalledProcessError as exc:
                if 'No such' in (exc.stderr or '') or 'not found' in (exc.stderr or '').lower():
                    continue
                raise
            actual = details.get('Labels') or {}
            owner = actual.get('com.docker.compose.project', '')
            if (details.get('Id') != resource or any(actual.get(k) != v for k, v in labels.items())
                    or not owner
                    or (not project and not actual.get('naturegym.paper_id'))):
                outcome['skipped'].append(dict(resource=resource, reason='ownership_mismatch'))
                continue
            if details.get('Containers'):
                reason = 'endpoints_present'
            elif _cleanup_run(['docker', 'ps', '-aq', '--filter', f'network={resource}'], deadline).stdout.strip():
                reason = 'container_reference'
            elif project_is_active(owner):
                reason = 'active_project'
            else:
                reason = None
            if reason:
                outcome['skipped'].append(dict(resource=resource, name=details['Name'], reason=reason))
                continue
            try:
                _cleanup_run(['docker', 'network', 'rm', resource], deadline,
                             timeout=max(.1, min(60, deadline - time.monotonic() - 5)))
            except subprocess.TimeoutExpired:
                # The daemon may have completed after the client timed out. Never repeat rm here.
                try:
                    _cleanup_run(['docker', 'network', 'inspect', resource], deadline, timeout=5)
                except subprocess.CalledProcessError as exc:
                    if 'No such' not in (exc.stderr or '') and 'not found' not in (exc.stderr or '').lower():
                        raise
                else:
                    raise
            outcome['removed'].append(resource)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        outcome['errors'].append(dict(resource=resource, error=str(exc),
                                      detail=getattr(exc, 'stderr', '') or ''))
    return outcome


def cleanup_containers(run_id: str | None, paper_id: str | None = None, *,
                       deadline: float | None = None) -> dict | None:
    """Remove only containers tagged by this batch's runtime wrapper."""
    if not run_id or not os.path.isfile('/usr/bin/docker'):
        return
    command = ['/usr/bin/docker', 'ps', '-aq', '--filter', f'label=naturegym.run_id={run_id}']
    if paper_id:
        command += ['--filter', f'label=naturegym.paper_id={paper_id}']
    try:
        deadline = deadline if deadline is not None else time.monotonic() + CLEANUP_TIMEOUT
        result = _cleanup_run(command, deadline)
        ids = result.stdout.split()
        if ids:
            return cleanup_objects([('container', name) for name in ids], deadline=deadline)
    except (OSError, subprocess.SubprocessError) as exc:
        return dict(removed=[], skipped=[], errors=[dict(resource=paper_id or run_id,
                    error=str(exc), detail=getattr(exc, 'stderr', '') or '')])


def cleanup_runtime_resources(run_id: str | None, paper_id: str | None = None) -> dict:
    """One bounded fallback; never removes images needed by an unfinished paper."""
    if not run_id or not os.path.isfile('/usr/bin/docker'):
        return {}
    deadline = time.monotonic() + CLEANUP_TIMEOUT
    return {'container_cleanup': cleanup_containers(run_id, paper_id, deadline=deadline),
            'network_cleanup': cleanup_networks(run_id, paper_id, deadline=deadline)}


def cleanup_images(run_id: str | None, paper_id: str, *,
                   image_ids: list[str] | None = None,
                   registered_ids: Sequence[str] = (),
                   deadline: float | None = None) -> dict | None:
    """Clean current-run images or explicit adopted IDs; never sweep past runs."""
    if not run_id or not paper_id or not shutil.which('docker'):
        return None
    labels = {'naturegym.run_id': run_id, 'naturegym.paper_id': paper_id,
              'naturegym.temporary': 'check'}
    deadline = deadline if deadline is not None else time.monotonic() + CLEANUP_TIMEOUT
    outcome = dict(removed=[], skipped=[], errors=[])
    resource = paper_id

    def docker(*args: str, timeout: float = 15) -> str:
        return _cleanup_run(['docker', *args], deadline, timeout).stdout

    try:
        filters = [arg for key, value in labels.items()
                   for arg in ('--filter', f'label={key}={value}')]
        images = (docker('image', 'ls', '-q', '--no-trunc', *filters).split()
                  if image_ids is None else image_ids)
        for image in dict.fromkeys([*images, *registered_ids]):
            resource = image
            try:
                details = json.loads(docker('image', 'inspect', image))[0]
            except subprocess.CalledProcessError as exc:
                if 'No such' in (exc.stderr or ''):
                    outcome['removed'].append(image)  # Confirmed absent; registry can drop it.
                    continue
                raise
            actual_labels = (details.get('Config') or {}).get('Labels') or {}
            expected = {k: v for k, v in labels.items()
                        if k != 'naturegym.run_id' or image not in registered_ids}
            if (details.get('Id') != image or not actual_labels.get('naturegym.run_id')
                    or any(actual_labels.get(k) != v for k, v in expected.items())):
                outcome['skipped'].append(dict(resource=image, reason='ownership_mismatch'))
                continue
            tags = set(details.get('RepoTags') or [])
            # Extra aliases may belong to a cache or another paper, even if named like a check.
            if len(tags) > 1 or details.get('RepoDigests'):
                outcome['skipped'].append(dict(resource=image, reason='shared_tags_or_digest'))
                continue
            # Include stopped containers and descendants, not just running users.
            if docker('ps', '-aq', '--filter', f'ancestor={image}').strip():
                outcome['skipped'].append(dict(resource=image, reason='container_reference'))
                continue
            try:
                docker('image', 'rm', '--no-prune', image,
                       timeout=max(.1, min(60, deadline - time.monotonic() - 5)))
            except subprocess.TimeoutExpired:
                try:
                    docker('image', 'inspect', image, timeout=5)
                except subprocess.CalledProcessError as exc:
                    if 'No such' not in (exc.stderr or ''):
                        raise
                else:
                    raise
            outcome['removed'].append(image)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        # A timeout has an uncertain outcome; stop here rather than retrying removal.
        outcome['errors'].append(dict(resource=resource, error=str(exc),
                                     detail=getattr(exc, 'stderr', '') or ''))
        print(f'Image cleanup failed for run {run_id}, paper {paper_id}: {type(exc).__name__}', flush=True)
    return outcome


if __name__ == '__main__':
    if len(sys.argv) < 3 or sys.argv[1] != '--supervise':
        raise SystemExit('Internal stage supervisor: --supervise COMMAND ...')
    raise SystemExit(supervise(sys.argv[2:]))
