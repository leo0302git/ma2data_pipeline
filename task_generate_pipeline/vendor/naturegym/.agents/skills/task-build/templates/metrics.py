"""Task-specific hooks. The fixed evaluator catches failures per artifact/item.

Both methods share validate_format and prepare; only method='program' calls
evaluate. Each item returns one raw scalar for its instance or joint scope.
Never import submitted code into this trusted process.
Keep scoring imports, model/service initialization and private resource reads
inside their consuming hooks; module import and validate_format must work
without scoring assets, tool records or Judge configuration.
In either feedback mode, public_message should preserve useful diagnostic detail.
Use the original message unchanged when confirmed public; rewrite only to omit
protected contents. Do not automatically copy unreviewed exception text, hidden
reference values, private rubric/Judge contents or private-input program output.
Missing public messages use fixed fallbacks; original diagnostics stay private.
"""


class SubmissionError(Exception):
    """Confirmed submitted-content/program defect, not an evaluator fault."""
    def __init__(self, *args, public_message=None):
        super().__init__(*args)
        self.public_message = public_message


class EvaluationError(RuntimeError):
    """Evaluation/resource fault, optionally with a reviewed public explanation."""
    def __init__(self, *args, public_message=None):
        super().__init__(*args)
        self.public_message = public_message


def validate_format(instance, role, path):
    """Validate one artifact; return None or raise SubmissionError.

    instance contains only the public name and deliverables mapping, in both
    scoring and --format-only runs. Check only README's public submission
    requirements and submitted contents: required file/directory type,
    parseability, schema, fields, types and public constraints or correspondence
    within the submission. Do not read hidden inputs/references, compare hidden
    membership, perform scientific judgments or consume tool records/services.
    Do not fail unrelated artifact roles. Existence/containment are checked centrally.
    Convert encoding, parsing or numerical errors to SubmissionError only when
    they establish a submitted-content defect, preserving the original cause.
    Library/configuration/internal faults retain their original exception type.
    Optional public_message explains the public requirement or submitted-content
    defect under the shared feedback rules above. Without it, public feedback
    uses a fixed generic message; original message/cause remain private diagnostics.
    Do not execute/import submitted code (including --help), load models or
    deserialize executable candidate data. Private/scientific checks belong in
    prepare/evaluate or rubric hooks.
    """
    raise NotImplementedError('Implement artifact conformance checks')


def prepare(item, instances, context):
    """Return extra task-specific context fields for this assessment.

    instances is a list: one instance or all declared joint participants.
    context.artifact(name, role) resolves validated outputs, including required
    upstream artifacts; missing/invalid upstream outputs are submission failures.
    context.resource(name, role, reference=False) resolves input/data/ paths
    under context['data_root'] and evaluation/assets/ paths in the package. The
    path selects the root; reference selects the input/reference mapping only.
    context['package_dir'] also locates code/configuration. E components use
    Phase 3's actual absolute installed paths; missing required tools are
    evaluation errors.
    context.tool_record(dependency_id) resolves an optional trusted file/directory
    from task.json's tool_records under --tool-records-dir, only when called.
    Read Phase 3's actual format and validate required fields, instance/operation
    correspondence and covered scope. Missing/bad records are evaluation failures,
    never zero operations. Keep solving-history records distinct from new calls;
    they are not submissions, task D or fixed E assets. Return to Phase 3 if its
    tool/export contract cannot supply the information required by V.
    context.cached(key, callable) reuses successful calculations AND failures;
    use a key identifying the actual execution inputs/conditions. Call it only
    for work this item needs, so failures do not block independent assessments.
    Cached values shared between jobs must be treated as read-only.

    context.map(evaluate_one, jobs) calls evaluate_one(job, child_context) and
    returns values in input order, using the required runtime --max-workers
    limit. Use it for independent programs, simulations or permitted
    tool/service calls. Keep in-process CPU-bound pure Python work serial;
    consider process-based execution only after profiling identifies a bottleneck.
    Preserve required repetitions, denominators and joint aggregation.
    Use child_context for paths/execution;
    do not capture the parent context, mutate shared state or nest maps. Give
    each job a deterministic seed when needed. All jobs finish before the first
    input-order failure is raised. Execute dependent steps in order within one job.

    context.run_submission(command, timeout=seconds, env=allowed_variables)
    launches candidate code with the runtime-provided UID/GID. Static access is
    prepared before mounting; the runner grants only declared image resources.
    Resolve the submitted entry point with context.artifact and required instance
    inputs with context.resource, then pass actual file/directory paths explicitly
    using README's arguments. These helpers neither grant access nor add arguments to command.
    For dynamic program inputs, use context.prepare_submission_inputs(writer):
    writer(directory) creates only authorized contents, then the helper seals
    them as trusted-owned, candidate-readable and non-writable. These read-only
    trees survive mapped jobs until evaluation exit and may be cached/shared.
    Keep private references elsewhere. Links, hard links and special files are rejected.
    For inputs the program must modify, context.prepare_submission_work(writer)
    populates a candidate-owned writable copy, cleaned at job exit; do not put
    its path in the shared cache. Copy only permitted source content.
    Pass returned paths explicitly; neither helper changes static inputs.
    For file outputs, context.candidate_directory() supplies a writable location; the candidate
    runs from scratch, not the submission directory. Keep protected references and
    trusted records out of command arguments and candidate inputs. Parse and validate
    generated outputs before scoring. Preserve raw execution errors. Convert an error to
    SubmissionError only when evidence establishes a submitted-program defect,
    preserving returncode/stdout/stderr and any applicable public_message on the
    new exception and chaining it. Known evaluation faults may use EvaluationError
    with a public_message; that does not change their evaluation_failed status.
    Enforce task-prescribed evaluation repetitions, not discretionary Solver
    training. Construction performs environment self-tests; prepare only actual
    assessment work here, without extra probe submissions or permission audits.
    Return {} if no preparation is necessary.
    """
    raise NotImplementedError('Implement item preparation')


def evaluate(item, instances, context):
    """Return one finite raw number, or a validity bool/0–1; no external total.

    Compute only this program item. Preserve source formulas, units, direction,
    required within-item statistics and complete joint scope. Use cached work
    from prepare; do not rerun a shared program for every metric. Necessary
    validity is expressed as its own item, not a preliminary success flag.
    Use context['work_dir'] for private diagnostics. Exceptions remain failures,
    not fabricated zeros. Require the defined sample/object/repetition coverage;
    never silently drop missing observations or average only successful runs.
    Apply predefined missing-as-incorrect/penalty rules with their full denominator
    when the assessment supports them. Validate required intermediate values before
    aggregation, even if a final score would be finite. Called only for
    method='program'; omit this hook when all items use method='rubric'.
    """
    raise NotImplementedError('Implement program assessment')
