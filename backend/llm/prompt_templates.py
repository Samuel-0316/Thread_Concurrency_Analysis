"""Prompt template helpers for concurrency reasoning.

Templates are designed to be explicit and to instruct the LLM to return
strict JSON that matches the schema. Keep prompts short and include
context bundles from the retriever.
"""
from typing import List, Dict, Any


DEFAULT_SCHEMA = {
    'required_keys': ['is_real_race', 'severity', 'root_cause', 'runtime_impact', 'recommended_fix', 'confidence'],
}


def build_race_prompt(issue: Any, context_bundle: Dict[str, Any], schema: Dict[str, Any] = DEFAULT_SCHEMA) -> str:
    """Construct a prompt asking the LLM to analyze the issue and return JSON.

    The prompt instructs the model to only output JSON matching the keys in schema['required_keys'].
    """
    preamble = f"You are an expert static concurrency analyst. Analyze the following concurrency issue and return ONLY a JSON object with the required keys: {schema['required_keys']}.\n"

    # brief structured context
    chunks = context_bundle.get('chunks', [])
    top_ctx = "\n".join([f"- {c['text']} (score={c['score']:.2f})" for c in chunks[:6]])

    tig = context_bundle.get('tig_summary', {})
    tig_text = f"Threads: {tig.get('threads', [])}; Relationships: {tig.get('relationships', [])}"

    instruction = (
        "Please reason deterministically using ONLY the provided context. "
        "Do NOT hallucinate additional facts. Do not return markdown, comments, or trailing text. "
        "If information is missing, set 'is_real_race' to false and explain what's missing in 'root_cause'. "
        "Never use null or omit any required key.\n"
    )

    schema_note = (
        "Output schema (JSON):\n"
        "{\n"
        "  \"is_real_race\": <bool>,\n"
        "  \"severity\": <'critical'|'high'|'medium'|'low'>,\n"
        "  \"root_cause\": <string>,\n"
        "  \"runtime_impact\": <string>,\n"
        "  \"recommended_fix\": <string>,\n"
        "  \"confidence\": <number 0-100>\n"
        "}\n"
        "Use empty strings or 0 only if a value cannot be determined from context.\n"
    )

    prompt = (
        preamble
        + "\nCONTEXT (top prioritized chunks):\n"
        + top_ctx
        + "\n\nTIG SUMMARY:\n"
        + tig_text
    )

    # Include knowledge base patterns if available
    kb = context_bundle.get('knowledge_base', {})
    kb_patterns = kb.get('matched_patterns', [])
    kb_strategies = kb.get('fix_strategies', [])

    if kb_patterns:
        prompt += "\n\nKNOWN BUG PATTERNS (from curated knowledge base):\n"
        for pat in kb_patterns[:3]:
            prompt += (
                f"- [{pat.get('id')}] {pat.get('name')}: {pat.get('description', '')[:200]}\n"
                f"  Severity: {pat.get('severity')}, Fix strategies: {pat.get('fix_strategies')}\n"
            )

    if kb_strategies:
        prompt += "\nRECOMMENDED FIX STRATEGIES:\n"
        for strat in kb_strategies[:4]:
            prompt += (
                f"- {strat.get('name', strat.get('id'))}: {strat.get('description', '')[:150]}\n"
            )

    prompt += (
        "\n\nINSTRUCTION:\n"
        + instruction
        + "\n" + schema_note
        + "\nReturn only the JSON object with no surrounding commentary."
    )
    return prompt
