"""IR-aware RAG retriever (lightweight, issue-centric).

Design decisions (kept intentionally simple per guidance):
- Scoring only uses: direct thread match, same variable, same function scope, same lock context.
- Chronology simulated by line numbers and access ordering.
- Deterministic provenance included for verification.
- No probabilistic ranking or complex graph weighting.
"""
from typing import List, Dict, Any, Optional


def _score_chunk(chunk: Dict[str, Any], issue: Any) -> float:
    """Concurrency-aware scoring prioritizing same file, thread, scope, lock, and line proximity.

    Variable-name overlap alone is intentionally weak to avoid unrelated-file pollution.
    """
    score = 0.0
    chunk_file = chunk.get('provenance', {}).get('file_path')
    issue_files = {getattr(a, 'file_path', None) for a in getattr(issue, 'accesses', []) or []}
    issue_threads = {getattr(a, 'thread_id', None) for a in getattr(issue, 'accesses', []) or []}
    issue_vars = {getattr(a, 'variable_name', None) for a in getattr(issue, 'accesses', []) or []}
    issue_locks = set()
    for a in getattr(issue, 'accesses', []) or []:
        issue_locks.update(getattr(a, 'held_locks', None) or [])

    if chunk_file and chunk_file in issue_files:
        score += 2.0
    if chunk.get('thread_id') and getattr(issue, 'accesses', None):
        # direct thread match: any access thread matches chunk thread
        if chunk['thread_id'] in issue_threads:
            score += 4.0
    if chunk.get('variable_name') and getattr(issue, 'accesses', None):
        if chunk['variable_name'] in issue_vars:
            # weak signal unless the file also matches
            score += 0.5 if not chunk_file or chunk_file not in issue_files else 2.0
    if chunk.get('function_scope') and getattr(issue, 'accesses', None):
        # function scope match (coarse)
        for a in issue.accesses:
            if getattr(a, 'function_scope', None) == chunk['function_scope']:
                score += 2.0
                break
    if chunk.get('held_locks') and getattr(issue, 'accesses', None):
        # overlap in locks
        if issue_locks & set(chunk.get('held_locks', [])):
            score += 2.0
    # Boost chunks that represent synchronization points or share synchronization neighborhood
    prov = chunk.get('provenance', {}) or {}
    prov_line = prov.get('line')
    # If chunk is a sync point (text contains 'Sync' or held_locks present), reward proximity and file match
    if ('Sync' in (chunk.get('text') or '') or chunk.get('held_locks')):
        if chunk_file and chunk_file in issue_files:
            score += 1.5
        # reward if sync is near any issue access line
        if prov_line is not None:
            for a in getattr(issue, 'accesses', []) or []:
                ia_line = getattr(a, 'line_number', None)
                if ia_line is not None and abs(ia_line - prov_line) <= 10:
                    score += 2.0
                    break
    # Reward same pragma/parallel region: if issue access has omp_pragma_line match
    for a in getattr(issue, 'accesses', []) or []:
        ia_pragma = getattr(a, 'omp_pragma_line', None)
        if ia_pragma and prov_line and ia_pragma == prov_line:
            score += 2.0
            break
    # Boost matching synchronization construct types (atomic, critical, reduction, barrier)
    issue_constructs = set()
    for a in getattr(issue, 'accesses', []) or []:
        if getattr(a, 'in_reduction', False):
            issue_constructs.add('reduction')
        if getattr(a, 'in_critical_section', False):
            issue_constructs.add('critical')
        sync_prims = getattr(a, 'synchronization_primitives', None) or []
        for sp in sync_prims:
            pname = sp.name if hasattr(sp, 'name') else str(sp)
            if 'ATOMIC' in pname:
                issue_constructs.add('atomic')
            elif 'BARRIER' in pname:
                issue_constructs.add('barrier')
            elif 'REDUCTION' in pname:
                issue_constructs.add('reduction')
    # Chunk sync info
    chunk_sync = None
    if 'Sync' in (chunk.get('text') or ''):
        if 'atomic' in (chunk.get('text') or '').lower():
            chunk_sync = 'atomic'
        elif 'critical' in (chunk.get('text') or '').lower():
            chunk_sync = 'critical'
        elif 'reduction' in (chunk.get('text') or '').lower():
            chunk_sync = 'reduction'
        elif 'barrier' in (chunk.get('text') or '').lower():
            chunk_sync = 'barrier'
    # Boost for same synchronization construct
    if chunk_sync and chunk_sync in issue_constructs:
        score += 2.5
    # small boost for recent/nearby lines (simulate chronology)
    if chunk.get('line_distance') is not None:
        # closer is better
        dist = chunk['line_distance']
        if dist == 0:
            score += 3.0
        else:
            score += max(0.0, 2.0 - min(2.0, dist / 100.0))
    return score


