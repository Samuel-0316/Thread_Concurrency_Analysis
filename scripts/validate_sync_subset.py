#!/usr/bin/env python3
"""Validate agent on reduction/atomic/barrier-heavy subset."""
import json
import sys
import os
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.parser_service.parser import ParserService
from backend.ir.ir_normalizer_v2 import normalize_to_ir
from backend.static_analysis.static_rules import find_openmp_races, find_data_races_from_ir
from backend.tig.tig_builder import build_tig_from_ir

top_20 = [
    'DRB188-barrier3-no.c',
    'DRB189-barrier3-yes.c',
    'DRB142-acquirerelease-orig-yes.c',
    'DRB184-barrier1-no.c',
    'DRB121-reduction-orig-no.c',
    'DRB185-barrier1-yes.c',
    'DRB097-target-teams-distribute-orig-no.c',
    'DRB143-acquirerelease-orig-no.c',
    'DRB147-critical1-orig-gpu-no.c',
    'DRB182-atomic3-no.c',
    'DRB058-jacobikernel-orig-no.c',
    'DRB074-flush-orig-yes.c',
    'DRB154-missinglock3-orig-gpu-no.c',
    'DRB163-simdmissinglock1-orig-gpu-no.c',
    'DRB108-atomic-orig-no.c',
    'DRB141-reduction-barrier-orig-no.c',
    'DRB146-atomicupdate-orig-gpu-no.c',
    'DRB183-atomic3-yes.c',
    'DRB139-worksharingcritical-orig-no.c',
    'DRB148-critical1-orig-gpu-yes.c',
]

benchmark_dir = Path('datasets/dataracebench/micro-benchmarks')
parser = ParserService()

total_findings = 0
findings_with_reductions = 0
findings_with_atomics = 0
findings_with_barriers = 0
findings_with_critical = 0
findings_with_protection = 0

for fname in top_20:
    fpath = benchmark_dir / fname
    if not fpath.exists():
        continue
    
    try:
        parsed = parser.parse_file(str(fpath))
        if not parsed:
            continue
        
        # Build IR/TIG
        ir = normalize_to_ir([parsed], repo_path=str(benchmark_dir))
        tig = build_tig_from_ir(ir)
        
        # Count reductions, atomics, barriers, critical in IR
        has_reduction = len([s for s in ir.all_synchronization_points if hasattr(s, 'primitive_type') and 'REDUCTION' in str(s.primitive_type)]) > 0
        has_atomic = len([s for s in ir.all_synchronization_points if hasattr(s, 'primitive_type') and 'ATOMIC' in str(s.primitive_type)]) > 0
        has_barrier = len([s for s in ir.all_synchronization_points if hasattr(s, 'primitive_type') and 'BARRIER' in str(s.primitive_type)]) > 0
        has_critical = len([s for s in ir.all_synchronization_points if hasattr(s, 'primitive_type') and 'CRITICAL' in str(s.primitive_type)]) > 0
        
        # Find races
        findings = find_openmp_races([parsed])
        total_findings += len(findings)
        
        # Count protected accesses
        protected_accesses = sum(1 for a in ir.all_accesses if getattr(a, 'held_locks', None) or getattr(a, 'synchronization_primitives', None))
        if protected_accesses > 0:
            findings_with_protection += len(findings)
        
        if has_reduction:
            findings_with_reductions += len(findings)
        if has_atomic:
            findings_with_atomics += len(findings)
        if has_barrier:
            findings_with_barriers += len(findings)
        if has_critical:
            findings_with_critical += len(findings)
        
        print(f"{fname:50} findings={len(findings):2} red={has_reduction} atom={has_atomic} barrier={has_barrier} crit={has_critical} protected={protected_accesses}")
    except Exception as e:
        print(f"{fname:50} ERROR: {e}")

print(f"\nSummary:")
print(f"  Total findings: {total_findings}")
print(f"  Findings in files with reductions: {findings_with_reductions}")
print(f"  Findings in files with atomics: {findings_with_atomics}")
print(f"  Findings in files with barriers: {findings_with_barriers}")
print(f"  Findings in files with critical: {findings_with_critical}")
print(f"  Findings in files with protected accesses: {findings_with_protection}")
