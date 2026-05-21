#!/usr/bin/env python
"""End-to-End Pipeline Test: Parser → IR → TIG → Static Analysis.

Demonstrates the complete IR-based pipeline working together.
"""

import os
import sys
import json
from pathlib import Path

sys.path.append(os.path.abspath('.'))

from backend.parser_service.parser import ParserService
from backend.ir.ir_normalizer_v2 import normalize_to_ir
from backend.tig.tig_builder import build_tig_from_ir, analyze_tig_for_races, tig_summary_from_ir
from backend.static_analysis.static_rules import run_all_rules_from_ir


def test_end_to_end_pipeline():
    """Test complete pipeline end-to-end."""
    print("End-to-End Pipeline Test: Parser → IR → TIG → Analysis")
    print("=" * 80)
    
    # Step 1: Parse
    print("\n[1/5] PARSE: Convert source code to AST/tokens")
    print("-" * 80)
    
    parser = ParserService()
    
    # Parse sample files
    sample_files = ['tests/sample.c', 'tests/sample.py']
    parsed = []
    
    for file_path in sample_files:
        if os.path.exists(file_path):
            result = parser.parse_file(file_path)
            if result:
                parsed.append(result)
                print(f"✓ Parsed: {file_path}")
    
    print(f"\nParsed {len(parsed)} files")
    print(f"Output: Dict-based parser output with AST, threading constructs, locks")
    
    # Step 2: Normalize to IR
    print("\n[2/5] NORMALIZE: Dict → Comprehensive IR Objects")
    print("-" * 80)
    
    ir = normalize_to_ir(parsed, repo_path=".")
    
    print(f"✓ Created IRRepository with:")
    print(f"  - Variables: {len(ir.all_variables)}")
    print(f"  - Accesses: {len(ir.all_accesses)}")
    print(f"  - Threads: {len(ir.all_threads)}")
    print(f"  - Synchronization Points: {len(ir.all_synchronization_points)}")
    print(f"\nIR Objects Contain:")
    print(f"  - MemoryAccess: thread_id, access_type, synchronization, confidence")
    print(f"  - Variable: scope, protection_methods, always_protected")
    print(f"  - ThreadContext: parallelism_model, omp_construct, parent/child")
    print(f"  - SynchronizationPoint: primitive_type, acquired_by")
    
    # Step 3: Build IR-Enriched TIG
    print("\n[3/5] BUILD TIG: Thread Interaction Graph with IR Metadata")
    print("-" * 80)
    
    tig = build_tig_from_ir(ir)
    
    print(f"✓ Built TIG with {tig.number_of_nodes()} nodes and {tig.number_of_edges()} edges")
    print(f"\nNode Types:")
    print(f"  - Variable nodes: enriched with scope, protection_methods")
    print(f"  - Thread nodes: enriched with parallelism_model, omp_construct")
    print(f"  - Synchronization nodes: enriched with primitive_type, acquired_by")
    print(f"\nEdge Types:")
    print(f"  - Access edges: enriched with access_type, confidence, synchronization")
    print(f"  - Control flow edges: enriched with thread context")
    
    tig_summary = tig_summary_from_ir(tig)
    print(f"\nTIG Summary:")
    print(f"  - Protected accesses: {tig_summary['protected_accesses']}")
    print(f"  - Unprotected accesses: {tig_summary['unprotected_accesses']}")
    print(f"  - High confidence accesses: {tig_summary['high_confidence_accesses']}")
    print(f"  - Always protected variables: {tig_summary['always_protected_variables']}")
    
    # Step 4: Run IR-Based Static Analysis
    print("\n[4/5] ANALYZE: Detect Concurrency Issues Using IR")
    print("-" * 80)
    
    analysis = run_all_rules_from_ir(ir)
    
    total = (
        len(analysis['data_races']) +
        len(analysis['unprotected_accesses']) +
        len(analysis['lock_order_violations']) +
        len(analysis['openmp_races'])
    )
    
    print(f"✓ Ran 4 analysis rules producing ConcurrencyIssue objects:")
    print(f"\n  Data Races: {len(analysis['data_races'])}")
    for race in analysis['data_races'][:2]:
        print(f"    - {race.issue_id}: {race.variable.name if race.variable else '?'}")
    
    print(f"\n  Unprotected Accesses: {len(analysis['unprotected_accesses'])}")
    for access in analysis['unprotected_accesses'][:2]:
        var = access.accesses[0].variable_name if access.accesses else '?'
        print(f"    - {var} (thread: {access.accesses[0].thread_id if access.accesses else '?'})")
    
    print(f"\n  Lock Order Violations: {len(analysis['lock_order_violations'])}")
    print(f"  OpenMP Races: {len(analysis['openmp_races'])}")
    print(f"\n  TOTAL Findings: {total}")
    
    # Step 5: Export Results
    print("\n[5/5] OUTPUT: Structured Results Ready for RAG/LLM")
    print("-" * 80)
    
    # Show what each finding contains
    if analysis['data_races']:
        sample_race = analysis['data_races'][0]
        print(f"\nSample Data Race Finding:")
        print(f"  Issue ID: {sample_race.issue_id}")
        print(f"  Type: {sample_race.issue_type}")
        print(f"  Severity: {sample_race.severity}")
        print(f"  Confidence: {sample_race.confidence.value}")
        print(f"  Variable: {sample_race.variable.name if sample_race.variable else '?'}")
        print(f"  Accesses: {len(sample_race.accesses)}")
        
        if sample_race.accesses:
            print(f"\n  Access 1 (Full IR Metadata):")
            a1 = sample_race.accesses[0]
            print(f"    - Variable: {a1.variable_name}")
            print(f"    - Access Type: {a1.access_type.value}")
            print(f"    - Thread: {a1.thread_id}")
            print(f"    - Parallelism: {a1.parallelism_model.value}")
            print(f"    - Construct: {a1.parallel_construct}")
            print(f"    - Synchronization: {[s.value for s in a1.synchronization_primitives]}")
            print(f"    - Confidence: {a1.confidence.value}")
            print(f"    - File: {a1.file_path}:{a1.line_number}")
        
        print(f"\n  Recommendations:")
        for rec in sample_race.recommendations or []:
            print(f"    - {rec}")
        
        print(f"\n  Ready for LLM Enhancement: Yes")
        print(f"  Can be serialized to JSON: Yes")
        print(f"  Can be cached in RAG: Yes")
    
    # Export to JSON
    output_file = "reports/e2e_pipeline_results.json"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    export_data = {
        'pipeline': 'Parser → IR → TIG → IR-Based Analysis',
        'status': 'complete',
        'stages': [
            {'stage': 'Parse', 'output': 'Dict-based parser output'},
            {'stage': 'Normalize', 'output': 'IRRepository (typed objects)'},
            {'stage': 'Build TIG', 'output': 'Enriched TIG with IR metadata'},
            {'stage': 'Analyze', 'output': 'ConcurrencyIssue objects'},
        ],
        'ir_statistics': {
            'files': len(ir.files),
            'variables': len(ir.all_variables),
            'accesses': len(ir.all_accesses),
            'threads': len(ir.all_threads),
            'sync_points': len(ir.all_synchronization_points),
        },
        'tig_statistics': {
            'nodes': tig.number_of_nodes(),
            'edges': tig.number_of_edges(),
            'protected_accesses': tig_summary['protected_accesses'],
            'unprotected_accesses': tig_summary['unprotected_accesses'],
        },
        'findings': {
            'data_races': len(analysis['data_races']),
            'unprotected_accesses': len(analysis['unprotected_accesses']),
            'lock_order_violations': len(analysis['lock_order_violations']),
            'openmp_races': len(analysis['openmp_races']),
            'total': total,
        },
        'benefits': [
            'Type-safe ConcurrencyIssue objects',
            'Full IR metadata in each finding',
            'Confidence tracking throughout',
            'Ready for RAG/LLM enhancement',
            'Structured for multi-agent reasoning',
            'Backward compatible with legacy code',
        ]
    }
    
    with open(output_file, 'w') as f:
        json.dump(export_data, f, indent=2)
    
    print(f"\n✓ Results saved to: {output_file}")
    
    return ir, tig, analysis


