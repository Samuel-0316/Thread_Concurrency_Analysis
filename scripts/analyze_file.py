#!/usr/bin/env python3
"""End-to-end single-file analysis pipeline for the VS Code extension.

Runs the complete pipeline on a single C/Python source file:
  Phase 1 -- Parse (AST extraction)
  Phase 2 -- IR normalisation
  Phase 3 -- TIG construction
  Phase 4 -- Static rule engine (findings)
  Phase 5 -- Multi-agent validation (Analyst -> Critic -> Resolver)
  Phase 6 -- Build Cytoscape-compatible graph elements
  Phase 7 -- Output JSON to stdout (--json mode)

Usage (from repo root):
    python scripts/analyze_file.py <source_file> --json

The script emits a single JSON object on stdout with the structure:
  {
    "file": "<path>",
    "elements": [ ... Cytoscape elements ... ],
    "findings": { ... },
    "summary": { ... }
  }
All diagnostic messages go to stderr when --json is used.
"""
import argparse
import json
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ── make backend importable ──────────────────────────────────────────
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.parser_service.parser import ParserService
from backend.ir.ir_normalizer_v2 import normalize_to_ir
from backend.tig.tig_builder import build_tig_from_ir
from backend.static_analysis.static_rules import run_all_rules, find_openmp_races


def _get_access_var(f):
    """Extract the variable name from a finding (dict or ConcurrencyIssue)."""
    if isinstance(f, dict):
        v = f.get('variable', '')
    elif hasattr(f, 'accesses') and f.accesses:
        v = f.accesses[0].variable_name
    elif hasattr(f, 'variable'):
        v = f.variable
    else:
        v = ''
    if hasattr(v, 'name'):
        v = v.name
    return v or ''


# ---------------------------------------------------------------------------
# Phase 6-like: build Cytoscape elements from TIG graph + findings
# ---------------------------------------------------------------------------

def _node_color(ntype):
    return {
        'thread': '#1f77b4',
        'variable': '#ff7f0e',
        'sync': '#2ca02c',
        'finding': '#d62728',
        'file': '#9467bd',
    }.get(ntype, '#888')


def _node_shape(ntype):
    return {
        'finding': 'triangle',
        'sync': 'diamond',
        'file': 'round-rectangle',
    }.get(ntype, 'ellipse')


