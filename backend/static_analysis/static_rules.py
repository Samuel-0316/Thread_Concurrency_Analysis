import re
from collections import Counter
from typing import List, Dict, Optional
import networkx as nx
from backend.ir.ir_schema_v2 import (
    IRRepository, ConcurrencyIssue, MemoryAccess, Variable,
    AccessType, SynchronizationPrimitive, ConfidenceLevel,
    find_unprotected_accesses, find_concurrent_accesses
)
from backend.tig.tig_builder import (
    build_tig_from_ir, 
    find_unprotected_accesses_in_tig,
    find_concurrent_accesses_in_tig
)


def find_unsynchronized_accesses(G: nx.DiGraph) -> List[Dict]:
    """Detect potential unsynchronized accesses.

    Heuristic:
    - For each edge thread -> var with relation 'may_access', if the file containing the thread
      has no lock nodes (type='lock') then flag as potential unsynchronized access.

    This is intentionally conservative and lightweight for the MVP.
    """
    findings = []

    # map file nodes to whether they have locks
    file_lock_map = {}
    for n, d in G.nodes(data=True):
        if d.get('type') == 'file':
            has_lock = False
            for succ in G.successors(n):
                sdata = G.nodes[succ]
                if sdata.get('type') == 'lock':
                    has_lock = True
                    break
            file_lock_map[n] = has_lock

    for u, v, ed in G.edges(data=True):
        if ed.get('relation') == 'may_access':
            thread_node = u
            var_node = v
            tdata = G.nodes.get(thread_node, {})
            # determine file node for thread
            source = tdata.get('source')
            file_node = f"file:{source}" if source else None
            has_lock = file_lock_map.get(file_node, False)
            if not has_lock:
                findings.append({
                    'thread': thread_node,
                    'variable': var_node,
                    'file': source,
                    'reason': 'no_lock_in_file',
                })

    return findings


def find_lock_order_pairs(G: nx.DiGraph) -> Dict:
    """For each thread, produce ordered lock acquisition pairs based on lineno heuristics.

    Returns a mapping of thread -> ordered list of lock node ids.
    """
    thread_locks = {}
    for n, d in G.nodes(data=True):
        if d.get('type') == 'thread':
            locks = []
            for succ in G.successors(n):
                ed = G.get_edge_data(n, succ)
                if ed and ed.get('relation') in ('acquires', 'contains'):
                    sdata = G.nodes[succ]
                    if sdata.get('type') == 'lock':
                        locks.append((succ, sdata.get('lineno', 0)))
            # sort locks by lineno as a heuristic for acquisition order
            locks_sorted = [ln for ln, _ in sorted(locks, key=lambda x: (x[1] or 0))]
            thread_locks[n] = locks_sorted
    return thread_locks


def find_lock_order_violations(G: nx.DiGraph) -> List[Dict]:
    """Detect inconsistent lock ordering across threads.

    Heuristic:
    - For each thread, list ordered lock pairs (A,B) where A acquired before B.
    - If any pair (A,B) and (B,A) observed across threads, flag violation.
    """
    violations = []
    thread_locks = find_lock_order_pairs(G)
    pair_orders = {}
    for t, locks in thread_locks.items():
        for i in range(len(locks)):
            for j in range(i + 1, len(locks)):
                a, b = locks[i], locks[j]
                pair_orders.setdefault((a, b), set()).add(t)

    # check opposite orders
    for (a, b), threads_ab in pair_orders.items():
        if (b, a) in pair_orders:
            threads_ba = pair_orders[(b, a)]
            violations.append({'pair': (a, b), 'threads_ab': list(threads_ab), 'threads_ba': list(threads_ba)})

    return violations


def find_deadlock_cycles(G: nx.DiGraph) -> List[List[str]]:
    """Build a lock-order graph and return cycles (potential deadlocks).

    Heuristic:
    - For each thread's ordered lock list, add edges A->B for consecutive acquisitions.
    - Then find cycles in this lock graph.
    """
    L = nx.DiGraph()
    thread_locks = find_lock_order_pairs(G)
    for locks in thread_locks.values():
        for i in range(len(locks)):
            for j in range(i + 1, len(locks)):
                a, b = locks[i], locks[j]
                L.add_edge(a, b)

    cycles = list(nx.simple_cycles(L))
    return cycles