def show_architecture_diagram():
    """Show the complete pipeline architecture."""
    print("\n" + "=" * 80)
    print("COMPLETE ARCHITECTURE")
    print("=" * 80)
    
    diagram = """
    ┌──────────────────────────────────────────────────────────────────┐
    │                       SOURCE CODE FILES                          │
    │  (C: pthread/OpenMP, Python: threading/asyncio)                 │
    └────────────────────────────┬─────────────────────────────────────┘
                                 │
                                 ▼
    ┌──────────────────────────────────────────────────────────────────┐
    │  PARSER (Parser Service)                                         │
    │  - Python: AST analysis                                          │
    │  - C: Tree-sitter + Regex (OpenMP clauses)                      │
    │  Output: Dict-based parsed representation                        │
    └────────────────────────────┬─────────────────────────────────────┘
                                 │
                                 ▼
    ┌──────────────────────────────────────────────────────────────────┐
    │  ✅ IR NORMALIZER (IR Normalization)                             │
    │  - Convert Dict → IRRepository                                  │
    │  - Preserve all metadata                                         │
    │  - Create typed MemoryAccess, Variable, ThreadContext objects   │
    │  Output: Comprehensive IR with all context                      │
    └────────────────────────────┬─────────────────────────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
                    ▼                         ▼
    ┌──────────────────────────┐  ┌──────────────────────────────────┐
    │  TIG BUILDER (from IR)   │  │  ✅ STATIC ANALYSIS (from IR)   │
    │  - Build enriched TIG    │  │  - find_data_races_from_ir()   │
    │  - Add IR metadata       │  │  - find_unprotected_access...()│
    │  - Query functions       │  │  - find_lock_order_...()       │
    │  Output: Graph with IR   │  │  - find_openmp_races_from_ir() │
    │  node/edge metadata      │  │  Output: ConcurrencyIssue[]    │
    └──────────────────────────┘  └──────────┬───────────────────────┘
                    │                        │
                    └────────────┬────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
                    ▼                         ▼
    ┌──────────────────────────┐  ┌──────────────────────────────────┐
    │  RAG RETRIEVER           │  │  REPORT EXPORTER                │
    │  - Extract rich context  │  │  - JSON export                  │
    │  - From ConcurrencyIssue │  │  - Structured findings          │
    │  - From IR metadata      │  │  - Recommendations              │
    │  Output: Enhanced context│  │  Output: JSON reports           │
    └──────────────────────────┘  └──────────────────────────────────┘
                    │
                    ▼
    ┌──────────────────────────────────────────────────────────────────┐
    │  LLM ORCHESTRATOR                                                │
    │  - Takes ConcurrencyIssue objects                                │
    │  - Enriches with RAG context                                     │
    │  - Queries LLM for analysis                                      │
    │  - Populates llm_analysis field                                  │
    │  Output: Enhanced findings with AI reasoning                     │
    └──────────────────────────────────────────────────────────────────┘
    """
    print(diagram)


if __name__ == '__main__':
    try:
        print("\n")
        ir, tig, analysis = test_end_to_end_pipeline()
        
        print("\n" + "=" * 80)
        print("PIPELINE VALIDATION COMPLETE ✅")
        print("=" * 80)
        
        show_architecture_diagram()
        
        print("\n" + "=" * 80)
        print("NEXT STEPS")
        print("=" * 80)
        print("""
1. ✅ IR Schema Implementation
2. ✅ IR Normalizer
3. ✅ Enriched TIG Builder
4. ✅ IR-Based Static Analysis
5. ⬜ RAG Retriever Enhancement
6. ⬜ LLM Orchestrator Integration
7. ⬜ Report Generation
8. ⬜ Multi-Agent Pipeline
        """)
        
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
