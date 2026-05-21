#!/usr/bin/env python
"""Integration test: LLM orchestrator hooked to retriever with mocked LLM client."""
import os, sys, json
sys.path.append(os.path.abspath('.'))

from backend.parser_service.parser import ParserService
from backend.ir.ir_normalizer_v2 import normalize_to_ir
from backend.static_analysis.static_rules import find_data_races_from_ir
from backend.llm.llm_orchestrator import LLMOrchestrator
from backend.llm.validators import validate_schema


def test_orchestrator_with_mock():
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
    if findings:
        issue = findings[0]
    else:
        issue = type('F', (), {})()
        issue.issue_id = 'fake'
        issue.accesses = [ir.all_accesses[0]]

    # Setup orchestrator with mocked client
    orchestrator = LLMOrchestrator(model='gpt-test')

    class MockChoice:
        def __init__(self, content):
            class Msg:
                def __init__(self, c):
                    self.content = c
            self.message = Msg(content)

    class MockResponse:
        def __init__(self, content):
            self.choices = [MockChoice(content)]

    class MockClient:
        def __init__(self, content):
            self._content = content
            class Chat:
                pass
            self.chat = self
        def completions(self):
            return None
        def create(self, **kwargs):
            return MockResponse(self._content)

    # mock JSON output matching our prompt schema
    mock_json = json.dumps({
        'is_real_race': True,
        'severity': 'high',
        'root_cause': 'Unprotected READ_WRITE to counter',
        'runtime_impact': 'Possible data corruption',
        'recommended_fix': 'Protect with mutex',
        'confidence': 92
    })

    orchestrator.client = MockClient(mock_json)

    res = orchestrator.analyze_finding(issue, ir=ir)
    print('Orchestrator result validation:', res.get('validation'))
    print('LLM analysis attached to finding:', getattr(issue, 'llm_analysis', None) is not None)


def test_normalize_schema_handles_nulls_and_error_payloads():
    orchestrator = LLMOrchestrator(model='gpt-test')

    raw = {
        'error': '429 quota exceeded',
        'raw_response': '',
        'severity': 'unknown',
        'is_real_race': None,
        'explanation': 'No race condition can be confirmed from the provided context.',
        'impact': None,
        'recommendations': ['Manual review required'],
        'confidence_pct': None,
    }

    normalized = orchestrator._normalize_schema_output(raw)
    ok, errs = validate_schema(normalized)

    assert normalized['is_real_race'] is False
    assert normalized['severity'] == 'low'
    assert normalized['root_cause'] == 'No race condition can be confirmed from the provided context.'
    assert normalized['runtime_impact'] == ''
    assert normalized['recommended_fix'] == 'Manual review required'
    assert normalized['confidence'] == 0.0
    assert ok, errs


if __name__ == '__main__':
    test_orchestrator_with_mock()
