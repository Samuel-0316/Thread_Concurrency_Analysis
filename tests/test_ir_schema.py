#!/usr/bin/env python
"""Test runner for the comprehensive IR schema.

Demonstrates how the IR becomes the universal language for all pipeline
components: parser → IR → TIG → static analysis → RAG/LLM
"""

import os
import sys
import json
from pathlib import Path

sys.path.append(os.path.abspath('.'))

from backend.parser_service.parser import ParserService
from backend.ir.ir_normalizer_v2 import normalize_to_ir
from backend.ir.ir_schema_v2 import (
    find_variable_by_name, find_accesses_for_variable,
    find_unprotected_accesses, find_concurrent_accesses
)


def test_ir_pipeline():
    """Test the IR pipeline on sample files."""
    print("Comprehensive IR Schema Test")
    print("=" * 70)
    
    # Step 1: Parse sample files
    print("\n1. Parsing sample files...")
    parser = ParserService()
    sample_files = [
        'tests/sample.c',
        'tests/sample.py'
    ]
    
    parsed = []
    for file_path in sample_files:
        if os.path.exists(file_path):
            result = parser.parse_file(file_path)
            if result:
                parsed.append(result)
                print(f"   ✓ Parsed: {file_path}")
        else:
            print(f"   ✗ Not found: {file_path}")
    
    if not parsed:
        print("   ERROR: No files parsed")
        return
    
    print(f"   Total parsed: {len(parsed)} files")
    
    # Step 2: Normalize to IR
    print("\n2. Normalizing to comprehensive IR...")
    ir = normalize_to_ir(parsed, repo_path=".")
    
    print(f"   Files in IR: {len(ir.files)}")
    print(f"   Total variables: {len(ir.all_variables)}")
    print(f"   Total accesses: {len(ir.all_accesses)}")
    print(f"   Total threads: {len(ir.all_threads)}")
    print(f"   Total sync points: {len(ir.all_synchronization_points)}")
    
    # Step 3: Query IR for insights
    print("\n3. Querying IR for analysis insights...")
    
    # Find specific variables
    print("\n   Variables in IR:")
    for var in ir.all_variables[:5]:
        print(f"   - {var.name} ({var.scope}) in {Path(var.file_path).name}")
    if len(ir.all_variables) > 5:
        print(f"   ... and {len(ir.all_variables) - 5} more")
    
    # Find accesses by variable
    if ir.all_variables:
        first_var = ir.all_variables[0]
        accesses = find_accesses_for_variable(ir, first_var)
        print(f"\n   Accesses to '{first_var.name}': {len(accesses)}")
        for access in accesses[:3]:
            print(f"   - Line {access.line_number}: {access.access_type.value} "
                  f"(thread: {access.thread_id}, sync: {access.synchronization_primitives})")
    
    # Find unprotected accesses
    unprotected = find_unprotected_accesses(ir)
    print(f"\n   Unprotected accesses: {len(unprotected)}")
    for access in unprotected[:3]:
        print(f"   - {access.variable_name} at {Path(access.file_path).name}:{access.line_number}")
    
    # Find concurrent accesses
    concurrent = find_concurrent_accesses(ir)
    print(f"\n   Potential race conditions: {len(concurrent)}")
    for a1, a2 in concurrent[:3]:
        print(f"   - {a1.variable_name}: {a1.thread_id} vs {a2.thread_id}")
    
    # Step 4: Show IR structure
    print("\n4. IR Structure Example (first access):")
    print("-" * 70)
    if ir.all_accesses:
        access = ir.all_accesses[0]
        access_dict = {
            'access_id': access.access_id,
            'variable': access.variable_name,
            'access_type': access.access_type.value,
            'file': Path(access.file_path).name,
            'line': access.line_number,
            'thread_id': access.thread_id,
            'parallelism': access.parallelism_model.value,
            'parallel_construct': access.parallel_construct,
            'synchronization': [s.value for s in access.synchronization_primitives],
            'in_critical': access.in_critical_section,
            'in_reduction': access.in_reduction,
            'confidence': access.confidence.value,
        }
        print(json.dumps(access_dict, indent=2))
    
    # Step 5: Show how IR enables downstream analysis
    print("\n5. Downstream Components Can Now Reason About IR:")
    print("-" * 70)
    print("   TIG Builder:")
    print("   - Uses IR accesses to build precise dependency graph")
    print("   - Each node enriched with synchronization context")
    print("   - Confidence-aware edge weights")
    print()
    print("   Static Analysis Rules:")
    print("   - Query IR for unprotected accesses directly")
    print("   - Find lock order violations using IR sync primitives")
    print("   - Detect deadlocks using thread/lock relationships")
    print()
    print("   RAG Retriever:")
    print("   - Uses IR thread context for better code snippet selection")
    print("   - Focuses on specific access patterns")
    print("   - Correlates synchronization strategies in context")
    print()
    print("   LLM Orchestrator:")
    print("   - Receives IR-enriched findings with full context")
    print("   - Better prompts with precise synchronization info")
    print("   - Can reason about OpenMP clause combinations")
    
    # Step 6: Save IR to file
    output_file = "reports/ir_sample.json"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    # Simple serialization (not using the full builder.to_json() for brevity)
    ir_summary = {
        'repo_id': ir.repo_id,
        'files': len(ir.files),
        'variables': len(ir.all_variables),
        'accesses': len(ir.all_accesses),
        'threads': len(ir.all_threads),
        'sync_points': len(ir.all_synchronization_points),
        'sample_access': access_dict if ir.all_accesses else None,
    }
    
    with open(output_file, 'w') as f:
        json.dump(ir_summary, f, indent=2)
    
    print(f"\n✓ IR sample saved to: {output_file}")
    
    return ir


def test_openmp_pragmas_create_ir_context():
    """OpenMP pragma metadata should become typed IR thread/sync context."""
    parsed = [{
        'path': 'synthetic_openmp.c',
        'language': 'c',
        'shared_variables': ['counter'],
        'var_reads': ['counter'],
        'var_writes': ['counter'],
        'omp_pragmas': [
            {'kind': 'parallel', 'line': 3, 'text': '#pragma omp parallel'},
            {'kind': 'critical', 'line': 7, 'text': '#pragma omp critical'},
        ],
        'omp_shared': ['counter'],
        'omp_private': [],
        'omp_firstprivate': [],
        'omp_lastprivate': [],
        'omp_reduction': [],
        'omp_critical_vars': ['counter'],
        'threads': [],
        'locks': [],
    }]

    ir = normalize_to_ir(parsed, repo_path='.')

    assert len(ir.all_threads) >= 1
    assert any(thread.parallelism_model.value == 'OPENMP' for thread in ir.all_threads)
    assert any(sync.primitive_type.value == 'CRITICAL_SECTION' for sync in ir.all_synchronization_points)
    assert any(access.parallelism_model.value == 'OPENMP' for access in ir.all_accesses)


if __name__ == '__main__':
    ir = test_ir_pipeline()
    
    print("\n" + "=" * 70)
    print("IR Schema Test Complete")
    print("=" * 70)
    print("\nNext Steps:")
    print("1. Enrich TIG with IR metadata")
    print("2. Update static analysis to consume IR")
    print("3. Enhance RAG with IR context")
    print("4. Improve LLM prompts with IR information")
