#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')
from backend.parser_service.parser import ParserService
from pathlib import Path

p = ParserService()
fpath = Path('datasets/dataracebench/micro-benchmarks/DRB121-reduction-orig-no.c')
result = p.parse_file(str(fpath))
print(f'Type: {type(result)}')
if isinstance(result, dict):
    print(f'Keys: {list(result.keys())}')
    print(f'Sample keys: {list(result.keys())[:10]}')
else:
    print(f'Value: {result}')
