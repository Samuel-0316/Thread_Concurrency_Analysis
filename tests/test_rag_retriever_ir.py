#!/usr/bin/env python
"""Test the IR-aware retriever and prompt/validator flow.

This is a lightweight integration check:
- parse sample files
- normalize to IR
- run static analysis to get a ConcurrencyIssue
- build context bundle
- create prompt
- run basic validators on a mock LLM response
"""
import os
import sys
sys.path.append(os.path.abspath('.'))

from backend.parser_service.parser import ParserService
from backend.ir.ir_normalizer_v2 import normalize_to_ir
from backend.static_analysis.static_rules import find_data_races_from_ir
from backend.rag.rag_retriever_ir import make_context_bundle
from backend.llm.prompt_templates import build_race_prompt
from backend.llm.validators import validate_schema, verify_claims_against_ir


def test_rag_flow():
    print('RAG Retriever IR Test')
    parser = ParserService()
    parsed = []
    sample_files = ['tests/sample.c', 'tests/sample.py']
    for p in sample_files:
        if os.path.exists(p):
            r = parser.parse_file(p)
            if r:
                parsed.append(r)
    if not parsed:
        print('No sample files parsed; aborting test')
        return

    ir = normalize_to_ir(parsed, repo_path='.')
    findings = find_data_races_from_ir(ir)
    if not findings:
        print('No data race findings in sample; test will still build context for a sample access')
        # build a fake minimal issue from first access
        first_access = ir.all_accesses[0]
        class FakeIssue:
            pass
        issue = FakeIssue()
        issue.issue_id = 'fake_1'
        issue.accesses = [first_access]
    else:
        issue = findings[0]

    bundle = make_context_bundle(issue, ir)
    print('\nContext chunks (top 5):')
    for c in bundle['chunks'][:5]:
        print(f" - {c['text']} (score={c['score']:.2f})")

    prompt = build_race_prompt(issue, bundle)
    print('\nGenerated Prompt (truncated):')
    print(prompt[:800])

    # Mock LLM response (simulate correct JSON)
    mock_response = {
        'is_real_race': True,
        'severity': 'high',
        'root_cause': 'Unprotected READ_WRITE to b in parallel region',
        'runtime_impact': 'Possible data corruption on concurrent writes',
        'recommended_fix': 'Protect with #pragma omp critical or use reduction',
        'confidence': 90,
    }

    ok, errs = validate_schema(mock_response)
    print('\nSchema validation:', ok, errs)

    ok2, errs2 = verify_claims_against_ir(mock_response, issue, ir)
    print('Deterministic verification against IR:', ok2, errs2)


if __name__ == '__main__':
    test_rag_flow()
