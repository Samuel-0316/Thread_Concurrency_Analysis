# Comprehensive IR Architecture Guide

## Overview

The **Intermediate Representation (IR)** is the universal language for concurrency analysis in this system. All components consume and produce IR, ensuring consistency and enabling powerful cross-component reasoning.

## Why IR is Critical

**Before IR (fragile):**
```
Parser → Output A
       ↓ (reshape)
    TIG ← Output B
       ↓ (reshape)
Static Rules ← Output C
       ↓ (reshape)
    RAG/LLM ← Output D
```

**With IR (robust & scalable):**
```
Parser → IR ← Normalized representation
       ↓
      TIG → Enriched graph with IR metadata
       ↓
Static Rules → Query IR directly
       ↓
    RAG/LLM → Context-aware analysis
```

## Core IR Concepts

### 1. MemoryAccess
Represents a single read/write operation on a variable.

**Key Fields:**
- `variable_name`: What is being accessed?
- `access_type`: READ, WRITE, READ_WRITE, ATOMIC_*, etc.
- `thread_id`: Which thread/parallel construct?
- `file_path`, `line_number`: Where in code?
- `synchronization_primitives`: What guards this access? [LOCK, CRITICAL_SECTION, ATOMIC, etc.]
- `omp_clauses`: OpenMP context (shared, private, reduction, etc.)
- `confidence`: HIGH, MEDIUM, LOW, UNKNOWN

**Example:**
```python
MemoryAccess(
    access_id="access_42",
    variable_name="sum",
    access_type=AccessType.WRITE,
    thread_id="omp_parallel_for_1",
    parallelism_model=ParallelismModel.OPENMP,
    parallel_construct="parallel_for",
    synchronization_primitives=[],  # Unprotected!
    file_path="main.c",
    line_number=42,
    confidence=ConfidenceLevel.HIGH
)
```

### 2. Variable
Represents a shared variable.

**Key Fields:**
- `name`: Variable name
- `scope`: global, file-local, function-local
- `accesses`: List of MemoryAccess objects to this variable
- `protection_methods`: Set of synchronization mechanisms protecting it
- `always_protected`: Boolean indicating if all accesses are protected

### 3. ThreadContext
Represents a thread or parallel task.

**Key Fields:**
- `thread_id`: Unique identifier
- `parallelism_model`: OPENMP, PTHREADS, CUDA, SEQUENTIAL
- `omp_construct`: parallel, parallel_for, task, etc. (if OpenMP)
- `accesses`: List of memory accesses by this thread
- `parent_thread`, `child_threads`: Thread hierarchy

### 4. SynchronizationPoint
Represents a lock, barrier, critical section, or other sync mechanism.

**Key Fields:**
- `primitive_type`: LOCK, ATOMIC, CRITICAL_SECTION, BARRIER, REDUCTION, etc.
- `location`: file:line format
- `acquired_by`: List of thread IDs that acquire this lock

### 5. ConcurrencyIssue
Represents a detected race condition or concurrency problem.

**Key Fields:**
- `issue_type`: data_race, lock_order_violation, deadlock, etc.
- `accesses`: List of conflicting MemoryAccess objects
- `threads_involved`: Threads in conflict
- `severity`: low, medium, high, critical
- `confidence`: ConfidenceLevel
- `is_real_race`: True/False (set after LLM analysis)
- `llm_analysis`: Dict with LLM results

## IR Usage by Component

### Parser → IR

**Current (simple):**
```python
parsed = parser.parse_file("main.c")
# {path, language, threads, locks, shared_variables, var_reads, var_writes, ...}
```

**Future (IR-first):**
```python
parsed = parser.parse_file("main.c")
ir = normalize_to_ir([parsed])
# Now use ir.all_accesses, ir.all_variables, ir.all_threads, etc.
```

### IR → TIG (Enriched)

**Current TIG:**
```
Nodes: file, variable, thread, lock
Edges: contains, may_access, acquires
```

**Enriched TIG (using IR):**
```
Nodes: file, variable, thread, lock, access_pattern
Edges with metadata:
  - may_access(var, thread)
    - weight: MemoryAccess.confidence
    - access_type: READ, WRITE, READ_WRITE
    - synchronization: SynchronizationPrimitive list
    - omp_context: Dict of OpenMP clauses
```

### IR → Static Analysis Rules