def run_all_rules(G: nx.DiGraph) -> Dict:
    """Run all static rules and return aggregated findings."""
    return {
        'unsynchronized_accesses': find_unsynchronized_accesses(G),
        'lock_order_violations': find_lock_order_violations(G),
        'deadlock_cycles': find_deadlock_cycles(G),
    }


def find_openmp_races(parsed_files: List[Dict]) -> List[Dict]:
    """Flag likely OpenMP data races using parsed metadata.

    Heuristic:
    - Look for C files with OpenMP parallel/parallel-for pragmas.
    - Treat writes to shared variables as suspicious unless those variables are
      explicitly listed in private/firstprivate/lastprivate/reduction clauses.
    """
    findings = []
    suppressed = []
    ignore_names = {
        'i', 'j', 'k', 'l', 'm', 'n', 'p', 'q', 'r', 's', 't',
        'idx', 'id', 'tmp', 'temp', 'len', 'size', 'count', 'main',
        'argc', 'argv', 'error', 'result', 'res', 'data'
    }
    for f in parsed_files:
        if f.get('language') != 'c':
            continue

        pragmas = f.get('omp_pragmas', [])
        if not any(p.get('kind') in ('parallel', 'parallel_for', 'for') for p in pragmas):
            continue

        protected = set(f.get('omp_private', []))
        protected.update(f.get('omp_firstprivate', []))
        protected.update(f.get('omp_lastprivate', []))
        protected.update(f.get('omp_reduction', []))
        critical_guarded = set(f.get('omp_critical_vars', []))

        has_critical = any(p.get('kind') == 'critical' for p in pragmas)

        variable_hits = Counter(f.get('var_reads', []))
        variable_hits.update(f.get('var_writes', []))

        shared_candidates = set(f.get('shared_variables', []))
        shared_candidates.update(f.get('omp_shared', []))

        # only keep variables that are actually written in the file or explicitly shared
        written = set(f.get('var_writes', []))
        shared_candidates = shared_candidates.intersection(written) | set(f.get('omp_shared', []))

        # remove obvious loop counters / function names / generic locals
        shared_candidates = {
            var for var in shared_candidates
            if var
            and var not in ignore_names
            and not re.match(r'^[A-Z]$', var)
            and not var.isdigit()
        }

        for var in sorted(shared_candidates):
            if not var:
                continue

            occurrences = variable_hits.get(var, 0)
            confidence = 0.9
            reasons = []

            if var in protected:
                confidence = 0.1
                reasons.append('protected_by_clause')
            if var in critical_guarded:
                confidence = min(confidence, 0.25)
                reasons.append('guarded_by_critical')
            if occurrences <= 1:
                confidence = min(confidence, 0.25)
                reasons.append('singleton_name')
            if has_critical and var not in set(f.get('omp_shared', [])):
                confidence = min(confidence, 0.45)
                reasons.append('critical_present')

            finding = {
                'file': f.get('path'),
                'variable': var,
                'reason': 'openmp_shared_write',
                'pragmas': [p.get('kind') for p in pragmas if p.get('kind') in ('parallel', 'parallel_for', 'for', 'critical')],
                'confidence': round(confidence, 2),
            }
            if reasons:
                finding['notes'] = reasons

            if confidence >= 0.5:
                findings.append(finding)
            else:
                suppressed.append(finding)

    return {'findings': findings, 'suppressed': suppressed}


# ============================================================================
# IR-BASED ANALYSIS (Comprehensive, Type-Safe, Confidence-Aware)
# ============================================================================

