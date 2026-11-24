"""Load the pinned NatureGym files bundled with the pipeline."""

import ast
import os
from hashlib import sha256
from pathlib import Path

from common import digest


BUNDLE_ROOT = Path(__file__).resolve().parent / "vendor" / "naturegym"
BUNDLE_SHA256 = "0fc8caaf447743e2cf338af61e4a153d03321134a68bebb2bb5030c7c2677602"
SKILLS = ".agents/skills/"
TEMPLATE = SKILLS + "task-build/templates/"
CHECKER = SKILLS + "task-build/scripts/check_package.py"
HELPERS = (SKILLS + "task-build/scripts/environment_runtime_verify.py",
           SKILLS + "task-build/scripts/docker_env_verify.py", "scripts/environment_images.py",
           "scripts/process_control.py", "scripts/batch_history.py", "scripts/account_pool.py",
           "scripts/account_quota.py")
FILES = {
    "checker": CHECKER,
    **{name: TEMPLATE + name for name in ("evaluator.py", "metrics.py", "task.json", "task.source.toml",
                                          "metadata.json", "packages.json", "rubric.json", "rubric.py", "judge.py")},
    "verification_rules": SKILLS + "task-verify/references/verification_rules.md",
    **{name: name for name in HELPERS},
}


FILES.update({'level3_rules.md': '.agents/skills/paper-filter/references/level3_rules.md', 'level2_rules.md': '.agents/skills/paper-filter/references/level2_rules.md', 'metadata_guide.md': '.agents/skills/task-build/references/metadata_guide.md'})

class NatureGym:
    def __init__(self):
        self.files = {name: (BUNDLE_ROOT / member).read_bytes() for name, member in FILES.items()}
        self.bundle_sha256 = digest({member: sha256(self.files[name]).hexdigest() for name, member in FILES.items()})
        if self.bundle_sha256 != BUNDLE_SHA256:
            raise ValueError("Bundled NatureGym files differ from the reviewed snapshot; restore vendor/naturegym")

    def templates(self):
        names = ("task.source.toml", "task.json", "metadata.json", "packages.json", "metrics.py",
                 "rubric.json", "rubric.py")
        result = {name: self.files[name].decode("utf-8") for name in names}
        evaluator = self.files["evaluator.py"].decode("utf-8")
        context = next(node for node in ast.parse(evaluator).body if isinstance(node, ast.ClassDef) and node.name == "Context")
        result["evaluator_context_api"] = "\n\n".join(ast.get_source_segment(evaluator, node)
            for node in context.body if isinstance(node, ast.FunctionDef) and node.name in {"artifact", "resource", "cached"})
        return result

    def install(self, checker_root, package):
        checker = checker_root / CHECKER
        evaluator = checker_root / TEMPLATE / "evaluator.py"
        source = self.files["checker"]
        if os.name == "nt":
            # Native checks use POSIX keys when locating the inspected hook AST.
            needle = b"relative = str(source.relative_to(self.package))"
            if source.count(needle) != 1:
                raise ValueError("Cannot apply the Windows path compatibility adjustment to the static checker")
            source = source.replace(needle, b"relative = source.relative_to(self.package).as_posix()")
        copies = [(checker, source), (evaluator, self.files["evaluator.py"]),
                  (package / "evaluation/evaluator.py", self.files["evaluator.py"])]
        copies.extend((checker_root / name, self.files[name]) for name in HELPERS)
        for path, data in copies:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return checker
