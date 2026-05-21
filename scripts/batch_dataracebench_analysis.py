#!/usr/bin/env python3
"""Batch analysis of DataRaceBench with LLM - FULL DATASET (206 FILES)

This script:
1. Discovers all .c files in DataRaceBench (206 files)
2. Extracts ground truth (filename pattern: *-yes.c = has race, *-no.c = no race)
3. Parses and detects OpenMP pragmas
4. Runs static analysis using parser metadata
5. Uses the configured LLM provider to analyze findings
6. Collects comprehensive metrics
7. Generates JSON report
"""

import os
import sys
import json
import glob
import time
from pathlib import Path
from typing import List, Dict
from dataclasses import dataclass, asdict
from collections import defaultdict

sys.path.insert(0, os.path.abspath('.'))

from backend.parser_service.parser import ParserService
from backend.ir.ir_normalizer_v2 import normalize_to_ir
from backend.llm.llm_orchestrator import LLMOrchestrator
from backend.tig.tig_builder import build_tig_from_ir
from backend.static_analysis.static_rules import find_openmp_races

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


@dataclass
class TestResult:
    """Result of analyzing one file."""
    file_path: str
    ground_truth: bool
    static_found_races: int
    llm_analyses: List[Dict]
    errors: List[str]
    
    def to_dict(self):
        return asdict(self)


