"""Execute generated code only in isolated, explicitly mounted Docker containers."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from common import file_sha, load, regular_files, save
from authoring_methods import METHOD_RUNNER_CONTRACT, execute_methods


class RuntimeUnavailable(ValueError):
    pass


class Defect(ValueError):
    def __init__(self, check, message, evidence):
        super().__init__(message)
        self.check, self.evidence = check, evidence


def retain_preparation_diagnostics(package, records, evidence, exit_code):
    """Keep bounded solver logs before the temporary output tree is removed."""
    folder = evidence / "preparation-diagnostics" / uuid.uuid4().hex[:10]
    copied, budget, scanned = [], 64000, 0
    try:
        folder.mkdir(parents=True, mode=0o700)
        for role, root in (("methods", records.parent / "isolated-method-runs"),
                           ("private", package / "evaluation/assets"), ("records", records)):
            for source in root.rglob("*"):
                scanned += 1
                if scanned > 1000 or len(copied) >= 16 or budget <= 0:
                    break
                if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(root.resolve()):
                    continue
                is_log = source.suffix == ".log"
                is_receipt = source.name.endswith(("receipt.json", "options.json", "status.json", "_solve.json"))
                if role == "records" and source.name in {"calibration.json", "preparation.json"}:
                    is_receipt = True
                if not (is_log or is_receipt):
                    continue
                size = source.stat().st_size
                limit = min(16000, budget)
                if not is_log and size > limit:
                    continue
                target = folder / role / source.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open("rb") as stream:
                    stream.seek(max(0, size - limit))
                    data = stream.read(limit)
                target.write_bytes(data)
                budget -= len(data)
                copied.append({"path": target.relative_to(evidence).as_posix(),
                               "original_bytes": size, "retained_bytes": len(data),
                               "truncated_tail": len(data) < size, "sha256": file_sha(target)})
        save(folder / "index.json", {"program_exit": exit_code, "files": copied,
                                    "meaning": "Execution diagnostics only; not an acceptance verdict."})
    except OSError:
        # Preserve the original preparation outcome if diagnostic storage fails.
        return


def run_command(command, evidence, timeout, cwd=None):
    from operations import checkpoint
    checkpoint()
    evidence.parent.mkdir(parents=True, exist_ok=True)
    with evidence.open("wb") as stream:
        try:
            run = subprocess.run(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=stream,
                                 stderr=subprocess.STDOUT, timeout=timeout)
            return run.returncode
        except subprocess.TimeoutExpired:
            return None
        except OSError as exc:
            raise RuntimeUnavailable(f"Could not launch {Path(command[0]).name}") from exc


def log_tail(path):
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 8192))
        return stream.read(8192).decode("utf-8", "replace")


def mount(path, target, read_only=True):
    source = str(path.resolve(strict=True))
    if any(ch in source for ch in ",\r\n") or any(ch in target for ch in ",\r\n"):
        raise ValueError("Unsupported Docker bind path")
    return ["--mount", f"type=bind,source={source},target={target}" + (",readonly" if read_only else "")]


def approved_dockerfile(path, base):
    raw = path.read_text(encoding="utf-8")
    if "<<" in raw or "\\\n" in raw or "\\\r" in raw:
        raise ValueError("Dockerfile must use single-line instructions")
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if any(re.match(r"#\s*(?:syntax|escape|check)\s*=", line, re.I) for line in lines):
        raise ValueError("Custom Dockerfile directives are unavailable")
    directives = [line for line in lines if not line.startswith("#")]
    if not directives or directives[0].split() != ["FROM", base]:
        raise ValueError(f"Dockerfile must start with FROM {base}")
    for line in directives[1:]:
        operation, _, value = line.partition(" ")
        if operation not in {"RUN", "COPY", "ENV", "WORKDIR", "LABEL", "SHELL"} or value.lstrip().startswith("--"):
            raise ValueError(f"Unsupported Dockerfile instruction: {operation}")
        if operation == "COPY" and re.search(r"(?:https?:|git@|\.\.)", value):
            raise ValueError("COPY must use the local environment context")


class Docker:
    def __init__(self, base, timeout, memory, cpus):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]+", base):
            raise ValueError("Invalid approved base image name")
        self.base, self.timeout, self.memory, self.cpus = base, timeout, memory, cpus
        self.user = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else "1000:1000"

    def available(self, folder):
        if run_command(["docker", "version", "--format", "{{.Server.Version}}"], folder / "docker-version.log", 15) != 0:
            raise RuntimeUnavailable("Docker daemon is unavailable; see docker-version.log")
        result = run_command(["docker", "image", "inspect", self.base, "--format", "{{.Id}}"],
                             folder / "base-image.log", 15)
        if result != 0:
            raise RuntimeUnavailable(f"Approved base image is unavailable locally: {self.base}")

    def capabilities(self, evidence):
        target = evidence / "runtime-capabilities.json"
        code = """import sys,json,platform,os,importlib.metadata as m