def _make_provenance(item: Any) -> Dict[str, Any]:
    """Return canonical provenance metadata for an IR item (access/variable/thread)."""
    prov = {}
    prov['file_path'] = getattr(item, 'file_path', None)
    prov['line'] = getattr(item, 'line_number', None)
    prov['thread_id'] = getattr(item, 'thread_id', None)
    prov['variable_name'] = getattr(item, 'variable_name', None) or getattr(item, 'name', None)
    prov['function_scope'] = getattr(item, 'function_scope', None)
    return prov


def extract_context_for_issue(issue: Any, ir: Any, max_chunks: int = 10) -> List[Dict[str, Any]]:
    """Extract prioritized context chunks for a `ConcurrencyIssue`.

    Args:
        issue: ConcurrencyIssue object
        ir: IRRepository (must have attributes: all_accesses, all_variables, all_threads)
        max_chunks: maximum returned chunks

    Returns:
        List of dicts: {text, provenance, score}
    """
    chunks: List[Dict[str, Any]] = []

    # Candidate sources: accesses (nearby accesses to same variable/file/thread), variable declaration, threads, sync points
    # 1) Accesses in IR: find accesses to same variable or same file/function
    issue_var_names = {getattr(a, 'variable_name', None) for a in getattr(issue, 'accesses', [])}
    issue_file_paths = {getattr(a, 'file_path', None) for a in getattr(issue, 'accesses', [])}
    issue_thread_ids = {getattr(a, 'thread_id', None) for a in getattr(issue, 'accesses', [])}

    for a in getattr(ir, 'all_accesses', []) or []:
        # Create minimal chunk
        var_name = getattr(a, 'variable_name', None)
        file_path = getattr(a, 'file_path', None)
        line_number = getattr(a, 'line_number', None)

        # compute line distance to any issue access (simulate chronology)
        min_dist = None
        for ia in getattr(issue, 'accesses', []) or []:
            ia_line = getattr(ia, 'line_number', None)
            if ia_line is not None and line_number is not None:
                d = abs(ia_line - line_number)
                min_dist = d if min_dist is None else min(min_dist, d)
        line_distance = min_dist if min_dist is not None else 9999

        chunk = {
            'text': f"Access to {var_name} at {file_path}:{line_number} (thread={getattr(a,'thread_id',None)})",
            'provenance': _make_provenance(a),
            'thread_id': getattr(a, 'thread_id', None),
            'variable_name': var_name,
            'function_scope': getattr(a, 'function_scope', None),
            'held_locks': getattr(a, 'held_locks', None) or [],
            'line_distance': line_distance,
        }
        chunk['score'] = _score_chunk(chunk, issue)
        # Filter obvious noise early: unrelated file + unrelated variable + no thread evidence
        if chunk['score'] <= 0.0:
            continue
        chunks.append(chunk)

    # 2) Variable declarations
    for v in getattr(ir, 'all_variables', []) or []:
        if getattr(v, 'name', None) not in issue_var_names and getattr(v, 'file_path', None) not in issue_file_paths:
            continue
        chunk = {
            'text': f"Variable {getattr(v, 'name', None)} declared at {getattr(v, 'file_path', None)}:{getattr(v, 'declaration_line', None)}",
            'provenance': _make_provenance(v),
            'thread_id': None,
            'variable_name': getattr(v, 'name', None),
            'function_scope': getattr(v, 'function_scope', None),
            'held_locks': getattr(v, 'protection_methods', []) or [],
            'line_distance': 0,
        }
        chunk['score'] = _score_chunk(chunk, issue)
        chunks.append(chunk)

    # 3) Thread contexts
    for t in getattr(ir, 'all_threads', []) or []:
        if getattr(t, 'thread_id', None) not in issue_thread_ids and issue_thread_ids:
            continue
        chunk = {
            'text': f"Thread {getattr(t, 'thread_id', None)} (model={getattr(t, 'parallelism_model', None)})",
            'provenance': {'thread_id': getattr(t, 'thread_id', None)},
            'thread_id': getattr(t, 'thread_id', None),
            'variable_name': None,
            'function_scope': None,
            'held_locks': [],
            'line_distance': 9999,
        }
        chunk['score'] = _score_chunk(chunk, issue)
        chunks.append(chunk)

    # 4) Synchronization points
    for s in getattr(ir, 'all_synchronization_points', []) or []:
        if issue_file_paths and getattr(s, 'file_path', None) not in issue_file_paths:
            continue
        chunk = {
            'text': f"Sync {getattr(s,'primitive_type',None)} at {getattr(s,'file_path',None)}:{getattr(s,'line_number',None)} acquired_by={getattr(s,'acquired_by',None)}",
            'provenance': {'file_path': getattr(s,'file_path',None), 'line': getattr(s,'line_number',None)},
            'thread_id': None,
            'variable_name': None,
            'function_scope': None,
            'held_locks': getattr(s, 'lock_name', None) and [getattr(s, 'lock_name')] or [],
            'line_distance': 9999,
        }
        chunk['score'] = _score_chunk(chunk, issue)
        chunks.append(chunk)

    # Sort by score desc and return top-k
    chunks_sorted = sorted(chunks, key=lambda c: c['score'], reverse=True)
    return chunks_sorted[:max_chunks]


