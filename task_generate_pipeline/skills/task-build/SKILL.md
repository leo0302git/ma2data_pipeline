---
name: task-build
description: Write a complete runnable NatureGym-style discovery task from the frozen plan.
---

# Build against the supplied templates

Existing authored files may be an unverified recovered draft. When authoring_draft
is supplied, its source identity has been checked, but its old interface or claims
may be obsolete. Inspect and reuse valid implementation parts, then adapt all
deliverables, grading and documentation to the current frozen plan and backend.
It supplies no passing verification, data-preparation or metadata receipt; the host
reruns preparation and all checks. Do not retain an unassessed executable obligation
or copy previous references into public data. Always own the resulting package.

An implementation library is interchangeable unless the frozen plan explicitly makes that API
part of the assessed interface. Preserve all scientific and data requirements, record equivalent
reader choices in your summary, and use the observed installed tools. Metadata follows preparation;
its being pending at build time is expected and is not a reason for needs_review. If there is a
genuine semantic contradiction in the frozen plan, return the concrete gap for a deliberate rescope.

You own the task package. Implement the work for this source, using whichever scientific method the data and locked assessment require. The host supplies the exact reference `task/evaluation/evaluator.py` and runs its static checker. Implement the task-specific metric hooks. The `templates` input is a schema example. Replace illustrative data, fields, and code with real requirements.

Use the supplied `source_evidence` schema and aggregate facts to write the implementation in this stage. Reading a `/source/...` path through the broker returns only that same profile. Put data-content checks in `work/prepare.py`, which receives the real read-only files after the draft is written. Complete the draft when the plan and aggregate facts support it.

1. Write `work/prepare.py`. Use the exact `/source/...` mounts in the frozen source profile. Write allowed data under `/out/public/` and protected references under `/out/private/`. Do not copy a complete raw file into the public tree. Validate layout and correspondence, use a reproducible split, and fail explicitly when the source cannot support the task. The code runs offline in the declared evaluator image. For genuine supporting calibration, it can write `/out/evidence/calibration.json` as a list of objects with exactly `id`, `method`, `value`, `conditions`. Use finite numeric values and explanatory text, never raw answers. The host publishes public and private directories separately.
2. Write `task/input/README.md` with level-2 headings Scientific Problem, Task Objective, Instances and Inputs, Evaluation, Deliverables, and Submission Interface. Document how to solve the assigned problem and what to submit under `{instance_name}/`. Protect exact source identity, original method, private material, and output answers. Write `task/input/data_description.md` with Directory Structure, Dataset Overview, File Formats & Schemas, and Special Notes. List all visible files, roles, units, and joins.
3. Write `task/task.source.toml` with schema version `1.4`, `task.name = "naturebench/<case_id>"` and a description consistent with the final README. Write `task/task.json` with schema version `1`, named instances, per-instance input/reference/deliverable mappings and all retained evaluation items. Visible data paths begin with `input/data/`; private reference paths begin with `evaluation/assets/`. A deliverable path is relative to its instance's submission directory.
4. Write `task/environment/Dockerfile` starting with the approved `FROM` image. Optional separate evaluator image uses `Dockerfile.eval` and `packages.eval.json`. Record installed imports and their pinned versions in `packages.json`, and provide `check_resources.sh` and `check_resources.eval.sh` for actual role checks. Use the approved local runtime image and local build context. No network, sidecar, external tool records, or submitted-executable runtime is available in this initial runner. Use `needs_review` if such a facility is necessary. Role check scripts see the Solver-visible tree at `/task/input`; the evaluator role also sees private files at `/task/evaluation`.
5. Implement `task/evaluation/metrics.py` with `SubmissionError`, `validate_format(instance, role, path)`, `prepare(item, instances, context)` and `evaluate(item, instances, context)` where applicable. `validate_format` sees public artifacts only. Normal evaluation returns the raw scientific metric and separates invalid submissions from evaluator faults. Use `context.resource`, `context.artifact`, and the native types documented in the supplied hook template. Program and rule-only rubric checks are available. A rule-only rubric also needs `evaluation/rubric.json` and `evaluation/rubric.py`. A criterion requiring an LLM Judge must be flagged for real Judge execution and cannot pass without it.

The metadata stage follows data preparation. Keep evaluation assets and raw data out of the Docker build context. Do not request a source upload or use a network call. Write complete files through the broker, including ancillary Python modules in `task/evaluation/lib/` if needed. All results are private until independent verification.

Calibration summary limits are 32768 UTF-8 bytes in total and at most 32 rows.
Each row has exactly id (text, up to 360 characters), method (text, up to 2048),
value (finite native number, never bool or null), and conditions (text, up to 4096).
Keep full private solve/audit logs separately. Explain reference validity and any
validity-gated baseline honestly; do not invent a numerical measurement to fit a schema.

# Output

Keep data_description.md standalone and limited to the provided input datasets and their schemas.
Put submission/output schemas and evaluation instructions in README.md. Do not cross-refer the
data description to README for an input schema or repeat the submission/evaluation instructions there.

Retain enough private provenance for an independent reader to audit source-to-package fidelity:
source checksums, anonymization/observation/feature mappings, split membership and seeds, label
aggregation rules, transformation parameters, and genuine reference solve options/logs/status/bounds.
These remain under evaluation/assets, never Solver data. Later controls inspect both the actual
raw source and derived payload independently, so successful preparation assertions alone are insufficient.

Write the package files and preparation program. Return `result_json` with a nonempty `summary` describing the authored implementation.


# Exact execution contract

Use `schema_rules`, `native_static_checker_source`, `write_contract`, `runtime_capabilities`, and `available_text_paths` when supplied. Request exact listed broker paths, including the `task/` prefix. Read `evidence/diagnostics.json` and `evidence/previous-diagnostics.json` when available, and inspect inline `previous_diagnostics` and `runtime_evidence` before reporting an unknown error. Read source profiles as evidence only. Repair concrete failures while preserving assessment semantics. Never downgrade validation or assert a run succeeded without real evidence.

Use the actual installed package versions from runtime_capabilities. Dockerfiles use single-line instructions and no network or ENTRYPOINT/CMD/USER instructions. Preparation helpers may be written in work/prepare_lib/*.py and are mounted at /program/prepare_lib. Read-only task/evaluation/lib modules are also available at /task/evaluation/lib during preparation. No other /task files exist then. Do not create helper tools under task/input directly. Generated public data must come from preparation outputs. Compute and record real numerical reference and baseline calibration observations in /out/evidence/calibration.json for all goal metrics, with method and conditions; a scalar observed optimum does not prove an arbitrary baseline.


# Explicit comparison profile

Apply comparison_policy.mode and its rule. generated_measured explicitly adapts only the original reported-comparison admissibility rule. Preserve every scientific constraint and other verification requirement. Actual strong/weak methods must use Solver-permitted data; private-answer oracle controls are separate. Numerical calibration links alone are not scientific proof: independently inspect actual method programs, split, metric, information boundaries and execution receipts. Keep the original strict profile distinct. Never describe generated_measured as literature SOTA or strict original-rule certification.