def find_data_races_from_ir(ir: IRRepository) -> List[ConcurrencyIssue]:
    """Find data races using comprehensive IR.
    
    Clause-aware: skips variable pairs where either access has
    synchronization primitives, in_reduction/in_critical flags, or
    the variable appears in reduction/private/firstprivate/lastprivate
    clauses on the access's omp_clauses metadata.
    
    Args:
        ir: IRRepository with all concurrency information
        
    Returns:
        List of ConcurrencyIssue objects with data_race type
    """
    races = []
    
    # Query 1: Find concurrent accesses to same variable
    concurrent_pairs = find_concurrent_accesses(ir)
    
    for a1, a2 in concurrent_pairs:
        # Check if this is an actual race (at least one write, no synchronization)
        has_write = (
            a1.access_type in [AccessType.WRITE, AccessType.READ_WRITE, AccessType.ATOMIC_CAS] or
            a2.access_type in [AccessType.WRITE, AccessType.READ_WRITE, AccessType.ATOMIC_CAS]
        )
        
        if not has_write:
            continue  # Read-read is safe
        
        # Check synchronization protection (including clause-based)
        a1_protected = bool(a1.held_locks or a1.synchronization_primitives or a1.in_reduction or a1.in_critical_section)
        a2_protected = bool(a2.held_locks or a2.synchronization_primitives or a2.in_reduction or a2.in_critical_section)
        
        if a1_protected or a2_protected:
            continue  # At least one side is clause-protected → no race

        # Check if variable is in a protective clause on either access
        var_name = a1.variable_name
        for acc in (a1, a2):
            clauses = getattr(acc, 'omp_clauses', None) or {}
            protected_vars = set(clauses.get('private', []) or [])
            protected_vars.update(clauses.get('firstprivate', []) or [])
            protected_vars.update(clauses.get('lastprivate', []) or [])
            protected_vars.update(clauses.get('reduction', []) or [])
            if var_name in protected_vars:
                a1_protected = True
                break

        if a1_protected:
            continue  # Clause-protected
        
        # Found a potential race
        var = next((v for v in ir.all_variables if v.name == a1.variable_name), None)
        
        issue = ConcurrencyIssue(
            issue_id=f"race_{len(races) + 1}",
            issue_type='data_race',
            accesses=[a1, a2],
            variable=var,
            file_path=a1.file_path or a2.file_path,
            primary_line=a1.line_number if a1.line_number else a2.line_number,
            severity='high',
            confidence=ConfidenceLevel.HIGH,
            reason=f"Variable {a1.variable_name} accessed by {a1.thread_id} and {a2.thread_id} without proper synchronization"
        )
        races.append(issue)
    
    return races


def find_unprotected_accesses_from_ir(ir: IRRepository) -> List[ConcurrencyIssue]:
    """Find accesses without synchronization protection.
    
    Clause-aware: skips accesses that are protected by OpenMP clauses
    (reduction, private, firstprivate, lastprivate), by synchronization
    primitives already assigned in the IR normalizer, or that occur in
    sequential context (outside any parallel region).
    
    Args:
        ir: IRRepository
        
    Returns:
        List of ConcurrencyIssue objects
    """
    issues = []
    
    unprotected = find_unprotected_accesses(ir)
    
    for access in unprotected:
        # Filter for writes (reads without locks are not races by themselves)
        if access.access_type not in [AccessType.WRITE, AccessType.READ_WRITE, AccessType.ATOMIC_CAS]:
            continue
        
        # Filter for multi-threaded contexts
        if not access.thread_id or access.parallelism_model.value == 'SEQUENTIAL':
            continue

        # ── Check synchronization primitives set by IR normalizer ──
        # The normalizer marks clause-protected accesses with REDUCTION,
        # BARRIER (for private/firstprivate/lastprivate), CRITICAL_SECTION, etc.
        if access.synchronization_primitives:
            continue

        # ── Check in_reduction flag ──
        if access.in_reduction:
            continue

        # ── Check in_critical_section flag ──
        if access.in_critical_section:
            continue

        # Filter for OpenMP private/firstprivate/lastprivate/reduction variables
        omp_clauses = getattr(access, 'omp_clauses', None) or {}
        private_vars = set(omp_clauses.get('private', []) or [])
        private_vars.update(omp_clauses.get('firstprivate', []) or [])
        private_vars.update(omp_clauses.get('lastprivate', []) or [])
        private_vars.update(omp_clauses.get('reduction', []) or [])

        if access.variable_name in private_vars:
            continue

        # Filter array element writes indexed by loop-private variables in parallel_for
        reason = getattr(access, 'reason', '') or ''
        if access.parallel_construct in ('parallel_for', 'for') and reason.startswith('index_by:'):
            index_vars = {v.strip() for v in reason.replace('index_by:', '').split(',') if v.strip()}
            # Safe if indexed by any private/loop-counter variable
            if index_vars & private_vars:
                continue
            # Also safe if all index vars are common loop counters
            common_counters = {'i', 'j', 'k', 'idx', 'tid'}
            if index_vars & common_counters:
                continue
        
        var = next((v for v in ir.all_variables if v.name == access.variable_name), None)
        
        issue = ConcurrencyIssue(
            issue_id=f"unprotected_{len(issues) + 1}",
            issue_type='unprotected_access',
            accesses=[access],
            variable=var,
            file_path=access.file_path,
            primary_line=access.line_number,
            severity='medium',
            confidence=ConfidenceLevel.MEDIUM,
            reason=f"Unprotected {access.access_type.value} to {access.variable_name} in {access.parallel_construct}"
        )
        issues.append(issue)
    
    return issues



