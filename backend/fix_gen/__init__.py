"""Fix generation package — Phase 6 of the concurrency analysis pipeline.

Provides automated code fix generation for detected concurrency issues,
unified diff output, and fix validation by re-running static analysis.
"""

from backend.fix_gen.fix_generator import FixSuggestion, generate_fixes
from backend.fix_gen.patch_formatter import generate_all_diffs, apply_fix_to_source
from backend.fix_gen.fix_validator import validate_fix, validate_all_fixes

__all__ = [
    'FixSuggestion',
    'generate_fixes',
    'generate_all_diffs',
    'apply_fix_to_source',
    'validate_fix',
    'validate_all_fixes',
]
