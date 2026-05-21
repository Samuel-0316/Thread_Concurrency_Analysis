#!/usr/bin/env python3
"""Diagnostic script to understand OpenMP metadata extraction."""

import os
import sys
import json
sys.path.insert(0, os.path.abspath('.'))

from backend.parser_service.parser import ParserService
from backend.ir.ir_normalizer_v2 import normalize_to_ir
from backend.static_analysis.static_rules import run_all_rules_from_ir

# Test file
test_file = "datasets/dataracebench/micro-benchmarks/DRB001-antidep1-orig-yes.c"

print(f"Testing: {test_file}")
print("=" * 80)

# 1. Check what parser extracts
print("\n[1] Parser Output:")
parser = ParserService()
parsed = parser.parse_file(test_file)

if parsed:
    print(f"Language: {parsed.get('language')}")
    print(f"File path: {parsed.get('path')}")
    print(f"\nOMP Pragmas: {len(parsed.get('omp_pragmas', []))} found")
    for pragma in parsed.get('omp_pragmas', []):
        print(f"  - {pragma}")
    
    print(f"\nOMP Private vars: {parsed.get('omp_private', [])}")
    print(f"OMP Shared vars: {parsed.get('omp_shared', [])}")
    print(f"OMP Critical vars: {parsed.get('omp_critical_vars', [])}")
    
    print(f"\nVariables found:")
    print(f"  Reads: {parsed.get('var_reads', [])}")
    print(f"  Writes: {parsed.get('var_writes', [])}")
    print(f"  Shared: {parsed.get('shared_variables', [])}")
else:
    print("Failed to parse file")
    sys.exit(1)

# 2. Check IR content
print("\n[2] IR Content:")
ir = normalize_to_ir([parsed], repo_path=os.path.dirname(test_file))
print(f"Variables in IR: {len(ir.all_variables)}")
for var in ir.all_variables:
    print(f"  - {var.name}")
    print(f"    Accesses: {len(var.accesses)}")
    for acc in var.accesses:
        print(f"      {acc.access_type.value} in {acc.parallelism_model.value} (thread: {acc.thread_id})")

print(f"\nThreads in IR: {len(ir.all_threads)}")
for thread in ir.all_threads:
    print(f"  - {thread.thread_id} ({thread.parallelism_model.value})")

print(f"\nAccesses in IR: {len(ir.all_accesses)}")
for acc in ir.all_accesses[:5]:  # First 5
    print(f"  - {acc.variable_name} {acc.access_type.value} in {acc.thread_id}")

# 3. Check static analysis output
print("\n[3] Static Analysis Output:")
findings = run_all_rules_from_ir(ir)
print(f"Data races: {len(findings.get('data_races', []))}")
print(f"Unprotected accesses: {len(findings.get('unprotected_accesses', []))}")
print(f"OpenMP races: {len(findings.get('openmp_races', []))}")
print(f"OpenMP races (suppressed): {len(findings.get('openmp_races_suppressed', []))}")

print("\nOpenMP race details:")
for race in findings.get('openmp_races', []):
    print(f"  - {race.issue_id}: {race.variable.name if race.variable else '?'} ({race.severity})")
    print(f"    Reason: {race.reason}")

print("\nOpenMP race suppressed details:")
for race in findings.get('openmp_races_suppressed', []):
    print(f"  - {race.issue_id}: {race.variable.name if race.variable else '?'}")
    if hasattr(race, 'reason'):
        print(f"    Reason: {race.reason}")

print("\n" + "=" * 80)
