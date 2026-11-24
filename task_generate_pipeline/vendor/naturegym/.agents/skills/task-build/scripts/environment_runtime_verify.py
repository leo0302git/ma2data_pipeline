#!/usr/bin/env python3
"""Host checks for task image layout, startup, tools and Compose cleanup."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from scripts import environment_images
from scripts.process_control import (CLEANUP_TIMEOUT, CLEANUP_PENDING_EXIT, cleanup_objects,
                                     cleanup_networks, cleanup_pending, _cleanup_run)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from docker_env_verify import validate_package_manifest


def dockerfile_sources(path: Path) -> list[str]:
    """Read local COPY/ADD inputs; stage/image copies are not host assets."""
    text = re.sub(r"\\\s*\n", " ", path.read_text(encoding="utf-8"))
    sources = []
    for line in text.splitlines():
        match = re.match(r"\s*(COPY|ADD)\s+(.*)", line, re.I)
        if not match:
            continue
        value = match[2].strip()
        options = []
        while value.startswith("--"):
            option, separator, value = value.partition(" ")
            options.append(option)
            value = value.lstrip()
            if not separator:
                raise ValueError(f"Invalid COPY/ADD in {path}")
        if any(option.startswith("--from=") for option in options):
            continue
        tokens = json.loads(value) if value.startswith("[") else shlex.split(value, comments=True)
        if not isinstance(tokens, list) or len(tokens) < 2 or not all(isinstance(v, str) for v in tokens):
            raise ValueError(f"Invalid COPY/ADD in {path}")
        sources.extend(source for source in tokens[:-1] if "://" not in source)
    return sources


def validate_environment_layout(
    environment: Path, dockerfile_overrides: dict[str, Path] | None = None,
) -> dict[str, list[list[str]]]:
    """Validate context-local assets and return only D mounts for role checks.

    The Dockerfile check covers explicit local COPY/ADD inputs, not successful
    installation or runtime capability; those require the image checks.
    """
    environment = environment.resolve()
    package = environment.parent
    for legacy in (environment / "build_inputs", package / "resources"):
        if legacy.exists() or legacy.is_symlink():
            raise ValueError(f"Unsupported E material location: {legacy}")
    sidecar_root = environment / "sidecar"
    sidecars = {}
    if sidecar_root.exists() or sidecar_root.is_symlink():
        if not sidecar_root.is_dir() or sidecar_root.is_symlink():
            raise ValueError("environment/sidecar must be a real directory")
        for service in sidecar_root.iterdir():
            if (not service.is_dir() or service.is_symlink()
                    or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", service.name)
                    or service.name == "main"):
                raise ValueError(f"Invalid sidecar service directory: {service.name}")
            for name in ("Dockerfile", "packages.json"):
                if not (service / name).is_file() or (service / name).is_symlink():
                    raise ValueError(f"Missing regular sidecar file: {service / name}")
            sidecars[service.name] = service / "Dockerfile"
        if sidecars and not (environment / "docker-compose.source.yaml").is_file():
            raise ValueError("Declared sidecars require environment/docker-compose.source.yaml")
        if sidecars:
            check = environment / "check_resources.sh"
            if not check.is_file() or check.is_symlink():
                raise ValueError("Declared sidecars require a regular environment/check_resources.sh")
            if "--sidecar" not in check.read_text(encoding="utf-8"):
                raise ValueError("environment/check_resources.sh must handle --sidecar <service> backend checks")
    dockerfiles = {"solver": environment / "Dockerfile", **{
        f"sidecar/{name}": path for name, path in sidecars.items()}}
    evaluator = environment / "Dockerfile.eval"
    if evaluator.is_file():
        dockerfiles["evaluator"] = evaluator
    for role, path in (dockerfile_overrides or {}).items():
        if role not in {"solver", "evaluator"}:
            raise ValueError(f"Unknown Dockerfile override role: {role}")
        path = Path(path).resolve()
        if not path.is_relative_to(environment) or not path.is_file():
            raise ValueError(f"Dockerfile override must be a file in environment/: {path}")
        dockerfiles[role] = path
    evaluator = dockerfiles.get("evaluator")
    contexts = {role: (path.parent if role.startswith("sidecar/") else environment)
                for role, path in dockerfiles.items()}
    assets: list[tuple[str, Path]] = []
    names = set()

    def add_components(role: str, root: Path) -> None:
        if not root.exists() and not root.is_symlink():
            return
        if not root.is_dir() or root.is_symlink():
            raise ValueError(f"E materials must be a real directory: {root}")
        for component in root.iterdir():
            if (not component.is_dir() or component.is_symlink()
                    or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", component.name)):
                raise ValueError(f"Invalid E asset directory: {component}")
            if role in {"solver", "evaluator"}:
                if component.name in names:
                    raise ValueError(f"Solver/evaluator component name collision: {component.name}")
                names.add(component.name)
            if role == "evaluator" and (evaluator is None or not evaluator.is_file()):
                raise ValueError("Private evaluator assets require Dockerfile.eval")
            assets.append((role, component))

    root = environment / "resources"
    if root.exists() or root.is_symlink():
        if not root.is_dir() or root.is_symlink():
            raise ValueError("environment/resources must be a real directory")
        for category in root.iterdir():
            if category.name not in {"solver", "evaluator"} or not category.is_dir() or category.is_symlink():
                raise ValueError("environment/resources permits only solver and evaluator directories; "
                                 "sidecar materials belong in sidecar/<service>/resources/<component>")
            add_components(category.name, category)
    for name, dockerfile in sidecars.items():
        add_components(f"sidecar/{name}", dockerfile.parent / "resources")

    # Protect the whole backend source, including startup/export code, rather
    # than only its acquired materials. Main COPY . would otherwise expose it.
    protected = [(owner, component) for owner, component in assets if owner == "evaluator"]
    protected.extend((f"sidecar/{name}", path.parent) for name, path in sidecars.items())
    inputs = {}
    for role, dockerfile in dockerfiles.items():
        if not dockerfile.is_file():
            continue
        context = contexts[role]
        paths = []

        def inspect_input(path: Path) -> Path:
            resolved = path.resolve()
            if not resolved.is_relative_to(context):
                raise ValueError(f"COPY/ADD input leaves its build context in {dockerfile}: {path}")
            for owner, private in protected:
                if role != owner and (resolved == private or resolved.is_relative_to(private)
                                      or private.is_relative_to(resolved)):
                    raise ValueError(f"{role} Dockerfile copies private or unrelated {owner} source: "
                                     f"{private.relative_to(environment)}")
            return resolved

        for source in dockerfile_sources(dockerfile):
            if Path(source).is_absolute() or ".." in Path(source).parts or "**" in source:
                raise ValueError(f"Use explicit build-context COPY/ADD paths in {dockerfile}: {source}")
            matches = [context] if Path(source) == Path(".") else list(context.glob(source))
            if not matches:
                raise ValueError(f"COPY/ADD source is missing from its build context in {dockerfile}: {source}")
            paths.extend(inspect_input(path) for path in matches)
        inputs[role] = paths
    for owner, component in assets:
        sources = inputs.get(owner, [])

        def first_uncovered(path: Path) -> Path | None:
            # Whole-tree COPY/ADD needs no coverage walk. For partial inputs,
            # descend only while declared paths could cover this branch.
            if any(path == source or path.is_relative_to(source) for source in sources):
                return None
            if (not path.is_dir() or path.is_symlink()
                    or not any(source.is_relative_to(path) for source in sources)):
                return path
            for child in sorted(path.iterdir()):
                missing = first_uncovered(child)
                if missing is not None:
                    return missing
            return None

        missing = first_uncovered(component)
        if missing is not None:
            raise ValueError(f"{missing.relative_to(environment)} is not consumed by its {owner} Dockerfile")
    mounts = {"solver": [], "evaluator": []}
    for role, relative in (("solver", "input/data"), ("evaluator", "evaluation/assets")):
        source = package / relative
        if not source.exists() and not source.is_symlink():
            continue
        if not source.is_dir() or source.is_symlink() or any(c in str(source) for c in (",", "\n", "\r")):
            raise ValueError(f"Task D mount must be a real directory: {source}")
        mounts[role].append([str(source), f"/task/{relative}"])
    return mounts


class InterruptedCheck(RuntimeError):
    """Stop checks while preserving the invocation-owned cleanup path."""


def read_startup_config(environment: Path, *, evaluator: bool = False) -> dict:
    """Read a role's source configuration; Docker validates full Compose semantics."""
    path = environment / ("docker-compose.eval.source.yaml" if evaluator else "docker-compose.source.yaml")
    if not path.exists():
        return {"services": {"main": {}}}
    import yaml
    try:
        config = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: invalid Compose YAML") from exc
    services = config.get("services") if isinstance(config, dict) else None
    if not isinstance(services, dict) or not isinstance(services.get("main"), dict):
        raise ValueError(f"{path}: Compose must define services.main")
    allowed = {"entrypoint", "command", "working_dir", "environment", "healthcheck"}
    main = services["main"]
    if evaluator and (set(config) != {"services"} or set(services) != {"main"} or set(main) - allowed):
        raise ValueError(f"{path}: only main startup fields are allowed: {sorted(allowed)}")
    for name in ("entrypoint", "command"):
        value = main.get(name)
        if value is not None and not (isinstance(value, str)
                or isinstance(value, list) and all(isinstance(part, str) for part in value)):
            raise ValueError(f"{path}: main.{name} must be a string, argument array or null")
    if main.get("command") is not None and not main["command"]:
        raise ValueError(f"{path}: main.command must retain a nonempty long-running command")
    if main.get("command") is None:
        main.pop("command", None)
    return config


