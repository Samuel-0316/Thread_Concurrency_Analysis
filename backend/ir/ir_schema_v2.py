"""Comprehensive Intermediate Representation (IR) for Concurrency Analysis.

The IR is the universal language for representing all concurrency-related
information in the system. All components consume and produce IR:
- Parser → IR
- TIG uses IR metadata
- Static analysis consumes IR
- RAG/LLM enhanced by IR
- Future agents reason about IR

This ensures consistency and scalability across the pipeline.
"""

from enum import Enum
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, asdict
import json


class AccessType(Enum):
    """Type of memory access."""
    READ = "READ"
    WRITE = "WRITE"
    READ_WRITE = "READ_WRITE"
    ATOMIC_READ = "ATOMIC_READ"
    ATOMIC_WRITE = "ATOMIC_WRITE"
    ATOMIC_CAS = "ATOMIC_CAS"


class SynchronizationPrimitive(Enum):
    """Type of synchronization mechanism."""
    LOCK = "LOCK"  # pthread_mutex, omp_lock
    ATOMIC = "ATOMIC"  # atomic operation
    CRITICAL_SECTION = "CRITICAL_SECTION"  # #pragma omp critical
    BARRIER = "BARRIER"  # #pragma omp barrier
    REDUCTION = "REDUCTION"  # #pragma omp reduction
    ORDERED = "ORDERED"  # #pragma omp ordered
    MASTER = "MASTER"  # #pragma omp master
    SINGLE = "SINGLE"  # #pragma omp single


class ConfidenceLevel(Enum):
    """Confidence in analysis findings."""
    HIGH = "HIGH"  # > 80%
    MEDIUM = "MEDIUM"  # 50-80%
    LOW = "LOW"  # < 50%
    UNKNOWN = "UNKNOWN"


class ParallelismModel(Enum):
    """Type of parallelism."""
    OPENMP = "OPENMP"
    PTHREADS = "PTHREADS"
    CUDA = "CUDA"
    SEQUENTIAL = "SEQUENTIAL"


@dataclass
class MemoryAccess:
    """Represents a single memory access to a variable."""
    
    access_id: str  # Unique identifier
    variable_name: str
    access_type: AccessType
    file_path: str
    line_number: int
    column_number: int = 0
    
    # Context information
    function_name: str = ""
    scope_level: int = 0  # 0=global, 1=file-scope, 2=function, 3+=nested
    
    # Thread/parallelism context
    thread_id: Optional[str] = None  # e.g., "omp_parallel_1", "pthread_42"
    parallelism_model: ParallelismModel = ParallelismModel.SEQUENTIAL
    parallel_construct: str = ""  # e.g., "parallel_for", "parallel", "critical"
    
    # Synchronization context
    held_locks: List[str] = None  # Lock names/IDs held at this access
    synchronization_primitives: List[SynchronizationPrimitive] = None
    in_critical_section: bool = False
    in_reduction: bool = False
    
    # OpenMP-specific
    omp_clauses: Dict[str, List[str]] = None  # e.g., {'shared': ['x', 'y'], 'private': ['z']}
    omp_pragma_line: Optional[int] = None
    # Reduction-specific operator
    reduction_operator: Optional[str] = None
    
    # Metadata
    confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN
    reason: str = ""  # Why this access was detected/classified
    source: str = "parser"  # parser, tree-sitter, manual
    
    def __post_init__(self):
        if self.held_locks is None:
            self.held_locks = []
        if self.synchronization_primitives is None:
            self.synchronization_primitives = []
        if self.omp_clauses is None:
            self.omp_clauses = {}


@dataclass
class Variable:
    """Represents a shared variable in the program."""
    
    var_id: str
    name: str
    file_path: str
    declaration_line: Optional[int] = None
    
    # Scope
    scope: str = "global"  # global, file-local, function-local
    scope_function: str = ""
    
    # Type information
    c_type: str = ""  # e.g., "int", "double*"
    
    # Access patterns
    accesses: List[MemoryAccess] = None
    
    # Synchronization
    always_protected: bool = False
    protection_methods: Set[str] = None  # Lock names, critical sections, etc.
    
    def __post_init__(self):
        if self.accesses is None:
            self.accesses = []
        if self.protection_methods is None:
            self.protection_methods = set()