**Example: Find Unprotected Accesses**
```python
from backend.ir.ir_schema_v2 import find_unprotected_accesses

unprotected = find_unprotected_accesses(ir)
# Direct query: no need to filter/transform
for access in unprotected:
    if access.access_type in [AccessType.WRITE, AccessType.READ_WRITE]:
        report_race(access)
```

**Example: Find Lock Order Violations**
```python
from backend.ir.ir_schema_v2 import find_concurrent_accesses

for thread in ir.all_threads:
    locks_acquired = extract_lock_order(thread, ir)
    check_lock_order_consistency(locks_acquired, ir)
```

### IR → RAG Retriever

**Enhanced Context:**
```python
access = ir.all_accesses[0]

# Use IR to get better context
context = rag_retriever.get_file_context(
    access.file_path,
    access.line_number,
    context_lines=5
)

# IR tells us which thread/pragma context to highlight
print(f"In {access.parallel_construct} context")
print(f"Thread: {access.thread_id}")
print(f"Protected by: {access.synchronization_primitives}")
```

### IR → LLM Orchestrator

**Better Prompts:**
```python
finding = ir.detected_issues[0]
context = rag_retriever.summarize_finding_context(finding)

prompt = f"""
Data race detected:
- Variable: {finding.variable.name}
- Accesses by threads: {[a.thread_id for a in finding.accesses]}
- Synchronization: {finding.accesses[0].synchronization_primitives}
- OpenMP Context: {finding.accesses[0].omp_clauses}

Code:
{context}

Analysis: Is this a real race?
"""

result = llm_orchestrator.analyze(prompt)
```

## IR API Reference

### IRBuilder (for constructing IR)

```python
builder = IRBuilder(repo_id="repo_1", repo_path=".")

# Add accesses
access = builder.add_memory_access(
    variable_name="sum",
    access_type=AccessType.WRITE,
    file_path="main.c",
    line_number=42,
    thread_id="omp_1",
    parallelism_model=ParallelismModel.OPENMP
)

# Add variables
var = builder.add_variable(
    name="sum",
    file_path="main.c",
    scope="global"
)

# Add threads
thread = builder.add_thread_context(
    parallelism_model=ParallelismModel.OPENMP,
    omp_construct="parallel_for"
)

# Add sync points
sync = builder.add_synchronization_point(
    primitive_type=SynchronizationPrimitive.LOCK,
    location="main.c:10",
    lock_name="mutex_1"
)

# Query
ir = builder.get_ir()
```

### Query Functions

```python
from backend.ir.ir_schema_v2 import (
    find_variable_by_name,
    find_accesses_for_variable,
    find_unprotected_accesses,
    find_concurrent_accesses
)

# Find a variable
var = find_variable_by_name(ir, "sum")

# Find all accesses to a variable
accesses = find_accesses_for_variable(ir, var)

# Find accesses without synchronization
unprotected = find_unprotected_accesses(ir)

# Find potential race pairs
races = find_concurrent_accesses(ir)
```

## Migration Path

### Phase 1: IR as Complement (Current)
- IR exists alongside parser output
- Components can use either
- Tests verify IR correctness

### Phase 2: IR Primary (Next)
- Parser output normalized to IR immediately
- All downstream components consume IR
- Legacy paths removed

### Phase 3: IR-Only (Future)
- All components think in terms of IR
- Easier to add new analysis types
- Cleaner APIs

## Benefits of IR Architecture

✅ **Consistency**: All components see same data structure  
✅ **Extensibility**: New analyses can be added without pipeline changes  
✅ **Reasoning**: Components can query/combine information easily  
✅ **Debugging**: IR can be inspected at any point  
✅ **Testing**: IR serves as contract between components  
✅ **Documentation**: IR schema documents data flow  
✅ **Performance**: IR can be cached/serialized  
✅ **Scalability**: Works with large repositories  

## Files

| File | Purpose |
|------|---------|
| `backend/ir/ir_schema_v2.py` | IR data classes and query functions |
| `backend/ir/ir_normalizer_v2.py` | Parser output → IR conversion |
| `tests/test_ir_schema.py` | IR schema validation tests |

## Future Work

- [ ] Persist IR to database (instead of in-memory)
- [ ] Version IR schema as analyses evolve
- [ ] Create IR diff/merge for incremental analysis
- [ ] Add IR visualization tools
- [ ] Optimize IR for large repositories
- [ ] Support additional languages via IR
