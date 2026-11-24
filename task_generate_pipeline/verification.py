"""Run independent test submissions and assemble NatureGym verification receipts."""

import math
import re
import shutil
import tempfile
from pathlib import Path

from common import load, regular_files, save, file_sha
from runtime import Defect, RuntimeUnavailable


CHECK_IDS = tuple(f"C{phase}.{i}" for phase, count in enumerate((5, 9, 6, 7, 9)) for i in range(1, count + 1))
KINDS = {"known_correct", "valid_wrong", "invalid"}


def item_scope(manifest):
    items = {item["id"]: item for item in manifest["evaluation_items"]}
    instances = manifest["instances"]
    scopes = set()
    for item_id, item in items.items():
        groups = item["instance_groups"] if item["scope"] == "joint" else [
            [row["name"]] for row in instances if item_id in row["evaluation_items"]]
        scopes.update((item_id, tuple(sorted(group))) for group in groups)
    formats = {(row["name"], role) for row in instances for role in row["deliverables"]}
    return items, scopes, formats


def score_rows(rows):
    result = {}
    for row in rows:
        key = (row["item_id"], tuple(sorted(row["instances"])))
        status, value, accepted = row["status"], row["value"], row.get("accepted")
        if (key in result or status not in {"completed", "submission_failed"}
                or (status == "completed" and not (value is None and accepted is False)
                    and (type(value) not in {int, float, bool} or not math.isfinite(value)))
                or (status == "submission_failed" and value is not None)
                or (accepted is not None and type(accepted) is not bool)):
            raise ValueError("Invalid, incomplete, or duplicate evaluator score row")
        result[key] = (status, value, accepted)
    return result


def observed_format(report):
    return {(instance, role): row["status"] for instance, roles in report["format_checks"].items()
            for role, row in roles.items()}


def same_score(actual, expected):
    a_status, a_value, a_accept = actual
    e_status, e_value, e_accept = expected
    if (a_status, a_accept) != (e_status, e_accept):
        return False
    if a_value is None or e_value is None or type(a_value) is bool or type(e_value) is bool:
        return a_value is e_value
    return math.isclose(a_value, e_value, rel_tol=1e-8, abs_tol=1e-8)


def check_cases(cases, manifest):
    if not isinstance(cases, list) or not 3 <= len(cases) <= 32:
        raise ValueError("Provide 3–32 independent verification cases")
    items, scopes, expected_roles = item_scope(manifest)
    if not scopes or not expected_roles:
        raise ValueError("The evaluator needs assessment scopes and deliverables")
    checked = []
    seen = set()
    for case in cases:
        cid, kind = case["id"], case["kind"]
        if (not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,35}", cid) or cid in seen or kind not in KINDS
                or not isinstance(case["method"], str) or not case["method"].strip()
                or len(case["method"]) > 1000):
            raise ValueError("Invalid or duplicate verification case")
        seen.add(cid)
        formats = {(row["instance"], row["role"]): row["status"] for row in case["formats"]}
        if (len(formats) != len(case["formats"]) or set(formats) != expected_roles
                or set(formats.values()) - {"completed", "submission_failed"}):
            raise ValueError("A verification case must cover every format role")
        scores = score_rows(case["scores"])
        if set(scores) != scopes:
            raise ValueError("A verification case must cover every score scope")
        if (kind == "invalid" and "submission_failed" not in formats.values()
                and not any(score[0] == "submission_failed" for score in scores.values())):
            raise ValueError("An invalid case must exercise a confirmed format or scoring content rejection")
        if kind != "invalid" and set(formats.values()) != {"completed"}:
            raise ValueError("Known correct and valid-wrong cases need valid artifacts")
        if kind == "known_correct" and any(score[0] != "completed" or score[2] is False for score in scores.values()):
            raise ValueError("Known-correct expectations must exercise successful scoring")
        checked.append((case, formats, scores))
    if {case["kind"] for case, _, _ in checked} != KINDS:
        raise ValueError("Include known-correct, valid-wrong, and invalid verification cases")
    positive = [scores for case, _, scores in checked if case["kind"] == "known_correct"]
    negative = [scores for case, _, scores in checked if case["kind"] == "valid_wrong"]
    for key in scopes:
        if items[key[0]]["category"] == "goal_achievement" and not any(
            not same_score(good[key], bad[key]) for good in positive for bad in negative
        ):
            raise ValueError("Known-correct and valid-wrong controls must distinguish every goal scope")
    return checked


def run_cases(runtime, image, package, program, root, source_files=(), frozen=()):
    try:
        return _run_cases(runtime, image, package, program, root, source_files, frozen)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, (Defect, RuntimeUnavailable)):
            raise
        raise Defect("C4.1", f"Invalid verification case or result contract: {exc}", "verification/") from exc


