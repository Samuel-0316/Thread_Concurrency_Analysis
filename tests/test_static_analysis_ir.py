#!/usr/bin/env python
"""Test IR-Based Static Analysis Rules.

Demonstrates the new analysis pipeline that consumes IR directly
and produces ConcurrencyIssue objects with full metadata.
"""

import os
import sys
import json
from pathlib import Path

sys.path.append(os.path.abspath('.'))

from backend.parser_service.parser import ParserService
from backend.ir.ir_normalizer_v2 import normalize_to_ir
from backend.static_analysis.static_rules import (
    run_all_rules_from_ir,
    find_data_races_from_ir,
    find_unprotected_accesses_from_ir,
    find_openmp_races_from_ir
)


def test_ir_based_analysis():
    """Test IR-based static analysis."""
    print("IR-Based Static Analysis Test")
    print("=" * 70)
    
    # Step 1: Parse and normalize to IR
    print("\n1. Parsing and normalizing to IR...")
    parser = ParserService()
    sample_files = ['tests/sample.c', 'tests/sample.py']
    
    parsed = []
    for file_path in sample_files:
        if os.path.exists(file_path):
            result = parser.parse_file(file_path)
            if result:
                parsed.append(result)
    
    if not parsed:
        print("   ERROR: No files parsed")
        return
    
    ir = normalize_to_ir(parsed, repo_path=".")
    print(f"   ✓ Normalized {len(ir.files)} files to IR")
    print(f"     - Variables: {len(ir.all_variables)}")
    print(f"     - Accesses: {len(ir.all_accesses)}")
    print(f"     - Threads: {len(ir.all_threads)}")
    print(f"     - Sync points: {len(ir.all_synchronization_points)}")
    
    # Step 2: Run IR-based analysis
    print(f"\n2. Running IR-based static analysis rules...")
    analysis = run_all_rules_from_ir(ir)
    
    print(f"   Data races found: {len(analysis['data_races'])}")
    print(f"   Unprotected accesses found: {len(analysis['unprotected_accesses'])}")
    print(f"   Lock order violations found: {len(analysis['lock_order_violations'])}")
    print(f"   OpenMP races found: {len(analysis['openmp_races'])}")
    print(f"   OpenMP races suppressed: {len(analysis['openmp_races_suppressed'])}")
    
    # Step 3: Display data race details
    print(f"\n3. Detailed findings:")
    print("-" * 70)
    
    if analysis['data_races']:
        print(f"\n   Data Races ({len(analysis['data_races'])}):")
        for issue in analysis['data_races'][:3]:
            print(f"   - Issue: {issue.issue_id}")
            print(f"     Type: {issue.issue_type}")
            print(f"     Severity: {issue.severity}")
            print(f"     Confidence: {issue.confidence.value}")
            print(f"     Accesses: {len(issue.accesses)}")
            if issue.accesses:
                print(f"       Thread 1: {issue.accesses[0].thread_id}")
                print(f"       Thread 2: {issue.accesses[1].thread_id if len(issue.accesses) > 1 else 'N/A'}")
            print(f"     Reason: {issue.reason[:60]}...")
    
    if analysis['unprotected_accesses']:
        print(f"\n   Unprotected Accesses ({len(analysis['unprotected_accesses'])}):")
        for issue in analysis['unprotected_accesses'][:3]:
            print(f"   - Issue: {issue.issue_id}")
            print(f"     Variable: {issue.accesses[0].variable_name if issue.accesses else 'N/A'}")
            print(f"     Thread: {issue.accesses[0].thread_id if issue.accesses else 'N/A'}")
            print(f"     Severity: {issue.severity}")
            print(f"     Reason: {issue.reason[:60]}...")
    
    if analysis['openmp_races']:
        print(f"\n   OpenMP Races ({len(analysis['openmp_races'])}):")
        for issue in analysis['openmp_races'][:3]:
            print(f"   - Issue: {issue.issue_id}")
            print(f"     Variable: {issue.variable.name if issue.variable else 'N/A'}")
            print(f"     Severity: {issue.severity}")
            print(f"     Confidence: {issue.confidence.value}")
            print(f"     Reason: {issue.reason[:60]}...")
            if issue.recommendations:
                print(f"     Recommendations: {issue.recommendations[0]}")
    
    if analysis['openmp_races_suppressed']:
        print(f"\n   Suppressed OpenMP Races ({len(analysis['openmp_races_suppressed'])}):")
        for issue in analysis['openmp_races_suppressed'][:3]:
            print(f"   - {issue.variable.name if issue.variable else 'N/A'}: {issue.reason[:50]}...")
    
    # Step 4: Show IR-based vs legacy analysis
    print(f"\n4. IR-Based Analysis Advantages:")
    print("-" * 70)
    print("   ✓ Type-safe ConcurrencyIssue objects (not dicts)")
    print("   ✓ Full metadata in each issue (accesses, confidence, reason)")
    print("   ✓ Structured recommendations")
    print("   ✓ Access details (thread_id, access_type, synchronization)")
    print("   ✓ Variable context (scope, protection_methods)")
    print("   ✓ OpenMP clause awareness (private, shared, reduction)")
    print("   ✓ Confidence tracking from IR")
    
    # Step 5: Export results
    output_file = "reports/static_analysis_ir_sample.json"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    export_data = {
        'analysis_type': 'IR-based comprehensive analysis',
        'files_analyzed': len(ir.files),
        'ir_statistics': {
            'variables': len(ir.all_variables),
            'accesses': len(ir.all_accesses),
            'threads': len(ir.all_threads),
            'sync_points': len(ir.all_synchronization_points),
        },
        'findings': {
            'data_races': len(analysis['data_races']),
            'unprotected_accesses': len(analysis['unprotected_accesses']),
            'lock_order_violations': len(analysis['lock_order_violations']),
            'openmp_races': len(analysis['openmp_races']),
            'openmp_races_suppressed': len(analysis['openmp_races_suppressed']),
            'total_findings': (
                len(analysis['data_races']) +
                len(analysis['unprotected_accesses']) +
                len(analysis['lock_order_violations']) +
                len(analysis['openmp_races'])
            )
        },
        'sample_finding': {
            'type': 'ConcurrencyIssue object',
            'fields': ['issue_id', 'issue_type', 'accesses', 'variable', 'severity', 
                      'confidence', 'reason', 'recommendations', 'llm_analysis'],
            'benefits': [
                'Full access details (thread_id, access_type, synchronization, omp_clauses)',
                'Variable metadata (scope, protection_methods, always_protected)',
                'Thread context (parallelism_model, omp_construct, parent/child)',
                'Confidence tracking from IR',
                'Structured for LLM enhancement',
            ]
        }
    }
    
    with open(output_file, 'w') as f:
        json.dump(export_data, f, indent=2)
    
    print(f"\n✓ Results saved to: {output_file}")
    
    return ir, analysis