def validate_compose(config: dict, environment: Path, names: list[str]) -> None:
    environment = environment.resolve()
    services = config.get("services", {})
    if not isinstance(services, dict) or set(services) != {"main", *names}:
        raise ValueError("Compose services must be main plus the declared sidecar service IDs")
    asset_roots = [environment / "resources", environment / "sidecar"]
    package = environment.parent
    data_roots = [package / "input/data", package / "evaluation/assets"]
    private_volume_names = {volume.get("source") for name in names
                            for volume in services.get(name, {}).get("volumes", [])
                            if isinstance(volume, dict) and volume.get("type") == "volume"}
    for name, service in services.items():
        if not isinstance(service, dict):
            raise ValueError(f"Invalid Compose service: {name}")
        if service.get("container_name"):
            raise ValueError(f"{name}: use project-scoped Compose container names during construction checks")
        if (service.get("privileged") or service.get("network_mode")
                or service.get("pid") or service.get("ipc") in ("host", "shareable")
                or str(service.get("ipc", "")).startswith("service:")
                or any(str(value).upper() in {"ALL", "SYS_ADMIN", "SYS_PTRACE"}
                       for value in service.get("cap_add", []))):
            raise ValueError(f"{name}: shared host/service namespaces or management privileges are unsupported")
        for volume in service.get("volumes", []):
            if not isinstance(volume, dict):
                continue
            if name == "main" and volume.get("type") == "volume" and volume.get("source") in private_volume_names:
                raise ValueError("Solver cannot share a sidecar state volume")
            if any(token in str(volume.get(field, "")) for field in ("source", "target")
                   for token in ("docker.sock", "podman.sock", "containerd.sock")):
                raise ValueError(f"{name}: container management sockets must not be exposed")
            if volume.get("type") != "bind":
                continue
            declared_source = environment / volume["source"]
            source = declared_source.resolve()
            bound_paths = {source, Path(os.path.abspath(declared_source))}
            if any(bound == root or bound.is_relative_to(root) or root.is_relative_to(bound)
                   for bound in bound_paths for root in asset_roots):
                raise ValueError(f"{name}: E assets and backend sources must enter the image through "
                                 "its Dockerfile, not a bind mount")
            data_bind = any(bound == root or bound.is_relative_to(root)
                            for bound in bound_paths for root in data_roots)
            if data_bind:
                if (not any(source == root or source.is_relative_to(root) for root in data_roots)
                        or not source.exists()):
                    raise ValueError(f"{name}: task D bind must resolve to existing packaged data")
            elif name != "main" and any(bound == package or bound.is_relative_to(package)
                                         for bound in bound_paths):
                raise ValueError(f"{name}: select sidecar task D from input/data/ or evaluation/assets/")
            if name == "main" and (source == package or package.is_relative_to(source)
                    or (source.is_relative_to(package)
                        and not source.is_relative_to(package / "input"))):
                raise ValueError("Solver bind mounts cannot expose private package/backend contents")
        if name == "main":
            continue
        build = service.get("build")
        if (not isinstance(build, dict)
                or not isinstance(build.get("context"), str)
                or (environment / build["context"]).resolve() != environment / "sidecar" / name
                or build.get("dockerfile") != "Dockerfile"):
            raise ValueError(f"{name}: build from environment/sidecar/{name}/ with Dockerfile")
        health = service.get("healthcheck")
        if (not isinstance(health, dict) or health.get("disable")
                or not health.get("test") or health["test"] == ["NONE"]):
            raise ValueError(f"{name}: an enabled readiness healthcheck is required")


