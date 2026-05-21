import os
import sys
import json

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.parser_service.parser import ParserService
from backend.ir.ir_schema import normalize
from backend.tig.tig_builder import build_tig, tig_summary
from backend.static_analysis.static_rules import run_all_rules


def collect_benchmark_c_files(repo_root):
    benchmark_root = os.path.join(repo_root, 'micro-benchmarks')
    files = []
    for root, _, filenames in os.walk(benchmark_root):
        for filename in filenames:
            if filename.lower().endswith('.c'):
                files.append(os.path.join(root, filename))
    return files


def main():
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'datasets', 'dataracebench'))
    print('Analyzing repo:', repo)
    svc = ParserService()
    benchmark_files = collect_benchmark_c_files(repo)
    print('Benchmark C files:', len(benchmark_files))
    parsed = []
    for path in benchmark_files:
        try:
            parsed_file = svc.parse_file(path)
            if parsed_file:
                parsed.append(parsed_file)
        except Exception as exc:
            print(f'Failed to parse {path}: {exc}')
    print('Parsed files count:', len(parsed))
    ir = normalize(parsed)
    print('IR entries:', len(ir))
    G = build_tig(ir)
    summary = tig_summary(G)
    findings = run_all_rules(G, parsed)
    out = {'parsed_count': len(parsed), 'ir_count': len(ir), 'tig_summary': summary, 'findings': findings}
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