def test_openmp_race_heuristic_suppresses_protected_variables():
    """Protected OpenMP variables should not be reported as races."""
    parsed_files = [{
        'path': 'synthetic.c',
        'language': 'c',
        'omp_pragmas': [
            {'kind': 'parallel', 'line': 1, 'text': '#pragma omp parallel'},
        ],
        'omp_shared': ['shared_counter'],
        'omp_private': ['private_tmp'],
        'omp_firstprivate': [],
        'omp_lastprivate': [],
        'omp_reduction': ['reduced_sum'],
        'omp_critical_vars': [],
        'shared_variables': ['shared_counter', 'private_tmp', 'reduced_sum'],
        'var_reads': ['shared_counter', 'private_tmp', 'reduced_sum'],
        'var_writes': ['shared_counter', 'private_tmp', 'reduced_sum'],
    }]

    result = find_openmp_races(parsed_files)

    reported_vars = {item['variable'] for item in result['findings']}
    suppressed_vars = {item['variable'] for item in result['suppressed']}

    assert 'shared_counter' in reported_vars
    assert 'private_tmp' in suppressed_vars
    assert 'reduced_sum' in suppressed_vars


if __name__ == '__main__':
    ir, analysis = test_ir_based_analysis()
    
    print("\n" + "=" * 70)
    print("IR-Based Static Analysis Test Complete")
    print("=" * 70)
    print("\nKey Benefits of IR-Based Analysis:")
    print("✓ Type-safe ConcurrencyIssue objects")
    print("✓ No data loss from IR → Analysis")
    print("✓ Confidence-aware findings")
    print("✓ Full synchronization context")
    print("✓ OpenMP-aware detection")
    print("✓ Ready for LLM enhancement")
    print("✓ Structured for downstream components")
