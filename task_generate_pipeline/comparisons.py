"""Explicit generated-task comparison profile, alongside the untouched upstream rules."""

import math

from common import digest, load, save


STRICT = 'strict_upstream_reported'
MEASURED = 'generated_measured'
PROFILES = (STRICT, MEASURED)
VERSION = 1
LEGACY_SEMANTICS = 'measured_reference_anchor_not_literature_sota'

MEASURED_RULE = """This explicitly authorized generated_measured profile adapts only the
upstream demand for corresponding previously reported comparison scores. Preserve the
original guides for provenance, but do not apply their ban on newly measured references
to this profile. All source-fidelity, scientific-validity, isolation, real environment,
dynamic-control and independent-review requirements remain mandatory.
Run actual strong and reasonable weak methods on exactly the same frozen task, split,
metric and comparison protocol. Both methods must use Solver-permitted information only.
A private-answer oracle is a grader positive control, never an admissible solving method.
Do not fabricate scores, transfer scores from seed data, change scientific constraints,
select a favorable split retrospectively, or use an infeasible point's undefined raw cost.
A validity-gated overall metric may legitimately be zero only when that is the metric
actually defined and independently evaluated; this is not a feasible raw-cost baseline.
If genuine comparable methods cannot be established, return needs_review.

In metadata.json add comparison_policy with mode=generated_measured, version=1,
literature_sota=false and legacy_is_sota_semantics=
measured_reference_anchor_not_literature_sota. Native is_sota=true remains the legacy
normalization-anchor selector ONLY. It is not a literature or global-optimality claim.
Each numerical comparison row requires reference_origin=measured, literature_sota=false,
information_boundary=solver_inputs_only, a calibration_id matching a real row from
data.calibration, and a nonempty comparison_conditions_id. Strong and weak references
within each item/instance group must have the same comparison_conditions_id. Link the
actual primary-metric value, with identical units and validity semantics. The calibration
method and conditions must identify the actual algorithm, split, metric, compute budget
and receipt locations, including any difference in deliberately predeclared method budgets.
Use method_role=null for a measured anchor (never invent paper_method). Retain genuine
full solve/audit logs privately. calibration.json is an observation, not independent proof;
the independent reviewer must inspect the generating program, information boundary and
execution receipts. It must establish that each selected score was really computed.

Report this as NatureGym-format generated-task measured-profile verification. Preserve
strict_upstream_complete=false unless a separate strict reported-reference audit succeeds.
Do not call an adapted profile an unqualified pass of the original paper-task rules.
"""


def audit_metadata(metadata, calibration, mode):
    """Fail closed on missing declarations and mismatched numerical observations.

    This host check links values to runtime observations. Scientific method legitimacy
    remains an explicit duty of the independent Agent and dynamic/source audit.
    """
    if mode not in PROFILES:
        raise ValueError('Unknown comparison profile')
    if mode == STRICT:
        if metadata.get('comparison_policy', {}).get('mode', STRICT) != STRICT:
            raise ValueError('Measured metadata cannot pass the strict profile')
        return {'mode': mode, 'host_link_check': 'not_applicable',
                'strict_reported_reference_review': 'required_independently'}
    policy = metadata.get('comparison_policy', {})
    expected = {'mode': MEASURED, 'version': VERSION, 'literature_sota': False,
                'legacy_is_sota_semantics': LEGACY_SEMANTICS}
    if any(type(policy.get(k)) is not type(v) or policy.get(k) != v for k, v in expected.items()):
        raise ValueError('Missing or invalid explicit measured-profile declaration')
    observations = {}
    for row in calibration:
        if row['id'] in observations:
            raise ValueError('Duplicate comparison calibration ID')
        observations[row['id']] = row
    linked = []
    groups = metadata.get('evaluation_comparisons', [])
    if not groups:
        raise ValueError('Measured profile requires comparison groups')
    for item in groups:
        for group in item['comparison_results']:
            conditions = set()
            rows = group['results']
            if len(rows) < 2:
                raise ValueError('Measured comparison needs distinct strong and weak methods')
            for row in rows:
                if (row.get('reference_origin') != 'measured'
                        or row.get('literature_sota') is not False
                        or row.get('information_boundary') != 'solver_inputs_only'
                        or row.get('method_role') == 'paper_method'):
                    raise ValueError('Invalid measured provenance or Solver information boundary')
                condition = row.get('comparison_conditions_id')
                if not isinstance(condition, str) or not condition.strip():
                    raise ValueError('Missing shared comparison conditions')
                conditions.add(condition)
                observed = observations.get(row.get('calibration_id'))
                value = row.get('result')
                if (observed is None or type(value) not in (int, float)
                        or not math.isfinite(value)
                        or type(observed['value']) not in (int, float)
                        or not math.isfinite(observed['value'])
                        or not math.isclose(value, observed['value'], rel_tol=1e-10, abs_tol=1e-12)):
                    raise ValueError('Comparison score lacks matching real calibration evidence')
                if not observed.get('method', '').strip() or not observed.get('conditions', '').strip():
                    raise ValueError('Calibration needs actual method and evaluation conditions')
                linked.append({'item_id': item['item_id'], 'instances': group['instances'],
                               'calibration_id': row['calibration_id'], 'value': value,
                               'observation_sha256': digest(observed)})
            if len(conditions) != 1:
                raise ValueError('Strong and weak methods must share comparison conditions')
            if len({row['calibration_id'] for row in rows}) != len(rows):
                raise ValueError('Strong and weak methods need distinct measured observations')
    if not linked:
        raise ValueError('No measured comparison observations linked')
    return {'mode': mode, 'version': VERSION, 'host_link_check': 'pass',
            'literature_sota': False, 'legacy_is_sota_semantics': LEGACY_SEMANTICS,
            'linked_observations': linked, 'strict_upstream_complete': False,
            'independent_method_and_boundary_review': 'required'}


def audit_package(package, calibration, mode, evidence):
    result = audit_metadata(load(package / 'metadata.json'), calibration, mode)
    save(evidence / 'comparison-profile.json', result)
    return result
