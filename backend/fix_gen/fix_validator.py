"""Fix validator -- applies a fix, re-runs static analysis, checks if the finding disappears.

Closes the feedback loop: ensures generated fixes actually resolve the issue.
"""

import os
import re
import tempfile
import shutil
from typing import Any, Dict, List, Optional

from backend.fix_gen.fix_generator import FixSuggestion
from backend.fix_gen.patch_formatter import apply_fix_to_source


def validate_fix(fix: FixSuggestion, original_lines: List[str],
                 file_path: str, language: str = 'c') -> Dict[str, Any]:
    """Validate a fix by re-running analysis on the patched code.

    Steps:
      1. Apply fix to source lines
      2. Write patched file to a temp directory
      3. Re-run parser + IR + static analysis
      4. Check if the specific finding disappeared

    Returns dict with: valid, finding_removed, new_issues_count, details
    """
    patched_lines = apply_fix_to_source(original_lines, fix)

    basename = os.path.basename(file_path)
    temp_dir = tempfile.mkdtemp(prefix='concurrency_fix_')
    temp_file = os.path.join(temp_dir, basename)

    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            f.writelines(patched_lines)

        # Re-run the pipeline on the patched file
        from backend.parser_service.parser import ParserService
        from backend.ir.ir_normalizer_v2 import normalize_to_ir
        from backend.tig.tig_builder import build_tig_from_ir
        from backend.static_analysis.static_rules import run_all_rules

        parser = ParserService()
        parsed = parser.parse_file(temp_file)
        if not parsed:
            return {
                'valid': False,
                'finding_removed': False,
                'new_issues_count': -1,
                'details': 'Parser failed on patched file',
            }

        ir = normalize_to_ir([parsed], repo_path=temp_dir)
        tig = build_tig_from_ir(ir)
        new_findings = run_all_rules(tig, parsed_files=[parsed], ir=ir)

        new_total = (
            len(new_findings.get('unprotected_accesses', []))
            + len(new_findings.get('openmp_races', []))
            + len(new_findings.get('data_races', []))
        )

        # Extract the variable name targeted by this fix
        fix_var = None
        var_match = re.search(r"'(\w+)'", fix.description)
        if var_match:
            fix_var = var_match.group(1)

        finding_still_present = False
        if fix_var:
            for issue in new_findings.get('unprotected_accesses', []):
                if hasattr(issue, 'accesses') and issue.accesses:
                    for a in issue.accesses:
                        if a.variable_name == fix_var:
                            finding_still_present = True
                            break

            for issue in new_findings.get('openmp_races', []):
                v = issue.get('variable', '') if isinstance(issue, dict) else getattr(issue, 'variable', '')
                if hasattr(v, 'name'):
                    v = v.name
                if v == fix_var:
                    finding_still_present = True

        finding_removed = not finding_still_present

        return {
            'valid': True,
            'finding_removed': finding_removed,
            'new_issues_count': new_total,
            'details': 'Fix removes the target finding' if finding_removed
                       else f'Finding for {fix_var} still present after fix',
        }

    except Exception as e:
        return {
            'valid': False,
            'finding_removed': False,
            'new_issues_count': -1,
            'details': f'Validation error: {e}',
        }
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


def validate_all_fixes(fixes: List[FixSuggestion], file_path: str,
                       language: str = 'c', max_validate: int = 10) -> List[FixSuggestion]:
    """Validate multiple fixes, updating their validation status in-place.

    Only validates the top `max_validate` fixes (by confidence).
    Returns the same list with updated validated / validation_result fields.
    """
    if not os.path.isfile(file_path):
        return fixes

    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        original_lines = f.readlines()

    for fix in fixes[:max_validate]:
        result = validate_fix(fix, original_lines, file_path, language)
        fix.validated = True
        fix.validation_result = result.get('details', '')
        if result.get('finding_removed'):
            fix.confidence = min(1.0, fix.confidence + 0.10)
        else:
            fix.confidence = max(0.0, fix.confidence - 0.20)

    return fixes
