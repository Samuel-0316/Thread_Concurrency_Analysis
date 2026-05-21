"""Fix generator for concurrency issues.

Generates concrete source-level patches for detected concurrency bugs.
Supports three fix strategies for OpenMP C code:
  1. Wrap access in #pragma omp critical
  2. Add #pragma omp atomic before single-statement accesses
  3. Convert to reduction clause on the enclosing pragma

For Python threading issues:
  1. Wrap access in `with lock:` context manager
"""

import os
import re
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class FixSuggestion:
    """A concrete code fix for a concurrency issue."""
    finding_id: str
    strategy: str           # 'critical', 'atomic', 'reduction', 'with_lock'
    description: str        # Human-readable explanation
    file_path: str
    original_lines: Dict[int, str]   # line_number -> original content
    patched_lines: Dict[int, str]    # line_number -> patched content
    insert_before: Dict[int, str]    # line_number -> text to insert BEFORE that line
    insert_after: Dict[int, str]     # line_number -> text to insert AFTER that line
    confidence: float = 0.0          # 0-1, how confident this fix is correct
    validated: bool = False
    validation_result: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_source(file_path: str) -> List[str]:
    """Read source file as list of lines."""
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        return f.readlines()


def _get_indentation(line: str) -> str:
    """Extract leading whitespace from a line."""
    return line[:len(line) - len(line.lstrip())]


def _is_single_statement(line: str) -> bool:
    """Check if a line is a single C statement (ends with ; and no braces)."""
    stripped = line.strip()
    return stripped.endswith(';') and '{' not in stripped and '}' not in stripped


def _find_pragma_line(lines: List[str], target_line: int) -> Optional[int]:
    """Search backwards from target_line to find the enclosing #pragma omp.

    Args:
        lines: 0-indexed source lines
        target_line: 1-indexed line number to search from

    Returns:
        1-indexed pragma line number, or None
    """
    for i in range(target_line - 2, max(-1, target_line - 30), -1):
        if i < 0:
            break
        if '#pragma omp' in lines[i]:
            return i + 1  # return 1-indexed
    return None


def _detect_reduction_operator(line: str, var_name: str) -> Optional[str]:
    """Detect if the line is a simple reduction on var_name.

    Matches: var += expr;  var = var + expr;  var *= expr;  etc.
    Returns the operator character (+, -, *, etc.) or None.
    """
    stripped = line.strip()
    # Pattern: var += expr;
    m = re.match(rf'\b{re.escape(var_name)}\s*([+\-*/&|^])=\s*.+;', stripped)
    if m:
        return m.group(1)
    # Pattern: var = var + expr;
    m = re.match(rf'\b{re.escape(var_name)}\s*=\s*{re.escape(var_name)}\s*([+\-*/&|^])\s*.+;', stripped)
    if m:
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Strategy 1: #pragma omp critical
# ---------------------------------------------------------------------------

def _generate_critical_fix(finding_id: str, file_path: str, lines: List[str],
                           var_name: str, line_num: int) -> Optional[FixSuggestion]:
    """Wrap the access in a #pragma omp critical section."""
    if line_num < 1 or line_num > len(lines):
        return None

    target_line = lines[line_num - 1]
    indent = _get_indentation(target_line)
    crit_name = re.sub(r'[^a-zA-Z0-9_]', '_', var_name)

    return FixSuggestion(
        finding_id=finding_id,
        strategy='critical',
        description=f"Wrap access to '{var_name}' at line {line_num} in #pragma omp critical",
        file_path=file_path,
        original_lines={line_num: target_line},
        patched_lines={},
        insert_before={line_num: f"{indent}#pragma omp critical(protect_{crit_name})\n{indent}{{\n"},
        insert_after={line_num: f"{indent}}}\n"},
        confidence=0.75,
    )


# ---------------------------------------------------------------------------
# Strategy 2: #pragma omp atomic
# ---------------------------------------------------------------------------

