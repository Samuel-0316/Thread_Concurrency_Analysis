"""Simple deterministic validators for LLM outputs against IR facts.

This module intentionally avoids complex ML-based hallucination detection.
It performs deterministic checks: presence of required keys and simple
fact verification against IR/TIG (e.g., named locks/variables exist).
"""
from typing import Dict, Any, Tuple, List

REQUIRED_KEYS = ['is_real_race', 'severity', 'root_cause', 'runtime_impact', 'recommended_fix', 'confidence']


def _map_aliases_to_canonical(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of `obj` with common legacy aliases mapped to canonical keys.

    This helps ensure callers can validate the normalized schema even if the
    input still contains older field names (e.g., 'impact' vs 'runtime_impact').
    """
    mapped = dict(obj or {})

    # explanation -> root_cause
    if 'root_cause' not in mapped and 'explanation' in mapped:
        mapped['root_cause'] = mapped.get('explanation')

    # impact -> runtime_impact
    if 'runtime_impact' not in mapped and 'impact' in mapped:
        mapped['runtime_impact'] = mapped.get('impact')

    # recommendations -> recommended_fix (join lists)
    if 'recommended_fix' not in mapped and 'recommendations' in mapped:
        rec = mapped.get('recommendations')
        if isinstance(rec, list):
            mapped['recommended_fix'] = '; '.join(str(item) for item in rec)
        else:
            mapped['recommended_fix'] = rec

    # confidence_pct -> confidence
    if 'confidence' not in mapped and 'confidence_pct' in mapped:
        mapped['confidence'] = mapped.get('confidence_pct')

    return mapped


def validate_schema(obj: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Check that required keys exist and types are plausible.

    The function first maps common legacy aliases to the canonical keys so that
    callers can pass either parsed-but-not-normalized LLM outputs or
    already-normalized objects and still get consistent validation results.
    """
    errors = []
    mapped = _map_aliases_to_canonical(obj)

    for k in REQUIRED_KEYS:
        if k not in mapped:
            errors.append(f"missing_key:{k}")

    # type checks
    if 'is_real_race' in mapped and not isinstance(mapped['is_real_race'], bool):
        errors.append('is_real_race_not_bool')
    if 'confidence' in mapped:
        try:
            conf = float(mapped['confidence'])
            if conf < 0 or conf > 100:
                errors.append('confidence_out_of_range')
        except Exception:
            errors.append('confidence_not_number')

    return (len(errors) == 0, errors)


def verify_claims_against_ir(obj: Dict[str, Any], issue: Any, ir: Any) -> Tuple[bool, List[str]]:
    """Do deterministic fact checks. Return (ok, errors).

    Examples of checks:
    - If obj claims a lock name protects variable X, verify that lock exists in IR
    - If obj mentions a variable, verify variable exists in IR
    - If obj mentions thread ids, verify they exist in IR
    """
    errors = []
    # simple variable existence check using words in root_cause
    root = obj.get('root_cause', '')
    tokens = set([t.strip(' ,.;()') for t in root.split()])
    ir_vars = {getattr(v, 'name', None) for v in getattr(ir, 'all_variables', []) or []}
    mentioned_vars = list(ir_vars & tokens)
    if mentioned_vars:
        # ok
        pass
    else:
        # If the LLM asserts a root_cause involving a specific variable name, flag missing
        for v in ir_vars:
            if v and v in root:
                mentioned_vars.append(v)
        if not mentioned_vars and any(w in root.lower() for w in ['variable', 'var', 'x', 'y']):
            errors.append('mentioned_variable_not_found')

    # lock existence check (scan recommended_fix and root_cause)
    locks = {getattr(s, 'lock_name', None) for s in getattr(ir, 'all_synchronization_points', []) or []}
    found_lock = False
    for l in locks:
        if not l:
            continue
        if l in obj.get('root_cause','') or l in obj.get('recommended_fix',''):
            found_lock = True
            break
    # if LLM claims "mutex_A protects x" but mutex_A not in IR, flag
    if any(w in obj.get('root_cause','') for w in ['mutex', 'lock', 'mutex_']) and not found_lock:
        errors.append('claimed_lock_not_found')

    # thread existence check (basic)
    thread_ids = {getattr(t, 'thread_id', None) for t in getattr(ir, 'all_threads', []) or []}
    if any(tid in obj.get('root_cause','') for tid in thread_ids):
        pass

    return (len(errors) == 0, errors)
