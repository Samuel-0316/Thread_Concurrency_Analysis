"""LLM Orchestrator for reasoning about concurrency findings.

Provides prompting, response parsing, provider selection, and caching.
"""

import json
import os
from typing import Dict, List, Optional, Any

# Integrations
from backend.rag.rag_retriever_ir import make_context_bundle
from backend.llm.prompt_templates import build_race_prompt
from backend.llm.providers import BaseLLMProvider, build_provider
from backend.llm.validators import validate_schema, verify_claims_against_ir


class LLMOrchestrator:
    """Orchestrates LLM calls for finding analysis."""

    def __init__(self, model: str = "gemini-2.5-flash", temperature: float = 0.3):
        """Initialize LLM orchestrator.
        
        Args:
            model: Google Gemini model name (gemini-2.5-flash, gemini-1.5-pro, etc.)
            temperature: LLM temperature (0.0-1.0)
        """
        self.model = model
        self.temperature = temperature
        self.provider: Optional[BaseLLMProvider] = None
        self.client = None
        self.analysis_cache = {}

    def initialize(self):
        """Initialize the configured provider (Gemini, OpenRouter, or Ollama)."""
        if self.provider is None:
            provider_name = os.getenv('LLM_PROVIDER', 'auto').lower()
            model_name = self.model
            if provider_name == 'openrouter':
                model_name = os.getenv('OPENROUTER_MODEL', model_name)
            elif provider_name == 'ollama':
                model_name = os.getenv('OLLAMA_MODEL', model_name)
            elif provider_name == 'gemini':
                model_name = os.getenv('GEMINI_MODEL', model_name)

            self.provider = build_provider(provider_name, model_name, temperature=self.temperature)
            self.client = self.provider
            self.model = model_name

    def analyze_finding(self, finding: Any, context_summary: Optional[str] = None, ir: Optional[Any] = None, tig: Optional[Any] = None, use_cache: bool = True) -> Dict:
        """Analyze a single concurrency finding using LLM.

        If `ir` is provided, the retriever will be used to build a structured
        context bundle and the prompt will be built from that bundle. The
        function validates the LLM output deterministically against IR facts.

        Args:
            finding: Finding dict or `ConcurrencyIssue` object
            context_summary: Fallback source context (string)
            ir: IRRepository for retriever
            tig: optional TIG graph for extra context
            use_cache: Whether to use cached results

        Returns:
            Dict with keys: finding, context_bundle, analysis, validation
        """
        self.initialize()

        # Build cache key
        file = None
        var = None
        line = None
        try:
            if isinstance(finding, dict):
                file = finding.get('file')
                var = finding.get('variable')
                line = finding.get('line')
            else:
                file = getattr(finding, 'file_path', None)
                # variable may be stored on finding.variable
                var = getattr(getattr(finding, 'variable', None), 'name', None) or getattr(finding, 'variable', None)
                line = getattr(finding, 'primary_line', None)
        except Exception:
            pass

        cache_key = f"{file}:{var}:{line}"
        if use_cache and cache_key in self.analysis_cache:
            return self.analysis_cache[cache_key]

        # If IR provided, use retriever to build context bundle and prompt
        context_bundle = None
        prompt = None
        if ir is not None:
            # ensure we pass a consistent issue object to retriever (prefer object)
            issue_obj = finding
            context_bundle = make_context_bundle(issue_obj, ir, tig)
            prompt = build_race_prompt(issue_obj, context_bundle)
        else:
            # fallback to legacy prompt with context_summary
            prompt = self._build_analysis_prompt(finding if isinstance(finding, dict) else {}, context_summary or "")

        # Call LLM
        try:
            system_instruction = "You are an expert in concurrent programming and data race detection. Analyze the given race condition and provide insights on severity, impact, and fixes."
            
            # Gemini API: system instruction in request, not as a message
            response = self.provider.generate_content(
                contents=prompt,
                generation_config={
                    'temperature': self.temperature,
                    'max_output_tokens': 2048,
                }
            )

            response_status = getattr(response, 'status', 'success')
            response_text = response.text

            # Detect infrastructure/transport errors returned inside response_text
            def _detect_llm_status(text: str) -> str:
                if not text:
                    return 'transport_failure'
                lowered = text.lower()
                if 'rate limit' in lowered or '429' in lowered or 'free-models-per-day' in lowered:
                    return 'quota_error'
                if 'timed out' in lowered or 'timeout' in lowered or 'read timeout' in lowered:
                    return 'timeout'
                # SDK wrappers often prefix errors with markers
                if 'openrouter_error' in lowered or 'openai_wrapper_error' in lowered or 'openai_error' in lowered:
                    return 'transport_failure'
                return 'success'

            llm_status = response_status or _detect_llm_status(response_text)

            # If infrastructure failure occurred, skip semantic parsing/normalization
            if llm_status != 'success':
                # Prepare a minimal result indicating skip
                validation = {
                    'schema_ok': False,
                    'schema_errors': [f'llm_{llm_status}'],
                    'fact_ok': None,
                    'fact_errors': [],
                }

                result = {
                    'finding': finding,
                    'context_bundle': context_bundle,
                    'analysis': None,
                    'validation': validation,
                    'model': self.model,
                    'llm_status': llm_status,
                    'semantic_analysis_skipped': True,
                }

                try:
                    if isinstance(finding, dict):
                        finding['llm_analysis'] = result
                    else:
                        setattr(finding, 'llm_analysis', result)
                except Exception:
                    pass

                self.analysis_cache[cache_key] = result
                return result

            # Otherwise parse and normalize as usual
            analysis = self._parse_analysis_response(response_text, finding if isinstance(finding, dict) else {})
            analysis = self._normalize_schema_output(analysis)

            # Run deterministic validators.
            # Always perform schema validation (structural checks) so `schema_ok`
            # is populated even when `ir` is not provided. Fact checks require IR.
            validation = {'schema_ok': None, 'schema_errors': [], 'fact_ok': None, 'fact_errors': []}
            parsed_obj = analysis if isinstance(analysis, dict) else {}
            ok, errs = validate_schema(parsed_obj)
            validation['schema_errors'] = errs
            # Assign schema_ok deterministically from errors list
            validation['schema_ok'] = (len(errs) == 0)

            if ir is not None:
                ok2, errs2 = verify_claims_against_ir(parsed_obj, finding, ir)
                validation['fact_ok'] = ok2
                validation['fact_errors'] = errs2

            # If schema validation failed, mark status accordingly
            if not validation['schema_ok']:
                llm_status = 'schema_failure'

            result = {
                'finding': finding,
                'context_bundle': context_bundle,
                'analysis': analysis,
                'validation': validation,
                'model': self.model,
                'llm_status': llm_status,
                'semantic_analysis_skipped': False,
            }

            # Attach llm_analysis onto finding if possible
            try:
                if isinstance(finding, dict):
                    finding['llm_analysis'] = result
                else:
                    setattr(finding, 'llm_analysis', result)
            except Exception:
                pass

            # Cache result
            self.analysis_cache[cache_key] = result

            return result

        except Exception as e:
            analysis = {
                'error': str(e),
                'raw_response': '',
                'explanation': f'Failed to analyze with LLM: {e}',
                'severity': 'unknown',
                'recommendations': ['Manual review required'],
            }
            analysis = self._normalize_schema_output(analysis)

            # Run deterministic validators on the error path as well. Always
            # perform schema validation so callers can inspect `schema_ok`.
            validation = {'schema_ok': None, 'schema_errors': [], 'fact_ok': None, 'fact_errors': []}
            parsed_obj = analysis if isinstance(analysis, dict) else {}
            ok, errs = validate_schema(parsed_obj)
            validation['schema_errors'] = errs
            validation['schema_ok'] = (len(errs) == 0)

            if ir is not None:
                ok2, errs2 = verify_claims_against_ir(parsed_obj, finding, ir)
                validation['fact_ok'] = ok2
                validation['fact_errors'] = errs2

            result = {
                'finding': finding,
                'context_bundle': context_bundle,
                'analysis': analysis,
                'validation': validation,
                'model': self.model,
                'llm_status': 'schema_failure',
                'semantic_analysis_skipped': False,
            }

            try:
                if isinstance(finding, dict):
                    finding['llm_analysis'] = result
                else:
                    setattr(finding, 'llm_analysis', result)
            except Exception:
                pass

            self.analysis_cache[cache_key] = result

            return result

    def generate_stream(self, prompt: str, generation_config: Optional[dict] = None):
        """Return a generator yielding token/text chunks from the configured client.

        If streaming is unavailable, yields a single full response string.
        """
        if generation_config is None:
            generation_config = {'temperature': self.temperature}

        if not hasattr(self, 'client') or self.client is None:
            self.initialize()

        if hasattr(self.provider, 'generate_stream'):
            yield from self.provider.generate_stream(prompt, generation_config)
        else:
            resp = self.provider.generate_content(contents=prompt, generation_config=generation_config)
            yield resp.text

        

    def analyze_batch(self, findings: List[Dict], context_summaries: List[str], max_results: int = 5) -> List[Dict]:
        """Analyze a batch of findings.
        
        Args:
            findings: List of findings
            context_summaries: Corresponding context summaries from RAG
            max_results: Maximum results to return
            
        Returns:
            List of analysis results
        """
        results = []
        for finding, context in zip(findings[:max_results], context_summaries[:max_results]):
            result = self.analyze_finding(finding, context)
            results.append(result)
        return results

    def _build_analysis_prompt(self, finding: Dict, context_summary: str) -> str:
        """Build the prompt for LLM analysis."""
        variable = finding.get('variable', 'unknown')
        omp_kind = finding.get('omp_kind', 'unknown')
        file_name = finding.get('file', '').split('/')[-1]
        
        prompt = f"""
Analyze this potential data race condition detected by static analysis:

**Finding Details:**
- File: {file_name}
- Variable: {variable}
- OpenMP Context: {omp_kind}
- Line: {finding.get('line', 'N/A')}

**Source Code Context:**
{context_summary}

**Please provide:**
1. **Severity**: Is this a real data race? (high/medium/low/false_positive)
2. **Explanation**: Why is this a data race (or why not)?
3. **Potential Impact**: What could go wrong at runtime?
4. **Reproduction Scenario**: How might the race manifest?
5. **Recommendations**: How to fix this issue? Suggest synchronization strategies.
6. **Confidence**: Your confidence in the analysis (0-100%)

Format your response as JSON with keys: severity, is_real_race, explanation, impact, reproduction, recommendations, confidence_pct
"""
        return prompt

    def _parse_analysis_response(self, response_text: str, finding: Dict) -> Dict:
        """Parse LLM response into structured format."""
        analysis = {
            'raw_response': response_text,
            'severity': 'unknown',
            'is_real_race': False,
            'explanation': '',
            'impact': '',
            'reproduction': '',
            'recommendations': [],
            'confidence_pct': 0,
        }
        
        # Try to extract JSON from response
        try:
            # Look for JSON block in response
            import re
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                json_str = json_match.group(0)
                parsed = json.loads(json_str)
                analysis.update(parsed)
        except Exception:
            # If JSON parsing fails, extract fields manually
            lines = response_text.split('\n')
            for line in lines:
                if 'severity' in line.lower():
                    if 'high' in line.lower():
                        analysis['severity'] = 'high'
                    elif 'medium' in line.lower():
                        analysis['severity'] = 'medium'
                    elif 'low' in line.lower():
                        analysis['severity'] = 'low'
                elif 'real' in line.lower() and 'race' in line.lower():
                    analysis['is_real_race'] = 'yes' in line.lower() or 'true' in line.lower()
            
            analysis['explanation'] = response_text[:500]  # First 500 chars
        
        return analysis

    def _normalize_schema_output(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Coerce LLM output into the current schema contract.

        This preserves the raw response while filling in missing keys and
        translating legacy field names so schema validation is measured against
        the contract we actually want, not the model's incidental formatting.
        """
        if not isinstance(analysis, dict):
            analysis = {}

        normalized = dict(analysis)

        def _as_text(value: Any) -> str:
            if value is None:
                return ''
            if isinstance(value, str):
                stripped = value.strip()
                if stripped.lower() in {'null', 'none', 'nan'}:
                    return ''
                return stripped
            return str(value).strip()

        def _coerce_confidence(value: Any) -> float:
            try:
                if value is None or value == '':
                    return 0.0
                return float(value)
            except Exception:
                return 0.0

        def _infer_is_real_race(text: str) -> Optional[bool]:
            lowered = text.lower()
            if not lowered:
                return None

            negative_markers = [
                'no race',
                'not a race',
                'not a real race',
                'false positive',
                'cannot be confirmed',
                'cannot confirm',
                'no data race',
                'manual review required',
                'insufficient information to confirm',
            ]
            if any(marker in lowered for marker in negative_markers):
                return False

            positive_markers = [
                'data race',
                'race condition',
                'real race',
                'high confidence',
                'severe race',
            ]
            if any(marker in lowered for marker in positive_markers):
                return True

            return None

        root_cause = _as_text(normalized.get('root_cause')) or _as_text(normalized.get('explanation'))
        runtime_impact = _as_text(normalized.get('runtime_impact')) or _as_text(normalized.get('impact'))
        recommendations_value = normalized.get('recommended_fix')
        if not _as_text(recommendations_value):
            recommendations_value = normalized.get('recommendations', '')
        if isinstance(recommendations_value, list):
            recommendations_value = '; '.join(_as_text(item) for item in recommendations_value if _as_text(item))

        normalized['root_cause'] = root_cause
        normalized['runtime_impact'] = runtime_impact
        normalized['recommended_fix'] = _as_text(recommendations_value)

        fallback_used = False
        race_value = normalized.get('is_real_race')
        if isinstance(race_value, bool):
            normalized['is_real_race'] = race_value
        else:
            inferred = _infer_is_real_race(' '.join(filter(None, [root_cause, runtime_impact, _as_text(normalized.get('raw_response'))])))
            if inferred is None:
                normalized['is_real_race'] = False
                fallback_used = True
            else:
                normalized['is_real_race'] = inferred

        severity = _as_text(normalized.get('severity')).lower()
        if severity in {'', 'unknown', 'null', 'none'}:
            severity = 'low' if normalized['is_real_race'] is False else 'medium'
        # Map legacy or descriptive token 'false_positive' to our canonical levels
        if severity == 'false_positive':
            severity = 'low'
        if severity not in {'high', 'medium', 'low'}:
            severity = 'low'
        normalized['severity'] = severity

        confidence_source = normalized.get('confidence')
        if confidence_source in (None, ''):
            confidence_source = normalized.get('confidence_pct', 0)
        normalized['confidence'] = _coerce_confidence(confidence_source)

        if fallback_used or normalized.get('error'):
            normalized['confidence'] = 0.0

        # Ensure canonical fields exist (avoid None)
        normalized['root_cause'] = normalized.get('root_cause', '') or root_cause
        normalized['runtime_impact'] = normalized.get('runtime_impact', '') or runtime_impact
        normalized['recommended_fix'] = normalized.get('recommended_fix', '') or _as_text(recommendations_value)

        # Prepare canonical-only output. Keep only the agreed contract fields.
        canonical = {
            'severity': normalized.get('severity', 'low'),
            'is_real_race': normalized.get('is_real_race', False),
            'confidence': float(normalized.get('confidence', 0.0) or 0.0),
            'root_cause': normalized.get('root_cause', ''),
            'runtime_impact': normalized.get('runtime_impact', ''),
            'recommended_fix': normalized.get('recommended_fix', ''),
        }

        return canonical


def analyze_findings_with_llm(findings: List[Dict], context_summaries: List[str], 
                              model: str = "gemini-1.5-pro", max_findings: int = 5) -> List[Dict]:
    """Convenience function to analyze findings with LLM.
    
    Args:
        findings: List of findings from static analysis
        context_summaries: List of context summaries from RAG
        model: Gemini model to use (gemini-1.5-pro, gemini-1.5-flash, etc.)
        max_findings: Maximum findings to analyze
        
    Returns:
        List of analysis results with LLM insights
    """
    orchestrator = LLMOrchestrator(model=model)
    results = orchestrator.analyze_batch(findings, context_summaries, max_results=max_findings)
    return results
