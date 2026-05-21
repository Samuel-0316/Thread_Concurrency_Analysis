import os
import sys
import json

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.parser_service.parser import ParserService
from backend.ir.ir_schema import normalize
from backend.tig.tig_builder import build_tig, tig_summary
from backend.static_analysis.static_rules import find_unsynchronized_accesses


def main():
    repo = os.path.dirname(__file__)
    svc = ParserService()
    parsed = svc.parse_repo(repo)
    ir = normalize(parsed)
    G = build_tig(ir)
    findings = find_unsynchronized_accesses(G)
    out = {'parsed_count': len(parsed), 'ir_count': len(ir), 'tig_summary': tig_summary(G), 'findings': findings}
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
