# IR Architecture Diagram

## System Architecture with IR as Universal Language

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          CONCURRENCY ANALYSIS PIPELINE                  │
└─────────────────────────────────────────────────────────────────────────┘

                              UNIFIED IR
                         (Universal Language)
                                 │
                ┌────────────────┬────────────────┐
                ▼                ▼                ▼
            ┌────────────┐  ┌────────────┐  ┌───────────┐
            │   PARSER   │  │  NORMALIZER│  │   BUILDER │
            │            │  │            │  │  (Future) │
            │ Input: Raw │  │ Input: Dict│  │ Add nodes │
            │ Code Files │  │ Output: IR │  │  to IR    │
            └────────────┘  └────────────┘  └───────────┘
                │                │
                └────────────────┴─────────────────┐
                                                   │
                                      ┌────────────▼──────────────┐
                                      │     IRRepository         │
                                      │  files[]                 │
                                      │  all_accesses[]          │
                                      │  all_variables[]         │
                                      │  all_threads[]           │
                                      │  all_sync_points[]       │
                                      │  detected_issues[]       │
                                      └────────────┬──────────────┘
                                                   │
                    ┌──────────────────────────────┼──────────────────────────────┐
                    │                              │                              │
                    ▼                              ▼                              ▼
            ┌─────────────────┐          ┌─────────────────┐          ┌─────────────────┐
            │   TIG BUILDER   │          │ STATIC ANALYSIS │          │  RAG RETRIEVER  │
            │                 │          │                 │          │                 │
            │Input: IR        │          │Input: IR        │          │Input: IR Access│
            │  Accesses       │          │  Query:         │          │  Get Context    │
            │  Threads        │          │  unprotected    │          │  Using Thread   │
            │  Sync Points    │          │  concurrent     │          │  & Function     │
            │                 │          │                 │          │                 │
            │Output: Graph    │          │Output: Issues   │          │Output: Context  │
            │  Enhanced with  │          │  (IR objects)   │          │  Snippets       │
            │  IR metadata    │          │                 │          │                 │
            └─────────────────┘          └─────────────────┘          └─────────────────┘
                    │                              │                              │
                    └──────────────────────────────┼──────────────────────────────┘
                                                   │
                                      ┌────────────▼──────────────┐
                                      │  LLM ORCHESTRATOR         │
                                      │                           │
                                      │Input: ConcurrencyIssue    │
                                      │  + RAG Context            │
                                      │                           │
                                      │Output: llm_analysis      │
                                      │  (stored in Issue)        │
                                      └────────────┬──────────────┘
                                                   │
                                                   ▼
                                      ┌────────────────────────────┐
                                      │  REPORT EXPORTER          │
                                      │                           │
                                      │  JSON/CSV with             │
                                      │  Full IR Context           │
                                      └────────────────────────────┘
```

## IR Data Structure Hierarchy

```
IRRepository
│
├── files[]: IRFile[]
│   ├── file_id, file_path, language
│   ├── variables[]: Variable[]
│   │   ├── var_id, name, scope
│   │   └── accesses[]: MemoryAccess[]
│   ├── accesses[]: MemoryAccess[]
│   ├── threads[]: ThreadContext[]
│   └── sync_points[]: SynchronizationPoint[]
│
├── all_accesses[]: MemoryAccess[]
│   ├── access_id, variable_name
│   ├── access_type: READ|WRITE|ATOMIC
│   ├── thread_id: str
│   ├── parallelism_model: OPENMP|PTHREADS
│   ├── synchronization_primitives[]
│   ├── omp_clauses: {shared[], private[], reduction[]}
│   └── confidence: HIGH|MEDIUM|LOW
│
├── all_variables[]: Variable[]
│   ├── var_id, name, scope
│   ├── accesses[]: MemoryAccess[]
│   ├── always_protected: bool
│   └── protection_methods: {locks, critical_sections}
│
├── all_threads[]: ThreadContext[]
│   ├── thread_id, parallelism_model
│   ├── omp_construct: parallel|parallel_for|task
│   ├── parent_thread, child_threads[]
│   └── accesses[]: MemoryAccess[]
│
├── all_sync_points[]: SynchronizationPoint[]
│   ├── sync_id, primitive_type: LOCK|BARRIER|CRITICAL
│   └── acquired_by: thread_id[]
│
└── detected_issues[]: ConcurrencyIssue[]
    ├── issue_id, issue_type: data_race|deadlock
    ├── accesses[]: MemoryAccess[]
    ├── threads_involved[]: ThreadContext[]
    ├── severity, confidence
    ├── is_real_race: bool
    └── llm_analysis: {explanation, recommendations}
```

## Component Integration Flow

```
PARSER → Dictionary Output
  │
  ▼
NORMALIZER → IR Objects
  │
  ├─────────────────────────────────────────┐
  │                                         │
  ▼                                         ▼
