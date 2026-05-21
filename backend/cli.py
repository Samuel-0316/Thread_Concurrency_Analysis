import argparse
from backend.parser_service.parser import ParserService
import json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path', help='Repository path to analyze')
    ap.add_argument('--out', default='parser_output.json')
    args = ap.parse_args()

    svc = ParserService()
    res = svc.parse_repo(args.path)
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(res, fh, indent=2)
    print(f'Parsed {len(res)} files; results written to {args.out}')


if __name__ == '__main__':
    main()