@dataclass
class ThreadContext:
    """Represents a thread or parallel task context."""
    
    thread_id: str  # e.g., "omp_parallel_1", "pthread_42", "main"
    parallelism_model: ParallelismModel
    
    # For OpenMP
    omp_construct: str = ""  # "parallel", "parallel_for", "task", etc.
    omp_clauses: Dict[str, List[str]] = None
    
    # For pthreads
    pthread_create_line: Optional[int] = None
    target_function: str = ""
    
    # Parent/child relationships
    parent_thread: Optional[str] = None
    child_threads: List[str] = None
    
    # Properties
    accesses: List[MemoryAccess] = None
    
    def __post_init__(self):
        if self.omp_clauses is None:
            self.omp_clauses = {}
        if self.child_threads is None:
            self.child_threads = []
        if self.accesses is None:
            self.accesses = []


@dataclass
class SynchronizationPoint:
    """Represents a synchronization mechanism."""
    
    sync_id: str
    primitive_type: SynchronizationPrimitive
    location: str  # file:line format
    
    # Lock-specific
    lock_name: str = ""
    acquired_by: List[str] = None  # Thread IDs that acquire this lock
    
    # Barrier/Critical-specific
    threads_involved: List[str] = None
    # Reduction-specific
    reduction_variables: List[str] = None
    # Pragma/scope metadata
    pragma_scope_start: Optional[int] = None
    pragma_scope_end: Optional[int] = None
    # Reduction operator mapping: {var_name: operator}
    reduction_ops: Dict[str, str] = None
    
    # Metadata
    file_path: str = ""
    line_number: int = 0
    
    def __post_init__(self):
        if self.acquired_by is None:
            self.acquired_by = []
        if self.threads_involved is None:
            self.threads_involved = []


@dataclass
class ConcurrencyIssue:
    """Represents a detected concurrency issue."""
    
    issue_id: str
    issue_type: str  # "data_race", "lock_order_violation", "deadlock", etc.
    
    # Involved accesses
    accesses: List[MemoryAccess]
    variable: Optional[Variable] = None
    threads_involved: List[ThreadContext] = None
    
    # Severity and confidence
    severity: str = "medium"  # low, medium, high, critical
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    is_real_race: Optional[bool] = None  # Set to True/False after LLM analysis
    
    # Location
    file_path: str = ""
    primary_line: int = 0
    
    # Explanation
    reason: str = ""
    llm_analysis: Optional[Dict] = None  # Populated by LLM orchestrator
    recommendations: List[str] = None
    
    def __post_init__(self):
        if self.threads_involved is None:
            self.threads_involved = []
        if self.recommendations is None:
            self.recommendations = []


@dataclass
class IRFile:
    """Complete IR for a single source file."""
    
    file_id: str
    file_path: str
    language: str  # "c", "python", "cpp"
    
    # Entities
    variables: List[Variable] = None
    accesses: List[MemoryAccess] = None
    threads: List[ThreadContext] = None
    synchronization_points: List[SynchronizationPoint] = None
    
    # Functions with concurrency info
    functions: Dict[str, Dict] = None  # {name: {line, accesses, threads, etc}}
    
    # Metadata
    parser_version: str = "1.0"
    ir_version: str = "1.0"
    
    def __post_init__(self):
        if self.variables is None:
            self.variables = []
        if self.accesses is None:
            self.accesses = []
        if self.threads is None:
            self.threads = []
        if self.synchronization_points is None:
            self.synchronization_points = []
        if self.functions is None:
            self.functions = {}


@dataclass
class IRRepository:
    """Complete IR for an entire repository."""
    
    repo_id: str
    repo_path: str
    
    # All files
    files: List[IRFile] = None
    
    # Aggregated cross-file info
    all_accesses: List[MemoryAccess] = None
    all_variables: List[Variable] = None
    all_threads: List[ThreadContext] = None
    all_synchronization_points: List[SynchronizationPoint] = None
    
    # Issues
    detected_issues: List[ConcurrencyIssue] = None
    
    # Metadata
    parser_version: str = "1.0"
    ir_version: str = "1.0"
    
    def __post_init__(self):
        if self.files is None:
            self.files = []
        if self.all_accesses is None:
            self.all_accesses = []
        if self.all_variables is None:
            self.all_variables = []
        if self.all_threads is None:
            self.all_threads = []
        if self.all_synchronization_points is None:
            self.all_synchronization_points = []
        if self.detected_issues is None:
            self.detected_issues = []


