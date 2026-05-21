"""IR Normalizer: Converts parser output to comprehensive IR.

Transforms raw parser output into the unified IR schema used by all
downstream components (TIG, static analysis, RAG, LLM).
"""

import os
from typing import Dict, List, Optional
from backend.ir.ir_schema_v2 import (
    IRBuilder, IRFile, MemoryAccess, Variable, ThreadContext,
    AccessType, SynchronizationPrimitive, ConfidenceLevel,
    ParallelismModel, IRRepository
)


def _detect_omp_thread_count(parsed_file):
    """Detect the OpenMP thread count from source hints.

    Looks for ``omp_set_num_threads(N)`` calls or defaults to 4
    (the typical OpenMP runtime default).
    """
    import re as _re
    for pragma in parsed_file.get('omp_pragmas', []):
        text = pragma.get('text', '')
        m = _re.search(r'num_threads\s*\(\s*(\d+)\s*\)', text)
        if m:
            return int(m.group(1))
    # Scan var_accesses / raw source for omp_set_num_threads(N)
    for acc in parsed_file.get('var_accesses', []):
        raw = str(acc)
        m = _re.search(r'omp_set_num_threads\s*\(\s*(\d+)\s*\)', raw)
        if m:
            return int(m.group(1))
    return 4  # sensible OpenMP default


