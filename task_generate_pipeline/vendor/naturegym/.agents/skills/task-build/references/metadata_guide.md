# Phase 5: Metadata Guide

## Objective

Generate private `metadata.json` from [the template](../templates/metadata.json). Metadata records the goal, task classification, provenance and comparable results. Reuse prior extraction and verify its applicability to the final package. Do not duplicate full evaluation definitions; comparison entries cover goal-achievement items only.

## Sources

- **Current task information and screening/data-check records**: Use the established goal, resource descriptions, compute records and extracted comparison results with their source evidence.
- **Phases 0–2**: Use final instance ID/name mappings, packaged data and README task/submission requirements.
- **Phases 3–4**: Use verified tools and actual interfaces, implemented evaluation items/scopes and their conditions, and construction-check evidence.
- **Paper and supporting materials**: Check domain classifications and task types against the final task's scientific content. Verify reported results at their cited source locations, resolving concrete gaps rather than repeating full extraction.

## Field Definitions

### Identity and Goal

- `task_name`: Match the standalone README title.
- `source`: Source `paper_id` and `title`; private provenance, not Solver-visible content.
- `goal`: The source-established Level 1 goal, preserving the required result and scientific meaning of `task_info.goal.description` and the README task objective. Instance inclusion and exclusion evidence stays in screening/build records.

### task_types

Record a nonempty, unique array of concise labels for the scientific work required by the final G and deliverables, separately from domain classifications. Common labels include `prediction`, `parameter_estimation`, `design`, `optimization`, `mechanism_discovery`, `theorem_proving`, `reinforcement_learning` and `representation_learning`; use another specific type when needed. Include multiple types when the goal requires them. Classify the required work rather than the authors' solving technique. For method-oriented goals, specify the concrete research type instead of the generic `method_development` label.

### tooling_metadata

Use null when no core tools need description. Otherwise record `main_tools[]` with `name`, `purpose`, `required_by` (`solver`, `evaluator`, `both`), `interfaces`, `gui_interaction_required` and the actual `interface`. Describe selected core tools and task capabilities from `task_info.environment.dependencies`, reconciled with Phase 3's verified environment and Phase 4's implementation. Include Solver and evaluator tools as applicable; do not rank Python, CLI and GUI interfaces or turn providing a library into a prescribed solving method. `required_by` describes use, not confidentiality. `interfaces` is a nonempty, unique list of confirmed task-relevant interface types: `library_api`, `cli`, `service_api` or `gui`. `gui_interaction_required` is `true` when any required operation needs GUI interaction, `false` when non-GUI interfaces cover all required operations, or `null` when unresolved; `true` requires `gui` in `interfaces`. Reconcile both fields with upstream E and the verified final tool use. `interface` identifies the actual import/API entry, executable command, service interface or GUI software interface after preparation, consistent with upstream `interfaces`, `gui_interaction_required`, `requirements` and construction checks; keep Solver-permitted usage instructions in README and evaluator-only instructions in private implementation/records. Do not inventory ordinary dependencies that provide no core capability needing description.

### domain_metadata

- `primary_domains`: Nonempty array of distinct application-problem domains. Common names include Mathematics, Physics, Computer Science, Biology, Medicine, Materials Science and Chemistry; use another specific domain name when needed, not the label `Other`. Include multiple domains when the research problem substantively spans them; a computational method alone does not add Computer Science to the application domain.
- `sub_domain`: 1-3 words describing the specific sub-field (e.g., "Protein Folding", "Fluid Dynamics").
- `domain_tags`: 3-5 keywords representing methods, objects of study, or key concepts.

### compute_resource_requirements

Record required `solving` and `evaluation` objects, each describing one complete workload under one selected configuration. Estimate `solving` using the paper's corresponding solving method for all retained instances, including required training, adaptation, search, simulation and repetitions; estimate `evaluation` from the final evaluator for one complete evaluation of that submission, including required repetitions. The reference method does not prescribe the Solver's approach unless the task requires it. Count shared work once. Exclude environment installation/image construction, analyses outside G and additional independent Solver attempts. This is a reference execution estimate, not a promise about arbitrary Solver exploration or a mandatory runtime limit.

