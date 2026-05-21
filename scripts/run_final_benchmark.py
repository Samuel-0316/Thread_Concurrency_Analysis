#!/usr/bin/env python3
"""Run a final representative benchmark on top-sync files and produce final reports."""
import os
import re
import json
from pathlib import Path
from backend.parser_service.parser import ParserService
from backend.ir.ir_normalizer_v2 import normalize_to_ir
from backend.tig.tig_builder import build_tig_from_ir
from backend.static_analysis.static_rules import run_all_rules
from backend.agent_service.orchestrator import MultiAgentOrchestrator
from backend.exporter.final_report import export_reports
from backend.kg.concurrency_kg import ConcurrencyKG


def top_sync_files(repo_path: str, limit: int = 25):
    bp = Path(repo_path) / 'datasets' / 'dataracebench' / 'micro-benchmarks'
    files = sorted(bp.glob('*.c'))
    scores = []
    for f in files:
        try:
            content = open(f, 'r', encoding='utf-8', errors='ignore').read()
            score = 0
            score += len(re.findall(r'reduction\s*\(', content)) * 10
            score += len(re.findall(r'atomic', content)) * 8
            score += len(re.findall(r'critical', content)) * 5
            score += len(re.findall(r'barrier', content)) * 3
            if score > 0:
                scores.append((f, score))
        except Exception:
            pass
    scores.sort(key=lambda x: x[1], reverse=True)
    return [str(f) for f, _ in scores[:limit]]


def main(limit: int = 25):
    repo = os.getcwd()
    files = top_sync_files(repo, limit=limit)
    print('Selected', len(files), 'files')

    parser = ParserService()
    parsed = []
    for p in files:
        try:
            parsed_p = parser.parse_file(p)
            if parsed_p:
                parsed.append(parsed_p)
        except Exception as e:
            print('Parser failed for', p, e)

    if not parsed:
        print('No parsed files; aborting')
        return

    ir = normalize_to_ir(parsed, repo_path=repo)
    tig = build_tig_from_ir(ir)

    findings = run_all_rules(tig, parsed_files=parsed, ir=ir)

    # Flatten openmp_races if present
    openmp_findings = findings.get('openmp_races') or []
    # Some analysis returns ConcurrencyIssue objects; normalize to dicts for orchestrator
    norm_findings = []
    for f in openmp_findings:
        if hasattr(f, 'issue_type') or hasattr(f, '__dict__'):
            # shallow dict
            d = {}
            try:
                d['file'] = getattr(f, 'file_path', None) or getattr(f, 'file', None) or getattr(f, 'path', None)
            except Exception:
                d['file'] = None
            d['variable'] = getattr(f, 'variable', None) or getattr(f, 'variable_name', None)
            raw_conf = getattr(f, 'confidence', None)
            conf = None
            try:
                conf = float(raw_conf)
            except Exception:
                try:
                    conf = float(getattr(raw_conf, 'value', 0.0))
                except Exception:
                    conf = 0.5
            d['confidence'] = conf
            d['reason'] = getattr(f, 'reason', None)
            norm_findings.append(d)
        else:
            norm_findings.append(f)

    openmp_findings = norm_findings

    # Run orchestrator
    orchestrator = MultiAgentOrchestrator()
    orches_res = orchestrator.run_on_findings(openmp_findings, ir=ir, tig=tig)

    # Export final reports
    out_prefix = os.path.join('reports', 'final_benchmark')
    # sanitize orchestrator result for JSON serialization
    def sanitize(obj, depth=0, max_depth=4):
        if depth > max_depth:
            return str(obj)
        if obj is None:
            return None
        if isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, list):
            return [sanitize(v, depth+1, max_depth) for v in obj]
        if isinstance(obj, dict):
            return {k: sanitize(v, depth+1, max_depth) for k, v in obj.items()}
        try:
            d = getattr(obj, '__dict__', None)
            if isinstance(d, dict):
                simple = {}
                for kk, vv in d.items():
                    if isinstance(vv, (str, int, float, bool, list, dict)):
                        simple[kk] = sanitize(vv, depth+1, max_depth)
                if simple:
                    return simple
        except Exception:
            pass
        try:
            return str(obj)
        except Exception:
            return None

    safe_orch = sanitize(orches_res)
    exported = export_reports(safe_orch, out_prefix)
    print('Exported final reports:', exported)

    # Persist simple KG
    kg = ConcurrencyKG()
    for i, f in enumerate(openmp_findings, 1):
        filep = f.get('file') or '<unknown>'
        var = f.get('variable')
        try:
            var_name = var.name if hasattr(var, 'name') else str(var)
        except Exception:
            var_name = str(var)
        fid = f"{filep}:{var_name}"
        kg.add_finding(fid, {'confidence': f.get('confidence'), 'reason': f.get('reason')})
    kg_path = os.path.join('reports', 'final_benchmark_kg.json')
    kg.persist(kg_path)
    print('Persisted KG to', kg_path)


if __name__ == '__main__':
    main()
