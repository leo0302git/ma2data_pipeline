"""Small shared primitives for immutable artifacts and single-writer state."""

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else _file_sha(stream)


def _file_sha(stream):
    sha = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        sha.update(block)
    return sha.hexdigest()


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temp = stream.name
        stream.write(canonical(data) + b"\n")
    try:
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def relative(name):
    if not isinstance(name, str) or len(name) > 250 or not re.fullmatch(r"[A-Za-z0-9_./-]+", name):
        raise ValueError(f"Invalid portable path: {name!r}")
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or str(path) != name or any(p in {".", ".."} or p.endswith(".") for p in path.parts):
        raise ValueError(f"Path must stay within the case directory: {name!r}")
    if any(p.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                                       *(f"LPT{i}" for i in range(1, 10))} for p in path.parts):
        raise ValueError(f"Reserved path: {name!r}")
    return path


def regular_files(root):
    for folder, dirs, files in os.walk(root, followlinks=False):
        parent = Path(folder)
        for name in sorted(dirs + files):
            path = parent / name
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError(f"Linked artifact is forbidden: {path.relative_to(root)}")
        for name in sorted(files):
            path = parent / name
            if not path.is_file() or path.stat().st_nlink != 1:
                raise ValueError(f"Expected a private regular artifact: {path.relative_to(root)}")
            yield path


def tree_sha(root):
    return digest({path.relative_to(root).as_posix(): file_sha(path) for path in regular_files(root)})