def find_lock_order_violations_from_ir(ir: IRRepository) -> List[ConcurrencyIssue]:
    """Find lock acquisition order violations.
    
    Args:
        ir: IRRepository
        
    Returns:
        List of ConcurrencyIssue objects
    """
    violations = []
    
    # Track lock orders per thread
    thread_lock_orders = {}
    
    for access in ir.all_accesses:
        if not access.held_locks or not access.thread_id:
            continue
        
        if access.thread_id not in thread_lock_orders:
            thread_lock_orders[access.thread_id] = []
        
        # Record lock order for this thread
        for lock in access.held_locks:
            if lock not in thread_lock_orders[access.thread_id]:
                thread_lock_orders[access.thread_id].append(lock)
    
    # Check for violations (lock A before B in one thread, B before A in another)
    pairs_seen = {}
    for thread_id, lock_order in thread_lock_orders.items():
        for i in range(len(lock_order)):
            for j in range(i + 1, len(lock_order)):
                lock_a, lock_b = lock_order[i], lock_order[j]
                pair_key = (lock_a, lock_b)
                reverse_pair = (lock_b, lock_a)
                
                if reverse_pair in pairs_seen:
                    # Violation found
                    violation = ConcurrencyIssue(
                        issue_id=f"lock_order_{len(violations) + 1}",
                        issue_type='lock_order_violation',
                        accesses=[],
                        severity='high',
                        confidence=ConfidenceLevel.HIGH,
                        reason=f"Lock order inconsistency: {lock_a} → {lock_b} in thread {thread_id}, but {lock_b} → {lock_a} in thread {pairs_seen[reverse_pair]}"
                    )
                    violations.append(violation)
                
                pairs_seen[pair_key] = thread_id
    
    return violations


def find_openmp_races_from_ir(ir: IRRepository) -> tuple:
    """Find OpenMP-specific data races using IR.
    
    Uses OpenMP clause information from IR to detect races with better precision.
    
    Args:
        ir: IRRepository with OpenMP metadata
        
    Returns:
        (findings, suppressed) tuple of ConcurrencyIssue lists
    """
    findings = []
    suppressed = []
    
    # Common loop counter names to ignore
    ignore_names = {
        'i', 'j', 'k', 'l', 'm', 'n', 'p', 'q', 'r', 's', 't',
        'idx', 'id', 'tmp', 'temp', 'len', 'size', 'count', 'main',
        'argc', 'argv', 'error', 'result', 'res', 'data'
    }
    
    # Analyze each variable for potential OpenMP races
    for var in ir.all_variables:
        if var.name in ignore_names:
            continue
        
        # Filter accesses to this variable that are in OpenMP contexts
        omp_accesses = [
            a for a in var.accesses
            if a.parallelism_model.value == 'OPENMP'
        ]
        
        if not omp_accesses:
            continue
        
        # Check if all accesses are protected
        unprotected_writes = [
            a for a in omp_accesses
            if a.access_type in [AccessType.WRITE, AccessType.READ_WRITE]
            and not a.held_locks
            and SynchronizationPrimitive.CRITICAL_SECTION not in a.synchronization_primitives
            and SynchronizationPrimitive.REDUCTION not in a.synchronization_primitives
        ]
        
        if not unprotected_writes:
            continue  # Variable is protected
        
        # Compute confidence based on OpenMP context
        confidence = 0.8
        reason_notes = []
        
        # Check for reduction clause (protects from race)
        if any(a.in_reduction for a in omp_accesses):
            confidence = 0.1
            reason_notes.append('protected_by_reduction')
        
        # Check for critical section
        if any(a.in_critical_section for a in omp_accesses):
            confidence = min(confidence, 0.3)
            reason_notes.append('guarded_by_critical')
        
        # Check for private clause
        if any(a.omp_clauses.get('private') for a in omp_accesses):
            confidence = 0.05
            reason_notes.append('declared_private')
        
        # Check for high confidence in IR
        if all(a.confidence == ConfidenceLevel.HIGH for a in omp_accesses):
            confidence = min(confidence, 0.9)
            reason_notes.append('high_confidence_accesses')
        
        # Create issue
        issue = ConcurrencyIssue(
            issue_id=f"omp_race_{len(findings) + len(suppressed) + 1}",
            issue_type='openmp_data_race',
            accesses=unprotected_writes,
            variable=var,
            file_path=var.file_path,
            primary_line=var.declaration_line or 0,
            severity='high' if confidence >= 0.6 else 'medium',
            confidence=ConfidenceLevel.HIGH if confidence >= 0.7 else ConfidenceLevel.MEDIUM,
            reason=f"Potential OpenMP race on shared variable {var.name}"
        )
        issue.recommendations = [
            f"Protect with #pragma omp critical",
            f"Use #pragma omp reduction if applicable",
            f"Declare as private if thread-local",
        ]
        
        if reason_notes:
            issue.reason += f" ({', '.join(reason_notes)})"
        
        if confidence >= 0.5:
            findings.append(issue)
        else:
            suppressed.append(issue)
    
    return findings, suppressed


