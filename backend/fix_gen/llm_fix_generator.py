"""LLM-powered fix generator for concurrency issues.

Leverages a local LLM (Ollama) to:
  1. Analyze the code context around each concurrency finding
  2. Choose the best fix strategy (reduction, lastprivate, critical, atomic)
  3. Generate the actual patched code

Falls back to the rule-based generator if the LLM output is invalid.
"""

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from backend.fix_gen.fix_generator import (
    FixSuggestion,
    _read_source,
    _get_indentation,
    _find_pragma_line,
    _extract_var_and_line,
    generate_fixes as rule_based_generate_fixes,
)


# ---------------------------------------------------------------------------
# LLM interaction
# ---------------------------------------------------------------------------

def _get_ollama_provider():
    """Build an Ollama provider using the same config as the rest of the pipeline."""
    from backend.llm.providers import build_provider
    model = os.getenv('OLLAMA_MODEL', 'qwen2.5-coder:3b')
    return build_provider('ollama', model, temperature=0.2)


def _call_llm(prompt: str, max_tokens: int = 2048) -> Optional[str]:
    """Send a prompt to the LLM and return the response text, or None on failure."""
    try:
        provider = _get_ollama_provider()
        response = provider.generate_content(prompt, {
            'temperature': 0.2,
            'max_output_tokens': max_tokens,
        })
        if response.status == 'success' and response.text:
            return response.text
        print(f"[LLM Error] Status: {response.status}, Error: {getattr(response, 'error', 'Unknown')}")
        return None
    except Exception as e:
        print(f"[LLM Exception] {e}")
        return None


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _build_fix_prompt(source_code: str, findings_desc: str,
                      language: str = 'c') -> str:
    """Build the prompt that asks the LLM to analyze and fix concurrency issues."""

    if language == 'python':
        return f"""You are an expert in parallel programming and Python concurrency.

TASK: Analyze the following PYTHON code for thread safety issues and generate a FIXED version.

SOURCE CODE:
```python
{source_code}
```

DETECTED CONCURRENCY ISSUES:
{findings_desc}

INSTRUCTIONS:
1. For EACH variable flagged, identify where it is accessed concurrently by multiple threads.
2. CHOOSE the fix strategy using these RULES:
   - Use `threading.Lock()` to protect shared variables that are read and written concurrently.
   - If a lock doesn't exist, create a global lock (e.g., `_lock = threading.Lock()`) or a class-level lock.
   - Use the `with _lock:` context manager around the vulnerable accesses.
   - Do NOT use `#pragma omp` or OpenMP syntax. This is Python.

3. Generate the COMPLETE fixed source code.

RESPOND IN EXACTLY THIS JSON FORMAT (no markdown, no explanation outside JSON):
{{
  "analysis": [
    {{
      "variable": "<var_name>",
      "issue_type": "<race_type>",
      "root_cause": "<plain English explanation of why this is a bug>",
      "strategy": "lock",
      "reasoning": "<why this strategy is best>"
    }}
  ],
  "fixed_code": "<the complete fixed source code as a single string>",
  "pragma_line": ""
}}"""

    return f"""You are an expert in parallel programming and OpenMP concurrency.

TASK: Analyze the following {language.upper()} code for thread safety issues and generate a FIXED version.

SOURCE CODE:
```{language}
{source_code}
```

DETECTED CONCURRENCY ISSUES:
{findings_desc}

INSTRUCTIONS:
1. For EACH variable flagged, trace its data flow carefully:
   - Is it READ before being WRITTEN inside the loop? (needs firstprivate)
   - Is it only WRITTEN, and we need the LAST iteration's value after the loop? (needs lastprivate)
   - Is it accumulated with an operator like ++, +=, *=? (needs reduction)
   - Is it only used as scratch inside each iteration? (needs private)

2. CHOOSE the fix strategy using these PRECISE RULES:

   ┌─────────────────────────────────────────────────────────────────────┐
   │ CLAUSE DECISION TABLE                                              │
   ├────────────────────┬────────────────────────────────────────────────┤
   │ reduction(op:var)  │ Variable is ACCUMULATED across iterations     │
   │                    │ using an operator: ++, +=, -=, *=, &=, |=    │
   │                    │ Example: counter++; sum += a[i];              │
   ├────────────────────┼────────────────────────────────────────────────┤
   │ firstprivate(var)  │ Variable is READ inside the loop using its    │
   │                    │ ORIGINAL value from BEFORE the parallel region │
   │                    │ Each thread gets a COPY initialized to the    │
   │                    │ original value.                                │
   │                    │ Example: result[i] = x; (x was set before)   │
   │                    │ WARNING: private(var) would leave it          │
   │                    │ UNINITIALIZED — use firstprivate instead!     │
   ├────────────────────┼────────────────────────────────────────────────┤
   │ lastprivate(var)   │ Variable is WRITTEN inside the loop, and its  │
   │                    │ value from the LAST ITERATION (highest i)     │
   │                    │ must be visible AFTER the parallel region.     │
   │                    │ Example: last_val = i; then printf(last_val)  │
   │                    │ WARNING: firstprivate only copies IN, NOT OUT │
   │                    │ — use lastprivate to copy the final value out!│
   ├────────────────────┼────────────────────────────────────────────────┤
   │ private(var)       │ Variable is used ONLY as scratch inside each  │
   │                    │ iteration. NOT read before first write. NOT   │
   │                    │ needed after the loop. Value is UNINITIALIZED.│
   │                    │ Example: int temp = a[i]*2; b[i] = temp;     │
   │                    │ DANGER: If the variable IS read before being  │
   │                    │ written, private gives GARBAGE — use          │
   │                    │ firstprivate instead!                          │
   ├────────────────────┼────────────────────────────────────────────────┤
   │ shared(var)        │ Array indexed by loop counter [i] — safe     │
   │                    │ because each thread writes to a UNIQUE index. │
   │                    │ No clause needed (shared by default).          │
   │                    │ Example: result[i] = val;                     │
   ├────────────────────┼────────────────────────────────────────────────┤
   │ #pragma omp atomic │ Single atomic read-modify-write on a shared  │
   │                    │ variable. Alternative to reduction.            │
   │                    │ Example: #pragma omp atomic \\n counter++;     │
   ├────────────────────┼────────────────────────────────────────────────┤
   │ #pragma omp critical│ Multi-statement block that must be exclusive.│
   │                    │ Use only when no clause fits.                  │
   └────────────────────┴────────────────────────────────────────────────┘

3. VERIFICATION CHECKLIST (apply to each variable before choosing):
   □ If variable is read BEFORE being written in the loop body → NOT private, use firstprivate
   □ If variable is used in printf() AFTER the loop → needs lastprivate (not firstprivate)
   □ If variable uses ++, +=, *= → use reduction, not atomic or critical
   □ If variable is an array indexed by [i] → leave as shared (default)

4. Generate the COMPLETE fixed source code.

RESPOND IN EXACTLY THIS JSON FORMAT (no markdown, no explanation outside JSON):
{{
  "analysis": [
    {{
      "variable": "<var_name>",
      "issue_type": "<race_type>",
      "root_cause": "<plain English explanation of why this is a bug>",
      "strategy": "<chosen_strategy>",
      "reasoning": "<why this strategy is best, referencing the decision table>"
    }}
  ],
  "fixed_code": "<the complete fixed source code as a single string>",
  "pragma_line": "<the modified #pragma omp line, if the fix modifies it>"
}}"""


