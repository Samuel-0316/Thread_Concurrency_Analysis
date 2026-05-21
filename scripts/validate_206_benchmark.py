#!/usr/bin/env python3
"""Validate improved synchronization semantics on full 206-file benchmark."""
import json
import sys
import os
from pathlib import Path
from collections import defaultdict
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.parser_service.parser import ParserService
from backend.ir.ir_normalizer_v2 import normalize_to_ir
from backend.static_analysis.static_rules import find_openmp_races
from backend.tig.tig_builder import build_tig_from_ir

benchmark_dir = Path('datasets/dataracebench/micro-benchmarks')
c_files = sorted(benchmark_dir.glob('*.c'))

parser = ParserService()

# Metrics collection
metrics = {
    'total_files': len(c_files),
    'files_processed': 0,
    'files_failed': 0,
    'total_findings': 0,
    'total_accesses': 0,
    'total_sync_points': 0,
    'total_accesses_with_sync': 0,
    'total_reduction_accesses': 0,
    'total_atomic_accesses': 0,
    'sync_construct_coverage': defaultdict(int),
    'files_by_construct': defaultdict(list),
}

start_time = time.time()
processed = 0

for fpath in c_files:
    try:
        # Parse and build IR
        parsed = parser.parse_file(str(fpath))
        if not parsed:
            metrics['files_failed'] += 1
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
        
        # Track construct types
        constructs = set()
        for s in ir.all_synchronization_points:
            prim_type = str(getattr(s, 'primitive_type', ''))
            if 'REDUCTION' in prim_type:
                constructs.add('reduction')
                metrics['sync_construct_coverage']['reduction'] += 1
            elif 'ATOMIC' in prim_type:
                constructs.add('atomic')
                metrics['sync_construct_coverage']['atomic'] += 1
            elif 'BARRIER' in prim_type:
                constructs.add('barrier')
                metrics['sync_construct_coverage']['barrier'] += 1
            elif 'CRITICAL' in prim_type:
                constructs.add('critical')
                metrics['sync_construct_coverage']['critical'] += 1
        
        for construct in constructs:
            metrics['files_by_construct'][construct].append(fpath.name)
        
        metrics['files_processed'] += 1
        processed += 1
        
        if processed % 50 == 0:
            elapsed = time.time() - start_time
            print(f"Progress: {processed}/{len(c_files)} ({100*processed//len(c_files)}%) in {elapsed:.1f}s")
    
    except Exception as e:
        metrics['files_failed'] += 1
        print(f"✗ {fpath.name}: {e}")

elapsed = time.time() - start_time

print(f"\n" + "="*80)
print("FULL BENCHMARK VALIDATION METRICS (206 Files)")
print("="*80)
print(f"Files processed: {metrics['files_processed']}/{metrics['total_files']}")
print(f"Files failed: {metrics['files_failed']}")
print(f"Processing time: {elapsed:.1f}s")
print(f"\nTotal findings: {metrics['total_findings']}")
print(f"Total memory accesses: {metrics['total_accesses']}")
print(f"Total synchronization points: {metrics['total_sync_points']}")
print(f"Accesses with synchronization: {metrics['total_accesses_with_sync']} ({100*metrics['total_accesses_with_sync']/max(metrics['total_accesses'],1):.1f}%)")
print(f"  - Reduction-scoped accesses: {metrics['total_reduction_accesses']}")
print(f"  - Atomic accesses: {metrics['total_atomic_accesses']}")

print(f"\nSynchronization point coverage:")
for construct in ['reduction', 'atomic', 'barrier', 'critical']:
    count = sum(1 for c in metrics['files_by_construct'][construct])
    pct = 100.0 * count / metrics['files_processed'] if metrics['files_processed'] > 0 else 0
    print(f"  - {construct}: {count} instances across files")

print(f"\nFiles by construct:")
for construct in ['reduction', 'atomic', 'barrier', 'critical']:
    files = metrics['files_by_construct'][construct]
    print(f"  - {construct}: {len(set(files))} unique files")

# Save results
result_file = 'results/benchmark_validation_206.json'
Path('results').mkdir(exist_ok=True)
with open(result_file, 'w') as f:
    # Convert defaultdicts to regular dicts for JSON serialization
    output = dict(metrics)
    output['sync_construct_coverage'] = dict(output['sync_construct_coverage'])
    output['files_by_construct'] = {k: v for k, v in output['files_by_construct'].items()}
    json.dump(output, f, indent=2)
print(f"\nResults saved to {result_file}")