def prepare_check_binds(config: dict, environment: Path, working_root: Path) -> None:
    """Resolve bind sources and isolate writable mounts before starting checks."""
    for name, service in config["services"].items():
        for index, mount in enumerate(service.get("volumes", [])):
            if mount.get("type") != "bind":
                continue
            source = (environment / mount["source"]).resolve(strict=True)
            if mount.get("read_only"):
                mount["source"] = str(source)
                continue
            destination = working_root / name / str(index)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, destination, symlinks=True)
            else:
                shutil.copy2(source, destination)
            mount["source"] = str(destination)


def verify_sidecars_main(argv: list[str]) -> int:
    """Start actual role configurations, then check main resources and optional sidecars."""
    parser = argparse.ArgumentParser(
        description="Build declared sidecars and check backend operations and public clients")
    parser.add_argument("environment", type=Path)
    parser.add_argument("--filter-result", type=Path, required=True)
    parser.add_argument("--paper-dir", type=Path, required=True)
    parser.add_argument("--images-json", type=Path, help="Built main role images; no task asset mounts are added")
    parser.add_argument("--dockerfiles-json", type=Path, help="Selected main role Dockerfiles for layout checks")
    parser.add_argument("--check-timeout", type=float, default=600)
    parser.add_argument("--keep-images", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--role", choices=("all", "solver", "evaluator", "sidecar"), default="all")
    parser.add_argument("--cpus")
    parser.add_argument("--memory")
    parser.add_argument("--gpus")
    args = parser.parse_args(argv)
    if not math.isfinite(args.check_timeout) or args.check_timeout <= 0:
        parser.error("--check-timeout must be a positive finite number")
    environment = args.environment.resolve()
    sidecar_root = environment / "sidecar"
    names = sorted(path.name for path in sidecar_root.iterdir()) if sidecar_root.exists() else []
    if not names and args.role == "sidecar":
        parser.error("No declared sidecar environments")
    dockerfiles = json.loads(args.dockerfiles_json.read_text()) if args.dockerfiles_json else {}
    mounts = validate_environment_layout(environment, dockerfiles)
    images = json.loads(args.images_json.read_text()) if args.images_json else {}
    if args.role != "sidecar" and not isinstance(images, dict):
        parser.error("Main image tags are required for public client checks")
    invocation = uuid.uuid4().hex[:12]
    project = f"naturegym-check-{invocation}"
    scripts = Path(__file__).resolve().parent
    reports = {name: environment / "sidecar" / name / "verify_result.txt" for name in names}
    if reports:
        environment_images.archive_reports(args.filter_result, args.paper_dir, list(reports.values()))
    logs = {name: [] for name in names}
    statuses = {name: False for name in names}
    owned_images = {name: f"naturegym-sidecar-{invocation}-{name}:latest" for name in names}
    image_metadata = {}
    created_images = []
    role_results = {role: dict(status='not verified', checked=False, log=[]) for role in images}
    cleanup_errors: list[str] = []
    cleanup_notes: list[str] = []
    cleanup_result = dict(removed=[], skipped=[], errors=[])
    cleanup_seconds = 0.0
    failure = None
    unverified = False
    startup_attempted = False
    interrupted_signal = None
    cleaning_up = False

    def interrupted(signum, _frame):
        nonlocal interrupted_signal
        interrupted_signal = signum
        if not cleaning_up:
            raise InterruptedCheck(f"Interrupted by signal {signum}")

    handlers = {signum: signal.signal(signum, interrupted) for signum in (signal.SIGINT, signal.SIGTERM)}
    with tempfile.TemporaryDirectory(prefix="naturegym-sidecar-check-") as temporary:
        temporary = Path(temporary)
        source = read_startup_config(environment)
        evaluator_main = read_startup_config(environment, evaluator=True)["services"]["main"]
        base = temporary / "base.json"
        base.write_text(json.dumps({"services": {"main": {"command": ["sh", "-c", "sleep infinity"]}}}))
        # Normalize null to the main default, while preserving the source file.
        source_file = temporary / "source.json"
        source_file.write_text(json.dumps(source))
        override = temporary / "override.json"
        ownership = {"naturegym.run_id": os.environ.get("NATUREGYM_RUN_ID", ""),
                     "naturegym.paper_id": os.environ.get("NATUREGYM_PAPER_ID", ""),
                     "naturegym.temporary": "check"}
        overrides = {name: {"image": image, "pull_policy": "never",
                            "labels": ownership, "build": {"labels": ownership}}
                     for name, image in owned_images.items()}
        # Harbor injects the main image. Standalone Compose validation still needs
        # one, including backend-only checks that never start this placeholder.
        overrides["main"] = {"image": next(iter(images.values()), "naturegym-main-not-started:latest"),
                             "pull_policy": "never", "labels": ownership}
        override.write_text(json.dumps({"services": overrides}))
        compose = ["docker", "compose", "--project-directory", str(environment), "-p", project,
                   "-f", str(base), "-f", str(source_file),
                   "-f", str(override)]

        def run(command: list[str], *, timeout: float | None = None,
                input_text: str | None = None) -> str:
            result = subprocess.run(command, capture_output=True, text=True, input=input_text,
                                    timeout=None if timeout == 0 else timeout or args.check_timeout, cwd=environment)
            output = result.stdout + result.stderr
            if result.returncode:
                raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}\n{output}")
            return output

        try:
            config = json.loads(run(compose + ["config", "--format", "json"]))
            validate_compose(config, environment, names)
            for kind in ("volumes", "networks"):
                for name, definition in config.get(kind, {}).items():
                    if (not definition.get("external")
                            and not definition.get("name", "").startswith(project + "_")):
                        raise ValueError(f"Construction checks require project-scoped {kind}: {name}")
            prepare_check_binds(config, environment, temporary / "working-mounts")
            for name, service in config["services"].items():
                if service.get("volumes"):
                    overrides[name]["volumes"] = service["volumes"]
            networks = {}
            for name, definition in config.get('networks', {}).items():
                if not definition.get('external'):
                    definition['labels'] = {**(definition.get('labels') or {}), **ownership}
                    networks[name] = {'labels': ownership}
            override.write_text(json.dumps({'services': overrides, 'networks': networks}))
            startup_attempted = True
            for name in names:
                service = environment / "sidecar" / name
                build_spec = config["services"][name]["build"]
                build_args = build_spec.get("args") or {}
                # Compose config resolves list/environment argument forms.
                if not isinstance(build_args, dict):
                    raise ValueError(f"Unresolved build arguments for {name}")
                meta = environment_images.select(args.filter_result, 'sidecar', name,
                    service / 'Dockerfile', service, paper_id=ownership['naturegym.paper_id'],
                    no_cache=args.no_cache, rebuild=args.rebuild, build_args=build_args,
                    platform=config["services"][name].get('platform'))
                image_metadata[name] = meta
                if meta['image_action'] == 'reused':
                    owned_images[name] = meta['image_id']
                    overrides[name]['image'] = meta['image_id']
                    config['services'][name]['image'] = meta['image_id']
                    logs[name].append('Reused adopted image; Docker build skipped.')
                else:
                    created_images.append(owned_images[name])
                    try:
                        # Building is not a short resource probe.
                        logs[name].append(run(compose + ['build'] + (['--no-cache'] if args.no_cache else []) + [name], timeout=0))
                        environment_images.finish_build(meta, owned_images[name])
                    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError) as exc:
                        logs[name].append(f'Build failed: {exc}')
                        failure = str(exc)
            override.write_text(json.dumps({'services': overrides, 'networks': networks}))
            if failure:
                raise RuntimeError('A required sidecar could not be built; dependent calls were not performed.')
            if names:
                output = run(compose + ["up", "-d", "--no-deps", "--no-build", "--wait", "--wait-timeout",
                                         str(max(1, int(args.check_timeout))), *names])
                for log in logs.values():
                    log.append(output)
            for name in names:
                log = logs[name]
                service = environment / "sidecar" / name
                manifest = json.loads((service / "packages.json").read_text())
                validate_package_manifest(manifest)
                imports = [dict(row, source=key) for key in ("base_packages", "task_packages")
                           for row in manifest[key]]
                imports_path = temporary / f"{name}-imports.json"
                imports_path.write_text(json.dumps(imports))
                destination = f"/tmp/naturegym-check-{invocation}"
                log.append(run(compose + ["exec", "-T", name, "mkdir", "-p", destination]))
                for source, target in ((scripts / "docker_env_verify.py", "docker_env_verify.py"),
                                       (imports_path, "imports.json")):
                    log.append(run(compose + ["cp", str(source), f"{name}:{destination}/{target}"]))
                command = ["python", f"{destination}/docker_env_verify.py", "--extra-imports-json",
                           f"{destination}/imports.json"]
                if not manifest["from_base"]:
                    command.append("--standalone")
                backend_ok = True
                if manifest["from_base"] or imports:
                    try:
                        log.append(run(compose + ["exec", "-T", name, *command]))
                    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                        log.append(str(exc))
                        backend_ok = False
                else:
                    log.append("No Python packages declared; checking native tool operation below.")
                try:
                    log.append(run(compose + ["exec", "-T", "--workdir", "/tmp", name,
                        "/bin/sh", "-s", "--", "--sidecar", name],
                        input_text=(environment / "check_resources.sh").read_text(encoding="utf-8")))
                except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                    log.append(str(exc))
                    backend_ok = False
                statuses[name] = backend_ok
            # Repeat main tool checks on the actual service network. Each role uses
            # its already built image, never a sidecar/private build context.
            for role, image in images.items():
                try:
                    role_override = temporary / f"{role}.json"
                    topology = deepcopy(config)
                    main = topology["services"]["main"]
                    if role == "evaluator":
                        public_env = main.get("environment", {})
                        main = {"command": ["sh", "-c", "sleep infinity"], **deepcopy(evaluator_main)}
                        values = main.get("environment") or {}
                        if isinstance(values, list):
                            values = dict(v.split("=", 1) if "=" in v else (v, os.environ.get(v)) for v in values)
                        main["environment"] = {**public_env, **values}
                        # Retain only the service network, not Solver startup/dependencies/mounts.
                        if "networks" in topology["services"]["main"]:
                            main["networks"] = topology["services"]["main"]["networks"]
                        topology["services"]["main"] = main
                    main.pop("build", None)
                    main.update({"image": image, "pull_policy": "never", "labels": ownership})
                    if args.cpus:
                        main["cpus"] = args.cpus
                    if args.memory:
                        main["mem_limit"] = args.memory
                    if args.gpus:
                        gpu = args.gpus.strip('"\'')
                        device = {"driver": "nvidia", "capabilities": ["gpu"]}
                        if gpu.startswith("device="):
                            device["device_ids"] = gpu[7:].split(",")
                        else:
                            device["count"] = "all" if gpu == "all" else int(gpu)
                        main["gpus"] = [device]
                    role_compose = ["docker", "compose", "--project-directory", str(environment),
                                    "-p", project, "-f", str(role_override)]
                    suffix = ".eval" if role == "evaluator" else ""
                    check = environment / f"check_resources{suffix}.sh"
                    if not check.is_file() or check.is_symlink():
                        raise ValueError(f"Missing regular resource check: {check}")
                    dockerfile = Path(dockerfiles.get(role, dockerfiles.get("solver", environment / "Dockerfile")))
                    manifest_path = environment / ("packages.eval.json" if dockerfile.name == "Dockerfile.eval" else "packages.json")
                    manifest = json.loads(manifest_path.read_text())
                    validate_package_manifest(manifest)
                    imports_path = temporary / f"{role}-imports.json"
                    imports_path.write_text(json.dumps([dict(row, source=key)
                        for key in ("base_packages", "task_packages") for row in manifest[key]]))
                    destination = f"/tmp/naturegym-check-{invocation}"
                    volumes = main.setdefault("volumes", [])
                    volumes.extend({"type": "bind", "source": str(p), "target": target, "read_only": True}
                        for p, target in ((check, "/tmp/naturegym-check-resources.sh"),
                            (scripts / "docker_env_verify.py", destination + "/docker_env_verify.py"),
                            (imports_path, destination + "/imports.json")))
                    for group in (["solver"] if role == "solver" else ["solver", "evaluator"]):
                        for origin, target in mounts[group]:
                            volumes.append({"type": "bind", "source": origin, "target": target, "read_only": True})
                    role_override.write_text(json.dumps(topology))
                    result = role_results[role]
                    log = result['log']
                    started = False
                    try:
                        log.append(run(role_compose + ["up", "-d", "--no-deps", "--no-build", "--wait", "--wait-timeout",
                                                       str(max(1, int(args.check_timeout))), "main"]))
                        started = True
                    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                        log.append(f'Startup failed; imports and resources not performed: {exc}')
                        result['status'] = 'has failures'
                    if started:
                        result['checked'] = True
                        result['status'] = 'all passed'
                        task_check = ["python", destination + "/docker_env_verify.py", "--checks", "task",
                                      "--extra-imports-json", destination + "/imports.json"]
                        if not manifest["from_base"]:
                            task_check.append("--standalone")
                        for label, command in (
                            ('Imports', role_compose + ["exec", "-T", "main", *task_check]),
                            ('Resources', role_compose + ["exec", "-T", "--workdir", "/tmp", "main",
                                                          "/bin/sh", "/tmp/naturegym-check-resources.sh"]),
                        ):
                            try:
                                log.append(label + ':\n' + run(command))
                            except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                                log.append(f'{label} failed: {exc}')
                                result['status'] = 'has failures'
                    if result['status'] != 'all passed':
                        try:
                            log.append(run(role_compose + ['logs', '--no-color', '--tail', '100', 'main'], timeout=15))
                        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                            log.append(f'Log collection failed: {exc}')
                    if role != list(images)[-1]:
                        try:
                            run(role_compose + ["rm", "-s", "-f", "main"], timeout=60)
                        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                            failure = f"Role handoff cleanup prevented the remaining checks: {exc}"
                            cleanup_notes.append(failure)
                            unverified = True
                            break
                except (OSError, ValueError, KeyError, subprocess.SubprocessError, RuntimeError) as exc:
                    role_results[role]['status'] = 'has failures'
                    role_results[role]['log'].append(f'Role setup/check failed: {exc}')
        except (OSError, ValueError, subprocess.SubprocessError, RuntimeError) as exc:
            failure = str(exc)
            print(f"Startup/resource check failed: {exc}", file=sys.stderr, flush=True)
        finally:
            cleaning_up = True
            cleanup_started = time.monotonic()
            deadline = cleanup_started + CLEANUP_TIMEOUT
            down_error = None
            if startup_attempted:
                for name in names:
                    if failure or not statuses[name]:
                        try:
                            logs[name].append(run(compose + ["logs", "--no-color", "--tail", "100", name],
                                                  timeout=max(.1, min(15, deadline - time.monotonic()))))
                        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                            logs[name].append(f"Log collection failed: {exc}")
                try:
                    run(compose + ["down", "--volumes", "--remove-orphans"],
                        timeout=max(.1, min(60, deadline - time.monotonic() - 5)))
                except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                    down_error = str(exc)
                    cleanup_notes.append(f"Compose teardown: {exc}")
                cleanup_result = cleanup_networks(ownership['naturegym.run_id'], ownership['naturegym.paper_id'],
                                                   project=project, deadline=deadline)
                if down_error:
                    try:
                        remaining = _cleanup_run(['docker', 'ps', '-aq', '--filter',
                                                  f'label=com.docker.compose.project={project}'], deadline).stdout.strip()
                        volumes = (_cleanup_run(['docker', 'volume', 'ls', '-q', '--filter',
                                                f'label=com.docker.compose.project={project}'], deadline).stdout.strip()
                                   if config.get('volumes') else '')
                        if remaining or volumes:
                            cleanup_errors.append(f"{down_error}; remaining containers={remaining!r}, volumes={volumes!r}")
                    except (OSError, subprocess.SubprocessError) as exc:
                        cleanup_errors.append(f"Cannot confirm Compose teardown: {exc}; {down_error}")
            if not args.keep_images:
                image_cleanup = cleanup_objects([('image', image) for image in created_images], deadline=deadline)
                for key in cleanup_result:
                    cleanup_result[key].extend(image_cleanup[key])
            cleanup_result['errors'].extend(dict(resource=project, error=error) for error in cleanup_errors)
            pending = cleanup_pending(cleanup_result)
            cleanup_seconds = time.monotonic() - cleanup_started
            print('\n===== CLEANUP =====', flush=True)
            for note in cleanup_notes:
                print(note, flush=True)
            print(json.dumps(cleanup_result), flush=True)
            for name, report in reports.items():
                passed = statuses[name] and failure is None
                status = ('not verified' if unverified else 'all passed' if passed and args.role == 'all'
                          else 'selected roles passed' if passed else 'has failures')
                content = (f"role: sidecar\nservice: {name}\nimage: {owned_images[name]}\n"
                           f"retained_image: {int(args.keep_images or image_metadata.get(name, {}).get('image_action') == 'reused')}\n"
                           f"image_action: {image_metadata.get(name, {}).get('image_action', 'built')}\n"
                           f"time: {datetime.now(timezone.utc).isoformat()}\n"
                           f"coverage: {'backend and ' + ', '.join(images) + ' clients' if images else 'selected backend only'}\n"
                           f"status: {status}\n"
                           f"cleanup_status: {'pending' if pending else 'complete'}\n"
                           + "\n".join(logs[name]) + "\n"
                           + (f"\nFailure: {failure}\n" if failure else "")
                           + (f"\nInterrupted by signal {interrupted_signal}\n" if interrupted_signal is not None else "")
                           + '\n===== CLEANUP =====\n' + '\n'.join(cleanup_notes)
                           + '\n' + json.dumps(cleanup_result))
                if image_metadata.get(name, {}).get('image_id'):
                    image_metadata[name]['checked_roles'] = [name] if statuses[name] else []
                    content += '\n' + environment_images.MARKER + json.dumps(image_metadata[name]) + '\n'
                handle, staging = tempfile.mkstemp(prefix=".verify-result-", dir=report.parent)
                try:
                    with os.fdopen(handle, "w", encoding="utf-8") as stream:
                        stream.write(content)
                    os.replace(staging, report)
                finally:
                    if os.path.exists(staging):
                        os.unlink(staging)
    for signum, handler in handlers.items():
        signal.signal(signum, handler)
    for result in role_results.values():
        if not result['checked']:
            result['log'].append(failure or 'Required startup/resource checks were not performed.')
        print('\n'.join(result['log']), flush=True)
    check_exit = 0 if (failure is None and all(statuses.values())
                       and all(r['status'] == 'all passed' for r in role_results.values())) else 1
    print('NATUREGYM_ENV_CHECK_RESULT=' + json.dumps(dict(
        check_exit=check_exit, unverified=unverified, roles=role_results,
        sidecars_ok=all(statuses.values()), failure=failure,
        cleanup=cleanup_result, cleanup_seconds=cleanup_seconds)), flush=True)
    if interrupted_signal is not None:
        return 128 + interrupted_signal
    return check_exit or (CLEANUP_PENDING_EXIT if cleanup_pending(cleanup_result) else 0)


if __name__ == "__main__":
    raise SystemExit(verify_sidecars_main(sys.argv[1:]))
