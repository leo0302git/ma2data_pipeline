"""Read Codex OAuth quota reports; persist only the scheduling fields."""
from __future__ import annotations

import json
import math
from pathlib import Path
import urllib.request


USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
# Published relative plan allowances; the usage endpoint does not report these weights.
PLAN_QUOTA_WEIGHTS = {"plus": 1.0, "prolite": 5.0, "pro": 10.0, "promax": 25.0}


def fetch_provider_quota(codex_home: str, *, timeout: float = 8) -> dict:
    auth = json.loads((Path(codex_home) / "auth.json").read_text(encoding="utf-8"))
    tokens = auth.get("tokens") if isinstance(auth, dict) else None
    if not isinstance(tokens, dict) or not isinstance(tokens.get("access_token"), str) or not tokens["access_token"]:
        raise ValueError("Codex login has no OAuth access token for quota queries")
    headers = {"Authorization": "Bearer " + tokens["access_token"]}
    if isinstance(tokens.get("account_id"), str) and tokens["account_id"]:
        headers["ChatGPT-Account-Id"] = tokens["account_id"]
    request = urllib.request.Request(USAGE_URL)
    for name, value in headers.items():
        request.add_unredirected_header(name, value)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        report = json.loads(response.read().decode("utf-8"))
    if not isinstance(report, dict):
        raise ValueError("Quota response must be an object")
    return report


def _number(value: object) -> float | None:
    if not isinstance(value, bool) and isinstance(value, (float, int)) and math.isfinite(value):
        return float(value)
    return None


def normalize_quota(report: dict, *, observed_at: float) -> dict:
    limits = report.get("rate_limit", report)
    if not isinstance(limits, dict):
        raise ValueError("Invalid quota limits")
    quota: dict = {"observed_at": observed_at, "windows": []}
    plan = report.get("plan_type", limits.get("plan_type"))
    if isinstance(plan, str) and plan.strip():
        quota["plan_type"] = plan.strip().lower()
    for key in ("allowed", "limit_reached"):
        if isinstance(limits.get(key), bool):
            quota[key] = limits[key]
    for name, window in limits.items():
        if not name.endswith("_window") or not isinstance(window, dict):
            continue
        record: dict = {"name": name}
        used = _number(window.get("used_percent"))
        if used is not None and 0 <= used <= 100:
            record["remaining_percent"] = 100 - used
        reset = _number(window.get("reset_at", window.get("resets_at")))
        after = _number(window.get("reset_after_seconds"))
        if reset is None and after is not None and after >= 0:
            reset = observed_at + after
        if reset is not None and reset > 0:
            record["resets_at"] = reset
        seconds = _number(window.get("limit_window_seconds"))
        if seconds is not None and seconds > 0:
            record["window_minutes"] = seconds / 60
        quota["windows"].append(record)
    return quota


def quota_block(quota: dict, now: float) -> tuple[bool, float | None]:
    exhausted = [w for w in quota.get("windows", []) if w.get("remaining_percent") == 0]
    if quota.get("allowed") is False or quota.get("limit_reached") is True:
        blocked = True
    elif quota.get("allowed") is True or quota.get("limit_reached") is False:
        blocked = False
    else:
        blocked = bool(exhausted)
    if not blocked:
        return False, None
    windows = exhausted or quota.get("windows", [])
    resets = [w.get("resets_at") for w in windows]
    if not resets or any(t is None for t in resets):
        return True, None
    reset = max(resets)
    return reset > now, reset


def quota_permits(quota: dict) -> bool:
    if quota.get("allowed") is False or quota.get("limit_reached") is True:
        return False
    if quota.get("allowed") is True or quota.get("limit_reached") is False:
        return True
    windows = quota.get("windows", [])
    return bool(windows) and all(w.get("remaining_percent", -1) > 0 for w in windows)


def quota_weight(quota: dict, *, weight: float | None = None) -> float | None:
    return weight if weight is not None else PLAN_QUOTA_WEIGHTS.get(quota.get("plan_type"))


def _quota_windows(quota: dict, now: float, minutes: int) -> list[dict]:
    return [w for w in quota.get("windows", [])
            if w.get("window_minutes") == minutes and w.get("resets_at", float("inf")) > now]


def _remaining(windows: list[dict]) -> float | None:
    remaining = [w.get("remaining_percent") for w in windows]
    return min(remaining) if remaining and all(value is not None for value in remaining) else None


def quota_score(quota: dict, now: float, *, weight: float | None = None) -> float:
    """Weighted weekly remaining percent; -1 means the window or weight is unknown."""
    remaining = _remaining(_quota_windows(quota, now, 7 * 24 * 60))
    effective_weight = quota_weight(quota, weight=weight)
    if remaining is None or effective_weight is None:
        return -1
    return remaining * effective_weight


def quota_group_score(quota: dict, now: float) -> float | None:
    """Within-plan window headroom, not capacity; None means no active five-hour window."""
    short = _quota_windows(quota, now, 5 * 60)
    if not short:
        return None
    short_remaining = _remaining(short)
    weekly_remaining = _remaining(_quota_windows(quota, now, 7 * 24 * 60))
    if short_remaining is None or weekly_remaining is None:
        return -1
    return min(short_remaining, weekly_remaining)
