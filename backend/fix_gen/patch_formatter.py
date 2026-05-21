"""Patch formatter -- converts FixSuggestions into unified diff format.

Generates both:
  - Unified diff strings (for display in the webview)
  - Patched file content (for validation)
"""

import os
import difflib
from typing import Dict, List, Optional

from backend.fix_gen.fix_generator import FixSuggestion


def apply_fix_to_source(lines: List[str], fix: FixSuggestion) -> List[str]:
    """Apply a single FixSuggestion to a list of source lines.

    Returns a new list of lines with the fix applied.
    Lines are 1-indexed in the fix, but 0-indexed in the list.
    """
    result = []
    for i, line in enumerate(lines):
        line_num = i + 1  # convert to 1-indexed

        # Insert before this line
        if line_num in fix.insert_before:
            result.append(fix.insert_before[line_num])

        # Replace or keep the line
        if line_num in fix.patched_lines:
            result.append(fix.patched_lines[line_num])
        else:
            result.append(line)

        # Insert after this line
        if line_num in fix.insert_after:
            result.append(fix.insert_after[line_num])

    return result


def generate_unified_diff(file_path: str, original_lines: List[str],
                          fix: FixSuggestion, context_lines: int = 3) -> str:
    """Generate a unified diff string for a FixSuggestion."""
    patched_lines = apply_fix_to_source(original_lines, fix)
    basename = os.path.basename(file_path)
    diff = difflib.unified_diff(
        original_lines,
        patched_lines,
        fromfile=f"a/{basename}",
        tofile=f"b/{basename}",
        n=context_lines,
    )
    return ''.join(diff)


def generate_all_diffs(file_path: str, fixes: List[FixSuggestion],
                       context_lines: int = 3) -> List[Dict]:
    """Generate diffs for all fix suggestions.

    Returns list of dicts with: finding_id, strategy, description,
    confidence, validated, diff
    """
    if not os.path.isfile(file_path):
        return []

    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        original_lines = f.readlines()

    results = []
    for fix in fixes:
        diff_text = generate_unified_diff(file_path, original_lines, fix, context_lines)
        results.append({
            'finding_id': fix.finding_id,
            'strategy': fix.strategy,
            'description': fix.description,
            'confidence': fix.confidence,
            'validated': fix.validated,
            'validation_result': fix.validation_result or '',
            'diff': diff_text,
            # Raw patch data needed by the extension to apply the fix:
            'file_path': fix.file_path,
            'original_lines': fix.original_lines,
            'patched_lines': fix.patched_lines,
            'insert_before': fix.insert_before,
            'insert_after': fix.insert_after,
        })
        if hasattr(fix, '_full_file_content'):
            results[-1]['full_file_content'] = fix._full_file_content
    return results


def write_patched_file(file_path: str, fix: FixSuggestion,
                       output_path: Optional[str] = None) -> str:
    """Apply a fix and write the patched file to disk.

    Returns path to the patched file.
    """
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        original_lines = f.readlines()

    patched_lines = apply_fix_to_source(original_lines, fix)

    if output_path is None:
        base, ext = os.path.splitext(file_path)
        output_path = f"{base}.patched{ext}"

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.writelines(patched_lines)

    return output_path
