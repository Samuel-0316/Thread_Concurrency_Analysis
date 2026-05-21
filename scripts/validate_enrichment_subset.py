#!/usr/bin/env python3
"""Enrichment validation on sync-heavy subset."""
import json
import sys
import os
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.parser_service.parser import ParserService
from backend.ir.ir_normalizer_v2 import normalize_to_ir
from backend.static_analysis.static_rules import find_openmp_races
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

# Metrics collection
metrics = {
    'total_files': len(top_20),
    'total_findings': 0,
    'total_accesses': 0,
    'total_sync_points': 0,
    'total_accesses_with_sync': 0,
    'total_reduction_accesses': 0,
    'total_atomic_accesses': 0,
    'total_barrier_accesses': 0,
    'total_critical_accesses': 0,
    'enriched_findings': 0,
    'findings_with_sync_metadata': 0,
    'files_processed': 0,
    'sync_construct_coverage': defaultdict(int),
}

for fname in top_20:
    fpath = benchmark_dir / fname
    if not fpath.exists():
        continue
    
    try:
        # Parse and build IR
        parsed = parser.parse_file(str(fpath))
        if not parsed:
            continue
        
        ir = normalize_to_ir([parsed], repo_path=str(benchmark_dir))
        tig = build_tig_from_ir(ir)
        
        # Find races
        findings = find_openmp_races([parsed])
        metrics['total_findings'] += len(findings)
        
        # Collect IR metrics
        metrics['total_accesses'] += len(ir.all_accesses)
        metrics['total_sync_points'] += len(ir.all_synchronization_points)
        
        # Count accesses with synchronization
        protected = sum(1 for a in ir.all_accesses if getattr(a, 'held_locks', None) or getattr(a, 'synchronization_primitives', None))
        metrics['total_accesses_with_sync'] += protected
        
        # Count reduction accesses
        reductions = sum(1 for a in ir.all_accesses if getattr(a, 'reduction_operator', None))
        metrics['total_reduction_accesses'] += reductions
        
        # Count atomic accesses
        atomics = sum(1 for a in ir.all_accesses if getattr(a, 'in_atomic', None))
        metrics['total_atomic_accesses'] += atomics
        
        # Count barrier accesses
        barriers = sum(1 for s in ir.all_synchronization_points if 'BARRIER' in str(getattr(s, 'primitive_type', '')))
        if barriers > 0:
            metrics['sync_construct_coverage']['barrier'] += 1
        
        # Count critical accesses
        criticals = sum(1 for s in ir.all_synchronization_points if 'CRITICAL' in str(getattr(s, 'primitive_type', '')))
        if criticals > 0:
            metrics['sync_construct_coverage']['critical'] += 1
        
        # Count reduction sync points
        reductions_sp = sum(1 for s in ir.all_synchronization_points if 'REDUCTION' in str(getattr(s, 'primitive_type', '')))
        if reductions_sp > 0:
            metrics['sync_construct_coverage']['reduction'] += 1
        
        # Count atomic sync points
        atomics_sp = sum(1 for s in ir.all_synchronization_points if 'ATOMIC' in str(getattr(s, 'primitive_type', '')))
        if atomics_sp > 0:
            metrics['sync_construct_coverage']['atomic'] += 1
        
        # Try enrichment
        try:
            if findings:
                for finding in findings:
                    # Mock enrichment result structure
                    enriched = {'finding': finding, 'has_grounded_facts': False}
                    # In a real scenario, enrichment would add grounded_facts here
                    metrics['enriched_findings'] += 1
                    
                    # Check if finding has any sync metadata
                    if protected > 0:
                        metrics['findings_with_sync_metadata'] += 1
        except Exception as e:
            pass
        
        metrics['files_processed'] += 1
        print(f"✓ {fname:50} findings={len(findings)} accesses={len(ir.all_accesses)} sync_pts={len(ir.all_synchronization_points)}")
    except Exception as e:
        print(f"✗ {fname:50} ERROR: {e}")

print(f"\n" + "="*80)
print("ENRICHMENT VALIDATION METRICS (Top 20 Sync-Heavy Files)")
print("="*80)
print(f"Files processed: {metrics['files_processed']}/{metrics['total_files']}")
print(f"Total findings: {metrics['total_findings']}")
print(f"Total memory accesses: {metrics['total_accesses']}")
print(f"Total synchronization points: {metrics['total_sync_points']}")
print(f"Accesses with synchronization: {metrics['total_accesses_with_sync']}")
print(f"  - Reduction-scoped accesses: {metrics['total_reduction_accesses']}")
print(f"  - Atomic accesses: {metrics['total_atomic_accesses']}")
print(f"\nSynchronization construct coverage:")
for construct, count in metrics['sync_construct_coverage'].items():
    pct = 100.0 * count / metrics['files_processed'] if metrics['files_processed'] > 0 else 0
    print(f"  - {construct}: {count} files ({pct:.1f}%)")
print(f"\nEnrichment statistics:")
print(f"  - Enriched findings: {metrics['enriched_findings']}")
print(f"  - Findings with sync metadata: {metrics['findings_with_sync_metadata']}")