def _run_cases(runtime, image, package, program, root, source_files=(), frozen=()):
    inputs = root / "inputs"
    inputs.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='source-audit-',dir=root) as temp:
        mounts=[(program, '/program/make_cases.py', True),(package,'/task',True),(inputs,'/out',False)]
        for index,(source,row) in enumerate(zip(source_files,frozen)):
            if file_sha(source)!=row['sha256']:
                raise RuntimeUnavailable('Source changed before independent source audit')
            target=Path(temp)/str(index);shutil.copyfile(source,target);target.chmod(0o600)
            if file_sha(target)!=row['sha256']:raise RuntimeUnavailable('Independent source staging checksum mismatch')
            mounts.append((target,row['mount'],True))
        code=runtime.run(image,'python',['/program/make_cases.py'],mounts,root/'make-cases.log')
        for source,row in zip(source_files,frozen):
            if file_sha(source)!=row['sha256']:raise RuntimeUnavailable('Source changed during independent source audit')
    if code:
        raise Defect("C4.2", f"Independent case preparation exited {code}", "verification/make-cases.log")
    list(regular_files(inputs))
    definitions = inputs / "cases.json"
    if not definitions.is_file() or definitions.stat().st_size > 1024 * 1024:
        raise Defect("C4.2", "Independent case preparation did not produce a bounded cases.json", "verification/inputs/")
    cases = check_cases(load(definitions)["cases"], load(package / "task.json"))
    findings, actual_cases = [], []
    audit = {}
    if source_files:
        path=inputs/'source_audit.json'
        if not path.is_file() or path.stat().st_size>128000:
            raise Defect('C1.5','Independent verifier must emit a bounded source_audit.json','verification/inputs/source_audit.json')
        audit=load(path)
        required={'source_fidelity','reference_integrity','public_payload_boundary'}
        rows=audit.get('checks',[])
        if (not isinstance(rows,list) or {r.get('id') for r in rows if isinstance(r,dict)}!=required
                or len(rows)!=3 or any(r.get('status') not in {'pass','failed','not_verified'}
                    or not isinstance(r.get('method'),str) or not r['method'].strip()
                    or not isinstance(r.get('observations'),dict) or not r['observations'] for r in rows)):
            raise Defect('C1.5','Invalid independent source audit contract','verification/inputs/source_audit.json')
        for row in rows:
            if row['status']=='not_verified':
                raise RuntimeUnavailable('Scientific source audit lacks evidence for '+row['id']+'; see verification/inputs/source_audit.json')
            if row['status']=='failed':findings.append(finding('C1.5',row['id']+': failed','verification/inputs/source_audit.json'))
        save(root/'source-audit-receipt.json',{'program_sha256':file_sha(program),'image':image,
             'sources':[{'mount':r['mount'],'sha256':r['sha256']} for r in frozen],
             'audit_sha256':file_sha(path)})
    baseline = {}
    known_submission = None
    for case, formats, scores in cases:
        cid = case["id"]
        submission = inputs / "submissions" / cid
        if not submission.is_dir():
            raise Defect("C4.2", f"Missing submission directory for {cid}", "verification/inputs/submissions/")
        location = root / cid
        format_code, format_report = runtime.evaluate(image, package, submission, location / "format", format_only=True)
        score_code, score_report = runtime.evaluate(image, package, submission, location / "score", format_only=False)
        if (format_code != int("submission_failed" in formats.values()) or format_report.get("error") is not None
                or format_report["evaluation_results"] or observed_format(format_report) != formats):
            findings.append(finding("C4.3", f"Format-only result differs for {cid}", f"verification/{cid}/format/score.json"))
        try:
            actual = score_rows(score_report["evaluation_results"])
            score_matches = (score_code in {0, 1} and score_report.get("error") is None
                             and observed_format(score_report) == formats and set(actual) == set(scores)
                             and all(same_score(actual[key], scores[key]) for key in scores))
        except ValueError:
            score_matches = False
        if not score_matches:
            findings.append(finding("C4.8", f"Native scoring differs from independently expected outcomes for {cid}",
                                    f"verification/{cid}/score/score.json"))
        if not baseline and case["kind"] == "known_correct" and score_matches:
            baseline = score_report
            known_submission = submission
        actual_cases.append({"id": cid, "kind": case["kind"], "method": case["method"],
                             "expected": {"formats": case["formats"], "scores": case["scores"]},
                             "observed": {"format_exit": format_code, "score_exit": score_code,
                                          "formats": [{"instance": i, "role": r, "status": s}
                                                      for (i, r), s in observed_format(score_report).items()],
                                          "scores": [{key: row.get(key) for key in ("item_id", "instances", "status", "value", "accepted")}
                                                     for row in score_report["evaluation_results"]]},
                             "status": "pass" if score_matches and not any(
                                 item["location"].startswith(f"verification/{cid}/") for item in findings) else "failed",
                             "evidence": f"verification/{cid}/score/score.json"})
    relocation = {}
    if known_submission is not None and hasattr(runtime,'evaluate_relocated'):
        exit_code,actual=runtime.evaluate_relocated(image,package,known_submission,root/'relocated/score')
        same = (exit_code==0 and actual.get('error') is None
                and observed_format(actual)==observed_format(baseline)
                and score_rows(actual['evaluation_results'])==score_rows(baseline['evaluation_results']))
        relocation={'status':'pass' if same else 'failed','evidence':'verification/relocated/score/score.json',
                    'working_directory':'/tmp','public_data_root':'/public-data','evaluation_code_root':'/task'}
        if not same:findings.append(finding('C4.7','Relocated public data changes evaluation outcomes',relocation['evidence']))
    receipt = {"cases": actual_cases, "baseline": baseline, "findings": findings,
               'source_audit':audit,'relocated_public_data':relocation}
    save(root / "observations.json", receipt)
    return receipt


