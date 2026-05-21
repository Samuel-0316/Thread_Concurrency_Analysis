# Thread Concurrency Analysis — Project Overview

> This document is a ground-truth reference derived directly from the codebase.  
> It is intended to help you create an accurate architecture diagram and understand every major component, data flow, and technology choice in the system.

---

## Table of Contents

1. [Project Summary](#1-project-summary)
2. [Repository Layout](#2-repository-layout)
3. [End-to-End Pipeline](#3-end-to-end-pipeline)
4. [Component Deep-Dives](#4-component-deep-dives)
   - [4.1 Parser Service](#41-parser-service)
   - [4.2 Intermediate Representation (IR)](#42-intermediate-representation-ir)
   - [4.3 Thread Interaction Graph (TIG)](#43-thread-interaction-graph-tig)
   - [4.4 Static Analysis Engine](#44-static-analysis-engine)
   - [4.5 Knowledge Graph (KG)](#45-knowledge-graph-kg)
   - [4.6 RAG Retriever](#46-rag-retriever)
   - [4.7 LLM Orchestrator](#47-llm-orchestrator)
   - [4.8 Multi-Agent System](#48-multi-agent-system)
   - [4.9 Fix Generator](#49-fix-generator)
   - [4.10 Report Exporter](#410-report-exporter)
   - [4.11 VS Code Extension](#411-vs-code-extension)
5. [Key Data Structures](#5-key-data-structures)
6. [Data Flow (Detailed)](#6-data-flow-detailed)
7. [Technology Stack](#7-technology-stack)
8. [Configuration & Environment Variables](#8-configuration--environment-variables)
9. [Test & Script Inventory](#9-test--script-inventory)
10. [Benchmarks & Results Artifacts](#10-benchmarks--results-artifacts)

---

## 1. Project Summary

**Thread Concurrency Analysis** is an AI-powered static analysis pipeline that detects data races and other concurrency bugs in **Python** and **C** source files. It combines:

- A regex/AST/Tree-sitter **parser** that extracts threading primitives and variable accesses from source code.
- A **unified Intermediate Representation (IR)** that is the single shared data model for all downstream components.
- A **Thread Interaction Graph (TIG)** backed by NetworkX for graph-based reasoning.
- A rule-based **static analysis engine** (unprotected accesses, lock-order violations, deadlock detection, OpenMP races, loop-carried dependences, data-flow def-use chains, pointer alias analysis).
- A **knowledge graph (KG)** that merges TIG nodes with static-analysis findings.
- A **RAG (Retrieval-Augmented Generation) retriever** that builds context bundles from IR metadata and a knowledge base of concurrency patterns.
- An **LLM Orchestrator** supporting Google Gemini, OpenRouter, and Ollama for semantic race verification and fix recommendations.
- A **multi-agent pipeline** (Analyst → Critic → Resolver) for structured LLM reasoning.
- A **fix generator** that produces concrete source-level patches (OpenMP critical/atomic/reduction, Python `with lock`).
- A **VS Code extension** with an interactive webview that visualises the TIG as a Cytoscape.js graph and shows colour-coded findings.

Primary target: **OpenMP C** parallel programs and **Python threading** programs.  
Benchmark dataset: **DataRaceBench** (micro-benchmarks for OpenMP race detection).

---

## 2. Repository Layout

```
Thread_Concurrency_Analysis/
│
├── backend/                         ← All Python backend logic
│   ├── cli.py                       ← Entry point: parse repo, write JSON
│   ├── parser_service/
│   │   ├── parser.py                ← ParserService (Python AST + C regex + Tree-sitter)
│   │   ├── tree_sitter_c.py         ← TreeSitterCParser wrapper
│   │   └── vendor/lib/              ← Compiled Tree-sitter C grammar (.dll/.so)
│   ├── ir/
│   │   ├── ir_schema.py             ← Legacy v1 normalize() helper
│   │   ├── ir_schema_v2.py          ← Comprehensive IR dataclasses + query functions
│   │   └── ir_normalizer_v2.py      ← IRNormalizer: parser dict → IRRepository
│   ├── tig/
│   │   └── tig_builder.py           ← build_tig_from_ir(): IRRepository → nx.DiGraph
│   ├── static_analysis/
│   │   ├── static_rules.py          ← Core race & lock-order rules
│   │   ├── loop_analysis.py         ← Loop-carried dependence detection
│   │   ├── data_flow.py             ← Def-use chain builder
│   │   └── alias_analysis.py        ← Pointer alias analysis
│   ├── kg/
│   │   └── concurrency_kg.py        ← ConcurrencyKG: TIG + findings → KG
│   ├── rag/
│   │   ├── rag_retriever.py         ← Legacy retriever
│   │   ├── rag_retriever_ir.py      ← IR-aware retriever + make_context_bundle()
│   │   └── knowledge_base/
│   │       ├── patterns.json        ← 10+ concurrency anti-pattern templates
│   │       └── fix_strategies.json  ← Fix strategy descriptions + code templates
│   ├── llm/
│   │   ├── providers.py             ← GeminiProvider, OpenRouterProvider, OllamaProvider
│   │   ├── llm_orchestrator.py      ← LLMOrchestrator.analyze_finding()
│   │   ├── prompt_templates.py      ← build_race_prompt() + DEFAULT_SCHEMA
│   │   ├── enrichment.py            ← IR/TIG fact → dict helpers for prompts
│   │   └── validators.py            ← validate_schema(), verify_claims_against_ir()
│   ├── agent_service/
│   │   ├── agent_base.py            ← Abstract AgentBase
│   │   ├── analyst.py               ← AnalystAgent: LLM or heuristic initial analysis
│   │   ├── critic.py                ← CriticAgent: schema + fact validation
│   │   ├── resolver.py              ← ResolverAgent: reconcile conflicts, optional re-query
│   │   └── orchestrator.py          ← MultiAgentOrchestrator: runs the full agent loop
│   ├── fix_gen/
│   │   ├── fix_generator.py         ← Rule-based FixSuggestion generator
│   │   ├── llm_fix_generator.py     ← LLM-assisted fix generator (Ollama fallback)
│   │   ├── fix_validator.py         ← Validates generated patches
│   │   └── patch_formatter.py       ← Unified-diff formatter
│   └── exporter/
│       ├── report.py                ← export_findings(): JSON + CSV output
│       └── final_report.py          ← generate_human_readable() + export_reports()
│
├── vscode-extension/                ← VS Code extension
│   ├── extension.js                 ← Activation, webview, Python bridge
│   ├── package.json                 ← Extension manifest (name: concurrency-analyzer)
│   └── media/
│       ├── main.js                  ← Webview JS (Cytoscape.js graph, fix UI)
│       └── styles.css               ← Webview styles
│
├── scripts/                         ← Standalone pipeline scripts
│   ├── analyze_file.py              ← End-to-end single-file pipeline (used by extension)
│   ├── analyze_project.py           ← Whole-project analysis
│   ├── run_tig.py                   ← TIG-only runner
│   ├── run_static.py                ← Static-only runner
│   ├── run_rag_llm_pipeline.py      ← RAG+LLM runner
│   ├── run_agent_validation.py      ← Multi-agent validation runner
│   ├── run_exporter.py              ← Exporter runner
│   ├── run_final_benchmark.py       ← Final benchmark runner
│   └── (many more benchmark/debug scripts)
│
├── tests/                           ← Test suite
│   ├── test_ir_schema.py            ← IR data model tests
│   ├── test_tig_from_ir.py          ← TIG construction tests
│   ├── test_static_analysis_ir.py   ← Static rules tests
│   ├── test_rag_retriever_ir.py     ← RAG retriever tests
│   ├── test_pipeline_e2e.py         ← End-to-end pipeline test
│   ├── test_concurrency_kg.py       ← KG tests
│   └── (more test files)
│
├── dev-test/                        ← Hand-crafted sample programs
│   ├── omp_01_reduction.c → omp_08_minmax.c   ← OpenMP C examples
│   └── py_01_counter_race.py → py_03_web_server.py  ← Python threading examples
│
├── docs/
│   ├── architecture/
│   │   ├── IR_ARCHITECTURE.md
│   │   ├── IR_ARCHITECTURE_DIAGRAM.md
│   │   └── IR_INDEX.md
│   └── implementation_notes/
│       ├── IR_IMPLEMENTATION_SUMMARY.md
│       ├── IR_MIGRATION_GUIDE.md
│       ├── STATIC_ANALYSIS_IR_ENHANCEMENT.md
│       ├── TIG_ENRICHMENT_COMPLETE.md
│       └── complete_ai_agent_project_blueprint_thread_safety_analysis.md
│
├── results/                         ← Benchmark/analysis output JSON files
├── reports/                         ← Validation reports
├── requirements.txt                 ← Python dependencies
└── README.md
```

---

## 3. End-to-End Pipeline

The pipeline is a linear sequence of phases. Each phase consumes the unified IR (after Phase 2) and enriches or queries it.

```
┌───────────────────────────────────────────────────────────────────────────────────────┐
│                        THREAD CONCURRENCY ANALYSIS PIPELINE                           │
└───────────────────────────────────────────────────────────────────────────────────────┘

  Source File(s)
  (.py / .c / .h)
       │
       ▼
┌─────────────────┐
│  Phase 1        │  ParserService.parse_file()
│  PARSER         │  • Python: AST-based (ast module)
│                 │  • C: regex + optional Tree-sitter
│                 │  Output: dict {path, language, threads, locks,
│                 │           shared_variables, var_reads, var_writes,
│                 │           omp_pragmas, omp_shared, omp_private, ...}
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Phase 2        │  IRNormalizer.normalize_repository() / normalize_to_ir()
│  IR NORMALIZER  │  • Converts parser dict → IRRepository dataclass tree
│                 │  • Populates: all_accesses, all_variables, all_threads,
│                 │               all_synchronization_points
│                 │  Output: IRRepository (typed, queryable)
└────────┬────────┘
         │
         ├─────────────────────────────────────────────────────────┐
         │                                                         │
         ▼                                                         ▼
┌─────────────────┐                                    ┌─────────────────┐
│  Phase 3        │  build_tig_from_ir(ir)             │  Phase 4        │
│  TIG BUILDER    │  • Nodes: file, var, thread,       │  STATIC RULES   │
│                 │    sync, access                    │                 │
│                 │  • Edges: contains, may_access,    │  run_all_rules() │
│                 │    acquires, accesses_via_lock      │  • find_unsync_accesses()
│                 │  Output: nx.DiGraph                │  • find_lock_order_pairs()
│                 │          (NetworkX directed graph) │  • find_openmp_races()
│                 │                                    │  • loop_analysis
│                 │                                    │  • data_flow (def-use)
│                 │                                    │  • alias_analysis
│                 │                                    │  Output: findings dict
└────────┬────────┘                                    └────────┬────────┘
         │                                                      │
         └──────────────────────┬───────────────────────────────┘
                                │
                                ▼
                   ┌─────────────────────────┐
                   │  (optional) Phase 5     │  ConcurrencyKG.build_from_tig()
                   │  KNOWLEDGE GRAPH (KG)   │  • Imports TIG nodes + edges
                   │                         │  • Adds finding nodes (omp_race,
                   │                         │    data_race, unprotected)
                   │                         │  • Edges: detected_in (finding→var)
                   │                         │  Output: nx.DiGraph (KG)
                   └────────────┬────────────┘
                                │
                                ▼
                   ┌─────────────────────────┐
                   │  Phase 6               │  make_context_bundle(issue, ir, tig)
                   │  RAG RETRIEVER          │  • Score IR chunks by: file match,
                   │                         │    thread match, variable match,
                   │                         │    sync proximity, lock overlap
                   │                         │  • Match knowledge base patterns
                   │                         │  • Attach fix strategies
                   │                         │  Output: context_bundle dict
                   └────────────┬────────────┘
                                │
                                ▼
                   ┌─────────────────────────┐
                   │  Phase 7               │  LLMOrchestrator.analyze_finding()
                   │  LLM ORCHESTRATOR       │  • build_race_prompt(issue, bundle)
                   │                         │  • Call LLM provider (Gemini/OpenRouter/
                   │                         │    Ollama) with structured JSON schema
                   │                         │  • Parse + validate response
                   │                         │  Output: {is_real_race, severity,
                   │                         │    root_cause, runtime_impact,
                   │                         │    recommended_fix, confidence}
                   └────────────┬────────────┘
                                │
                                ▼
                   ┌─────────────────────────┐
                   │  Phase 8               │  MultiAgentOrchestrator.run_on_findings()
                   │  MULTI-AGENT SYSTEM     │
                   │                         │  AnalystAgent
                   │                         │    • Uses LLM (or heuristic fallback)
                   │                         │    • Produces: analysis dict
                   │                         │              ↓
                   │                         │  CriticAgent
                   │                         │    • validate_schema() — required keys
                   │                         │    • verify_claims_against_ir() — facts
                   │                         │              ↓
                   │                         │  ResolverAgent
                   │                         │    • Reconciles analyst vs critic
                   │                         │    • Optional LLM re-query (narrow cases)
                   │                         │  Output: {finding, analyst, critic,
                   │                         │           resolver, context_bundle}
                   └────────────┬────────────┘
                                │
                                ▼
                   ┌─────────────────────────┐
                   │  Phase 9               │  FixGenerator.generate_fixes()
                   │  FIX GENERATOR          │  • Rule-based: critical / atomic /
                   │                         │    reduction / with_lock
                   │                         │  • LLM-assisted (Ollama) fallback
                   │                         │  • patch_formatter → unified diff
                   │                         │  Output: List[FixSuggestion]
                   └────────────┬────────────┘
                                │
                                ▼
                   ┌─────────────────────────┐
                   │  Phase 10              │  export_findings() / export_reports()
                   │  REPORT EXPORTER        │  • high_confidence.json
                   │                         │  • suppressed.json
                   │                         │  • summary.csv
                   │                         │  • <prefix>.json  (agent results)
                   │                         │  • <prefix>.txt   (human-readable)
                   └─────────────────────────┘
                                │
                                ▼
                   ┌─────────────────────────┐
                   │  VS CODE EXTENSION      │  extension.js → analyze_file.py
                   │  (Webview / Cytoscape)  │  • Cytoscape.js interactive TIG
                   │                         │  • Colour-coded nodes (safe/unsafe)
                   │                         │  • Finding list with LLM analysis
                   │                         │  • Apply fix UI (patches file in-place)
                   └─────────────────────────┘
```

---

## 4. Component Deep-Dives

### 4.1 Parser Service

**File:** `backend/parser_service/parser.py`  
**Class:** `ParserService`

Supports `.py`, `.c`, `.h` files. Entry point: `parse_repo(repo_path)` or `parse_file(path)`.

#### Python Parser (`_parse_python`)
- Uses Python's built-in `ast` module.
- Custom `PythonAccessVisitor` walks the AST and records every `ast.Name` node as a read or write, annotated with scope (function name stack).
- Detects:
  - **Thread creation**: `threading.Thread(...)`, `ThreadPoolExecutor`, `multiprocessing.Process`
  - **Lock usage**: `threading.Lock()`, `RLock`, `Semaphore`, `asyncio.Lock`, `with <lock>` context managers
  - **Shared variables**: globals referenced inside thread functions
  - **Variable reads/writes**: scope-aware via the visitor

#### C Parser (`_parse_c`)
- Regex-based extraction of:
  - Global variable declarations
  - `pthread_create`, `pthread_mutex_lock/unlock`, `omp_lock`
  - `#pragma omp` directives (parallel, parallel for, critical, atomic, reduction, single, etc.)
  - OpenMP clauses: `shared(...)`, `private(...)`, `firstprivate(...)`, `lastprivate(...)`, `reduction(...)`
  - Variable read/write accesses via pattern matching
- **Tree-sitter integration** (`tree_sitter_c.py`): optional secondary parser using compiled C grammar from `vendor/lib/`. Extracts the same keys so results can be merged. Falls back gracefully if the library is unavailable.

**Output dict keys:**
```
path, language, threads[], locks[], shared_variables[], var_reads[], var_writes[],
omp_pragmas[], omp_shared[], omp_private[], omp_firstprivate[], omp_lastprivate[],
omp_reduction[], omp_critical_vars[], var_accesses[]
```

---

### 4.2 Intermediate Representation (IR)

**Files:**
- `backend/ir/ir_schema_v2.py` — data model
- `backend/ir/ir_normalizer_v2.py` — parser dict → IR

The IR is the **single shared data structure** for all downstream components. All components consume and produce IR.

#### Core IR Dataclasses

| Class | Purpose |
|---|---|
| `MemoryAccess` | One read/write of one variable by one thread |
| `Variable` | A shared variable with all its accesses |
| `ThreadContext` | A thread or parallel task context (OpenMP / pthreads) |
| `SynchronizationPoint` | A lock, barrier, critical section, atomic, or reduction |
| `ConcurrencyIssue` | A detected race or concurrency bug (populated by static rules + LLM) |
| `IRFile` | All IR entities for one source file |
| `IRRepository` | All IR entities across the entire repo/run |

#### Key Enums

| Enum | Values |
|---|---|
| `AccessType` | `READ`, `WRITE`, `READ_WRITE`, `ATOMIC_READ`, `ATOMIC_WRITE`, `ATOMIC_CAS` |
| `SynchronizationPrimitive` | `LOCK`, `ATOMIC`, `CRITICAL_SECTION`, `BARRIER`, `REDUCTION`, `ORDERED`, `MASTER`, `SINGLE` |
| `ConfidenceLevel` | `HIGH` (>80%), `MEDIUM` (50–80%), `LOW` (<50%), `UNKNOWN` |
| `ParallelismModel` | `OPENMP`, `PTHREADS`, `CUDA`, `SEQUENTIAL` |

#### IRBuilder
Helper class to incrementally construct IR:
- `add_memory_access(variable_name, access_type, file_path, line_number, ...)` → `MemoryAccess`
- `add_variable(name, file_path, ...)` → `Variable`
- `add_thread_context(parallelism_model, ...)` → `ThreadContext`
- `add_synchronization_point(primitive_type, location, ...)` → `SynchronizationPoint`
- `add_concurrency_issue(accesses, issue_type, ...)` → `ConcurrencyIssue`
- `to_json()` → serialized JSON string

#### IRNormalizer
**File:** `backend/ir/ir_normalizer_v2.py`  
**Class:** `IRNormalizer`

Converts parser output dict to `IRRepository`:
- Detects OpenMP thread count from `num_threads(N)` pragma or defaults to 4.
- Creates one `ThreadContext` per OpenMP pragma or pthread.
- Creates one `MemoryAccess` per variable read/write, annotated with thread_id, access type, synchronization primitives, and OMP clauses.
- Creates `SynchronizationPoint` for every lock, critical section, atomic, barrier, and reduction.
- Sets `always_protected` on `Variable` objects when all accesses are protected.

---

### 4.3 Thread Interaction Graph (TIG)

**File:** `backend/tig/tig_builder.py`  
**Function:** `build_tig_from_ir(ir: IRRepository) → nx.DiGraph`

The TIG is a **NetworkX directed graph** where:

#### Node Types

| Type | Node ID format | Key attributes |
|---|---|---|
| `file` | `file:<path>` | `path`, `language` |
| `variable` | `var:<name>` | `scope`, `c_type`, `always_protected`, `protection_methods`, `num_accesses` |
| `thread` | `thread:<id>` | `parallelism_model`, `omp_construct`, `parent_thread`, `num_child_threads` |
| `sync` | `sync:<sync_id>` | `primitive_type`, `location`, `lock_name`, `acquired_by` |
| `access` | `access:<id>` | (inline on edges) |
| `finding` | `finding:<type>_<idx>` | `subtype`, `variable`, `severity` |

#### Edge Types

| Relation | From → To | Metadata |
|---|---|---|
| `contains` | file → var/thread/lock | — |
| `may_access` | thread → var | `access_type`, `synchronization`, `confidence`, `omp_context` |
| `acquires` | thread → lock/sync | — |
| `detected_in` | finding → var | — |

The TIG is also used by the VS Code extension via `build_cytoscape_elements()` in `scripts/analyze_file.py`, which converts it to **Cytoscape.js** node/edge JSON with colour-coded and labelled nodes.

---

### 4.4 Static Analysis Engine

**Files:**
- `backend/static_analysis/static_rules.py` — core rules
- `backend/static_analysis/loop_analysis.py` — loop-carried dependences
- `backend/static_analysis/data_flow.py` — def-use chains
- `backend/static_analysis/alias_analysis.py` — pointer aliasing

#### Core Rules (`static_rules.py`)

| Function | What it detects |
|---|---|
| `find_unsynchronized_accesses(G)` | Thread→var edges where the file has no lock node |
| `find_lock_order_pairs(G)` | Files where locks are acquired in inconsistent orders (deadlock risk) |
| `find_openmp_races(ir)` | OpenMP shared variables written without critical/atomic/reduction protection |
| `find_unprotected_accesses_in_tig(ir)` | IR query: accesses with no sync primitives |
| `find_concurrent_accesses_in_tig(ir)` | IR query: variable accessed by multiple threads where at least one writes |
| `run_all_rules(ir, tig)` | Runs all rules; returns `{unsynchronized_accesses, lock_order_violations, deadlock_cycles, openmp_races, openmp_races_suppressed, data_races, unprotected_accesses}` |

#### Loop Analysis (`loop_analysis.py`)
- Detects `#pragma omp parallel for` loops.
- Extracts array access patterns (variable, index expression, read/write).
- Identifies loop-carried dependences: flow (write→read), anti (read→write), output (write→write).
- Checks whether loop-variable indices provably overlap across iterations.

#### Data-Flow Analysis (`data_flow.py`)
- Builds def-use map: variable name → list of `DefUseEntry` (def/use, line, thread, function, held_locks).
- Identifies `DefUseChain` objects: cross-thread def-use pairs where neither access holds a lock.

#### Alias Analysis (`alias_analysis.py`)
- Extracts `PointerFact` from C source: `p = arr`, `p = &var`, `p = arr + offset`, `p = malloc(...)`.
- Computes `AliasPair` objects (may-alias) with confidence scores to reduce false-positive race reports on different pointer names.

---

### 4.5 Knowledge Graph (KG)

**File:** `backend/kg/concurrency_kg.py`  
**Class:** `ConcurrencyKG`

A lightweight graph backed by NetworkX that **merges TIG + static-analysis findings** into a single queryable structure.

- `build_from_tig(tig_graph, findings)`: imports all TIG nodes and edges, then creates typed finding nodes (`finding:omp_race_N`, `finding:data_race_N`, `finding:unprotected_N`) and connects them to the affected variable nodes via `detected_in` edges.
- Supports JSON persistence via NetworkX node-link format.
- Used as an optional phase between static analysis and RAG/LLM.

---

### 4.6 RAG Retriever

**Files:**
- `backend/rag/rag_retriever_ir.py` — IR-aware retriever (primary)
- `backend/rag/rag_retriever.py` — legacy retriever
- `backend/rag/knowledge_base/patterns.json` — concurrency anti-patterns
- `backend/rag/knowledge_base/fix_strategies.json` — fix strategy descriptions

#### `make_context_bundle(issue, ir, tig)`

Builds the context bundle passed to the LLM prompt. Scoring is **deterministic** (no probabilistic ranking):

| Signal | Score boost |
|---|---|
| Same file as issue | +2.0 |
| Same thread ID | +4.0 |
| Same variable (same file) | +2.0 |
| Same variable (different file) | +0.5 |
| Same function scope | +2.0 |
| Overlapping held locks | +2.0 |
| Sync point in same file | +1.5 |
| Sync point within 10 lines of access | +2.0 |
| Same pragma line | +2.0 |
| Matching construct type (atomic/critical/reduction/barrier) | +1.5 |

The bundle also includes:
- **Knowledge base patterns** matched against the issue type (from `patterns.json`).
- **Fix strategies** from `fix_strategies.json` mapped to the matched patterns.
- **TIG summary**: threads, synchronization relationships.

#### Knowledge Base (`patterns.json`)
Contains patterns such as:
- PAT-001: Unprotected shared write in parallel for
- PAT-002: Shared loop counter modification
- PAT-003: Indirect array access race
- PAT-004: Missing reduction clause
- … (10+ patterns total)

Each pattern includes: `name`, `category`, `language`, `description`, `symptoms`, `code_example`, `fix_strategies`, `severity`, `confidence`.

#### Fix Strategies (`fix_strategies.json`)
- `reduction`: Add OpenMP reduction clause (best performance)
- `atomic`: Add `#pragma omp atomic` (single-statement)
- `critical`: Wrap in `#pragma omp critical`
- `private_clause`: Add private clause
- `declare_inside_loop`: Move declaration inside loop body
- `privatize_array`: Use per-thread arrays + reduction
- `with_lock`: Python `with lock:` context manager

---

### 4.7 LLM Orchestrator

**File:** `backend/llm/llm_orchestrator.py`  
**Class:** `LLMOrchestrator`

#### Provider Selection (`backend/llm/providers.py`)
Controlled by `LLM_PROVIDER` environment variable (`auto`, `gemini`, `openrouter`, `ollama`):

| Provider | Class | Env vars |
|---|---|---|
| Google Gemini | `GeminiProvider` | `GOOGLE_API_KEY`, `GEMINI_MODEL` |
| OpenRouter | `OpenRouterProvider` | `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` |
| Ollama (local) | `OllamaProvider` | `OLLAMA_MODEL`, `OLLAMA_BASE_URL` |

Default model: `gemini-2.5-flash`. Temperature: `0.3`.

All providers implement `BaseLLMProvider.generate_content(contents, generation_config) → ProviderResponse`.

Error classification: `quota_error`, `timeout`, `transport_failure`.

#### `analyze_finding(finding, context_summary, ir, tig)`
1. Builds cache key (file, variable, line).
2. Calls `make_context_bundle()` from RAG retriever.
3. Calls `build_race_prompt(issue, bundle)` → structured prompt with JSON schema instruction.
4. Sends to LLM provider; parses JSON from response (handles markdown code fences).
5. Validates with `validate_schema()` and `verify_claims_against_ir()`.
6. Returns: `{finding, context_bundle, analysis, validation, llm_status}`.

#### Prompt Schema (required LLM output keys)
```json
{
  "is_real_race": <bool>,
  "severity": "critical|high|medium|low",
  "root_cause": "<string>",
  "runtime_impact": "<string>",
  "recommended_fix": "<string>",
  "confidence": <number 0-100>
}
```

#### Validators (`backend/llm/validators.py`)
- `validate_schema(obj)`: checks all 6 required keys exist and types are plausible.
- `verify_claims_against_ir(analysis, finding, ir)`: verifies that any variable or lock names mentioned in the LLM output actually exist in the IR.

---

### 4.8 Multi-Agent System

**Files:** `backend/agent_service/`  
**Orchestrator:** `MultiAgentOrchestrator`

Three-step pipeline per finding:

#### Step 1 — AnalystAgent (`analyst.py`)
- If LLM + IR available: calls `LLMOrchestrator.analyze_finding()`.
- If LLM infrastructure fails (quota/timeout/transport): falls back to **deterministic heuristic** analysis based on `finding.confidence`.
- Output: `{source: 'llm'|'heuristic', analysis: {...}, meta: {...}}`

#### Step 2 — CriticAgent (`critic.py`)
- Calls `validate_schema(analysis)` — checks all required keys.
- Calls `verify_claims_against_ir(analysis, finding, ir)` — verifies facts.
- Output: `{schema_ok, schema_errors, fact_ok, fact_errors}`

#### Step 3 — ResolverAgent (`resolver.py`)
- If schema invalid: creates conservative corrected analysis (is_real_race=False, confidence=0).
- If facts disputed: adjusts confidence downward, notes dispute.
- Optionally re-queries LLM **only** if: schema valid, narrow fact error (`mentioned_variable_not_found`), and original confidence ≥ 80%.
- Output: `{resolved: {...}, notes: [...]}`

#### Infrastructure Failure Fast-Path
If `llm_status` ∈ `{quota_error, transport_failure, timeout}`, the orchestrator bypasses Critic/Resolver entirely and returns a conservative non-race result with `confidence=0.0`.

---

### 4.9 Fix Generator

**Files:** `backend/fix_gen/`

#### Rule-Based Generator (`fix_generator.py`)
**Class:** `FixSuggestion`  
**Fields:** `finding_id`, `strategy`, `description`, `file_path`, `original_lines`, `patched_lines`, `insert_before`, `insert_after`, `confidence`, `validated`

Strategies for **C (OpenMP)**:
1. **`critical`**: Wrap access in `#pragma omp critical(protect_<var>)` block.
2. **`atomic`**: Add `#pragma omp atomic update` before single-statement access.
3. **`reduction`**: Convert shared accumulation to `reduction(<op>:<var>)` clause on the enclosing pragma.

Strategies for **Python**:
1. **`with_lock`**: Wrap access in `with <lock>:` context manager.

#### LLM-Assisted Generator (`llm_fix_generator.py`)
- Uses **Ollama** with model from `OLLAMA_MODEL` env var (default: `qwen2.5-coder:3b`).
- Builds a prompt asking the model to choose fix strategy and return patched code as JSON.
- Falls back to `rule_based_generate_fixes()` if LLM output is invalid.

#### Patch Formatter (`patch_formatter.py`)
- `apply_fix_to_source(lines, fix)` → new list of lines with inserts and replacements applied.
- Also used by the VS Code extension's JavaScript code for in-place file patching.

---

### 4.10 Report Exporter

**Files:** `backend/exporter/report.py`, `backend/exporter/final_report.py`

#### `export_findings(findings, out_dir)`
Produces three output files:
- `high_confidence.json` — unsynchronized_accesses, lock_order_violations, deadlock_cycles, openmp_races
- `suppressed.json` — openmp_races_suppressed
- `summary.csv` — rule name + count per rule

#### `export_reports(orchestrator_result, out_prefix)`
Produces two files from multi-agent results:
- `<prefix>.json` — full structured JSON (finding + analyst + critic + resolver per finding)
- `<prefix>.txt` — human-readable text report: title, analyst confidence, critic verdict, resolver is_real_race + recommended_fix

---

### 4.11 VS Code Extension

**Files:** `vscode-extension/extension.js`, `vscode-extension/media/main.js`, `vscode-extension/media/styles.css`  
**Package name:** `concurrency-analyzer`  
**Command:** `concurrencyAnalyzer.open` → "Concurrency Analyzer: Open Panel"  
**Min VS Code version:** 1.70.0

#### Activation Flow
1. User runs command `Concurrency Analyzer: Open Panel`.
2. Extension creates a `WebviewPanel` with `enableScripts: true`.
3. Extension loads `.env` file from repo root to inject API keys.
4. Active editor path is sent to webview via `postMessage`.

#### Analysis Trigger
When the webview sends `analyzeFile`:
1. Extension resolves Python executable (`.venv/Scripts/python.exe` → `.venv/bin/python` → `python3`).
2. Runs `scripts/analyze_file.py --json [--llm] [--quick] <file_path>` as a child process.
3. Stdout is the JSON result; stderr is logged to the output channel.
4. Timeout: 5 min with LLM, 2 min without.

#### Webview (`media/main.js`)
- Receives JSON result from extension host.
- Renders **Cytoscape.js** interactive graph of TIG nodes and edges.
- **Node colours:**
  - Thread: `#1f77b4` (blue)
  - Variable (safe/protected): `#81c784` (green)
  - Variable (unsafe): `#ffb74d` (orange)
  - Sync point: `#2ca02c` (green)
  - Finding: `#d62728` (red), triangle shape
  - File: `#9467bd` (purple), round-rectangle shape
- Clause labels shown below variable nodes (e.g., "reduction(+)", "firstprivate").
- Sidebar shows findings list with LLM analysis and recommended fix.
- "Apply Fix" button: sends `applyFix` message back to extension which patches the file in-place.
- Modes: **quick** (static only, no LLM) and **full** (with LLM analysis).

#### Fix Application (`extension.js`)
- `mergeFixMaps(fixes)`: merges patched_lines, insert_before, insert_after from multiple fixes (skips conflicting patches at same line).
- `applyFixesToFile(filePath, fixes)`: reads file, applies merged fixes, writes back.
- `applyFullFileFix(filePath, content)`: replaces file content wholesale (used for LLM-generated full-file patches).

---

## 5. Key Data Structures

### IRRepository (top-level IR object)
```
IRRepository
├── repo_id: str
├── repo_path: str
├── files: List[IRFile]
├── all_accesses: List[MemoryAccess]
├── all_variables: List[Variable]
├── all_threads: List[ThreadContext]
├── all_synchronization_points: List[SynchronizationPoint]
└── detected_issues: List[ConcurrencyIssue]
```

### MemoryAccess (most critical object)
```
MemoryAccess
├── access_id: str                          # unique ID e.g. "access_1"
├── variable_name: str
├── access_type: AccessType                 # READ|WRITE|READ_WRITE|ATOMIC_*
├── file_path: str
├── line_number: int
├── column_number: int
├── function_name: str
├── scope_level: int                        # 0=global, 1=file, 2=function, 3+=nested
├── thread_id: Optional[str]               # e.g. "omp_parallel_for_1"
├── parallelism_model: ParallelismModel    # OPENMP|PTHREADS|CUDA|SEQUENTIAL
├── parallel_construct: str                # "parallel_for", "parallel", "critical"
├── held_locks: List[str]
├── synchronization_primitives: List[SynchronizationPrimitive]
├── in_critical_section: bool
├── in_reduction: bool
├── omp_clauses: Dict[str, List[str]]      # {shared:[], private:[], reduction:[]}
├── omp_pragma_line: Optional[int]
├── reduction_operator: Optional[str]
├── confidence: ConfidenceLevel
├── reason: str
└── source: str                            # "parser"|"tree-sitter"|"manual"
```

### ConcurrencyIssue
```
ConcurrencyIssue
├── issue_id: str
├── issue_type: str                        # "data_race"|"lock_order_violation"|"deadlock"
├── accesses: List[MemoryAccess]
├── variable: Optional[Variable]
├── threads_involved: List[ThreadContext]
├── severity: str                          # "low"|"medium"|"high"|"critical"
├── confidence: ConfidenceLevel
├── is_real_race: Optional[bool]          # set by LLM analysis
├── file_path: str
├── primary_line: int
├── reason: str
├── llm_analysis: Optional[Dict]          # {is_real_race, severity, root_cause, ...}
└── recommendations: List[str]
```

### FixSuggestion
```
FixSuggestion
├── finding_id: str
├── strategy: str                          # "critical"|"atomic"|"reduction"|"with_lock"
├── description: str
├── file_path: str
├── original_lines: Dict[int, str]        # line_number → original content
├── patched_lines: Dict[int, str]         # line_number → patched content
├── insert_before: Dict[int, str]         # line_number → text to insert before
├── insert_after: Dict[int, str]          # line_number → text to insert after
├── confidence: float                      # 0.0 – 1.0
├── validated: bool
└── validation_result: Optional[str]
```

---

## 6. Data Flow (Detailed)

```
Source code file(s) (.py / .c / .h)
        │
        │ read by ParserService
        ▼
Parser dict {path, language, threads, locks, shared_variables,
              var_reads, var_writes, omp_pragmas, omp_shared,
              omp_private, omp_firstprivate, omp_lastprivate,
              omp_reduction, omp_critical_vars, var_accesses}
        │
        │ IRNormalizer.normalize_repository()
        ▼
IRRepository ◄──────────────────────────────────────────────────────────┐
  (typed, queryable)                                                      │
  all_accesses[]    ─────────────────────────────────────────────────┐   │
  all_variables[]   ──────────────────┐                              │   │
  all_threads[]     ────────────────┐ │                              │   │
  all_sync_points[] ──────────────┐ │ │                              │   │
                                  │ │ │                              │   │
        │                         │ │ │                              │   │
        │ build_tig_from_ir()     │ │ │                              │   │
        ▼                         │ │ │                              │   │
  nx.DiGraph (TIG)                │ │ │    run_all_rules()           │   │
  Nodes: file, var,               │ │ │      ├── find_unsync()  ─────┘   │
    thread, sync,                 │ │ │      ├── find_lock_order()       │
    finding                       │ │ │      ├── find_openmp_races() ────┘
  Edges: contains,                │ │ │      ├── loop_analysis()
    may_access,                   │ │ │      ├── data_flow()
    acquires                      │ │ │      └── alias_analysis()
        │                         │ │ │              │
        │                         │ │ │              ▼
        │                     findings dict:
        │                     {unsynchronized_accesses,
        │                      lock_order_violations,
        │                      deadlock_cycles,
        │                      openmp_races,
        │                      data_races, ...}
        │                              │
        └──────────────────────────────┤
                                       │
                                       │ (optional)
                                       │ ConcurrencyKG.build_from_tig()
                                       ▼
                                  nx.DiGraph (KG)
                                  TIG nodes + finding nodes
                                       │
                                       │ per finding:
                                       │ make_context_bundle(issue, ir, tig)
                                       ▼
                                  context_bundle {
                                    chunks[]: scored IR snippets,
                                    tig_summary: {...},
                                    knowledge_base: {
                                      matched_patterns[],
                                      fix_strategies[]
                                    }
                                  }
                                       │
                                       │ build_race_prompt(issue, bundle)
                                       ▼
                                  LLM prompt (structured JSON instruction)
                                       │
                                       │ GeminiProvider / OpenRouterProvider
                                       │ / OllamaProvider
                                       ▼
                                  LLM JSON response {
                                    is_real_race, severity,
                                    root_cause, runtime_impact,
                                    recommended_fix, confidence
                                  }
                                       │
                                       │ validate_schema() + verify_claims_against_ir()
                                       ▼
                              MultiAgentOrchestrator
                                 AnalystAgent → analysis dict
                                 CriticAgent  → {schema_ok, fact_ok, ...}
                                 ResolverAgent → {resolved, notes}
                                       │
                                       │ FixGenerator.generate_fixes()
                                       ▼
                              List[FixSuggestion]
                              (patched_lines, insert_before, insert_after)
                                       │
                                       │ export_findings() / export_reports()
                                       ▼
                              high_confidence.json
                              suppressed.json
                              summary.csv
                              <prefix>.json / <prefix>.txt
                                       │
                                       │ (via analyze_file.py)
                                       ▼
                              Cytoscape.js JSON (elements[])
                                       │
                              VS Code Webview (Cytoscape.js graph)
```

---

## 7. Technology Stack

| Layer | Technology | Version / Notes |
|---|---|---|
| Language | Python | 3.x |
| Graph library | NetworkX | In-memory directed graphs |
| Python AST parser | `ast` (stdlib) | For Python source files |
| C parser | Regex + Tree-sitter | Tree-sitter optional; requires compiled vendor libs |
| Tree-sitter binding | `tree_sitter` package | C grammar from `vendor/lib/` |
| LLM — primary | Google Gemini | `gemini-2.5-flash` default; via `google-generativeai` |
| LLM — alternative | OpenRouter | Any model; via HTTPS API |
| LLM — local | Ollama | `qwen2.5-coder:3b` default; via local HTTP |
| HTTP client | `requests` | For OpenRouter and Ollama providers |
| Environment config | `python-dotenv` | `.env` file loading |
| VS Code extension | Node.js / JavaScript | VS Code API 1.70+ |
| Graph visualisation | Cytoscape.js | Loaded in webview |
| Benchmark dataset | DataRaceBench | OpenMP micro-benchmark suite |
| Output formats | JSON, CSV, unified diff | — |
| Dependencies | `requirements.txt` | `tree_sitter`, `networkx`, `requests`, `python-dotenv`, `google-generativeai` |

---

## 8. Configuration & Environment Variables

| Variable | Used by | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `providers.py` | `auto` / `gemini` / `openrouter` / `ollama` |
| `GOOGLE_API_KEY` / `GEMINI_API_KEY` | `GeminiProvider` | Gemini API authentication |
| `GEMINI_MODEL` | `LLMOrchestrator` | Override default Gemini model |
| `OPENROUTER_API_KEY` | `OpenRouterProvider` | OpenRouter API key |
| `OPENROUTER_MODEL` | `LLMOrchestrator` | Override OpenRouter model |
| `OLLAMA_MODEL` | `OllamaProvider`, `llm_fix_generator.py` | Local Ollama model name |
| `OLLAMA_BASE_URL` | `OllamaProvider` | Ollama API base URL (default: `http://localhost:11434`) |

The VS Code extension reads a `.env` file from the workspace root and injects these into the child process environment.

---

## 9. Test & Script Inventory

### Tests (`tests/`)

| File | What it tests |
|---|---|
| `test_ir_schema.py` | IR dataclasses, IRBuilder, query functions |
| `test_tig_from_ir.py` | TIG node/edge construction from IR |
| `test_static_analysis_ir.py` | Static rules applied to IR |
| `test_static_analysis_dataracebench_ir.py` | Static rules on DataRaceBench files |
| `test_rag_retriever_ir.py` | RAG scoring, context bundle generation |
| `test_tig_dataracebench_ir.py` | TIG on DataRaceBench |
| `test_llm_orchestrator_integration.py` | LLM orchestrator integration |
| `test_concurrency_kg.py` | ConcurrencyKG build and query |
| `test_pipeline_e2e.py` | Full pipeline end-to-end |
| `run_tests.py` | Test runner script |

### Scripts (`scripts/`)

| File | Purpose |
|---|---|
| `analyze_file.py` | **Primary**: end-to-end single-file analysis (used by VS Code extension) |
| `analyze_project.py` | Whole-project analysis |
| `run_tig.py` | TIG-only run |
| `run_static.py` / `run_static_extended.py` | Static analysis run |
| `run_rag_llm_pipeline.py` | RAG + LLM analysis |
| `run_agent_validation.py` | Multi-agent validation |
| `run_exporter.py` | Export findings to files |
| `run_final_benchmark.py` | Final benchmark run |
| `run_dataracebench_detailed.py` / `run_on_dataracebench.py` | DataRaceBench batch analysis |
| `batch_dataracebench_analysis.py` | Batch DataRaceBench with multiple configs |
| `compare_benchmarks.py` | Compare benchmark results |
| `validate_*.py` | Various validation scripts |
| `enrich_results.py` | Post-process results with LLM enrichment |
| `report_validation.py` | Validate generated reports |

---

## 10. Benchmarks & Results Artifacts

All results are stored in `results/`:

| File | Contents |
|---|---|
| `dataracebench_full_results.json` | Full DataRaceBench analysis results |
| `dataracebench_full_results_ir.json` | Results using IR-based pipeline |
| `final_benchmark.json` / `.txt` | Final benchmark summary |
| `final_benchmark_kg.json` | Benchmark with KG phase enabled |
| `e2e_pipeline_results.json` | End-to-end pipeline test results |
| `agent_validation_results.json` | Multi-agent validation output |
| `static_analysis_ir_sample.json` | Sample static analysis on IR |
| `tig_ir_sample.json` | Sample TIG from IR |
| `rag_llm_analysis.json` | RAG + LLM analysis sample |
| `ir_sample.json` | Sample IR JSON |
| `benchmark_comparison.csv` | Side-by-side benchmark comparison |
| `plots/` | Benchmark visualisation plots |

`results/dataracebench_manual/` contains per-file ground-truth annotations used during validation.

`reports/benchmark_validation_206.json` — validation results for the 206-file benchmark subset.

---

## Architecture Diagram Summary (for drawing)

The following nodes and their connections are the minimum needed for an accurate architecture diagram:

**Nodes (boxes):**
1. Source Code Files (Input)
2. ParserService (Python AST + C Regex + Tree-sitter)
3. IRNormalizer → IRRepository (Central data hub)
4. TIG Builder → NetworkX DiGraph
5. Static Analysis Engine (Rules + Loop + DataFlow + Alias)
6. ConcurrencyKG (optional KG layer)
7. RAG Retriever + Knowledge Base
8. LLM Orchestrator (Gemini / OpenRouter / Ollama)
9. Multi-Agent System (Analyst → Critic → Resolver)
10. Fix Generator (Rule-based + LLM-assisted)
11. Report Exporter (JSON + CSV + Text)
12. VS Code Extension + Cytoscape.js Webview (Output UI)

**Key data flows (arrows):**
- Source Files → ParserService → dict
- dict → IRNormalizer → IRRepository
- IRRepository → TIG Builder → TIG graph
- IRRepository → Static Analysis Engine → findings dict
- TIG + findings → ConcurrencyKG
- IRRepository + TIG → RAG Retriever → context_bundle
- context_bundle → LLM Orchestrator → LLM analysis
- LLM analysis → Multi-Agent System → validated results
- validated results → Fix Generator → FixSuggestions
- everything → Report Exporter → JSON/CSV/text files
- analyze_file.py (Phases 1–9) → Cytoscape JSON → VS Code Webview
- VS Code Webview → "Apply Fix" → modified source files
