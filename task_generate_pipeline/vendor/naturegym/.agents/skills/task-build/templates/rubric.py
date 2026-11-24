"""Hooks for method='rubric': evidence, component scoring and final composition."""


def prepare_evidence(item, instances, context):
    """Return a nonempty list of evaluation evidence records.

    Called once per item/scope; return the necessary evidence shared by its
    components. Each record has a unique id, source locator and textual content.
    Include instance/object identities, correspondence and assessment conditions needed
    to interpret the observations. The Judge sees only the rubric basis,
    selected component (including its score) and these records, not upstream definitions.

    Read actual artifacts using context.artifact and actual trusted information
    using context.resource, which resolves input/data/ from data_root and
    evaluation/assets/ from the evaluator's package, regardless of data role.
    For official tool records use context.tool_record(dependency_id), following
    Phase 3's format; validate the required contents, correspondence and scope
    before extracting evidence. Missing/bad records are evaluation failures,
    not proof of absent operations or unsupported Solver claims.
    source locates the file/section/observation; include the actual supporting
    content and its correspondence to referenced objects, not filenames alone.
    Read all material needed for the checks, but return only the contents
    needed for judgment. For long tables, perform the defined deterministic
    checks and include necessary observations/results, coverage and source
    locations. Select relevant explanations, reference passages and code;
    a report may remain complete when its full argument is assessed. Do not
    concatenate every file, raw-data re-export or intermediate table. Preserve
    necessary conditions, qualifications and contradictory observations;
    generic summaries or first-N-row previews are not automatically sufficient.
    Extract/render nontext material adequately. Unsupported extraction is an
    evaluation failure, not proof the submission lacks evidence. Record observed
    absences explicitly. Never silently truncate decisive content.

    For LLM components, offline checks assemble the complete Judge input, including
    system instructions, rubric text and these records, and verify the fixed
    ceiling of 1M (1,000,000) input tokens. Smaller check models do not redefine it.
    If necessary contents exceed it, revise extraction without weakening the
    assessment; splitting records alone does not reduce the request size.
    """
    raise NotImplementedError('Implement evidence extraction')


def score_rule(item, instances, context, evidence):
    """Score item['component'] under item['component']['score'].

    The input evidence contains the records returned by prepare_evidence.
    Return {item_id, component_id, raw_score, reason, evidence}; the returned
    evidence contains only cited record IDs. Return one numerical anchor value
    without rounding; do not compute other components or the external task total.
    Required when any component has scorer='rule'; read its scorer from
    item['component']['scorer'].
    """
    raise NotImplementedError('Implement deterministic rubric scoring')


def aggregate(item, judgments):
    """Implement custom composition, only when aggregation.method='custom'.

    judgments contains all scored components. Return the final raw number from
    the documented formula, consistent with item['result'] and any declared
    bounds. Preserve the anchor ordering and item['result']['direction']; do not
    add normalization, weight changes or rounding absent from that formula.
    When aggregation.target_range is present, implement its mapping here;
    the fixed evaluator checks the final range without mapping custom results
    again. sum/normalized_weighted, including their optional mapping, need no hook.
    """
    raise NotImplementedError('Implement source-defined rubric composition')
