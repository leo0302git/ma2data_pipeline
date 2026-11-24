"""Plan, construct and independently verify native NatureGym-style task packages."""

import argparse
import json
import math
import shutil
import tempfile
import tomllib
import uuid
import sys
from contextlib import ExitStack, nullcontext
from collections import Counter, defaultdict
from pathlib import Path

from contracts import agent_contract, diagnostics, evidence_readable, SUBMISSION_BACKEND
from agent import AccountUnavailable, Codex, NeedsReview, append_event, stage
from common import digest, file_sha, load, lock, regular_files, save, tree_sha, relative
from reference import NatureGym
from runtime import Defect, Docker, RuntimeUnavailable, static_check
from sources import COMPLETE, SERVER_ROOT, case_id, freeze, public_shape, read_inventory, read_seeds, enrich_profiles
from verification import finding, report, run_cases
from comparisons import STRICT, MEASURED, PROFILES, audit_package
from operations import (PipelinePaused, checkpoint, execution_guard, project_root,
                        recovery_report, set_pause, audit_preservation, published_signature, registry_paths)


HERE = Path(__file__).parent
SCHEMA = 1
PLAN_KIND = "naturegym-seed-plan"
PASS = {"pass", "pass_with_warnings"}
DOCS = {"task/input/README.md", "task/input/data_description.md"}
AUTHORED = DOCS | {"task/task.source.toml", "task/task.json", "task/metadata.json", "work/prepare.py",
                   "task/evaluation/metrics.py", "task/evaluation/rubric.json", "task/evaluation/rubric.py",
                   "task/environment/Dockerfile", "task/environment/Dockerfile.eval", "task/environment/packages.json",
                   "task/environment/packages.eval.json", "task/environment/constraints.txt",
                   "task/environment/constraints.eval.txt", "task/environment/check_resources.sh",
                   "task/environment/check_resources.eval.sh"}
PRE_METADATA = (AUTHORED - {"task/metadata.json", "task/evaluation/rubric.json", "task/evaluation/rubric.py",
                            "task/environment/Dockerfile.eval", "task/environment/packages.eval.json",
                            "task/environment/constraints.txt", "task/environment/constraints.eval.txt"})


def authored(path):
    return (path in AUTHORED or path.startswith("task/evaluation/lib/") and path.endswith(".py")
            or path.startswith("work/prepare_lib/") and path.endswith(".py")
            or path.startswith("task/environment/tools/") and Path(path).suffix in {".sh", ".py", ".json", ".txt"})


def readable(path):
    artifact = path.startswith(('task/input/data/','task/evaluation/assets/')) and Path(path).suffix in {
        '.json','.csv','.tsv','.txt','.log','.options','.mps','.md','.py'}
    return artifact or evidence_readable(path) or authored(path) or path in {"work/make_cases.py", "task/evaluation/evaluator.py", "task/evaluation/judge.py",
        "evidence/diagnostics.json", "evidence/previous-diagnostics.json", "verification/observations.json",
        "verification/inputs/source_audit.json", "evidence/environment-provenance.json", "evidence/solver-isolation.json"}


def summary(result):
    value = result.get("summary")
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise NeedsReview("Stage did not supply a concise implementation summary")
    return result


def build_plan(rows, seeds, bindings, agent, folder, *, preview=False):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["domain"], row["category"])].append(row)
    results, jobs = [], []
    for group, members in groups.items():
        key = json.dumps(group, ensure_ascii=False, separators=(",", ":"))
        complete = [row for row in members if row["download_status"] == COMPLETE and row["paths"]]
        candidates = [seed for seed in seeds.values() if list(group) in seed["groups"]]
        hint = [seed["id"] for seed in candidates]
        binding = bindings.get(key)
        selected = None
        reason = "No indexed candidate for this subject/task group"
        status = "needs_review"
        if binding is not None:
            selected = seeds.get(binding["seed_id"])
            if selected is None or binding["seed_sha256"] != digest(selected) or list(group) not in selected["groups"]:
                selected = None
                reason = "The fixed seed changed or disappeared; review a deliberate binding migration"
            else:
                reason = binding["reason"]
        elif candidates and preview:
            status, reason = "preview", "Candidates are available for group-level selection"
        elif candidates:
            context = {"subject": group[0], "task_category": group[1], "complete_source_count": len(complete),
                       "file_types": dict(Counter("".join(Path(path).suffixes[-2:]).lower()
                                                  for row in complete for path in row["paths"])),
                       "candidates": candidates}
            try:
                choice = stage(agent, "seed-select", context, folder, lambda p: False, lambda p: False,
                               folder / "seed-events.jsonl", turns=2)
                selected = next((seed for seed in candidates if seed["id"] == choice.get("seed_id")), None)
                reason = choice.get("reason")
                if selected is None or not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
                    raise NeedsReview("Agent did not select a valid indexed seed with evidence")
                bindings[key] = {"seed_id": selected["id"], "seed_sha256": digest(selected), "reason": reason}
            except AccountUnavailable as exc:
                selected, status, reason = None, "waiting_resource", str(exc)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                selected, reason = None, str(exc)
        for row in members:
            state, note = status, reason
            if row not in complete:
                state, note = "incomplete_download", "Inventory does not mark the required files as complete"
            elif selected is not None:
                state = "planned"
                jobs.append({"case_id": case_id(row), "source": row, "seed": selected,
                             "seed_sha256": digest(selected), "selection_reason": reason})
            results.append({"source_id": row["source_id"], "group": list(group), "state": state, "reason": note,
                            "candidate_seed_ids": hint, "seed_id": selected["id"] if selected else None})
    return {"schema": SCHEMA, "kind": PLAN_KIND, "preview": preview, "results": results, "jobs": jobs,
            "summary": dict(Counter(row["state"] for row in results))}