# ---------------------------------------------------------------------------
# Knowledge Base retrieval
# ---------------------------------------------------------------------------

import json as _json
import os as _os

_KB_DIR = _os.path.join(_os.path.dirname(__file__), 'knowledge_base')
_PATTERNS_CACHE: Optional[List] = None
_STRATEGIES_CACHE: Optional[Dict] = None


def _load_patterns() -> List[Dict]:
    global _PATTERNS_CACHE
    if _PATTERNS_CACHE is not None:
        return _PATTERNS_CACHE
    path = _os.path.join(_KB_DIR, 'patterns.json')
    if not _os.path.isfile(path):
        _PATTERNS_CACHE = []
        return _PATTERNS_CACHE
    with open(path, 'r', encoding='utf-8') as f:
        _PATTERNS_CACHE = _json.load(f)
    return _PATTERNS_CACHE


def _load_strategies() -> Dict[str, Dict]:
    global _STRATEGIES_CACHE
    if _STRATEGIES_CACHE is not None:
        return _STRATEGIES_CACHE
    path = _os.path.join(_KB_DIR, 'fix_strategies.json')
    if not _os.path.isfile(path):
        _STRATEGIES_CACHE = {}
        return _STRATEGIES_CACHE
    with open(path, 'r', encoding='utf-8') as f:
        _STRATEGIES_CACHE = _json.load(f)
    return _STRATEGIES_CACHE


