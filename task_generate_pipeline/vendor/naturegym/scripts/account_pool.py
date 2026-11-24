"""Local Codex account leases. Only pre-call capacity shortages are schedulable."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
import fcntl
import http.client
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import tempfile
import threading
import time
from collections.abc import Callable, Mapping

if __package__:
    from .account_quota import (fetch_provider_quota, normalize_quota, quota_block, quota_permits,
                               quota_score, quota_group_score, quota_weight)
else:
    from account_quota import (fetch_provider_quota, normalize_quota, quota_block, quota_permits,
                              quota_score, quota_group_score, quota_weight)


RESOURCE_ERROR = 76
LEASE_ENV = "NATUREGYM_ACCOUNT_LEASE"
_current: ContextVar[AccountLease | None] = ContextVar("codex_account", default=None)


class ResourceWait(Exception):
    def __init__(self, reason: str, retry_at: float):
        super().__init__(reason)
        self.reason = reason
        self.retry_at = retry_at


def validate_account_options(config: str | None, agent: str, model: str | None, effort: str | None) -> None:
    if config and (agent != "codex" or not model or not effort):
        raise ValueError("--account-pool requires --agent codex, --model and --effort")


class AccountLease:
    def __init__(self, pool: AccountPool, account: dict, handle):
        self.pool = pool
        self.account = account
        self.handle = handle
        self.acquired_at = time.time()

    def __enter__(self) -> AccountLease:
        self.token = _current.set(self)
        return self

    def __exit__(self, *_: object) -> None:
        _current.reset(self.token)
        # Close, do not LOCK_UN: inherited descriptors protect still-running children.
        self.handle.close()

    def assignment(self) -> dict:
        return dict(config=str(self.pool.path), account_id=self.account["id"],
                    codex_home=self.account["codex_home"], codex_binary=self.account["codex_binary"],
                    fd=self.handle.fileno(), acquired_at=self.acquired_at)


def account_assignment(env: Mapping[str, str] | None = None) -> dict | None:
    lease = _current.get()
    if lease is not None:
        return lease.assignment()
    raw = (os.environ if env is None else env).get(LEASE_ENV)
    return json.loads(raw) if raw else None


def account_environment(env: Mapping[str, str]) -> dict[str, str]:
    result = dict(env)
    assignment = account_assignment(env)
    if assignment:
        result["CODEX_HOME"] = assignment["codex_home"]
        result[LEASE_ENV] = json.dumps(assignment)
        for name in ("CODEX_API_KEY", "CODEX_ACCESS_TOKEN"):
            result.pop(name, None)
    return result


def account_fds(env: Mapping[str, str]) -> tuple[int, ...]:
    assignment = account_assignment(env)
    return (int(assignment["fd"]),) if assignment else ()


class AccountPool:
    def __init__(self, config: str | Path):
        self.path = Path(config).expanduser().resolve()
        self.state_path = self.path.with_name(self.path.stem + ".state.json")
        self.slots = self.path.with_name(self.path.stem + ".slots")
        self._config()

    def _config(self) -> dict:
        config = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("Account pool config must be an object")
        accounts = config.get("accounts")
        if not isinstance(accounts, list) or not accounts:
            raise ValueError("Account pool requires a nonempty accounts list")
        ids, homes = set(), set()
        for item in accounts:
            if not isinstance(item, dict):
                raise ValueError("Each account must be an object")
            name = item.get("id", "")
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) or name in ids:
                raise ValueError("Account IDs must be unique simple names")
            ids.add(name)
            if not isinstance(item.get("codex_home"), str) or not item["codex_home"]:
                raise ValueError("Each account requires a CODEX_HOME path")
            home = Path(item["codex_home"]).expanduser()
            home = (self.path.parent / home).resolve() if not home.is_absolute() else home.resolve()
            if not home.is_dir() or str(home) in homes:
                raise ValueError("Each account requires a distinct existing CODEX_HOME directory")
            homes.add(str(home))
            item["codex_home"] = str(home)
            item.setdefault("codex_binary", "codex")
            if not isinstance(item["codex_binary"], str) or not shutil.which(item["codex_binary"]):
                raise ValueError(f"Codex executable unavailable for account {name}")
            capacity = item.setdefault("capacity", None)
            if capacity is not None and (isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1):
                raise ValueError("Account capacity must be a positive integer or omitted for unlimited concurrency")
            weight = item.get("quota_weight")
            if weight is not None and (isinstance(weight, bool) or not isinstance(weight, (int, float))
                                       or not math.isfinite(weight) or weight <= 0):
                raise ValueError("Account quota_weight must be positive and finite or omitted for automatic weighting")
            if not isinstance(item.get("enabled", True), bool):
                raise ValueError("Account enabled must be boolean")
        ceiling = config.setdefault('adaptive_ceiling', 64)
        if isinstance(ceiling, bool) or not isinstance(ceiling, int) or ceiling < 1:
            raise ValueError('adaptive_ceiling must be a positive integer')
        for key, default in (("retry_seconds", 5), ("cooling_seconds", 60), ("quota_retry_seconds", 300),
                             ("quota_refresh_seconds", 60), ("quota_timeout_seconds", 8)):
            value = config.setdefault(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{key} must be positive and finite")
        return config

    @contextmanager
    def _state(self):
        with self.state_path.with_suffix(".json.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
            if not isinstance(state, dict) or not isinstance(state.get("accounts", {}), dict):
                raise ValueError("Invalid account pool state")
            if state.get("host", socket.gethostname()) != socket.gethostname():
                raise ValueError("Account pool state belongs to another host; use a host-local pool")
            state.setdefault("host", socket.gethostname())
            state.setdefault("accounts", {})
            yield state

    def _save(self, state: dict) -> None:
        fd, filename = tempfile.mkstemp(prefix=self.state_path.name + ".", dir=self.path.parent)
        temporary = Path(filename)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.state_path)
        finally:
            temporary.unlink(missing_ok=True)

    def refresh_quota(self, *, force: bool = False, fetch: Callable[..., dict] | None = None,
                      stop: threading.Event | None = None) -> dict[str, str]:
        """Query outside the lease-state lock; one refresher per shared pool."""
        fetch = fetch or fetch_provider_quota
        results = {}
        with self.path.with_name(self.path.stem + '.quota.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return results
            config = self._config()
            for account in config['accounts']:
                if stop is not None and stop.is_set():
                    break
                if not account.get('enabled', True):
                    continue
                name, home = account['id'], account['codex_home']
                with self._state() as state:
                    status = state['accounts'].get(name, {})
                    checked = status.get('quota_checked_at', 0)
                    requested = status.get('quota_refresh_requested_at', 0)
                    due = (status.get('quota_home') != home or requested > checked
                           or status.get('quota_blocked') and time.time() >= status.get('quota_retry_at', float('inf'))
                           or time.time() >= checked + config['quota_refresh_seconds'])
                if not force and not due:
                    continue
                started = time.time()
                try:
                    quota = normalize_quota(fetch(home, timeout=config['quota_timeout_seconds']), observed_at=started)
                    error = None
                except (OSError, ValueError, http.client.HTTPException) as failure:
                    quota = None
                    # Error bodies/exception strings may contain credentials. Never persist them.
                    error = {'type': type(failure).__name__}
                    if isinstance(getattr(failure, 'code', None), int):
                        error['http_status'] = failure.code
                if stop is not None and stop.is_set():
                    break
                with self._state() as state:
                    status = state['accounts'].setdefault(name, {})
                    if status.get('quota_home') != home:
                        status.pop('quota', None)
                    status.update(quota_home=home, quota_checked_at=started)
                    # A native quota denial remains blocked until a newer report
                    # confirms recovery. Timers only schedule another query.
                    if 'quota_failed_at' in status and 'quota_blocked' not in status:
                        previous = status.get('quota', {})
                        status['quota_blocked'] = not (
                            previous.get('observed_at', 0) >= status['quota_failed_at'] and quota_permits(previous))
                    if quota is not None:
                        status['quota'] = quota
                        status.pop('quota_error', None)
                        blocked, reset = quota_block(quota, time.time())
                        if started >= status.get('quota_failed_at', 0):
                            if quota_permits(quota):
                                status.pop('quota_retry_at', None)
                                status['quota_blocked'] = False
                                if status.get('last_error') == 'quota' and 'quota_failed_at' not in status:
                                    status.pop('retry_at', None)  # Old fixed-cooldown records.
                        if blocked and reset is not None and reset > time.time():
                            status['quota_retry_at'] = reset
                    else:
                        status['quota_error'] = error
                    if status.get('quota_blocked') and status.get('quota_retry_at', 0) <= time.time():
                        status['quota_retry_at'] = time.time() + config['quota_retry_seconds']
                    self._save(state)
                results[name] = 'provider' if quota is not None else 'unavailable'
        return results

    @contextmanager
    def monitor(self):
        stop = threading.Event()
        def refresh() -> None:
            while not stop.is_set():
                try:
                    self.refresh_quota(stop=stop)
                except (OSError, ValueError):
                    # Invalid config/state is reported by acquire; do not print private data here.
                    pass
                stop.wait(.5)
        worker = threading.Thread(target=refresh, daemon=True, name='codex-quota-refresh')
        worker.start()
        try:
            yield
        finally:
            stop.set()
            worker.join(timeout=1)

    def acquire(self) -> AccountLease:
        with self._state() as state, ExitStack() as cleanup:
            config = self._config()
            now = time.time()
            self.slots.mkdir(mode=0o700, exist_ok=True)
            candidates = []
            deadlines = []
            enabled = [a for a in config["accounts"] if a.get("enabled", True)]
            if enabled and all(state["accounts"].get(a["id"], {}).get("auth_failed") for a in enabled):
                raise ResourceWait("All enabled accounts require login; re-login and clear their failure records",
                                   now + config['retry_seconds'])
            for account in enabled:
                status = state["accounts"].get(account["id"], {})
                if status.get("auth_failed"):
                    continue
                if status.get('quota_home') != account['codex_home'] or 'quota_checked_at' not in status:
                    deadlines.append(now + config['retry_seconds'])
                    continue
                quota = status.get('quota', {})
                if quota.get('plan_type') == 'free':
                    deadlines.append(now + config['quota_refresh_seconds'])
                    continue
                pending_quota = status.get('quota_blocked', 'quota_failed_at' in status and not (
                    quota.get('observed_at', 0) >= status.get('quota_failed_at', 0) and quota_permits(quota)))
                if pending_quota:
                    deadlines.append(now + config['quota_refresh_seconds'])
                    continue
                blocked, reset = quota_block(quota, now)
                cooldown = max(status.get('retry_at', 0), status.get('quota_retry_at', 0))
                if blocked or cooldown > now:
                    deadlines.append(max(reset or now + config['quota_refresh_seconds'], cooldown)
                                     if blocked else cooldown)
                    continue
                occupied = 0
                available = None
                pattern = re.compile(re.escape(account['id']) + r"\.([0-9]+)\.lock$")
                previous_slots = [int(m[1]) + 1 for path in self.slots.glob(account['id'] + '.*.lock')
                                  if (m := pattern.fullmatch(path.name))]
                capacity = account['capacity']
                limit = status.get('adaptive_limit', capacity)
                if capacity is not None and limit is not None:
                    limit = min(capacity, limit)
                scan_count = max([limit or 0, *previous_slots])
                # A lowered capacity must still count older slots held by active calls.
                for number in range(scan_count):
                    handle = (self.slots / f"{account['id']}.{number}.lock").open("a")
                    cleanup.callback(handle.close)
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        occupied += 1
                        handle.close()
                    else:
                        if available is None and (limit is None or number < limit):
                            available = handle
                        else:
                            handle.close()
                if available is None and limit is None:
                    # Extend an unlimited pool only when every existing slot is
                    # occupied; sequential calls must reuse a single lock file.
                    handle = (self.slots / f"{account['id']}.{scan_count}.lock").open('a')
                    cleanup.callback(handle.close)
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        handle.close()
                    else:
                        available = handle
                if available is not None and (limit is None or occupied < limit):
                    score = quota_score(quota, now, weight=account.get("quota_weight"))
                    candidates.append((-score, occupied, account["id"], account, available))
                elif available is not None:
                    available.close()
                    deadlines.append(now + config['retry_seconds'])
                else:
                    deadlines.append(now + config['retry_seconds'])
            if not candidates:
                # Poll changed quota/config at least once per refresh interval; no Agent is called.
                retry_at = min([now + config['quota_refresh_seconds'], *deadlines])
                raise ResourceWait("No Codex account capacity available", retry_at)
            finalists = []
            groups = {}
            for entry in candidates:
                quota = state["accounts"][entry[2]].get("quota", {})
                group_score = quota_group_score(quota, now)
                if group_score is None:
                    finalists.append(entry)
                    continue
                plan = quota.get("plan_type")
                # Overrides may represent different allowances; unknown plans are not pooled.
                group = (("plan", plan, quota_weight(quota, weight=entry[3].get("quota_weight")))
                         if plan and plan != "unknown" else ("account", entry[2]))
                rank = (-group_score, *entry[:3])
                if group not in groups or rank < groups[group][0]:
                    groups[group] = (rank, entry)
            finalists.extend(entry for _, entry in groups.values())
            selected = min(finalists, key=lambda entry: entry[:3])
            for entry in candidates:
                if entry is not selected:
                    entry[4].close()
            _, _, _, account, handle = selected
            if not self.state_path.exists():
                self._save(state)
            cleanup.pop_all()
            return AccountLease(self, account, handle)

    def _occupied_slots(self, account_id: str) -> int:
        occupied = 0
        for path in self.slots.glob(account_id + '.*.lock'):
            if not re.fullmatch(re.escape(account_id) + r'\.[0-9]+\.lock', path.name):
                continue
            with path.open('a') as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    occupied += 1
        return occupied

    def record_failure(self, account_id: str, kind: str) -> None:
        with self._state() as state:
            config = self._config()
            if account_id not in {a["id"] for a in config["accounts"]}:
                return
            status = state["accounts"].setdefault(account_id, {})
            now = time.time()
            status.update(last_error=kind, time=now)
            if kind == "auth":
                status["auth_failed"] = True
            elif kind == 'quota':
                blocked, reset = quota_block(status.get('quota', {}), now)
                status.update(quota_failed_at=now, quota_refresh_requested_at=now,
                              quota_blocked=True,
                              quota_retry_at=reset if blocked and reset is not None else now + config['quota_retry_seconds'])
            else:
                status['retry_at'] = max(status.get('retry_at', 0), now + config['cooling_seconds'])
                limit = max(1, self._occupied_slots(account_id) - 1)
                status.update(adaptive_limit=min(limit, status.get('adaptive_limit', limit)),
                              successes=0, limit_changed_at=now)
            self._save(state)

    def record_success(self, account_id: str, *, acquired_at: float | None) -> None:
        if acquired_at is None:
            return
        with self._state() as state:
            config = self._config()
            account = next((a for a in config['accounts'] if a['id'] == account_id), None)
            status = state['accounts'].get(account_id, {})
            limit = status.get('adaptive_limit')
            if (account is None or limit is None or status.get('auth_failed')
                    or acquired_at < max(status.get('limit_changed_at', 0), status.get('time', 0))):
                return
            successes = status.get('successes', 0) + 1
            if successes >= limit:
                ceiling = account['capacity'] if account['capacity'] is not None else config['adaptive_ceiling']
                if limit + 1 >= ceiling:
                    status.pop('adaptive_limit', None)
                    status.pop('successes', None)
                else:
                    status.update(adaptive_limit=limit + 1, successes=0, limit_changed_at=time.time())
            else:
                status['successes'] = successes
            self._save(state)

    def enable(self, account_id: str) -> None:
        with self._state() as state:
            if account_id not in {a["id"] for a in self._config()["accounts"]}:
                raise ValueError("Unknown account ID")
            state["accounts"].pop(account_id, None)
            self._save(state)


def resource_error(log: str | Path) -> str | None:
    """Inspect only native failed-call errors; never classify tool text or business reports."""
    kind = None
    with Path(log).open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "turn.failed":
                error = event.get("error", {})
                message = str(error.get("message", "")) if isinstance(error, dict) else ""
            elif event.get("type") == "error":
                message = str(event.get("message", ""))
            else:
                continue
            kind = _resource_kind(message) or kind
    return kind


def _resource_kind(message: str) -> str | None:
    text = message.lower()
    if any(s in text for s in ("usage limit", "quota exhausted", "insufficient_quota", "exceeded your current quota", "quota has been exceeded")):
        return "quota"
    if any(s in text for s in ("unauthorized", "authentication failed", "invalid api key", "token expired", "refresh token")):
        return "auth"
    if (any(s in text for s in ("rate limit", "rate_limit", "too many requests"))
            or re.search(r"\b(?:http(?: status)?(?: code)?|status(?: code)?)[\s:]+429\b", text)):
        return "rate_limit"
    if any(s in text for s in ("model capacity", "at capacity", "overloaded")):
        return "capacity"
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clear an account's recorded auth failure/cooldown after re-login")
    parser.add_argument("config")
    parser.add_argument("account_id")
    args = parser.parse_args()
    AccountPool(args.config).enable(args.account_id)
    print(f"Enabled account {args.account_id}; config enabled=false still prevents allocation")