def _generate_atomic_fix(finding_id: str, file_path: str, lines: List[str],
                         var_name: str, line_num: int) -> Optional[FixSuggestion]:
    """Add #pragma omp atomic before a single-statement access."""
    if line_num < 1 or line_num > len(lines):
        return None

    target_line = lines[line_num - 1]
    if not _is_single_statement(target_line):
        return None

    stripped = target_line.strip()
    atomic_type = ''

    # Detect update: var += expr;  var++;  ++var;
    if re.match(rf'\b{re.escape(var_name)}\s*[+\-*/&|^]=', stripped) or \
       re.match(rf'\b{re.escape(var_name)}\s*(\+\+|--)', stripped) or \
       re.match(rf'(\+\+|--)\s*{re.escape(var_name)}', stripped):
        atomic_type = ' update'
    # Detect write: var = expr; (not compound)
    elif re.match(rf'\b{re.escape(var_name)}\s*=\s*[^=]', stripped) and \
         not re.match(rf'\b{re.escape(var_name)}\s*[+\-*/&|^]=', stripped):
        atomic_type = ' write'

    indent = _get_indentation(target_line)

    return FixSuggestion(
        finding_id=finding_id,
        strategy='atomic',
        description=f"Add #pragma omp atomic{atomic_type} before '{var_name}' at line {line_num}",
        file_path=file_path,
        original_lines={line_num: target_line},
        patched_lines={},
        insert_before={line_num: f"{indent}#pragma omp atomic{atomic_type}\n"},
        insert_after={},
        confidence=0.80,
    )


# ---------------------------------------------------------------------------
# Strategy 3: reduction clause
# ---------------------------------------------------------------------------

def _generate_reduction_fix(finding_id: str, file_path: str, lines: List[str],
                            var_name: str, line_num: int) -> Optional[FixSuggestion]:
    """Add a reduction clause to the enclosing #pragma omp parallel for."""
    if line_num < 1 or line_num > len(lines):
        return None

    target_line = lines[line_num - 1]
    op = _detect_reduction_operator(target_line, var_name)
    if not op:
        return None

    pragma_line_num = _find_pragma_line(lines, line_num)
    if not pragma_line_num:
        return None

    pragma_line = lines[pragma_line_num - 1]

    # Already has reduction for this var
    if 'reduction(' in pragma_line and var_name in pragma_line:
        return None

    new_pragma = pragma_line.rstrip('\n')

    # Remove var from shared() clause if present
    shared_match = re.search(r'shared\s*\(([^)]*)\)', new_pragma)
    if shared_match:
        shared_vars = [v.strip() for v in shared_match.group(1).split(',')]
        if var_name in shared_vars:
            shared_vars.remove(var_name)
            if shared_vars:
                replacement = f"shared({', '.join(shared_vars)})"
            else:
                replacement = ''
            new_pragma = new_pragma[:shared_match.start()] + replacement + new_pragma[shared_match.end():]

    new_pragma = new_pragma.rstrip() + f" reduction({op}:{var_name})\n"

    return FixSuggestion(
        finding_id=finding_id,
        strategy='reduction',
        description=f"Add reduction({op}:{var_name}) clause to pragma at line {pragma_line_num}",
        file_path=file_path,
        original_lines={pragma_line_num: pragma_line},
        patched_lines={pragma_line_num: new_pragma},
        insert_before={},
        insert_after={},
        confidence=0.90,
    )


# ---------------------------------------------------------------------------
# Strategy 4: Python lock guard
# ---------------------------------------------------------------------------

