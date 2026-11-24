# Level 3: Resource and Task Completeness Filtering

## Level Responsibilities

Determine whether the task retained at Level 2 has the resources needed for complete solving and evaluation under its declared requirements. Confirm instance boundaries and relationships by combining Level 2 candidates and O/V requirements with D/E evidence. Inspect resources(data and environment) for the whole task and each instance, covering solving and evaluation. Apply the [core definitions](core_definitions.md).

### Resource Scope

Task resources include data and environment support for solving and evaluation. Classify them by actual use:

- **Data (D)**: Development materials, problem inputs and evaluation references, such as training, validation and test data, labels, measured observations, dictionaries, knowledge bases, model weights supplied as development foundations or research objects, and code or repositories that the task requires the Solver to analyze, modify or optimize.
- **Environment (E)**: Tools and execution support, such as libraries, simulators, standalone software, locally deployable services, fixed model tools with their weights and other runtime assets, necessary dependencies, and computing resources.

### Inspection Depth

Use lightweight checks without downloading task data or cloning repositories; full acquisition belongs to data-check. Read the paper, supporting documents, repository listings, data previews and metadata, and check resource routes and permissions. Existing local materials may be inspected; configured access still needs an actual resource/permission check.

Only when these checks cannot resolve a specific screening question, download the smallest necessary portion for verification and stop once it is answered. Reuse existing files and register retained inspection materials under the [source-material rules](../SKILL.md#supplementary-and-other-source-materials). Record the question, inspected scope and observed result in rule evidence. Do not install environments or run solving/evaluation.

Perform the necessary source and access checks before deciding, and ground every passing decision in their evidence. A pass requires supported resource identity, required contents, access and task support. Checks that require full acquisition or execution remain downstream; preserve source-reported facts as such and record inspection limits in rule evidence. Lack of full acquisition alone is not grounds for rejection. Missing essential information, unresolved contradictions or unsupported access/task support cannot pass by promising later verification.

## Identification and Extraction

Trace the resources required by the retained goal, candidate instances, deliverables and evaluation items. Use the paper's data/code descriptions, supporting materials, resource documentation and repository inventories within the [defined inspection depth](#inspection-depth). Inspect repository READMEs and related documentation for acquisition instructions and links to required D/E resources; follow the relevant links to confirm where those resources are supplied.

For each required resource, locate its acquisition or preparation routes and record all applicable values in `source.acquisition_scenarios` using the [classification table](output_schema.md#acquisition-sources). Follow the applicable lightweight checks and record source locations, versions and supplied components. In `source.instruction`, identify each URL's purpose, supplied contents and applicable scenarios, then give the complete, source-supported steps, including necessary inputs, prerequisites, acquisition conditions and configuration requirements, and expected outputs. Distinguish required links from alternatives.

### Data D

1. **Identify source data and acquisition routes.** Identify the data used in the paper's task and locate its acquisition sources from the paper, supplements, repository and data documentation. Establish each resource's contents and version, then trace its use as development material, problem input or evaluation reference against the permitted starting point, required Solver work and V. Include required external data and source-defined generation/preparation inputs; the source method's outputs cannot replace missing task inputs.
2. **Determine roles and selected contents.** Classify resources by actual use: D_dev supports development, X_test supplies the objects/inputs on which the solution operates or is tested, and Y_ref supplies information used to judge its behavior or results. Determine data roles by actual use, not resource names or access permissions. Include task inputs/reference contents obtained through services. Pretrained weights supplied as development foundations belong to D_dev; models serving as the task's objects of study or testing belong to X_test. Classify other uses by the same role definitions. For code tasks, distinguish target or task-defined starter code in D from execution tools in E and the assessment implementation in V; preserve required structure and supporting files. Select portions of a shared repository by actual role and access, not file extension. Preserve source-defined objects, sample selections, splits, permitted fields and configurations.

   Use the task/evaluation definitions and data use across compared methods or variants to distinguish:

   - **Source-method selection**: If selecting a subset is part of the method's own preprocessing or solving, retain the full task-permitted inputs before that selection. Do not extract only the method-selected subset; the Solver decides which permitted inputs to use.
   - **Task-defined subset**: If the paper defines its task on a subset of a larger data source, retain only that specified subset as the task scope. Do not expand it to the full source dataset.

3. **Record the shared inventory.** Record each resource once in `data.sources[]` with a stable ID, contents, version and acquisition source. Use instance bindings to distinguish its roles and selections. Keep source-reported counts and coverage distinct from what was inspected, stating the objects and conditions they describe. Do not extend an aggregate count or partial preview to every object or instance, or treat the requested output range as verified input/reference coverage.

### Environment E

1. **Identify core tools and dependencies.** Identify core scientific tools/dependencies used by the paper or needed for this task's solving and evaluation, including libraries, simulators, standalone software, script tools, locally deployable services and fixed model tools with their runtime assets. Software for loading, training or analyzing models belongs to E even when the model artifacts belong to D. Record as E entries the core dependencies that directly support an identified solving or evaluation capability; leave the full implementation dependency inventory, including general helper libraries and transitive dependencies, to task-build. Do not include construction-only tools or supply the reference solution.
2. **Identify use and preparation conditions.** For each dependency, record the work it supports, confirmed `interfaces` and `gui_interaction_required` under the [E fields](output_schema.md#environment-e). In `requirements`, identify which operations each usable interface covers and the necessary version, configuration and runtime conditions. Distinguish an available GUI from operations that require it. Classify the required acquisition routes and identify the required files, sources, acquisition steps and expected contents in `source.instruction`; ordinary package installation needs no retained distribution copy, while separately required assets also need recorded acquisition routes. Record necessary preparation conditions in `requirements`, leaving concrete environment construction to task-build.

   Set `required_by` from who uses the tool's scientific capability. Identify protected information under the firewall. In `integrity_requirements`, record the trustworthy tool facts, state or execution records required by V, applicable V IDs and coverage, or null after confirming none. Use both protection requirements to determine feasible `solver_access` (`direct`, `interface` or `forbidden`) without performing the handling here. Data-check confirms or corrects the requirements against actual resources and records concrete material handling. Task-build Phase 3 selects the environment arrangement and defines any required record format and export interface for Phase 4 to consume. Keep source support and screening judgments in their respective evidence fields.
3. **Record reported computing information.** Separate solving from evaluation under [Computing Demand](output_schema.md#computing-demand). Extract only source-reported CPU quantities and units, GPU models/counts, system RAM, device memory and runtime. Preserve each report's workload, capacity/usage and timing scope; keep different workloads/configurations separate. Unknown fields are null, and a role with no report is null. Do not estimate missing quantities or assign resource grades here; task-build estimates the complete task from these reports and the final package.

### Instance Identification and Resource Bindings

Level 2 identifies candidate problems to extract O and V. Here, confirm their concrete data composition and complete solving/evaluation scope, then associate the relevant tools. Use source and resource evidence within the [defined inspection depth](#inspection-depth).

1. **Identify each candidate's data composition.** Use the paper and resource descriptions to determine which development materials, problem inputs and evaluation references are used together. Identify their research objects, selected contents and conditions, and how the inputs and required results correspond to the evaluation references.

2. **Confirm complete instances.** For each data combination, trace the starting information, required Solver work, submitted results and necessary evaluation under G and C. Use O/V and the [instance boundaries](core_definitions.md#settings--instances) to determine whether the candidate contains one complete problem, combines separately complete problems, or omits data/tests required of the same solution. Confirm or revise the grouping accordingly. Method variants or different uses of the same permitted data do not by themselves create instances. Preserve G and the source-defined object–condition combinations; resource organization or availability alone does not determine the boundary.

3. **Bind data and tools by use.** Reference the shared D/E inventories:

   - **Data:** Record each instance's D_dev, X_test and Y_ref under its `data` roles using the [binding schema](output_schema.md#instance-data-bindings). Use `source_id` and `selection` for acquired/prepared contents; use `runtime_generation` for task-generated inputs/references, describing their generation and required D/E. Record correspondence, Solver access and source support for both. Shared resources may be referenced by multiple instances.
   - **Environment:** Complete `environment_ids` with task-relevant core tools and libraries for solving and evaluation, including those used by the authors and those supporting other applicable methods. Providing a tool does not require the Solver to adopt thatsource method.

4. **Record instance relationships.** Confirm relationships from the source's actual resource uses and evaluation requirements:

   - **Shared resources:** Reuse the same D/E entries, retaining each instance's uses and Solver/evaluator access permissions.
   - **Joint evaluation:** Confirm each item's `scope` and, for joint items, record `instance_groups` as explicit arrays of participating instance IDs. Each group produces one result; groups may share one assessment definition. Their combined membership must equal the instances referencing the item; omit `instance_groups` for per-instance items. Identify each participant's data and deliverable contribution.
   - **Artifact dependencies:** When an instance requires another instance's Solver-produced deliverable, record the producer, deliverable and required use in the consumer's `dependencies`, with source evidence. Permitted pre-existing inputs belong in D; the author's outputs cannot replace work assigned to the Solver.

Record source evidence for the confirmed boundaries and relationships, preserve IDs for unchanged instances, update affected C/O/V cites, and follow the shared recording rules for changes to earlier definitions or judgments. Confirming an instance's boundary does not establish that it can be retained. Apply checks below; Rule 3.5 records final inclusion, subject to the data-size check in Rule 3.6.

### Comparison Results and Reference Standards

For each confirmed candidate instance and each required goal-achievement item, inspect the source locations identified at Level 2. For a joint item, inspect the complete participant group. Extract the following:

1. **Numerical comparison results.** Extract reported scores and their method identities, versions, conditions and precise source locations. Identify and retain the corresponding SOTA result under [Comparison Evidence](#comparison-evidence). For program goal items, retain only the references selected below; deduplicate repeated results and avoid an inventory of ablations. Read numerical values directly from text, tables or readable plots, preserving reported precision and statistical meaning. When a reference aggregate qualifies under [Level 2](level2_rules.md#aggregating-reported-results), extract its complete reported constituents, verify correspondence to the confirmed scope and calculate it; otherwise do not calculate missing method scores. A supported analytical zero reference is allowed under [Program Reference Selection](#program-reference-selection). Record the calculation and provenance using the [comparison fields](output_schema.md#comparison-groups-and-results), retaining original reported results and their conditions.

2. **Rubric reference standards.** Extract the source rubric or the findings, facts and validation evidence from which its judgments can be established for the instance. Record the applicable standards and any source-defined levels or combination rules in `criteria`, with precise locations in `source`; state instance-specific differences when an item is shared. Record applicable reported numbers or qualitative findings in `comparison_results`; use `[]` when the instance-specific standards suffice without a reported performance result. Classifying an item as `rubric` does not waive a numerical metric's comparison requirement. A rubric definition or maximum is not a reported performance result. Concrete scoring scales and implementation remain with task-build.

3. **Assessment and comparison scope.** Match the extracted information to the confirmed instances. For the same assessment, use individual assessments/results when available, even if an aggregate is also reported; use joint scope only when the source supplies that assessment solely for the combined scope. Preserve joint criteria, participants, necessary local checks and aggregation. Do not assign a joint result to each participant or derive missing individual results. Different result values alone do not require separate evaluation-item definitions.

4. **Verify correspondence at the source.** Revisit the cited table cell, plot panel or passage and its headings, legends and notes. Confirm the reported result and method, task/data and instance/group, and assessment meaning against the established definitions. Apply [Rule 3.4's accepted differences and routine checks](#comparison-evidence) as part of this correspondence check. Additional inspection of configurations or code is needed only for a specific concern affecting applicability. For qualitative findings, preserve the claim's direction and qualifications. Verify method roles and the paper's ranking under the corresponding source conditions. Correct extraction or mapping errors, and resolve definition problems under [Rule 2.1](level2_rules.md#conflicting-source-definitions).

5. **Record contents and provenance.** Group reported results by `scope` and `instance_ids`, recording method role, variant, SOTA status and result conditions. Do not invent method competition or performance values for a rubric-based judgment.

   Reuse `source_materials[]` and existing files; complete the needed material descriptions, source evidence and supported acquisition instructions. Each result's `source` records its precise location and provenance; `source_ids` links additional supporting materials in this inventory. Use `source_ids: []` when the original paper suffices without additional material; still provide the precise location in `source`. Cite standard-only material IDs and locations in the item's `source`.

   Materials used only by Pipeline Agents to extract reported results or establish assessment standards belong in `source_materials[]`. Task data required for solving or evaluation, including development materials, problem inputs and evaluation references, belong in task D with the relevant instance bindings. Assign roles under the existing [D definitions](core_definitions.md#data-and-resource-roles). Supply the required D portions in the delivered task data; a provenance citation alone does not supply them.

   For D reused from a source material, record its material ID, existing path, required portions and copying/extraction steps, including how to obtain missing contents, in the D resource's `source.instruction`; cite supporting checks in `source.evidence`. Level 3 records these instructions. Data-check copies or extracts the required contents into its output `data/`, completes any missing parts, and fills D's `local_files[]` with the actual paths.

#### Program Reference Selection

For each program goal item's instance or complete joint scope, retain one corresponding paper-reported or permitted aggregate SOTA, one zero baseline, and optionally one strong/representative paper baseline. Use `is_sota` and `baseline_roles` under the [comparison schema](output_schema.md#comparison-groups-and-results). A baseline serving both purposes is one result with both roles; keep at most three distinct results. Select one source result when SOTA methods tie.

Prefer a finite, applicable no-information level for the zero baseline. An analytical expectation is permitted when supported by the metric definition and assessment conditions; record the policy, derivation, assumptions and definition sources in its existing `method`, `conditions` and `source`. Otherwise use the paper's explicit basic/default baseline, or the weakest comparable complete conventional baseline if none is designated. Exclude ablations, deliberately disabled configurations and failed methods. For the optional representative, prefer the paper's designated strong/representative baseline, otherwise the strongest comparable complete baseline. This reference does not replace the zero baseline.

For `target` program goals, record the finite numerical target in each comparison group's `target`, using the same raw quantity, units and applicable conditions as its results. Preserve its scientific definition and provenance in the item's `criteria` and `source`. The target must lie within any supported theoretical bounds. Rank methods by absolute distance to that target, retaining reported raw values.

Confirm that SOTA strictly improves on the zero baseline in the metric's direction, with the same quantity, units and applicable assessment conditions. For `target`, require `abs(SOTA - target) < abs(zero - target)`. Equal or reversed anchors cannot establish an improvement interval; resolve source/selection issues or fail the affected scope under Rule 3.4. A small but supported positive improvement is valid. Do not invent a reference, weaken SOTA or run a new method to obtain one. The supported analytical no-information expectation is the only additional calculated zero-reference exception to permitted reported aggregates.

First completion of these fields at Level 3 is scheduled extraction, not a correction to Level 2.

## Screening Rules

Apply Rules 3.1–3.4 to required D/E and comparison materials at the [defined inspection depth](#inspection-depth), identifying the affected instances and requirements. Reuse checks when conditions and evidence are shared. Archival materials not needed by the task are outside these checks.

A local failure prevents retaining the affected scope unchanged. Rule 3.5 determines whether a complete task remains; Rule 3.6 checks its combined data size. Use [Results and Recording](#results-and-recording) for decision fields and evidence.

### Rule 3.1: Automatic Resource Acquisition

#### Pass Conditions

Every required resource is available through a supported automatic acquisition or use route under the current access configuration, without further manual preparation. Downloads, repositories, packages, and APIs may qualify. Required preparation inputs and dependencies must meet the same acquisition conditions. Optional method choices do not become required resources merely because the authors used them.

For required resources used in solving or evaluation:

- **Services**: The implementation and runtime assets must support local deployment with the required identity, version, capabilities and workload. A remote endpoint or client SDK alone is insufficient. Download platforms used only during preparation are not task runtime services.
- **Pretrained models**: Required starting weights and runtime assets must be obtainable for local use. Required pretrained LLMs need downloadable open weights and a supported local runtime. Check starting resources for models the Solver must train under G; their trained outputs need not exist beforehand.

Under the LLM Judge exception defined in Level 2, all other resources required for solving or evaluation remain subject to the applicable acquisition and local-execution requirements.

#### Judgment Method

1. Trace required contents and installation/generation prerequisites to their sources. Check acquisition URLs with `scripts/validate_links.py`; inspect provisioned local resources directly.
2. Confirm actual access through a lightweight resource/permission check, using applicable Resource Access Config entries without exposing secrets. A reachable landing page, authentication heuristic or configuration file alone does not establish access.
3. For a failed request, check relevant source-linked or authoritative alternative routes before deciding. A timeout or rate limit does not establish that the resource is absent; exhaustive searching is unnecessary.

Use script observations and completed supplementary checks to update link records under [Link Validation Usage](validate_links_usage.md).

#### Reject Conditions

Reject the affected scope if a required route remains unsupported or still requires manual approval, application, login, purchase or preparation. Promised future availability is insufficient.

### Rule 3.2: Resource Completeness

#### Pass Conditions

The identified resources support all required solving work, deliverables and evaluation. Trace the input/reference fields, identifiers and operations required by O and V to actual resource contents and tools in D/E, with supported acquisition or preparation routes. A resource name or related file alone does not establish that it supplies the required contents.

- **D_dev**: Necessary development materials and their prerequisites are present or have supported preparation routes. It may be empty when none are required.
- **D_eval**: X_test supplies the required objects, inputs or initial conditions; Y_ref supplies any reference information needed by V. Source descriptions or bounded inspection must establish the required contents and input/reference relationship. An existing reference-answer file is not mandatory. Y_ref may be empty only when V needs no additional reference information.
- **E**: Tools, dependencies and computing resources support all required solving and evaluation operations, with compatible versions, configurations and runtime prerequisites. Check dependency declarations, build configurations and documentation for the selected revision, rather than assuming a different release's requirements apply. Library versions may be adjusted for compatibility while preserving required scientific functionality and evaluation meaning. Where V requires trusted observations or control, establish that the selected tools and execution support can cover the defined operations and period while preserving their integrity; planned capabilities without support are insufficient.
- **Comparison and standard materials**: The reported-result or rubric materials needed for instance-specific assessment are available. The original paper may suffice; additional provenance files are not required.

Before declaring a required field or grouping absent, inspect related tables, metadata and documented mappings. Check whether shared identifiers support the needed association; matching row counts or row order alone is insufficient. Keep these checks within the [defined inspection depth](#inspection-depth).

**Generation and reconstruction of static task data**: Apply `generated` or `restructured` to D_dev, X_test and Y_ref only when source evidence establishes an explicit scenario for generating or reconstructing static task data. Use the paper's procedure or an established general method with evidenced applicability to the source task. Required parameters must be defined, and any required inputs and dependencies must be obtainable. Preserve the source-defined data and evaluation conditions; required exploration remains Solver work. Missing or defective data do not establish such a scenario. Do not synthesize replacements or repair scientific contents to obtain a pass.

For entries with `runtime_generation`, verify the defined generation rule, timing, generator class (evaluator-side direct generation or E-tool invocation, or Solver-environment E generation) and any necessary D/E support. Register required starting resources in D/E and reference them through the instance's normal data bindings and `environment_ids`. If no additional D or dedicated E is needed, confirm that the source-supported procedure specifies how to generate the required inputs or references. Implementation belongs to task-build; future data need not exist beforehand. When synthesis is Solver work, check the task's starting requirements and evaluation without requiring an author-provided dataset, configuration or generator unless the task prescribes it as a starting resource. Record these cases under [Instance Data Bindings](output_schema.md#instance-data-bindings).

#### Reject Conditions

Reject the affected scope when required components are missing, incomplete, placeholders or incompatible with automatic preparation. Author-produced results or derivative artifacts cannot replace missing solving inputs or work assigned to the Solver.

For example, a repository containing only plotting data, analysis results or source-model outputs fails when the required development inputs, test inputs or references are missing；a required simulator's runtime dependencies cannot be satisfied.

### Rule 3.3: Information Separability

#### Pass Conditions

Resources remain usable under their task-defined roles and Solver/evaluator access permissions while satisfying the [Information Firewall](core_definitions.md#information-firewall):

- **Development and evaluation data**: Permitted development contents do not expose protected evaluation information. Problem inputs remain usable while protected references are withheld, preserving the correspondence needed by V.
- **Tools**: Required Solver operations do not expose protected data, source solutions, evaluator state or credentials through code, configurations, installed assets or feedback. Determine whether the complete capability can be supplied directly after feasible handling, or through a permitted interface with its protected implementation/assets withheld. Check the interface's required responses, errors, logs and output files; an `interface` label does not resolve remaining disclosure. A Solver-required tool cannot be made unusable by marking it forbidden; ordinary evaluation software is not automatically private. Where `integrity_requirements` apply, the access route must also preserve the trustworthiness of the specified facts, state or records; confidentiality alone does not establish that protection.

Establish whether unnecessary revealing contents can be excluded or evaluation-only contents kept private while preserving all required solving and evaluation resources. Assess feasible selections or access controls from available evidence; this does not require performing the separation at Level 3. Data-check confirms the necessary handling against actual resources, and task-build implements it. Separate files, downloads or environments are not inherently required. Separation must preserve necessary information and use. Check for remaining disclosure through related fields, files or interfaces using the available evidence. Empty D_dev or Y_ref does not waive protection elsewhere.

For partitioned evaluation, establish that task-build can prepare D_eval under Level 2's [source protocol and fixed-split adaptation](level2_rules.md#source-evaluation-protocol). The final evaluation partition and its access boundary must be fixed during task-build, before solving; do not delegate this partitioning to the Solver. Within D_dev, preserve any source-provided validation split.

#### Source Data Overlap and Inspection Scope

Judge data use from the paper, dataset descriptions and relevant preparation/loading code. Train/test overlap already present within a source dataset or across related datasets is a source-data property when the paper uses those data as supplied; it does not by itself fail information separability. Do not remove exposing portions, change splits or otherwise alter an instance's source-defined data to eliminate a cross-instance conflict. Explicit use of one instance's protected reference values as another's training data or problem inputs is a cross-instance conflict, even when the paper specifies that use.

At Level 3 and during data-check, inspect the paper's data uses and resource bindings for cross-instance disclosure. Do not separately check whether source datasets have inherent overlap.

#### Cross-Instance Information Exposure

Check the combined Solver-visible contents and tool feedback across all retained instances, in both directions and beyond those declared artifact dependencies. In particular, check whether one instance's development data or problem inputs expose another's protected evaluation references.

Required Solver-produced outputs may pass between solving stages or dependent instances; this is not by itself disclosure of source references. Preserve that required output flow instead of supplying the source reference output. If one instance requires inputs that are another instance's protected reference information, do not substitute the latter instance's predicted outputs to avoid the exposure.

Instance order, separate directories or separate agents do not resolve this conflict. When required inputs expose another retained instance's protected information, exclude one of the conflicting instances under [Rule 3.5](#scope-review-and-reject-conditions), including any additional exclusions needed to preserve dependencies. Merging instances does not by itself resolve the conflict.

#### Reject Conditions

Reject an affected instance or combination if necessary separation makes resources unusable, provides precomputed results that replace work the Solver is required to perform, or leaves protected information exposed. Apply Rule 3.5 to the affected combination.

### Rule 3.4: Task and Evaluation Consistency

#### Pass Conditions

Source evidence and bounded checks establish all three conditions:

1. **Resource identity and coverage**: The obtainable version, objects, scale, selected contents and data splits match the source task. Apply the [data-selection distinction](#data-d): preserve task-defined subsets and the full permitted inputs before source-method selection.
2. **Evaluation conditions**: Resources support the required test conditions, configurations, sampling and aggregation under Level 2's [source evaluation protocol](level2_rules.md#source-evaluation-protocol), including its partition-parameter and seed requirements.
3. **Applicable comparisons and standards**: Each required numerical goal-achievement metric has the corresponding reported or permitted aggregate SOTA result under the comparison rules below, with the selected zero reference and strict improvement interval for program goals; rubric and validity judgments have the applicable standards specified below. Apply the accepted differences in the comparison checks to each instance or complete joint scope. A source location identified at Level 2 is insufficient by itself.

#### Handling Data Discrepancies

1. **Establish the cause.** Identify what disagrees and compare the same resource version, data scope and processing stage. Check counting units (files, records or research objects), parsing, approximate or rounded reporting, source-defined selections and documented preprocessing against source descriptions, metadata or relevant code. A count difference or filename alone does not establish a mismatch.
2. **Assess the effect.** An explained difference is acceptable only when the required objects, inputs/references, splits and evaluation conditions remain supported. For example, a CSV line count may include the header while the paper counts data records, or a reported total may be explicitly approximate or rounded. Confirm the explanation from evidence. A small difference is not automatically harmless: missing required data or an unexplained change to split membership can affect task completeness or evaluation comparability.
3. **Resolve and record.** Correct inspection or counting errors and recheck the affected requirements within the defined inspection depth. Record the discrepancy, established cause, supporting checks and effect on task/evaluation consistency in rule evidence. Reject the affected scope if a necessary condition remains unsupported. Do not ignore conflicting evidence, delete or alter source records, change splits or invent a subset merely to match the paper's description.

#### Comparison Evidence

- **Numerical goal-achievement metrics**: Require reported method scores or their permitted aggregates matching the quantity, units, measurement boundary, direction/target, data population and statistical interpretation. Apply the permitted fixed-split adaptation and retain the source score's original conditions. This requirement is unchanged by the item's `method`, including rubric judgments aggregated into the metric.

  Each numerical comparison group must retain the best corresponding reported or permitted aggregate method result, marked `is_sota: true`; program groups select one result when methods tie. Identify it from the paper's corresponding results before judging applicability. For permitted aggregates, use the same calculation across corresponding source methods and distinguish the calculated values and ranking from directly reported results. It may come from the authors or another method; the author's result may serve when it is the only supported method result for that scope. Program goals also require the zero reference and strict improvement interval under [Program Reference Selection](#program-reference-selection); the representative baseline is optional. Other rubric comparison baselines remain optional. Do not skip a known better same-condition result and relabel a weaker baseline as SOTA to obtain a pass, or rank results across incompatible conditions. No search for the latest field-wide SOTA is required.

- **Rubric-based goal-achievement judgments**: Require a source rubric or source information supporting the same required finding or capability under the instance's conditions. Its standards and necessary references must be applicable; generic rubrics, unrelated findings and judgment standards without source support are insufficient. A ready-made numerical source score is not required unless the assessment requires the numerical comparison described above.
- **Validity**: Require applicable acceptance standards and reference information, not a performance comparison.

Any recorded source result must apply to its declared participants and assessment. A joint result does not automatically apply to fewer participants. Apart from permitted reported aggregates and the supported analytical zero reference above, do not calculate replacement scores or treat rubric maxima as reported performance. An optional representative baseline may be absent; a program zero reference may not.

For a confirmed source implementation defect, follow [Rule 2.1](level2_rules.md#conflicting-source-definitions): use the established correct evaluation definition, retain the reported score and disclose its defect and interpretation limits. The documented defect alone does not reject the score; a missing or contradictory scientific definition remains unresolved.

For routine numerical-score checks, use the cited source context and established task definitions to confirm the reported score and method, the corresponding task, data and instance or joint scope, and the metric's meaning under the necessary evaluation conditions. Apply the pipeline's accepted differences during this check: the [eligible fixed-split adaptation](level2_rules.md#fixed-split-adaptation), discrepancies accepted under [Handling Data Discrepancies](#handling-data-discrepancies), and the source-implementation-defect handling above do not by themselves invalidate comparability. Preserve their original conditions and interpretation limits rather than requiring exact historical identity.

For resource or time assessments, check that the adopted quantity, counted work, measurement period and necessary hardware/operating conditions support the source comparison. Reported setup is not automatically mandatory, but a source-conditioned result cannot be relabelled as hardware-independent. Preserve original conditions and any supported differences in the comparison's `conditions`; do not rescale or recalculate the reported score. Revisit Rule 2.2 when a necessary measurement or control is unsupported.

Investigate additional training, search or runtime details only when a specific concern may affect task constraints, assessed objects, metric meaning or necessary evaluation conditions. Missing incidental method details alone do not invalidate the score. Reuse supported earlier findings; record applicable accepted differences in `conditions` and any targeted check and decision basis in rule evidence. A material mismatch outside those accepted rules, or an unresolved essential definition, prevents use; matching the source score or recovering a complete experiment history is unnecessary. A comparison alone does not establish superiority.

#### Reject Conditions

Reject the affected instance or joint scope when source checks, including the accepted differences above, cannot establish resource consistency, the required SOTA result/judgment standard, or a program zero reference with a strict improvement interval. Check relevant source locations and correct extraction or mapping errors before rejecting; a weaker baseline cannot replace the paper's corresponding SOTA. Do not redefine G, change required data or weaken evaluation to obtain correspondence. Recheck Rule 2.1 if its evaluation definition or identified comparison basis was wrong.

Source-supported correspondence awaiting full file verification is not itself a failure; missing screening support cannot be deferred.

Examples of unsupported resource consistency include:

- Only mini/toy/sample/demo/example data are obtainable, while the source task requires a larger or different set of contents.
- Only a different version, year or snapshot is obtainable and it cannot supply the source-defined contents or evaluation conditions, or the version used by the paper cannot be established among available alternatives.

### Rule 3.5: Task Resource Support

#### Pass Conditions

At least one complete source instance remains, and both conditions hold:

1. **Instance support**: Every retained instance satisfies Rules 3.1–3.4. Required artifact dependencies have retained, supported producers whose deliverables meet the consumers' input requirements; producing those artifacts remains Solver work.
2. **Whole-task completeness**: The retained instances and their relationships collectively support G and all task-wide requirements. Preserve the applicable C, O and necessary evaluation for each retained instance. Evaluation items used only by excluded instances may be removed, provided no task-wide requirement is lost. They satisfy [task completeness](core_definitions.md#task-completeness) and Levels 1 and 2; individually complete instances are insufficient when a task-wide requirement is lost.

#### Scope Review and Reject Conditions

Account for all identified source instances, including earlier evaluation-based exclusions and confirmed boundary revisions. Reuse supported earlier judgments; reconsider an excluded instance only when new evidence warrants it.

Instance exclusions preserve G and each retained instance's data, constraints, required solving work and evaluation. They cannot relax a budget or remove necessary validation. Partition changes are limited to the permitted [fixed-split adaptation](level2_rules.md#fixed-split-adaptation). If no complete-instance selection qualifies, paper-filter applies [scope reassessment](../SKILL.md#scope-reassessment); later stages using these instance-selection rules do not gain permission to change G.

For a cross-instance exposure conflict, assess which complete scope remains when either conflicting instance is excluded. There is no fixed preference for excluding the instance exposing the information or the one whose references are exposed. Exclude unsupported dependent instances as well, and the retained selection still satisfies G, all necessary evaluation and the remaining dependencies. Among qualifying alternatives, prefer retaining more complete source instances; resolve ties using the paper's stated aims and substantive evidence. Record the conflict, affected IDs and selection rationale in rule evidence and `instance_assessments`.

After excluding instances, update resource bindings and declared Solver access so contents needed only by excluded instances are no longer provided to the Solver; recheck disclosure across the retained scope. Do not delete or alter data still required by retained instances to resolve a conflict. Level 3 establishes these bindings and access boundaries; later stages verify and implement the actual isolation.

Before retaining fewer instances, recheck affected producer/consumer dependencies, joint requirements, comparisons and task-wide support. Reject when no permitted selection satisfies the pass conditions. A locally supported instance may still be excluded because the whole task or its combined size does not qualify; preserve that distinction in the inclusion decision.

### Rule 3.6: Data Scale Feasibility

Estimate the complete required data total for the scope supported by Rule 3.5.

#### Size Tiers

Use configurable boundaries a and b, with 0 < a < b; defaults are 1 GB and 50 GB in 1024-based units. These are filtering limits, not scientific task constraints.

| Tier | Range | Decision |
|------|-------|----------|
| **S** | Total < a | Pass |
| **M** | a ≤ total ≤ b | Pass |
| **L** | Total > b | Reject the assessed scope; review a permitted reduction |

#### Estimation Method

Count the task's required D: development materials, problem inputs and evaluation references, whether already local, acquired, generated or restructured. Estimate the contents needed after required extraction or preparation, not the volume downloaded. If acquisition requires a whole archive or repository, count only the portion needed as task D; independent download of that portion is not required.

Include task-required inputs and references generated by tools or evaluators during execution, even when they have no D acquisition entries. Estimate the complete input/reference set prescribed for the retained instances and their evaluation conditions.

Exclude comparison-only materials, construction/inspection documents, acquisition metadata used only for those materials, software installation size, Solver exploration products, and evaluation intermediates or logs that do not serve as task inputs or references. If a comparison material also supplies task D, count that required portion.

1. **Run the script.** Use `scripts/estimate_size.py` with the identified data sources and selected limits; see [Size Estimation Usage](estimate_size_usage.md).
2. **Complete or refine estimates.** For required data the script cannot estimate, or when it reports a whole container instead of the required contents, use the paper, file inventories, data documentation or preparation instructions to estimate the task-D portion. Record the selected contents, size basis and adjustments to script estimates in Rule 3.6 evidence; full download or generation is not required. Do not assume an arbitrary fraction of a larger source.
3. **Check coverage and counting.** Review `data.sources[]`, instance descriptions and E/V requirements for all task-required inputs/references, including those generated during execution. Reconcile these contents with the estimates and total, count shared data once, and remove duplicate size estimates. Distinguish archive/download sizes from the contents used by the task; do not add a transport archive and its extracted contents as two copies of the same data.

#### Confidence Levels

| Level | Evidence |
|-------|----------|
| `high` | Complete coverage by file sizes or inventories |
| `medium` | A supported total requiring estimates |
| `low` | Source-description estimates supporting the total |
| `indeterminate` | No complete total can be established |

Confirmed zero data volume requires supporting evidence; an empty inventory or unestimated total is not zero.

#### Pass / Reject Conditions

Pass only when the complete estimated total is at most b; exactly b passes. Reject an oversized or indeterminate total. Unknown-size required components cannot be omitted to obtain a pass.

For an oversized scope, assess whether excluding less central complete source instances can bring the total within b. Judge relative importance from the paper's stated aims and substantive evidence. Exclusions must preserve G, all task-wide requirements and all requirements of retained instances under [Rule 3.5's scope review](#scope-review-and-reject-conditions). Recheck affected dependencies and evaluation, then recalculate the complete retained total. Preserve the original size failure, record the exclusion rationale and update instance inclusion. Arbitrary sampling or omission of necessary/shared data cannot establish a pass.

### Overall Decision

- **Pass**: Rule 3.5 establishes a qualifying task and its combined required data pass Rule 3.6.
- **Fail**: No permitted selection of complete instances satisfies both rules, and filtering-time scope reassessment yields no qualifying subgoal.

Rule outcomes describe the last assessed candidates after rechecks. Preserve earlier failures and affected instances in screening records. An overall rejection or cleared active instance list does not make independently supported resource checks fail.

## Results and Recording

Use the [Output Schema](output_schema.md) for field definitions, stage ownership and change records.

### Task Information

- **`task_info.data`**: Complete `sources[]` with `id`, task input/reference contents, versions, acquisition routes and source evidence. Record each resource once; instance data bindings reference these IDs.
- **`task_info.instances[]`**: Confirm instance boundaries, descriptions, objects and C/O/V references. Complete `dependencies`, `data.d_dev`, `data.d_eval.x_test`, `data.d_eval.y_ref` and `environment_ids`, recording required artifact uses, resource selections, correspondence and access boundaries. Use `dependencies: []` only when no required artifact dependencies remain.
- **`task_info.environment`**: Record selected capabilities and `dependencies[]` with stable `id` values, `purpose`, `interfaces`, `gui_interaction_required`, `requirements`, `preparation`, `required_by`, `solver_access`, required `integrity_requirements` and acquisition routes; instance `environment_ids` records applicability. Preserve supported solving/evaluation computing information when available.
- **Current scope**: Keep only final included instances and their required components/resources in task information. Update the retained instances' associations with task components and resources, and confirm that comparison results apply to those instances. Use the goal last assessed at Level 1, including after scope reassessment. Preserve the evidence for instance inclusion and exclusion decisions in screening records.
- **`task_info.evaluation_items[]`**: Complete reported or permitted aggregate `comparison_results` for numerical metrics and any available reported results for rubric items, with their instance/group scope and `source_ids`. Complete applicable standards in `criteria`, judging information in `required_information`, instance-specific conditions in `protocol`, and provenance in `source`. A rubric item may use `comparison_results: []` when no reported performance result is needed and Rule 3.4 establishes its instance-specific judgment basis; this does not waive a numerical metric's comparison requirement.
- **`task_info.data.size_tier`**: Record the combined assessed data tier under Rule 3.6.

### Construction Sources

Complete required result/rubric-material entries in `source_materials[]`, reusing existing records and files. Link reported results through comparison `source_ids` and cite standard-only material IDs in the item's `source`. Keep construction-only materials outside D.

### Screening Records

In the Level 3 entry of `filtering_process`, record:

- **`rules[]`**: Rules 3.1–3.6 with `passed`, `reason` and `evidence`. Rules 3.1–3.4 identify assessed resources and affected instances or requirements; Rule 3.4 also records the extracted basis and applicability decision for each required goal item and instance/group. Rule 3.5 adds `instance_assessments[]` (`instance_id`, `description`, `included`, `reason`, `evidence`) for all identified source instances, including Level 2 exclusions and candidates added or revised during this level's identification; its overall reason addresses whole-task support.
- **Rule 3.1 `links_checked`**: Record current link checks, including supported corrections. Identify supplementary verification in `notes` or rule `evidence`; local-resource checks remain in `evidence`.
- **Rule 3.6 size fields**: Record `size_tier`, `tier_thresholds`, `estimation_confidence`, `estimated_total_bytes`, `estimated_total_size`, `per_url_estimates` and `size_failure_reason`, including supported corrections. Explain estimate corrections, complete inventory coverage and deduplication in `evidence`. Apply the shared [link and size recording rules](output_schema.md#link-and-size-evidence) to corrections and reruns.
- **`task_info_changes[]`**: Record changes to established task information, including removed evaluations or instances, under the [shared change rules](output_schema.md#task-information-changes). Normal first completion of D/E, comparison results and instance-specific reference standards is not a change entry.
- **`passed`, `reason`, `evidence`**: Record the overall conclusion and assessed instances. Explain any recheck of earlier rules, including changed evaluation/comparison applicability.

Unexecuted checks remain unassessed. Missing extraction must be completed when the source supplies it, not classified as a resource limitation.