def scope_task(agent, context, case, event_log):
    path = case / "task-plan.json"
    if path.exists():
        return load(path)
    keys = ("goal", "preserved", "changes", "evaluation_unit", "public_data", "private_data", "submission", "scoring")
    material = context
    for schema_attempt in range(2):
        result = stage(agent, "task-scope", material, case,
                       lambda p: p.startswith('attempt-01/') and readable(p[len('attempt-01/'):]),
                       lambda p: False, event_log)
        errors = []
        for key in keys:
            value = result.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f'{key}: requires nonempty text')
            elif len(value) > 4000:
                errors.append(f'{key}: {len(value)} characters exceeds 4000')
        ids = result.get('evidence_ids')
        allowed = {row['id'] for row in context['source_evidence']}
        if (not isinstance(ids, list) or not ids
                or any(not isinstance(value, str) or value not in allowed for value in ids)):
            errors.append('evidence_ids: requires a nonempty list of actual frozen source evidence IDs')
        if not errors:
            save(path, result)
            return result
        append_event(event_log, {'event': 'scope_schema_feedback', 'errors': errors,
                                'schema_attempt': schema_attempt+1})
        material = context | {'scope_validation_feedback': {
            'errors': errors, 'previous_result': result,
            'repair_scope': 'Repair response shape or compact wording only. Preserve complete scientific constraints, data boundaries, metric and genuine evidence-gap verdicts. This is not a failed source-data check.'}}
    raise NeedsReview('Scope response schema failed after bounded correction: ' + '; '.join(errors))


def move_derived(source, target, attempt):
    if not target.resolve().is_relative_to(attempt.resolve()) or target.is_symlink():
        raise ValueError("Derived data target is outside the attempt")
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    source.replace(target)


