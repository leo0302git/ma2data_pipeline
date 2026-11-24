"""Regression probes for the Agent handoffs, using only synthetic data.

Docker is replaced with an in-process fixture. The real pinned static checker
and native --format-only entry point are exercised separately.
"""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline as pipeline_module

from agent import stage
from common import digest, load, save, file_sha
from pipeline import build_plan, main, process_job, calibration
from reference import NatureGym
from runtime import RuntimeUnavailable, Defect
from sources import COMPLETE
from verification import CHECK_IDS, report


SEED = {"id": "mlebench/test-seed", "revision": "test-v1", "benchmark": "MLE-bench",
        "url": "https://example.org/test-seed", "groups": [["数学", "测试"]], "file_suffixes": [".csv"],
        "contract": "Synthetic fixture for one file output"}
MANIFEST = {"schema_version": 1, "tool_records": {}, "evaluation_items": [{
    "id": "E1", "name": "Exact result", "method": "program", "category": "goal_achievement", "scope": "instance",
    "result": {"type": "number", "description": "Exact-match indicator", "unit": None, "direction": "higher_is_better",
               "min": 0, "max": 1}, "validity_item_ids": [], "deliverable_roles": ["result"], "input_roles": ["problem"],
    "reference_roles": ["target"]}], "instances": [{"name": "instance_a", "evaluation_items": ["E1"],
        "solver_data": {"problem": "input/data/input.csv"}, "inputs": {"problem": "input/data/input.csv"},
        "references": {"target": "evaluation/assets/expected.txt"}, "deliverables": {"result": "result.txt"}}]}
README = """# Synthetic sum

## Scientific Problem
Use a small array to exercise scoring.
## Task Objective
Return the sum of the integers.
## Instances and Inputs
Read `data/input.csv` for instance_a.
## Evaluation
The result is compared for exact agreement.
## Deliverables
Submit one text file.
## Submission Interface
Write one integer to `instance_a/result.txt`.
"""
DESCRIPTION = """# Synthetic sum data
## Directory Structure
The input table is `data/input.csv`.
## Dataset Overview
One numeric instance is provided.
## File Formats & Schemas
The `x` column contains integers.
## Special Notes
No external dependencies are required.
"""
METRICS = '''class SubmissionError(Exception):
    pass

def validate_format(instance, role, path):
    try:
        int(path.read_text(encoding="utf-8").strip())
    except ValueError as error:
        raise SubmissionError("Expected one integer") from error

def prepare(item, instances, context):
    return {}

def evaluate(item, instances, context):
    name = instances[0] if isinstance(instances[0], str) else instances[0]["name"]
    expected = context.resource(name, "target", reference=True).read_text().strip()
    received = context.artifact(name, "result").read_text().strip()
    return float(int(expected) == int(received))
'''


def metadata():
    compute = {"cpu_compute": {"quantity": 1, "unit": "physical_core", "severity": "low"},
               "memory": {"quantity": 1, "unit": "GiB", "severity": "low"},
               "gpu_compute": {"count": 0, "severity": "none"},
               "runtime": {"quantity": 0.1, "unit": "h", "severity": "under_1h"},
               "notes": "Synthetic test array checked with one arithmetic operation."}
    return {"task_name": "Synthetic sum", "tooling_metadata": {"main_tools": []},
            "domain_metadata": {"primary_domains": ["Mathematics"], "sub_domain": "Arithmetic",
                                "domain_tags": ["sum", "array", "verification"]},
            "compute_resource_requirements": {"solving": compute, "evaluation": compute},
            "source": {"paper_id": "fixture", "title": "Synthetic test evidence"}, "goal": "Return the sum of the integers",
            "task_types": ["prediction"], "evaluation_weights": {"E1": 1.0}, "evaluation_scope_weights": {},
            "evaluation_comparisons": [{"item_id": "E1", "comparison_results": [{"scope": "instance",
                "instances": ["instance_a"], "results": [
                    {"method": "Independent arithmetic reference", "method_role": "paper_method", "variant": None,
                     "is_sota": True, "baseline_roles": [], "result": 1.0,
                     "conditions": None, "source": "Synthetic fixture calculation"},
                    {"method": "Constant incorrect value", "method_role": "baseline", "variant": None,
                     "is_sota": False, "baseline_roles": ["zero"], "result": 0.0,
                     "conditions": None, "source": "Synthetic fixture calculation"}]}]}]}


