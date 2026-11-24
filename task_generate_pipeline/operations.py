"""Project-local pause controls, read-only recovery reports and preservation checks.

These operations never authenticate, call a model, rewrite a task or resume a job.
"""

import json
import time
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from common import digest, file_sha, load, lock, regular_files, relative, save, tree_sha


_GUARD = ContextVar("pipeline_pause_guard", default=None)


class PipelinePaused(RuntimeError):
    pass


def project_root(here, explicit=None):
    if explicit is not None:
        root = explicit.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Project root must be a directory")
        return root
    here = here.resolve()
    for root in (here, *here.parents):
        if ((root / ".pipeline-project.json").is_file()
                or (root / "private/accounts.json").is_file() and (root / "runs").is_dir()):
            return root
    return here.parent


def pause_paths(root, output=None):
    root = root.resolve()
    paths = [root / "USER_PAUSED"]
    if output is not None:
        output = output.resolve()
        paths.append(output / "USER_PAUSED")
        if output.is_relative_to(root):
            paths.extend(p / "USER_PAUSED" for p in output.parents if p.is_relative_to(root))
    return list(dict.fromkeys(paths))


def registry_paths(root, here):
    marker = root / ".pipeline-project.json"
    config = load(marker) if marker.is_file() else {}
    if not config.get("registry_dir"):
        return here / "seed_catalog.json", here / "group_seed_bindings.json"
    registry = (root / str(relative(config["registry_dir"]))).resolve(strict=True)
    if not registry.is_relative_to(root.resolve()):
        raise ValueError("Shared seed registry must stay inside the project")
    seeds, bindings = registry / "seed_catalog.json", registry / "group_seed_bindings.json"
    if not seeds.is_file() or not bindings.is_file():
        raise ValueError("Registered seed catalog or group bindings are missing; do not create an empty replacement")
    return seeds, bindings


def pause_status(root, output=None):
    markers = [str(p) for p in pause_paths(root, output) if p.exists() or p.is_symlink()]
    return {"paused": bool(markers), "pause_markers": markers,
            "pause_semantics": "Cooperative stop before the next model action, runtime command or publication. An already running subprocess may finish."}


def assert_running(root, output=None):
    state = pause_status(root, output)
    if state["paused"]:
        raise PipelinePaused("Pipeline is paused: " + ", ".join(state["pause_markers"]))


def checkpoint():
    guard = _GUARD.get()
    if guard is not None:
        assert_running(*guard)


@contextmanager
def execution_guard(root, output=None):
    token = _GUARD.set((root, output))
    try:
        checkpoint()
        yield
    finally:
        _GUARD.reset(token)


def set_pause(root, output, *, paused, reason):
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ValueError("A concise operator reason is required")
    root = root.resolve(strict=True)
    target = root if output is None else output.resolve()
    if not target.is_relative_to(root):
        raise ValueError("Pause controls must stay inside the project")
    marker = target / "USER_PAUSED"
    if marker.is_symlink():
        raise ValueError("A linked pause marker cannot be changed")
    with lock(root / "control/.pause.lock"):
        record = {"action": "pause" if paused else "resume", "target": str(target),
                  "reason": reason, "time_unix": time.time(), "starts_jobs": False}
        if paused:
            save(marker, record)
        elif marker.exists():
            # Preserve the previous marker without assuming it is JSON.
            record["previous_marker_sha256"] = file_sha(marker)
            record["previous_marker_text"] = marker.read_text(encoding="utf-8", errors="replace")[:8000]
        save(root / "control/pause-history" / f"{time.time_ns()}.json", record)
        if not paused and marker.exists():
            marker.unlink()
    return pause_status(root, output) | record


def published_signature(run_dir, state):
    run_dir = run_dir.resolve()
    package = run_dir / str(relative(state["package"]))
    if not package.resolve().is_relative_to(run_dir):
        raise ValueError("Published path escapes the run")
    signature = load(package / "receipt.json")["package_sha256"]
    if tree_sha(package / "task") != signature:
        raise ValueError("Published task differs from its receipt")
    if load(package / "task_verify_result.json").get("overall_status") not in {"pass", "pass_with_warnings"}:
        raise ValueError("Published task has no passing verification report")
    return signature