result={'python':sys.version,'platform':platform.platform(),'packages':{d.metadata['Name']:d.version for d in m.distributions()},'api_probes':{}}
status={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in open('/proc/self/status') if line.startswith(('CapEff:','CapBnd:','NoNewPrivs:'))}
result['security_probe']={'uid':os.getuid(),'euid':os.geteuid(),'gid':os.getgid(),'egid':os.getegid(),'effective_capabilities_hex':status.get('CapEff'),'bounding_capabilities_hex':status.get('CapBnd'),'no_new_privileges':int(status['NoNewPrivs'])}
try:
 import highspy
 h=highspy.Highs();h.setOptionValue('output_flag',False);a=h.getLp().a_matrix_
 result['api_probes']['highspy']={'sparse_vector_types':{name:type(getattr(a,name)).__name__ for name in ('start_','index_','value_')},'repeated_value_access_same_object':a.value_ is a.value_}
except ImportError:
 pass
print(json.dumps(result))
"""
        if self.run(self.base, "python", ["-c", code], [], target):
            raise RuntimeUnavailable("Could not inspect the approved runtime image")
        result = load(target)
        result["image"] = self.base
        result["container_limits"] = {"cpus": self.cpus, "memory": self.memory, "timeout_seconds": self.timeout}
        result["authoring_method_runner"] = METHOD_RUNNER_CONTRACT
        result["execution_contract"] = {
            "resource_checks": "Scripts are sent to sh -s on stdin, with cwd=/tmp. They have no script-file location. Keep each role check standalone; do not use $0 or dirname to locate a companion script.",
            "container_security": {"runtime_user": self.user, "cap_drop": ["ALL"],
                "no_new_privileges": True, "network": "none", "read_only_root": True,
                "privilege_note": "UID 0 is not proof of privilege: this runner drops ALL capabilities. Do not assume chown to another owner or switching child user/group is permitted. Inspect the actual security_probe; keep methods public-input-only without requiring unavailable privilege."},
            "solver_check_data": "/task/input",
            "evaluator_check_data": "/task/input and /task/evaluation"}
        if result.get("api_probes", {}).get("highspy", {}).get("sparse_vector_types", {}).get("value_") == "list":
            result["api_probes"]["highspy"]["usage_note"] = (
                "Sparse vector property access returns a fresh Python list. Cache starts=a.start_, "
                "indices=a.index_, values=a.value_ once before iterating nonzeros. Repeated property "
                "lookups inside the loop can copy the entire vector on every iteration.")
        save(target, result)
        return result

    def build(self, package, evidence):
        context = package / "environment"
        list(regular_files(context))
        images = {}
        for role, filename in (("solver", "Dockerfile"), ("evaluator", "Dockerfile.eval")):
            file = context / filename
            if role == "evaluator" and not file.exists():
                images[role] = images["solver"]
                continue
            if not file.is_file():
                raise Defect("C0.5", f"Missing environment/{filename}", f"task/environment/{filename}")
            try:
                approved_dockerfile(file, self.base)
            except ValueError as exc:
                raise Defect("C0.5", str(exc), f"task/environment/{filename}") from exc
            log = evidence / f"build-{role}.log"
            iid = evidence / f"{role}.image-id"
            iid.unlink(missing_ok=True)
            code = run_command(["docker", "build", "--network", "none", "--pull=false", "--iidfile", str(iid),
                                "--file", str(file), str(context)], log, self.timeout)
            if code is None:
                raise RuntimeUnavailable(f"Docker {role} image build timed out")
            if code or not iid.is_file():
                if any(value in log_tail(log).lower() for value in ("connection refused", "cannot connect to the docker",
                                                                   "no space left on device", "proxyconnect")):
                    raise RuntimeUnavailable(f"Docker host could not build the {role} image")
                raise Defect("C0.5", f"{role} Dockerfile failed to build (exit {code})", log.name)
            images[role] = iid.read_text(encoding="ascii").strip()
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", images[role]):
                raise RuntimeUnavailable("Docker did not return an immutable image ID")
        save(evidence / "images.json", images)
        provenance = {}
        for role, image in images.items():
            inspect = evidence / f"image-{role}.json"
            history = evidence / f"image-{role}-history.log"
            if run_command(["docker", "image", "inspect", image], inspect, 20):
                raise RuntimeUnavailable("Could not inspect task image provenance")
            info = load(inspect)[0]
            selected = {key: info.get(key) for key in ("Id", "Parent", "Created", "Architecture", "Os", "RootFS")}
            save(inspect, selected)
            if run_command(["docker", "history", "--no-trunc", "--format", "{{json .}}", image], history, 20):
                raise RuntimeUnavailable("Could not inspect task image layers")
            provenance[role] = selected | {"history": history.name}
        save(evidence / "environment-provenance.json", provenance)
        return images

    def run(self, image, entry, args, mounts, evidence):
        name = "task-generate-" + uuid.uuid4().hex
        command = ["docker", "run", "--rm", "--name", name, "--pull=never", "--network", "none", "--read-only",
                   "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "128",
                   "--memory", self.memory, "--cpus", str(self.cpus), "--user", self.user,
                   "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=512m", "--env", "HOME=/tmp",
                   "--env", "PYTHONDONTWRITEBYTECODE=1", "--workdir", "/tmp"]
        for path, destination, readonly in mounts:
            command.extend(mount(path, destination, readonly))
        started = time.monotonic()
        result = run_command(command + ["--entrypoint", entry, image, *args], evidence, self.timeout)
        save(evidence.with_suffix(evidence.suffix + ".runtime.json"), {
            "container": name, "image": image, "entrypoint": entry, "arguments": args,
            "working_directory": "/tmp", "runtime_user": self.user, "network": "none", "read_only_root": True,
            "cap_drop": ["ALL"], "no_new_privileges": True, "cpus": self.cpus, "memory": self.memory,
            "timeout_seconds": self.timeout, "duration_seconds": round(time.monotonic()-started,3),
            "exit_code": result, "mounts": [{"source": str(p), "target": dest, "read_only": ro}
                                            for p,dest,ro in mounts]})
        if result is None:
            run_command(["docker", "rm", "-f", name], evidence.with_suffix(".cleanup.log"), 15)
            raise RuntimeUnavailable(f"Isolated container timed out: {evidence.name}")
        if result in {125, 126, 127}:
            raise RuntimeUnavailable(f"Could not run the isolated container: {evidence.name}")
        return result

    def _run_budgeted(self, image, entry, args, mounts, evidence, deadline, limit=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeUnavailable("Shared data-preparation and calibration-method budget exhausted")
        original = self.timeout
        self.timeout = min(original, remaining, limit if limit is not None else original)
        try:
            return self.run(image, entry, args, mounts, evidence)
        finally:
            self.timeout = original

    def prepare(self, image, script, files, evidence_rows, package, evidence, records):
        deadline = time.monotonic() + self.timeout
        public, private = package / "input/data", package / "evaluation/assets"
        public.mkdir(parents=True, exist_ok=True)
        private.mkdir(parents=True, exist_ok=True)
        mounts = [(script, "/program/prepare.py", True), (public, "/out/public", False),
                  (private, "/out/private", False), (records, "/out/evidence", False)]
        # Preparation modules are explicit, read-only, and separate from published data.
        for folder, destination in ((script.parent / "prepare_lib", "/program/prepare_lib"),
                (script.parent.parent / "task/evaluation/lib", "/task/evaluation/lib")):
            if folder.is_dir():
                list(regular_files(folder))
                mounts.append((folder, destination, True))
        # A root container with cap-drop ALL cannot bypass a different host UID's 0600 file.
        # Copy only into this project's attempt, verify bytes, and preserve raw-file permissions.
        with tempfile.TemporaryDirectory(prefix="source-stage-", dir=evidence) as temporary:
            staged = Path(temporary)
            for index, (file, row) in enumerate(zip(files, evidence_rows)):
                if file_sha(file) != row["sha256"]:
                    raise RuntimeUnavailable("Source changed before data preparation")
                target = staged / str(index)
                shutil.copyfile(file, target)
                target.chmod(0o600)
                if file_sha(target) != row["sha256"]:
                    raise RuntimeUnavailable("Staged source checksum mismatch")
                mounts.append((target, row["mount"], True))
            code = None
            try:
                code = self._run_budgeted(image, "python", ["/program/prepare.py"], mounts,
                                          evidence / "prepare.log", deadline)
            finally:
                retain_preparation_diagnostics(package, records, evidence, code)
        if code:
            raise Defect("C3.1", f"Data preparation failed (exit {code})", "prepare.log")
        for file, row in zip(files, evidence_rows):
            if file_sha(file) != row["sha256"]:
                raise RuntimeUnavailable("Source changed while data preparation was running")
        try:
            execute_methods(script, public, private, records, evidence, image,
                lambda entry, args, mounts, log, limit: self._run_budgeted(
                    image, entry, args, mounts, log, deadline, limit),
                lambda: max(0, deadline - time.monotonic()))
        except ValueError as exc:
            if isinstance(exc, RuntimeUnavailable):
                raise
            save(evidence / "method-runs/error.json", {"kind": "host method-contract failure", "error": str(exc)})
            raise Defect("C3.1", str(exc), "evidence/method-runs/error.json") from exc
        finally:
            if (records / "method-jobs.json").exists():
                retain_preparation_diagnostics(package, records, evidence, code)
        for file, row in zip(files, evidence_rows):
            if file_sha(file) != row["sha256"]:
                raise RuntimeUnavailable("Source changed while calibration methods were running")

    def checks(self, package, images, evidence):
        for role, script, view in (("solver", "check_resources.sh", package / "input"),
                                   ("evaluator", "check_resources.eval.sh", package)):
            env = package / "environment"
            check = env / script
            manifest = env / ("packages.eval.json" if role == "evaluator" and (env / "Dockerfile.eval").is_file()
                              else "packages.json")
            if not check.is_file() or not manifest.is_file():
                raise Defect("C0.5", f"Missing {role} resource or package manifest", f"task/environment/{script}")
            data = load(manifest)
            packages = [*data.get("base_packages", []), *data.get("task_packages", [])]
            pins = [(entry["import"], entry["pip"], entry["version"]) for entry in packages]
            if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name)
                   or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", distribution)
                   for name, distribution, _ in pins):
                raise Defect("C0.5", f"Invalid {role} dependency import", f"task/environment/{manifest.name}")
            target = "/task/input" if role == "solver" else "/task"
            mounts = [(view, target, True), (check, "/check.sh", True)]
            probe = ("test ! -e /task/evaluation && test ! -e /task/task.json && test ! -e /task/metadata.json"
                     if role == "solver" else "test -f /task/evaluation/evaluator.py && test -f /task/task.json")
            if self.run(images[role], "/bin/sh", ["-c", probe + " && /bin/sh -s < /check.sh"], mounts,
                        evidence / f"check-{role}.log"):
                raise Defect("C0.5", f"{role} resource check failed", f"check-{role}.log")
            code = """import importlib, json, sys
