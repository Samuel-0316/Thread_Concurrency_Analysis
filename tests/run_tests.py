import os
import sys
import json

# ensure project root is on sys.path so `backend` package is importable
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.parser_service.parser import ParserService
from backend.ir.ir_schema import normalize


def main():
    repo = os.path.dirname(__file__)
    svc = ParserService()
    parsed = svc.parse_repo(repo)
    ir = normalize(parsed)
    out = {'parsed_count': len(parsed), 'ir': ir}
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
