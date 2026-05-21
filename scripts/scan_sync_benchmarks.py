#!/usr/bin/env python3
"""Scan benchmarks for reduction/atomic patterns."""
import re
from pathlib import Path

bp = Path('datasets/dataracebench/micro-benchmarks')
files = sorted(bp.glob('*.c'))

file_scores = []
for f in files:
    try:
        content = open(f, 'r', encoding='utf-8', errors='ignore').read()
        score = 0
        # Count reduction pragmas
        red_count = len(re.findall(r'reduction\s*\(', content))
        score += red_count * 10
        # Count atomic pragmas
        atom_count = len(re.findall(r'atomic', content))
        score += atom_count * 8
        # Count critical
        crit_count = len(re.findall(r'critical', content))
        score += crit_count * 5
        # Count barrier
        barrier_count = len(re.findall(r'barrier', content))
        score += barrier_count * 3
        
        if score > 0:
            file_scores.append((f.name, score, red_count, atom_count, crit_count, barrier_count))
    except Exception as e:
        pass

file_scores.sort(key=lambda x: x[1], reverse=True)

print(f"Total files with sync constructs: {len(file_scores)}")
print("\nTop 20 by score:")
for i, (name, score, red, atom, crit, barrier) in enumerate(file_scores[:20], 1):
    print(f"  {i:2}. {name:50} score={score:3} (red={red} atom={atom} crit={crit} barrier={barrier})")