def run_all_rules_from_ir(ir: IRRepository) -> Dict:
    """Run all analysis rules on IR, producing ConcurrencyIssue objects.
    
    This is the new, comprehensive IR-based analysis pipeline.
    
    Args:
        ir: IRRepository
        
    Returns:
        Dict with findings organized by issue type
    """
    return {
        'data_races': find_data_races_from_ir(ir),
        'unprotected_accesses': find_unprotected_accesses_from_ir(ir),
        'lock_order_violations': find_lock_order_violations_from_ir(ir),
        'openmp_races': find_openmp_races_from_ir(ir)[0],
        'openmp_races_suppressed': find_openmp_races_from_ir(ir)[1],
    }

def run_all_rules(G: nx.DiGraph, parsed_files: List[Dict] = None, ir: Optional[IRRepository] = None) -> Dict:
    """Run all static rules and return aggregated findings.
    
    Supports both legacy (graph-based) and new (IR-based) analysis.
    If IR is provided, uses comprehensive IR analysis (preferred).
    Otherwise falls back to graph-based analysis.
    
    New in Sprint 5: Also runs data-flow, loop, and alias analyses
    to refine finding confidence and add enhanced context.
    """
    
    # Prefer IR-based analysis if available
    if ir is not None:
        findings = run_all_rules_from_ir(ir)
    else:
        # Legacy graph-based analysis (fallback)
        findings = {
            'unsynchronized_accesses': find_unsynchronized_accesses(G),
            'lock_order_violations': find_lock_order_violations(G),
            'deadlock_cycles': find_deadlock_cycles(G),
        }
        if parsed_files is not None:
            openmp = find_openmp_races(parsed_files)
            findings['openmp_races'] = openmp['findings']
            findings['openmp_races_suppressed'] = openmp['suppressed']

    # ── Enhanced analyses (Sprint 5) ──
    enhanced = {}
    try:
        if ir is not None:
            from backend.static_analysis.data_flow import def_use_summary
            enhanced['def_use'] = def_use_summary(ir)
    except Exception:
        pass

    try:
        if parsed_files:
            from backend.static_analysis.loop_analysis import loop_analysis_summary
            enhanced['loop_analysis'] = loop_analysis_summary(parsed_files)
    except Exception:
        pass

    try:
        if parsed_files:
            from backend.static_analysis.alias_analysis import alias_analysis_summary
            enhanced['alias_analysis'] = alias_analysis_summary(parsed_files)
    except Exception:
        pass

    if enhanced:
        findings['enhanced_analysis'] = enhanced

    # ── Confidence refinement ──
    try:
        findings = refine_confidence(findings, enhanced)
    except Exception:
        pass

    return findings


# ---------------------------------------------------------------------------
# Confidence refinement using data-flow + loop + alias evidence
# ---------------------------------------------------------------------------