def _generate_python_lock_fix(finding_id: str, file_path: str, lines: List[str],
                              var_name: str, line_num: int) -> Optional[FixSuggestion]:
    """Wrap the access in a `with _lock:` block (Python)."""
    if line_num < 1 or line_num > len(lines):
        return None

    target_line = lines[line_num - 1]
    indent = _get_indentation(target_line)
    extra_indent = indent + '    '
    patched_target = extra_indent + target_line.lstrip()

    return FixSuggestion(
        finding_id=finding_id,
        strategy='with_lock',
        description=f"Wrap access to '{var_name}' at line {line_num} in `with _lock:` guard",
        file_path=file_path,
        original_lines={line_num: target_line},
        patched_lines={line_num: patched_target},
        insert_before={line_num: f"{indent}with _lock:  # protect '{var_name}'\n"},
        insert_after={},
        confidence=0.70,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_fixes(findings: Dict[str, Any], file_path: str,
                   language: str = 'c') -> List[FixSuggestion]:
    """Generate fix suggestions for all findings in a file.

    Args:
        findings: Dict from static analysis (keys: unprotected_accesses, openmp_races, etc.)
        file_path: Path to the source file
        language: 'c' or 'python'

    Returns:
        List of FixSuggestion objects, sorted by confidence (best first)
    """
    if not os.path.isfile(file_path):
        return []

    lines = _read_source(file_path)
    fixes: List[FixSuggestion] = []

    if language == 'c':
        fixes.extend(_generate_c_fixes(findings, file_path, lines))
    elif language == 'python':
        fixes.extend(_generate_python_fixes(findings, file_path, lines))

    fixes.sort(key=lambda f: f.confidence, reverse=True)
    return fixes


def _extract_var_and_line(issue, file_path: str):
    """Extract variable name and line number from a finding (dict or dataclass)."""
    if hasattr(issue, 'accesses') and issue.accesses:
        a = issue.accesses[0]
        var = a.variable_name
        line = a.line_number
    elif isinstance(issue, dict):
        var = issue.get('variable', '')
        if hasattr(var, 'name'):
            var = var.name
        line = issue.get('line', 0)
    else:
        var = getattr(issue, 'variable', '')
        if hasattr(var, 'name'):
            var = var.name
        line = getattr(issue, 'line', 0) or getattr(issue, 'primary_line', 0)
    return var, line


def _generate_c_fixes(findings: Dict, file_path: str,
                      lines: List[str]) -> List[FixSuggestion]:
    """Generate C/OpenMP fixes for all findings.

    Strategy:
    - Group findings by their enclosing parallel region
    - For each region, generate ONE combined fix that addresses ALL
      variables at once, rather than individual per-line patches that
      break when applied together
    """
    fixes = []

    # Collect all issues with their variable and line
    all_issues = []
    for idx, issue in enumerate(findings.get('unprotected_accesses', [])):
        fid = f"finding:unprotected_{idx}"
        var, line = _extract_var_and_line(issue, file_path)
        if var and line and 0 < line <= len(lines):
            all_issues.append((fid, var, line))
    for idx, issue in enumerate(findings.get('openmp_races', [])):
        fid = f"finding:omp_race_{idx}"
        var, line = _extract_var_and_line(issue, file_path)
        if var and line and 0 < line <= len(lines):
            all_issues.append((fid, var, line))

    if not all_issues:
        return fixes

    # Group issues by enclosing pragma
    pragma_groups = {}  # pragma_line -> [(fid, var, access_line), ...]
    ungrouped = []
    for fid, var, line in all_issues:
        pragma_line = _find_pragma_line(lines, line)
        if pragma_line:
            pragma_groups.setdefault(pragma_line, []).append((fid, var, line))
        else:
            ungrouped.append((fid, var, line))

    # For each parallel region, generate a combined fix
    for pragma_line, issues in pragma_groups.items():
        combined = _generate_combined_region_fix(
            file_path, lines, pragma_line, issues)
        fixes.extend(combined)

    # For ungrouped issues, fall back to per-line fixes
    for fid, var, line in ungrouped:
        r = _generate_reduction_fix(fid, file_path, lines, var, line)
        if r:
            fixes.append(r)
        a = _generate_atomic_fix(fid, file_path, lines, var, line)
        if a:
            fixes.append(a)
        c = _generate_critical_fix(fid, file_path, lines, var, line)
        if c:
            fixes.append(c)

    return fixes


def _find_loop_body_range(lines: List[str], pragma_line: int) -> Tuple[int, int]:
    """Find the start and end line of the loop body after a pragma.

    Returns (first_body_line, last_body_line) as 1-indexed inclusive.
    """
    # Search forward from pragma to find the for loop and its body
    start = None
    brace_depth = 0
    in_body = False

    for i in range(pragma_line - 1, min(len(lines), pragma_line + 5)):
        stripped = lines[i].strip()
        if 'for' in stripped and '(' in stripped:
            # Found the for statement, body starts after it
            # Check if { is on this line or next
            if '{' in stripped:
                start = i + 1  # 0-indexed
                brace_depth = stripped.count('{') - stripped.count('}')
                in_body = True
                continue
            else:
                # Look for { on next lines
                for j in range(i + 1, min(len(lines), i + 3)):
                    if '{' in lines[j]:
                        start = j + 1  # 0-indexed, line after {
                        brace_depth = lines[j].count('{') - lines[j].count('}')
                        in_body = True
                        break
                if in_body:
                    break
                # No braces = single statement body
                if i + 1 < len(lines):
                    return (i + 2, i + 2)  # 1-indexed
    if not in_body or start is None:
        return (pragma_line + 2, pragma_line + 2)

    # Walk forward to find matching }
    for i in range(start, len(lines)):
        brace_depth += lines[i].count('{') - lines[i].count('}')
        if brace_depth <= 0:
            return (start + 1, i + 1)  # 1-indexed inclusive
    return (start + 1, len(lines))


def _generate_combined_region_fix(file_path: str, lines: List[str],
                                   pragma_line: int,
                                   issues: List[Tuple[str, str, int]]
                                   ) -> List[FixSuggestion]:
    """Generate smart combined fixes for all issues in one parallel region."""
    fixes = []
    pragma_text = lines[pragma_line - 1]

    # Deduplicate by variable
    var_lines = {}  # var -> [lines]
    for fid, var, line in issues:
        var_lines.setdefault(var, []).append(line)

    # Classify each variable
    reductions = {}   # var -> operator
    lastprivates = [] # vars that should be lastprivate
    atomics = {}      # var -> line (single-statement atomic-compatible)
    critical_vars = []  # vars needing critical section

    body_start, body_end = _find_loop_body_range(lines, pragma_line)

    for var, access_lines in var_lines.items():
        first_line = min(access_lines)
        line_text = lines[first_line - 1] if first_line <= len(lines) else ''

        # Check for reduction pattern (++, +=, etc.)
        # Strip trailing C comments for reliable pattern matching
        stripped = re.sub(r'/\*.*?\*/', '', line_text).strip()
        stripped = re.sub(r'//.*$', '', stripped).strip()

        op = _detect_reduction_operator(stripped + '\n', var)
        if op:
            reductions[var] = op
            continue

        # Check for ++ or -- pattern
        if re.match(rf'\b{re.escape(var)}\s*(\+\+|--)', stripped) or \
           re.match(rf'(\+\+|--)\s*{re.escape(var)}', stripped):
            reductions[var] = '+'
            continue

        # Check if it's a simple write (candidate for lastprivate or atomic)
        is_write = bool(re.match(rf'\b{re.escape(var)}\s*=\s*[^=]', stripped))

        if is_write:
            # If it's `var = expr;` pattern, use lastprivate (best for loop-carried writes)
            if re.match(rf'\b{re.escape(var)}\s*=\s*\w+\s*;', stripped):
                lastprivates.append(var)
            elif stripped.endswith(';'):
                atomics[var] = first_line
            else:
                critical_vars.append(var)
        else:
            critical_vars.append(var)

    # ── Fix 1: Pragma clause fix (reduction + lastprivate) ──
    # This is the BEST fix: modify the pragma line to add clauses
    if reductions or lastprivates:
        new_pragma = pragma_text.rstrip('\n').rstrip()

        for var, op in reductions.items():
            if f'reduction(' not in new_pragma or var not in new_pragma:
                new_pragma += f" reduction({op}:{var})"

        for var in lastprivates:
            if 'lastprivate(' not in new_pragma or var not in new_pragma:
                new_pragma += f" lastprivate({var})"

        new_pragma += '\n'

        # Build finding_id from all involved vars
        involved = list(reductions.keys()) + lastprivates
        fid = f"finding:combined_pragma_{pragma_line}"

        desc_parts = []
        for v, op in reductions.items():
            desc_parts.append(f"reduction({op}:{v})")
        for v in lastprivates:
            desc_parts.append(f"lastprivate({v})")

        fixes.append(FixSuggestion(
            finding_id=fid,
            strategy='pragma_clause',
            description=f"Add {', '.join(desc_parts)} to pragma at line {pragma_line}. "
                       f"This is the safest and most efficient fix.",
            file_path=file_path,
            original_lines={pragma_line: pragma_text},
            patched_lines={pragma_line: new_pragma},
            insert_before={},
            insert_after={},
            confidence=0.92,
        ))

    # ── Fix 2: Atomic for remaining simple writes ──
    for var, line_num in atomics.items():
        a = _generate_atomic_fix(
            f"finding:atomic_{var}_{line_num}", file_path, lines, var, line_num)
        if a:
            fixes.append(a)

    # ── Fix 3: Single critical section wrapping entire loop body ──
    # This is the safest fallback: wrap everything in one critical section
    if len(var_lines) > 1:
        indent = _get_indentation(lines[body_start - 1]) if body_start <= len(lines) else '        '

        all_vars_str = ', '.join(sorted(var_lines.keys()))
        fid = f"finding:combined_critical_{pragma_line}"

        fixes.append(FixSuggestion(
            finding_id=fid,
            strategy='critical_block',
            description=f"Wrap entire loop body (lines {body_start}-{body_end}) in "
                       f"#pragma omp critical to protect all shared variables: {all_vars_str}. "
                       f"Simple but may reduce parallelism.",
            file_path=file_path,
            original_lines={body_start: lines[body_start - 1]},
            patched_lines={},
            insert_before={body_start: f"{indent}#pragma omp critical\n{indent}{{\n"},
            insert_after={body_end: f"{indent}}}\n"},
            confidence=0.70,
        ))

    return fixes


def _generate_python_fixes(findings: Dict, file_path: str,
                           lines: List[str]) -> List[FixSuggestion]:
    """Generate Python threading fixes."""
    fixes = []
    for idx, issue in enumerate(findings.get('unprotected_accesses', [])):
        fid = f"finding:unprotected_{idx}"
        var, line = _extract_var_and_line(issue, file_path)
        if not var or not line:
            continue
        lf = _generate_python_lock_fix(fid, file_path, lines, var, line)
        if lf:
            fixes.append(lf)
    return fixes
