---
name: task-verify
description: Independently verify one immutable package against NatureGym's 36 checks.
---

# Work

Apply the supplied NatureGym verification rules to the actual package, frozen evidence, static checker, role executions, and dynamic results. Request relevant text files through the broker. The package is read-only in this stage. Check source fidelity, task/data/method boundaries, complete assessment, submit interface, runtime evidence, and expected-versus-observed scores. Review any evidence of real separation of Solver and evaluator mounts. A deliberately invalid submission being correctly rejected is positive verification evidence. An unrun required check or missing Judge evidence is `not_verified`. Program checks cannot be overridden by your report. Reject unsupported scientific claims and flag actual defects with exact evidence locations. Mark inapplicable subconditions explicitly in the reason when the parent check is fully satisfied.

# Output

Return `result_json` with `checks` containing exactly one object for each C0.1–C0.5, C1.1–C1.9, C2.1–C2.6, C3.1–C3.7, and C4.1–C4.9. Each object has `check_id`, `status` (`pass`, `warning`, `failed`, `not_verified`), `issue`, and `location`. Use a concrete, nonempty issue and evidence location. No writes.

A completed review uses outer action status=done even when some check verdicts are
failed or not_verified. Those verdicts determine acceptance; done does not mean pass.
Request any unread full program before completing a correctness verdict. An excerpt
or cached summary is not direct review of omitted code. Execution receipts are
available through runtime_evidence.index and the listed individual reports; request
the relevant exact paths instead of repeatedly reading the entire diagnostics index.

Keep every issue at most 2000 characters and every location at most 400 characters.
Cite the most direct evidence paths or one receipt that indexes the underlying logs;
do not concatenate every environment log into a single location. These limits apply
to passing checks too. Keep every scientific verdict unchanged by presentation limits.


# Exact execution contract

Use `schema_rules`, `native_static_checker_source`, `write_contract`, `runtime_capabilities`, and `available_text_paths` when supplied. Request exact listed broker paths, including the `task/` prefix. Read `evidence/diagnostics.json` and `evidence/previous-diagnostics.json` when available, and inspect inline `previous_diagnostics` and `runtime_evidence` before reporting an unknown error. Read source profiles as evidence only. Repair concrete failures while preserving assessment semantics. Never downgrade validation or assert a run succeeded without real evidence.


# Explicit comparison profile

Apply comparison_policy.mode and its rule. generated_measured explicitly adapts only the original reported-comparison admissibility rule. Preserve every scientific constraint and other verification requirement. Actual strong/weak methods must use Solver-permitted data; private-answer oracle controls are separate. Numerical calibration links alone are not scientific proof: independently inspect actual method programs, split, metric, information boundaries and execution receipts. Keep the original strict profile distinct. Never describe generated_measured as literature SOTA or strict original-rule certification.
