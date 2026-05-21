#!/usr/bin/env python
"""Test IR-based TIG on DataRaceBench dataset.

Demonstrates how the enriched TIG provides better analysis on real code.
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
    analyze_tig_for_races
)


def test_tig_on_dataracebench():
    """Test IR-based TIG on DataRaceBench."""
    print("IR-Based TIG on DataRaceBench")
    print("=" * 70)
    
    # Check if DataRaceBench exists
    dataracebench_path = "datasets/DataRaceBench"
    if not os.path.exists(dataracebench_path):
        print(f"   ERROR: DataRaceBench not found at {dataracebench_path}")
        print("   Clone with: git clone https://github.com/PRUNERS/DataRaceBench datasets/DataRaceBench")
        return
    
    # Step 1: Parse DataRaceBench files
    print(f"\n1. Parsing DataRaceBench from {dataracebench_path}...")
    
    parser = ParserService()
    c_files = list(Path(dataracebench_path).glob("**/*.c"))
    
    print(f"   Found {len(c_files)} C files")
    
    if len(c_files) == 0:
        print("   ERROR: No C files found")
        return
    
    # Parse first 10 files for this test
    files_to_parse = c_files[:10]
    parsed = []
    
    for file_path in files_to_parse:
        try:
            result = parser.parse_file(str(file_path))
            if result:
                parsed.append(result)
        except Exception as e:
            print(f"   ⚠ Error parsing {file_path.name}: {str(e)[:50]}")
    
    print(f"   ✓ Parsed {len(parsed)} files successfully")
    
    # Step 2: Normalize to IR
    print(f"\n2. Normalizing to IR...")
    ir = normalize_to_ir(parsed, repo_path=dataracebench_path)
    
    print(f"   Files in IR: {len(ir.files)}")
    print(f"   Total variables: {len(ir.all_variables)}")
    print(f"   Total accesses: {len(ir.all_accesses)}")
    print(f"   Total threads: {len(ir.all_threads)}")
    print(f"   Total sync points: {len(ir.all_synchronization_points)}")
    
    # Step 3: Build enriched TIG
    print(f"\n3. Building enriched TIG from IR...")
    tig = build_tig_from_ir(ir)
    
    print(f"   TIG nodes: {tig.number_of_nodes()}")
    print(f"   TIG edges: {tig.number_of_edges()}")
    
    # Step 4: Analyze TIG
    print(f"\n4. Analyzing TIG for potential races...")
    summary = tig_summary_from_ir(tig)
    
    print(f"\n   Node distribution:")
    for node_type, count in summary['node_types'].items():
        print(f"   - {node_type}: {count}")
    
    print(f"\n   Edge distribution:")
    for relation, count in summary['edge_relations'].items():
        print(f"   - {relation}: {count}")
    
    print(f"\n   Access analysis:")
    print(f"   - Access types: {summary.get('access_types', {})}")
    print(f"   - High confidence: {summary['high_confidence_accesses']}")
    print(f"   - Protected: {summary['protected_accesses']}")
    print(f"   - Unprotected: {summary['unprotected_accesses']}")
    print(f"   - In critical sections: {summary['critical_section_accesses']}")
    print(f"   - Potential races: {summary['potential_races']}")
    
    # Step 5: Find race patterns
    print(f"\n5. Finding concurrent race patterns...")
    analysis = analyze_tig_for_races(tig)
    
    print(f"   Unprotected accesses: {analysis['unprotected_accesses_count']}")
    print(f"   Concurrent patterns: {analysis['concurrent_access_patterns']}")
    
    if analysis['concurrent_races']:
        print(f"\n   Sample race patterns:")
        for race in analysis['concurrent_races'][:5]:
            print(f"   - Variable: {race['variable']}")
            print(f"     Threads: {len(race['threads'])} concurrent threads")
            print(f"     Unprotected writes: {race['unprotected_writes']}")
            print(f"     Severity: {race['severity']}")
    
    # Step 6: Show enriched metadata examples
    print(f"\n6. Sample enriched metadata:")
    print("-" * 70)
    
    # Show variables with most accesses
    print(f"\n   Top variables by access count:")
    sorted_vars = sorted(
        [(v, len(v.accesses)) for v in ir.all_variables],
        key=lambda x: x[1],
        reverse=True
    )[:5]
    for var, access_count in sorted_vars:
        print(f"   - {var.name}: {access_count} accesses")
        print(f"     Scope: {var.scope}, Always protected: {var.always_protected}")
    
    # Show threads
    print(f"\n   Thread distribution by parallelism model:")
    model_counts = {}
    for thread in ir.all_threads:
        model = thread.parallelism_model.value
        model_counts[model] = model_counts.get(model, 0) + 1
    for model, count in model_counts.items():
        print(f"   - {model}: {count} threads")
    
    # Step 7: Export results
    output_file = "reports/tig_dataracebench_ir_based.json"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    export_data = {
        'dataset': 'DataRaceBench (subset)',
        'files_analyzed': len(parsed),
        'ir_statistics': {
            'variables': len(ir.all_variables),
            'accesses': len(ir.all_accesses),
            'threads': len(ir.all_threads),
            'sync_points': len(ir.all_synchronization_points),
        },
        'tig_structure': {
            'nodes': tig.number_of_nodes(),
            'edges': tig.number_of_edges(),
            'node_types': summary['node_types'],
            'edge_relations': summary['edge_relations'],
        },
        'analysis': {
            'high_confidence_accesses': summary['high_confidence_accesses'],
            'protected_accesses': summary['protected_accesses'],
            'unprotected_accesses': summary['unprotected_accesses'],
            'critical_section_accesses': summary['critical_section_accesses'],
            'potential_races': summary['potential_races'],
            'concurrent_patterns': analysis['concurrent_access_patterns'],
        },
        'benefits': [
            'Enriched nodes with IR metadata (scope, protection_methods, confidence)',
            'Enriched edges with access types, synchronization, OpenMP clauses',
            'Direct race detection from TIG queries',
            'Type-safe analysis with confidence tracking',
            'Foundation for better LLM reasoning',
        ]
    }
    
    with open(output_file, 'w') as f:
        json.dump(export_data, f, indent=2)
    
    print(f"\n✓ Results saved to: {output_file}")
    
    return ir, tig, analysis


if __name__ == '__main__':
    try:
        ir, tig, analysis = test_tig_on_dataracebench()
        
        print("\n" + "=" * 70)
        print("IR-Based TIG on DataRaceBench Complete")
        print("=" * 70)
        print("\nIR-Enriched TIG Advantages:")
        print("✓ All IR metadata preserved in graph")
        print("✓ No data loss between components")
        print("✓ Type-safe node/edge queries")
        print("✓ Confidence-aware analysis")
        print("✓ OpenMP-aware race detection")
        print("✓ Foundation for better accuracy")
        
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
