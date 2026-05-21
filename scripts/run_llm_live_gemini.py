#!/usr/bin/env python
"""Live LLM analysis with Google Gemini API.

This script:
1. Parses sample files and normalizes to IR
2. Runs static analysis to find ConcurrencyIssues
3. Uses Gemini API (via GOOGLE_API_KEY env var) to analyze findings
4. Validates responses deterministically
5. Prints results

Setup:
    1. Get Gemini API key from https://aistudio.google.com/app/apikeys
    2. Set environment variable (PowerShell):
       $Env:GOOGLE_API_KEY = "sk-proj-YOUR_KEY_HERE"
       
    3. Run this script:
       python scripts/run_llm_live_gemini.py
"""
import os
import sys
import json

sys.path.append(os.path.abspath('.'))

from backend.parser_service.parser import ParserService
from backend.ir.ir_normalizer_v2 import normalize_to_ir
from backend.static_analysis.static_rules import find_data_races_from_ir
from backend.llm.llm_orchestrator import LLMOrchestrator
from backend.tig.tig_builder import build_tig_from_ir


def main():
    print("=" * 70)
    print("Live Gemini LLM Analysis")
    print("=" * 70)
    
    # Check for API key
    api_key = os.getenv('GOOGLE_API_KEY')
    if not api_key:
        print("\n❌ GOOGLE_API_KEY not set!")
        print("\nSetup instructions:")
        print("1. Get your key: https://aistudio.google.com/app/apikeys")
        print("2. Set in PowerShell:")
        print("   $Env:GOOGLE_API_KEY = \"your-key-here\"")
        print("\nOr set in system environment and restart terminal.")
        return
    
    print(f"\n✓ Using GOOGLE_API_KEY (length: {len(api_key)} chars)")
    
    # Parse and normalize
    print("\n[1/5] Parsing sample files...")
    parser = ParserService()
    parsed = []
    sample_files = ['tests/sample.c', 'tests/sample.py']
    
    for p in sample_files:
        if os.path.exists(p):
            r = parser.parse_file(p)
            if r:
                parsed.append(r)
                print(f"  ✓ {p}")
    
    if not parsed:
        print("  ❌ No files parsed")
        return
    
    print(f"\n[2/5] Normalizing to IR...")
    ir = normalize_to_ir(parsed, repo_path='.')
    print(f"  ✓ Variables: {len(ir.all_variables)}")
    print(f"  ✓ Accesses: {len(ir.all_accesses)}")
    print(f"  ✓ Threads: {len(ir.all_threads)}")
    
    print(f"\n[3/5] Running static analysis...")
    findings = find_data_races_from_ir(ir)
    print(f"  ✓ Found {len(findings)} data races")
    
    if not findings:
        print("  (Using first access as mock finding for demo)")
        from backend.ir.ir_schema_v2 import ConcurrencyIssue
        finding = ConcurrencyIssue(
            issue_id='demo_1',
            issue_type='data_race',
            accesses=[ir.all_accesses[0]] if ir.all_accesses else [],
            variable=ir.all_variables[0] if ir.all_variables else None,
            severity='high',
            confidence=None
        )
        findings = [finding]
    
    # Build TIG for context
    print(f"\n[4/5] Building enriched TIG...")
    tig = build_tig_from_ir(ir)
    print(f"  ✓ TIG nodes: {tig.number_of_nodes()}")
    print(f"  ✓ TIG edges: {tig.number_of_edges()}")
    
    # Run LLM analysis
    print(f"\n[5/5] Analyzing with Gemini...")
    orchestrator = LLMOrchestrator(model='gemini-2.5-flash')
    
    for i, finding in enumerate(findings[:2]):  # Analyze first 2 findings
        print(f"\n  Finding {i+1}: {finding.issue_id}")
        print(f"  Type: {finding.issue_type}")
        
        try:
            result = orchestrator.analyze_finding(finding, ir=ir, tig=tig)
            
            print(f"\n  LLM Analysis:")
            analysis = result.get('analysis', {})
            for key, val in analysis.items():
                if key != 'raw_response':
                    print(f"    {key}: {val}")
            
            print(f"\n  Validation:")
            validation = result.get('validation', {})
            print(f"    Schema OK: {validation.get('schema_ok')}")
            if validation.get('schema_errors'):
                print(f"    Schema errors: {validation['schema_errors']}")
            print(f"    Facts OK: {validation.get('fact_ok')}")
            if validation.get('fact_errors'):
                print(f"    Fact errors: {validation['fact_errors']}")
            
            # Check if llm_analysis was attached
            if hasattr(finding, 'llm_analysis') and finding.llm_analysis:
                print(f"\n  ✓ llm_analysis attached to finding")
        
        except Exception as e:
            print(f"\n  ❌ Error: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 70)
    print("Analysis complete!")
    print("=" * 70)


if __name__ == '__main__':
    main()
