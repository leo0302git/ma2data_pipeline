---
name: seed-select
description: Choose a fixed seed for one inventory group from the reviewed public index.
---

# Inputs

Read the inventory subject and task category, the group's payload format summary, and the candidate contracts from NatureBench, TB-Science, and MLE-bench. The data files have not yet been opened.

# Work

Compare what the Solver must achieve, what the data must support, what artifacts it must submit, and how a new evaluator can establish correctness. Select one existing candidate for the whole group. Record why its core assessment transfers, the minimum necessary material, and which source-level facts are still unknown. Keep the group as supplied. Use `needs_review` if evidence is insufficient or there is no compatible candidate. Do not generate task content or change a previous binding.

file_suffixes are retrieval hints. Do not reject a candidate solely because a new
source encoding has another extension. The achievement and necessary scientific
modality must still transfer; record actual missing observations or incompatible
assessment requirements for source-level validation.

# Output

Return `result_json` containing `seed_id` and a nonempty `reason`. No file writes are needed.
