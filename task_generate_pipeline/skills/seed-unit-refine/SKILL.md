---
name: seed-unit-refine
description: Clarify an existing group seed as a per-source generation contract during an audited migration.
---

The host confirmed a group-versus-package ambiguity in an existing seed contract.
This authorized migration clarifies the generation unit. Each new package uses ONE
inventory source and that source's own files. A group may have many sources, all
using this SAME benchmark seed, but each produces a separate package. Group counts
are coverage metadata, never a requirement to include sibling sources in one package.

Read the supplied locked seed and its original documents as reference data. Preserve
the selected benchmark, scientific field, task family, objective direction, complete
mathematical feasible region, required actual labels/measurements and independent
grading. Clarify only package scope; do not make missing data acceptable or claim
runtime verification. Requirements for one source's own missing files remain blockers.
Do not require a collection-wide reference, train/test partition, aggregation or
generalization claim where only one source is supplied. The benchmark's original
dataset is not a prerequisite for the new package.

Return done, no reads or writes, with result_json containing package_unit="one_source",
core_assessment (string), minimum_source_requirements (nonempty string array),
adaptations (string array), and reason (string describing the clarification).
