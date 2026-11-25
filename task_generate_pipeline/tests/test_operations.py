import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pipeline
from agent import stage
from common import file_sha, load, save, tree_sha
from operations import (PipelinePaused, audit_preservation, checkpoint, execution_guard,
                        pause_status, project_root, recovery_report, set_pause, registry_paths)
from runtime import run_command


class OperationsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_project_pause_blocks_new_run_before_account_or_docker_access(self):
        set_pause(self.root, None, paused=True, reason="Operator requested pause")
        args = ["pipeline.py", "run", "--project-root", str(self.root), "--plan", "missing.json",
                "--data-root", "missing-data", "--run-dir", str(self.root / "new-run")]
        with patch("sys.argv", args), patch.object(pipeline, "Codex") as model, patch.object(pipeline, "Docker") as docker:
            with patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(pipeline.main(), 75)
        model.assert_not_called()
        docker.assert_not_called()
        self.assertFalse((self.root / "new-run").exists())

    def test_preview_allowed_while_paused_without_creating_binding_lock(self):
        set_pause(self.root, None, paused=True, reason="Hold")
        bindings = self.root / "bindings.json"
        save(bindings, {"schema": 1, "kind": pipeline.PLAN_KIND, "groups": {}})
        before = bindings.read_bytes()
        args = ["pipeline.py", "plan", "--preview", "--project-root", str(self.root),
                "--bindings", str(bindings), "--output", str(self.root / "preview.json")]
        with patch("sys.argv", args), patch("sys.stdout", new_callable=io.StringIO), patch.object(pipeline, "Codex") as model:
            self.assertEqual(pipeline.main(), 0)
        model.assert_not_called()
        self.assertEqual(bindings.read_bytes(), before)
        self.assertFalse(bindings.with_name("bindings.json.lock").exists())

    def test_new_process_can_infer_project_root_from_deployment_marker(self):
        save(self.root / ".pipeline-project.json", {"schema": 1})
        nested = self.root / "versions/v32"
        nested.mkdir(parents=True)
        self.assertEqual(project_root(nested), self.root)

    def test_parent_pause_cannot_be_bypassed_by_clearing_child(self):
        out = self.root / "runs/batch/job"
        set_pause(self.root, None, paused=True, reason="Project hold")
        set_pause(self.root, out, paused=True, reason="Run hold")
        result = set_pause(self.root, out, paused=False, reason="Clear run-level hold only")
        self.assertTrue(result["paused"])
        with self.assertRaises(PipelinePaused), execution_guard(self.root, out):
            pass

    def test_mid_stage_pause_rejects_model_edits_before_they_are_applied(self):
        root = self.root
        class Agent:
            def call(self, *args):
                set_pause(root, None, paused=True, reason="Stop during model response")
                return {"status": "done", "reason": "fixture", "read": [],
                        "write": [{"path": "candidate.txt", "content": "must not apply"}], "result_json": "{}"}
        with execution_guard(root):
            with self.assertRaises(PipelinePaused):
                stage(Agent(), "fixture", {}, root, lambda p: False, lambda p: True, root / "events")
        self.assertFalse((root / "candidate.txt").exists())
        # ContextVar is reset even after a paused stage.
        checkpoint()

    def test_pause_blocks_runtime_subprocess_and_preserves_existing_log(self):
        log = self.root / "runtime.log"
        log.write_text("prior evidence", encoding="utf-8")
        with execution_guard(self.root):
            set_pause(self.root, None, paused=True, reason="Stop before next runtime")
            with patch("runtime.subprocess.run") as run:
                with self.assertRaises(PipelinePaused):
                    run_command(["docker", "version"], log, 1)
            run.assert_not_called()
        self.assertEqual(log.read_text(), "prior evidence")

    def test_recovery_report_checks_published_bytes_and_lists_unstarted_cases(self):
        run = self.root / "run"
        package = run / "passed/case-1"
        save(package / "task/data.json", {"real": "fixture"})
        save(package / "receipt.json", {"package_sha256": tree_sha(package / "task")})
        save(package / "task_verify_result.json", {"overall_status": "pass"})
        save(run / "cases/case-1/state.json", {"source_id": "one", "status": "pass", "package_passed": True,
                                               "package": "passed/case-1"})
        plan = self.root / "plan.json"
        save(plan, {"jobs": [{"case_id": "case-1", "source": {"source_id": "one"}},
                              {"case_id": "case-2", "source": {"source_id": "two"}}]})
        rows = recovery_report(self.root, run, plan)["rows"]
        self.assertTrue(rows[0]["package_verified"])
        self.assertEqual(rows[1]["status"], "not_started")
        save(package / "task/data.json", {"tampered": True})
        row = recovery_report(self.root, run, plan)["rows"][0]
        self.assertEqual(row["action"], "inspect_published_integrity")
        self.assertFalse(row["package_verified"])

    def test_deleted_old_pool_backup_is_not_required_but_raw_and_task_checks_remain(self):
        raw = self.root / "raw.txt"
        raw.write_text("immutable raw", encoding="utf-8")
        package = self.root / "task"
        save(package / "data.json", {"original": True})
        save(self.root / "empty-pool.json", {"accounts": []})
        baseline = {"schema": 1, "kind": "pipeline-preservation", "project_root": str(self.root), "artifacts": [
            {"kind": "file", "role": "source", "path": str(raw), "sha256": file_sha(raw)},
            {"kind": "tree", "role": "task", "path": str(package), "sha256": tree_sha(package)},
            {"kind": "file", "role": "empty_pool", "path": str(self.root / "empty-pool.json"),
             "sha256": file_sha(self.root / "empty-pool.json")}]}
        self.assertEqual(audit_preservation(baseline, self.root)["artifacts"], 3)
        raw.write_text("changed raw", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Preservation failed"):
            audit_preservation(baseline, self.root)
        raw.write_text("immutable raw", encoding="utf-8")
        save(package / "data.json", {"changed": True})
        with self.assertRaisesRegex(ValueError, "Preservation failed"):
            audit_preservation(baseline, self.root)

    def test_missing_preserved_file_is_a_failure(self):
        baseline = {"schema": 1, "kind": "pipeline-preservation", "project_root": str(self.root), "artifacts": [
            {"kind": "file", "path": str(self.root / "missing"), "sha256": "0" * 64}]}
        with self.assertRaisesRegex(ValueError, "Preservation failed"):
            audit_preservation(baseline, self.root)

    def test_missing_empty_tree_is_not_accepted_as_an_existing_empty_tree(self):
        from common import digest
        baseline = {"schema": 1, "kind": "pipeline-preservation", "project_root": str(self.root), "artifacts": [
            {"kind": "tree", "path": str(self.root / "missing-tree"), "sha256": digest({})}]}
        with self.assertRaisesRegex(ValueError, "Preservation failed"):
            audit_preservation(baseline, self.root)

    def test_registered_seed_registry_is_shared_and_never_silently_reinitialized(self):
        catalog = self.root / 'config/seed-registry/seed_catalog.json'
        bindings = catalog.with_name('group_seed_bindings.json')
        save(catalog, {'seeds': ['fixed fixture']})
        save(bindings, {'groups': {'existing': 'fixed fixture'}})
        save(self.root / '.pipeline-project.json', {'registry_dir': 'config/seed-registry'})
        first = registry_paths(self.root, self.root / 'versions/v32')
        second = registry_paths(self.root, self.root / 'versions/v33')
        self.assertEqual(first, second)
        self.assertEqual(first, (catalog, bindings))
        bindings.unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            registry_paths(self.root, self.root / 'versions/v33')

    def test_audit_refuses_to_overwrite_its_baseline(self):
        path = self.root / "baseline.json"
        save(path, {"original": True})
        before = path.read_bytes()
        args = ["pipeline.py", "audit", "--project-root", str(self.root),
                "--baseline", str(path), "--output", str(path)]
        with patch("sys.argv", args), patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as error:
                pipeline.main()
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(path.read_bytes(), before)

    def test_registry_audit_allows_new_groups_but_rejects_existing_group_seed_drift(self):
        from common import digest
        seed = {'id': 'fixed', 'benchmark': 'TB-Science', 'revision': 'fixture',
                'url': 'https://example.test/seed', 'groups': [['math', 'optimization']],
                'file_suffixes': ['.csv'], 'contract': 'Evaluate the submitted solution.'}
        key = '["math","optimization"]'
        binding = {'seed_id': 'fixed', 'seed_sha256': digest(seed)}
        seeds, bindings = self.root/'seeds.json', self.root/'bindings.json'
        save(seeds, {'schema': 1, 'seeds': [seed]})
        save(bindings, {'groups': {key: binding}})
        baseline = {'schema': 1, 'kind': 'pipeline-preservation', 'project_root': str(self.root),
                    'artifacts': [{'kind': 'file', 'path': str(self.root/'anchor'), 'sha256': 'unused'}],
                    'registries': [{'seeds': str(seeds), 'bindings': str(bindings), 'frozen_groups': {key: binding}}]}
        (self.root/'anchor').write_text('immutable')
        baseline['artifacts'][0]['sha256'] = file_sha(self.root/'anchor')
        added = seed | {'id': 'new', 'groups': [['math', 'classification']]}
        save(seeds, {'schema': 1, 'seeds': [seed, added]})
        save(bindings, {'groups': {key: binding, '["math","classification"]': {'seed_id': 'new', 'seed_sha256': digest(added)}}})
        self.assertEqual(audit_preservation(baseline, self.root)['checked']['shared_seed_registry'], 1)
        save(bindings, {'groups': {key: {'seed_id': 'new', 'seed_sha256': digest(added)}}})
        with self.assertRaisesRegex(ValueError, 'existing group seed changed'):
            audit_preservation(baseline, self.root)


if __name__ == "__main__":
    unittest.main()
