#!/usr/bin/env python
"""DataRaceBench analysis runner with detailed reporting.

Runs the full parser -> IR -> TIG -> static rules pipeline on DataRaceBench micro-benchmarks
and produces a comprehensive findings report with per-file analysis.
"""

import os
import sys
import json
from pathlib import Path
from collections import defaultdict

sys.path.append(os.path.abspath('.'))

from backend.parser_service.parser import ParserService
from backend.ir.ir_schema import normalize
from backend.tig.tig_builder import build_tig
from backend.static_analysis.static_rules import run_all_rules


def run_analysis(repo_path):
    """Run full analysis pipeline on DataRaceBench."""
    print(f"Analyzing DataRaceBench: {repo_path}")
    print("=" * 70)
    
    # Parse all C files in micro-benchmarks
    svc = ParserService()
    files = [
        os.path.join(r, n)
        for r, _, ns in os.walk(os.path.join(repo_path, 'micro-benchmarks'))
        for n in ns
        if n.lower().endswith('.c')
    ]
    
    print(f"\n1. Parsing {len(files)} C files...")
    parsed = [svc.parse_file(p) for p in files]
    parsed = [p for p in parsed if p]
    print(f"   Successfully parsed: {len(parsed)} files")
    
    # Build IR and TIG
    print(f"\n2. Building IR and TIG...")
    ir = normalize(parsed)
    G = build_tig(ir)
    print(f"   TIG nodes: {G.number_of_nodes()}")
    print(f"   TIG edges: {G.number_of_edges()}")
    
    # Run static rules
    print(f"\n3. Running static analysis rules...")
    findings = run_all_rules(G, parsed)
    
    # Analyze findings by type and file
    print(f"\n4. Findings Summary:")
    print("-" * 70)
    
    for key in ['unsynchronized_accesses', 'lock_order_violations', 'deadlock_cycles', 
                'openmp_races', 'openmp_races_suppressed']:
        items = findings.get(key, [])
        count = len(items) if isinstance(items, list) else (items if isinstance(items, int) else 0)
        confidence = "HIGH" if key != 'openmp_races_suppressed' else "LOW"
        print(f"   {key:.<40} {count:>6} [{confidence}]")
    
    # Per-file analysis for OpenMP races
    print(f"\n5. Per-File Analysis (High-Confidence OpenMP Races):")
    print("-" * 70)
    file_findings = defaultdict(list)
    for finding in findings.get('openmp_races', []):
        if 'file' in finding:
            fname = Path(finding['file']).name
            file_findings[fname].append(finding)
    
    sorted_files = sorted(file_findings.items(), key=lambda x: -len(x[1]))
    for fname, items in sorted_files[:20]:  # Top 20 files
        print(f"   {fname:.<50} {len(items):>3} findings")
    
    if len(sorted_files) > 20:
        print(f"   ... and {len(sorted_files) - 20} more files")
    
    print(f"\n6. Suppressed Findings (Low-Confidence):")
    print("-" * 70)
    suppressed_files = defaultdict(list)
    for finding in findings.get('openmp_races_suppressed', []):
        if 'file' in finding:
            fname = Path(finding['file']).name
            suppressed_files[fname].append(finding)
    
    sorted_suppressed = sorted(suppressed_files.items(), key=lambda x: -len(x[1]))
    for fname, items in sorted_suppressed[:10]:  # Top 10 suppressed
        print(f"   {fname:.<50} {len(items):>3} suppressed")
    
    if len(sorted_suppressed) > 10:
        print(f"   ... and {len(sorted_suppressed) - 10} more files")
    
    return findings, ir, G


if __name__ == '__main__':
    repo = os.path.abspath('datasets/dataracebench')
    findings, ir, G = run_analysis(repo)
    
    # Save detailed report
    report = {
        "summary": {
            "total_files_parsed": len(ir),
            "tig_nodes": G.number_of_nodes(),
            "tig_edges": G.number_of_edges(),
            "findings": {k: len(v) if isinstance(v, list) else v for k, v in findings.items()},
        },
        "findings": findings,
    }
    
    report_path = os.path.join("reports", "dataracebench_analysis.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    
    print(f"\n✓ Full report saved to: {report_path}")