def evidence(cid):
    good = cid == "known"
    valid = cid != "malformed"
    statuses = "completed" if valid else "submission_failed"
    return {"format_checks": {"instance_a": {"result": {"status": statuses, "error": None}}}, "error": None,
            "evaluation_results": [{"item_id": "E1", "instances": ["instance_a"], "status": statuses,
                                    "value": float(good) if valid else None, "accepted": valid}]}


class FixtureAgent:
    identity = {"fixture": 1}

    def __init__(self, broken_first=False):
        self.calls = Counter()
        self.broken_first = broken_first

    def call(self, name, context, _event_log):
        self.calls[name] += 1
        writes = {}
        result = {"summary": "Synthetic fixture output"}
        if name == "seed-select":
            result = {"seed_id": context["candidates"][0]["id"], "reason": "Same fixture contract"}
        elif name == "task-scope":
            assert context['submission_backend']['submitted_program_execution'] is False
            assert context['approved_base_image']==context['runtime_capabilities']['image']
            fields = ("goal", "preserved", "changes", "evaluation_unit", "public_data", "private_data", "submission", "scoring")
            result = {key: "Synthetic scope with source evidence" for key in fields} | {"evidence_ids": ["D1"]}
        elif name == "task-build":
            writes = {"task/input/README.md": "# Broken fixture\n" if self.broken_first else README,
                      "task/input/data_description.md": DESCRIPTION, "task/task.json": json.dumps(MANIFEST),
                      "task/task.source.toml": 'schema_version = "1.4"\n[task]\nname = "naturebench/' +
                      context["case_id"] + '"\ndescription = "Synthetic sum"\n',
                      "task/evaluation/metrics.py": METRICS, "task/environment/Dockerfile": "FROM python:3.12-slim\n",
                      "task/environment/packages.json": '{"from_base":false,"base_packages":[],"task_packages":[]}',
                      "task/environment/check_resources.sh": 'test -f /task/input/data/input.csv\n',
                      "task/environment/check_resources.eval.sh": 'test -f /task/evaluation/assets/expected.txt\n',
                      "work/prepare.py": "# The fixture runtime supplies the synthetic data.\n"}
        elif name == "task-fix":
            assert not context.get("unverified_checks"), "Unrun future checks are not completed-review evidence gaps"
            if not any(row["check_id"] == "C3.3" for row in context["failed_checks"]):
                raise AssertionError(context["failed_checks"])
            writes = {"task/input/README.md": README}
        elif name == "task-metadata":
            assert context['current_boundary_check']['status']=='pass'
            assert 'failed_checks' not in context, 'Historical failures must not masquerade as current blockers'
            writes = {"task/metadata.json": json.dumps(metadata())}
        elif name == "verification-cases":
            if 'verification_preparation_failure' in context:
                assert 'int64' in context['verification_preparation_failure']['log_tail'] or 'still fails' in context['verification_preparation_failure']['log_tail']
            writes = {"work/make_cases.py": "# Private test fixtures are provided by FixtureRuntime.\n"}
        elif name == "task-verify":
            if len(context["dynamic_cases"]) != 3:
                raise AssertionError("Independent reviewer needs actual test rows from its runner")
            result = {"checks": [{"check_id": cid, "status": "pass", "issue": "Synthetic fixture check",
                                  "location": "verification/observations.json"} for cid in CHECK_IDS]}
        else:
            raise AssertionError(name)
        return {"status": "done", "reason": "fixture", "read": [],
                "write": [{"path": path, "content": text} for path, text in writes.items()],
                "result_json": json.dumps(result)}


