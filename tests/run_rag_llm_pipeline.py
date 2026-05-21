#!/usr/bin/env python
"""RAG/LLM Pipeline Test Runner.

Demonstrates the full retrieval-augmented generation and LLM analysis pipeline.
Shows how detected findings are enriched with source code context and analyzed
by a language model for actionable insights.
"""

import os
import sys
import json
from pathlib import Path

sys.path.append(os.path.abspath('.'))

from backend.rag.rag_retriever import retrieve_batch_context
from backend.llm.llm_orchestrator import LLMOrchestrator

# Load environment variables (optional API key)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def run_rag_llm_pipeline(findings_file: str = "reports/dataracebench_analysis.json", 
                         max_findings: int = 3,
                         use_mock: bool = True):
    """Run the RAG/LLM pipeline on saved findings.
    
    Args:
        findings_file: Path to findings JSON file
        max_findings: Number of findings to analyze
        use_mock: If True, use mock LLM responses (no API key needed)
    """
    print("RAG/LLM Pipeline Demo")
    print("=" * 70)
    
    # Load findings
    print(f"\n1. Loading findings from {findings_file}...")
    try:
        with open(findings_file, 'r') as f:
            report = json.load(f)
            findings = report['findings'].get('openmp_races', [])
    except FileNotFoundError:
        print(f"   ERROR: File not found. Run tests/run_dataracebench_detailed.py first.")
        return
    except Exception as e:
        print(f"   ERROR: Failed to load findings: {e}")
        return
    
    if not findings:
        print("   No findings to analyze.")
        return
    
    print(f"   Loaded {len(findings)} high-confidence findings")
    print(f"   Analyzing first {min(max_findings, len(findings))} findings...")
    
    # Retrieve context using RAG
    print(f"\n2. Retrieving source code context (RAG)...")
    findings_subset = findings[:max_findings]
    findings_with_context = retrieve_batch_context(findings_subset, max_findings=max_findings)
    
    for i, finding_ctx in enumerate(findings_with_context, 1):
        print(f"\n   Finding {i}:")
        print(f"   - File: {Path(finding_ctx.get('file', 'N/A')).name}")
        print(f"   - Variable: {finding_ctx.get('variable', 'N/A')}")
        print(f"   - Context retrieved: {len(finding_ctx.get('context_summary', ''))} chars")
    
    # Analyze findings with LLM
    print(f"\n3. Analyzing findings with LLM...")
    
    api_key = os.getenv('OPENAI_API_KEY', '').strip()
    if use_mock or not api_key:
        print("   Using MOCK LLM responses (no API key configured)")
        print("   To use real OpenAI API:")
        print("     1. Set OPENAI_API_KEY in .env file")
        print("     2. Run: pip install openai")
        print("     3. Pass use_mock=False")
        
        # Use mock analysis
        results = generate_mock_analysis(findings_with_context)
    else:
        print(f"   Using OpenAI API (gpt-4)...")
        try:
            orchestrator = LLMOrchestrator(model="gpt-4")
            results = []
            for finding_ctx in findings_with_context:
                result = orchestrator.analyze_finding(
                    finding_ctx,
                    finding_ctx.get('context_summary', ''),
                    use_cache=True
                )
                results.append(result)
                print(f"   ✓ Analyzed: {Path(finding_ctx.get('file', 'N/A')).name}")
        except Exception as e:
            print(f"   ERROR: LLM analysis failed: {e}")
            print(f"   Falling back to mock responses...")
            results = generate_mock_analysis(findings_with_context)
    
    # Display results
    print(f"\n4. Analysis Results:")
    print("=" * 70)
    display_results(results)
    
    # Save results
    output_file = "reports/rag_llm_analysis.json"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    
    print(f"\n✓ Results saved to: {output_file}")


def generate_mock_analysis(findings_with_context):
    """Generate mock LLM analysis for demonstration."""
    mock_responses = [
        {
            "severity": "high",
            "is_real_race": True,
            "explanation": "Variable is accessed in parallel region without synchronization.",
            "impact": "Multiple threads may read/write simultaneously, causing undefined behavior.",
            "reproduction": "Run with multiple threads; race condition may manifest as data corruption.",
            "recommendations": ["Add #pragma omp critical", "Use atomic operations", "Use locks"],
            "confidence_pct": 92,
        },
        {
            "severity": "medium",
            "is_real_race": True,
            "explanation": "Shared variable in reduction context but with additional unguarded accesses.",
            "impact": "Final result may be incorrect; intermediate calculations corrupted.",
            "reproduction": "High thread count increases probability of race manifestation.",
            "recommendations": ["Ensure all accesses are in reduction or critical sections", "Review OpenMP clauses"],
            "confidence_pct": 78,
        },
        {
            "severity": "low",
            "is_real_race": False,
            "explanation": "Variable marked as shared in parallel for loop with implicit barrier.",
            "impact": "Barrier at end of parallel for ensures synchronization before next access.",
            "reproduction": "Not reproducible; implicit barrier prevents race.",
            "recommendations": ["Verify implicit barrier behavior in your OpenMP implementation"],
            "confidence_pct": 65,
        },
    ]
    
    results = []
    for i, finding_ctx in enumerate(findings_with_context):
        mock_response = mock_responses[i % len(mock_responses)]
        results.append({
            'finding': finding_ctx,
            'analysis': mock_response,
            'model': 'mock',
            'source': 'mock_analysis',
        })
    
    return results


def display_results(results):
    """Display analysis results in human-readable format."""
    for i, result in enumerate(results, 1):
        finding = result.get('finding', {})
        analysis = result.get('analysis', {})
        
        print(f"\nFinding #{i}:")
        print(f"  File: {Path(finding.get('file', 'N/A')).name}")
        print(f"  Variable: {finding.get('variable', 'N/A')}")
        print(f"  Line: {finding.get('line', 'N/A')}")
        
        print(f"\n  Analysis:")
        print(f"    Severity: {analysis.get('severity', 'N/A').upper()}")
        print(f"    Is Real Race: {analysis.get('is_real_race', False)}")
        print(f"    Confidence: {analysis.get('confidence_pct', 0)}%")
        
        print(f"\n    Explanation:")
        explanation = analysis.get('explanation', 'N/A')
        for line in explanation.split('\n'):
            if line.strip():
                print(f"      {line}")
        
        print(f"\n    Impact:")
        impact = analysis.get('impact', 'N/A')
        for line in impact.split('\n'):
            if line.strip():
                print(f"      {line}")
        
        print(f"\n    Recommendations:")
        recommendations = analysis.get('recommendations', [])
        if isinstance(recommendations, list):
            for rec in recommendations:
                print(f"      • {rec}")
        else:
            print(f"      {recommendations}")
        
        print("-" * 70)


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Run RAG/LLM pipeline on findings')
    parser.add_argument('--findings', default='reports/dataracebench_analysis.json',
                        help='Path to findings JSON file')
    parser.add_argument('--max-findings', type=int, default=3,
                        help='Maximum number of findings to analyze')
    parser.add_argument('--use-api', action='store_true',
                        help='Use real OpenAI API (requires OPENAI_API_KEY in .env)')
    
    args = parser.parse_args()
    
    run_rag_llm_pipeline(
        findings_file=args.findings,
        max_findings=args.max_findings,
        use_mock=not args.use_api
    )
