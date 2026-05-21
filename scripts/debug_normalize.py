#!/usr/bin/env python3
import sys
import traceback
sys.path.insert(0, '.')
from backend.parser_service.parser import ParserService
from backend.ir.ir_normalizer_v2 import normalize_to_ir
from pathlib import Path

p = ParserService()
fpath = Path('datasets/dataracebench/micro-benchmarks/DRB121-reduction-orig-no.c')
parsed = p.parse_file(str(fpath))
try:
    ir = normalize_to_ir([parsed], repo_path=str(Path('datasets/dataracebench/micro-benchmarks')))
    print("Success!")
except Exception as e:
    traceback.print_exc()
