import networkx as nx
from typing import List, Dict, Optional
from backend.ir.ir_schema_v2 import IRRepository, MemoryAccess, Variable, ThreadContext, SynchronizationPrimitive


def build_tig_from_ir(ir: IRRepository) -> nx.DiGraph:
    """Build a Thread Interaction Graph (TIG) from comprehensive IR.
    
    This enhanced version consumes IRRepository and enriches the graph with
    IR metadata for better analysis.

    Nodes:
      - file:<path>          (scope: aggregated file metadata)
      - var:<name>           (scope, protection_methods, always_protected)
      - thread:<id>          (parallelism_model, parent/child relationships)
      - lock:<id>            (primitive_type, acquired_by)
      - access:<id>          (access_type, confidence, synchronization)

    Edges:
      - file -> var          (contains)
      - file -> thread       (contains)
      - file -> lock         (contains)
      - thread -> var        (may_access)
        - Metadata: access_type, synchronization, confidence, omp_context
      - thread -> lock       (acquires)

    This version preserves all IR metadata for downstream analysis.
    """
    G = nx.DiGraph()

    # Add file nodes
    for ir_file in ir.files:
        file_node = f"file:{ir_file.file_path}"
        G.add_node(file_node, type='file', path=ir_file.file_path, language=ir_file.language)

    # Add variable nodes with IR metadata
    var_node_map = {}  # Map variable name to node ID
    for var in ir.all_variables:
        var_node = f"var:{var.name}"
        var_node_map[var.name] = var_node
        
        # Enrich with IR metadata
        G.add_node(var_node, 
                  type='variable',
                  name=var.name,
                  scope=var.scope,
                  c_type=var.c_type,
                  always_protected=var.always_protected,
                  protection_methods=list(var.protection_methods),
                  num_accesses=len(var.accesses),
                  declaration_line=var.declaration_line)
        
        # Add containment edge from file
        file_node = f"file:{var.file_path}"
        if G.has_node(file_node):
            G.add_edge(file_node, var_node, relation='contains')

    # Add thread nodes with IR metadata
    thread_node_map = {}  # Map thread ID to node ID
    for thread in ir.all_threads:
        thread_node = f"thread:{thread.thread_id}"
        thread_node_map[thread.thread_id] = thread_node
        
        G.add_node(thread_node,
                  type='thread',
                  thread_id=thread.thread_id,
                  parallelism_model=thread.parallelism_model.value,
                  omp_construct=thread.omp_construct,
                  parent_thread=thread.parent_thread,
                  num_child_threads=len(thread.child_threads),
                  num_accesses=len(thread.accesses))

    # Add synchronization point nodes
    sync_node_map = {}  # Map sync ID to node ID
    for sync in ir.all_synchronization_points:
        sync_node = f"sync:{sync.sync_id}"
        sync_node_map[sync.sync_id] = sync_node
        
        G.add_node(sync_node,
                  type='sync',
                  sync_id=sync.sync_id,
                  primitive_type=sync.primitive_type.value,
                  location=sync.location,
                  lock_name=sync.lock_name,
                  acquired_by=sync.acquired_by,
                  num_threads_involved=len(sync.threads_involved))

    # Add memory access edges with rich metadata
    for access in ir.all_accesses:
        if not access.thread_id:
            continue
        
        thread_node = thread_node_map.get(access.thread_id)
        var_node = var_node_map.get(access.variable_name)
        
        if thread_node and var_node:
            # Create edge with enriched metadata
            edge_attrs = {
                'relation': 'may_access',
                'access_type': access.access_type.value,
                'access_id': access.access_id,
                'file_path': access.file_path,
                'line_number': access.line_number,
                'confidence': access.confidence.value,
                'in_critical_section': access.in_critical_section,
                'in_reduction': access.in_reduction,
                'synchronization': [s.value for s in access.synchronization_primitives],
                'held_locks': access.held_locks,
                'omp_clauses': access.omp_clauses,
                'parallelism_model': access.parallelism_model.value,
                'parallel_construct': access.parallel_construct,
            }
            
            G.add_edge(thread_node, var_node, **edge_attrs)

            # Add protected_by / atomic_access / reduction_scope edges when appropriate
            # Protected by: held locks or critical sections
            if access.held_locks:
                for lname in access.held_locks:
                    # try to find corresponding sync node by lock_name
                    for sync in ir.all_synchronization_points:
                        if getattr(sync, 'lock_name', None) == lname:
                            sync_node = sync_node_map.get(sync.sync_id)
                            if sync_node:
                                G.add_edge(thread_node, var_node, relation='protected_by', lock_name=lname, sync_id=sync.sync_id)
            if access.in_critical_section:
                # link to nearby critical syncs in same file
                for sync in ir.all_synchronization_points:
                    if sync.primitive_type == SynchronizationPrimitive.CRITICAL_SECTION and sync.file_path == access.file_path:
                        # if access line is within pragma scope, or same line
                        if sync.line_number and abs(sync.line_number - access.line_number) < 50:
                            G.add_edge(thread_node, var_node, relation='protected_by', sync_id=sync.sync_id)

            # Atomic accesses
            if SynchronizationPrimitive.ATOMIC in (access.synchronization_primitives or []):
                G.add_edge(thread_node, var_node, relation='atomic_access', access_id=access.access_id)

            # Reduction scope
            if access.in_reduction:
                # try to find reduction sync that references this variable
                for sync in ir.all_synchronization_points:
                    if sync.primitive_type == SynchronizationPrimitive.REDUCTION:
                        if getattr(sync, 'reduction_variables', None) and access.variable_name in (sync.reduction_variables or []):
                            # attach operator if available
                            rops = getattr(sync, 'reduction_ops', None) or {}
                            op = rops.get(access.variable_name) if rops else None
                            G.add_edge(thread_node, var_node, relation='reduction_scope', sync_id=sync.sync_id, reduction_variables=sync.reduction_variables, reduction_op=op)

    # Add thread-to-lock acquisition edges
    for sync in ir.all_synchronization_points:
        sync_node = sync_node_map.get(sync.sync_id)
        if not sync_node:
            continue
        
        for thread_id in sync.acquired_by:
            thread_node = thread_node_map.get(thread_id)
            if thread_node:
                G.add_edge(thread_node, sync_node, 
                          relation='acquires',
                          primitive_type=sync.primitive_type.value)

    # Add synchronized_with edges: threads involved in same sync point
    for sync in ir.all_synchronization_points:
        if not sync.threads_involved:
            continue
        involved = [t for t in sync.threads_involved if t in thread_node_map]
        for i in range(len(involved)):
            for j in range(i+1, len(involved)):
                t1 = thread_node_map.get(involved[i])
                t2 = thread_node_map.get(involved[j])
                if t1 and t2:
                    G.add_edge(t1, t2, relation='synchronized_with', sync_id=sync.sync_id)

    # Add barrier_after ordering edges between barrier sync nodes in the same file
    barrier_nodes = [s for s in ir.all_synchronization_points if s.primitive_type == SynchronizationPrimitive.BARRIER]
    # group by file
    from collections import defaultdict
    by_file = defaultdict(list)
    for s in barrier_nodes:
        by_file.get(s.file_path, []).append(s)
    for fpath, syncs in by_file.items():
        syncs_sorted = sorted(syncs, key=lambda s: getattr(s, 'line_number', 0) or 0)
        for idx in range(len(syncs_sorted)-1):
            s1 = syncs_sorted[idx]
            s2 = syncs_sorted[idx+1]
            n1 = sync_node_map.get(s1.sync_id)
            n2 = sync_node_map.get(s2.sync_id)
            if n1 and n2:
                G.add_edge(n1, n2, relation='barrier_after')

    # Add parent-child thread relationships
    for thread in ir.all_threads:
        if thread.parent_thread:
            parent_node = thread_node_map.get(thread.parent_thread)
            child_node = thread_node_map.get(thread.thread_id)
            if parent_node and child_node:
                G.add_edge(parent_node, child_node, relation='spawns')

    return G