def bounded_profiles(root, budget, *, inspect=True):
    paths = list(regular_files(root))
    if sum(path.stat().st_size for path in paths) > budget:
        raise NeedsReview("Generated data exceed the configured artifact budget")
    rows = []
    for path in paths[:80]:
        profile = {}
        if inspect:
            try:
                profile = public_shape(path)
            except (OSError, ValueError, KeyError, StopIteration) as exc:
                profile = {"profile_error": type(exc).__name__}
        rows.append({"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "profile": profile})
    return {"files": rows, "file_count": len(paths)}


def check_boundaries(package, frozen, context):
    public = package / "input"
    private = package / "evaluation/assets"
    private_files=list(regular_files(private));public_files=list(regular_files(public))
    hidden={row['sha256']:['frozen source '+row['id']] for row in frozen}
    for path in private_files:hidden.setdefault(file_sha(path),[]).append(path.relative_to(package).as_posix())
    duplicates, duplicate_count = [], 0
    for path in public_files:
        matches=hidden.get(file_sha(path))
        if matches:
            duplicate_count += 1
            if len(duplicates) < 16:
                duplicates.append((path.relative_to(package).as_posix(), matches))
    if duplicates:
        details = '; '.join(name + ' duplicates ' + ', '.join(matches) for name, matches in duplicates)
        if duplicate_count > len(duplicates):
            details += f'; {duplicate_count - len(duplicates)} additional public files also match'
        raise Defect("C2.6", f"Public files duplicate protected material ({duplicate_count} files): " + details,
                     duplicates[0][0])
    markers = ["/source/", "evaluation/assets/", "CODEX_HOME", context["seed"]["url"], context["source_id"]]
    for name in ("README.md", "data_description.md"):
        text = (public / name).read_text(encoding="utf-8")
        if any(len(marker) >= 8 and marker.casefold() in text.casefold() for marker in markers):
            raise Defect("C2.6", "Public documentation exposes a protected reference", f"input/{name}")
    return {'status':'pass','public_file_count':len(public_files),'private_file_count':len(private_files),
            'public_tree_sha256':tree_sha(public),'private_tree_sha256':tree_sha(private),
            'source_sha256':[r['sha256'] for r in frozen],
            'no_exact_raw_or_private_copies':True,'public_document_protected_marker_hits':[]}


def calibration(records):
    path = records / "calibration.json"
    if not path.exists():
        return []
    if path.stat().st_size > 32768:
        raise Defect('C3.1', 'Calibration evidence exceeds 32768 bytes', 'evidence/preparation-diagnostics/')
    values = load(path)
    if not isinstance(values, list) or len(values) > 32:
        raise Defect('C3.1', 'Calibration summary must be a list with at most 32 rows', 'evidence/preparation-diagnostics/')
    for index, row in enumerate(values):
        if not isinstance(row, dict) or set(row) != {'id', 'method', 'value', 'conditions'}:
            raise Defect('C3.1', f'Calibration row {index} must contain exactly id, method, value and conditions', 'evidence/preparation-diagnostics/')
        if type(row['value']) not in {int, float} or not math.isfinite(row['value']):
            raise Defect('C3.1', f'Calibration row {index}.value must be a finite number, excluding bool', 'evidence/preparation-diagnostics/')
        for key, limit in (('id', 360), ('method', 2048), ('conditions', 4096)):
            if not isinstance(row[key], str):
                raise Defect('C3.1', f'Calibration row {index}.{key} must be text', 'evidence/preparation-diagnostics/')
            if len(row[key]) > limit:
                raise Defect('C3.1', f'Calibration row {index}.{key} length {len(row[key])} exceeds {limit}', 'evidence/preparation-diagnostics/')
    return values


def supported_runtime(package, ref, case_name):
    try:
        with (package / "task.source.toml").open("rb") as stream:
            identity = tomllib.load(stream)
    except (OSError, ValueError) as exc:
        raise Defect("C0.1", "Invalid native task identity manifest", "task/task.source.toml") from exc
    definition = identity.get("task")
    if not isinstance(definition, dict) or definition.get("name") != f"naturebench/{case_name}":
        raise Defect("C0.1", "Package name does not match the frozen case ID", "task/task.source.toml")
    try:
        task = load(package / "task.json")
        if not isinstance(task, dict):
            raise ValueError("Expected a task manifest object")
    except (OSError, ValueError) as exc:
        raise Defect("C0.2", "Invalid private task manifest", "task/task.json") from exc
    if task.get("execution") or task.get("tool_records"):
        raise RuntimeUnavailable("Submitted-program execution and trusted sidecars require an approved runtime backend")
    if (package / "evaluation/rubric.json").is_file():
        rubric = (package / "evaluation/rubric.json").read_text(encoding="utf-8")
        if '"llm"' in rubric:
            (package / "evaluation/judge.py").write_bytes(ref.files["judge.py"])
            raise RuntimeUnavailable("This rubric requires a real managed Judge, which this offline backend cannot reach")


def independent_case_results(agent, runtime, images, package, attempt, material, files, frozen, event_log):
    """Repair a broken independent preparation program without changing the candidate."""
    signature = tree_sha(package)
    program = attempt / 'work/make_cases.py'
    plan = attempt / 'verification-plan.json'
    root = attempt / 'verification'
    feedback = {}
    archived = []
    for repair in range(2):
        if not plan.exists():
            allowed = lambda p: readable(p) or any(p.startswith(name + '/') and Path(p).suffix in {'.log', '.json', '.py'} for name in archived)
            result = stage(agent, 'verification-cases', material | feedback, attempt, allowed,
                           lambda p: p == 'work/make_cases.py', event_log)
            if not program.is_file():
                raise Defect('C4.1', 'Verification Agent did not create the case preparation program', 'verification-plan.json')
            save(plan, summary(result))
        if tree_sha(package) != signature:
            raise NeedsReview('Candidate changed during independent case preparation repair')
        recipe = {'package_sha256': signature, 'images': images, 'test_program_sha256': file_sha(program)}
        observations, receipt = root / 'observations.json', root / 'receipt.json'
        if observations.exists() and receipt.is_file() and load(receipt) == recipe:
            return load(observations)
        if root.exists():
            root.rename(attempt / ('verification-incomplete-' + uuid.uuid4().hex[:10]))
        try:
            dynamic = run_cases(runtime, images['evaluator'], package, program, root, files, frozen)
        except Defect as exc:
            if exc.check != 'C4.2' or repair:
                raise
            archive = attempt / ('verification-preparation-failed-' + uuid.uuid4().hex[:10])
            if root.exists():
                root.rename(archive)
            else:
                archive.mkdir()
            shutil.copyfile(program, archive / 'make_cases.py')
            plan.rename(archive / 'verification-plan.json')
            archived.append(archive.name)
            log = archive / 'make-cases.log'
            feedback = {'verification_preparation_failure': {
                'check_id': exc.check, 'issue': str(exc),
                'previous_program': archive.name + '/make_cases.py',
                'log_path': archive.name + '/make-cases.log',
                'log_tail': log.read_text(errors='replace')[-6000:] if log.exists() else None,
                'repair_scope': 'Repair only the independent preparation program. Preserve checks, expected scoring semantics and the candidate. Report scientific inconsistencies; never change expectations merely to agree with observed evaluator output.'}}
            save(attempt / 'evidence/verification-preparation-retry.json', feedback)
            continue
        save(receipt, recipe)
        return dynamic


def run_attempt(agent, runtime, ref, case, attempt, context, files, byte_limit, event_log, previous):
    package, evidence = attempt / "task", attempt / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    checker = ref.install(case / "reference", package)
    runtime.available(evidence)
    capabilities = runtime.capabilities(evidence) if hasattr(runtime, "capabilities") else {"image": runtime.base}
    contract = agent_contract(ref, context.get('comparison_profile', STRICT))
    feedback = {} if previous is None else {
        "failed_checks": [row for row in previous["failures"] if row.get("evidence_status") != "not_verified"],
        "unverified_checks": [row for row in previous["failures"] if row.get("evidence_status") == "not_verified"],
        "previous_diagnostics": diagnostics(previous["attempt"])}
    build_result = attempt / "build-result.json"
    if not build_result.exists():
        material = context | contract | feedback | {"approved_base_image": runtime.base, "runtime_capabilities": capabilities,
                                         "templates": ref.templates()}
        if previous is None:
            result = stage(agent, "task-build", material, attempt, readable, authored, event_log)
        else:
            material["failed_checks"] = feedback["failed_checks"]
            material["previous_diagnostics"] = diagnostics(previous["attempt"])
            save(evidence / "previous-diagnostics.json", material["previous_diagnostics"])
            for path in regular_files(previous["attempt"]):
                name = path.relative_to(previous["attempt"]).as_posix()
                if authored(name) or name == "work/make_cases.py":
                    target = attempt / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
            result = stage(agent, "task-fix", material, attempt, readable, authored, event_log)
        missing = sorted(name for name in PRE_METADATA if not (attempt / name).is_file())
        if missing:
            raise Defect("C0.1", "Required Agent outputs missing: " + ", ".join(missing), "build-result.json")
        save(build_result, summary(result))
    runtime.available(evidence)
    images = runtime.build(package, evidence)
    prepared = evidence / "data-preparation.json"
    if not prepared.exists():
        with tempfile.TemporaryDirectory(prefix="materialize-", dir=attempt) as tmp:
            staging = Path(tmp)
            records = staging / "records"
            records.mkdir()
            runtime.prepare(images["evaluator"], attempt / "work/prepare.py", files, context["source_evidence"],
                            staging, evidence, records)
            public, private = staging / "input/data", staging / "evaluation/assets"
            public_index = bounded_profiles(public, byte_limit)
            private_index = bounded_profiles(private, byte_limit, inspect=False)
            if not public_index["file_count"]:
                raise Defect("C3.1", "Preparation produced no Solver data", "prepare.log")
            notes = calibration(records)
            move_derived(public, package / "input/data", attempt)
            move_derived(private, package / "evaluation/assets", attempt)
            check_boundaries(package, context["source_evidence"], context)
            save(prepared, {"public": public_index, "private_schema": private_index, "calibration": notes,
                            "isolated_methods": load(evidence / "method-runs/index.json")
                                if (evidence / "method-runs/index.json").exists() else None,
                            "public_sha256": tree_sha(package / "input/data"),
                            "private_sha256": tree_sha(package / "evaluation/assets")})
    data = load(prepared)
    for key, folder in (("public", "input/data"), ("private", "evaluation/assets")):
        if data[f"{key}_sha256"] != tree_sha(package / folder):
            raise NeedsReview(f"Materialized {key} data changed after preparation")
    boundary=check_boundaries(package,context['source_evidence'],context)
    save(evidence/'boundary-check.json',boundary)
    save(evidence / "diagnostics.json", diagnostics(attempt))
    # Historical logs stay available through the broker. Inlining them again beside
    # current receipts and requested file contents can overflow the model context.
    post_feedback={'prior_attempt_findings':feedback.get('failed_checks',[])+feedback.get('unverified_checks',[]),
                   'previous_diagnostics_path':'evidence/previous-diagnostics.json' if previous else None}
    material = context | contract | post_feedback | {"data": data, "images": images, "runtime_capabilities": capabilities,
                                     'current_boundary_check':boundary,
                                     "metadata_template": ref.templates()["metadata.json"]}
    environment_before=tree_sha(package/'environment')
    if not (attempt / "metadata-result.json").exists():
        result = stage(agent, "task-metadata", material, attempt, readable,
                       lambda p: p in DOCS | {"task/metadata.json",'task/environment/check_resources.sh',
                                              'task/environment/check_resources.eval.sh'}, event_log)
        save(attempt / "metadata-result.json", summary(result))
    if tree_sha(package/'environment')!=environment_before:
        images=runtime.build(package,evidence)
        material['images']=images
    boundary=check_boundaries(package,context['source_evidence'],context)
    save(evidence/'boundary-check.json',boundary)
    material['current_boundary_check']=boundary
    try:
        comparison_audit = audit_package(package, data['calibration'],
                                         context.get('comparison_profile', STRICT), evidence)
    except ValueError as exc:
        raise Defect('C1.3', str(exc), 'task/metadata.json') from exc
    material['comparison_profile_audit'] = comparison_audit
    supported_runtime(package, ref, context["case_id"])
    ok, static = static_check(checker, package, evidence)
    if not ok:
        failures = [finding(row["id"], "; ".join(row["errors"]), "evidence/static-check.json")
                    for row in static["checks"] if row["status"] == "failed"]
        if not failures:
            raise RuntimeUnavailable("Native static verification is blocked; see evidence/static-check.json")
        return report(failures=failures), None
    runtime.checks(package, images, evidence)
    signature = tree_sha(package)
    save(attempt / "filter_result.json", {"task_build": {"status": "success", "path": "task",
         "instances": {row["name"]: row["name"] for row in load(package / "task.json")["instances"]}},
         "package_sha256": signature, "adaptation_plan": "../task-plan.json"})
    material = material | {"static_check": static, "native_task_manifest": load(package / "task.json")}
    dynamic = independent_case_results(agent, runtime, images, package, attempt, material,
                                       files, context['source_evidence'], event_log)
    # The full logs stay available on demand; duplicating them beside all actual
    # controls can squeeze a newly requested scientific program out of the context.
    current_diagnostics = diagnostics(attempt)
    save(evidence / 'diagnostics.json', current_diagnostics)
    review = stage(agent, "task-verify", material | {"dynamic_cases": dynamic["cases"],
                   'source_audit':dynamic.get('source_audit'), 'relocated_public_data':dynamic.get('relocated_public_data'),
                   "verified_solver_mount": "task/input only",
                   "runtime_evidence": {'index': 'evidence/diagnostics.json',
                       'available_reports': sorted(current_diagnostics),
                       'notice': 'Read the specific actual receipts needed through the broker; the index alone is not execution proof.'},
                   "verification_rules": ref.files["verification_rules"].decode("utf-8")},
                   attempt, readable, lambda p: False, event_log)
    if tree_sha(package) != signature:
        raise NeedsReview("Package changed after the verification evidence was generated")
    save(attempt / "independent-review.json", review)
    result = report(review, dynamic["findings"], dynamic)
    result['comparison_profile'] = context.get('comparison_profile', STRICT)
    if result['comparison_profile'] == MEASURED:
        result['strict_upstream_complete'] = False
        result['qualification'] = 'Generated-task measured profile; native structure/runtime checks retained. Not a literature SOTA or strict original reported-comparison certification.'
    return result, signature


def publish(run_dir, case_name, attempt, signature):
    checkpoint()
    destination = run_dir / "passed" / case_name
    if destination.exists():
        if tree_sha(destination / "task") != signature:
            raise NeedsReview("A different verified package already exists for this case")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="publish-", dir=destination.parent) as tmp:
        staged = Path(tmp) / "release"
        shutil.copytree(attempt / "task", staged / "task")
        for name in ("task_verify_result.json", "filter_result.json"):
            shutil.copyfile(attempt / name, staged / name)
        save(staged / "receipt.json", {"package_sha256": signature, "attempt": attempt.name})
        if (attempt / 'evidence/comparison-profile.json').exists():
            shutil.copyfile(attempt / 'evidence/comparison-profile.json', staged / 'comparison-profile.json')
        if tree_sha(staged / "task") != signature:
            raise NeedsReview("Verified package changed during publishing")
        staged.replace(destination)
    return destination


def repairable_evidence_gap(result, attempt):
    """Only a completed real review with missing dynamic controls is retryable here."""
    checks = result.get("unverified_checks", [])
    return (result["overall_status"] == "not_verified" and bool(checks)
            and all(row["check_id"].startswith("C4.") for row in checks)
            and bool(result.get("dynamic_test", {}).get("baseline_results"))
            and (attempt / "independent-review.json").is_file())


def process_job(job, run_dir, source_root, prefix, budget, attempts, agent, runtime, ref, comparison_profile=STRICT):
    checkpoint()
    row, seed = job["source"], job["seed"]
    name = case_id(row)
    if name != job["case_id"] or job["seed_sha256"] != digest(seed):
        raise NeedsReview("Plan case ID or fixed seed digest is inconsistent")
    case = run_dir / "cases" / name
    case.mkdir(parents=True, exist_ok=True)
    state_path, events = case / "state.json", case / "events.jsonl"
    previous = None
    try:
        files, frozen = freeze(row, source_root, prefix, budget)
        enrich_profiles(frozen, files, job.get('source_profiles'))
        draft = None
        draft_receipt = case / 'authoring-draft.json'
        if draft_receipt.exists():
            draft = load(draft_receipt)
            if (draft.get('source_id') != row['source_id']
                    or draft.get('source_sha256') != [s['sha256'] for s in frozen]
                    or draft.get('upstream_id') != seed.get('upstream_id', seed['id'])
                    or not isinstance(draft.get('copied_files'), dict)):
                raise NeedsReview('Recovered authoring draft does not match the frozen source and upstream seed')
            if not state_path.exists():
                for path, checksum in draft['copied_files'].items():
                    relative(path)
                    if not (authored(path) or path == 'work/prepare.py'):
                        raise NeedsReview('Recovered draft must contain authoring source files only')
                    target = case / 'attempt-01' / path
                    if not target.resolve().is_relative_to((case / 'attempt-01').resolve()):
                        raise NeedsReview('Recovered draft resolves outside its task workspace')
                    if file_sha(target) != checksum:
                        raise NeedsReview('Recovered authoring draft changed before its first model review')
        identity = {"job": job, "frozen": frozen, "naturegym_bundle_sha256": ref.bundle_sha256,
                    "comparison_profile": comparison_profile,
                    "authoring_draft": draft,
                    "agent": agent.identity, "runtime": {"base": runtime.base, "timeout": runtime.timeout,
                    "memory": runtime.memory, "cpus": runtime.cpus},
                    "pipeline": {path.name: file_sha(path) for path in sorted(HERE.glob("*.py"))}}
        previous_state = load(state_path) if state_path.exists() else {}
        fingerprint = digest(identity)
        if previous_state and previous_state.get("recipe") != fingerprint:
            raise NeedsReview("Source, seed, prompts or runtime changed; use a new run directory")
        if previous_state.get("package_passed"):
            published_signature(run_dir, previous_state)
            return {key: value for key, value in previous_state.items() if key != "recipe"}
        save(case / "frozen-inputs.json", identity)
        state = {"recipe": fingerprint, "source_id": row["source_id"], "case_id": name,
                 "seed_id": seed["id"], "package_passed": False}
        save(state_path, state | {"status": "scope"})
        context = {"case_id": name, "group": [row["domain"], row["category"]], "source_id": row["source_id"],
                   "comparison_profile": comparison_profile,
                   "comparison_policy": agent_contract(ref, comparison_profile)['comparison_policy'],
                   "seed": seed, "source_evidence": frozen,
                   "source_format_policy": "Seed suffixes are retrieval hints. Verify actual content and scientific modality; never reject or replace a fixed group seed solely because a source has a different filename extension.",
                   "submission_backend": SUBMISSION_BACKEND,
                   "authoring_draft": draft,
                   "package_scope": {"unit": "one_source", "source_id": row['source_id'],
                                     "source_file_count": len(files), "other_group_sources_required": False}}
        context['approved_base_image']=runtime.base
        if hasattr(runtime,'capabilities'):
            preflight=case/'preflight';preflight.mkdir(exist_ok=True)
            runtime.available(preflight)
            context['runtime_capabilities']=runtime.capabilities(preflight)
        else:
            context['runtime_capabilities']={'image':runtime.base,'inspection':'not available in this backend'}
        context["adaptation_plan"] = scope_task(agent, context, case, events)
        for index in range(1, attempts + 1):
            checkpoint()
            attempt = case / f"attempt-{index:02d}"
            attempt.mkdir(exist_ok=True)
            result_file = attempt / "task_verify_result.json"
            if result_file.exists() and (attempt / "attempt-result.json").exists():
                result = load(result_file)
                signature = load(attempt / "attempt-result.json")["package_sha256"]
            else:
                save(state_path, state | {"status": "running", "attempt": index})
                try:
                    result, signature = run_attempt(agent, runtime, ref, case, attempt, context, files, budget, events, previous)
                except Defect as exc:
                    result, signature = report(failures=[finding(exc.check, str(exc), exc.evidence)]), None
                save(result_file, result)
                if result["overall_status"] != "not_verified" or (attempt / "independent-review.json").is_file():
                    save(attempt / "attempt-result.json", {"package_sha256": signature})
            status = result["overall_status"]
            if status in PASS:
                if not signature or tree_sha(attempt / "task") != signature:
                    raise NeedsReview("Verified package no longer matches its recorded digest")
                dest = publish(run_dir, name, attempt, signature)
                state |= {"status": status, "attempt": index, "package_passed": True,
                          "package": dest.relative_to(run_dir).as_posix()}
                break
            if (status == "not_verified" and not repairable_evidence_gap(result, attempt)) or index == attempts:
                state |= {"status": status, "attempt": index}
                break
            previous = {"attempt": attempt, "failures":
                        [row | {"evidence_status": "failed"} for row in result["failed_checks"]]
                        + ([row | {"evidence_status": "not_verified"} for row in result["unverified_checks"]]
                           if (attempt / "independent-review.json").is_file() else [])}
    except PipelinePaused as exc:
        state = (load(state_path) if state_path.exists() else
                 {"source_id": row["source_id"], "case_id": name, "seed_id": seed["id"], "package_passed": False})
        state |= {"status": "paused", "reason": str(exc)}
    except (AccountUnavailable, RuntimeUnavailable, NeedsReview, OSError, ValueError, KeyError, TypeError) as exc:
        status = ("waiting_resource" if isinstance(exc, AccountUnavailable) else "not_verified"
                  if isinstance(exc, RuntimeUnavailable) else "needs_review")
        state = (load(state_path) if state_path.exists() else
                 {"source_id": row["source_id"], "case_id": name, "seed_id": seed["id"], "package_passed": False})
        state |= {"status": status, "package_passed": False, "reason": str(exc)}
        if state.get("attempt") and status == "not_verified":
            save(case / f"attempt-{state['attempt']:02d}" / "task_verify_result.json", report(blocker=str(exc)))
    save(state_path, state)
    append_event(events, {"event": "case_result", "status": state["status"], "attempt": state.get("attempt")})
    return {key: value for key, value in state.items() if key != "recipe"}


def model_options(parser):
    parser.add_argument("--account-pool", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--effort")


def operation(args, root):
    if args.output is not None and not args.output.resolve().is_relative_to(root):
        raise ValueError("Operational reports must stay inside the project")
    if args.output is not None:
        inputs = [getattr(args, name, None) for name in ("plan", "baseline")]
        if any(path is not None and path.resolve() == args.output.resolve() for path in inputs):
            raise ValueError("Operational output must not overwrite its input")
    if args.action in {"pause", "resume"}:
        result = set_pause(root, args.run_dir, paused=args.action == "pause", reason=args.reason)
    elif args.action == "status":
        result = recovery_report(root, args.run_dir, args.plan)
    elif args.action == "audit":
        result = audit_preservation(load(args.baseline), root)
    else:
        # Diagnostic containers are allowed while jobs are paused; no model is involved.
        with tempfile.TemporaryDirectory(prefix="preflight-", dir=root) as tmp:
            runtime = Docker(args.base_image, 60, "1g", 1)
            runtime.available(Path(tmp))
            caps = runtime.capabilities(Path(tmp))
        missing = sorted(set(args.require_package) - {p.lower() for p in caps.get("packages", {})})
        result = {"status": "failed" if missing else "passed", "model_called": False,
                  "accounts_checked": False, "runtime": caps, "missing_packages": missing,
                  "reference_bundle_sha256": NatureGym().bundle_sha256}
    if args.output is not None:
        save(args.output, result)
    print(json.dumps(result, ensure_ascii=False))
    return int(result.get("status") == "failed")


def _main(guards):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version",
                        version="task_generate_pipeline " + load(Path(__file__).resolve().parent / "VERSION.json")["version"])
    commands = parser.add_subparsers(dest="action", required=True)
    plan = commands.add_parser("plan", help="Group the CSV and choose one frozen seed per group")
    plan.add_argument("--inventory", type=Path, default=HERE / "ldm_1105_server_inventory.csv")
    plan.add_argument("--seeds", type=Path)
    plan.add_argument("--bindings", type=Path,
                      help="Shared group-to-seed registry; override only for an isolated experiment")
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--preview", action="store_true", help="List potential matches without a model or file access")
    plan.add_argument("--no-seed-discovery", action="store_true", help="Use only the existing reviewed seed catalog")
    plan.add_argument("--seed-cache", type=Path, help="Private cache of original public benchmark task briefs")
    plan.add_argument("--discovery-proxy", help="Optional process-local proxy for benchmark reference retrieval")
    plan.add_argument("--discovery-proxy-file", type=Path, help="Read a private proxy URL without exposing credentials in command arguments")
    plan.add_argument("--data-root", type=Path, default=Path(SERVER_ROOT), help="Read-only source root for discovery evidence")
    plan.add_argument("--source-profiles", type=Path, help="Optional measured source-probe profiles for group discovery")
    model_options(plan)
    run = commands.add_parser("run", help="Run the Agent-driven construction and verification stages")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--data-root", type=Path, required=True)
    run.add_argument("--server-prefix", default=SERVER_ROOT)
    run.add_argument("--run-dir", type=Path, required=True)
    run.add_argument("--base-image", default="python:3.12-slim", help="An operator-approved image already in local Docker")
    run.add_argument("--source-id", action="append", default=[])
    run.add_argument("--limit", type=int)
    run.add_argument("--attempts", type=int, choices=range(1, 5), default=2)
    run.add_argument("--max-source-gib", type=int, default=50, help="Source and derived-data budget in GiB (1–100)")
    run.add_argument("--timeout", type=int, default=600)
    run.add_argument("--memory", default="4g")
    run.add_argument("--cpus", type=float, default=2)
    run.add_argument("--resume", action="store_true")
    run.add_argument('--comparison-profile', choices=PROFILES, default=STRICT,
                     help='Use generated_measured explicitly for new-task strong/weak measured comparisons')
    model_options(run)
    for command in (plan, run):
        command.add_argument("--project-root", type=Path, help="Project root whose USER_PAUSED marker applies to every run")
    for name in ("status", "pause", "resume", "audit", "preflight"):
        command = commands.add_parser(name, help="Project-local operation; never starts model jobs")
        command.add_argument("--project-root", type=Path)
        command.add_argument("--output", type=Path)
        if name in {"status", "pause", "resume"}:
            command.add_argument("--run-dir", type=Path)
        if name in {"pause", "resume"}:
            command.add_argument("--reason", required=True)
        if name == "status":
            command.add_argument("--plan", type=Path)
        if name == "audit":
            command.add_argument("--baseline", type=Path, required=True)
        if name == "preflight":
            command.add_argument("--base-image", required=True)
            command.add_argument("--require-package", action="append", default=[])
    args = parser.parse_args()
    try:
        root = project_root(HERE, args.project_root)
        if args.action not in {"plan", "run"}:
            return operation(args, root)
        if args.action == "plan":
            seeds, bindings = registry_paths(root, HERE)
            args.seeds, args.bindings = args.seeds or seeds, args.bindings or bindings
        preview = args.action == "plan" and args.preview
        if not preview:
            guards.enter_context(execution_guard(root, args.output.parent if args.action == "plan" else args.run_dir))
        if not preview and (not args.account_pool or not args.model):
            raise ValueError("Agent runs require --account-pool and --model; use plan --preview for a local inventory preview")
        agent = None if preview else Codex(args.account_pool, args.model, args.effort)
        if args.action == "plan":
            if len({path.resolve() for path in (args.inventory, args.seeds, args.bindings, args.output)}) != 4:
                raise ValueError("Inventory, seeds, bindings and output must be distinct files")
            with (nullcontext() if preview else lock(args.bindings.with_name(args.bindings.name + ".lock"))):
                state = load(args.bindings) if args.bindings.exists() else {"schema": SCHEMA, "kind": PLAN_KIND, "groups": {}}
                if state.get("schema") != SCHEMA or state.get("kind") != PLAN_KIND:
                    raise ValueError("Expected a binding file made by this rewrite; choose a fresh binding path")
                rows = read_inventory(args.inventory)
                discovery = None
                if not preview and not args.no_seed_discovery:
                    if args.discovery_proxy and args.discovery_proxy_file:
                        raise ValueError("Choose one discovery proxy setting")
                    discovery_proxy = (args.discovery_proxy_file.read_text().strip()
                                       if args.discovery_proxy_file else args.discovery_proxy)
                    from discovery import discover_missing
                    with lock(args.seeds.with_name(args.seeds.name + '.lock')):
                        discovery = discover_missing(rows, args.seeds, state['groups'], agent,
                                                     args.output.parent / 'seed-discovery',
                                                     cache=args.seed_cache or args.output.parent / 'benchmark-cache',
                                                     proxy=discovery_proxy, data_root=args.data_root,
                                                     source_profiles=args.source_profiles)
                    save(args.output.parent / 'seed-discovery-result.json', discovery)
                result = build_plan(rows, read_seeds(args.seeds), state["groups"], agent,
                                    args.output.parent, preview=preview)
                if discovery:
                    gaps = {sid: row['reason'] for row in discovery['groups'] if row['status'] == 'needs_review'
                            for sid in row['sources']}
                    for row in result['results']:
                        if row['state'] == 'needs_review' and not row['seed_id'] and row['source_id'] in gaps:
                            row['reason'] = gaps[row['source_id']]
                if not preview and args.source_profiles:
                    profiles = {x['source_id']: x for x in load(args.source_profiles)}
                    for job in result['jobs']:
                        if job['source']['source_id'] in profiles:
                            job['source_profiles'] = profiles[job['source']['source_id']]
                if not preview:
                    checkpoint()
                    save(args.bindings, state)
                save(args.output, result)
            print(json.dumps(result["summary"], ensure_ascii=False))
            return 0
        payload = load(args.plan)
        if payload.get("schema") != SCHEMA or payload.get("kind") != PLAN_KIND or payload.get("preview"):
            raise ValueError("Run requires a locked plan produced without --preview")
        jobs = [job for job in payload["jobs"] if not args.source_id or job["source"]["source_id"] in args.source_id]
        if args.limit is not None:
            if args.limit < 1:
                raise ValueError("--limit must be positive")
            jobs = jobs[:args.limit]
        if not jobs or (args.source_id and set(args.source_id) - {job["source"]["source_id"] for job in jobs}):
            raise ValueError("No planned job for one or more requested source IDs")
        if args.timeout <= 0 or not math.isfinite(args.cpus) or args.cpus <= 0 or not 1 <= args.max_source_gib <= 100:
            raise ValueError("Runtime time and CPU limits must be positive and the data budget must be 1–100 GiB")
        root, out = args.data_root.resolve(strict=True), args.run_dir.resolve()
        if out.is_relative_to(root):
            raise ValueError("Keep run outputs outside the raw data directory")
        runtime = Docker(args.base_image, args.timeout, args.memory, args.cpus)
        reference = NatureGym()
        with lock(out / ".run.lock"):
            config = {"plan_sha256": file_sha(args.plan), "cases": [job["case_id"] for job in jobs]}
            config_path = out / "run.json"
            if config_path.exists():
                if not args.resume or load(config_path) != config:
                    raise ValueError("Use --resume with the same plan and cases, or select a new run directory")
            elif args.resume:
                raise ValueError("No existing run to resume")
            save(config_path, config)
            results = []
            for job in jobs:
                checkpoint()
                results.append(process_job(job, out, root, args.server_prefix, args.max_source_gib * (1024 ** 3),
                                           args.attempts, agent, runtime, reference, args.comparison_profile))
                save(out / "batch-report.json", results)
                if results[-1]["status"] == "paused":
                    return 75
            print(json.dumps(dict(Counter(row["status"] for row in results)), ensure_ascii=False))
        return int(not all(row["package_passed"] for row in results))
    except (RuntimeUnavailable, OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, f"error: {exc}\n")


def main():
    try:
        with ExitStack() as guards:
            return _main(guards)
    except PipelinePaused as exc:
        print(str(exc), file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
