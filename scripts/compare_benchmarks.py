#!/usr/bin/env python3
"""Compare DataRaceBench benchmark JSON reports and generate plots/tables.

Outputs:
- reports/benchmark_comparison.csv
- reports/plots/detection_rate.png
- reports/plots/schema_pass_rate.png
- reports/plots/llm_findings.png
- reports/plots/runtime_seconds.png
"""
import json
import glob
import os
import csv
from datetime import datetime

try:
    import matplotlib.pyplot as plt
except Exception:
    plt = None


REPORTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'reports')
REPORTS_DIR = os.path.normpath(REPORTS_DIR)
PLOTS_DIR = os.path.join(REPORTS_DIR, 'plots')
os.makedirs(PLOTS_DIR, exist_ok=True)


def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def safe_get(d, candidates):
    for k in candidates:
        if isinstance(d, dict) and k in d:
            return d[k]
    return None


def extract_metrics(path):
    data = load_json(path)
    # flatten top-level if nested under 'summary' or similar
    if isinstance(data, dict) and len(data) == 1:
        sole = next(iter(data.values()))
        if isinstance(sole, dict):
            data = {**data, **sole}

    metrics = {}
    metrics['report'] = os.path.basename(path)
    metrics['mtime'] = datetime.fromtimestamp(os.path.getmtime(path)).isoformat()

    metrics['runtime_seconds'] = safe_get(data, ['runtime_seconds', 'runtime', 'duration', 'elapsed_seconds'])
    metrics['files_analyzed'] = safe_get(data, ['files_analyzed', 'files', 'num_files'])
    metrics['expected_races'] = safe_get(data, ['expected_races', 'expected', 'ground_truth_races'])
    metrics['total_races_found'] = safe_get(data, ['total_races_found', 'races_found', 'total_found', 'total_races'])
    metrics['llm_findings'] = safe_get(data, ['llm_findings', 'llm_findings_count', 'llm_findings_total', 'llm_findings'])
    metrics['schema_pass_rate'] = safe_get(data, ['schema_pass_rate', 'schema_pass_pct', 'schema_pass'])
    # Agent validation derived metrics (if report is agent_validation_results.json or contains 'results')
    metrics['infrastructure_failures'] = None
    metrics['schema_failures'] = None
    metrics['semantic_failures'] = None
    metrics['successful_analyses'] = None
    metrics['grounded_count'] = None
    try:
        results = data.get('results') if isinstance(data, dict) else None
        if isinstance(results, list):
            infra = 0
            schema_fail = 0
            semantic_fail = 0
            success = 0
            for r in results:
                # Analyst meta usually contains llm_status and semantic flag
                analyst = r.get('analyst', {}) or {}
                meta = analyst.get('meta', {}) or {}
                llm_status = meta.get('llm_status')
                if llm_status in ('quota_error', 'transport_failure', 'timeout'):
                    infra += 1
                    continue
                if llm_status == 'schema_failure' or (meta.get('schema_ok') is False and llm_status == 'success'):
                    schema_fail += 1
                    # If llm_status==success but schema invalid, count as semantic failure
                    if llm_status == 'success':
                        semantic_fail += 1
                    continue
                if llm_status == 'success' or meta.get('schema_ok') is True:
                    # treat as success when schema passed
                    success += 1
            metrics['infrastructure_failures'] = infra
            metrics['schema_failures'] = schema_fail
            metrics['semantic_failures'] = semantic_fail
            metrics['successful_analyses'] = success
            metrics['grounded_count'] = sum(1 for r in results if r.get('grounded') is True)
    except Exception:
        pass
    # detection_rate may already exist
    dr = safe_get(data, ['detection_rate', 'detection_rate_pct'])
    if dr is not None:
        metrics['detection_rate_pct'] = float(dr)
    else:
        # compute if possible
        er = metrics.get('expected_races')
        tf = metrics.get('total_races_found')
        try:
            if er and tf:
                metrics['detection_rate_pct'] = float(tf) / float(er) * 100.0
            else:
                metrics['detection_rate_pct'] = None
        except Exception:
            metrics['detection_rate_pct'] = None

    # normalize numeric types
    for k in ['runtime_seconds', 'files_analyzed', 'expected_races', 'total_races_found', 'llm_findings', 'schema_pass_rate', 'detection_rate_pct']:
        v = metrics.get(k)
        if isinstance(v, str):
            try:
                metrics[k] = float(v)
            except Exception:
                metrics[k] = None

    return metrics


def main():
    pattern = os.path.join(REPORTS_DIR, 'dataracebench_full_results*.json')
    files = sorted(glob.glob(pattern))
    if not files:
        pattern2 = os.path.join(REPORTS_DIR, '*.json')
        files = sorted(glob.glob(pattern2))

    rows = []
    for p in files:
        try:
            rows.append(extract_metrics(p))
        except Exception as e:
            print('Skipping', p, 'due to', e)

    if not rows:
        print('No report files found to compare.')
        return

    csv_path = os.path.join(REPORTS_DIR, 'benchmark_comparison.csv')
    fieldnames = ['report', 'mtime', 'runtime_seconds', 'files_analyzed', 'expected_races', 'total_races_found', 'detection_rate_pct', 'llm_findings', 'schema_pass_rate']
    with open(csv_path, 'w', newline='', encoding='utf-8') as cf:
        w = csv.DictWriter(cf, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fieldnames})

    print('Wrote CSV:', csv_path)

    if plt is None:
        print('matplotlib not available; skipping plots.')
        return

    # Prepare plotting
    labels = [r['report'] for r in rows]
    x = range(len(labels))

    def save_bar(values, title, ylabel, out_name):
        fig, ax = plt.subplots(figsize=(max(6, len(labels)*0.6), 4))
        ax.bar(x, values, color='C0')
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=30, ha='right')
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        fig.tight_layout()
        out = os.path.join(PLOTS_DIR, out_name)
        fig.savefig(out)
        plt.close(fig)
        print('Saved plot:', out)

    # detection rate
    det = [r.get('detection_rate_pct') or 0.0 for r in rows]
    save_bar(det, 'Detection Rate (%)', 'Percent', 'detection_rate.png')

    # schema pass rate
    schema = [r.get('schema_pass_rate') or 0.0 for r in rows]
    save_bar(schema, 'Schema Pass Rate', 'Percent', 'schema_pass_rate.png')

    # llm findings
    llm = [r.get('llm_findings') or 0.0 for r in rows]
    save_bar(llm, 'LLM Findings (count)', 'Count', 'llm_findings.png')

    # runtime seconds
    runtime = [r.get('runtime_seconds') or 0.0 for r in rows]
    save_bar(runtime, 'Runtime (seconds)', 'Seconds', 'runtime_seconds.png')


if __name__ == '__main__':
    main()
