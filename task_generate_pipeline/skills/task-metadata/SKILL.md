---
name: task-metadata
description: Complete task documentation and private NatureGym metadata using observed data and scoring evidence.
---

# Work

This stage can write only task/metadata.json, task/input/README.md,
task/input/data_description.md, task/environment/check_resources.sh and
task/environment/check_resources.eval.sh. The shared write_contract describes the wider
authoring workflow; it does not grant those other paths here. If task/task.json or metrics.py
has a routine path defect, record it in the summary and complete the allowed metadata work.
Native checks and task-fix will handle that defect before release.

prior_attempt_findings are historical. Use current_boundary_check and the current execution
receipts to determine which old findings have been resolved. This stage completes documents;
it does not certify release or require future independent checks to have already run. Return done
when its deliverables are complete. You may correct check_resources.sh/check_resources.eval.sh
paths to match the actual package; these scripts will be executed again afterward. Do not edit
the image definition or scoring semantics here. Flag a genuine remaining scientific evidence gap
as needs_review; do not repeat a superseded failure as a new blocker.

For CPU severity, use a sourced physical-core mapping or an explicitly workload-based estimate
with its unit and estimation basis. A Docker CPU quota alone is not a hardware physical-core
measurement. For a serial algorithm, an estimated one-physical-core workload may be justified
as an estimate, while separately recording the tested logical-CPU quota.

Inspect the actual packaged file manifest, data schemas, Docker image identities, frozen adaptation plan, and authorized calibration observations. Read the task README, scoring manifest, and metrics through the file broker as needed. Synchronize names, instance scopes, input mappings, compute estimates, scoring descriptions and private source attribution. Preserve benchmark provenance only in private metadata. Put `metadata.json` at the task root. Use the NatureGym metadata template. Provide evaluation weights and scope-matched comparison results for the relevant items. Cite the supporting evidence and evaluation conditions for each numerical reference. Never label a guessed or unrelated score as SOTA. Use `needs_review` if a required comparison basis is absent. A computed reference is allowed only when supported by its documented method and the applicable reference rules.

# Output

Write `task/metadata.json`. Refine the two public Markdown files only to match the measured package. Return `result_json` with a nonempty `summary`.


# Exact execution contract

Use `schema_rules`, `native_static_checker_source`, `write_contract`, `runtime_capabilities`, and `available_text_paths` when supplied. Request exact listed broker paths, including the `task/` prefix. Read `evidence/diagnostics.json` and `evidence/previous-diagnostics.json` when available, and inspect inline `previous_diagnostics` and `runtime_evidence` before reporting an unknown error. Read source profiles as evidence only. Repair concrete failures while preserving assessment semantics. Never downgrade validation or assert a run succeeded without real evidence.


# Explicit comparison profile

Apply comparison_policy.mode and its rule. generated_measured explicitly adapts only the original reported-comparison admissibility rule. Preserve every scientific constraint and other verification requirement. Actual strong/weak methods must use Solver-permitted data; private-answer oracle controls are separate. Numerical calibration links alone are not scientific proof: independently inspect actual method programs, split, metric, information boundaries and execution receipts. Keep the original strict profile distinct. Never describe generated_measured as literature SOTA or strict original-rule certification.
