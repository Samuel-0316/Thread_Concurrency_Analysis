"""Simple pointer alias analysis for concurrency checking.

Tracks `ptr = base + offset` patterns and pointer assignments
to detect when two pointers may alias (point to the same memory).
This helps reduce false positives when the static rules flag
accesses through different pointer names that actually alias.
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field


@dataclass
class PointerFact:
    """A known fact about a pointer's value."""
    ptr_name: str           # Name of the pointer variable
    base: str               # Base pointer it derives from (or 'alloc' for malloc)
    offset: Optional[str]   # Offset expression, or None if unknown
    line: int               # Line where this fact is established
    file_path: str


@dataclass
class AliasPair:
    """Two pointers that may alias."""
    ptr_a: str
    ptr_b: str
    reason: str
    confidence: float  # 0-1


def extract_pointer_facts(parsed_file: Dict) -> List[PointerFact]:
    """Extract pointer assignment facts from a parsed C file.

    Tracks patterns like:
      - int *p = arr;           → p aliases arr
      - int *p = &var;          → p aliases var
      - int *p = arr + offset;  → p aliases arr with offset
      - int *p = malloc(...)    → p is fresh allocation
      - p = q;                  → p aliases q
    """
    source = parsed_file.get('source', '') or parsed_file.get('content', '')
    file_path = parsed_file.get('file_path', '')
    if not source:
        return []

    facts = []
    lines = source.split('\n')

    # Pattern: type *ptr = expr;
    ptr_decl = re.compile(
        r'(?:(?:int|double|float|char|long|void|unsigned)\s*\*+\s*)'
        r'(\w+)\s*=\s*(.+?)\s*;'
    )
    # Pattern: ptr = expr;
    ptr_assign = re.compile(r'(\w+)\s*=\s*(.+?)\s*;')
    # Pattern for malloc/calloc
    alloc_pattern = re.compile(r'\b(?:malloc|calloc|realloc)\s*\(')
    # Pattern for address-of
    addr_pattern = re.compile(r'&(\w+)')
    # Pattern for base + offset
    offset_pattern = re.compile(r'(\w+)\s*\+\s*(.+)')

    known_ptrs: Set[str] = set()

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith('//') or stripped.startswith('#'):
            continue

        line_num = i + 1

        # Check pointer declarations
        for m in ptr_decl.finditer(stripped):
            ptr_name = m.group(1)
            rhs = m.group(2).strip()
            known_ptrs.add(ptr_name)

            fact = _parse_rhs(ptr_name, rhs, line_num, file_path)
            if fact:
                facts.append(fact)

        # Check pointer reassignments (only for known pointers)
        for m in ptr_assign.finditer(stripped):
            ptr_name = m.group(1)
            if ptr_name not in known_ptrs:
                continue
            rhs = m.group(2).strip()
            fact = _parse_rhs(ptr_name, rhs, line_num, file_path)
            if fact:
                facts.append(fact)

    return facts


def _parse_rhs(ptr_name: str, rhs: str, line: int, file_path: str) -> Optional[PointerFact]:
    """Parse the right-hand side of a pointer assignment."""
    # malloc/calloc → fresh allocation
    if re.search(r'\b(?:malloc|calloc|realloc)\b', rhs):
        return PointerFact(ptr_name, 'alloc', None, line, file_path)

    # &var → aliases var
    m = re.match(r'&(\w+)', rhs)
    if m:
        return PointerFact(ptr_name, m.group(1), '0', line, file_path)

    # base + offset → aliases base with offset
    m = re.match(r'(\w+)\s*\+\s*(.+)', rhs)
    if m:
        return PointerFact(ptr_name, m.group(1), m.group(2).strip(), line, file_path)

    # Simple assignment: ptr = other_ptr or ptr = array_name
    m = re.match(r'(\w+)\s*$', rhs)
    if m:
        return PointerFact(ptr_name, m.group(1), None, line, file_path)

    # Cast: (type*)expr
    m = re.match(r'\(\s*\w+\s*\*\s*\)\s*(.+)', rhs)
    if m:
        return _parse_rhs(ptr_name, m.group(1).strip(), line, file_path)

    return None


def find_alias_pairs(facts: List[PointerFact]) -> List[AliasPair]:
    """Determine which pointers may alias based on collected facts.

    Two pointers may alias if:
      - They derive from the same base (regardless of offset) with
        overlapping or unknown offsets
      - One is assigned from the other

    Returns list of AliasPair with confidence scores.
    """
    pairs = []

    # Group facts by base
    base_groups: Dict[str, List[PointerFact]] = {}
    for f in facts:
        if f.base == 'alloc':
            continue  # Fresh allocations don't alias
        base_groups.setdefault(f.base, []).append(f)

    # Check for same-base aliases
    for base, group in base_groups.items():
        ptrs = list(set(f.ptr_name for f in group))
        for i in range(len(ptrs)):
            for j in range(i + 1, len(ptrs)):
                # Check if they might point to the same memory
                facts_a = [f for f in group if f.ptr_name == ptrs[i]]
                facts_b = [f for f in group if f.ptr_name == ptrs[j]]

                # If either has unknown offset, they may alias
                a_offsets = {f.offset for f in facts_a}
                b_offsets = {f.offset for f in facts_b}

                if None in a_offsets or None in b_offsets:
                    confidence = 0.7
                    reason = f"Both derive from '{base}' with unknown offset"
                elif a_offsets & b_offsets:
                    confidence = 0.95
                    reason = f"Both derive from '{base}' with same offset"
                else:
                    confidence = 0.3
                    reason = f"Both derive from '{base}' but different offsets"

                pairs.append(AliasPair(
                    ptr_a=ptrs[i],
                    ptr_b=ptrs[j],
                    reason=reason,
                    confidence=confidence,
                ))

    # Check for direct assignment aliases (p = q)
    direct_aliases: Dict[str, Set[str]] = {}
    for f in facts:
        if f.base != 'alloc' and f.offset is None:
            # p = base (direct alias)
            direct_aliases.setdefault(f.ptr_name, set()).add(f.base)

    for ptr, bases in direct_aliases.items():
        for base in bases:
            if base in direct_aliases or any(f2.ptr_name == base for f2 in facts):
                # Transitive: ptr -> base -> ...
                pairs.append(AliasPair(
                    ptr_a=ptr,
                    ptr_b=base,
                    reason=f"Direct assignment: {ptr} = {base}",
                    confidence=0.9,
                ))

    return pairs


def alias_analysis_summary(parsed_files: List[Dict]) -> Dict[str, Any]:
    """Run alias analysis and return a summary."""
    all_facts = []
    for pf in parsed_files:
        all_facts.extend(extract_pointer_facts(pf))

    pairs = find_alias_pairs(all_facts)

    return {
        'pointer_facts': len(all_facts),
        'alias_pairs': len(pairs),
        'high_confidence_aliases': sum(1 for p in pairs if p.confidence >= 0.7),
        'pairs': [
            {
                'ptr_a': p.ptr_a,
                'ptr_b': p.ptr_b,
                'reason': p.reason,
                'confidence': p.confidence,
            }
            for p in pairs[:20]
        ],
    }
