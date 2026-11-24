"""Freeze the dataset identity and provide bounded, private source evidence."""

import csv
import copy
import gzip
import hashlib
import math
import re
from itertools import islice
from pathlib import PurePosixPath, Path

from common import digest, file_sha, load


SERVER_ROOT = "/data/ma2data/storage/catalog/LDM_JC_1105/2026-W45"
BENCHMARKS = {"NatureBench", "TB-Science", "MLE-bench"}
COMPLETE = "当前公开清单已获齐"


def read_inventory(path):
    fields = {"source_id": "数据源ID", "resource_id": "资源ID", "domain": "本体学科",
              "category": "本体任务", "download_status": "下载状态", "paths": "服务器路径"}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not set(fields.values()).issubset(reader.fieldnames or []):
            raise ValueError("Inventory CSV is missing required columns")
        rows = []
        for raw in reader:
            if None in raw or any(raw[value] is None for value in fields.values()):
                raise ValueError(f"Malformed inventory row {reader.line_num}")
            row = {key: raw[value].strip() for key, value in fields.items()}
            row["paths"] = [value.strip() for value in row["paths"].splitlines() if value.strip()]
            rows.append(row)
    if not rows or any(not row["source_id"] or not row["resource_id"] for row in rows):
        raise ValueError("Inventory needs source and resource IDs")
    for field in ("source_id", "resource_id"):
        if len({row[field] for row in rows}) != len(rows):
            raise ValueError(f"Inventory repeats {field}")
    return rows


def read_seeds(path):
    catalog = load(path)
    if catalog["schema"] != 1 or not isinstance(catalog["seeds"], list):
        raise ValueError("Seed catalog must have schema 1 and a seed list")
    seeds = {}
    for row in catalog["seeds"]:
        keys = ("id", "benchmark", "revision", "url", "groups", "file_suffixes", "contract")
        seed = {key: row[key] for key in keys}
        if 'documents' in row:
            docs = row['documents']
            if (not isinstance(docs, list) or not 1 <= len(docs) <= 4
                    or any(not isinstance(d, dict) or not isinstance(d.get('text'), str)
                           or not 40 <= len(d['text'].encode('utf-8')) <= 192 * 1024
                           or not isinstance(d.get('url'), str) or not d['url'].startswith('https://')
                           or hashlib.sha256(d['text'].encode('utf-8')).hexdigest() != d.get('sha256') for d in docs)):
                raise ValueError('Discovered seed original documents are missing, oversized or changed')
            seed['documents'] = docs
            seed['upstream_id'] = row.get('upstream_id', seed['id'])
        if (not isinstance(seed["id"], str) or not seed["id"] or seed["id"] in seeds
                or seed["benchmark"] not in BENCHMARKS or not seed["revision"]
                or not seed["url"].startswith("https://") or not seed["contract"]
                or not isinstance(seed["file_suffixes"], list) or not seed["file_suffixes"]
                or any(not isinstance(ext, str) or not ext.startswith(".") for ext in seed["file_suffixes"])
                or not isinstance(seed["groups"], list) or not seed["groups"]
                or any(not isinstance(group, list) or len(group) != 2
                       or any(not isinstance(value, str) or not value for value in group) for group in seed["groups"])):
            raise ValueError(f"Invalid benchmark seed: {seed['id']}")
        seeds[seed["id"]] = seed
    return seeds


def extension_matches(row, seed):
    return bool(row["paths"]) and any(path.lower().endswith(tuple(seed["file_suffixes"])) for path in row["paths"])


def case_id(row):
    return "task-" + digest(row["resource_id"])[:16]


def resolve_sources(row, root, prefix):
    paths = []
    for listed in row["paths"]:
        path = PurePosixPath(listed)
        if ".." in path.parts:
            raise ValueError("Inventory path contains a parent traversal")
        if path.is_absolute():
            path = path.relative_to(PurePosixPath(prefix))
        file = root.joinpath(*path.parts).resolve(strict=True)
        if not file.is_relative_to(root) or not file.is_file() or not file.stat().st_size:
            raise ValueError("Source must be a nonempty file inside the data root")
        paths.append(file)
    if not paths or len(set(paths)) != len(paths):
        raise ValueError("Source needs at least one distinct input file")
    return paths