def build_tig(ir: List[Dict]) -> nx.DiGraph:
    """Build a Thread Interaction Graph (TIG) from normalized IR.

    Nodes:
      - file:<path>
      - var:<name>
      - thread:<id or lineno>
      - lock:<id or lineno>

    Edges (example relationships):
      - file -> entity (contains)
      - thread -> var (access)
      - thread -> lock (acquire)

    This is a lightweight builder intended for MVP iteration.
    """
    G = nx.DiGraph()

    for f in ir:
        fnode = f"file:{f.get('path')}"
        G.add_node(fnode, type='file', path=f.get('path'))
        for ent in f.get('entities', []):
            etype = ent.get('type')
            if etype == 'variable':
                vname = ent.get('name')
                vnode = f"var:{vname}"
                G.add_node(vnode, type='variable', name=vname)
                G.add_edge(fnode, vnode, relation='contains')
            elif etype == 'thread':
                tid = f"thread:{f.get('path')}@{ent.get('lineno', '0')}"
                G.add_node(tid, type='thread', source=f.get('path'), lineno=ent.get('lineno'))
                G.add_edge(fnode, tid, relation='contains')
            elif etype == 'lock':
                name = ent.get('name')
                lid = f"lock:{f.get('path')}@{ent.get('lineno', '0')}"
                attrs = {'type': 'lock', 'source': f.get('path'), 'lineno': ent.get('lineno')}
                if name:
                    attrs['name'] = name
                G.add_node(lid, **attrs)
                G.add_edge(fnode, lid, relation='contains')
            else:
                # raw or unknown
                nid = f"raw:{f.get('path')}@{ent.get('type')}@{ent.get('lineno', '')}"
                G.add_node(nid, type='raw', info=ent)
                G.add_edge(fnode, nid, relation='contains')

    # Example heuristic: connect threads to variables in same file as potential access
    for node, data in G.nodes(data=True):
        if data.get('type') == 'thread':
            # find variables in same file
            source = data.get('source')
            file_node = f"file:{source}"
            for _, vdata in G.nodes(data=True):
                if vdata.get('type') == 'variable':
                    # naive connect if variable is in file_node's successors
                    if G.has_edge(file_node, f"var:{vdata.get('name')}"):
                        G.add_edge(node, f"var:{vdata.get('name')}", relation='may_access')

    # Heuristic: connect threads to locks in same file when lock names are present
    for tnode, tdata in G.nodes(data=True):
        if tdata.get('type') != 'thread':
            continue
        source = tdata.get('source')
        for lnode, ldata in G.nodes(data=True):
            if ldata.get('type') != 'lock':
                continue
            if ldata.get('source') == source:
                # if lock has a name attribute and it's present, connect as acquires
                G.add_edge(tnode, lnode, relation='acquires')

    return G


