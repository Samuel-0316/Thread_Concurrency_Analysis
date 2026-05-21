#!/usr/bin/env python3
"""Post-process existing agent validation results to attach IR/TIG grounded facts.

This avoids re-running LLMs and only builds IR for referenced files, then
enriches any result with llm_status == 'success'.
"""
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.parser_service.parser import ParserService
from backend.ir.ir_normalizer_v2 import normalize_to_ir
from backend.tig.tig_builder import build_tig_from_ir
from backend.rag.rag_retriever_ir import make_context_bundle
from backend.llm.enrichment import enrich_result


def main(input_path='reports/agent_validation_results.json', out_path=None):
    out_path = out_path or input_path
    print('Loading', input_path)
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    results = data.get('results', []) or []

    # Collect files to parse
    seen = set()
    files = []
    for r in results:
        f = r.get('finding', {})
        fp = f.get('file')
        if fp and fp not in seen:
            seen.add(fp)
            files.append(fp)

    parser = ParserService()
    parsed = []
    for fp in files:
        try:
            p = parser.parse_file(fp)
            if p:
                parsed.append(p)
        except Exception as e:
            print('Parse failed for', fp, e)

    if parsed:
        ir_repo = normalize_to_ir(parsed, repo_path=os.getcwd())
        tig = build_tig_from_ir(ir_repo)
    else:
        ir_repo = None
        tig = None

    updated = 0
    for r in results:
        try:
            analyst_meta = r.get('analyst', {}).get('meta', {}) or {}
            llm_status = analyst_meta.get('llm_status')
            if llm_status != 'success':
                continue

            f = r.get('finding', {})
            var = f.get('variable')
            filep = f.get('file')

            # Build a simple issue object
            accesses = []
            if ir_repo is not None:
                for a in getattr(ir_repo, 'all_accesses', []) or []:
                    try:
                        if var and getattr(a, 'variable_name', None) == var and (not filep or getattr(a, 'file_path', None) == filep):
                            accesses.append(a)
                    except Exception:
                        pass

            issue_obj = SimpleNamespace(issue_id=f"{filep}:{var}", accesses=accesses)
            if ir_repo is not None:
                try:
                    bundle = make_context_bundle(issue_obj, ir_repo, tig)
                    r['grounding'] = bundle
                    r['grounded'] = True
                    enrich_result(r, ir_repo)
                    updated += 1
                except Exception as e:
                    # attach minimal markers
                    r['grounding'] = None
                    r['grounded'] = False
        except Exception:
            continue

    print('Updated', updated, 'results with grounded_facts')
    with open(out_path, 'w', encoding='utf-8') as out:
        json.dump(data, out, indent=2)


if __name__ == '__main__':
    main()