TIG BUILDER                         STATIC ANALYSIS
  │ Enhanced nodes/edges            │ Unprotected accesses
  │ IR metadata attached            │ Lock order violations
  │ Confidence scoring              │ Deadlock detection
  │                                 │ OpenMP race detection
  ▼                                 ▼
  TIG Graph ◄─── Analysis Queries ──► ConcurrencyIssue[]
  │                                       │
  │                                       ▼
  │                                   RAG RETRIEVER
  │                                   │ Thread context
  │                                   │ Function scope
  │                                   │ Sync strategy
  │                                   ▼
  │                                   Context Snippets
  │                                       │
  │                                       ▼
  │                                   LLM ORCHESTRATOR
  │                                   │ Verify race
  │                                   │ Recommend fix
  │                                   │ Set is_real_race
  │                                   ▼
  │                                   Issues w/ Analysis
  │                                       │
  └───────────────────────────────────────┘
                  │
                  ▼
          REPORT EXPORTER
          │ JSON with IR context
          │ CSV summary
          │ Visualizations
          ▼
        Reports/Artifacts
```

## Example: From Raw Code to IR to Analysis

### Raw Code
```c
// main.c
int counter = 0;
pthread_mutex_t lock;

void* worker(void* arg) {
    for (int i = 0; i < 1000; i++) {
        counter++;  // Line 8: WRITE, unprotected → RACE!
    }
    return NULL;
}
```

### Parsed Output (Dict)
```python
{
    'path': 'main.c',
    'threads': [{'id': 'worker', 'type': 'pthread'}],
    'shared_variables': ['counter'],
    'locks': [{'name': 'lock', 'type': 'pthread_mutex_t'}],
    'var_writes': ['counter'],
    'omp_pragmas': []
}
```

### IR Objects
```
IRFile(
    variables=[
        Variable(name='counter', accesses=[
            MemoryAccess(
                variable_name='counter',
                access_type=AccessType.WRITE,
                thread_id='thread_1',
                parallelism_model=ParallelismModel.PTHREADS,
                line_number=8,
                held_locks=[],  ← No protection!
                synchronization_primitives=[],
                confidence=ConfidenceLevel.HIGH
            )
        ])
    ],
    threads=[
        ThreadContext(thread_id='thread_1', parallelism_model=PTHREADS)
    ]
)
```

### Static Analysis Query
```python
unprotected = find_unprotected_accesses(ir)
# Returns the WRITE access above

races = find_concurrent_accesses(ir)
# Detects potential race since counter written without sync
```

### ConcurrencyIssue Generated
```
ConcurrencyIssue(
    issue_type='data_race',
    variable=Variable(name='counter'),
    accesses=[
        MemoryAccess(variable_name='counter', access_type=WRITE, thread_id='thread_1', ...),
        MemoryAccess(variable_name='counter', access_type=WRITE, thread_id='thread_2', ...)
    ],
    severity='high',
    confidence=ConfidenceLevel.HIGH
)
```

### LLM Analysis
```python
issue.llm_analysis = {
    'is_real_race': True,
    'explanation': 'Two threads write counter without synchronization',
    'recommendations': ['Use pthread_mutex_lock/unlock around counter++', 'Use atomic operation'],
    'confidence_pct': 95
}
```

### Final Report
```json
{
  "issue_id": "issue_1",
  "issue_type": "data_race",
  "variable": "counter",
  "threads": ["thread_1", "thread_2"],
  "severity": "high",
  "confidence": "HIGH",
  "is_real_race": true,
  "file": "main.c",
  "line": 8,
  "llm_analysis": {
    "explanation": "...",
    "recommendations": ["..."]
  }
}
```

## Benefits Visualization

```
┌─────────────────────────────────────────────────────────┐
│ WITHOUT IR (Current)                                    │
├─────────────────────────────────────────────────────────┤
│ Parser → Dict                                           │
│ ↓ transform → TIG (internal format)                    │
│ ↓ transform → Static Analysis (different format)       │
│ ↓ transform → RAG (yet another format)                 │
│ ↓ transform → LLM (LLM-specific format)                │
│                                                         │
│ ❌ Data loss at each transform                          │
│ ❌ Inconsistent filtering/logic                         │
│ ❌ Hard to debug cross-component issues                 │
│ ❌ Difficult to add new analyses                        │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ WITH IR (New)                                           │
├─────────────────────────────────────────────────────────┤
│ Parser → Dict                                           │
│ ↓ normalize → IR                                        │
│ ↓ query    → TIG (enriched with IR metadata)           │
│ ↓ query    → Static Analysis (IR query results)        │
│ ↓ context  → RAG (IR-aware context extraction)         │
│ ↓ enhance  → LLM (enriched with IR information)        │
│                                                         │
│ ✅ No data loss (all metadata preserved)               │
│ ✅ Consistent data structure across components         │
│ ✅ Easy to debug (inspect IR at any point)             │
│ ✅ Simple to add new analyses (query IR)               │
│ ✅ Better reasoning (cross-component context)          │
│ ✅ Type-safe (dataclasses + enums)                     │
└─────────────────────────────────────────────────────────┘
```