class BatchAnalyzer:
    """Batch analyzer for full DataRaceBench dataset."""
    
    def __init__(self, dataracebench_dir: str = "datasets/dataracebench/micro-benchmarks"):
        self.dataracebench_dir = dataracebench_dir
        self.parser = ParserService()
        provider = os.getenv('LLM_PROVIDER', 'auto').lower()
        model_name = os.getenv('LLM_MODEL', 'inclusionai/ring-2.6-1t:free')
        if provider == 'openrouter':
            model_name = os.getenv('OPENROUTER_MODEL', model_name)
        elif provider == 'ollama':
            model_name = os.getenv('OLLAMA_MODEL', model_name)
        elif provider == 'gemini':
            model_name = os.getenv('GEMINI_MODEL', model_name)
        self.model = model_name
        self.orchestrator = LLMOrchestrator(model=self.model)
        self.results: List[TestResult] = []
        self.last_api_call = 0
        self.min_request_interval = 1.0
        self.use_llm = bool(os.getenv('OPENROUTER_API_KEY') or os.getenv('GOOGLE_API_KEY'))
        
    def discover_files(self) -> List[str]:
        """Find all .c files in DataRaceBench."""
        pattern = os.path.join(self.dataracebench_dir, "**/*.c")
        files = glob.glob(pattern, recursive=True)
        return sorted(files)
    
    def extract_ground_truth(self, filepath: str) -> bool:
        """Extract ground truth from filename."""
        basename = os.path.basename(filepath).lower()
        if '-yes' in basename:
            return True
        elif '-no' in basename:
            return False
        return None
    
    def analyze_file(self, filepath: str) -> TestResult:
        """Analyze a single DataRaceBench file."""
        ground_truth = self.extract_ground_truth(filepath)
        result = TestResult(
            file_path=filepath,
            ground_truth=ground_truth,
            static_found_races=0,
            llm_analyses=[],
            errors=[]
        )
        
        try:
            # Parse file
            parsed = self.parser.parse_file(filepath)
            if not parsed:
                result.errors.append("Failed to parse")
                return result
            
            # Use parser-based OpenMP heuristic with false-positive reduction
            openmp_result = find_openmp_races([parsed])
            findings = list(openmp_result.get('findings', []))
            result.static_found_races = len(findings)

            if findings and self.use_llm:
                ir = normalize_to_ir([parsed], repo_path=os.path.dirname(filepath))
                tig = build_tig_from_ir(ir)
                
                for finding_dict in findings[:3]:
                    try:
                        # Rate limiting
                        now = time.time()
                        if now - self.last_api_call < self.min_request_interval:
                            time.sleep(self.min_request_interval - (now - self.last_api_call))
                        
                        class PseudoFinding:
                            pass
                        
                        finding = PseudoFinding()
                        finding.issue_id = f"race_{finding_dict['variable']}"
                        finding.issue_type = 'omp_data_race'
                        finding.variable = type('obj', (object,), {'name': finding_dict['variable']})()
                        finding.file_path = filepath
                        finding.primary_line = 0
                        
                        llm_result = self.orchestrator.analyze_finding(finding, ir=ir, tig=tig)
                        self.last_api_call = time.time()
                        
                        analysis = llm_result.get('analysis', {})
                        result.llm_analyses.append({
                            'variable': finding_dict['variable'],
                            'is_real_race': analysis.get('is_real_race'),
                            'severity': analysis.get('severity'),
                            'confidence': analysis.get('confidence'),
                            'schema_ok': llm_result.get('validation', {}).get('schema_ok'),
                            'heuristic_confidence': finding_dict.get('confidence'),
                            'heuristic_notes': finding_dict.get('notes', []),
                        })
                    except Exception as e:
                        result.errors.append(f"LLM failed: {str(e)[:50]}")
            elif findings and not self.use_llm:
                for finding in findings[:3]:
                    issue = finding
                    result.llm_analyses.append({
                        'variable': issue.variable.name if issue.variable else '',
                        'is_real_race': None,
                        'severity': issue.severity,
                        'confidence': None,
                        'schema_ok': None,
                        'issue_type': issue.issue_type,
                        'heuristic_confidence': getattr(issue, 'confidence', None).value if getattr(issue, 'confidence', None) else None,
                        'heuristic_reason': issue.reason,
                    })
        
        except Exception as e:
            result.errors.append(f"Analysis failed: {str(e)[:50]}")
        
        return result
    
    def run_batch(self, max_files: int = None) -> List[TestResult]:
        """Run batch analysis on all files."""
        files = self.discover_files()
        
        if max_files:
            files = files[:max_files]
        
        print(f"\n{'='*80}")
        print(f"DataRaceBench Full Batch Analysis (206 Files)")
        print(f"{'='*80}")
        print(f"Total files: {len(files)}")
        if self.use_llm:
            print(f"Model: {self.model}")
            print(f"Rate limit: 1 sec between LLM calls")
            print(f"Estimated duration: ~{len(files)} minutes")
        else:
            print(f"Model: disabled (OPENROUTER_API_KEY / GOOGLE_API_KEY not set)")
            print(f"Mode: static-only IR benchmark")
            print(f"Estimated duration: ~{len(files) // 2} minutes")
        print(f"{'='*80}\n")
        
        start_time = time.time()
        
        for i, filepath in enumerate(files, 1):
            rel_path = os.path.relpath(filepath)
            gt = self.extract_ground_truth(filepath)
            print(f"[{i:3d}/{len(files)}] {rel_path[:60]:60s} ", end='', flush=True)
            
            result = self.analyze_file(filepath)
            self.results.append(result)
            
            if result.errors:
                print(f"ERR")
            else:
                print(f"OK ({result.static_found_races}R,{len(result.llm_analyses)}A)")
        
        elapsed = time.time() - start_time
        print(f"\nCompleted in {elapsed:.0f} seconds ({elapsed/60:.1f} minutes)")
        return self.results
    
    def generate_report(self, output_file: str = "reports/dataracebench_full_results_final.json"):
        """Generate comprehensive report."""
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        
        metrics = {
            'total_files': len(self.results),
            'with_ground_truth': sum(1 for r in self.results if r.ground_truth is not None),
            'expected_races': sum(1 for r in self.results if r.ground_truth is True),
            'expected_no_races': sum(1 for r in self.results if r.ground_truth is False),
            'detection_rate': 100.0 * sum(1 for r in self.results if r.ground_truth is True and r.static_found_races > 0) / max(1, sum(1 for r in self.results if r.ground_truth is True)),
            'total_races_found': sum(r.static_found_races for r in self.results),
            'files_with_races': sum(1 for r in self.results if r.static_found_races > 0),
            'llm_findings': sum(len(r.llm_analyses) for r in self.results),
            'schema_pass_rate': 100.0 * sum(1 for r in self.results for a in r.llm_analyses if a.get('schema_ok')) / max(1, sum(len(r.llm_analyses) for r in self.results)),
        }
        
        report = {
            'model': self.model,
            'llm_enabled': self.use_llm,
            'analysis_mode': 'parser_heuristic_v2',
            'metrics': metrics,
            'sample': [r.to_dict() for r in self.results[:50]],
        }
        
        with open(output_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        print(f"\n{'='*80}")
        print("FULL DATASET RESULTS")
        print(f"{'='*80}")
        print(f"Files analyzed: {metrics['total_files']}/206")
        print(f"Expected races: {metrics['expected_races']}")
        print(f"Detection rate: {metrics['detection_rate']:.1f}%")
        print(f"Total races found: {metrics['total_races_found']}")
        print(f"LLM findings: {metrics['llm_findings']}")
        print(f"Schema pass rate: {metrics['schema_pass_rate']:.1f}%")
        print(f"Report: {output_file}")
        print(f"{'='*80}\n")


def main():
    analyzer = BatchAnalyzer()
    analyzer.run_batch()
    analyzer.generate_report()


if __name__ == '__main__':
    main()
