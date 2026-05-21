"""Loop-carried dependence detection for parallel for loops.

Detects when iteration `i` of a parallel for loop writes to a location
that iteration `j` reads or writes, which would cause a loop-carried
dependence and a potential data race under OpenMP parallelisation.
"""

import re
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class LoopAccess:
    """An array/variable access inside a loop body."""
    variable: str        # Array or variable name
    index_expr: str      # Index expression as string (e.g., 'i', 'i+1', 'index[i]')
    access_type: str     # 'read' or 'write'
    line: int
    uses_induction: bool  # Does the index depend on the loop variable?
    is_indirect: bool     # Is it an indirect access (e.g., arr[index[i]])?


@dataclass
class LoopDependence:
    """A detected loop-carried dependence."""
    variable: str
    write_line: int
    read_line: int
    write_index: str
    read_index: str
    dependence_type: str  # 'flow' (write→read), 'anti' (read→write), 'output' (write→write)
    is_definitely_racy: bool  # True if indices provably overlap
    reason: str


def analyze_loop_accesses(parsed_file: Dict) -> List[Dict[str, Any]]:
    """Extract loop access patterns from a parsed C file.

    Looks for #pragma omp parallel for loops and extracts array access
    patterns from their bodies.

    Args:
        parsed_file: Parsed file dict from ParserService

    Returns:
        List of loop info dicts, each with 'pragma_line', 'induction_var',
        'accesses' (list of LoopAccess)
    """
    source = parsed_file.get('source', '') or parsed_file.get('content', '')
    if not source:
        return []

    lines = source.split('\n')
    loops = []

    # Find #pragma omp parallel for directives
    pragma_pattern = re.compile(r'#pragma\s+omp\s+parallel\s+for')
    for_pattern = re.compile(r'for\s*\(\s*(?:int\s+)?(\w+)\s*=')

    for i, line in enumerate(lines):
        if not pragma_pattern.search(line):
            continue

        # Find the for loop on the next non-empty line(s)
        induction_var = None
        for j in range(i + 1, min(i + 5, len(lines))):
            m = for_pattern.search(lines[j])
            if m:
                induction_var = m.group(1)
                break

        if not induction_var:
            continue

        # Extract the loop body (find matching braces)
        body_start = None
        brace_count = 0
        body_lines = []

        for j in range(i + 1, min(i + 100, len(lines))):
            if '{' in lines[j]:
                if body_start is None:
                    body_start = j
                brace_count += lines[j].count('{') - lines[j].count('}')
            elif body_start is not None:
                brace_count += lines[j].count('{') - lines[j].count('}')

            if body_start is not None:
                body_lines.append((j + 1, lines[j]))  # (1-indexed line, content)

            if body_start is not None and brace_count <= 0:
                break

        # Analyze accesses in the loop body
        accesses = _extract_array_accesses(body_lines, induction_var)

        loops.append({
            'pragma_line': i + 1,
            'induction_var': induction_var,
            'body_start': (body_start or i) + 1,
            'accesses': accesses,
        })

    return loops


def _extract_array_accesses(body_lines: List[Tuple[int, str]],
                            induction_var: str) -> List[LoopAccess]:
    """Extract array access patterns from loop body lines."""
    accesses = []

    # Pattern for array access: var[expr]
    arr_pattern = re.compile(r'(\w+)\s*\[([^\]]+)\]')
    # Pattern for writes: var[expr] = ... or var[expr] += ...
    write_pattern = re.compile(r'(\w+)\s*\[([^\]]+)\]\s*[+\-*/&|^]?=')

    for line_num, line_content in body_lines:
        stripped = line_content.strip()
        if not stripped or stripped.startswith('//') or stripped.startswith('{') or stripped.startswith('}'):
            continue

        # Find all array accesses in this line
        write_matches = set()
        for m in write_pattern.finditer(stripped):
            var_name = m.group(1)
            index_expr = m.group(2).strip()
            write_matches.add((var_name, index_expr))

            uses_ind = induction_var in index_expr
            is_indirect = '[' in index_expr or any(
                c.isalpha() and c != induction_var[0] for c in index_expr
                if not index_expr.replace(induction_var, '').replace(' ', '').replace('+', '').replace('-', '').replace('*', '').lstrip('0123456789')
            )

            accesses.append(LoopAccess(
                variable=var_name,
                index_expr=index_expr,
                access_type='write',
                line=line_num,
                uses_induction=uses_ind,
                is_indirect=bool(re.search(r'\w+\[', index_expr)),
            ))

        # Find reads (array accesses that aren't writes)
        for m in arr_pattern.finditer(stripped):
            var_name = m.group(1)
            index_expr = m.group(2).strip()
            if (var_name, index_expr) not in write_matches:
                uses_ind = induction_var in index_expr
                accesses.append(LoopAccess(
                    variable=var_name,
                    index_expr=index_expr,
                    access_type='read',
                    line=line_num,
                    uses_induction=uses_ind,
                    is_indirect=bool(re.search(r'\w+\[', index_expr)),
                ))

    return accesses