Read source reports in `task_info.environment.compute` under [Computing Demand](../../paper-filter/references/output_schema.md#computing-demand), including their workload descriptions and evidence, then reconcile them with final instances, tools, evaluator and construction-check observations. Preserve applicable reported fields and estimate only missing quantities; incomplete reports do not require re-estimating known values. Keep method, workload, hardware, concurrency and timing consistent rather than combining incompatible experiments. Reassess affected quantities when the workload or configuration changes. Estimates select a feasible task configuration, not a reconstructed claim about what the authors used.

Record and estimate demand from the information already available; a complete task-solving or evaluation run is not required solely to fill metadata.

#### Fields

| Field in each object | What to record |
|---|---|
| `cpu_compute.quantity`, `unit`, `severity` | Positive integer CPU quantity and its grade. Preserve applicable reported quantities and units: `physical_core`, `logical_processor`, `processor`, or `core_unspecified` when the source does not distinguish core type. Newly estimated quantities default to `physical_core`. Retain a relevant CPU model and the grading basis in `notes`. |
| `memory.quantity`, `unit`, `severity` | Positive finite system-RAM capacity in `GiB` for the selected workload and concurrency, and its grade. RAM is what the task needs in its execution environment, including bare-metal/container/sandbox execution; whole-machine RAM and an arbitrary container limit are not demand measurements. Exclude disk, swap and virtual address space. |
| `gpu_compute.model`, `count`, `memory_per_device`, `severity` | Selected GPU model/version, nonnegative integer count, and card-count grade. With GPUs, `memory_per_device` contains a positive finite `quantity` and `unit: "GB"` or `"GiB"` for configured single-card capacity, not measured task usage. With `count: 0`, use `severity: "none"` and omit `model` and `memory_per_device`. |
| `runtime.quantity`, `unit`, `severity` | Positive finite elapsed hours or a bounded range string such as `"2-4"`, `unit: "h"`, and its grade. No `scope` field: metadata always describes complete-workload wall time under the selected configuration. |
| `notes` | Nonempty explanation of the reference paper method or final evaluator, adopted report/check locations, reused versus estimated fields, workload counts/calculation and hardware/concurrency assumptions. Include relevant qualifications and uncertainty without copying reports or logs. |

Complete compute fields with reported, extrapolated or estimated values; do not use null or zero for missing demand. Estimates may be coarse or ranged but need explicit grounds. If a necessary workload is undefined, resolve it before completing this record. Keep uncertainty and its basis in `phase_5.uncertain_tags`; uncertainty alone is not a scientific rejection.

#### Select and Estimate a Configuration

Use applicable reported configurations first. Preserve a known applicable GPU model while estimating a missing count; when only the count is known, retain it where applicable to the selected model and workload. For missing GPU models, compare RTX 4090 24GB and A100 80GB PCIe candidates against required operations, precision, memory and supported execution. Resolve model variants from sources where possible; otherwise identify the selected variant as an assumption. Preserve other applicable reported models rather than converting everything to these references. Missing GPU details do not cancel reported GPU use, and CPU information alone does not establish GPU absence.

For estimated allocations, balance resource capacity and complete-workload elapsed time for the reference method or final evaluator. Among feasible GPU configurations with practical runtimes, prefer fewer GPUs; equal-count candidates default to 4090 unless documented workload-specific support/performance favors A100. Permit supported multi-GPU configurations when needed. Do not select CPU-only execution or an excessively slow configuration merely to minimize card count, or change G, data, required training or precision to fit fewer cards. Card memories do not form one usable pool without supported sharding.

For missing quantities, use method complexity, actual data volume and representation, model architecture/scale, numerical precision, batch/input size, training versus inference, necessary iterations/calls and implementation parallelism. Prefer relevant check observations or applicable tool/model measurements where available; a smoke test alone does not establish full demand. Estimate RAM/device working sets from concurrently resident data, models, gradients/optimizer states where applicable, buffers and worker copies, not archive sizes or weights alone. Source GB/GiB conversions must retain their meaning; 1 GiB is 2^30 bytes.

#### Complete-Workload Runtime

Use an applicable complete source timing, extrapolate supported partial timings, or estimate missing timing from the work above. A simple calculation is one-time work plus the sum of operation time times necessary execution count. Scale only comparable operations; data/model-size changes require a supported complexity or measured scaling relationship, not automatic linear extrapolation. Use applicable reported scheduling; otherwise choose supported instance and within-instance concurrency that balances resources and elapsed time, accounting for dependencies. Source descriptions, implementation documentation and applicable measurements can support parallel execution without a new full parallel run; do not assume linear speedup. State the schedule and scaling assumptions in `notes`. Resource capacity follows peak concurrent work rather than the sum of every instance's allocation.

For unusually long estimates, recheck workload counts, throughput assumptions and estimated hardware/concurrency choices for the same reference method or evaluator. Retain justified long runtimes with their basis and uncertainty rather than omitting necessary work or substituting an easier solving method.

For `evaluation`, base resource and runtime estimates on Phase 4's final evaluator and verification. Reuse representative complete-run observations where available. For partial checks, account for the full operation counts, data scale, repetitions and shared work, and estimate unmeasured necessary steps. Checks that skip required computation or fail early do not establish complete evaluation cost. Record the observed scope and extrapolation in `notes`.

Interpret source `wall_time`, `cpu_time` and `gpu_time` before using them. Device-hours need known counts and execution overlap to support elapsed-time conversion; CPU and GPU times cannot simply be added when work overlaps. Do not fabricate missing source device counts to make a reported timing appear measured on the selected configuration, or convert hardware time using device counts or peak-performance ratios alone. Use such partial evidence as estimation support with its limits. Include unmeasured necessary work and retain ranges. Required Judge request latency contributes to evaluation time, while the common Judge backend's hardware is not task-local GPU demand.

#### Grades

| Field | `severity` |
|---|---|
| `cpu_compute` | `low` (<=32 physical cores), `medium` (>32-128), `high` (>128) |
| `memory` | `low` (<=64 GiB), `medium` (>64-256), `high` (>256) |
| `gpu_compute` | `none` (0 GPUs), `low` (1), `medium` (2-8), `high` (>8) |
| `runtime` | `under_1h` (<1h), `1_to_4h` (1<=h<4), `4_to_12h` (4<=h<12), `12_to_24h` (12<=h<=24), `over_24h` (>24h) |

CPU grades use physical-core quantities. When retaining another unit, keep its quantity/unit unchanged and state the supported physical-core mapping or a separately identified workload-based physical-core estimate for grading in `notes`. Do not apply core thresholds directly to processor-package or logical-processor counts, or present the estimate as a recovered source hardware mapping.

GPU grades describe card count; model and per-card memory preserve configuration differences. Do not assign GPU grades to another accelerator type. For bounded runtime ranges, grade the upper bound and retain the range; this does not make timings on different hardware equivalent. No separate GPU-memory grade is needed.

### evaluation_weights

Record one positive numerical weight per goal-achievement item ID, summing to 1. Validity and artifact-format checks have no weight. Do not count a derived aggregate and its constituent scores twice.

Allocate weights by each item's contribution to G and the priorities supported by the paper's stated aims, central claims and substantive validation. Core scientific requirements should carry more weight than secondary aspects. Where findings and their scientific justification are assessed separately, follow the [rubric weighting principle](evaluator_guide.md#weighting-results-and-justification). Use equal weights only for items of comparable scientific importance. Record the item IDs, relative-importance rationale and supporting source locations in `task_build.phase_5.evaluation_weights_reason`.

These weights are for an external evaluation protocol. The task evaluator returns raw item results, including their defined internal aggregation, without cross-item normalization or a task total.

### evaluation_scope_weights (optional)

Use this object only when a goal item's assessment scopes need different relative weights. Key entries by item ID; each value lists `{"instances": ["package instance name", ...], "weight": number}`. A scope is one referring instance for `scope: instance`, or one complete `instance_groups` entry for `scope: joint`; weight the joint group once, not each participant.

```json
{
  "E1": [
    {"instances": ["instance_a"], "weight": 0.6},
    {"instances": ["instance_b"], "weight": 0.4}
  ]
}
```

Omit the field or an item entry for equal scope weights; `{}` is also valid. A configured item must cover every applicable scope exactly once with positive finite weights summing to 1. Use the source-supported scientific importance of these scopes, not their compute cost or an observed Solver result. Record the item/scope identities, allocation rationale and source locations in the existing `task_build.phase_5.evaluation_weights_reason`. Keep these weights separate from internal rubric-component weights and source-defined scientific aggregation; they do not change instance boundaries or joint evaluation definitions.

### evaluation_comparisons

Record one entry per goal-achievement item, grouping its comparisons by assessment scope and then by source method/result. Validity items have no entry; their standards and required references remain in V and D.

```json
{
  "item_id": "retained goal-achievement item ID",
  "comparison_results": [
    {
      "scope": "instance",
      "instances": ["package instance name"],
      "results": [
        {
          "method": "source method or result identifier",
          "method_role": "paper_method | baseline | null",
          "variant": "string | null",
          "is_sota": "boolean | null",
          "baseline_roles": [],
          "result": 0.7,
          "conditions": null,
          "source": "precise reported-result location and supporting material IDs"
        }
      ]
    }
  ]
}
```

Preserve the upstream [comparison groups, result definitions and selection rules](../../paper-filter/references/output_schema.md#comparison-groups-and-results). Map group `instance_ids` to package `instances` through `task_build.instances`. Each `instance` group names exactly one instance, with one group for every instance assessed by the item. A `joint` item has one comparison group for each declared `task.json.evaluation_items[].instance_groups` entry. The group's scope must match `task.json`; do not mix scopes, repeat a group, broadcast an aggregate to individual instances or put different instance/group values inside one result. Keep method/variant identity, SOTA status, `baseline_roles`, conditions and provenance within each result. Program groups retain only the required SOTA and zero reference plus an optional representative under [Program Reference Selection](../../paper-filter/references/level3_rules.md#program-reference-selection). Confirm a strict improvement interval for each final scope, and reconcile all references with the theoretical bounds in task.json. A missing or reversed interval requires source/selection repair; do not substitute an arbitrary small denominator. For program `target` goals, also copy the group's finite `target` in the raw units and verify its source-supported definition and conditions. Require `abs(SOTA - target) < abs(zero - target)` and select tied/representative methods by distance. Keep `results[].result` as the reported raw quantity; absolute distance is computed during normalization. Omit `target` for other groups. Raw theoretical bounds remain in `task.json.result.min/max`. Item weights remain separate in `evaluation_weights`.

`result` retains a finite number or substantive text, not an object. `variant` identifies a source method version/configuration only when needed to distinguish its result; otherwise use null. `conditions` records result-specific assessment conditions and any reported ± expression with its known meaning. Use null when all necessary conditions are established by the shared definition and no additional conditions apply; null does not stand for missing required information. Preserve a source-defined joint result even when its participants are solved separately; do not derive per-instance comparisons merely because individual observations are obtainable. Follow the upstream rules for units, statistical interpretation, ± values, plots and source evaluation protocols rather than re-extracting established information. Source-score absence does not mean that a valid non-numerical comparison basis is absent. Rubric ranges/maxima define the scoring scale, not source comparison results. Do not invent a number for a non-numerical source finding.

### Verify and Obtain Corresponding Results

Apply [Comparison Evidence](../../paper-filter/references/level3_rules.md#comparison-evidence) to the final package: confirm basic task/data, assessment-scope and metric correspondence under the adopted evaluation conditions and the pipeline's accepted differences. Reuse supported findings and add targeted checks only for specific concerns; complete recovery of source-method settings or reproduction of reported scores is unnecessary. Resolve conflicts under [Rule 2.1](../../paper-filter/references/level2_rules.md#conflicting-source-definitions); do not select a different criterion during metadata preparation.

1. Read the current goal items' `comparison_results[].results[]`, resolve result `source_ids` in `source_materials[]` and reuse recorded source evidence and acquisitions. Verify item/instance mappings and that result meaning, objects, requirements and conditions agree with the final README, task.json and evaluator. Revisit cited passages/materials for verification or gaps; do not repeat complete extraction.
2. Verify reported values or findings at their cited locations. For [permitted aggregates](../../paper-filter/references/level2_rules.md#aggregating-reported-results), verify the reported constituents and recorded calculation, preserving calculated status and provenance under the shared comparison schema. Inspect existing inventories/files before acquiring additional source documents under the [shared rules](../../paper-filter/SKILL.md#supplementary-and-other-source-materials). Correct extraction errors with source evidence and a change record. For a confirmed source implementation defect, preserve the reported score and carry its defect description, source/code location, known or possible impact and interpretation limits into `conditions`; do not treat the defect as an extraction error or replace the score. Outside permitted reported aggregates and supported analytical no-information expectations for program zero references, do not calculate new comparison scores or evaluate reference artifacts to supply missing results. Preserve each analytical reference's policy, derivation, conditions and supporting definition sources.
3. Recheck method classification and confirm that each numerical comparison group retains the best corresponding reported or permitted aggregate method result, marked `is_sota: true`. Preserve the distinction between a source-reported ranking and one established by the permitted calculation. This result may come from the authors or another method. Program groups also require the selected zero reference; only the representative is optional. Other rubric baselines remain optional. Do not skip a known better same-condition result and relabel a weaker baseline to obtain a pass. Preserve source data and necessary statistical conditions under the [source evaluation protocol](../../paper-filter/references/level2_rules.md#source-evaluation-protocol). For an eligible fixed-split adaptation, retain the reported mean, its original partition/run counts and aggregation, and the difference from the task evaluation; no matching single-split source score is required. A documented source ranking is not a newly established ranking on the packaged split.
4. Record unavailable/incomparable results and inspected evidence in `phase_5.comparison_issues`. Numerical metrics need the corresponding reported or permitted aggregate SOTA result for every required assessment scope, even when implemented with a rubric. Other source-supported rubric judgments may use `comparison_results: []` when their standards provide the necessary comparison basis without a reported performance result; verify that basis for every referring instance. Otherwise repair or make a supported whole-instance decision; do not pass with missing groups, empty `results` or `result: null`.

Keep comparison results and their source evidence distinct from evaluator correctness tests. Recomputing a known result may verify the implementation, but its output cannot replace a missing or incompatible source comparison. Do not substitute theoretical optima, change source data or weaken G to supply a comparison. Keep source evidence private.

## Correctness and Recording

Check item IDs and instance names against task.json, task name and goal against README and `task_info.goal`. Verify every comparison's substantive applicability; matching identifiers alone is insufficient. Check numerical units/directions/statistics or non-numerical meanings against the implemented criteria. Keep classification and compute estimates supported and identify uncertain fields.

Record issues in `task_build.phase_5`: `uncertain_tags`, `comparison_issues` and necessary notes. Low confidence in classification or resource-demand estimates is descriptive uncertainty, not a construction failure; record the affected fields and estimation/evidence limits rather than raising confidence without support. Missing required comparison evidence or execution conditions remain failures of their respective checks. Update metadata when resources, instances or the actual scope change. Keep it private, outside Solver-visible documents and resources.