def retrieve_from_knowledge_base(
    issue: Any,
    language: str = 'c',
    max_patterns: int = 3,
) -> Dict[str, Any]:
    """Match a finding against the curated bug-pattern knowledge base.

    Returns the most relevant patterns and their fix strategies.

    Args:
        issue: A ConcurrencyIssue or dict with keys like 'category', 'reason', 'variable'
        language: 'c' or 'python'
        max_patterns: Maximum number of patterns to return

    Returns:
        Dict with 'matched_patterns' (list) and 'fix_strategies' (list)
    """
    patterns = _load_patterns()
    strategies = _load_strategies()

    # Extract issue attributes
    if isinstance(issue, dict):
        category = issue.get('category', issue.get('reason', ''))
        reason = issue.get('reason', '')
        variable = issue.get('variable', '')
    else:
        category = getattr(issue, 'category', getattr(issue, 'reason', ''))
        reason = getattr(issue, 'reason', '')
        variable = getattr(issue, 'variable', '')

    if hasattr(variable, 'name'):
        variable = variable.name

    # Score each pattern
    scored = []
    for pat in patterns:
        score = 0.0

        # Language match
        if language in pat.get('language', []):
            score += 2.0
        else:
            continue  # Skip patterns for other languages

        # Category match
        pat_cat = pat.get('category', '')
        if pat_cat and (pat_cat in category or pat_cat in reason or category in pat_cat):
            score += 5.0

        # Keyword matching on reason/description
        reason_lower = (reason + ' ' + category).lower()
        name_lower = pat.get('name', '').lower()
        desc_lower = pat.get('description', '').lower()

        keywords = ['race', 'reduction', 'atomic', 'critical', 'shared', 'lock',
                     'barrier', 'nowait', 'deadlock', 'parallel for', 'unprotected']
        for kw in keywords:
            if kw in reason_lower and kw in desc_lower:
                score += 1.0

        # Boost for specific patterns based on common reasons
        if 'unprotected' in reason_lower and 'unprotected' in name_lower:
            score += 3.0
        if 'reduction' in reason_lower and 'reduction' in name_lower:
            score += 3.0
        if 'indirect' in reason_lower and 'indirect' in name_lower:
            score += 3.0
        if 'lock' in reason_lower and 'lock' in name_lower:
            score += 3.0

        scored.append((score, pat))

    # Sort by score, take top N
    scored.sort(key=lambda x: x[0], reverse=True)
    matched = [pat for score, pat in scored[:max_patterns] if score > 0]

    # Collect relevant fix strategies
    strategy_ids = set()
    for pat in matched:
        strategy_ids.update(pat.get('fix_strategies', []))

    matched_strategies = []
    for sid in strategy_ids:
        if sid in strategies:
            matched_strategies.append({
                'id': sid,
                **strategies[sid],
            })

    return {
        'matched_patterns': matched,
        'fix_strategies': matched_strategies,
    }


def make_context_bundle(issue: Any, ir: Any, tig: Optional[Any] = None,
                        max_chunks: int = 10, language: str = 'c') -> Dict[str, Any]:
    """High-level bundle suitable for LLM: includes prioritized chunks + simple TIG summary + KB patterns."""
    chunks = extract_context_for_issue(issue, ir, max_chunks=max_chunks)

    # simple tig summary: list threads and relationships limited to issue threads
    tig_summary = {'threads': [], 'relationships': []}
    try:
        issue_threads = {getattr(a, 'thread_id', None) for a in getattr(issue, 'accesses', [])}
        for c in chunks:
            tid = c.get('thread_id')
            if tid and tid not in tig_summary['threads']:
                tig_summary['threads'].append(tid)
        # If TIG available, try to extract direct edges between issue threads (simple)
        if tig is not None:
            for t1 in tig_summary['threads']:
                for t2 in tig_summary['threads']:
                    if t1 == t2:
                        continue
                    if tig.has_edge(t1, t2) or tig.has_edge(t2, t1):
                        tig_summary['relationships'].append({'from': t1, 'to': t2})
    except Exception:
        pass

    # Retrieve from knowledge base
    kb_results = retrieve_from_knowledge_base(issue, language=language)

    return {
        'issue_id': getattr(issue, 'issue_id', None),
        'chunks': chunks,
        'tig_summary': tig_summary,
        'knowledge_base': kb_results,
    }