def tig_summary(G: nx.DiGraph) -> Dict:
    """Return a small summary of the TIG for quick inspection."""
    nodes = list(G.nodes(data=True))
    edges = [(u, v, d) for u, v, d in G.edges(data=True)]
    types = {}
    for _, d in nodes:
        types[d.get('type')] = types.get(d.get('type'), 0) + 1
    return {'node_count': G.number_of_nodes(), 'edge_count': G.number_of_edges(), 'types': types, 'edges_sample': edges[:10]}


def tig_summary_from_ir(G: nx.DiGraph) -> Dict:
    """Enhanced summary of IR-based TIG with metadata insights."""
    nodes = list(G.nodes(data=True))
    edges = [(u, v, d) for u, v, d in G.edges(data=True)]
    
    # Count node types
    types = {}
    for _, d in nodes:
        types[d.get('type')] = types.get(d.get('type'), 0) + 1
    
    # Analyze edges by type
    edge_relations = {}
    edge_access_types = {}
    high_confidence_accesses = 0
    
    for u, v, d in edges:
        relation = d.get('relation', 'unknown')
        edge_relations[relation] = edge_relations.get(relation, 0) + 1
        
        access_type = d.get('access_type')
        if access_type:
            edge_access_types[access_type] = edge_access_types.get(access_type, 0) + 1
        
        if d.get('confidence') == 'HIGH':
            high_confidence_accesses += 1
    
    # Find protected vs unprotected accesses
    protected_accesses = 0
    unprotected_accesses = 0
    for u, v, d in edges:
        if d.get('relation') == 'may_access':
            held_locks = d.get('held_locks', [])
            sync_primitives = d.get('synchronization', [])
            if held_locks or sync_primitives:
                protected_accesses += 1
            else:
                unprotected_accesses += 1
    
    # Find variables that are always protected
    always_protected_vars = 0
    for _, d in nodes:
        if d.get('type') == 'variable' and d.get('always_protected'):
            always_protected_vars += 1
    
    # Find accesses in critical sections
    critical_section_accesses = sum(
        1 for u, v, d in edges
        if d.get('in_critical_section') is True
    )
    
    return {
        'node_count': G.number_of_nodes(),
        'edge_count': G.number_of_edges(),
        'node_types': types,
        'edge_relations': edge_relations,
        'access_types': edge_access_types,
        'high_confidence_accesses': high_confidence_accesses,
        'protected_accesses': protected_accesses,
        'unprotected_accesses': unprotected_accesses,
        'always_protected_variables': always_protected_vars,
        'critical_section_accesses': critical_section_accesses,
        'potential_races': unprotected_accesses,
    }