class IRBuilder:
    """Helper class to construct IR incrementally."""
    
    def __init__(self, repo_id: str, repo_path: str):
        self.ir = IRRepository(repo_id=repo_id, repo_path=repo_path)
        self.access_counter = 0
        self.variable_counter = 0
        self.thread_counter = 0
        self.sync_counter = 0
    
    def add_memory_access(self, variable_name: str, access_type: AccessType,
                         file_path: str, line_number: int,
                         **kwargs) -> MemoryAccess:
        """Create and add a memory access."""
        self.access_counter += 1
        access = MemoryAccess(
            access_id=f"access_{self.access_counter}",
            variable_name=variable_name,
            access_type=access_type,
            file_path=file_path,
            line_number=line_number,
            **kwargs
        )
        self.ir.all_accesses.append(access)
        return access
    
    def add_variable(self, name: str, file_path: str, **kwargs) -> Variable:
        """Create and add a variable."""
        self.variable_counter += 1
        var = Variable(
            var_id=f"var_{self.variable_counter}",
            name=name,
            file_path=file_path,
            **kwargs
        )
        self.ir.all_variables.append(var)
        return var
    
    def add_thread_context(self, parallelism_model: ParallelismModel, **kwargs) -> ThreadContext:
        """Create and add a thread context."""
        self.thread_counter += 1
        thread = ThreadContext(
            thread_id=f"thread_{self.thread_counter}",
            parallelism_model=parallelism_model,
            **kwargs
        )
        self.ir.all_threads.append(thread)
        return thread
    
    def add_synchronization_point(self, primitive_type: SynchronizationPrimitive,
                                  location: str, **kwargs) -> SynchronizationPoint:
        """Create and add a synchronization point."""
        self.sync_counter += 1
        sync = SynchronizationPoint(
            sync_id=f"sync_{self.sync_counter}",
            primitive_type=primitive_type,
            location=location,
            **kwargs
        )
        self.ir.all_synchronization_points.append(sync)
        return sync
    
    def add_concurrency_issue(self, accesses: List[MemoryAccess],
                             issue_type: str, **kwargs) -> ConcurrencyIssue:
        """Create and add a concurrency issue."""
        issue_id = f"issue_{len(self.ir.detected_issues) + 1}"
        issue = ConcurrencyIssue(
            issue_id=issue_id,
            issue_type=issue_type,
            accesses=accesses,
            **kwargs
        )
        self.ir.detected_issues.append(issue)
        return issue
    
    def get_ir(self) -> IRRepository:
        """Get the constructed IR."""
        return self.ir
    
    def to_json(self) -> str:
        """Serialize IR to JSON."""
        # Convert dataclasses and enums to JSON-serializable format
        def serialize_obj(obj):
            if isinstance(obj, Enum):
                return obj.value
            elif hasattr(obj, '__dataclass_fields__'):
                return {k: serialize_obj(v) for k, v in asdict(obj).items()}
            elif isinstance(obj, (list, tuple)):
                return [serialize_obj(item) for item in obj]
            elif isinstance(obj, dict):
                return {k: serialize_obj(v) for k, v in obj.items()}
            elif isinstance(obj, set):
                return list(obj)
            return obj
        
        return json.dumps(serialize_obj(self.ir), indent=2, default=str)


# Helper functions for common IR operations

def find_variable_by_name(ir: IRRepository, var_name: str) -> Optional[Variable]:
    """Find a variable by name in the IR."""
    return next((v for v in ir.all_variables if v.name == var_name), None)


def find_accesses_for_variable(ir: IRRepository, variable: Variable) -> List[MemoryAccess]:
    """Find all accesses to a given variable."""
    return [a for a in ir.all_accesses if a.variable_name == variable.name]


def find_unprotected_accesses(ir: IRRepository) -> List[MemoryAccess]:
    """Find accesses that are not protected by locks/synchronization."""
    unprotected = []
    for access in ir.all_accesses:
        if not access.held_locks and not access.synchronization_primitives:
            unprotected.append(access)
    return unprotected


def find_concurrent_accesses(ir: IRRepository) -> List[tuple]:
    """Find pairs of accesses that could race (different threads, overlapping access)."""
    races = []
    for i, a1 in enumerate(ir.all_accesses):
        for a2 in ir.all_accesses[i+1:]:
            # Check if different threads and both write or one writes
            if (a1.thread_id and a2.thread_id and a1.thread_id != a2.thread_id and
                a1.variable_name == a2.variable_name and
                (a1.access_type in [AccessType.WRITE, AccessType.READ_WRITE] or
                 a2.access_type in [AccessType.WRITE, AccessType.READ_WRITE])):
                races.append((a1, a2))
    return races
