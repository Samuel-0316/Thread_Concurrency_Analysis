import json
import os
from typing import Dict, Any, List


def generate_human_readable(result: Dict[str, Any]) -> str:
    lines: List[str] = []
    res = result.get('results', [])
    lines.append(f"Multi-Agent Validation Report — findings: {len(res)}")
    lines.append('')
    for i, r in enumerate(res, 1):
        f = r.get('finding', {})
        analyst = r.get('analyst', {})
        resolver = r.get('resolver', {})
        verdict = r.get('critic', {})

        title = f.get('file', '<unknown>') + ':' + str(f.get('variable', '<var>'))
        lines.append(f"{i}. {title}")
        lines.append(f"   - Analyst source: {analyst.get('source')}")
        a_analysis = analyst.get('analysis', {})
        lines.append(f"   - Analyst confidence: {a_analysis.get('confidence', 'n/a')}")
        lines.append(f"   - Critic schema_ok: {verdict.get('schema_ok')}, fact_ok: {verdict.get('fact_ok')}")
        resolved = resolver.get('resolved') or {}
        lines.append(f"   - Resolved is_real_race: {resolved.get('is_real_race')}, severity: {resolved.get('severity')}")
        lines.append(f"   - Recommended fix: {resolved.get('recommended_fix')}")
        lines.append('')

    return '\n'.join(lines)


def export_reports(orchestrator_result: Dict[str, Any], out_prefix: str) -> Dict[str, str]:
    os.makedirs(os.path.dirname(out_prefix), exist_ok=True)
    json_path = f"{out_prefix}.json"
    text_path = f"{out_prefix}.txt"

    with open(json_path, 'w', encoding='utf-8') as fh:
        json.dump(orchestrator_result, fh, indent=2)

    human = generate_human_readable(orchestrator_result)
    with open(text_path, 'w', encoding='utf-8') as fh:
        fh.write(human)

    return {'json': json_path, 'text': text_path}


if __name__ == '__main__':
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else 'reports/agent_validation_results.json'
    out_prefix = sys.argv[2] if len(sys.argv) > 2 else 'reports/agent_validation_report'
    with open(path, 'r', encoding='utf-8') as fh:
        data = json.load(fh)
    exported = export_reports(data, out_prefix)
    print(json.dumps(exported, indent=2))