def public_shape(path):
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, delimiter="\t" if suffix == ".tsv" else ",")
            columns = next(reader, [])[:80]
            candidate_labels = {i: set() for i, name in enumerate(columns)
                                if name.strip().casefold() in {"label", "target", "class", "y", "cell_type"}}
            numeric = set(range(len(columns)))
            rows, malformed = 0, 0
            for row in islice(reader, 512):
                rows += 1
                if len(row) < len(columns):
                    malformed += 1
                    continue
                for index, values in candidate_labels.items():
                    if row[index].strip():
                        values.add(row[index].strip())
                for index in tuple(numeric):
                    if row[index].strip():
                        try:
                            if math.isfinite(float(row[index])):
                                continue
                        except ValueError:
                            pass
                        numeric.remove(index)
        return {"columns": [name[:120] for name in columns], "sample_rows": rows, "short_rows": malformed,
                "numeric_columns": [columns[i][:120] for i in sorted(numeric)],
                "candidate_label_cardinality": {columns[i][:120]: len(values) for i, values in candidate_labels.items()}}
    if suffix == ".json" and path.stat().st_size <= 1024 * 1024:
        value = load(path)
        return {"json_type": type(value).__name__, "keys": [str(key)[:120] for key in list(value)[:80]]
                if isinstance(value, dict) else [], "length": len(value) if isinstance(value, (dict, list)) else None}
    if suffix == ".h5ad":
        try:
            import h5py
        except ImportError:
            return {"profile_note": "Install h5py to inspect AnnData schemas"}
        from profiles import h5ad_profile
        return h5ad_profile(path)
    if path.name.lower().endswith(".mps.gz"):
        with gzip.open(path, "rb") as stream:
            header = stream.read(8192)
        return {"mps_name_record": any(line.startswith(b"NAME") for line in header.splitlines())}
    return {}


def freeze(row, root, prefix, byte_limit):
    files = resolve_sources(row, root, prefix)
    if sum(path.stat().st_size for path in files) > byte_limit:
        raise ValueError("Source exceeds the configured size budget")
    evidence = []
    for index, path in enumerate(files, 1):
        stat = path.stat()
        checksum = file_sha(path)
        try:
            profile = public_shape(path)
        except (OSError, ValueError, KeyError, TypeError, StopIteration, csv.Error) as exc:
            profile = {"profile_error": type(exc).__name__}
        if (path.stat().st_size, path.stat().st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
            raise ValueError("Source changed during freezing")
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", path.name)[-100:]
        evidence.append({"id": f"D{index}", "mount": f"/source/{index:03d}-{name}", "bytes": stat.st_size,
                         "sha256": checksum, "suffixes": "".join(path.suffixes).lower(), "profile": profile})
    return files, evidence


def enrich_profiles(evidence, files, measured):
    """Bind additional measured format evidence to the exact frozen source bytes."""
    if measured is None:
        return
    supplied = measured.get('files') if isinstance(measured, dict) else None
    if not isinstance(supplied, list) or len(supplied) != len(files):
        raise ValueError('Measured source profile does not cover the frozen source files')
    records = {str(Path(row['path']).resolve()): row for row in supplied}
    for path, frozen in zip(files, evidence):
        record = records.get(str(path.resolve()))
        if not record or record.get('sha256') != frozen['sha256']:
            raise ValueError('Measured source profile no longer matches the frozen source bytes')
        visible = copy.deepcopy(record)
        if 'solve' in visible:
            visible['solve'] = {k: v for k, v in visible['solve'].items()
                                if k in {'status', 'seconds', 'value_valid'}}
        frozen['profile']['measured'] = visible
