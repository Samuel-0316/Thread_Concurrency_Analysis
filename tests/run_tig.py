import os
import sys
import json

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.parser_service.parser import ParserService
from backend.ir.ir_schema import normalize
from backend.tig.tig_builder import build_tig, tig_summary


def main():
    repo = os.path.dirname(__file__)
    svc = ParserService()
    parsed = svc.parse_repo(repo)
    ir = normalize(parsed)
    G = build_tig(ir)
    s = tig_summary(G)
    print(json.dumps(s, indent=2))
    # print node types counts
    for n, d in list(G.nodes(data=True))[:20]:
        print(n, d)
    for u, v, d in list(G.edges(data=True))[:20]:
        print(u, '->', v, d)


if __name__ == '__main__':
    main()
