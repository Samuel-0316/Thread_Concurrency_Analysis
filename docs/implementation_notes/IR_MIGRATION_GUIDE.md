# IR Integration Migration Guide

## Overview
This guide shows how to update each component to use the comprehensive IR schema.

## 1. Parser Service Integration

### Current Code
```python
def parse_repo(self, repo_path):
    results = []
    for filepath in glob.glob(f"{repo_path}/**/*.[cpy]", recursive=True):
        result = self.parse_file(filepath)
        if result:
            results.append(result)
    return results
```

### Updated Code
```python
from backend.ir.ir_normalizer_v2 import normalize_to_ir

def parse_repo_to_ir(self, repo_path):
    """Parse repository and return IR."""
    results = []
    for filepath in glob.glob(f"{repo_path}/**/*.[cpy]", recursive=True):
        result = self.parse_file(filepath)
        if result:
            results.append(result)
    
    # Normalize to IR
    return normalize_to_ir(results, repo_path)
```

### Usage
```python
# Old way
parsed = parser.parse_repo(".")
tig = build_tig(normalized_parse_output)

# New way
ir = parser.parse_repo_to_ir(".")
tig = build_tig_from_ir(ir)
```

---

## 2. TIG Builder Enhancement

### Current Code
```python
def build_tig(normalized):
    G = nx.DiGraph()
    for file_dict in normalized:
        file_node = f"file:{file_dict['path']}"
        G.add_node(file_node, type='file')
        
        for var in file_dict.get('shared_variables', []):
            var_node = f"var:{var}"
            G.add_node(var_node, type='variable')
            G.add_edge(file_node, var_node, label='contains')
```

### Updated Code
```python
from backend.ir.ir_schema_v2 import IRRepository

def build_tig_from_ir(ir: IRRepository):
    """Build TIG using enriched IR metadata."""
    G = nx.DiGraph()
    
    # Add file nodes
    for ir_file in ir.files:
        file_node = f"file:{ir_file.file_path}"
        G.add_node(file_node, type='file')
    
    # Add variable nodes with metadata
    for var in ir.all_variables:
        var_node = f"var:{var.name}"
        G.add_node(var_node, type='variable', 
                   scope=var.scope,
                   always_protected=var.always_protected,
                   protection_methods=list(var.protection_methods))
    
    # Add access edges with IR context
    for access in ir.all_accesses:
        if access.thread_id:
            thread_node = f"thread:{access.thread_id}"
            G.add_node(thread_node, type='thread', 
                      parallelism_model=access.parallelism_model.value)
            
            # Edge with enriched metadata
            G.add_edge(thread_node, f"var:{access.variable_name}",
                      access_type=access.access_type.value,
                      synchronization=[s.value for s in access.synchronization_primitives],
                      confidence=access.confidence.value,
                      omp_context=access.omp_clauses)
    
    # Add synchronization nodes
    for sync in ir.all_synchronization_points:
        sync_node = f"sync:{sync.sync_id}"
        G.add_node(sync_node, type='sync',
                  primitive=sync.primitive_type.value)
        
        # Connect to threads that use it
        for thread_id in sync.acquired_by:
            G.add_edge(f"thread:{thread_id}", sync_node, label='acquires')
    
    return G
```

### Usage
```python
# Enrich TIG queries
print(f"Variable protection: {G.nodes[var_node]['protection_methods']}")
print(f"Access type: {G[thread_node][var_node]['access_type']}")
print(f"Is protected: {G.nodes[var_node]['always_protected']}")
```

---

## 3. Static Analysis Rules

### Current Code
```python
def find_unsynchronized_accesses(G):
    races = []
    # Parse graph manually, lots of filtering
    for edge in G.edges():
        if edge[0].startswith('thread:') and edge[1].startswith('var:'):
            races.append(edge)
    return races
```

### Updated Code
```python
from backend.ir.ir_schema_v2 import find_unprotected_accesses, find_concurrent_accesses

def find_races_from_ir(ir: IRRepository):
    """Find data races using IR queries."""
    races = []
    
    # Query 1: Unprotected accesses
    unprotected = find_unprotected_accesses(ir)
    
    for access in unprotected:
        if access.access_type in [AccessType.WRITE, AccessType.READ_WRITE]:
            # Check for concurrent accesses
            concurrent = find_concurrent_accesses(ir)
            for a1, a2 in concurrent:
                if a1.variable_name == access.variable_name:
                    issue = ir.builder.add_concurrency_issue(
                        accesses=[a1, a2],
                        issue_type="data_race",
                        variable=find_variable_by_name(ir, a1.variable_name),
                        threads_involved=[
                            find_thread_by_id(ir, a1.thread_id),
                            find_thread_by_id(ir, a2.thread_id)
                        ],
                        severity="high",
                        confidence=ConfidenceLevel.HIGH
                    )
                    races.append(issue)
    
    return races
```

### Benefits
- Direct IR queries instead of graph parsing
- Type-safe access to metadata
- Easier to express complex conditions

---

## 4. RAG Retriever Enhancement

### Current Code
```python
def get_variable_usage_context(self, file_path, variable_name):
    with open(file_path) as f:
        lines = f.readlines()
    
    # Simple line-by-line scan
    usages = []
    for i, line in enumerate(lines):
        if variable_name in line:
            usages.append((i, line))
    return usages
```

