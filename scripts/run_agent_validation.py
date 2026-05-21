#!/usr/bin/env python3
"""Run the adversarial multi-agent validation pipeline on saved findings.

Saves output to ``reports/agent_validation_results.json``.

When invoked with ``--json`` the script sends **only** the final JSON blob
to *stdout* (all diagnostics go to *stderr*) so that the VS Code extension
can ``JSON.parse(stdout)`` cleanly.
"""
import argparse
import json
import os
import sys
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from backend.agent_service.orchestrator import MultiAgentOrchestrator
from backend.llm.llm_orchestrator import LLMOrchestrator
from backend.parser_service.parser import ParserService
from backend.ir.ir_normalizer_v2 import normalize_to_ir
from backend.tig.tig_builder import build_tig_from_ir


# ---------------------------------------------------------------------------
# Helper: summarise a networkx graph for serialisation
# ---------------------------------------------------------------------------

def tig_summary(graph):
    """Return a lightweight dict summary of a networkx graph."""
    try:
        return {
            'nodes': graph.number_of_nodes(),
            'edges': graph.number_of_edges(),
            'node_types': list({d.get('type', 'unknown') for _, d in graph.nodes(data=True)}),
        }
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def main(findings_path: str = 'reports/dataracebench_analysis.json',
         out_path: str = 'reports/agent_validation_results.json',
         json_mode: bool = False):
    # In --json mode every diagnostic goes to stderr so stdout stays clean.
    _print = (lambda *a, **kw: print(*a, file=sys.stderr, **kw)) if json_mode else print

    _print('Loading findings from', findings_path)
    with open(findings_path, 'r', encoding='utf-8') as f:
        report = json.load(f)

    findings = report.get('findings', {}).get('openmp_races', [])
    _print('Found', len(findings), 'findings to analyze')

    # Initialize LLM orchestrator but keep it optional (may use heuristics)
    try:
        provider = os.getenv('LLM_PROVIDER', 'auto').lower()
        model_name = os.getenv('LLM_MODEL', 'inclusionai/ring-2.6-1t:free')
        if provider == 'openrouter':
            model_name = os.getenv('OPENROUTER_MODEL', model_name)
        elif provider == 'ollama':
            model_name = os.getenv('OLLAMA_MODEL', model_name)
        elif provider == 'gemini':
            model_name = os.getenv('GEMINI_MODEL', model_name)
        orchestrator_llm = LLMOrchestrator(model=model_name)
        orchestrator_llm.initialize()
        _print('LLM orchestrator initialized')
    except Exception:
        orchestrator_llm = None
        _print('LLM orchestrator not available; running in heuristic-only mode')

    orchestrator = MultiAgentOrchestrator(llm_orchestrator=orchestrator_llm)

    # Build parsed files list for findings (only parse files referenced by findings)
    parser = ParserService()
    parsed_files = []
    seen = set()
    for f in findings:
        fp = f.get('file')
        if not fp or fp in seen:
            continue
        seen.add(fp)
        try:
            parsed = parser.parse_file(fp)
            if parsed:
                parsed_files.append(parsed)
        except Exception as e:
            _print('Parser failed for', fp, e)

    if parsed_files:
        try:
            ir_repo = normalize_to_ir(parsed_files, repo_path=os.getcwd())
            tig = build_tig_from_ir(ir_repo)
            _print('Built IR and TIG: files=', len(parsed_files))
        except Exception as e:
            _print('Failed to build IR/TIG:', e)
            ir_repo = None
            tig = None
    else:
        ir_repo = None
        tig = None

    result = orchestrator.run_on_findings(findings, ir=ir_repo, tig=tig)

    # Sanitize result for JSON serialization (remove non-serializable or circular objects)
    def sanitize(obj, depth=0, max_depth=4):
        """Shallow, depth-limited sanitizer to avoid circular refs."""
        if depth > max_depth:
            try:
                return str(obj)
            except Exception:
                return None

        if obj is None:
            return None
        if isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, list):
            return [sanitize(v, depth + 1, max_depth) for v in obj]
        if isinstance(obj, dict):
            outd = {}
            for k, v in obj.items():
                # skip LLM client or large raw responses
                if k in ('client', 'genai', 'llm_client'):
                    outd[k] = '<OMITTED_CLIENT>'
                    continue
                outd[k] = sanitize(v, depth + 1, max_depth)
            return outd

        # networkx graphs -> summary (shallow)
        try:
            import networkx as nx
            if isinstance(obj, nx.Graph) or isinstance(obj, nx.DiGraph):
                try:
                    return {'_type': 'networkx_graph', **tig_summary(obj)}
                except Exception:
                    return {'_type': 'networkx_graph'}
        except Exception:
            pass

        # IR repository and other custom objects -> shallow dict or repr
        try:
            d = getattr(obj, '__dict__', None)
            if isinstance(d, dict):
                # copy only simple attrs
                simple = {}
                for kk, vv in d.items():
                    if isinstance(vv, (str, int, float, bool)):
                        simple[kk] = vv
                if simple:
                    return {'_type': obj.__class__.__name__, 'attrs': simple}
        except Exception:
            pass

        try:
            return str(obj)
        except Exception:
            return None

    safe_result = sanitize(result)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as out_f:
        json.dump(safe_result, out_f, indent=2)

    _print('Wrote results to', out_path)

    # In --json mode, emit the JSON blob on stdout for the VS Code extension
    if json_mode:
        json.dump(safe_result, sys.stdout)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Run multi-agent validation pipeline')
    ap.add_argument('file', nargs='?', default=None, help='Optional single file to analyse')
    ap.add_argument('--json', dest='json_mode', action='store_true',
                    help='Machine mode: emit only JSON on stdout (diagnostics go to stderr)')
    ap.add_argument('--findings', default='reports/dataracebench_analysis.json',
                    help='Path to findings JSON (default: reports/dataracebench_analysis.json)')
    ap.add_argument('--out', default='reports/agent_validation_results.json',
                    help='Path to write the full results (default: reports/agent_validation_results.json)')
    cli = ap.parse_args()
    main(findings_path=cli.findings, out_path=cli.out, json_mode=cli.json_mode)
