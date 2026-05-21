from typing import Any, Dict, Optional
from backend.rag.rag_retriever_ir import make_context_bundle


class AnalystAgent:
    """Analyst produces an initial analysis for a finding.

    If an `llm_orchestrator` is provided in context, it will be used to
    request an LLM analysis; otherwise a deterministic heuristic analysis
    is produced from the static-finding metadata.
    """

    def __init__(self, llm_orchestrator: Optional[Any] = None):
        self.llm = llm_orchestrator

    def act(self, finding: Dict[str, Any], ir: Optional[Any] = None, tig: Optional[Any] = None) -> Dict[str, Any]:
        # If an LLM orchestrator is available and IR is present, prefer it
        if self.llm and ir is not None:
            # reuse LLMOrchestrator's analyze_finding which builds context bundle
            result = self.llm.analyze_finding(finding, context_summary=None, ir=ir, tig=tig)

            # If the LLM call failed at the infrastructure layer, do not
            # treat the LLM output as a semantic analysis. Fall back to the
            # deterministic heuristic and annotate the metadata so the
            # orchestrator can bypass critic/resolver as needed.
            llm_status = result.get('llm_status')
            if llm_status and llm_status != 'success':
                # Produce deterministic heuristic analysis instead
                heuristic = self.act(finding, ir=None, tig=None) if self.llm else None
                # heuristic may recurse; if heuristic is None, build inline
                if heuristic and heuristic.get('source') == 'heuristic':
                    analysis = heuristic.get('analysis')
                else:
                    # fallback simple heuristic
                    confidence = float(finding.get('confidence', 0.5))
                    severity = 'high' if confidence >= 0.8 else ('medium' if confidence >= 0.5 else 'low')
                    is_real = True if confidence >= 0.7 else False
                    recommended_fix = f"Add synchronization (e.g., #pragma omp critical) around {finding.get('variable')}" if is_real else "Review variable access patterns"
                    analysis = {
                        'is_real_race': is_real,
                        'severity': severity,
                        'confidence': round(confidence * 100.0, 2),
                        'root_cause': f"Deterministic fallback due to LLM status: {llm_status}",
                        'runtime_impact': 'potential data corruption' if is_real else 'unlikely',
                        'recommended_fix': recommended_fix,
                    }

                meta = result.get('validation', {}) or {}
                meta.update({'llm_status': llm_status, 'semantic_analysis_skipped': True})
                return {'source': 'heuristic', 'analysis': analysis, 'meta': meta}

            # normalize to expected analyst shape
            analysis = result.get('analysis') or {}
            meta = result.get('validation', {}) or {}
            # propagate llm_status and semantic flag if present
            if 'llm_status' in result:
                meta['llm_status'] = result.get('llm_status')
                meta['semantic_analysis_skipped'] = result.get('semantic_analysis_skipped', False)
            return {'source': 'llm', 'analysis': analysis, 'meta': meta}

        # Deterministic heuristic analysis
        confidence = float(finding.get('confidence', 0.5))
        severity = 'high' if confidence >= 0.8 else ('medium' if confidence >= 0.5 else 'low')
        is_real = True if confidence >= 0.7 else False
        recommended_fix = f"Add synchronization (e.g., #pragma omp critical) around {finding.get('variable')}" if is_real else "Review variable access patterns"

        analysis = {
            'is_real_race': is_real,
            'severity': severity,
            'confidence': round(confidence * 100.0, 2),
            'root_cause': f"Static heuristic: {finding.get('reason')}",
            'runtime_impact': 'potential data corruption' if is_real else 'unlikely',
            'recommended_fix': recommended_fix,
        }

        return {'source': 'heuristic', 'analysis': analysis, 'meta': {'heuristic_confidence': confidence}}
