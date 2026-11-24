---
name: verification-cases
description: Independently design runnable correctness and rejection controls for the native evaluator.
---

# Work

This is the program-authoring phase. Your broker can read and write files; it cannot
execute programs on a continue action. After you return done with a complete program,
the host executes it in the controlled container, runs native scoring for its controls,
and supplies the resulting receipts to the independent final reviewer. A missing receipt
for the current, not-yet-executed program is expected here: do not keep requesting that
execution on continue, claim it already passed, or stop solely because its receipts do
not exist. Return a concise authoring summary, explicitly leaving execution unverified.
Actual previous execution failures remain evidence that the program must address.

Cover each distinct scientific predicate and score branch, using prior_attempt_findings to
close identified evidence gaps. A correct optimum and an infeasible solution alone do not test
rejection of a feasible non-optimal solution. For MILPs, independently construct and verify a
feasible solution outside the objective tolerance, plus a row/bound-feasible fractional solution
that isolates integrality rejection. A convex combination of two verified distinct integer
feasible solutions can supply the latter when applicable. Use actual independent source
mathematics to derive expectations; never import the scorer or copy its computations.
If a requested branch is mathematically unreachable, provide a concrete proof and supporting
observations instead of fabricating a fixture. Report resource limits honestly.
An exact-feasibility optimality certificate alone does not prove unreachability for an
evaluator that accepts numerical feasibility tolerances. Use the published tolerances
when constructing a probe or a conservative bound. The generated program must record
its measured supporting observations; the final reviewer decides whether they suffice.

The host also mounts each frozen /source/... file read-only during this program. Independently
audit the new package against those actual source bytes. Do not call or import evaluation/metrics.py
or its task-specific helpers to establish your expected outcomes. Installed independent readers
(for example HiGHS for MPS and h5py/scipy for AnnData) are available as listed in runtime_capabilities.
Compare complete mathematical content or observation/feature/annotation correspondence, rather
than trusting a preparation assertion. For optimization compare the original and public models'
dimensions, coefficients, bounds, integrality, objective offset and direction, accounting for
documented anonymization; compare the canonical model used by scoring as well. Verify the private
reference against the original problem and inspect the actual solve log/status and bounds. For
annotation audit source-to-derived feature/ID correspondence, disjoint reference/query observations,
label aggregation and the scientific meaning of the source annotations. Check the actual public
payload uses only declared problem data and contains no private solutions, provenance or label fields.
Write /out/source_audit.json with exactly three checks, IDs source_fidelity, reference_integrity,
public_payload_boundary. Each has id, status (pass/failed/not_verified), a nonempty method and a
nonempty observations object containing measured counts, comparisons, hashes and limitations.
Record scientific uncertainty honestly. The independent final reviewer examines both this program
and its real output; an unsupported pass statement is insufficient.

Generate 3–32 controls in total, covering all three required kinds. Invalid content may pass
standalone shape checks and be rejected as submission_failed during full scoring when a public
inventory mismatch is discovered. Model that case honestly; at least one format or full-score
content rejection is required for each invalid control. Scientific infeasibility with valid
content is a valid_wrong control instead. Keep the native evaluator's classifications intact.

Read the package specification, actual data layout, metric contract and approved evidence. Write `work/make_cases.py` that reads the mounted package at `/task` and produces private control artifacts under `/out`. Derive expectations independently from the evidence or a simple independent calculation. A known-answer fixture can read protected references in this isolated verification step. Never put a protected fixture in Solver data. Do not call or copy the evaluator implementation to generate the expected scores.

Cover every instance and declared evaluation scope, in separate probes for a known correct submission, a syntactically valid but scientifically wrong submission, and a missing or malformed submission. The valid-wrong test must show an actual score or acceptance difference. Keep unrelated roles and scopes independently represented. Make expected errors distinguish invalid content from evaluation failure.

Write `/out/cases.json` as `{ "cases": [...] }`. Each case has an `id` slug, `kind` (`known_correct`, `valid_wrong`, `invalid`), a nonempty `method`, a `formats` array of `{instance, role, status}` with `completed` or `submission_failed`, and a `scores` array of `{item_id, instances, status, value, accepted}`. `accepted` can be null for validity rows, `value` can be null for confirmed submission failures. Scores and format coverage must match all native scopes and deliverables for each case. Put submissions in `/out/submissions/<id>/<instance>/...`. The host runs both `--format-only` and normal scoring in the actual evaluator image, with distinct result locations.

# Output

Write only `work/make_cases.py`. Return `result_json` with a nonempty `summary`.


# Exact execution contract

All JSON outputs must use native Python scalars and containers. Explicitly convert
NumPy/solver integer, floating and boolean scalars before json.dump; convert arrays
with tolist when appropriate. Keep allow_nan=False and represent nonfinite bounds
explicitly according to the contract. Serialization errors are program failures,
not scientific audit outcomes.

Respect the actual CPU and memory budget. Keep large sparse model matrices sparse;
do not allocate rows*columns dense arrays just to compare coefficients or residuals.
Cache solver binding arrays once before traversing them; repeated property reads
may copy complete lists. Inspect source dimensions and nonzero counts first.

When verification_preparation_failure is supplied, inspect its retained log and
previous program, and repair only work/make_cases.py. Preserve independent checks
and scoring expectations. If the error exposes a real source/reference inconsistency,
report the gap instead of removing the comparison or changing expected scores to
match the evaluator. The candidate package remains immutable during this repair.

Use `schema_rules`, `native_static_checker_source`, `write_contract`, `runtime_capabilities`, and `available_text_paths` when supplied. Request exact listed broker paths, including the `task/` prefix. Read `evidence/diagnostics.json` and `evidence/previous-diagnostics.json` when available, and inspect inline `previous_diagnostics` and `runtime_evidence` before reporting an unknown error. Read source profiles as evidence only. Repair concrete failures while preserving assessment semantics. Never downgrade validation or assert a run succeeded without real evidence.

For validity-gated goals the native evaluator may return status=completed, value=null, accepted=false. Mirror that exact contract when a well-formed submission violates scientific feasibility. A truly malformed artifact yields submission_failed. Inspect the supplied native APIs rather than guessing.


# Explicit comparison profile

Apply comparison_policy.mode and its rule. generated_measured explicitly adapts only the original reported-comparison admissibility rule. Preserve every scientific constraint and other verification requirement. Actual strong/weak methods must use Solver-permitted data; private-answer oracle controls are separate. Numerical calibration links alone are not scientific proof: independently inspect actual method programs, split, metric, information boundaries and execution receipts. Keep the original strict profile distinct. Never describe generated_measured as literature SOTA or strict original-rule certification.