class FixtureRuntime:
    base, timeout, memory, cpus = "python:3.12-slim", 30, "4g", 2

    def __init__(self, unavailable=False):
        self.unavailable = unavailable

    def available(self, folder):
        if self.unavailable:
            raise RuntimeUnavailable("Fixture simulates missing Docker")

    def build(self, package, folder):
        return {"solver": "fixture-image", "evaluator": "fixture-image"}

    def prepare(self, image, script, files, frozen, package, folder, records):
        (package / "input/data").mkdir(parents=True)
        (package / "evaluation/assets").mkdir(parents=True)
        (package / "input/data/input.csv").write_text("x\n1\n2\n", encoding="utf-8")
        (package / "evaluation/assets/expected.txt").write_text("3\n", encoding="utf-8")

    def checks(self, package, images, folder):
        pass

    def run(self, image, entry, args, mounts, log):
        output = next(path for path, destination, _ in mounts if destination == "/out")
        cases = []
        for cid, kind in (("known", "known_correct"), ("wrong", "valid_wrong"), ("malformed", "invalid")):
            folder = output / "submissions" / cid / "instance_a"
            folder.mkdir(parents=True)
            (folder / "result.txt").write_text({"known": "3", "wrong": "0", "malformed": "?"}[cid], encoding="utf-8")
            observed = evidence(cid)
            cases.append({"id": cid, "kind": kind, "method": "Independent fixture calculation", "formats": [
                {"instance": "instance_a", "role": "result", "status": observed["format_checks"]["instance_a"]["result"]["status"]}],
                "scores": observed["evaluation_results"]})
        save(output / "cases.json", {"cases": cases})
        save(output / 'source_audit.json', {'checks':[{'id':cid,'status':'pass',
             'method':'Synthetic fixture runtime comparison','observations':{'fixture':True}}
             for cid in ('source_fidelity','reference_integrity','public_payload_boundary')]})
        return 0

    def evaluate(self, image, package, submission, result, *, format_only):
        observed = evidence(submission.name)
        if format_only:
            observed["evaluation_results"] = []
        save(result / "score.json", observed)
        return int(submission.name == "malformed"), observed


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="naturegym-rewrite-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        self.source = self.raw / "input.csv"
        self.source.write_text("x,answer\n1,3\n2,3\n", encoding="utf-8")
        self.row = {"source_id": "fixture-source-id", "resource_id": "resource-1", "domain": "数学", "category": "测试",
                    "download_status": COMPLETE, "paths": ["input.csv"]}
        self.agent = FixtureAgent()
        self.bindings = {}
        self.plan = build_plan([self.row], {SEED["id"]: SEED}, self.bindings, self.agent, self.root)
        self.job = self.plan["jobs"][0]

    def test_same_group_reuses_seed_and_rejects_seed_drift(self):
        second = self.row | {"source_id": "fixture-source-2", "resource_id": "resource-2"}
        plan = build_plan([self.row, second], {SEED["id"]: SEED}, self.bindings, self.agent, self.root)
        self.assertEqual(len(plan["jobs"]), 2)
        self.assertEqual({job["seed_sha256"] for job in plan["jobs"]}, {digest(SEED)})
        self.assertEqual(self.agent.calls["seed-select"], 1)
        changed = SEED | {"revision": "changed"}
        plan = build_plan([self.row], {SEED["id"]: changed}, self.bindings, self.agent, self.root)
        self.assertEqual(plan["summary"], {"needs_review": 1})

    def test_default_registry_reuses_seed_across_different_batch_outputs(self):
        agent = FixtureAgent()
        with patch.object(pipeline_module, 'HERE', self.root), \
             patch.object(pipeline_module, 'read_inventory', return_value=[self.row]), \
             patch.object(pipeline_module, 'read_seeds', return_value={SEED['id']: SEED}), \
             patch.object(pipeline_module, 'Codex', return_value=agent):
            for name in ('batch-one', 'batch-two'):
                args = ['pipeline.py', 'plan', '--output', str(self.root/name/'plan.json'),
                        '--account-pool', str(self.root/'fixture-pool.json'), '--model', 'fixture',
                        '--no-seed-discovery']
                with patch.object(sys, 'argv', args):
                    self.assertEqual(main(), 0)
                self.assertTrue((self.root/name/'plan.json').is_file())
        self.assertEqual(agent.calls['seed-select'], 1)
        registry = load(self.root/'group_seed_bindings.json')
        self.assertEqual(len(registry['groups']), 1)
        self.assertEqual(next(iter(registry['groups'].values()))['seed_id'], SEED['id'])

    def test_agent_cannot_write_outside_stage(self):
        class Bad:
            def call(self, name, context, path):
                return {"status": "done", "reason": "test", "read": [],
                        "write": [{"path": "../outside.txt", "content": "outside"}], "result_json": "{}"}
        with self.assertRaises(ValueError):
            stage(Bad(), "task-build", {}, self.root, lambda p: True, lambda p: True, self.root / "events")
        self.assertFalse((self.root.parent / "outside.txt").exists())

    def test_group_seed_is_reused_across_source_encodings_for_scientific_scope_review(self):
        agent, bindings = FixtureAgent(), {}
        first = self.row | {'paths': ['observations.parquet']}
        plan = build_plan([first], {SEED['id']: SEED}, bindings, agent, self.root)
        self.assertEqual(plan['summary'], {'planned': 1})
        second = self.row | {'source_id': 'another-encoding', 'paths': ['observations.zip']}
        later = build_plan([second], {SEED['id']: SEED}, bindings, agent, self.root)
        self.assertEqual(later['summary'], {'planned': 1})
        self.assertEqual(later['jobs'][0]['seed_sha256'], plan['jobs'][0]['seed_sha256'])
        self.assertEqual(agent.calls['seed-select'], 1)

    def test_agent_reads_source_profile_without_opening_raw_bytes(self):
        raw = self.root / "source" / "sample.csv"
        raw.parent.mkdir()
        raw.write_text("hidden-answer-123", encoding="utf-8")

        class Reader:
            def call(self, name, context, path):
                if context["step"] == 1:
                    return {"status": "continue", "reason": "read", "read": ["/source/sample.csv"],
                            "write": [], "result_json": ""}
                observed = context["read_results"]["source/sample.csv"]
                assert "hidden-answer-123" not in observed and json.loads(observed)["source_profile"]["sample_rows"] == 4
                return {"status": "done", "reason": "profile read", "read": [], "write": [], "result_json": "{}"}

        result = stage(Reader(), "task-build", {"source_evidence": [
            {"mount": "/source/sample.csv", "profile": {"sample_rows": 4}}]}, self.root,
            lambda p: False, lambda p: False, self.root / "events")
        self.assertEqual(result, {})

    def test_review_without_actual_cases_cannot_pass(self):
        review = {"checks": [{"check_id": cid, "status": "pass", "issue": "test", "location": "test"} for cid in CHECK_IDS]}
        self.assertEqual(report(review)["overall_status"], "not_verified")

    def test_long_calibration_explanation_is_preserved(self):
        records = self.root/'calibration-records'
        rows = [{'id':'E1.reference', 'method':'Independently validated feasible comparator',
                 'value':1.0, 'conditions':'Measured source-specific conditions; '*25}]
        save(records/'calibration.json', rows)
        self.assertEqual(calibration(records), rows)

    def test_invalid_calibration_values_have_actionable_repair_checks(self):
        records = self.root/'bad-calibration';records.mkdir()
        for value in [float('nan'), True, None, '1.0']:
            with self.subTest(value=value):
                (records/'calibration.json').write_text(json.dumps([{'id':'E1', 'method':'Actual observation',
                    'value':value, 'conditions':'Controlled run'}]))
                with self.assertRaisesRegex(Defect, 'value must be a finite number') as caught:
                    calibration(records)
                self.assertEqual(caught.exception.check, 'C3.1')

    def test_oversized_calibration_text_is_bounded_and_named(self):
        records = self.root/'large-calibration'
        save(records/'calibration.json', [{'id':'E1', 'method':'Actual observation',
             'value':1.0, 'conditions':'x'*4097}])
        with self.assertRaisesRegex(Defect, r'conditions length 4097 exceeds 4096'):
            calibration(records)

    def test_recovered_draft_cannot_cross_source_identity(self):
        out = self.root/'wrong-draft'
        case = out/'cases'/self.job['case_id']
        save(case/'authoring-draft.json', {'source_id':'other-source', 'source_sha256':[file_sha(self.source)],
             'upstream_id':SEED['id'], 'copied_files':{}})
        result = process_job(self.job, out, self.raw, '/unused', 2**20, 1,
                             self.agent, FixtureRuntime(), NatureGym())
        self.assertFalse(result['package_passed'])
        self.assertIn('does not match', result['reason'])
        self.assertEqual(self.agent.calls['task-build'], 0)

    def test_valid_draft_still_requires_fresh_model_build_and_verification(self):
        out = self.root/'valid-draft'
        case = out/'cases'/self.job['case_id']
        draft = case/'attempt-01/task/input/README.md'
        draft.parent.mkdir(parents=True)
        draft.write_text('# Unverified older interface\n')
        save(case/'authoring-draft.json', {'source_id':self.row['source_id'],
             'source_sha256':[file_sha(self.source)], 'upstream_id':SEED['id'],
             'copied_files':{'task/input/README.md':file_sha(draft)}})
        result = process_job(self.job, out, self.raw, '/unused', 2**20, 1,
                             self.agent, FixtureRuntime(), NatureGym())
        self.assertTrue(result['package_passed'], result)
        self.assertEqual(self.agent.calls['task-build'], 1)
        self.assertEqual(self.agent.calls['task-metadata'], 1)
        self.assertEqual(self.agent.calls['verification-cases'], 1)
        self.assertEqual(self.agent.calls['task-verify'], 1)

    def test_recovered_draft_rejects_parent_path_before_reading_it(self):
        out = self.root/'escaping-draft'
        case = out/'cases'/self.job['case_id']
        save(case/'authoring-draft.json', {'source_id':self.row['source_id'],
             'source_sha256':[file_sha(self.source)], 'upstream_id':SEED['id'],
             'copied_files':{'task/evaluation/lib/../../../../outside.py':'0'*64}})
        result = process_job(self.job, out, self.raw, '/unused', 2**20, 1,
                             self.agent, FixtureRuntime(), NatureGym())
        self.assertFalse(result['package_passed'])
        self.assertIn('Path must stay', result['reason'])
        self.assertEqual(self.agent.calls['task-build'], 0)

    def test_scope_can_read_recovered_interface_without_writing_it(self):
        class InspectDraft(FixtureAgent):
            def call(self, name, context, event_log):
                if name == 'task-scope' and self.calls[name] == 0:
                    self.calls[name] += 1
                    return {'status':'continue','reason':'Inspect the existing interface',
                            'read':['attempt-01/task/task.json'],'write':[], 'result_json':'{}'}
                if name == 'task-scope':
                    actual = context['read_results']['attempt-01/task/task.json']
                    assert json.loads(actual) == MANIFEST, actual
                return super().call(name, context, event_log)
        out = self.root/'scope-reads-draft'
        case = out/'cases'/self.job['case_id']
        draft = case/'attempt-01/task/task.json'
        save(draft, MANIFEST)
        save(case/'authoring-draft.json', {'source_id':self.row['source_id'],
             'source_sha256':[file_sha(self.source)], 'upstream_id':SEED['id'],
             'copied_files':{'task/task.json':file_sha(draft)}})
        agent = InspectDraft()
        result = process_job(self.job, out, self.raw, '/unused', 2**20, 1,
                             agent, FixtureRuntime(), NatureGym())
        self.assertTrue(result['package_passed'], result)
        self.assertEqual(agent.calls['task-scope'], 2)

    def test_independent_preparation_is_repaired_without_reauthoring_candidate(self):
        class FailOnce(FixtureRuntime):
            def __init__(self):
                super().__init__()
                self.calls = 0
            def run(self, image, entry, args, mounts, log):
                self.calls += 1
                if self.calls == 1:
                    log.write_text('TypeError: Object of type int64 is not JSON serializable\n')
                    return 1
                return super().run(image, entry, args, mounts, log)
        runtime = FailOnce()
        result = process_job(self.job, self.root/'control-repair', self.raw, '/unused', 2**20, 1,
                             self.agent, runtime, NatureGym())
        self.assertTrue(result['package_passed'], result)
        self.assertEqual(result['attempt'], 1)
        self.assertEqual(self.agent.calls['task-build'], 1)
        self.assertEqual(self.agent.calls['task-fix'], 0)
        self.assertEqual(self.agent.calls['verification-cases'], 2)
        attempt = self.root/'control-repair/cases'/result['case_id']/'attempt-01'
        archive, = attempt.glob('verification-preparation-failed-*')
        self.assertIn('int64', (archive/'make-cases.log').read_text())
        self.assertTrue((archive/'make_cases.py').is_file())
        self.assertEqual((attempt/'task/evaluation/metrics.py').read_text(), METRICS)

    def test_repeated_independent_preparation_failure_stays_failed(self):
        class AlwaysFail(FixtureRuntime):
            def run(self, image, entry, args, mounts, log):
                log.write_text('Preparation still fails\n')
                return 1
        out = self.root/'control-fails'
        result = process_job(self.job, out, self.raw, '/unused', 2**20, 1,
                             self.agent, AlwaysFail(), NatureGym())
        self.assertFalse(result['package_passed'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.agent.calls['verification-cases'], 2)
        self.assertEqual(self.agent.calls['task-fix'], 0)
        report = load(out/'cases'/result['case_id']/'attempt-01/task_verify_result.json')
        self.assertIn('C4.2', {r['check_id'] for r in report['failed_checks']})

    def test_scientific_source_audit_failure_is_not_rewritten_as_preparation_repair(self):
        class SourceMismatch(FixtureRuntime):
            def run(self, image, entry, args, mounts, log):
                code = super().run(image, entry, args, mounts, log)
                output = next(path for path, destination, _ in mounts if destination == '/out')
                audit = load(output/'source_audit.json')
                audit['checks'][0]['status'] = 'failed'
                save(output/'source_audit.json', audit)
                return code
        out = self.root/'source-mismatch'
        result = process_job(self.job, out, self.raw, '/unused', 2**20, 1,
                             self.agent, SourceMismatch(), NatureGym())
        self.assertFalse(result['package_passed'])
        self.assertEqual(self.agent.calls['verification-cases'], 1)
        report = load(out/'cases'/result['case_id']/'attempt-01/task_verify_result.json')
        self.assertIn('C1.5', {r['check_id'] for r in report['failed_checks']})

    def test_completed_review_with_missing_controls_is_repaired_before_release(self):
        class EvidenceAgent(FixtureAgent):
            def call(self, name, context, event_log):
                if name == "task-fix":
                    self.calls[name] += 1
                    assert context["failed_checks"] == []
                    assert context["unverified_checks"][0]["check_id"] == "C4.8"
                    return {"status":"done","reason":"Add missing real controls","read":[],"write":[],
                            "result_json":json.dumps({"summary":"Preserve scoring; regenerate missing controls",
                                                      "fixed_checks":["C4.8"]})}
                response = super().call(name, context, event_log)
                if name == "task-verify" and self.calls[name] == 1:
                    review = json.loads(response["result_json"])
                    row = next(row for row in review["checks"] if row["check_id"] == "C4.8")
                    row.update(status="not_verified", issue="A targeted scientific branch needs executed evidence")
                    response["result_json"] = json.dumps(review)
                return response
        agent=EvidenceAgent();out=self.root/"evidence-repair"
        result=process_job(self.job,out,self.raw,"/unused",2**20,2,agent,FixtureRuntime(),NatureGym())
        self.assertTrue(result["package_passed"],result)
        self.assertEqual(result["attempt"],2)
        first=load(out/"cases"/result["case_id"]/"attempt-01/task_verify_result.json")
        self.assertEqual(first["overall_status"],"not_verified")
        self.assertEqual(agent.calls["task-fix"],1)
        self.assertEqual(agent.calls["verification-cases"],2)
        self.assertEqual(agent.calls["task-verify"],2)

    def test_no_docker_keeps_draft_and_resumes_without_reauthoring(self):
        reference = NatureGym()
        runtime = FixtureRuntime(unavailable=True)
        out = self.root / "runs"
        run = lambda: process_job(self.job, out, self.raw, "/unused", 2**20, 2, self.agent, runtime, reference)
        self.assertEqual(run()["status"], "not_verified")
        self.assertFalse((out / "passed").exists())
        runtime.unavailable = False
        result = run()
        self.assertTrue(result["package_passed"], result)
        self.assertEqual(self.agent.calls["task-scope"], 1)
        self.assertEqual(self.agent.calls["task-build"], 1)
        self.assertEqual(self.agent.calls["task-verify"], 1)
        self.assertTrue(run()["package_passed"])
        self.assertEqual(self.agent.calls["task-verify"], 1)
        self.source.write_text("x,answer\n0,0\n", encoding="utf-8")
        self.assertFalse(run()["package_passed"])

    def test_pause_during_build_preserves_state_and_resumes_same_identity(self):
        from operations import execution_guard, set_pause
        root, original = self.root, self.agent.call
        should_pause = True
        def call(name, context, event_log):
            nonlocal should_pause
            action = original(name, context, event_log)
            if name == 'task-build' and should_pause:
                should_pause = False
                set_pause(root, None, paused=True, reason='Synthetic interruption')
            return action
        self.agent.call = call
        out = root / 'interrupted'
        runtime, reference = FixtureRuntime(), NatureGym()
        run = lambda: process_job(self.job, out, self.raw, '/unused', 2**20, 2, self.agent, runtime, reference)
        with execution_guard(root, out):
            result = run()
        self.assertEqual(result['status'], 'paused')
        self.assertFalse(result['package_passed'])
        recipe = load(out/'cases'/result['case_id']/'state.json')['recipe']
        set_pause(root, None, paused=False, reason='Resume synthetic test only')
        with execution_guard(root, out):
            result = run()
        self.assertTrue(result['package_passed'], result)
        self.assertEqual(load(out/'cases'/result['case_id']/'state.json')['recipe'], recipe)
        self.assertEqual(self.agent.calls['task-scope'], 1)
        # A completed run is validated and returned without any further model call.
        before = self.agent.calls.copy()
        with execution_guard(root, out), patch.object(runtime, 'build', side_effect=AssertionError('must not rebuild')):
            self.assertTrue(run()['package_passed'])
        self.assertEqual(self.agent.calls, before)

    def test_native_static_checker_triggers_repair_and_format_contract(self):
        self.agent.broken_first = True
        out = self.root / "runs"
        plan_path = self.root / "plan.json"
        save(plan_path, self.plan)
        argv = ["pipeline.py", "run", "--plan", str(plan_path), "--data-root", str(self.raw),
                "--run-dir", str(out), "--account-pool", str(self.root / "accounts.json"), "--model", "fixture"]
        with (patch.object(sys, "argv", argv), patch.object(sys, "stdout", io.StringIO()),
              patch("pipeline.Codex", return_value=self.agent), patch("pipeline.Docker", return_value=FixtureRuntime())):
            exit_code = main()
        result, = load(out / "batch-report.json")
        self.assertTrue(result["package_passed"], result)
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["attempt"], 2)
        self.assertEqual(self.agent.calls["task-fix"], 1)
        case = out / "cases" / self.job["case_id"]
        failed = load(case / "attempt-01/task_verify_result.json")
        self.assertIn("C3.3", {row["check_id"] for row in failed["failed_checks"]})
        package = out / result["package"] / "task"
        submission = self.root / "probe" / "instance_a"
        submission.mkdir(parents=True)
        artifact = submission / "result.txt"
        for value, expected in (("3", "completed"), ("?", "submission_failed")):
            artifact.write_text(value, encoding="utf-8")
            run = subprocess.run([sys.executable, str(package / "evaluation/evaluator.py"), "--format-only",
                                  "--submission-dir", str(submission.parent)], capture_output=True,
                                 encoding="utf-8", timeout=25)
            raw = json.loads(run.stdout)
            self.assertEqual(raw["format_checks"]["instance_a"]["result"]["status"], expected)
            self.assertEqual(raw["evaluation_results"], [])

        launcher = """import os, runpy, sys
sys.argv = sys.argv[1:]
main = runpy.run_path(sys.argv[0])['main']
if os.name == 'nt':
    # The scoring computation is platform-neutral. Windows does not expose
    # the POSIX directory permissions of the Docker execution environment.
    main.__globals__['check_private_directory'] = lambda path: None
raise SystemExit(main())
"""
        for value, expected in (("3", 1.0), ("0", 0.0)):
            artifact.write_text(value, encoding="utf-8")
            run = subprocess.run([sys.executable, "-X", "utf8", "-c", launcher, str(package / "evaluation/evaluator.py"),
                                  "--submission-dir", str(submission.parent), "--work-dir", str(self.root / "private-work"),
                                  "--exec-work-dir", str(self.root / "candidate-work"), "--exec-uid", "22001",
                                  "--exec-gid", "22001", "--data-root", str(package), "--tool-records-dir",
                                  str(self.root / "records"), "--max-workers", "1"], capture_output=True,
                                 encoding="utf-8", timeout=25)
            raw = json.loads(run.stdout)
            self.assertEqual(run.returncode, 0, raw)
            self.assertEqual(raw["evaluation_results"][0]["value"], expected)


if __name__ == "__main__":
    unittest.main()
