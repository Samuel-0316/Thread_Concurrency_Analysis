from typing import Any, Dict


class ResolverAgent:
    """Resolver attempts to reconcile analyst and critic disagreements.

    If an `llm_orchestrator` is supplied, the resolver may re-query the model
    with critic feedback. Otherwise it applies conservative deterministic
    resolution rules: prefer IR-backed facts and lower confidence on disputed
    claims.
    """

    def __init__(self, llm_orchestrator: Any = None):
        self.llm = llm_orchestrator

    def _should_requery(self, analyst_output: Dict[str, Any], critic_verdict: Dict[str, Any], finding: Dict[str, Any]) -> bool:
        """Return True only for the smallest set of cases worth a live retry.

        The pipeline should preserve quota by avoiding retries for weak or
        broad fact mismatches. We only re-query when the initial finding is
        already high confidence and the critic reports a narrow, actionable
        factual issue that the model can plausibly correct.
        """
        if self.llm is None:
            return False
        if not critic_verdict.get('schema_ok', True):
            return False
        if critic_verdict.get('fact_ok', True) is not False:
            return False

        fact_errors = critic_verdict.get('fact_errors') or []
        if not isinstance(fact_errors, list):
            fact_errors = [fact_errors]

        # Avoid burning quota on broad or low-signal failures.
        allowed_errors = {'mentioned_variable_not_found'}
        if not fact_errors or len(fact_errors) > 1:
            return False
        if fact_errors[0] not in allowed_errors:
            return False

        # Only retry strong candidates; weaker findings stay deterministic.
        confidence = finding.get('confidence')
        try:
            confidence_value = float(confidence)
        except Exception:
            confidence_value = float(analyst_output.get('analysis', {}).get('confidence', 0.0) or 0.0)

        if confidence_value <= 1.0:
            confidence_value *= 100.0

        return confidence_value >= 80.0

    def act(self, analyst_output: Dict[str, Any], critic_verdict: Dict[str, Any], finding: Dict[str, Any], ir: Any) -> Dict[str, Any]:
        analysis = analyst_output.get('analysis', {})

        # If schema invalid, create a conservative corrected analysis
        if not critic_verdict.get('schema_ok', True):
            corrected = analysis.copy()
            corrected['is_real_race'] = False
            corrected['confidence'] = 0.0
            corrected['recommended_fix'] = corrected.get('recommended_fix', '') or 'Manual review required'
            return {'resolved': corrected, 'notes': ['schema_invalid_applied_conservative_resolution']}

        # If factual checks fail and the case is strong enough, attempt one live re-query.
        if ir is not None and self._should_requery(analyst_output, critic_verdict, finding):
            # Ask LLM to reconsider with critic feedback (use analyze_finding again)
            # Provide critic notes in the prompt by attaching to finding
            feedback_finding = dict(finding)
            feedback_finding['critic_notes'] = critic_verdict.get('fact_errors', [])
            result = self.llm.analyze_finding(feedback_finding, ir=ir)
            new_analysis = result.get('analysis', {})
            return {'resolved': new_analysis, 'notes': ['requeried_llm_with_critic_feedback']}

        # Deterministic conservative resolution: prefer lower confidence
        if critic_verdict.get('fact_ok') is False:
            corrected = analysis.copy()
            corrected['is_real_race'] = False
            corrected['confidence'] = min(10.0, float(corrected.get('confidence', 0.0)))
            corrected['recommended_fix'] = corrected.get('recommended_fix', '') or 'Manual review suggested'
            return {'resolved': corrected, 'notes': ['fact_check_failed_conservative_resolution']}

        # If no problems, return analyst analysis unchanged
        return {'resolved': analysis, 'notes': ['no_change']}