from importlib.metadata import version
for module, distribution, expected in json.loads(sys.argv[1]):
    importlib.import_module(module)
    if version(distribution) != expected:
        raise ValueError(f'{distribution}: expected {expected}, observed {version(distribution)}')
"""
            if self.run(images[role], "python", ["-c", code, json.dumps(pins)], [], evidence / f"imports-{role}.log"):
                raise Defect("C0.5", f"{role} dependency import or version check failed", f"imports-{role}.log")
            if self.run(images[role], "python", ["-m", "pip", "check"], [], evidence / f"pip-check-{role}.log"):
                raise Defect("C0.5", f"{role} installed dependencies are inconsistent", f"pip-check-{role}.log")
        code = '''import os,json,errno,uuid
from pathlib import Path
p=Path('/task/input')
checks={'public_input_exists':p.is_dir(),'private_evaluation_absent':not Path('/task/evaluation').exists(),
 'private_manifest_absent':not Path('/task/task.json').exists(),'private_metadata_absent':not Path('/task/metadata.json').exists(),
 'raw_sources_absent':not Path('/source').exists(),'account_home_absent':'CODEX_HOME' not in os.environ}
try:
 (p/('.write-probe-'+uuid.uuid4().hex)).write_text('probe')
 checks['public_input_read_only']=False
except OSError as error:
 checks['public_input_read_only']=error.errno in (errno.EROFS,errno.EACCES,errno.EPERM)
tmp=Path('/tmp')/('probe-'+uuid.uuid4().hex);tmp.write_text('ok');checks['temporary_output_writable']=tmp.read_text()=='ok';tmp.unlink()
print(json.dumps({'checks':checks,'all_passed':all(checks.values())}))
raise SystemExit(0 if all(checks.values()) else 1)
'''
        if self.run(images['solver'], 'python', ['-c', code], [(package/'input','/task/input',True)], evidence/'solver-isolation.json'):
            raise Defect('C2.6','Solver view failed a real separation/access probe','solver-isolation.json')

    def evaluate_relocated(self, image, package, submission, result_dir):
        return self.evaluate(image,package,submission,result_dir,format_only=False,relocated=True)

    def evaluate(self, image, package, submission, result_dir, *, format_only, relocated=False):
        result_dir.mkdir(parents=True, exist_ok=True)
        view = package
        if format_only:
            view = result_dir.parent / "format-view"
            (view / "evaluation").mkdir(parents=True, exist_ok=True)
            manifest = load(package / "task.json")
            save(view / "task.json", {"schema_version": manifest["schema_version"], "instances": [
                {"name": row["name"], "deliverables": row["deliverables"]} for row in manifest["instances"]]})
            for name in ("evaluator.py", "metrics.py"):
                shutil.copyfile(package / "evaluation" / name, view / "evaluation" / name)
            modules = package / "evaluation/lib"
            if modules.is_dir():
                shutil.copytree(modules, view / "evaluation/lib", dirs_exist_ok=True)
        if relocated:
            view = result_dir.parent / 'evaluation-only-view'
            (view/'evaluation/assets').mkdir(parents=True,exist_ok=True)
            (view/'input').mkdir(exist_ok=True)
            for name in ('task.json','metadata.json'):
                shutil.copyfile(package/name,view/name)
            for file in regular_files(package/'evaluation'):
                rel=file.relative_to(package/'evaluation')
                if rel.parts[0]=='assets':continue
                target=view/'evaluation'/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(file,target)
        (result_dir / "score.json").unlink(missing_ok=True)
        mounts = [(view, "/task", True), (submission, "/submission", True), (result_dir, "/results", False)]
        if format_only and (package / "input").is_dir():
            # Format validation may use public schemas/identifiers, but never private references.
            (view / "input").mkdir(exist_ok=True)
            mounts.append((package / "input", "/task/input", True))
        if relocated:
            mounts += [(package/'evaluation/assets','/task/evaluation/assets',True),
                       (package/'input','/public-data/input',True)]
        args = ["/task/evaluation/evaluator.py", "--submission-dir", "/submission", "--result-file", "/results/score.json"]
        if format_only:
            args.append("--format-only")
        else:
            args += ["--work-dir", "/tmp/private-work", "--exec-work-dir", "/tmp/candidate-work", "--exec-uid", "22001",
                     "--exec-gid", "22001", "--data-root", "/public-data" if relocated else "/task", "--tool-records-dir", "/tmp/records", "--max-workers", "1"]
        code = self.run(image, "python", args, mounts, result_dir / "run.log")
        report = result_dir / "score.json"
        if code not in {0, 1, 2} or not report.is_file() or report.stat().st_size > 2 * 1024 * 1024:
            raise Defect("C4.4", "Evaluator did not produce a valid native result", str(result_dir / "run.log"))
        return code, load(report)


def static_check(checker, package, evidence):
    launcher = """import os, runpy, sys, types
from pathlib import Path
if os.name == 'nt':
    # The frozen checker imports Linux runtime helpers, but its static entry point
    # never acquires a Unix lock. Deliberately expose no lock operations here.
    sys.modules['fcntl'] = types.ModuleType('fcntl')
sys.argv = sys.argv[1:]
sys.path.insert(0, str(Path(sys.argv[0]).parent))
runpy.run_path(sys.argv[0], run_name='__main__')
"""
    code = run_command([sys.executable, "-I", "-X", "utf8", "-B", "-c", launcher,
                        str(checker), str(package), "--json"], evidence / "static-check.json", 45)
    if code is None:
        raise RuntimeUnavailable("Native static checker timed out")
    try:
        report = json.loads((evidence / "static-check.json").read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RuntimeUnavailable("Native static checker could not complete; see evidence/static-check.json") from exc
    return code == 0, report
