#!/usr/bin/env python
"""Test IR-based TIG Builder.

Demonstrates how the TIG is now enriched with IR metadata for better analysis.
"""

import os
import sys
import json
from pathlib import Path

sys.path.append(os.path.abspath('.'))

from backend.parser_service.parser import ParserService
from backend.ir.ir_normalizer_v2 import normalize_to_ir
from backend.tig.tig_builder import (
    build_tig_from_ir, tig_summary_from_ir,
    find_unprotected_accesses_in_tig,
    find_concurrent_accesses_in_tig,
    analyze_tig_for_races
)


def test_tig_from_ir():
    """Test TIG builder with IR input."""
    print("IR-Based TIG Builder Test")
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
    
    # Step 2: Build TIG from IR
    print("\n2. Building enriched TIG from IR...")
    tig = build_tig_from_ir(ir)
    print(f"   ✓ Built TIG with {tig.number_of_nodes()} nodes, {tig.number_of_edges()} edges")
    
    # Step 3: Analyze TIG structure
    print("\n3. Analyzing TIG structure...")
    summary = tig_summary_from_ir(tig)
    
    print(f"   Node types:")
    for node_type, count in summary['node_types'].items():
        print(f"   - {node_type}: {count}")
    
    print(f"\n   Edge relations:")
    for relation, count in summary['edge_relations'].items():
        print(f"   - {relation}: {count}")
    
    print(f"\n   Access metadata:")
    print(f"   - High confidence accesses: {summary['high_confidence_accesses']}")
    print(f"   - Protected accesses: {summary['protected_accesses']}")
    print(f"   - Unprotected accesses: {summary['unprotected_accesses']}")
    print(f"   - In critical sections: {summary['critical_section_accesses']}")
    
    # Step 4: Show enriched node metadata
    print("\n4. Sample node metadata (enriched with IR):")
    print("-" * 70)
    
    # Show a variable node
    for node, data in tig.nodes(data=True):
        if data.get('type') == 'variable':
            print(f"\n   Variable Node: {node}")
            print(f"   - Scope: {data.get('scope')}")
            print(f"   - Type: {data.get('c_type')}")
            print(f"   - Always protected: {data.get('always_protected')}")
            print(f"   - Protection methods: {data.get('protection_methods')}")
            print(f"   - Number of accesses: {data.get('num_accesses')}")
            break
    
    # Show a thread node
    for node, data in tig.nodes(data=True):
        if data.get('type') == 'thread':
            print(f"\n   Thread Node: {node}")
            print(f"   - Parallelism model: {data.get('parallelism_model')}")
            print(f"   - OpenMP construct: {data.get('omp_construct')}")
            print(f"   - Parent thread: {data.get('parent_thread')}")
            print(f"   - Number of accesses: {data.get('num_accesses')}")
            break
    
    # Step 5: Show enriched edge metadata
    print("\n5. Sample edge metadata (enriched with IR):")
    print("-" * 70)
    
    for u, v, d in tig.edges(data=True):
        if d.get('relation') == 'may_access':
            print(f"\n   Edge: {u} → {v}")
            print(f"   - Access type: {d.get('access_type')}")
            print(f"   - Confidence: {d.get('confidence')}")
            print(f"   - Location: {Path(d.get('file_path')).name}:{d.get('line_number')}")
            print(f"   - In critical section: {d.get('in_critical_section')}")
            print(f"   - In reduction: {d.get('in_reduction')}")
            print(f"   - Synchronization: {d.get('synchronization')}")
            print(f"   - Held locks: {d.get('held_locks')}")
            print(f"   - OpenMP clauses: {d.get('omp_clauses')}")
            print(f"   - Parallelism model: {d.get('parallelism_model')}")
            print(f"   - Parallel construct: {d.get('parallel_construct')}")
            break
    
    # Step 6: Find unprotected accesses
    print("\n6. Finding unprotected accesses in TIG...")
    unprotected = find_unprotected_accesses_in_tig(tig)
    print(f"   Found {len(unprotected)} unprotected accesses")
    for u, v, d in unprotected[:3]:
        print(f"   - {u} → {v} ({d.get('access_type')})")
    
    # Step 7: Find concurrent access patterns
    print("\n7. Finding concurrent access patterns...")
    concurrent = find_concurrent_accesses_in_tig(tig)
    print(f"   Found {len(concurrent)} concurrent access patterns")
    for race in concurrent[:3]:
        print(f"   - Variable: {race['variable']}")
        print(f"     Threads: {race['threads']}")
        print(f"     Unprotected writes: {race['unprotected_writes']}")
        print(f"     Severity: {race['severity']}")
    
    # Step 8: Full race analysis
    print("\n8. Comprehensive race analysis...")
    analysis = analyze_tig_for_races(tig)
    print(f"   Unprotected accesses: {analysis['unprotected_accesses_count']}")
    print(f"   Concurrent patterns: {analysis['concurrent_access_patterns']}")
    
    # Step 9: Export results
    output_file = "reports/tig_ir_sample.json"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    tig_export = {
        'summary': {
            'nodes': summary['node_count'],
            'edges': summary['edge_count'],
            'node_types': summary['node_types'],
            'edge_relations': summary['edge_relations'],
            'protected_accesses': summary['protected_accesses'],
            'unprotected_accesses': summary['unprotected_accesses'],
            'potential_races': summary['potential_races'],
        },
        'analysis': {
            'unprotected_accesses': len(unprotected),
            'concurrent_patterns': len(concurrent),
        }
    }
    
    with open(output_file, 'w') as f:
        json.dump(tig_export, f, indent=2)
    
    print(f"\n✓ TIG export saved to: {output_file}")
    
    return tig, ir


if __name__ == '__main__':
    tig, ir = test_tig_from_ir()
    
    print("\n" + "=" * 70)
    print("IR-Based TIG Builder Test Complete")
    print("=" * 70)
    print("\nKey Benefits of IR-Enriched TIG:")
    print("✓ Nodes have full context (scope, protection_methods, parallelism_model)")
    print("✓ Edges have rich metadata (access_type, confidence, synchronization)")
    print("✓ OpenMP support (clauses, constructs, reductions)")
    print("✓ Direct race detection using TIG queries")
    print("✓ All IR metadata flows through for better analysis")
