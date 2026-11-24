"""Host-managed public-input-only calibration methods; no nested Docker or UID switch."""
from pathlib import Path
import re
import shutil

from common import file_sha, load, regular_files, relative, save, tree_sha


METHOD_RUNNER_CONTRACT = {
    "schema": 1,
    "manifest": "/out/evidence/method-jobs.json",
    "manifest_example": {"schema": 1, "jobs": [
        {"id": "weak", "program": "methods/weak/main.py", "timeout_seconds": 30, "args": []},
        {"id": "strong", "program": "methods/strong/main.py", "timeout_seconds": 480, "args": []}]},
    "program_root": "work/prepare_lib/",
    "method_mounts": {"/input": "derived public data, read-only",
                      "/program": "this method's Python program directory, read-only",
                      "/out": "fresh output directory for this method only"},
    "finalizer": "work/prepare_lib/finalize.py",
    "finalizer_runtime_script": "/program/prepare_lib/finalize.py",
    "finalizer_python_search_paths": ["/program", "/program/prepare_lib"],
    "finalizer_mounts": {"/out/public": "derived public data, read-only",
                         "/task/evaluation/lib": "optional task Python helpers, read-only code only; no evaluation assets",
                         "/out/private": "private assembly outputs, writable",
                         "/out/evidence": "records and calibration.json, writable",
                         "/method-runs": "actual host-executed methods, outputs and receipts, read-only"},
    "published_private_archive": "task/evaluation/assets/host-method-runs/ (host-reserved)",
    "budget": "Preparation, sequential method containers and finalization share one total host timeout. Leave room for startup and finalization; each method must stop cleanly before its declared container limit.",
    "boundary": "Raw sources, private assets, other methods' outputs and host credentials are never mounted into a method container. All methods see exactly the same frozen public data. No privilege change or nested container access is needed.",
    "provenance": "evidence/method-runs/index.json and per-method run.log.runtime.json are produced by the host. Finalization assembles and independently checks actual method results; it must not solve again, choose different methods, alter public data or fabricate calibration.",
}


class MethodContractError(ValueError):
    pass


def read_jobs(manifest, helper_root):
    """Validate all jobs before any method is executed."""
    if (manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_nlink != 1
            or manifest.stat().st_size > 64000):
        raise MethodContractError("Invalid or oversized isolated-method manifest")
    value = load(manifest)
    if (not isinstance(value, dict) or set(value) != {"schema", "jobs"}
            or type(value["schema"]) is not int or value["schema"] != 1
            or not isinstance(value["jobs"], list) or not 1 <= len(value["jobs"]) <= 16):
        raise MethodContractError("Expected method-jobs.json with schema1 and1..16 jobs")
    result, seen = [], set()
    for job in value["jobs"]:
        if not isinstance(job, dict) or set(job) != {"id", "program", "timeout_seconds", "args"}:
            raise MethodContractError("Invalid isolated-method job fields")
        name = job["id"]
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,35}", name) or name in seen:
            raise MethodContractError("Method IDs must be unique lowercase portable names")
        seen.add(name)
        if type(job["timeout_seconds"]) is not int or job["timeout_seconds"] < 1:
            raise MethodContractError("Method timeout_seconds must be a positive integer")
        args = job["args"]
        if (not isinstance(args, list) or len(args) > 32
                or any(not isinstance(arg, str) or len(arg) > 1000 or "\0" in arg for arg in args)):
            raise MethodContractError("Invalid isolated-method argument list")
        portable = relative(job["program"])
        if portable.parts[:2] != ("methods", name) or portable.suffix != ".py":
            raise MethodContractError("Each method program must be below prepare_lib/methods/<id>/")
        program = helper_root.joinpath(*portable.parts)
        if not program.resolve().is_relative_to(helper_root.resolve()) or not program.is_file():
            raise MethodContractError("Method program is missing or escapes its helper root")
        # Siblings belong only to this method, never a broader shared authoring tree.
        program_dir = helper_root / "methods" / name
        files = list(regular_files(program_dir))
        if not files or any(p.suffix != ".py" for p in files):
            raise MethodContractError("A method program directory may contain Python source files only")
        result.append((job, program, program_dir))
    return result


