#!/usr/bin/env python3
"""Run validation on a small 20-file subset focused on reduction/atomic-heavy benchmarks."""
import os
import json
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.parser_service.parser import ParserService

# Find all C files in datasets
benchmark_dir = Path('datasets/dataracebench/micro-benchmarks')
c_files = sorted(benchmark_dir.glob('*.c'))

parser = ParserService()

# Scan files for reduction/atomic content
file_scores = []
for fpath in c_files:
    try:
        p = parser.parse_file(str(fpath))
        if not p:
            continue
        
        # Score by reduction/atomic/barrier/critical presence
        score = 0
        pragmas = p.get('omp_pragmas', []) or []
        for pragma in pragmas:
            kind = pragma.get('kind', '')
            if kind == 'reduction':
                score += 10
            elif kind == 'atomic':
                score += 8
            elif kind == 'barrier':
                score += 3
            elif kind == 'critical':
                score += 5
        
        reduction_vars = len(p.get('omp_reduction', []) or [])
        score += reduction_vars * 2
        
        if score > 0:
            file_scores.append((fpath, score))
    except Exception as e:
        pass

# Sort by score and take top 20
file_scores.sort(key=lambda x: x[1], reverse=True)
top_20 = [str(f) for f, s in file_scores[:20]]

print(f"Found {len(c_files)} C files total")
print(f"Selected {len(top_20)} reduction/atomic-heavy files")
print("\nTop 20 reduction/atomic-heavy files:")
for i, (fpath, score) in enumerate(file_scores[:20], 1):
    print(f"  {i:2}. {fpath.name:40} (score={score})")

# Write subset to file
with open('datasets/dataracebench/reduction_subset_20.json', 'w') as f:
    json.dump({'files': top_20, 'count': len(top_20)}, f, indent=2)

print(f"\nWrote subset list to datasets/dataracebench/reduction_subset_20.json")