def recovery_report(root, run_dir=None, plan=None):
    """Read states and validate published task hashes; do not turn this into a job runner."""
    result = pause_status(root, run_dir) | {"schema": 1, "starts_jobs": False, "rows": []}
    if run_dir is None:
        return result
    run_dir = run_dir.resolve()
    jobs = load(plan)["jobs"] if plan is not None else []
    expected = {j["case_id"]: j["source"]["source_id"] for j in jobs}
    paths = {p.parent.name: p for p in (run_dir / "cases").glob("*/state.json")}
    for name in sorted(set(expected) | set(paths)):
        relative(name)
        state = load(paths[name]) if name in paths else {}
        if not isinstance(state, dict):
            raise ValueError(f"Invalid case state: {name}")
        row = {"case_id": name, "source_id": state.get("source_id", expected.get(name)),
               "status": state.get("status", "not_started"), "reason": state.get("reason"),
               "package_verified": False, "action": "review"}
        if state.get("package_passed"):
            try:
                signature = published_signature(run_dir, state)
                row |= {"package_verified": True, "package_sha256": signature, "action": "keep_published"}
            except (OSError, KeyError, TypeError, ValueError) as exc:
                row |= {"status": "needs_review", "reason": str(exc), "action": "inspect_published_integrity"}
        elif row["status"] in {"not_started", "paused", "waiting_resource"}:
            row["action"] = "check_conditions_then_resume_same_identity"
        result["rows"].append(row)
    result["status_counts"] = dict(Counter(r["status"] for r in result["rows"]))
    result["resume_rule"] = "Same frozen identity only. Changed code, prompts, sources, image or resources require a new run; preserve old attempts."
    return result


def source_tree_sha(root):
    return digest({p.relative_to(root).as_posix(): file_sha(p) for p in regular_files(root)
                   if "__pycache__" not in p.relative_to(root).parts and p.suffix != ".pyc"})


def audit_preservation(baseline, root):
    """Compare a reviewed, versioned baseline. Missing files are failures, never skipped.

    A deletion/migration receipt replaces only explicitly retired account-backup
    paths when building a new baseline. Source, package and code checks remain.
    """
    if (baseline.get("schema") != 1 or baseline.get("kind") != "pipeline-preservation"
            or Path(baseline["project_root"]).resolve() != root.resolve()):
        raise ValueError("Preservation baseline does not belong to this project")
    checked = Counter()
    failures = []
    for row in baseline["artifacts"]:
        path = Path(row["path"])
        kind = row["kind"]
        if kind not in {"file", "tree", "source_tree"}:
            raise ValueError("Unknown preservation artifact kind")
        try:
            if path.is_symlink() or not (path.is_file() if kind == "file" else path.is_dir()):
                raise ValueError("Preserved artifact is missing or linked")
            observed = (file_sha(path) if kind == "file" else
                        source_tree_sha(path) if kind == "source_tree" else tree_sha(path))
            if observed != row["sha256"]:
                failures.append({"path": str(path), "error": "SHA256 mismatch"})
            else:
                checked[row.get("role", kind)] += 1
        except (OSError, ValueError) as exc:
            failures.append({"path": str(path), "error": str(exc)})
    if not baseline["artifacts"]:
        raise ValueError("An empty preservation baseline cannot pass")
    for registry in baseline.get("registries", []):
        try:
            entries = load(Path(registry["seeds"]))["seeds"]
            seeds = {seed["id"]: seed for seed in entries}
            groups = load(Path(registry["bindings"]))["groups"]
            if len(seeds) != len(entries):
                raise ValueError("Duplicate seed IDs")
            for key, frozen in registry["frozen_groups"].items():
                current = groups[key]
                if any(current[field] != frozen[field] for field in ("seed_id", "seed_sha256")):
                    raise ValueError("An existing group seed changed without a recorded migration")
            for key, binding in groups.items():
                seed = seeds[binding["seed_id"]]
                if binding["seed_sha256"] != digest(seed) or json.loads(key) not in seed["groups"]:
                    raise ValueError("Registry seed hash or subject/task group is inconsistent")
            checked["shared_seed_registry"] += 1
        except (OSError, ValueError, KeyError, TypeError) as exc:
            failures.append({"path": registry["bindings"], "error": str(exc)})
    if failures:
        raise ValueError("Preservation failed: " + json.dumps(failures, ensure_ascii=False))
    return {"status": "passed", "checked": dict(checked), "artifacts": sum(checked.values()),
            "baseline_sha256": digest(baseline), "time_unix": time.time()}