class IRNormalizer:
    """Normalizes parser output to comprehensive IR."""
    
    def __init__(self, repo_path: str = ""):
        self.repo_path = repo_path
        self.thread_counter = 0
        self.access_counter = 0
        self.variable_counter = 0
    
    def normalize_repository(self, parsed_files: List[Dict]) -> IRRepository:
        """Convert parsed files to repository-level IR.
        
        Args:
            parsed_files: List of file dicts from parser
            
        Returns:
            IRRepository with all normalized information
        """
        builder = IRBuilder(
            repo_id="repo_1",
            repo_path=self.repo_path or parsed_files[0].get('path', '').split(os.sep)[0]
        )
        
        # Process each file
        ir_files = []
        for parsed_file in parsed_files:
            ir_file = self.normalize_file(parsed_file, builder)
            if ir_file:
                ir_files.append(ir_file)
                builder.ir.files.append(ir_file)
        
        return builder.get_ir()
    
    def normalize_file(self, parsed_file: Dict, builder: IRBuilder) -> Optional[IRFile]:
        """Convert a single parsed file to IR.
        
        Args:
            parsed_file: File dict from parser
            builder: IRBuilder for registering entities
            
        Returns:
            IRFile with normalized information
        """
        file_path = parsed_file.get('path', '')
        language = parsed_file.get('language', 'c')
        
        ir_file = IRFile(
            file_id=f"file_{len(builder.ir.files) + 1}",
            file_path=file_path,
            language=language
        )
        
        # Extract variables first (needed for access context)
        shared_vars = parsed_file.get('shared_variables', [])
        var_map = {}
        for var_name in shared_vars:
            var = Variable(
                var_id=f"var_{self.variable_counter}",
                name=var_name,
                file_path=file_path,
                scope="global"
            )
            self.variable_counter += 1
            ir_file.variables.append(var)
            builder.ir.all_variables.append(var)
            var_map[var_name] = var
        
        # Extract thread contexts
        thread_map = {}
        for thread_info in parsed_file.get('threads', []):
            thread_ctx = self._extract_thread_context(
                thread_info, file_path, language, builder
            )
            if thread_ctx:
                ir_file.threads.append(thread_ctx)
                builder.ir.all_threads.append(thread_ctx)
                thread_map[thread_info.get('id', str(len(thread_map)))] = thread_ctx

        # ── Ensure a main thread exists ──
        main_thread = None
        if parsed_file.get('omp_pragmas'):
            main_thread = builder.add_thread_context(
                ParallelismModel.OPENMP,
                omp_construct='main',
                omp_clauses={},
                parent_thread=None,
                accesses=[]
            )
            # Override the auto-generated ID with a stable name
            main_thread.thread_id = 'thread_main'
            ir_file.threads.append(main_thread)

        # Materialize OpenMP thread contexts and synchronization points from pragma metadata.
        omp_context_map = {}  # maps (kind, line) -> list of worker thread IDs
        for omp_pragma in parsed_file.get('omp_pragmas', []):
            worker_threads = self._extract_omp_thread_context(
                omp_pragma, parsed_file, file_path, language, builder
            )
            if worker_threads:
                for wt in worker_threads:
                    ir_file.threads.append(wt)
                # Store the first worker's thread_id for access mapping
                key = (omp_pragma.get('kind', 'unknown'), omp_pragma.get('line', 0))
                omp_context_map[key] = worker_threads[0].thread_id

            first_tid = worker_threads[0].thread_id if worker_threads else None
            sync_point = self._extract_omp_synchronization(
                omp_pragma, parsed_file, file_path, builder, first_tid
            )
            if sync_point:
                ir_file.synchronization_points.append(sync_point)

            # Add an implicit barrier sync node at the end of parallel for (unless nowait)
            pragma_kind = omp_pragma.get('kind', 'unknown')
            has_nowait = 'nowait' in omp_pragma.get('text', '')
            if pragma_kind in ('parallel_for', 'parallel', 'for') and not has_nowait:
                all_tids = [wt.thread_id for wt in (worker_threads or [])]
                if main_thread:
                    all_tids.append(main_thread.thread_id)
                barrier_sync = builder.add_synchronization_point(
                    primitive_type=SynchronizationPrimitive.BARRIER,
                    location=f"{file_path}:{omp_pragma.get('line', 0)}",
                    line_number=omp_pragma.get('line', 0),
                    file_path=file_path,
                    threads_involved=all_tids,
                    acquired_by=all_tids,
                )
                ir_file.synchronization_points.append(barrier_sync)
        
        # Extract synchronization primitives
        for lock_info in parsed_file.get('locks', []):
            sync = self._extract_synchronization(
                lock_info, file_path, language, builder
            )
            if sync:
                ir_file.synchronization_points.append(sync)
                builder.ir.all_synchronization_points.append(sync)
        
        # Extract memory accesses from OpenMP pragmas
        for omp_pragma in parsed_file.get('omp_pragmas', []):
            thread_id = omp_context_map.get((omp_pragma.get('kind', 'unknown'), omp_pragma.get('line', 0)))
            accesses = self._extract_omp_accesses(
                omp_pragma, parsed_file, file_path, builder, var_map=var_map, thread_id=thread_id
            )
            ir_file.accesses.extend(accesses)
            builder.ir.all_accesses.extend(accesses)
        
        # Extract line-level variable accesses when available; fall back to aggregate accesses
        line_accesses = parsed_file.get('var_accesses', []) or []
        if line_accesses:
            accesses = self._extract_line_variable_accesses(
                line_accesses, parsed_file, file_path, builder, var_map=var_map, threads=ir_file.threads
            )
            ir_file.accesses.extend(accesses)
            builder.ir.all_accesses.extend(accesses)
        else:
            for var_name in shared_vars:
                accesses = self._extract_variable_accesses(
                    var_name, parsed_file, file_path, builder, var_map=var_map
                )
                ir_file.accesses.extend(accesses)
                builder.ir.all_accesses.extend(accesses)
        
        return ir_file
    
    def _extract_thread_context(self, thread_info: Dict, file_path: str,
                               language: str, builder: IRBuilder) -> Optional[ThreadContext]:
        """Extract thread context from parser output."""
        self.thread_counter += 1
        
        if language == 'c':
            parallelism = ParallelismModel.PTHREADS
            construct = thread_info.get('type', 'pthread')
        else:
            parallelism = ParallelismModel.PTHREADS
            construct = thread_info.get('target', 'thread')
        
        return ThreadContext(
            thread_id=f"thread_{self.thread_counter}",
            parallelism_model=parallelism,
            omp_construct=construct,
            pthread_create_line=thread_info.get('lineno'),
            accesses=[]
        )

    def _extract_omp_thread_context(self, omp_pragma: Dict, parsed_file: Dict,
                                    file_path: str, language: str, builder: IRBuilder):
        """Extract OpenMP worker thread contexts from pragma metadata.

        Returns a *list* of ThreadContext objects (one per worker thread),
        or None if the pragma doesn't spawn threads.
        """
        pragma_kind = omp_pragma.get('kind', 'unknown')
        if pragma_kind not in {'parallel', 'parallel_for', 'for', 'task', 'single', 'master', 'sections'}:
            return None

        clauses = {
            'shared': list(parsed_file.get('omp_shared', [])),
            'private': list(parsed_file.get('omp_private', [])),
            'firstprivate': list(parsed_file.get('omp_firstprivate', [])),
            'lastprivate': list(parsed_file.get('omp_lastprivate', [])),
            'reduction': list(parsed_file.get('omp_reduction', [])),
        }

        # Determine number of worker threads for this pragma
        num_threads = _detect_omp_thread_count(parsed_file)
        pragma_line = omp_pragma.get('line', 0)

        workers = []
        for i in range(num_threads):
            thread = builder.add_thread_context(
                ParallelismModel.OPENMP,
                omp_construct=pragma_kind,
                omp_clauses=clauses,
                parent_thread='thread_main',
                accesses=[]
            )
            # Give workers stable, readable IDs
            thread.thread_id = f"thread_{i}"
            workers.append(thread)

        return workers

    def _extract_omp_synchronization(self, omp_pragma: Dict, parsed_file: Dict, file_path: str,
                                     builder: IRBuilder, thread_id: Optional[str] = None) -> Optional:
        """Extract synchronization points from OpenMP pragma metadata."""
        pragma_kind = omp_pragma.get('kind', 'unknown')
        pragma_line = omp_pragma.get('line', 0)
        pragma_text = omp_pragma.get('text', '')

        primitive_map = {
            'critical': SynchronizationPrimitive.CRITICAL_SECTION,
            'atomic': SynchronizationPrimitive.ATOMIC,
            'reduction': SynchronizationPrimitive.REDUCTION,
            'barrier': SynchronizationPrimitive.BARRIER,
            'ordered': SynchronizationPrimitive.ORDERED,
            'master': SynchronizationPrimitive.MASTER,
            'single': SynchronizationPrimitive.SINGLE,
        }

        prim_type = primitive_map.get(pragma_kind)
        
        # Check for reduction clause within parallel or for pragmas
        if prim_type is None and 'reduction' in pragma_text:
            prim_type = SynchronizationPrimitive.REDUCTION
        
        if prim_type is None:
            return None

        # For reductions, attach variables if provided on the pragma (parser supplies at file level)
        reduction_vars = list(parsed_file.get('omp_reduction', []) or [])
        reduction_ops = dict(parsed_file.get('omp_reduction_map', {}) or {})

        # Heuristic pragma scope: start at pragma line, assume end within next 40 lines
        scope_start = pragma_line
        scope_end = pragma_line + 40

        sync = builder.add_synchronization_point(
            primitive_type=prim_type,
            location=f"{file_path}:{pragma_line}",
            line_number=pragma_line,
            file_path=file_path,
            threads_involved=[thread_id] if thread_id else [],
            acquired_by=[thread_id] if thread_id else [],
            reduction_variables=reduction_vars,
            reduction_ops=reduction_ops,
            pragma_scope_start=scope_start,
            pragma_scope_end=scope_end
        )
        return sync

    def _extract_line_variable_accesses(self, var_accesses: List[Dict], parsed_file: Dict,
                                        file_path: str, builder: IRBuilder,
                                        var_map: Optional[Dict[str, Variable]] = None,
                                        threads: Optional[List[ThreadContext]] = None) -> List[MemoryAccess]:
        """Extract per-line variable accesses emitted by the parser.

        Clause-aware: when a variable appears in reduction / firstprivate /
        lastprivate / private clause lists from the parsed pragma metadata,
        its accesses are marked with appropriate synchronization flags so
        downstream analysis does NOT flag them as unprotected.
        """
        accesses = []

        # Pre-compute clause sets once (generic — works for any program)
        clause_private = set(parsed_file.get('omp_private', []))
        clause_firstprivate = set(parsed_file.get('omp_firstprivate', []))
        clause_lastprivate = set(parsed_file.get('omp_lastprivate', []))
        clause_reduction = set(parsed_file.get('omp_reduction', []))
        clause_all_protected = clause_private | clause_firstprivate | clause_lastprivate | clause_reduction

        for entry in var_accesses:
            var_name = entry.get('variable_name')
            if not var_name:
                continue

            access_kind = (entry.get('access_type') or 'read').lower()
            if access_kind == 'write':
                access_type = AccessType.WRITE
            elif access_kind == 'read_write':
                access_type = AccessType.READ_WRITE
            else:
                access_type = AccessType.READ

            thread_id = entry.get('thread_hint')
            function_name = entry.get('function')
            if not thread_id and function_name and threads:
                # Map python function scope to thread target
                for t in threads:
                    if t.omp_construct == function_name:
                        thread_id = t.thread_id
                        break
                if not thread_id and function_name == 'main':
                    thread_id = 'thread_main'

            omp_kind = entry.get('omp_kind')
            omp_line = entry.get('omp_line')
            index_vars = entry.get('index_vars', []) or []
            sync_primitives = []
            in_reduction = False
            in_critical = False

            # ── Clause-aware sync primitive assignment ──
            # If the variable is inside a parallel region (has omp_kind),
            # check whether it's protected by a clause.
            if omp_kind and omp_kind in ('parallel_for', 'parallel', 'for'):
                if var_name in clause_reduction:
                    sync_primitives.append(SynchronizationPrimitive.REDUCTION)
                    in_reduction = True
                elif var_name in clause_firstprivate or var_name in clause_lastprivate or var_name in clause_private:
                    # private/firstprivate/lastprivate vars have thread-local copies
                    # Mark as protected (no race possible)
                    sync_primitives.append(SynchronizationPrimitive.BARRIER)

            # Standard pragma-level sync detection
            if omp_kind == 'critical':
                sync_primitives.append(SynchronizationPrimitive.CRITICAL_SECTION)
                in_critical = True
            elif omp_kind == 'atomic':
                sync_primitives.append(SynchronizationPrimitive.ATOMIC)
            elif omp_kind == 'reduction':
                sync_primitives.append(SynchronizationPrimitive.REDUCTION)
                in_reduction = True

            self.access_counter += 1
            access = MemoryAccess(
                access_id=f"access_{self.access_counter}",
                variable_name=var_name,
                access_type=access_type,
                file_path=file_path,
                line_number=entry.get('line_number', 0) or 0,
                thread_id=thread_id,
                parallelism_model=ParallelismModel.OPENMP if thread_id else ParallelismModel.SEQUENTIAL,
                parallel_construct=omp_kind or 'sequential',
                omp_pragma_line=omp_line,
                omp_clauses={
                    'shared': list(parsed_file.get('omp_shared', [])),
                    'private': list(clause_private),
                    'firstprivate': list(clause_firstprivate),
                    'lastprivate': list(clause_lastprivate),
                    'reduction': list(clause_reduction),
                },
                synchronization_primitives=sync_primitives,
                in_critical_section=in_critical,
                in_reduction=in_reduction,
                reason=("index_by:" + ','.join(index_vars)) if index_vars else '',
                confidence=ConfidenceLevel.HIGH if thread_id else ConfidenceLevel.MEDIUM,
                source='parser',
            )
            accesses.append(access)
            if var_map and var_name in var_map:
                var_map[var_name].accesses.append(access)

        return accesses
    
    def _extract_synchronization(self, lock_info: Dict, file_path: str,
                                 language: str, builder: IRBuilder) -> Optional:
        """Extract synchronization primitive from parser output."""
        # Determine primitive and capture lock name/op where available
        prim_type = SynchronizationPrimitive.LOCK
        lock_name = lock_info.get('name') or lock_info.get('lock_name') or ''
        op = lock_info.get('op')

        sync = builder.add_synchronization_point(
            primitive_type=prim_type,
            location=f"{file_path}:{lock_info.get('lineno', 0)}",
            line_number=lock_info.get('lineno', 0),
            file_path=file_path,
            lock_name=lock_name,
            acquired_by=[],
            threads_involved=[]
        )
        return sync
    
    def _extract_omp_accesses(self, omp_pragma: Dict, parsed_file: Dict,
                             file_path: str, builder: IRBuilder,
                             var_map: Optional[Dict[str, Variable]] = None,
                             thread_id: Optional[str] = None) -> List[MemoryAccess]:
        """Extract memory accesses from OpenMP pragmas."""
        accesses = []
        pragma_kind = omp_pragma.get('kind', 'unknown')
        pragma_line = omp_pragma.get('line', 0)
        
        # Get shared variables from pragma
        shared_vars = set(parsed_file.get('omp_shared', []))
        shared_vars.update(parsed_file.get('omp_reduction', []))
        
        # Get private variables (not data race concerns)
        private_vars = set(parsed_file.get('omp_private', []))
        
        # Determine synchronization context
        sync_primitives = []
        if pragma_kind == 'critical':
            sync_primitives.append(SynchronizationPrimitive.CRITICAL_SECTION)
        elif pragma_kind == 'reduction':
            sync_primitives.append(SynchronizationPrimitive.REDUCTION)
        elif pragma_kind == 'atomic':
            sync_primitives.append(SynchronizationPrimitive.ATOMIC)
        
        # Create access entries for shared variables in this pragma
        for var_name in shared_vars:
            if var_name not in private_vars:
                self.access_counter += 1
                # Determine reduction operator if this var is part of reductions
                reduction_ops_map = parsed_file.get('omp_reduction_map', {}) if parsed_file else {}
                red_op = reduction_ops_map.get(var_name)

                access = MemoryAccess(
                    access_id=f"access_{self.access_counter}",
                    variable_name=var_name,
                    access_type=AccessType.READ_WRITE,  # Conservative
                    file_path=file_path,
                    line_number=pragma_line,
                    thread_id=thread_id or f"omp_{pragma_kind}_{pragma_line}",
                    parallelism_model=ParallelismModel.OPENMP,
                    parallel_construct=pragma_kind,
                    omp_pragma_line=pragma_line,
                    synchronization_primitives=sync_primitives,
                    in_critical_section=(pragma_kind == 'critical'),
                    in_reduction=(pragma_kind == 'reduction' or bool(red_op)),
                    reduction_operator=red_op,
                    omp_clauses={
                        'shared': list(parsed_file.get('omp_shared', [])),
                        'private': list(parsed_file.get('omp_private', [])),
                        'reduction': list(parsed_file.get('omp_reduction', [])),
                    },
                    confidence=ConfidenceLevel.HIGH,
                    source='parser'
                )
                accesses.append(access)
                if var_map and var_name in var_map:
                    var_map[var_name].accesses.append(access)
        
        return accesses
    
    def _extract_variable_accesses(self, var_name: str, parsed_file: Dict,
                                   file_path: str, builder: IRBuilder,
                                   var_map: Optional[Dict[str, Variable]] = None) -> List[MemoryAccess]:
        """Extract read/write accesses for a variable."""
        accesses = []
        var_reads = parsed_file.get('var_reads', [])
        var_writes = parsed_file.get('var_writes', [])
        
        # Track if variable is accessed
        has_reads = var_name in var_reads
        has_writes = var_name in var_writes
        
        if not (has_reads or has_writes):
            return accesses
        
        # Create access entry (simplified; in real scenario would track per-line)
        if has_writes and has_reads:
            access_type = AccessType.READ_WRITE
        elif has_writes:
            access_type = AccessType.WRITE
        else:
            access_type = AccessType.READ
        
        self.access_counter += 1
        access = MemoryAccess(
            access_id=f"access_{self.access_counter}",
            variable_name=var_name,
            access_type=access_type,
            file_path=file_path,
            line_number=0,  # Aggregate for now
            parallelism_model=ParallelismModel.SEQUENTIAL,
            confidence=ConfidenceLevel.MEDIUM,
            source='parser'
        )
        accesses.append(access)
        if var_map and var_name in var_map:
            var_map[var_name].accesses.append(access)
        
        return accesses


def normalize_to_ir(parsed_files: List[Dict], repo_path: str = "") -> IRRepository:
    """Convenience function to normalize parsed files to IR.
    
    Args:
        parsed_files: List of dicts from parser
        repo_path: Repository path
        
    Returns:
        Complete IRRepository
    """
    normalizer = IRNormalizer(repo_path)
    return normalizer.normalize_repository(parsed_files)
