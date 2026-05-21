"""Enrichment utilities to attach IR/TIG facts to grounded LLM analyses.

The goal is to convert the loosely structured `context_bundle` into
referenced IR entities so downstream tools can reason over concrete facts.
"""
from typing import Dict, Any, List


def _access_to_dict(a: Any) -> Dict[str, Any]:
    return {
        'variable_name': getattr(a, 'variable_name', None),
        'file_path': getattr(a, 'file_path', None),
        'line_number': getattr(a, 'line_number', None),
        'thread_id': getattr(a, 'thread_id', None),
        'held_locks': getattr(a, 'held_locks', None) or [],
        'access_type': getattr(a, 'access_type', None).name if getattr(a, 'access_type', None) else None,
        'synchronization_region': None,
        'in_atomic': getattr(a, 'synchronization_primitives', None) and 'ATOMIC' in str(getattr(a, 'synchronization_primitives', [])) if getattr(a, 'synchronization_primitives', None) else False,
        'in_critical': getattr(a, 'in_critical_section', False),
    }


def _var_to_dict(v: Any) -> Dict[str, Any]:
    return {
        'name': getattr(v, 'name', None),
        'file_path': getattr(v, 'file_path', None),
        'declaration_line': getattr(v, 'declaration_line', None),
        'protection_methods': list(getattr(v, 'protection_methods', []) or []),
    }


def _sync_to_dict(s: Any) -> Dict[str, Any]:
    return {
        'lock_name': getattr(s, 'lock_name', None),
        'primitive_type': getattr(s, 'primitive_type', None).name if getattr(s, 'primitive_type', None) else None,
        'file_path': getattr(s, 'file_path', None),
        'line_number': getattr(s, 'line_number', None),
        'acquired_by': getattr(s, 'acquired_by', None) or [],
        'threads_involved': getattr(s, 'threads_involved', None) or [],
        'reduction_variables': getattr(s, 'reduction_variables', None) or [],
        'reduction_ops': getattr(s, 'reduction_ops', None) or {},
        'pragma_scope_start': getattr(s, 'pragma_scope_start', None),
        'pragma_scope_end': getattr(s, 'pragma_scope_end', None),
        'critical_name': getattr(s, 'critical_name', None),
        'is_atomic': getattr(s, 'primitive_type', None).name == 'ATOMIC' if getattr(s, 'primitive_type', None) else False,
        'is_barrier': getattr(s, 'primitive_type', None).name == 'BARRIER' if getattr(s, 'primitive_type', None) else False,
    }


def _thread_to_dict(t: Any) -> Dict[str, Any]:
    return {
        'thread_id': getattr(t, 'thread_id', None),
        'parallelism_model': getattr(t, 'parallelism_model', None).name if getattr(t, 'parallelism_model', None) else None,
        'omp_construct': getattr(t, 'omp_construct', None),
    }


