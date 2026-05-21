from typing import Any, Dict, List, Optional
from backend.agent_service.analyst import AnalystAgent
from backend.agent_service.critic import CriticAgent
from backend.agent_service.resolver import ResolverAgent
from backend.rag.rag_retriever_ir import make_context_bundle
from types import SimpleNamespace


class MultiAgentOrchestrator:
    """Orchestrates Analyst -> Critic -> Resolver for a batch of findings."""

    def __init__(self, llm_orchestrator: Optional[Any] = None):
        self.llm = llm_orchestrator
        self.analyst = AnalystAgent(llm_orchestrator)
        self.critic = CriticAgent()
        self.resolver = ResolverAgent(llm_orchestrator)

    def run_on_findings(self, findings: List[Dict], ir: Any = None, tig: Any = None) -> Dict[str, Any]:
        results = []

        for f in findings:
            # Step 1: Analyst
            analyst_out = self.analyst.act(f, ir=ir, tig=tig)

            # If the analyst metadata indicates an LLM infrastructure failure,
            # skip critic reasoning and resolver semantic retries and route to
            # a deterministic conservative fallback to preserve quota and
            # avoid corrupting downstream metrics.
            meta = analyst_out.get('meta', {}) or {}
            llm_status = meta.get('llm_status')
            infra_failures = {'quota_error', 'transport_failure', 'timeout'}
            if llm_status in infra_failures:
                critic_out = {
                    'schema_ok': False,
                    'schema_errors': [f'llm_{llm_status}'],
                    'fact_ok': None,
                    'fact_errors': [],
                }

                # Conservative resolution: mark as non-race, zero confidence
                resolved = {
                    'is_real_race': False,
                    'severity': 'low',
                    'confidence': 0.0,
                    'root_cause': f'LLM infrastructure failure: {llm_status}',
                    'recommended_fix': 'Manual review suggested',
                }
                resolver_out = {'resolved': resolved, 'notes': ['llm_infrastructure_failure_bypass']}
            else:
                # Step 2: Critic
                critic_out = self.critic.act(analyst_out, f, ir)

                # Step 3: Resolver
                resolver_out = self.resolver.act(analyst_out, critic_out, f, ir)

            # Final deterministic verification (re-run validators)
            final = {
                'finding': f,
                'analyst': analyst_out,
                'critic': critic_out,
                'resolver': resolver_out,
            }

            # Semantic grounding: attach IR/TIG context bundle when LLM succeeded
            try:
                meta = analyst_out.get('meta', {}) or {}
                llm_status = meta.get('llm_status')
                if llm_status == 'success' and ir is not None:
                    # Build a minimal issue object with accesses from IR matching file/variable
                    var = f.get('variable')
                    filep = f.get('file')
                    accesses = []
                    try:
                        for a in getattr(ir, 'all_accesses', []) or []:
                            if var and getattr(a, 'variable_name', None) == var and (not filep or getattr(a, 'file_path', None) == filep):
                                accesses.append(a)
                    except Exception:
                        accesses = []

                    issue_obj = SimpleNamespace(issue_id=f"{filep}:{var}", accesses=accesses)
                    try:
                        context_bundle = make_context_bundle(issue_obj, ir, tig)
                        final['grounding'] = context_bundle
                        final['grounded'] = True
                        # Enrich grounded result with IR/TIG facts
                        try:
                            from backend.llm.enrichment import enrich_result
                            enrich_result(final, ir)
                        except Exception:
                            pass
                    except Exception:
                        final['grounding'] = None
                        final['grounded'] = False
            except Exception:
                pass
            results.append(final)

        return {'results': results, 'count': len(results)}