def find_unprotected_accesses_in_tig(G: nx.DiGraph) -> List[tuple]:
    """Find all unprotected memory access edges in TIG."""
    unprotected = []
    for u, v, d in G.edges(data=True):
        if (d.get('relation') == 'may_access' and
            not d.get('held_locks') and
            not d.get('synchronization')):
            unprotected.append((u, v, d))
    return unprotected


def find_concurrent_accesses_in_tig(G: nx.DiGraph) -> List[Dict]:
    """Find potential data races: multiple threads accessing same variable."""
    races = []
    
    # Find all variable nodes
    var_nodes = [n for n, d in G.nodes(data=True) if d.get('type') == 'variable']
    
    for var_node in var_nodes:
        # Find all threads accessing this variable
        accessing_threads = []
        for u, v, d in G.in_edges(var_node, data=True):
            if d.get('relation') == 'may_access' and u.startswith('thread:'):
                accessing_threads.append((u, d))
        
        # If multiple threads access and at least one writes, it's a potential race
        if len(accessing_threads) > 1:
            has_write = any(
                d.get('access_type') in ['WRITE', 'READ_WRITE', 'ATOMIC_WRITE', 'ATOMIC_CAS']
                for _, d in accessing_threads
            )
            
            if has_write:
                # Check if they're protected
                unprotected_writes = [
                    (thread, d) for thread, d in accessing_threads
                    if d.get('access_type') in ['WRITE', 'READ_WRITE', 'ATOMIC_WRITE', 'ATOMIC_CAS']
                    and not d.get('held_locks')
                    and not d.get('synchronization')
                ]
                
                if unprotected_writes:
                    races.append({
                        'variable': var_node.replace('var:', ''),
                        'threads': [t for t, _ in accessing_threads],
                        'accesses': [d for _, d in accessing_threads],
                        'unprotected_writes': len(unprotected_writes),
                        'severity': 'high' if len(unprotected_writes) > 1 else 'medium'
                    })
    
    return races


def analyze_tig_for_races(G: nx.DiGraph) -> Dict:
    """Comprehensive TIG analysis for concurrency issues."""
    unprotected = find_unprotected_accesses_in_tig(G)
    concurrent = find_concurrent_accesses_in_tig(G)
    
    return {
        'unprotected_accesses_count': len(unprotected),
        'concurrent_access_patterns': len(concurrent),
        'unprotected_accesses': unprotected[:10],  # Sample
        'concurrent_races': concurrent[:10],  # Sample
    }


# ---------------------------------------------------------------------------
# Cross-file TIG merging
# ---------------------------------------------------------------------------