def execute_methods(script, public, private, records, evidence, image, run, remaining):
    """Optional second preparation phase. run() is the same hardened host container runner."""
    manifest = records / "method-jobs.json"
    if not manifest.exists() and not manifest.is_symlink():
        return None
    helper_root = script.parent / "prepare_lib"
    jobs = read_jobs(manifest, helper_root)
    finalizer = helper_root / "finalize.py"
    if not finalizer.is_file() or finalizer.is_symlink():
        raise MethodContractError("Isolated methods require work/prepare_lib/finalize.py")
    if (records / "calibration.json").exists():
        raise MethodContractError("For isolated methods, write calibration.json only in finalization after real executions")
    if sum(job["timeout_seconds"] for job, _, _ in jobs) >= remaining():
        raise MethodContractError("Declared method budgets leave no time for preparation overhead and finalization")
    reserved = private / "host-method-runs"
    if reserved.exists() or reserved.is_symlink():
        raise MethodContractError("host-method-runs is reserved for trusted host execution artifacts")
    public_sha = tree_sha(public)
    manifest_sha = file_sha(manifest)
    archive = records.parent / "isolated-method-runs"
    archive.mkdir(exist_ok=False)
    receipts = []
    for job, program, program_dir in jobs:
        root = archive / job["id"]
        root.mkdir()
        shutil.copytree(program_dir, root / "program")
        program_sha = tree_sha(root / "program")
        output = root / "output"
        output.mkdir()
        log = evidence / "method-runs" / job["id"] / "run.log"
        mounts = [(public, "/input", True), (root / "program", "/program", True), (output, "/out", False)]
        entry = "/program/" + program.relative_to(program_dir).as_posix()
        code = run("python", [entry, *job["args"]], mounts, log, job["timeout_seconds"])
        if code:
            raise MethodContractError(f"Isolated method {job['id']} failed (exit{code}); inspect method-runs/{job['id']}/run.log")
        if tree_sha(public) != public_sha or tree_sha(root / "program") != program_sha:
            raise MethodContractError("Frozen public inputs or method source changed during execution")
        output_sha = tree_sha(output)  # rejects links, hardlinks and special files before host reads them
        shutil.copyfile(log, root / "run.log")
        native = log.with_suffix(log.suffix + ".runtime.json")
        shutil.copyfile(native, root / "execution.json")
        receipt = {"id": job["id"], "program_sha256": program_sha,
                   "public_sha256": public_sha, "output_sha256": output_sha,
                   "execution_receipt_sha256": file_sha(native),
                   "execution_receipt": native.relative_to(evidence.parent).as_posix(),
                   "private_archive": f"host-method-runs/{job['id']}",
                   "timeout_seconds": job["timeout_seconds"], "arguments": job["args"]}
        save(root / "receipt.json", receipt)
        receipts.append(receipt)
    index = {"schema": 1, "kind": "actual isolated public-input-only method executions",
             "manifest_sha256": manifest_sha, "public_sha256": public_sha,
             "image": image, "jobs": receipts,
             "boundary": METHOD_RUNNER_CONTRACT["boundary"]}
    save(archive / "index.json", index)
    archive_sha = tree_sha(archive)
    save(evidence / "method-runs/index.json", index | {"archive_sha256": archive_sha})
    mounts = [(helper_root, "/program/prepare_lib", True),
              (public, "/out/public", True), (private, "/out/private", False),
              (records, "/out/evidence", False), (archive, "/method-runs", True)]
    evaluation_helpers = script.parent.parent / "task/evaluation/lib"
    if evaluation_helpers.is_dir():
        list(regular_files(evaluation_helpers))
        mounts.append((evaluation_helpers, "/task/evaluation/lib", True))
    bootstrap = ("import runpy,sys;sys.path[:0]=['/program','/program/prepare_lib'];"
                 "runpy.run_path('/program/prepare_lib/finalize.py',run_name='__main__')")
    code = run("python", ["-c", bootstrap], mounts, evidence / "method-runs/finalize.log", None)
    if code:
        raise MethodContractError(f"Method finalization failed (exit{code}); inspect method-runs/finalize.log")
    if tree_sha(public) != public_sha or tree_sha(archive) != archive_sha or file_sha(manifest) != manifest_sha:
        raise MethodContractError("Finalization changed frozen public inputs, method artifacts or the declared protocol")
    if reserved.exists() or reserved.is_symlink():
        raise MethodContractError("Finalizer wrote into the host-reserved method archive")
    shutil.copytree(archive, reserved)
    if tree_sha(reserved) != archive_sha:
        raise MethodContractError("Published method archive does not match actual execution artifacts")
    return index | {"archive_sha256": archive_sha}
