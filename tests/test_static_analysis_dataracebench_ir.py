#!/usr/bin/env python
"""Test IR-Based Static Analysis on DataRaceBench.

Demonstrates improved race detection using comprehensive IR metadata.
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
    find_openmp_races_from_ir
)


def test_ir_analysis_on_dataracebench():
    """Test IR-based analysis on DataRaceBench."""
    print("IR-Based Static Analysis on DataRaceBench")
    print("=" * 70)
    
    # Check if DataRaceBench exists
    dataracebench_path = "datasets/DataRaceBench"
    if not os.path.exists(dataracebench_path):
        print(f"   ERROR: DataRaceBench not found at {dataracebench_path}")
        return
    
    # Step 1: Parse DataRaceBench files
    print(f"\n1. Parsing DataRaceBench...")
    
    parser = ParserService()
    c_files = list(Path(dataracebench_path).glob("**/*.c"))
    
    print(f"   Found {len(c_files)} C files")
    
    # Parse first 20 files for comprehensive testing
    files_to_parse = c_files[:20]
    parsed = []
    
    for file_path in files_to_parse:
        try:
            result = parser.parse_file(str(file_path))
            if result:
                parsed.append(result)
        except Exception as e:
            pass
    
    print(f"   ✓ Parsed {len(parsed)} files successfully")
    
    # Step 2: Normalize to IR
    print(f"\n2. Normalizing to IR...")
    ir = normalize_to_ir(parsed, repo_path=dataracebench_path)
    
    print(f"   Variables: {len(ir.all_variables)}")
    print(f"   Accesses: {len(ir.all_accesses)}")
    print(f"   Threads: {len(ir.all_threads)}")
    
    # Step 3: Run comprehensive analysis
    print(f"\n3. Running IR-based analysis...")
    analysis = run_all_rules_from_ir(ir)
    
    total_findings = (
        len(analysis['data_races']) +
        len(analysis['unprotected_accesses']) +
        len(analysis['lock_order_violations']) +
        len(analysis['openmp_races'])
    )
    
    print(f"\n   Analysis Results:")
    print(f"   - Data races: {len(analysis['data_races'])}")
    print(f"   - Unprotected accesses: {len(analysis['unprotected_accesses'])}")
    print(f"   - Lock order violations: {len(analysis['lock_order_violations'])}")
    print(f"   - OpenMP races: {len(analysis['openmp_races'])}")
    print(f"   - OpenMP suppressed: {len(analysis['openmp_races_suppressed'])}")
    print(f"   - TOTAL: {total_findings}")
    
    # Step 4: Show detailed findings
    print(f"\n4. Sample Findings (First 3 of each type):")
    print("-" * 70)
    
    all_findings = []
    
    if analysis['data_races']:
        print(f"\n   Data Races:")
        for issue in analysis['data_races'][:3]:
            print(f"   - {issue.issue_id}: {issue.reason[:60]}...")
            all_findings.append(issue)
    
    if analysis['unprotected_accesses']:
        print(f"\n   Unprotected Accesses:")
        for issue in analysis['unprotected_accesses'][:3]:
            var_name = issue.accesses[0].variable_name if issue.accesses else '?'
            print(f"   - {var_name}: {issue.reason[:50]}...")
            all_findings.append(issue)
    
    if analysis['openmp_races']:
        print(f"\n   OpenMP Races:")
        for issue in analysis['openmp_races'][:3]:
            var_name = issue.variable.name if issue.variable else '?'
            print(f"   - {var_name}: Severity {issue.severity}")
            all_findings.append(issue)
    
    # Step 5: Show IR metadata usage
    print(f"\n5. IR Metadata in Findings:")
    print("-" * 70)
    
    if all_findings:
        sample = all_findings[0]
        print(f"\n   Sample Issue: {sample.issue_id}")
        print(f"   - Type: {sample.issue_type}")
        print(f"   - Severity: {sample.severity}")
        print(f"   - Confidence: {sample.confidence.value}")
        
        if sample.accesses:
            access = sample.accesses[0]
            print(f"\n   Access 1 Metadata (from IR):")
            print(f"   - Variable: {access.variable_name}")
            print(f"   - Access type: {access.access_type.value}")
            print(f"   - Thread: {access.thread_id}")
            print(f"   - Parallelism: {access.parallelism_model.value}")
            print(f"   - Construct: {access.parallel_construct}")
            print(f"   - Synchronization: {[s.value for s in access.synchronization_primitives]}")
            print(f"   - In critical: {access.in_critical_section}")
            print(f"   - In reduction: {access.in_reduction}")
            print(f"   - Confidence: {access.confidence.value}")
    
    # Step 6: Statistics
    print(f"\n6. Analysis Statistics:")
    print("-" * 70)
    
    # Count by severity
    severity_counts = {}
    for findings_list in [analysis['data_races'], analysis['unprotected_accesses'], 
                         analysis['lock_order_violations'], analysis['openmp_races']]:
        for issue in findings_list:
            severity = issue.severity
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
    
    print(f"\n   By Severity:")
    for severity in ['critical', 'high', 'medium', 'low']:
        count = severity_counts.get(severity, 0)
        print(f"   - {severity}: {count}")
    
    # Count by issue type
    type_counts = {}
    for findings_list in [analysis['data_races'], analysis['unprotected_accesses'], 
                         analysis['lock_order_violations'], analysis['openmp_races']]:
        for issue in findings_list:
            issue_type = issue.issue_type
            type_counts[issue_type] = type_counts.get(issue_type, 0) + 1
    
    print(f"\n   By Issue Type:")
    for issue_type, count in sorted(type_counts.items()):
        print(f"   - {issue_type}: {count}")
    
    # Step 7: Export results
    output_file = "reports/static_analysis_dataracebench_ir.json"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    export_data = {
        'dataset': 'DataRaceBench (subset)',
        'files_analyzed': len(parsed),
        'ir_statistics': {
            'variables': len(ir.all_variables),
            'accesses': len(ir.all_accesses),
            'threads': len(ir.all_threads),
        },
        'findings': {
            'data_races': len(analysis['data_races']),
            'unprotected_accesses': len(analysis['unprotected_accesses']),
            'lock_order_violations': len(analysis['lock_order_violations']),
            'openmp_races': len(analysis['openmp_races']),
            'openmp_suppressed': len(analysis['openmp_races_suppressed']),
            'total': total_findings,
        },
        'severity_distribution': severity_counts,
        'issue_types': type_counts,
        'improvements': [
            'Type-safe ConcurrencyIssue objects instead of dicts',
            'Full access metadata preserved (thread_id, synchronization, confidence)',
            'Variable context available (scope, protection_methods)',
            'Confidence tracking from IR',
            'OpenMP clause awareness',
            'Structured for LLM enhancement',
            'Ready for multi-agent reasoning',
        ]
    }
    
    with open(output_file, 'w') as f:
        json.dump(export_data, f, indent=2)
    
    print(f"\n✓ Results saved to: {output_file}")
    
    return ir, analysis


if __name__ == '__main__':
    try:
        ir, analysis = test_ir_analysis_on_dataracebench()
        
        print("\n" + "=" * 70)
        print("IR-Based Static Analysis Complete")
        print("=" * 70)
        print("\nPipeline: Parser → IR → Enriched TIG → IR-Based Analysis")
        print("Output: Typed ConcurrencyIssue objects with full metadata")
        print("\nNext: RAG/LLM can use these findings for better analysis")
        
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
