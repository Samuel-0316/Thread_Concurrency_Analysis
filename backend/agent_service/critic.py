from typing import Any, Dict, List
from backend.llm.validators import validate_schema, verify_claims_against_ir


class CriticAgent:
    """Critic validates analyst output deterministically against schema and IR."""

    def __init__(self):
        pass

    def act(self, analyst_output: Dict[str, Any], finding: Dict[str, Any], ir: Any) -> Dict[str, Any]:
        analysis = analyst_output.get('analysis', {})

        schema_ok, schema_errors = validate_schema(analysis)

        fact_ok = None
        fact_errors: List[str] = []
        if ir is not None:
            fact_ok, fact_errors = verify_claims_against_ir(analysis, finding, ir)

        verdict = {
            'schema_ok': schema_ok,
            'schema_errors': schema_errors,
            'fact_ok': fact_ok,
            'fact_errors': fact_errors,
        }

        return verdict