def _build_findings_description(findings: Dict, lines: List[str], language: str = 'c') -> str:
    """Build a human-readable description of all findings for the prompt.

    Includes data flow context (read-before-write, post-loop usage,
    accumulation patterns) to help the LLM choose the right clause.
    """
    parts = []
    idx = 0
    seen_vars = set()

    # Pre-analyze the source for data flow hints
    source = ''.join(lines)
    # Find the parallel region and post-region code
    pragma_line_idx = None
    for i, line in enumerate(lines):
        if '#pragma omp' in line and ('parallel' in line or 'for' in line):
            pragma_line_idx = i
            break

    def _analyze_var_usage(var_name):
        """Analyze how a variable is used in the loop and after it."""
        hints = []
        if language != 'c':
            return hints
            
        # Check for accumulation patterns
        import re as _re
        accum_pat = _re.compile(r'\b' + _re.escape(var_name) + r'\s*(\+\+|--|\+=|-=|\*=|/=|&=|\|=|\^=)')
        if accum_pat.search(source):
            hints.append(f"ACCUMULATION: `{var_name}` uses ++/+=/etc (suggests reduction)")

        # Check if variable is used after the parallel region (printf, return, etc.)
        if pragma_line_idx is not None:
            # Find end of parallel region (crude: find closing brace)
            brace_depth = 0
            end_idx = len(lines)
            for i in range(pragma_line_idx, len(lines)):
                brace_depth += lines[i].count('{') - lines[i].count('}')
                if brace_depth <= 0 and i > pragma_line_idx + 1:
                    end_idx = i
                    break
            post_region = ''.join(lines[end_idx:])
            if _re.search(r'\b' + _re.escape(var_name) + r'\b', post_region):
                hints.append(f"POST-LOOP USAGE: `{var_name}` is used AFTER the parallel region (suggests lastprivate or reduction)")

        # Check if variable is read before written in the loop body
        if pragma_line_idx is not None:
            loop_lines = lines[pragma_line_idx:]
            first_read = first_write = None
            for i, line in enumerate(loop_lines):
                stripped = line.strip()
                if _re.search(r'\b' + _re.escape(var_name) + r'\b', stripped):
                    # Is this a write (assignment LHS)?
                    is_write = bool(_re.match(r'.*\b' + _re.escape(var_name) + r'\s*(\+\+|--|=|\+=|-=|\*=)', stripped))
                    # Is this a read (appears on RHS or in expression)?
                    is_read_rhs = bool(_re.search(r'=\s*.*\b' + _re.escape(var_name) + r'\b', stripped))
                    is_read_arg = bool(_re.search(r'\(\s*.*\b' + _re.escape(var_name) + r'\b', stripped))
                    is_read = is_read_rhs or is_read_arg
                    if is_read and first_read is None:
                        first_read = i
                    if is_write and first_write is None:
                        first_write = i
            if first_read is not None and (first_write is None or first_read <= first_write):
                hints.append(f"READ-BEFORE-WRITE: `{var_name}` is READ before being WRITTEN in the loop (needs firstprivate, NOT private)")

        return hints

    all_issues = []
    for issue in findings.get('unprotected_accesses', []):
        var, line = _extract_var_and_line(issue, '')
        if var and var not in seen_vars:
            all_issues.append((var, line, 'unprotected access'))
            seen_vars.add(var)
    for issue in findings.get('openmp_races', []):
        var, line = _extract_var_and_line(issue, '')
        if var and var not in seen_vars:
            all_issues.append((var, line, 'OpenMP race condition'))
            seen_vars.add(var)

    for var, line, issue_type in all_issues:
        if line and 0 < line <= len(lines):
            code = lines[line - 1].strip()
            parts.append(f"  {idx+1}. Variable `{var}` at line {line}: {issue_type}")
            parts.append(f"     Code: {code}")
            # Add data flow hints
            hints = _analyze_var_usage(var)
            for h in hints:
                parts.append(f"     → {h}")
            idx += 1

    return '\n'.join(parts) if parts else 'No specific findings.'


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _extract_json_from_response(text: str) -> Optional[Dict]:
    """Extract JSON from LLM response, handling markdown fences and extra text."""
    # Try direct parse first
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code fences
    json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try finding the first { ... } block
    brace_start = text.find('{')
    if brace_start >= 0:
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[brace_start:i+1])
                    except json.JSONDecodeError:
                        break

    return None


