"""Data-flow analysis — def-use chains for concurrency analysis.

For each variable, tracks which lines define (write) and which lines
use (read). Builds a simple def-use graph that can be queried to
determine if a write in one thread can reach a read in another.
"""

from typing import Any, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field


@dataclass
class DefUseEntry:
    """A single definition or use of a variable."""
    variable: str
    line: int
    kind: str          # 'def' (write) or 'use' (read)
    thread_id: str
    function: str
    file_path: str
    in_parallel: bool  # inside a parallel region?
    held_locks: List[str] = field(default_factory=list)


@dataclass
class DefUseChain:
    """A def followed by a use of the same variable."""
    variable: str
    def_entry: DefUseEntry
    use_entry: DefUseEntry
    cross_thread: bool    # def and use are in different threads
    both_unprotected: bool  # neither def nor use holds a lock


def build_def_use_map(ir: Any) -> Dict[str, List[DefUseEntry]]:
    """Build a map of variable name -> list of DefUseEntry from the IR.

    Args:
        ir: IRRepository with all_variables, all_accesses

    Returns:
        Dict mapping variable names to their definitions and uses
    """
    result: Dict[str, List[DefUseEntry]] = {}

    for access in getattr(ir, 'all_accesses', []):
        var_name = getattr(access, 'variable_name', None)
        if not var_name:
            continue

        line = getattr(access, 'line_number', 0)
        thread_id = getattr(access, 'thread_id', 'unknown')
        function = getattr(access, 'function_scope', '')
        file_path = getattr(access, 'file_path', '')
        held_locks = list(getattr(access, 'held_locks', []) or [])

        # Determine access type
        access_type = str(getattr(access, 'access_type', ''))
        if 'WRITE' in access_type or 'CAS' in access_type:
            kind = 'def'
        else:
            kind = 'use'

        # Check if inside a parallel region
        in_parallel = bool(getattr(access, 'omp_context', None))

        entry = DefUseEntry(
            variable=var_name,
            line=line,
            kind=kind,
            thread_id=thread_id,
            function=function,
            file_path=file_path,
            in_parallel=in_parallel,
            held_locks=held_locks,
        )

        result.setdefault(var_name, []).append(entry)

    return result


def find_def_use_chains(du_map: Dict[str, List[DefUseEntry]]) -> List[DefUseChain]:
    """Find all def-use chains (write → read of same variable).

    A def-use chain is potentially dangerous if:
      - The def and use are in different threads (cross_thread=True)
      - Neither the def nor the use holds a lock (both_unprotected=True)
    """
    chains = []

    for var_name, entries in du_map.items():
        defs = [e for e in entries if e.kind == 'def']
        uses = [e for e in entries if e.kind == 'use']

        for d in defs:
            for u in uses:
                if d.line == u.line and d.file_path == u.file_path:
                    continue  # Same statement

                cross_thread = d.thread_id != u.thread_id
                both_unprotected = (not d.held_locks) and (not u.held_locks)

                chains.append(DefUseChain(
                    variable=var_name,
                    def_entry=d,
                    use_entry=u,
                    cross_thread=cross_thread,
                    both_unprotected=both_unprotected,
                ))

    return chains


def find_racy_def_use_chains(ir: Any) -> List[DefUseChain]:
    """High-level API: find def-use chains that indicate potential races.

    Returns only chains where:
      - The write and read are in different threads AND
      - At least one access is unprotected (no held locks) AND
      - At least one access is inside a parallel region
    """
    du_map = build_def_use_map(ir)
    chains = find_def_use_chains(du_map)

    racy = []
    for c in chains:
        if not c.cross_thread:
            continue
        if not c.both_unprotected:
            continue
        if not (c.def_entry.in_parallel or c.use_entry.in_parallel):
            continue
        racy.append(c)

    return racy


def def_use_summary(ir: Any) -> Dict[str, Any]:
    """Generate a summary of def-use analysis for the IR."""
    du_map = build_def_use_map(ir)
    chains = find_def_use_chains(du_map)
    racy = [c for c in chains if c.cross_thread and c.both_unprotected]

    return {
        'variables_tracked': len(du_map),
        'total_entries': sum(len(v) for v in du_map.values()),
        'total_chains': len(chains),
        'cross_thread_chains': sum(1 for c in chains if c.cross_thread),
        'racy_chains': len(racy),
        'racy_variables': list(set(c.variable for c in racy)),
    }
