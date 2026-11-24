"""Regressions for the concrete failures seen in the November real-data pilot."""
import json
import sys
import tempfile
import unittest
import subprocess
import shlex
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import stage
from common import file_sha, save
from contracts import diagnostics, evidence_readable
from runtime import Docker
from runtime import Defect
from pipeline import check_boundaries
from verification import score_rows, check_cases


class HandoffTests(unittest.TestCase):
    @unittest.skipUnless(Path('/bin/sh').is_file(), 'Requires the actual POSIX shell invocation')
    def test_resource_checks_receive_scripts_on_stdin_from_tmp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);package = root/'task';env = package/'environment'
            env.mkdir(parents=True);(package/'input').mkdir();(package/'evaluation').mkdir()
            (package/'task.json').write_text('{}')
            (package/'evaluation/evaluator.py').write_text('# role probe')
            save(env/'packages.json', {'base_packages':[], 'task_packages':[]})
            for name in ['check_resources.sh', 'check_resources.eval.sh']:
                (env/name).write_text('test "$0" = /bin/sh || exit 41\ntest "$PWD" = /tmp || exit 42\nprintf "%s\\n" "$0"\n')
            observed = []
            runtime = Docker('fixture-base', 30, '1g', 1)
            def run(image, entry, args, mounts, log):
                if log.name not in {'check-solver.log', 'check-evaluator.log'}:
                    return 0
                script = next(p for p, dst, _ in mounts if dst == '/check.sh')
                if log.name == 'check-solver.log':
                    view = root/'solver-view';view.mkdir()
                else:
                    view = package
                command = re.sub(r'/task(?=/|\s|$)', lambda _: shlex.quote(str(view)), args[1]).replace('/check.sh', shlex.quote(str(script)))
                result = subprocess.run([entry, args[0], command], cwd='/tmp', capture_output=True, text=True, timeout=5)
                observed.append((log.name, result.returncode, result.stdout.strip()))
                return result.returncode
            runtime.run = run
            runtime.checks(package, {'solver':'solver-image', 'evaluator':'evaluator-image'}, root)
            self.assertEqual(observed, [('check-solver.log', 0, '/bin/sh'), ('check-evaluator.log', 0, '/bin/sh')])

    def test_failed_preparation_retains_solver_receipts_without_other_private_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);attempt=root/"attempt";script=attempt/"work/prepare.py"
            script.parent.mkdir(parents=True);script.write_text("pass")
            source=root/"source.dat";source.write_text("source")
            evidence=attempt/"evidence";evidence.mkdir()
            records=attempt/"records";records.mkdir()
            runtime=Docker("python:3.12-slim",30,"1g",1)
            def run(image,entry,args,mounts,log):
                private=next(path for path,target,_ in mounts if target=="/out/private")
                (private/"solve.log").write_text("x"*30000+"\nTime limit reached\n")
                save(private/"solve.receipt.json",{"status":"TimeLimit","bound":172.26,"incumbent":175})
                save(private/"baseline_solve.json",{"return_status":"Maximum_WallTime_Exceeded","wall_seconds":220})
                (private/"oversized_solve.json").write_text("x"*17000)
                (private/"solution.json").write_text("private-answer-not-for-diagnostic-forwarding")
                secret=root/"secret.txt";secret.write_text("outside-private-secret")
                try:(private/"leak.log").symlink_to(secret)
                except OSError:pass
                return 1
            runtime.run=run
            with self.assertRaises(Defect):
                runtime.prepare("image",script,[source],[{"mount":"/source/input","sha256":file_sha(source)}],
                                attempt/"staging",evidence,records)
            result=diagnostics(attempt);text=json.dumps(result)
            self.assertIn("Time limit reached",text)
            self.assertIn("172.26",text)
            self.assertIn("Maximum_WallTime_Exceeded",text)
            self.assertIn("baseline_solve.json",text)
            self.assertNotIn("oversized_solve.json",text)
            self.assertNotIn("private-answer-not-for-diagnostic-forwarding",text)
            self.assertNotIn("outside-private-secret",text)
            paths=[name for name in result if "preparation-diagnostics" in name]
            self.assertTrue(paths)
            self.assertTrue(all(evidence_readable(name) for name in paths))
            self.assertFalse(evidence_readable("evidence/auth.json"))

    def test_large_cache_retains_file_identities_and_working_notes(self):
        class Agent:
            def call(self, name, context, log):
                if context["step"] == 1:
                    return {"status":"continue","reason":"Compare three source-backed contracts",
                            "read":["task/a.txt","task/b.txt","task/c.txt"],"write":[],
                            "result_json":json.dumps({"checked":"Preserve all three evidence identities"})}
                self_context = json.dumps(context, ensure_ascii=False).encode()
                assert len(self_context) < 710 * 1024
                assert set(context["read_results"]) == {"task/a.txt","task/b.txt","task/c.txt"}
                assert set(context["read_history"]) == set(context["read_results"])
                assert context["working_notes"]["checked"] == "Preserve all three evidence identities"
                assert context["action_history"][-1]["read"] == ["task/a.txt","task/b.txt","task/c.txt"]
                assert any("cache_compacted" in value for value in context["read_results"].values())
                assert len(context["read_history"]["task/a.txt"]["sha256"]) == 64
                return {"status":"done","reason":"Compared retained evidence","read":[],"write":[],"result_json":"{}"}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/"task").mkdir()
            for name in ["a","b","c"]:(root/"task"/(name+".txt")).write_text(name*150000)
            self.assertEqual(stage(Agent(),"task-metadata",{"fixed_evidence":"x"*400000},root,
                                   lambda p:p.startswith("task/"),lambda p:False,root/"events"),{})

    def test_boundary_failure_names_counterpart_and_success_has_current_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'input/data').mkdir(parents=True);(root/'evaluation/assets').mkdir(parents=True)
            for name in ('README.md','data_description.md'):(root/'input'/name).write_text('Public documentation')
            public=root/'input/data/problem.json';public.write_text('{"coefficients":[1,2]}')
            private=root/'evaluation/assets/duplicate.json';private.write_bytes(public.read_bytes())
            context={'source_id':'protected-source','seed':{'url':'https://example.org/protected-seed'}}
            with self.assertRaises(Defect) as raised:check_boundaries(root,[],context)
            self.assertIn('evaluation/assets/duplicate.json',str(raised.exception))
            private.write_text('{"answer":3}')
            result=check_boundaries(root,[],context)
            self.assertEqual(result['status'],'pass');self.assertEqual(result['public_file_count'],3)
            self.assertEqual(len(result['public_tree_sha256']),64)

    def test_boundary_reports_all_duplicate_pairs_before_expensive_preparation_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'input/data').mkdir(parents=True);(root/'evaluation/assets').mkdir(parents=True)
            for name,value in (('model.json','{"coefficient":2}'),('schema.json','{"unit":"MW"}')):
                (root/'input/data'/name).write_text(value)
                (root/'evaluation/assets'/name).write_text(value)
            with self.assertRaises(Defect) as raised:
                check_boundaries(root,[],{'source_id':'protected-source','seed':{'url':'https://example.org/seed'}})
            self.assertIn('2 files',str(raised.exception))
            self.assertIn('input/data/model.json',str(raised.exception))
            self.assertIn('evaluation/assets/model.json',str(raised.exception))
            self.assertIn('input/data/schema.json',str(raised.exception))
            self.assertIn('evaluation/assets/schema.json',str(raised.exception))

    def test_relocated_run_separates_public_data_and_private_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);package=root/'task'
            (package/'evaluation/assets').mkdir(parents=True);(package/'input/data').mkdir(parents=True)
            (package/'input/data/input.txt').write_text('public problem')
            (package/'evaluation/assets/reference.txt').write_text('private reference')
            for name in ('metrics.py','evaluator.py'):(package/'evaluation'/name).write_text('pass')
            save(package/'task.json',{'schema_version':1});save(package/'metadata.json',{})
            runtime=Docker('python:3.12-slim',30,'1g',1);result=root/'relocated/score'
            def run(image,entry,args,mounts,log):
                targets={dest:p for p,dest,_ in mounts}
                self.assertEqual(args[args.index('--data-root')+1],'/public-data')
                self.assertEqual(targets['/public-data/input'],package/'input')
                self.assertEqual(targets['/task/evaluation/assets'],package/'evaluation/assets')
                self.assertFalse((targets['/task']/'input/data/input.txt').exists())
                self.assertFalse((targets['/task']/'evaluation/assets/reference.txt').exists())
                save(result/'score.json',{'format_checks':{},'evaluation_results':[],'error':None})
                return 0
            runtime.run=run
            self.assertEqual(runtime.evaluate_relocated('image',package,root/'submission',result)[0],0)

    def test_large_authoring_artifact_returns_hash_and_bounded_actual_text(self):
        class Agent:
            def call(self,name,context,log):
                if context['step']==1:
                    return {'status':'continue','reason':'Inspect real payload','read':['task/input/data/model.mps'],
                            'write':[],'result_json':''}
                result=json.loads(context['read_results']['task/input/data/model.mps'])
                assert result['bounded_excerpt'] and result['head'].startswith('NAME') and result['tail'].endswith('ENDATA')
                assert result['bytes']>192*1024 and len(result['sha256'])==64
                return {'status':'done','reason':'Inspected','read':[],'write':[],'result_json':'{}'}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'task/input/data/model.mps';p.parent.mkdir(parents=True)
            p.write_text('NAME\n'+'x'*250000+'\nENDATA')
            self.assertEqual(stage(Agent(),'task-verify',{},root,lambda p:True,lambda p:False,root/'events'),{})

    def test_fourteen_controls_include_full_scoring_content_rejections(self):
        from test_pipeline import MANIFEST
        def case(cid,kind,score=0,status='completed'):
            return {'id':cid,'kind':kind,'method':'Independent control',
                'formats':[{'instance':'instance_a','role':'result','status':'completed'}],
                'scores':[{'item_id':'E1','instances':['instance_a'],'status':status,
                           'value':None if status=='submission_failed' else score,
                           'accepted':status=='completed'}]}
        controls=[case('correct','known_correct',1),case('wrong','valid_wrong')]
        controls += [case('invalid-%d'%i,'invalid',status='submission_failed') for i in range(12)]
        self.assertEqual(len(check_cases(controls,MANIFEST)),14)
        controls[-1]=case('fake-invalid','invalid')
        with self.assertRaises(ValueError):check_cases(controls,MANIFEST)

    def test_metadata_can_request_the_full_authored_file_inventory(self):
        class Agent:
            def call(self,name,context,log):
                if context['step']==1:
                    return {'status':'continue','reason':'Read metadata inputs','read':[
                        'task/file%d.txt'%i for i in range(16)],'write':[],'result_json':''}
                assert len(context['read_results'])==16
                return {'status':'done','reason':'Read complete','read':[],'write':[],'result_json':'{}'}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'task').mkdir()
            for i in range(16):(root/'task'/('file%d.txt'%i)).write_text('public specification')
            self.assertEqual(stage(Agent(),'task-metadata',{},root,lambda p:p.startswith('task/'),lambda p:False,root/'events'),{})

    def test_rejected_write_is_atomic_and_can_be_corrected(self):
        class Agent:
            def call(self, name, context, log):
                if context['step'] == 1:
                    writes = [{'path':'task/good.txt','content':'partial'}, {'path':'work/RELEASE.txt','content':'extra'}]
                else:
                    assert not (log.parent/'task/good.txt').exists()
                    assert 'Unsupported write path' in context['read_results']['action_feedback']
                    writes = [{'path':'task/good.txt','content':'complete'}]
                return {'status':'done','reason':'test','read':[],'write':writes,'result_json':'{}'}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage(Agent(), 'task-build', {}, root, lambda p:False, lambda p:p=='task/good.txt', root/'events')
            self.assertEqual((root/'task/good.txt').read_text(), 'complete')

    def test_native_gated_null_is_accepted_but_unexplained_null_is_rejected(self):
        row = {'item_id':'E1','instances':['one'],'status':'completed','value':None,'accepted':False}
        self.assertEqual(score_rows([row])[('E1',('one',))], ('completed',None,False))
        for accepted in (None, True):
            with self.assertRaises(ValueError):
                score_rows([row | {'accepted':accepted}])

    def test_diagnostics_expose_only_bounded_execution_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'evidence').mkdir()
            (root/'evidence/prepare.log').write_text('x'*30000+'\nRuntimeError: missing module\n')
            (root/'evidence/auth.json').write_text('secret-value')
            report=diagnostics(root)
            self.assertIn('missing module', report['evidence/prepare.log'])
            self.assertLessEqual(len(report['evidence/prepare.log'].encode()),24000)
            self.assertNotIn('secret-value',json.dumps(report))

    def test_prepare_mounts_helpers_and_checked_private_source_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            script=root/'attempt/work/prepare.py'
            script.parent.mkdir(parents=True)
            script.write_text('pass')
            helpers=script.parent/'prepare_lib'
            helpers.mkdir()
            (helpers/'helper.py').write_text('x=1')
            lib=root/'attempt/task/evaluation/lib'
            lib.mkdir(parents=True)
            (lib/'check.py').write_text('x=2')
            source=root/'source.bin'
            source.write_bytes(b'private-source')
            expected=file_sha(source)
            evidence=root/'attempt/evidence'
            evidence.mkdir()
            runtime=Docker('python:3.12-slim',30,'1g',1)
            def run(image, entry, args, mounts, log):
                targets={destination:path for path,destination,_ in mounts}
                self.assertEqual(targets['/program/prepare_lib'],helpers)
                self.assertEqual(targets['/task/evaluation/lib'],lib)
                staged=targets['/source/test']
                self.assertNotEqual(staged,source)
                self.assertTrue(staged.resolve().is_relative_to(evidence.resolve()))
                self.assertEqual(file_sha(staged),expected)
                return 0
            runtime.run=run
            runtime.prepare('image',script,[source],[{'mount':'/source/test','sha256':expected}],
                            root/'materialize',evidence,root/'records')
            self.assertEqual(file_sha(source),expected)

    def test_format_view_has_public_data_and_excludes_private_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);package=root/'task'
            (package/'evaluation').mkdir(parents=True)
            (package/'input').mkdir()
            for name in ('metrics.py','evaluator.py'):
                (package/'evaluation'/name).write_text('pass')
            save(package/'task.json',{'schema_version':1,'instances':[{'name':'one',
                'deliverables':{'answer':'answer.json'},'references':{'truth':'evaluation/assets/private'}}]})
            runtime=Docker('python:3.12-slim',30,'1g',1)
            result=root/'results/format'
            def run(image,entry,args,mounts,log):
                targets={target:source for source,target,_ in mounts}
                self.assertEqual(targets['/task/input'],package/'input')
                self.assertNotEqual(targets['/task'],package)
                manifest=json.loads((targets['/task']/'task.json').read_text())
                self.assertNotIn('references',manifest['instances'][0])
                self.assertFalse((targets['/task']/'evaluation/assets').exists())
                save(result/'score.json',{'format_checks':{},'evaluation_results':[],'error':None})
                return 0
            runtime.run=run
            self.assertEqual(runtime.evaluate('image',package,root/'submission',result,format_only=True)[0],0)


if __name__=='__main__': unittest.main()
