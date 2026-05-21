"""RAG (Retrieval-Augmented Generation) module for finding context.

Provides retrieval of source code snippets and metadata for detected findings.
Used to augment LLM prompts with relevant code context.
"""

import os
from pathlib import Path
from typing import Dict, List, Optional


class RAGRetriever:
    """Retrieves source code context for concurrency findings."""

    def __init__(self, repo_path: str = ""):
        self.repo_path = repo_path
        self.source_cache = {}

    def get_file_context(self, file_path: str, line_number: Optional[int] = None, context_lines: int = 5) -> Dict:
        """Retrieve source code context for a finding.
        
        Args:
            file_path: Absolute or relative path to source file
            line_number: Line number of the finding (1-indexed)
            context_lines: Number of lines to include before/after
            
        Returns:
            Dict with 'source', 'start_line', 'end_line', 'content'
        """
        # Cache source file if not already loaded
        if file_path not in self.source_cache:
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    self.source_cache[file_path] = f.readlines()
            except Exception as e:
                return {'error': f'Could not read file: {e}', 'source': file_path}
        
        lines = self.source_cache[file_path]
        
        if line_number is None or line_number < 1:
            # No specific line; return function signature + a few lines
            return {
                'source': file_path,
                'line': None,
                'content': ''.join(lines[:20]),  # First 20 lines
                'start_line': 1,
                'end_line': min(20, len(lines)),
            }
        
        # Get context around the line
        start = max(0, line_number - 1 - context_lines)
        end = min(len(lines), line_number + context_lines)
        
        context_content = ''.join(lines[start:end])
        
        return {
            'source': file_path,
            'line': line_number,
            'start_line': start + 1,
            'end_line': end,
            'content': context_content,
            'target_line': lines[line_number - 1].rstrip() if line_number <= len(lines) else 'N/A',
        }

    def get_function_containing_line(self, file_path: str, line_number: int) -> Optional[str]:
        """Get the function definition that contains the given line (heuristic).
        
        For C files: looks backwards for 'type name(' pattern.
        For Python files: looks for 'def name(' pattern.
        """
        if file_path not in self.source_cache:
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    self.source_cache[file_path] = f.readlines()
            except Exception:
                return None
        
        lines = self.source_cache[file_path]
        if line_number > len(lines):
            return None
        
        # Look backwards for function definition
        for i in range(line_number - 1, -1, -1):
            line = lines[i].strip()
            if '(' in line and '{' in lines[i]:
                # Found likely function def
                return line[:100]  # Return first 100 chars of function sig
        
        return None

    def get_variable_usage_context(self, file_path: str, variable_name: str, max_occurrences: int = 5) -> List[Dict]:
        """Find all usages of a variable and return context for each."""
        if file_path not in self.source_cache:
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    self.source_cache[file_path] = f.readlines()
            except Exception:
                return []
        
        lines = self.source_cache[file_path]
        results = []
        
        for line_num, line_content in enumerate(lines, 1):
            if variable_name in line_content and len(results) < max_occurrences:
                results.append({
                    'line_number': line_num,
                    'line_content': line_content.rstrip(),
                    'context': self.get_file_context(file_path, line_num, context_lines=2),
                })
        
        return results

    def summarize_finding_context(self, finding: Dict) -> str:
        """Create a human-readable summary of context for a finding."""
        file_path = finding.get('file', '')
        variable = finding.get('variable', 'unknown')
        line_num = finding.get('line', None)
        omp_kind = finding.get('omp_kind', 'unknown')
        
        context = self.get_file_context(file_path, line_num, context_lines=3)
        
        summary = f"""
Finding Summary:
  File: {Path(file_path).name}
  Variable: {variable}
  OpenMP Context: {omp_kind}
  Line: {line_num}
  
Source Code Context:
{context.get('content', 'N/A')}

Function Containing Finding:
{self.get_function_containing_line(file_path, line_num or 1) or 'N/A'}
"""
        return summary


def retrieve_batch_context(findings: List[Dict], repo_path: str = "", max_findings: int = 10) -> List[Dict]:
    """Retrieve context for a batch of findings.
    
    Args:
        findings: List of finding dicts from static analysis
        repo_path: Path to source repository
        max_findings: Maximum number of findings to process
        
    Returns:
        List of findings with added 'context_summary' field
    """
    retriever = RAGRetriever(repo_path)
    results = []
    
    for finding in findings[:max_findings]:
        context_summary = retriever.summarize_finding_context(finding)
        results.append({
            **finding,
            'context_summary': context_summary,
        })
    
    return results