def merge_tigs(tig_list: List[nx.DiGraph]) -> nx.DiGraph:
    """Merge multiple per-file TIG graphs into a unified project-wide graph.

    Strategy:
      - Variable nodes with the same name are merged into a single node,
        combining accesses from all files.
      - Thread, file, and sync nodes are kept distinct (prefixed by file).
      - Cross-file edges are created when threads from different files
        access the same variable.

    Args:
        tig_list: List of per-file TIG DiGraphs from build_tig_from_ir()

    Returns:
        Unified project-wide TIG DiGraph
    """
    merged = nx.DiGraph()

    # Track which files contribute to each variable
    var_files: Dict[str, List[str]] = {}  # var_name -> [file_paths]

    for tig in tig_list:
        for node_id, data in tig.nodes(data=True):
            ntype = data.get('type', '')

            if ntype == 'variable':
                # Merge variable nodes: if same var name exists, combine metadata
                if merged.has_node(node_id):
                    existing = merged.nodes[node_id]
                    # Combine accesses count
                    existing['num_accesses'] = existing.get('num_accesses', 0) + data.get('num_accesses', 0)
                    # Merge protection methods
                    existing_methods = set(existing.get('protection_methods', []))
                    existing_methods.update(data.get('protection_methods', []))
                    existing['protection_methods'] = list(existing_methods)
                    # Track cross-file presence
                    existing['cross_file'] = True
                    files = existing.get('source_files', [])
                    if data.get('source_file') and data['source_file'] not in files:
                        files.append(data['source_file'])
                    existing['source_files'] = files
                else:
                    merged.add_node(node_id, **data)
                    source = data.get('source_file', '')
                    merged.nodes[node_id]['source_files'] = [source] if source else []
                    merged.nodes[node_id]['cross_file'] = False

                var_name = data.get('name', node_id.replace('var:', ''))
                if var_name not in var_files:
                    var_files[var_name] = []
            else:
                # Non-variable nodes: add directly (they're unique per file)
                if not merged.has_node(node_id):
                    merged.add_node(node_id, **data)

        # Copy all edges
        for u, v, data in tig.edges(data=True):
            if merged.has_edge(u, v):
                # Edge exists — could be cross-file access to same var
                existing = merged.edges[u, v]
                existing['cross_file'] = True
            else:
                merged.add_edge(u, v, **data)

    # Track which files each variable appears in
    for tig in tig_list:
        for node_id, data in tig.nodes(data=True):
            if data.get('type') == 'variable':
                var_name = data.get('name', node_id.replace('var:', ''))
                # Find file nodes in this tig
                for pred in tig.predecessors(node_id):
                    pred_data = tig.nodes.get(pred, {})
                    if pred_data.get('type') == 'file':
                        file_path = pred_data.get('path', pred)
                        if file_path not in var_files.get(var_name, []):
                            var_files.setdefault(var_name, []).append(file_path)

    # Mark variables that appear in multiple files
    for var_name, files in var_files.items():
        var_node = f"var:{var_name}"
        if merged.has_node(var_node) and len(files) > 1:
            merged.nodes[var_node]['cross_file'] = True
            merged.nodes[var_node]['source_files'] = files

    return merged


# ---------------------------------------------------------------------------
# Happens-before reasoning
# ---------------------------------------------------------------------------

def compute_happens_before(tig: nx.DiGraph) -> nx.DiGraph:
    """Compute the transitive closure of happens-before relationships.

    Uses `spawns`, `barrier_after`, and `synchronized_with` edges to build
    a happens-before graph. If thread A spawns thread B, then all of A's
    pre-spawn actions happen-before all of B's actions.

    Args:
        tig: A TIG DiGraph (single-file or merged)

    Returns:
        A new DiGraph containing only happens-before edges with
        transitive closure applied.
    """
    hb = nx.DiGraph()

    # Collect ordering edges from the TIG
    ordering_relations = {'spawns', 'barrier_after', 'synchronized_with'}

    for u, v, data in tig.edges(data=True):
        rel = data.get('relation', '')
        if rel in ordering_relations:
            hb.add_edge(u, v, relation=rel, direct=True)

    # Compute transitive closure
    if hb.number_of_nodes() == 0:
        return hb

    closure = nx.transitive_closure(hb, reflexive=False)

    # Mark transitive (non-direct) edges
    for u, v in closure.edges():
        if not hb.has_edge(u, v):
            closure.edges[u, v]['relation'] = 'happens_before_transitive'
            closure.edges[u, v]['direct'] = False
        else:
            closure.edges[u, v].update(hb.edges[u, v])

    return closure


def can_race(tig: nx.DiGraph, thread_a: str, thread_b: str) -> bool:
    """Check whether two threads can potentially race.

    Two threads can race if neither happens-before the other in the
    happens-before graph. If A happens-before B or B happens-before A,
    they are ordered and cannot race on accesses separated by the
    ordering edge.

    Args:
        tig: A TIG DiGraph
        thread_a: Node ID of first thread
        thread_b: Node ID of second thread

    Returns:
        True if the threads are unordered (can race), False if ordered
    """
    hb = compute_happens_before(tig)

    a_before_b = hb.has_node(thread_a) and hb.has_node(thread_b) and \
                 nx.has_path(hb, thread_a, thread_b)
    b_before_a = hb.has_node(thread_a) and hb.has_node(thread_b) and \
                 nx.has_path(hb, thread_b, thread_a)

    # If neither is ordered before the other, they can race
    return not a_before_b and not b_before_a