def build_cytoscape_elements(tig_graph, findings_dict, file_path, ir=None):
    """Convert a networkx TIG + findings into Cytoscape.js elements list.

    Enhancements over the original:
      - Variable nodes are coloured GREEN (#81c784) when protected by a
        clause (reduction/firstprivate/lastprivate/private) and ORANGE
        (#ffb74d) otherwise.  Driven purely by IR metadata — generic for
        any program.
      - Each variable node carries a `clause_label` data attribute
        (e.g. "reduction(+)", "firstprivate") rendered below the name.
      - A `safe` boolean data attribute distinguishes safe from unsafe.
      - Individual worker thread nodes are rendered (thread_0, thread_1…).
      - An implicit barrier sync node is included when present in the TIG.
      - A `fixes_verified` count is tracked and returned.
    """
    elements = []
    added_ids = set()
    fixes_verified = [0]

    # ── Pre-compute clause protection sets from parser metadata via IR ──
    clause_private = set()
    clause_firstprivate = set()
    clause_lastprivate = set()
    clause_reduction = set()
    clause_reduction_map = {}  # var_name -> operator (e.g. '+')

    if ir is not None:
        for thread in ir.all_threads:
            clauses = getattr(thread, 'omp_clauses', None) or {}
            clause_private.update(clauses.get('private', []))
            clause_firstprivate.update(clauses.get('firstprivate', []))
            clause_lastprivate.update(clauses.get('lastprivate', []))
            clause_reduction.update(clauses.get('reduction', []))
        # Try to extract reduction operators from sync points
        for sp in ir.all_synchronization_points:
            ops = getattr(sp, 'reduction_ops', None)
            if ops:
                clause_reduction_map.update(ops)

    clause_all_protected = clause_private | clause_firstprivate | clause_lastprivate | clause_reduction

    # Loop-counter variables (auto-private) detected from omp_private on IR threads
    auto_private_candidates = set()
    if ir is not None:
        for thread in ir.all_threads:
            clauses = getattr(thread, 'omp_clauses', None) or {}
            auto_private_candidates.update(clauses.get('private', []))

    def _clause_label(var_name):
        """Determine the clause label for a variable (generic)."""
        if var_name in clause_reduction:
            op = clause_reduction_map.get(var_name, '')
            return f"reduction({op})" if op else "reduction"
        if var_name in clause_firstprivate:
            return "firstprivate"
        if var_name in clause_lastprivate:
            return "lastprivate"
        if var_name in clause_private:
            return "private(auto)"
        return ""

    def _is_safe_variable(var_name, data):
        """Check if a variable is clause-protected (generic)."""
        if var_name in clause_all_protected:
            return True
        # Check if always_protected flag is set on the TIG node
        if data.get('always_protected'):
            return True
        # Check if protection_methods is non-empty
        if data.get('protection_methods'):
            return True
        return False

    def _is_indexed_by_private(var_name, data):
        """Check if a shared array is accessed only through private indices."""
        # Heuristic: if the variable node has accesses with reason index_by:i
        # and i is in private set, it's safe
        if ir is not None:
            for access in ir.all_accesses:
                if access.variable_name != var_name:
                    continue
                reason = getattr(access, 'reason', '') or ''
                if reason.startswith('index_by:'):
                    index_vars = {v.strip() for v in reason.replace('index_by:', '').split(',') if v.strip()}
                    if index_vars & auto_private_candidates:
                        return True
        return False

    def _add_node(nid, label, ntype, **extra):
        nid = str(nid)
        if nid in added_ids:
            return
        el = {
            'data': {
                'id': nid,
                'label': str(label),
                'type': ntype,
                'color': _node_color(ntype),
                'shape': _node_shape(ntype),
                **extra,
            }
        }
        elements.append(el)
        added_ids.add(nid)

    def _add_edge(src, dst, rel, **extra):
        src, dst = str(src), str(dst)
        edge_id = f"{src}->{dst}:{rel}"
        if src in added_ids and dst in added_ids and edge_id not in added_ids:
            elements.append({
                'data': {
                    'id': edge_id,
                    'source': src,
                    'target': dst,
                    'type': rel,
                    'label': rel,
                    **extra,
                }
            })
            added_ids.add(edge_id)

    # ── nodes from TIG ──
    for node_id, data in tig_graph.nodes(data=True):
        ntype = data.get('type', 'unknown')
        label = data.get('name') or data.get('thread_id') or data.get('sync_id') or node_id

        if ntype == 'file':
            label = os.path.basename(data.get('path', node_id))
            _add_node(node_id, label, ntype)
        elif ntype == 'variable':
            var_name = data.get('name', node_id.replace('var:', ''))
            cl = _clause_label(var_name)
            safe = _is_safe_variable(var_name, data) or bool(cl)
            # Also mark arrays indexed by private vars as safe
            if not safe and _is_indexed_by_private(var_name, data):
                safe = True
                cl = "shared[i]"
            display_label = f"{var_name}\n{cl}" if cl else var_name
            _add_node(node_id, display_label, ntype,
                      safe=str(safe).lower(),
                      clause_label=cl,
                      var_name=var_name)
            if safe:
                fixes_verified[0] += 1
        elif ntype == 'thread':
            thread_id = data.get('thread_id', node_id.replace('thread:', ''))
            _add_node(node_id, thread_id, ntype)
        elif ntype == 'sync':
            prim = data.get('primitive_type', '')
            sync_label = data.get('sync_id', node_id)
            if prim == 'BARRIER':
                sync_label = 'sync_barrier'
            _add_node(node_id, sync_label, ntype, primitive_type=prim)
        else:
            _add_node(node_id, label, ntype)

    # ── edges from TIG ──
    for u, v, data in tig_graph.edges(data=True):
        rel = data.get('relation', 'related')
        _add_edge(u, v, rel)

    # ── Helper: extract variable name from a finding (dict or dataclass) ──
    def _var_name(f, fallback='?'):
        if isinstance(f, dict):
            v = f.get('variable', fallback)
        else:
            v = getattr(f, 'variable', fallback)
        if v and hasattr(v, 'name'):
            v = v.name
        return v or fallback

    def _file_path(f):
        if isinstance(f, dict):
            return f.get('file') or f.get('file_path', '')
        return getattr(f, 'file_path', '')

    # ── OpenMP race findings ──
    for idx, f in enumerate(findings_dict.get('openmp_races', [])):
        fid = f"finding:omp_race_{idx}"
        var = _var_name(f)
        _add_node(fid, f"Race: {var}", 'finding')
        # Ensure variable node exists
        var_node = f"var:{var}"
        _add_node(var_node, var, 'variable')
        _add_edge(fid, var_node, 'detected_issue')

    # ── Data race findings (IR-based) ──
    for idx, f in enumerate(findings_dict.get('data_races', [])):
        fid = f"finding:data_race_{idx}"
        var = _var_name(f)
        reason = ''
        if isinstance(f, dict):
            reason = f.get('reason', '')
        elif hasattr(f, 'reason'):
            reason = f.reason or ''
        _add_node(fid, f"DataRace: {var}", 'finding')
        var_node = f"var:{var}"
        _add_node(var_node, var, 'variable')
        _add_edge(fid, var_node, 'detected_issue')

    # ── Unprotected access findings ──
    for idx, f in enumerate(findings_dict.get('unprotected_accesses', [])):
        fid = f"finding:unprotected_{idx}"
        # These are ConcurrencyIssue objects
        if hasattr(f, 'accesses') and f.accesses:
            access = f.accesses[0]
            var = access.variable_name
            thread_id = access.thread_id
            line = access.line_number
            construct = access.parallel_construct
            label = f"Unprotected: {var}@L{line}"
        elif isinstance(f, dict):
            var = f.get('variable', '?')
            thread_id = f.get('thread', None)
            line = f.get('line', 0)
            construct = ''
            label = f"Unprotected: {var}"
        else:
            var = str(f)
            thread_id = None
            line = 0
            construct = ''
            label = f"Unprotected: {var}"

        _add_node(fid, label, 'finding')

        # Connect to variable
        var_node = f"var:{var}"
        _add_node(var_node, var, 'variable')
        _add_edge(fid, var_node, 'detected_issue')

        # Connect to thread if known
        if thread_id:
            thread_node = f"thread:{thread_id}"
            _add_node(thread_node, thread_id, 'thread')
            _add_edge(thread_node, fid, 'triggers')

    # ── Unsynchronized access findings (graph-based) ──
    for idx, f in enumerate(findings_dict.get('unsynchronized_accesses', [])):
        fid = f"finding:unsync_{idx}"
        if isinstance(f, dict):
            var = f.get('variable', '?')
        else:
            var = str(f)
        _add_node(fid, f"Unsync: {var}", 'finding')
        var_node = f"var:{var}"
        _add_node(var_node, var, 'variable')
        _add_edge(fid, var_node, 'detected_issue')

    return elements, fixes_verified[0]