def refine_confidence(findings: Dict, enhanced: Dict) -> Dict:
    """Adjust finding confidence using def-use, loop, and alias results.

    Rules:
      - If a variable has a racy def-use chain → boost confidence
      - If a variable has a loop-carried dependence → boost confidence
      - If a variable has alias pairs → flag in finding metadata
      - If no enhanced evidence supports the finding → lower confidence
    """
    du = enhanced.get('def_use', {})
    loop = enhanced.get('loop_analysis', {})
    alias = enhanced.get('alias_analysis', {})

    racy_vars = set(du.get('racy_variables', []))
    loop_deps = {d['variable'] for d in loop.get('dependences', [])}
    alias_vars = set()
    for p in alias.get('pairs', []):
        alias_vars.add(p.get('ptr_a', ''))
        alias_vars.add(p.get('ptr_b', ''))

    for key in ['unprotected_accesses', 'openmp_races', 'data_races']:
        for issue in findings.get(key, []):
            var_name = _get_finding_var(issue)
            if not var_name:
                continue

            # Boost if confirmed by data-flow analysis
            if var_name in racy_vars:
                _adjust_confidence(issue, +0.15, 'confirmed_by_def_use')

            # Boost if confirmed by loop analysis
            if var_name in loop_deps:
                _adjust_confidence(issue, +0.10, 'confirmed_by_loop_analysis')

            # Flag if aliased (potential false positive or true positive)
            if var_name in alias_vars:
                _set_metadata(issue, 'has_alias', True)

    return findings


def _get_finding_var(issue) -> str:
    """Extract variable name from a finding."""
    if hasattr(issue, 'accesses') and issue.accesses:
        return issue.accesses[0].variable_name
    if isinstance(issue, dict):
        v = issue.get('variable', '')
        return v.name if hasattr(v, 'name') else v
    return getattr(issue, 'variable', '')


def _adjust_confidence(issue, delta: float, reason: str):
    """Adjust the confidence of a finding."""
    if hasattr(issue, 'confidence'):
        if hasattr(issue.confidence, 'value'):
            pass  # Enum, can't adjust
        elif isinstance(issue.confidence, (int, float)):
            issue.confidence = max(0, min(1, issue.confidence + delta))
    elif isinstance(issue, dict):
        old = issue.get('confidence', 0.5)
        if isinstance(old, (int, float)):
            issue['confidence'] = max(0, min(1, old + delta))
        issue.setdefault('confidence_adjustments', []).append(reason)


def _set_metadata(issue, key: str, value):
    """Set metadata on a finding."""
    if isinstance(issue, dict):
        issue[key] = value
    elif hasattr(issue, '__dict__'):
        setattr(issue, key, value)


# ---------------------------------------------------------------------------
# LLM escalation threshold
# ---------------------------------------------------------------------------

def should_escalate_to_llm(finding) -> bool:
    """Determine if a finding should be escalated to LLM for deeper analysis.

    Routing strategy:
      - HIGH confidence (≥0.8) → deterministic resolution (no LLM needed)
      - MEDIUM confidence (0.4-0.8) → escalate to LLM for validation
      - LOW confidence (<0.4) → escalate to LLM for classification

    Also escalates if the finding has special flags (aliases, cross-file).

    Returns True if the finding should be sent to the LLM agent.
    """
    conf = _get_confidence_value(finding)

    # Always escalate medium-confidence findings
    if 0.4 <= conf < 0.8:
        return True

    # Escalate low confidence too (needs classification)
    if conf < 0.4:
        return True

    # High confidence + alias → escalate (alias may cause false positive)
    if isinstance(finding, dict) and finding.get('has_alias'):
        return True

    # High confidence + cross-file → escalate (complex reasoning needed)
    if isinstance(finding, dict) and finding.get('cross_file'):
        return True

    # High confidence, no special flags → deterministic, no LLM needed
    return False


def _get_confidence_value(finding) -> float:
    """Extract a numeric confidence value from a finding."""
    if isinstance(finding, dict):
        c = finding.get('confidence', 0.5)
    else:
        c = getattr(finding, 'confidence', 0.5)

    if isinstance(c, (int, float)):
        return float(c)

    # Handle ConfidenceLevel enum
    conf_str = str(c).upper()
    if 'HIGH' in conf_str:
        return 0.9
    elif 'MEDIUM' in conf_str:
        return 0.6
    elif 'LOW' in conf_str:
        return 0.3
    return 0.5