def finding(check, reason, location):
    return {"check_id": check, "issue": reason, "location": location}


def report(agent_report=None, failures=(), dynamic=None, blocker=None):
    if dynamic is not None and (not dynamic.get("cases") or not dynamic.get("baseline")):
        dynamic = None
    agent_checks = [] if agent_report is None else agent_report.get("checks", [])
    if agent_report is not None and (not isinstance(agent_checks, list) or len(agent_checks) != len(CHECK_IDS)
                                     or any(not isinstance(row, dict) for row in agent_checks)
                                     or {row.get("check_id") for row in agent_checks} != set(CHECK_IDS)):
        raise ValueError("Independent review must return every one of the 36 NatureGym check IDs")
    states = {row["check_id"]: row for row in agent_checks}
    groups = {"failed": list(failures), "not_verified": [], "warning": []}
    for cid in CHECK_IDS:
        if any(row["check_id"] == cid for row in groups["failed"]):
            continue
        row = states.get(cid)
        if row is None:
            groups["not_verified"].append(finding(cid, blocker or "Independent verification has not completed", "state.json"))
            continue
        status, issue, location = row.get("status"), row.get("issue"), row.get("location")
        if (status not in {"pass", *groups} or not isinstance(issue, str) or not issue.strip()
                or not isinstance(location, str) or not location.strip() or len(issue) > 2000 or len(location) > 400):
            raise ValueError(f"Invalid independent review check {cid}")
        if dynamic is None and cid.startswith("C4.") and status in {"pass", "warning"}:
            groups["not_verified"].append(finding(cid, "No real evaluator run", "verification/"))
        elif status != "pass":
            groups[status].append(finding(cid, issue, location))
    failed = {r["check_id"] for r in groups["failed"]}
    unverified = {r["check_id"] for r in groups["not_verified"]}
    status = "failed" if failed else "not_verified" if unverified else "pass_with_warnings" if groups["warning"] else "pass"
    cases = [] if dynamic is None else dynamic["cases"]
    dynamic_status = ("failed" if any(cid.startswith("C4.") for cid in failed) else "not_verified"
                      if dynamic is None or any(cid.startswith("C4.") for cid in unverified) else "pass")
    correct = [case for case in cases if case["kind"] == "known_correct"]
    return {"path": "task", "overall_status": status, "failed_checks": groups["failed"], "warnings": groups["warning"],
            "unverified_checks": groups["not_verified"], "summary": {"total_checks": 36, "passed": 36 - len(failed) - len(unverified),
                "failed": len(failed), "warnings": len(groups["warning"]), "not_verified": len(unverified)},
            "dynamic_test": {"status": dynamic_status, "baseline_results": {} if dynamic is None else dynamic["baseline"],
                "correctness_test": {"method": "Independent test submissions", "expected": {c["id"]: c["expected"] for c in correct},
                    "actual": {c["id"]: c["observed"] for c in correct}, "status": dynamic_status,
                    "evidence": "verification/observations.json"},
                "robustness_tests": [{"case": c["id"], "expected": c["expected"], "observed": c["observed"],
                    "status": c["status"], "evidence": c["evidence"]} for c in cases if c["kind"] != "known_correct"],
                "error": None if dynamic_status == "pass" else blocker or "Required checks did not pass"}}
