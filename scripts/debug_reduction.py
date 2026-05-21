#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')
from backend.parser_service.parser import ParserService
from backend.ir.ir_normalizer_v2 import normalize_to_ir, IRNormalizer
from backend.ir.ir_schema_v2 import IRBuilder
from pathlib import Path

p = ParserService()
fpath = Path('datasets/dataracebench/micro-benchmarks/DRB121-reduction-orig-no.c')
parsed = p.parse_file(str(fpath))

print(f"Parsed pragmas: {parsed.get('omp_pragmas', [])}")
print(f"Parsed reduction: {parsed.get('omp_reduction', [])}")
print(f"Reduction map: {parsed.get('omp_reduction_map', {})}")

# Build IR with debugging
normalizer = IRNormalizer(str(fpath.parent))
builder = IRBuilder(repo_id="test", repo_path=str(fpath.parent))

# Manually test pragma extraction
for pragma in parsed.get('omp_pragmas', []):
    print(f"\nTesting pragma: {pragma}")
    sync_point = normalizer._extract_omp_synchronization(pragma, parsed, str(fpath), builder, None)
    print(f"  Result: {sync_point}")

# Now build full IR
ir = normalize_to_ir([parsed], repo_path=str(fpath.parent))
print(f"\nSync points in IR: {len(ir.all_synchronization_points)}")
for sp in ir.all_synchronization_points:
    print(f"  - {sp.primitive_type if hasattr(sp, 'primitive_type') else 'unknown'}: {sp}")