def detect_loop_dependences(loops: List[Dict]) -> List[LoopDependence]:
    """Detect loop-carried dependences from extracted loop access patterns.

    Rules:
      1. If two iterations write to the same array with the same index → output dependence
      2. If one iteration writes and another reads the same array+index → flow/anti dependence
      3. Indirect accesses (arr[index[i]]) are flagged as potentially racy
    """
    dependences = []

    for loop in loops:
        accesses = loop.get('accesses', [])
        induction = loop.get('induction_var', 'i')

        writes = [a for a in accesses if a.access_type == 'write']
        reads = [a for a in accesses if a.access_type == 'read']

        for w in writes:
            # Check for indirect access (always potentially racy)
            if w.is_indirect:
                dependences.append(LoopDependence(
                    variable=w.variable,
                    write_line=w.line,
                    read_line=w.line,
                    write_index=w.index_expr,
                    read_index=w.index_expr,
                    dependence_type='output',
                    is_definitely_racy=False,
                    reason=f"Indirect write {w.variable}[{w.index_expr}]: "
                           f"two iterations may map to same index",
                ))

            # Write-write (output dependence)
            for w2 in writes:
                if w is w2:
                    continue
                if w.variable != w2.variable:
                    continue
                if _indices_may_overlap(w.index_expr, w2.index_expr, induction):
                    dependences.append(LoopDependence(
                        variable=w.variable,
                        write_line=w.line,
                        read_line=w2.line,
                        write_index=w.index_expr,
                        read_index=w2.index_expr,
                        dependence_type='output',
                        is_definitely_racy=w.index_expr == w2.index_expr and not w.uses_induction,
                        reason=f"Write-write to {w.variable}: "
                               f"[{w.index_expr}] at L{w.line} vs [{w2.index_expr}] at L{w2.line}",
                    ))

            # Write-read (flow dependence)
            for r in reads:
                if w.variable != r.variable:
                    continue
                if _indices_may_overlap(w.index_expr, r.index_expr, induction):
                    dependences.append(LoopDependence(
                        variable=w.variable,
                        write_line=w.line,
                        read_line=r.line,
                        write_index=w.index_expr,
                        read_index=r.index_expr,
                        dependence_type='flow' if w.line <= r.line else 'anti',
                        is_definitely_racy=w.index_expr != f"{induction}" or r.index_expr != f"{induction}",
                        reason=f"Write-read on {w.variable}: "
                               f"write [{w.index_expr}] at L{w.line}, read [{r.index_expr}] at L{r.line}",
                    ))

    return dependences


def _indices_may_overlap(idx1: str, idx2: str, induction: str) -> bool:
    """Check if two index expressions might refer to the same element
    across different iterations.

    Simple heuristic:
      - Same expression using induction var (e.g. both are 'i') → no overlap
        (different iterations access different elements)
      - Different expressions (e.g. 'i' vs 'i+1') → may overlap
      - Neither uses induction → definite overlap
      - Indirect (contains nested []) → may overlap
    """
    idx1 = idx1.strip()
    idx2 = idx2.strip()

    # Both are just the induction variable → no overlap across iterations
    if idx1 == induction and idx2 == induction:
        return False

    # Either is indirect → conservative: may overlap
    if '[' in idx1 or '[' in idx2:
        return True

    # Neither uses induction → same element every iteration → overlap
    if induction not in idx1 and induction not in idx2:
        return True

    # Different expressions involving induction (e.g. i vs i+1) → may overlap
    if idx1 != idx2:
        return True

    return False


def loop_analysis_summary(parsed_files: List[Dict]) -> Dict[str, Any]:
    """Run loop analysis on parsed files and return a summary."""
    all_loops = []
    all_deps = []

    for pf in parsed_files:
        loops = analyze_loop_accesses(pf)
        deps = detect_loop_dependences(loops)
        all_loops.extend(loops)
        all_deps.extend(deps)

    return {
        'parallel_loops_found': len(all_loops),
        'total_array_accesses': sum(len(l['accesses']) for l in all_loops),
        'dependences_detected': len(all_deps),
        'definitely_racy': sum(1 for d in all_deps if d.is_definitely_racy),
        'indirect_accesses': sum(
            1 for l in all_loops for a in l['accesses'] if a.is_indirect
        ),
        'dependences': [
            {
                'variable': d.variable,
                'type': d.dependence_type,
                'write_line': d.write_line,
                'read_line': d.read_line,
                'reason': d.reason,
                'is_definitely_racy': d.is_definitely_racy,
            }
            for d in all_deps[:20]  # Limit output
        ],
    }
