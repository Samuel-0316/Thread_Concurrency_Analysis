#!/usr/bin/env python3
"""Analyze an entire project directory for concurrency issues.

Scans all .c and .py files, runs the analysis pipeline on each,
merges the TIG graphs into a unified Knowledge Graph, and detects
cross-file concurrency patterns.

Usage:
    python scripts/analyze_project.py <directory> [--json]
"""

import argparse
import json
import os
import sys

# Ensure project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from backend.parser_service.parser import ParserService
from backend.ir.ir_normalizer_v2 import normalize_to_ir
from backend.tig.tig_builder import build_tig_from_ir, merge_tigs
from backend.static_analysis.static_rules import run_all_rules
from backend.kg.concurrency_kg import ConcurrencyKG


SUPPORTED_EXTENSIONS = {'.c', '.py'}

# Directories to skip during project scanning
EXCLUDED_DIRS = {
    '.venv', 'venv', 'env', '.env',
    'node_modules',
    '__pycache__', '.git', '.hg', '.svn',
    '.tox', '.mypy_cache', '.pytest_cache',
    'build', 'dist', 'egg-info',
    '.vscode', '.idea',
}


def discover_files(directory: str, max_files: int = 500) -> list:
    """Find all supported source files in a directory tree.

    Excludes virtual environments, node_modules, and other non-project
    directories to avoid scanning thousands of irrelevant files.
    """
    files = []
    for root, dirs, names in os.walk(directory):
        # Prune excluded directories IN-PLACE so os.walk skips them
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS
                   and not d.endswith('.egg-info')]

        for name in names:
            ext = os.path.splitext(name)[1].lower()
            if ext in SUPPORTED_EXTENSIONS:
                files.append(os.path.join(root, name))
                if len(files) >= max_files:
                    break
        if len(files) >= max_files:
            break

    files.sort()
    return files


def run_project_analysis(directory: str, json_mode: bool = False):
    """Run the full pipeline on every source file, then merge."""
    log = (lambda *a, **kw: print(*a, file=sys.stderr, **kw)) if json_mode else print

    if not os.path.isdir(directory):
        error = {'error': f'Directory not found: {directory}'}
        if json_mode:
            json.dump(error, sys.stdout)
        else:
            print(json.dumps(error, indent=2))
        return

    # ── Discover files ──
    source_files = discover_files(directory)
    log(f"[Project] Found {len(source_files)} source files in {directory}")

    if not source_files:
        error = {'error': 'No .c or .py files found in directory'}
        if json_mode:
            json.dump(error, sys.stdout)
        else:
            print(json.dumps(error, indent=2))
        return

    # ── Per-file analysis ──
    parser = ParserService()
    per_file_results = []
    all_tigs = []
    all_findings = {}
    total_findings = 0

    for i, fpath in enumerate(source_files):
        fname = os.path.basename(fpath)
        log(f"[{i+1}/{len(source_files)}] Analyzing {fname} …")

        parsed = parser.parse_file(fpath)
        if not parsed:
            log(f"  Skipped (parser returned empty)")
            continue

        lang = parsed.get('language', '?')
        ir = normalize_to_ir([parsed], repo_path=os.path.dirname(fpath))
        tig = build_tig_from_ir(ir)
        findings = run_all_rules(tig, parsed_files=[parsed], ir=ir)

        omp_count = len(findings.get('openmp_races', []))
        unprotected_count = len(findings.get('unprotected_accesses', []))
        data_race_count = len(findings.get('data_races', []))
        file_findings = omp_count + unprotected_count + data_race_count
        total_findings += file_findings

        log(f"  {lang} | TIG: {tig.number_of_nodes()}n/{tig.number_of_edges()}e | "
            f"Findings: {file_findings} (omp={omp_count}, unprotected={unprotected_count})")

        all_tigs.append(tig)

        per_file_results.append({
            'file': fpath,
            'language': lang,
            'tig_nodes': tig.number_of_nodes(),
            'tig_edges': tig.number_of_edges(),
            'openmp_races': omp_count,
            'unprotected_accesses': unprotected_count,
            'data_races': data_race_count,
        })

        # Merge findings
        for key in ['openmp_races', 'unprotected_accesses', 'data_races',
                     'unsynchronized_accesses', 'lock_order_violations']:
            items = findings.get(key, [])
            if items:
                all_findings.setdefault(key, []).extend(items)

    # ── Merge TIGs ──
    log(f"\n[Merge] Merging {len(all_tigs)} TIG graphs …")
    if all_tigs:
        merged_tig = merge_tigs(all_tigs)
        log(f"  Merged TIG: {merged_tig.number_of_nodes()} nodes, {merged_tig.number_of_edges()} edges")
    else:
        import networkx as nx
        merged_tig = nx.DiGraph()
        log(f"  No TIGs to merge")

    # ── Build unified KG ──
    log("[KG] Building project-wide Knowledge Graph …")
    kg = ConcurrencyKG()
    kg.build_from_tig(merged_tig, all_findings)
    stats = kg.stats()
    log(f"  KG: {stats['nodes']} nodes, {stats['edges']} edges")
    log(f"  Node types: {stats['node_types']}")

    # ── Cross-file analysis ──
    log("[Cross-file] Detecting cross-file patterns …")
    cross_file = kg.cross_file_summary()
    log(f"  Cross-file variables: {cross_file['total_cross_file_vars']}")
    log(f"  High risk: {cross_file['high_risk']}, Medium: {cross_file['medium_risk']}, Low: {cross_file['low_risk']}")

    # ── Variable summaries ──
    var_summaries = kg.all_variable_summaries()
    unguarded = kg.unguarded_writes()

    # ── Assemble result ──
    log("[Output] Assembling JSON …")

    summary = {
        'directory': directory,
        'files_analyzed': len(per_file_results),
        'files_skipped': len(source_files) - len(per_file_results),
        'total_findings': total_findings,
        'merged_tig_nodes': merged_tig.number_of_nodes(),
        'merged_tig_edges': merged_tig.number_of_edges(),
        'kg_nodes': stats['nodes'],
        'kg_edges': stats['edges'],
        'cross_file_vars': cross_file['total_cross_file_vars'],
        'cross_file_high_risk': cross_file['high_risk'],
    }

    result = {
        'summary': summary,
        'per_file': per_file_results,
        'knowledge_graph': {
            'stats': stats,
            'variable_summaries': var_summaries,
            'unguarded_writes': unguarded,
            'cross_file': cross_file,
        },
    }

    if json_mode:
        json.dump(result, sys.stdout, default=str)
    else:
        print(json.dumps(result, indent=2, default=str))

    log("Done.")


if __name__ == '__main__':
    ap = argparse.ArgumentParser(
        description='Run concurrency analysis on all source files in a directory')
    ap.add_argument('directory', help='Path to the project directory to analyse')
    ap.add_argument('--json', dest='json_mode', action='store_true',
                    help='Machine mode: emit only JSON to stdout')
    cli = ap.parse_args()
    run_project_analysis(cli.directory, json_mode=cli.json_mode)
