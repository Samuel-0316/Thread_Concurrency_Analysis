import os
import json
import sys

# ensure project root is on sys.path
sys.path.append(os.path.abspath('.'))

from backend.exporter.report import export_findings
from backend.parser_service.parser import ParserService
from backend.ir.ir_schema import normalize
from backend.tig.tig_builder import build_tig
from backend.static_analysis.static_rules import run_all_rules


def main(repo_path=None):
    repo = os.path.abspath(repo_path) if repo_path else os.path.abspath('datasets/dataracebench')
    svc = ParserService()
    files = [
        os.path.join(r, n)
        for r, _, ns in os.walk(os.path.join(repo, 'micro-benchmarks'))
        for n in ns
        if n.lower().endswith('.c')
    ]
    parsed = [svc.parse_file(p) for p in files]
    parsed = [p for p in parsed if p]
    G = build_tig(normalize(parsed))
    findings = run_all_rules(G, parsed)

    out_dir = os.path.join('reports', 'dataracebench_manual')
    paths = export_findings(findings, out_dir)
    print(json.dumps({"out_dir": out_dir, "paths": paths, "counts": {k:(len(v) if isinstance(v, list) else v) for k,v in findings.items()}}, indent=2))


if __name__ == '__main__':
    repo_arg = sys.argv[1] if len(sys.argv) > 1 else None
    main(repo_arg)