# ---------------------------------------------------------------------------
# Sanitiser (for ConcurrencyIssue dataclass objects)
# ---------------------------------------------------------------------------

def _sanitize(obj, depth=0, max_depth=5):
    if depth > max_depth:
        return str(obj) if obj is not None else None
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, list):
        return [_sanitize(v, depth + 1, max_depth) for v in obj]
    if isinstance(obj, dict):
        return {k: _sanitize(v, depth + 1, max_depth) for k, v in obj.items()}
    if isinstance(obj, set):
        return list(obj)
    # Enum
    if hasattr(obj, 'value'):
        return obj.value
    # dataclass
    if hasattr(obj, '__dataclass_fields__'):
        return {k: _sanitize(getattr(obj, k), depth + 1, max_depth) for k in obj.__dataclass_fields__}
    # networkx graph
    try:
        import networkx as nx
        if isinstance(obj, (nx.Graph, nx.DiGraph)):
            return {'_type': 'graph', 'nodes': obj.number_of_nodes(), 'edges': obj.number_of_edges()}
    except Exception:
        pass
    return str(obj)


# ---------------------------------------------------------------------------
# Confidence normalization helper
# ---------------------------------------------------------------------------

def _coerce_confidence(value, default=0.7):
    """Normalize confidence to a float in [0, 1]."""
    if value is None:
        return default
    # Enum or object with a .value
    if hasattr(value, 'value'):
        value = value.value
    # Named levels
    if isinstance(value, str):
        upper = value.strip().upper()
        if upper in ('HIGH', 'H'):
            return 0.9
        if upper in ('MEDIUM', 'M'):
            return 0.6
        if upper in ('LOW', 'L'):
            return 0.3
        if upper in ('UNKNOWN', ''):
            return default
        try:
            value = float(value)
        except Exception:
            return default
    try:
        num = float(value)
    except Exception:
        return default
    if 1.0 < num <= 100.0:
        return num / 100.0
    return max(0.0, min(1.0, num))


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(file_path: str, json_mode: bool = False, use_llm: bool = True, quick: bool = False):
    log = (lambda *a, **kw: print(*a, file=sys.stderr, **kw)) if json_mode else print

    if not os.path.isfile(file_path):
        error = {'error': f'File not found: {file_path}'}
        if json_mode:
            json.dump(error, sys.stdout)
        else:
            print(json.dumps(error, indent=2))
        return

    log(f"[Phase 1] Parsing: {file_path}")
    parser = ParserService()
    parsed = parser.parse_file(file_path)
    if not parsed:
        error = {'error': f'Parser returned empty result for {file_path}'}
        if json_mode:
            json.dump(error, sys.stdout)
        else:
            print(json.dumps(error, indent=2))
        return

    lang = parsed.get('language', '?')
    log(f"  Language: {lang}")
    log(f"  Threads found: {len(parsed.get('threads', []))}")
    log(f"  Locks found:   {len(parsed.get('locks', []))}")
    log(f"  OMP pragmas:   {len(parsed.get('omp_pragmas', []))}")
    log(f"  Shared vars:   {parsed.get('shared_variables', [])}")

    # ── Phase 2: IR normalisation ──
    log("[Phase 2] Normalising to IR ...")
    ir = normalize_to_ir([parsed], repo_path=os.path.dirname(file_path))
    log(f"  IR: {len(ir.all_variables)} variables, {len(ir.all_accesses)} accesses, "
        f"{len(ir.all_threads)} threads, {len(ir.all_synchronization_points)} sync points")

    # ── Phase 3: TIG construction ──
    log("[Phase 3] Building TIG ...")
    tig = build_tig_from_ir(ir)
    log(f"  TIG: {tig.number_of_nodes()} nodes, {tig.number_of_edges()} edges")

    # ── Phase 4: Static analysis (findings) ──
    log("[Phase 4] Running static rules ...")
    findings = run_all_rules(tig, parsed_files=[parsed], ir=ir)

    # If IR-based analysis returned ConcurrencyIssue objects, convert them
    omp_races_raw = findings.get('openmp_races', [])
    omp_count = len(omp_races_raw)

    # Also run the simpler heuristic OpenMP race finder for comparison
    heuristic_findings = find_openmp_races([parsed])
    heuristic_count = len(heuristic_findings.get('findings', []))

    log(f"  IR-based OpenMP races:  {omp_count}")
    log(f"  Heuristic OpenMP races: {heuristic_count}")
    log(f"  Data races (IR):        {len(findings.get('data_races', []))}")
    log(f"  Unprotected accesses:   {len(findings.get('unprotected_accesses', []))}")

    # Merge: prefer IR-based if available, fallback to heuristic
    if omp_count == 0 and heuristic_count > 0:
        log("  -> Using heuristic findings as IR produced none")
        findings['openmp_races'] = heuristic_findings['findings']
        omp_races_raw = findings['openmp_races']
        omp_count = heuristic_count

    # Deduplicate: remove unprotected_accesses for variables already in openmp_races
    if omp_count > 0 and findings.get('unprotected_accesses'):
        omp_race_vars = set()
        for f in omp_races_raw:
            v = _get_access_var(f)
            if v:
                omp_race_vars.add(v)
        if omp_race_vars:
            before = len(findings['unprotected_accesses'])
            findings['unprotected_accesses'] = [
                ua for ua in findings['unprotected_accesses']
                if _get_access_var(ua) not in omp_race_vars
            ]
            deduped = before - len(findings['unprotected_accesses'])
            if deduped:
                log(f"  -> Deduped {deduped} unprotected_accesses (already in openmp_races)")

    # ── Phase 5: Multi-agent validation ──
    # Auto-detect LLM availability: if an API key is configured, use it.
    # Otherwise, fall back to deterministic heuristic analysis.
    agent_results = None
    try:
        from backend.agent_service.orchestrator import MultiAgentOrchestrator

        all_finding_count = (omp_count
                             + len(findings.get('data_races', []))
                             + len(findings.get('unprotected_accesses', [])))

        if all_finding_count > 0 and not quick:
            # LLM orchestrator: only when --llm flag is passed
            llm_orch = None
            if use_llm:
                try:
                    from backend.llm.llm_orchestrator import LLMOrchestrator
                    has_key = bool(
                        os.environ.get('GEMINI_API_KEY')
                        or os.environ.get('GOOGLE_API_KEY')
                        or os.environ.get('OPENROUTER_API_KEY')
                        or os.environ.get('OLLAMA_MODEL')
                    )
                    if has_key:
                        llm_orch = LLMOrchestrator()
                        llm_orch.initialize()
                        log("[Phase 5] Running multi-agent validation (LLM mode) ...")
                    else:
                        log("[Phase 5] Running multi-agent validation (heuristic -- no API key) ...")
                except Exception as llm_err:
                    log(f"[Phase 5] LLM init failed ({llm_err}), using heuristic mode ...")
            else:
                log("[Phase 5] Running multi-agent validation (heuristic mode) ...")

            # Convert ALL findings to dict format for orchestrator
            finding_dicts = []

            # OMP races
            for f in omp_races_raw:
                if isinstance(f, dict):
                    fd = dict(f)
                    if 'confidence' in fd:
                        fd['confidence'] = _coerce_confidence(fd.get('confidence'))
                    finding_dicts.append(fd)
                elif hasattr(f, '__dataclass_fields__'):
                    fd = {
                        'file': getattr(f, 'file_path', file_path),
                        'variable': getattr(f, 'variable', None),
                        'reason': getattr(f, 'reason', 'openmp_shared_write'),
                        'confidence': _coerce_confidence(0.8),
                    }
                    if fd['variable'] and hasattr(fd['variable'], 'name'):
                        fd['variable'] = fd['variable'].name
                    finding_dicts.append(fd)

            # Unprotected accesses (can be ConcurrencyIssue objects or dicts)
            for f in findings.get('unprotected_accesses', []):
                if isinstance(f, dict):
                    accesses = f.get('accesses', [])
                    var_name = f.get('variable', None)
                    line_num = f.get('line', None)
                    if not var_name and accesses:
                        a0 = accesses[0] if isinstance(accesses[0], dict) else {}
                        var_name = a0.get('variable_name', 'unknown')
                        line_num = line_num or a0.get('line_number')
                    fd = {
                        'file': f.get('file', file_path),
                        'variable': var_name or 'unknown',
                        'line': line_num,
                        'reason': f.get('issue_type', f.get('reason', 'unprotected_shared_access')),
                        'confidence': _coerce_confidence(f.get('confidence', 0.7)),
                        'id': f.get('issue_id', f.get('id', '')),
                    }
                    finding_dicts.append(fd)
                else:
                    # ConcurrencyIssue object
                    var_name = getattr(f, 'variable', None)
                    if var_name and hasattr(var_name, 'name'):
                        var_name = var_name.name
                    fd = {
                        'file': getattr(f, 'file_path', file_path),
                        'variable': var_name or 'unknown',
                        'line': getattr(f, 'primary_line', None),
                        'reason': getattr(f, 'issue_type', 'unprotected_shared_access'),
                        'confidence': _coerce_confidence(getattr(f, 'confidence', 0.7)),
                        'id': getattr(f, 'issue_id', ''),
                    }
                    finding_dicts.append(fd)

            # Deduplicate by variable name -- no need to LLM-analyze same var twice
            seen_vars = set()
            unique_findings = []
            for fd in finding_dicts:
                var = fd.get('variable', '')
                if var not in seen_vars:
                    seen_vars.add(var)
                    unique_findings.append(fd)

            # Cap at 5 unique findings to keep LLM time reasonable
            unique_findings = unique_findings[:5]
            log(f"  {len(finding_dicts)} findings -> {len(unique_findings)} unique (by variable)")

            if unique_findings:
                orchestrator = MultiAgentOrchestrator(llm_orchestrator=llm_orch)
                agent_results = orchestrator.run_on_findings(unique_findings, ir=ir, tig=tig)
                mode = "LLM" if llm_orch else "heuristic"
                log(f"  Agent results: {agent_results.get('count', 0)} findings validated ({mode})")
        else:
            log("[Phase 5] Skipped -- no findings to validate")
    except Exception as e:
        log(f"[Phase 5] Agent validation error: {e}")

    # -- Phase 6: LLM-powered fix generation & validation --
    fix_diffs = []
    llm_fix_analysis = []  # LLM root-cause analysis per variable
    try:
        from backend.fix_gen.llm_fix_generator import generate_fixes_with_llm
        from backend.fix_gen.fix_generator import generate_fixes as rule_based_fixes
        from backend.fix_gen.patch_formatter import generate_all_diffs
        from backend.fix_gen.fix_validator import validate_all_fixes

        has_findings = (
            len(findings.get('unprotected_accesses', [])) > 0
            or len(findings.get('openmp_races', [])) > 0
        )

        if has_findings and not quick:
            log("[Phase 6] LLM-powered fix generation ...")

            if use_llm:
                fixes = generate_fixes_with_llm(
                    findings, file_path, language=lang, log_fn=log)
            else:
                log("[Phase 6] LLM disabled, using rule-based fixes")
                fixes = rule_based_fixes(findings, file_path, language=lang)

            log(f"  Generated {len(fixes)} fix suggestions")

            # Extract LLM analysis for sidebar display
            for fix in fixes:
                analysis_data = getattr(fix, '_llm_analysis', None)
                if analysis_data and not llm_fix_analysis:
                    llm_fix_analysis = analysis_data

            if fixes:
                log("[Phase 6b] Validating fixes ...")
                fixes = validate_all_fixes(fixes, file_path, language=lang, max_validate=6)
                validated_count = sum(1 for f in fixes if f.validated)
                removed_count = sum(1 for f in fixes if f.validated and 'removes' in (f.validation_result or ''))
                log(f"  Validated: {validated_count}, Finding removed: {removed_count}")

                # Generate diffs for the top fixes
                fix_diffs = generate_all_diffs(file_path, fixes, context_lines=3)
                log(f"  Diffs generated: {len(fix_diffs)}")
        else:
            log("[Phase 6] Skipped -- no findings to generate fixes for")
    except Exception as e:
        log(f"[Phase 6] Fix generation error: {e}")

    # ── Phase 7: Build Cytoscape elements ──
    log("[Phase 7] Building Cytoscape graph elements ...")
    elements, fixes_verified = build_cytoscape_elements(tig, findings, file_path, ir=ir)
    log(f"  Elements: {len(elements)}, Fixes verified (clause-safe): {fixes_verified}")

    # ── Phase 8: Build Concurrency Knowledge Graph ──
    log("[Phase 8] Building Knowledge Graph ...")
    from backend.kg.concurrency_kg import ConcurrencyKG
    kg = ConcurrencyKG()
    kg.build_from_tig(tig, findings)
    kg_stats = kg.stats()
    log(f"  KG: {kg_stats['nodes']} nodes, {kg_stats['edges']} edges")
    log(f"  KG node types: {kg_stats['node_types']}")

    # Run developer queries on the KG
    var_summaries = kg.all_variable_summaries()
    unguarded = kg.unguarded_writes()
    log(f"  Variable summaries: {len(var_summaries)}")
    log(f"  Unguarded writes: {len(unguarded)}")

    # ── Assemble final result ──
    log("[Output] Assembling JSON ...")

    summary = {
        'file': file_path,
        'language': lang,
        'variables': len(ir.all_variables),
        'threads': len(ir.all_threads),
        'sync_points': len(ir.all_synchronization_points),
        'accesses': len(ir.all_accesses),
        'tig_nodes': tig.number_of_nodes(),
        'tig_edges': tig.number_of_edges(),
        'kg_nodes': kg_stats['nodes'],
        'kg_edges': kg_stats['edges'],
        'openmp_races': omp_count,
        'data_races': len(findings.get('data_races', [])),
        'unprotected_accesses': len(findings.get('unprotected_accesses', [])),
        'fix_suggestions': len(fix_diffs),
        'fixes_verified': fixes_verified,
    }

    result = {
        'file': file_path,
        'elements': elements,
        'findings': _sanitize(findings),
        'fixes': fix_diffs,
        'summary': summary,
        'knowledge_graph': {
            'stats': kg_stats,
            'variable_summaries': var_summaries,
            'unguarded_writes': unguarded,
        },
        'llm_fix_analysis': llm_fix_analysis,
    }

    if agent_results:
        result['agent_results'] = _sanitize(agent_results)

    if json_mode:
        json.dump(result, sys.stdout, default=str)
    else:
        print(json.dumps(result, indent=2, default=str))

    log("Done.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    ap = argparse.ArgumentParser(
        description='Run the full concurrency analysis pipeline on a single source file')
    ap.add_argument('file', help='Path to the C/Python source file to analyse')
    ap.add_argument('--json', dest='json_mode', action='store_true',
                    help='Machine mode: emit only JSON to stdout (diagnostics -> stderr)')
    ap.add_argument('--llm', dest='use_llm', action='store_true',
                    help='Enable LLM-powered validation (slower but deeper analysis)')
    ap.add_argument('--quick', dest='quick', action='store_true',
                    help='Quick mode: skip LLM validation and fix generation (for post-fix re-analysis)')
    cli = ap.parse_args()
    run_pipeline(cli.file, json_mode=cli.json_mode, use_llm=cli.use_llm, quick=cli.quick)