def enrich_result(final: Dict[str, Any], ir: Any) -> Dict[str, Any]:
    """Attach `grounded_facts` to `final` based on its `grounding` bundle.

    Modifies `final` in-place and also returns it.
    """
    if not final or 'grounding' not in final or ir is None:
        return final

    bundle = final.get('grounding') or {}
    chunks = bundle.get('chunks', []) or []

    vars_found = {}
    accesses_found = {}
    syncs_found = {}
    threads_found = {}

    # Index IR for quick lookup
    try:
        ir_accesses = getattr(ir, 'all_accesses', []) or []
        ir_vars = getattr(ir, 'all_variables', []) or []
        ir_syncs = getattr(ir, 'all_synchronization_points', []) or []
        ir_threads = getattr(ir, 'all_threads', []) or []
    except Exception:
        ir_accesses = ir_vars = ir_syncs = ir_threads = []

    for c in chunks:
        vname = c.get('variable_name')
        fpath = c.get('provenance', {}).get('file_path') or c.get('file_path') or c.get('provenance') and c['provenance'].get('file_path')
        tid = c.get('thread_id')

        # Match accesses by variable & file & proximity
        for a in ir_accesses:
            try:
                if vname and getattr(a, 'variable_name', None) == vname:
                    if fpath and getattr(a, 'file_path', None) != fpath:
                        continue
                    key = f"access:{getattr(a,'file_path',None)}:{getattr(a,'line_number',None)}:{getattr(a,'variable_name',None)}"
                    ad = _access_to_dict(a)
                    # annotate reduction scoping from access metadata or syncs
                    if getattr(a, 'in_reduction', False) or getattr(a, 'reduction_operator', None):
                        ad['reduction_scoped'] = True
                        ad['reduction_operator'] = getattr(a, 'reduction_operator', None)
                        ad['protected_by'] = ad.get('protected_by') if ad.get('protected_by') else 'reduction'
                    accesses_found[key] = ad
            except Exception:
                continue

        # Match variables
        for v in ir_vars:
            try:
                if vname and getattr(v, 'name', None) == vname:
                    key = f"var:{getattr(v,'file_path',None)}:{getattr(v,'name',None)}"
                    vars_found[key] = _var_to_dict(v)
            except Exception:
                continue

        # Match sync points by lock name or file/line
        for s in ir_syncs:
            try:
                lname = getattr(s, 'lock_name', None)
                # match by explicit lock name
                if lname and lname in (c.get('held_locks') or []):
                    key = f"sync:{getattr(s,'file_path',None)}:{getattr(s,'line_number',None)}:{lname}"
                    syncs_found[key] = _sync_to_dict(s)
                    continue
                # match by proximity to provenance line
                prov_line = c.get('provenance', {}).get('line')
                if prov_line and getattr(s, 'file_path', None) == fpath and getattr(s, 'line_number', None) and abs(getattr(s, 'line_number') - prov_line) <= 10:
                    key = f"sync:{getattr(s,'file_path',None)}:{getattr(s,'line_number',None)}:prox"
                    syncs_found[key] = _sync_to_dict(s)
                    continue
            except Exception:
                continue

        # Match threads
        for t in ir_threads:
            try:
                if tid and getattr(t, 'thread_id', None) == tid:
                    key = f"thread:{getattr(t,'thread_id',None)}"
                    threads_found[key] = _thread_to_dict(t)
            except Exception:
                continue

    final['grounded_facts'] = {
        'variables': list(vars_found.values()),
        'accesses': list(accesses_found.values()),
        'synchronization_points': list(syncs_found.values()),
        'threads': list(threads_found.values()),
    }

    # Post-process accesses to annotate synchronization_region and lock ownership where possible
    try:
        for a in final['grounded_facts']['accesses']:
            alin = a.get('line_number')
            afile = a.get('file_path')
            for s in ir_syncs:
                if getattr(s, 'file_path', None) != afile:
                    continue
                start = getattr(s, 'pragma_scope_start', None) or getattr(s, 'line_number', None)
                end = getattr(s, 'pragma_scope_end', None) or getattr(s, 'line_number', None)
                if start is None or end is None:
                    continue
                if alin is not None and start <= alin <= end:
                    # annotate synchronization region
                    a['synchronization_region'] = {
                        'sync_line': getattr(s, 'line_number', None),
                        'primitive': getattr(s, 'primitive_type', None).name if getattr(s, 'primitive_type', None) else None,
                        'reduction_variables': getattr(s, 'reduction_variables', None) or [],
                        'lock_name': getattr(s, 'lock_name', None),
                    }
                    # if this sync has an acquired_by list, mark lock ownership
                    if getattr(s, 'acquired_by', None):
                        a['held_locks'] = a.get('held_locks', []) or []
                        if getattr(s, 'lock_name', None):
                            a['held_locks'].append(getattr(s, 'lock_name'))
                    break
    except Exception:
        pass

    return final