def _validate_fixed_code(original: str, fixed: str, language: str = 'c') -> bool:
    """Basic validation that the fixed code is syntactically plausible."""
    if not fixed or len(fixed.strip()) < 20:
        return False

    # Must still contain key structural elements
    if language == 'c':
        if 'main' not in fixed and 'void' not in fixed and '#include' not in fixed:
            return False
        # Must have balanced braces
        if fixed.count('{') != fixed.count('}'):
            return False
        # Must have a #pragma omp somewhere
        if '#pragma omp' not in fixed:
            return False
    elif language == 'python':
        # Must contain some Python structure
        if 'def ' not in fixed and 'import ' not in fixed and 'class ' not in fixed:
            return False
        # Must NOT contain C-specific constructs (LLM hallucination guard)
        if '#pragma omp' in fixed or '#include' in fixed:
            return False

    return True


# ---------------------------------------------------------------------------
# Fix conversion: LLM output -> FixSuggestion
# ---------------------------------------------------------------------------

def _fix_reduction_operator(pragma_text: str, source_code: str) -> str:
    """Fix reduction clauses missing the operator (e.g. reduction(var) → reduction(+:var)).

    LLMs sometimes omit the operator in reduction clauses. This function
    infers the correct operator from usage patterns in the source code.
    """
    import re as _re

    def _infer_op(var_name):
        """Infer the reduction operator from source patterns."""
        if _re.search(r'\b' + _re.escape(var_name) + r'\s*(\+\+|\+=)', source_code):
            return '+'
        if _re.search(r'\b' + _re.escape(var_name) + r'\s*(-=|--)', source_code):
            return '-'
        if _re.search(r'\b' + _re.escape(var_name) + r'\s*\*=', source_code):
            return '*'
        if _re.search(r'\b' + _re.escape(var_name) + r'\s*&=', source_code):
            return '&'
        if _re.search(r'\b' + _re.escape(var_name) + r'\s*\|=', source_code):
            return '|'
        if _re.search(r'\b' + _re.escape(var_name) + r'\s*\^=', source_code):
            return '^'
        return '+'  # default to + if can't infer

    # Match reduction(var) without an operator — i.e. no colon inside
    # Valid: reduction(+:var)  Invalid: reduction(var)
    pattern = _re.compile(r'reduction\(([^:)]+)\)')
    for m in pattern.finditer(pragma_text):
        inner = m.group(1).strip()
        # If inner doesn't contain ':', it's missing the operator
        if ':' not in inner:
            # Could be comma-separated vars
            vars_list = [v.strip() for v in inner.split(',')]
            op = _infer_op(vars_list[0])
            fixed = f"reduction({op}:{inner})"
            pragma_text = pragma_text[:m.start()] + fixed + pragma_text[m.end():]

    return pragma_text


