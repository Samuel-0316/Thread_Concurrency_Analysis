import os
import json
import csv
from datetime import datetime


def export_findings(findings, out_dir):
    os.makedirs(out_dir, exist_ok=True)

    high_keys = [
        "unsynchronized_accesses",
        "lock_order_violations",
        "deadlock_cycles",
        "openmp_races",
    ]
    suppressed_keys = ["openmp_races_suppressed"]

    high = {k: findings.get(k, []) for k in high_keys if findings.get(k)}
    suppressed = {k: findings.get(k, []) for k in suppressed_keys if findings.get(k)}

    high_path = os.path.join(out_dir, "high_confidence.json")
    suppressed_path = os.path.join(out_dir, "suppressed.json")
    with open(high_path, "w", encoding="utf-8") as f:
        json.dump(high, f, indent=2)
    with open(suppressed_path, "w", encoding="utf-8") as f:
        json.dump(suppressed, f, indent=2)

    summary_path = os.path.join(out_dir, "summary.csv")
    rows = []
    for k in high_keys + suppressed_keys:
        v = findings.get(k)
        count = len(v) if isinstance(v, list) else (v if isinstance(v, int) else (0 if v is None else 1))
        rows.append((k, count))

    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rule", "count"])
        for r in rows:
            w.writerow(r)

    return {
        "high": high_path,
        "suppressed": suppressed_path,
        "summary": summary_path,
    }


if __name__ == "__main__":
    import sys
    from backend.parser_service.parser import ParserService
    from backend.ir.ir_schema import normalize
    from backend.tig.tig_builder import build_tig
    from backend.static_analysis.static_rules import run_all_rules

    repo = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.abspath("datasets/dataracebench")
    svc = ParserService()
    files = [
        os.path.join(r, n)
        for r, _, ns in os.walk(os.path.join(repo, "micro-benchmarks"))
        for n in ns
        if n.lower().endswith('.c')
    ]
    parsed = [svc.parse_file(p) for p in files]
    parsed = [p for p in parsed if p]
    G = build_tig(normalize(parsed))
    findings = run_all_rules(G, parsed)

    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out_dir = os.path.join("reports", f"dataracebench_{ts}")
    paths = export_findings(findings, out_dir)
    print(json.dumps({"out_dir": out_dir, "paths": paths}, indent=2))