### Updated Code
```python
from backend.ir.ir_schema_v2 import MemoryAccess

def get_variable_usage_context_from_ir(self, access: MemoryAccess):
    """Get context using IR metadata."""
    file_path = access.file_path
    var_name = access.variable_name
    line_number = access.line_number
    
    with open(file_path) as f:
        lines = f.readlines()
    
    context = {
        'variable': var_name,
        'access_type': access.access_type.value,
        'line': line_number,
        'code': ''.join(lines[max(0, line_number-3):line_number+3]),
        'thread_context': access.thread_id,
        'parallelism': access.parallelism_model.value,
        'parallel_construct': access.parallel_construct,
        'synchronization': [s.value for s in access.synchronization_primitives],
        'omp_clauses': access.omp_clauses,
    }
    
    return context
```

### Benefits
- Richer context with synchronization information
- Precise line numbers from IR
- OpenMP clause awareness

---

## 5. LLM Orchestrator Enhancement

### Current Code
```python
def analyze_finding(self, finding):
    prompt = f"""
    Possible data race on variable: {finding['variable']}
    Analyze if this is a real race.
    """
    return llm.analyze(prompt)
```

### Updated Code
```python
from backend.ir.ir_schema_v2 import ConcurrencyIssue

def analyze_issue_from_ir(self, issue: ConcurrencyIssue, 
                         rag_context: Dict):
    """Analyze concurrency issue using IR."""
    
    # Build rich prompt from IR
    prompt = f"""
    Concurrency Issue: {issue.issue_type}
    Severity: {issue.severity}
    Confidence: {issue.confidence.value}
    
    Variable: {issue.variable.name if issue.variable else 'unknown'}
    Accesses:
    """
    
    for access in issue.accesses:
        prompt += f"""
        - Thread: {access.thread_id}
        - Type: {access.access_type.value}
        - Parallelism: {access.parallelism_model.value}
        - Construct: {access.parallel_construct}
        - Protection: {[s.value for s in access.synchronization_primitives]}
        - OpenMP: {access.omp_clauses}
        """
    
    prompt += f"""
    Code Context:
    {rag_context['code']}
    
    Analysis:
    1. Is this a real race?
    2. What synchronization would fix it?
    3. How confident are you?
    """
    
    result = self.llm.analyze(prompt)
    
    # Store result back in IR
    issue.llm_analysis = {
        'is_real_race': result['is_real_race'],
        'explanation': result['explanation'],
        'recommendations': result['recommendations'],
        'confidence_pct': result['confidence_pct']
    }
    issue.is_real_race = result['is_real_race']
    
    return issue
```

### Benefits
- Precise issue representation
- Thread/synchronization context in prompt
- OpenMP clause awareness
- Results stored back in IR

---

## 6. Integration Test

### New Test
```python
# tests/test_ir_integration.py

from backend.parser_service.parser import ParserService
from backend.ir.ir_normalizer_v2 import normalize_to_ir
from backend.tig.tig_builder import build_tig_from_ir
from backend.static_analysis.static_rules import find_races_from_ir
from backend.rag.rag_retriever import RAGRetriever
from backend.llm.llm_orchestrator import LLMOrchestrator

def test_full_ir_pipeline():
    """Test IR as universal language through full pipeline."""
    
    # 1. Parse to IR
    parser = ParserService()
    parsed = parser.parse_repo("tests/")
    ir = normalize_to_ir(parsed, repo_path="tests/")
    
    # 2. Build enriched TIG from IR
    tig = build_tig_from_ir(ir)
    assert len(tig.nodes()) > 0
    
    # 3. Find races using IR queries
    races = find_races_from_ir(ir)
    
    # 4. Get RAG context using IR
    rag = RAGRetriever()
    if races:
        for race in races:
            for access in race.accesses:
                context = rag.get_variable_usage_context_from_ir(access)
                assert context['thread_context'] is not None
    
    # 5. Analyze with LLM using IR
    llm = LLMOrchestrator(use_mock=True)
    for race in races[:1]:  # Test one
        analyzed = llm.analyze_issue_from_ir(race, context)
        assert analyzed.is_real_race is not None
    
    print("✓ Full IR pipeline works end-to-end")

if __name__ == '__main__':
    test_full_ir_pipeline()
```

---

## Implementation Roadmap

### Week 1: Foundation
- ✅ Create ir_schema_v2.py
- ✅ Create ir_normalizer_v2.py
- ✅ Create test_ir_schema.py
- [ ] Create this migration guide

### Week 2: Integration (Priority)
- [ ] Update TIG builder to use IR
- [ ] Update static rules to use IR
- [ ] Create integration tests

### Week 3: Enhancement
- [ ] Update RAG to use IR
- [ ] Update LLM to use IR
- [ ] End-to-end validation

### Week 4: Polish
- [ ] Remove legacy code paths
- [ ] Add IR visualization
- [ ] Document for users

---

## Quick Start

To start using IR in your component:

```python
from backend.ir.ir_schema_v2 import (
    IRRepository, MemoryAccess, Variable, ThreadContext,
    AccessType, SynchronizationPrimitive, ConfidenceLevel,
    find_unprotected_accesses, find_concurrent_accesses
)

def my_analysis(ir: IRRepository):
    """My analysis that uses IR."""
    
    # Query IR
    for access in ir.all_accesses:
        print(f"Access: {access.variable_name} by {access.thread_id}")
    
    # Use helper functions
    unprotected = find_unprotected_accesses(ir)
    concurrent = find_concurrent_accesses(ir)
    
    # Add issues back to IR
    for a1, a2 in concurrent:
        ir.builder.add_concurrency_issue(
            accesses=[a1, a2],
            issue_type="data_race"
        )
```

That's it! You now have:
- Type safety
- Consistency with other components
- Full metadata context
- Queryable results