def _llm_result_to_fix_suggestions(llm_result: Dict, file_path: str,
                                    original_lines: List[str], language: str = 'c') -> List[FixSuggestion]:
    """Convert parsed LLM JSON output into FixSuggestion objects."""
    fixes = []
    source_code = ''.join(original_lines)

    analysis = llm_result.get('analysis', [])
    fixed_code = llm_result.get('fixed_code', '')
    pragma_line_text = llm_result.get('pragma_line', '')

    # Post-process: fix reduction clauses missing the operator
    if pragma_line_text:
        pragma_line_text = _fix_reduction_operator(pragma_line_text, source_code)
    if fixed_code:
        fixed_code = _fix_reduction_operator(fixed_code, source_code)

    # Strategy 1: If LLM provided a modified pragma line, create a pragma-level fix
    if pragma_line_text and pragma_line_text.strip().startswith('#pragma'):
        # Find the original pragma line
        for i, line in enumerate(original_lines):
            if '#pragma omp' in line and ('parallel' in line or 'for' in line):
                pragma_line_num = i + 1
                original_pragma = line

                # Build description from analysis
                desc_parts = []
                for a in analysis:
                    strategy = a.get('strategy', '')
                    var = a.get('variable', '')
                    reason = a.get('reasoning', '')
                    if strategy and var:
                        desc_parts.append(f"{strategy} for `{var}` ({reason})")

                new_pragma = pragma_line_text.rstrip() + '\n'

                # Preserve original indentation
                indent = _get_indentation(original_pragma)
                if not new_pragma.startswith(indent):
                    new_pragma = indent + new_pragma.lstrip()

                fixes.append(FixSuggestion(
                    finding_id=f"finding:llm_pragma_{pragma_line_num}",
                    strategy='llm_pragma_clause',
                    description=(
                        "LLM-recommended pragma fix: " +
                        '; '.join(desc_parts) if desc_parts
                        else f"LLM-generated pragma modification at line {pragma_line_num}"
                    ),
                    file_path=file_path,
                    original_lines={pragma_line_num: original_pragma},
                    patched_lines={pragma_line_num: new_pragma},
                    insert_before={},
                    insert_after={},
                    confidence=0.90,
                ))
                break

    # Strategy 2: If LLM provided complete fixed code, create a full-file fix
    if fixed_code and _validate_fixed_code(''.join(original_lines), fixed_code, language):
        fixed_lines = fixed_code.split('\n')

        # Find which lines actually changed
        patched_lines = {}
        insert_before = {}
        insert_after = {}

        if language == 'c':
            # C approach: find the pragma line and replace it
            for i, orig_line in enumerate(original_lines):
                line_num = i + 1
                if '#pragma omp' in orig_line and ('parallel' in orig_line or 'for' in orig_line):
                    # Find the corresponding pragma in the fixed code
                    for fixed_line in fixed_lines:
                        if '#pragma omp' in fixed_line and fixed_line.strip() != orig_line.strip():
                            patched_lines[line_num] = _get_indentation(orig_line) + fixed_line.strip() + '\n'
                            break

        # For all languages: always include the full-file replacement fix
        # Build root cause descriptions from LLM analysis
        root_causes = []
        for a in analysis:
            rc = a.get('root_cause', '')
            if rc:
                root_causes.append(f"- {a.get('variable', '?')}: {rc}")

        if patched_lines:
            # C-style combined fix (pragma-level changes)
            fixes.append(FixSuggestion(
                finding_id="finding:llm_full_fix",
                strategy='llm_combined',
                description=(
                    "LLM-analyzed fix addressing all concurrency issues.\n" +
                    "Root causes identified by LLM:\n" +
                    '\n'.join(root_causes)
                ),
                file_path=file_path,
                original_lines={ln: original_lines[ln-1] for ln in patched_lines},
                patched_lines=patched_lines,
                insert_before=insert_before,
                insert_after=insert_after,
                confidence=0.88,
            ))

        # Always include the full-file fix as a separate option for the UI
        full_fix = FixSuggestion(
            finding_id="finding:llm_full_file",
            strategy='llm_full_file',
            description=(
                "LLM full-file fix (replace entire file).\n" +
                ("Root causes identified by LLM:\n" + '\n'.join(root_causes) if root_causes else "")
            ),
            file_path=file_path,
            original_lines={},
            patched_lines={},
            insert_before={},
            insert_after={},
            confidence=0.92,
        )
        full_fix._full_file_content = fixed_code
        fixes.append(full_fix)

    # Add individual analysis results as metadata on each fix
    for fix in fixes:
        fix._llm_analysis = analysis  # Attach for sidebar display

    return fixes


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_fixes_with_llm(findings: Dict[str, Any], file_path: str,
                             language: str = 'c',
                             log_fn=None) -> List[FixSuggestion]:
    """Generate fix suggestions using LLM analysis + rule-based fallback.

    Pipeline:
      1. Read source code and build prompt with findings context
      2. Ask LLM to analyze root causes and generate fix
      3. Parse and validate LLM response
      4. Convert to FixSuggestion objects
      5. If LLM fails, fall back to rule-based generator

    Args:
        findings: Dict from static analysis
        file_path: Path to the source file
        language: 'c' or 'python'
        log_fn: Optional logging function

    Returns:
        List of FixSuggestion objects
    """
    log = log_fn or (lambda *a, **kw: None)

    if not os.path.isfile(file_path):
        return []

    lines = _read_source(file_path)
    source_code = ''.join(lines)

    # Build the prompt
    findings_desc = _build_findings_description(findings, lines, language)
    prompt = _build_fix_prompt(source_code, findings_desc, language)

    log("[Phase 6a] Sending code to LLM for fix analysis...")

    # Call LLM
    response_text = _call_llm(prompt, max_tokens=3000)

    if not response_text:
        log("[Phase 6a] LLM returned no response, falling back to rule-based fixes")
        return rule_based_generate_fixes(findings, file_path, language)

    # Parse JSON response
    llm_result = _extract_json_from_response(response_text)

    if not llm_result:
        log("[Phase 6a] Could not parse LLM JSON response, falling back to rule-based fixes")
        log(f"  Raw response (first 200 chars): {response_text[:200]}")
        return rule_based_generate_fixes(findings, file_path, language)

    log(f"[Phase 6a] LLM analysis received: {len(llm_result.get('analysis', []))} issues analyzed")

    # Convert LLM output to FixSuggestion objects
    llm_fixes = _llm_result_to_fix_suggestions(llm_result, file_path, lines, language)

    if not llm_fixes:
        log("[Phase 6a] LLM produced no valid fixes, falling back to rule-based")
        return rule_based_generate_fixes(findings, file_path, language)

    log(f"[Phase 6a] LLM generated {len(llm_fixes)} fix suggestions")

    # Also get rule-based fixes as fallback options
    rule_fixes = rule_based_generate_fixes(findings, file_path, language)

    # Combine: LLM fixes first (higher confidence), then rule-based
    all_fixes = llm_fixes + rule_fixes

    # Attach LLM analysis to all fixes for sidebar display
    llm_analysis = llm_result.get('analysis', [])
    for fix in all_fixes:
        if not hasattr(fix, '_llm_analysis'):
            fix._llm_analysis = llm_analysis

    # Sort by confidence
    all_fixes.sort(key=lambda f: f.confidence, reverse=True)

    return all_fixes
