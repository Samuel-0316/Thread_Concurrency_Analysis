import json
import os
from typing import Any, Dict, List, Optional

import networkx as nx
from networkx.readwrite import json_graph


class ConcurrencyKG:
    """Lightweight concurrency knowledge graph backed by NetworkX.

    Persistence is JSON via NetworkX node-link format. Keep API minimal.
    """

    def __init__(self):
        self.graph = nx.DiGraph()

    # ── Core mutation API ─────────────────────────────────────────

    def add_node(self, node_id: str, **attrs: Any) -> None:
        self.graph.add_node(node_id, **attrs)

    def add_edge(self, src: str, dst: str, rel: str, **attrs: Any) -> None:
        self.graph.add_edge(src, dst, relation=rel, **attrs)

    def add_finding(self, finding_id: str, meta: Optional[Dict[str, Any]] = None) -> None:
        meta = meta or {}
        self.add_node(finding_id, type='finding', **meta)

    def add_thread_relation(self, src: str, dst: str, relation: str = 'happens_before') -> None:
        self.add_edge(src, dst, relation)

    # ── Build from TIG + findings ────────────────────────────────

    def build_from_tig(self, tig_graph: nx.DiGraph, findings: Optional[Dict] = None) -> None:
        """Populate the KG by copying all nodes/edges from a TIG graph and
        adding finding nodes from the static analysis results.

        This bridges Phase 2 (TIG) → Phase 7 (KG) in the architecture.
        """
        # Import TIG nodes
        for node_id, data in tig_graph.nodes(data=True):
            self.graph.add_node(node_id, **data)

        # Import TIG edges
        for u, v, data in tig_graph.edges(data=True):
            self.graph.add_edge(u, v, **data)

        # Import findings as typed nodes
        if not findings:
            return

        for idx, issue in enumerate(findings.get('openmp_races', [])):
            fid = f"finding:omp_race_{idx}"
            var = self._extract_var(issue)
            self.add_node(fid, type='finding', subtype='omp_race',
                          variable=var, severity=self._extract_severity(issue))
            var_node = f"var:{var}"
            if self.graph.has_node(var_node):
                self.add_edge(fid, var_node, 'detected_in')

        for idx, issue in enumerate(findings.get('data_races', [])):
            fid = f"finding:data_race_{idx}"
            var = self._extract_var(issue)
            self.add_node(fid, type='finding', subtype='data_race',
                          variable=var, severity=self._extract_severity(issue))
            var_node = f"var:{var}"
            if self.graph.has_node(var_node):
                self.add_edge(fid, var_node, 'detected_in')

        for idx, issue in enumerate(findings.get('unprotected_accesses', [])):
            fid = f"finding:unprotected_{idx}"
            if hasattr(issue, 'accesses') and issue.accesses:
                a = issue.accesses[0]
                var = a.variable_name
                thread_id = a.thread_id
                line = a.line_number
            elif isinstance(issue, dict):
                var = issue.get('variable', '?')
                thread_id = issue.get('thread', None)
                line = issue.get('line', 0)
            else:
                var = str(issue)
                thread_id = None
                line = 0

            self.add_node(fid, type='finding', subtype='unprotected_access',
                          variable=var, line=line,
                          severity=self._extract_severity(issue))
            var_node = f"var:{var}"
            if self.graph.has_node(var_node):
                self.add_edge(fid, var_node, 'detected_in')
            if thread_id:
                thread_node = f"thread:{thread_id}"
                if self.graph.has_node(thread_node):
                    self.add_edge(thread_node, fid, 'triggers')

        for idx, issue in enumerate(findings.get('unsynchronized_accesses', [])):
            fid = f"finding:unsync_{idx}"
            if isinstance(issue, dict):
                var = issue.get('variable', '?')
            else:
                var = str(issue)
            self.add_node(fid, type='finding', subtype='unsynchronized',
                          variable=var,
                          severity=self._extract_severity(issue))
            var_node = f"var:{var}"
            if self.graph.has_node(var_node):
                self.add_edge(fid, var_node, 'detected_in')

        for idx, issue in enumerate(findings.get('lock_order_violations', [])):
            fid = f"finding:lock_order_{idx}"
            self.add_node(fid, type='finding', subtype='lock_order_violation')

    # ── Developer query API ──────────────────────────────────────

    def threads_for_variable(self, var_name: str) -> List[str]:
        """Return thread IDs that access a given variable."""
        var_node = f"var:{var_name}"
        if not self.graph.has_node(var_node):
            return []
        threads = []
        for pred in self.graph.predecessors(var_node):
            d = self.graph.nodes[pred]
            if d.get('type') == 'thread':
                threads.append(pred)
        return threads

    def locks_for_variable(self, var_name: str) -> List[str]:
        """Return sync/lock nodes that protect a given variable."""
        var_node = f"var:{var_name}"
        if not self.graph.has_node(var_node):
            return []
        locks = []
        for pred in self.graph.predecessors(var_node):
            edge_data = self.graph.edges[pred, var_node]
            rel = edge_data.get('relation', '')
            if rel in ('protected_by', 'atomic_access', 'reduction_scope'):
                locks.append(pred)
        # Also check sync nodes connected to threads that access this var
        for thread in self.threads_for_variable(var_name):
            for succ in self.graph.successors(thread):
                d = self.graph.nodes.get(succ, {})
                if d.get('type') == 'sync':
                    locks.append(succ)
        return list(set(locks))

    def findings_for_variable(self, var_name: str) -> List[Dict[str, Any]]:
        """Return all findings that affect a given variable."""
        var_node = f"var:{var_name}"
        if not self.graph.has_node(var_node):
            return []
        results = []
        for pred in self.graph.predecessors(var_node):
            d = self.graph.nodes[pred]
            if d.get('type') == 'finding':
                results.append({'id': pred, **d})
        return results

    def unguarded_writes(self) -> List[Dict[str, Any]]:
        """Return all unprotected/unsynchronized findings in the KG."""
        results = []
        for n, d in self.graph.nodes(data=True):
            if d.get('type') == 'finding' and d.get('subtype') in (
                    'unprotected_access', 'unsynchronized', 'omp_race', 'data_race'):
                results.append({'id': n, **d})
        return results

    def variable_summary(self, var_name: str) -> Dict[str, Any]:
        """Get a complete summary for a variable: threads, locks, findings, protection status."""
        threads = self.threads_for_variable(var_name)
        locks = self.locks_for_variable(var_name)
        findings = self.findings_for_variable(var_name)
        return {
            'variable': var_name,
            'threads': threads,
            'locks': locks,
            'findings': findings,
            'is_protected': len(locks) > 0,
            'thread_count': len(threads),
            'finding_count': len(findings),
        }

    def all_variable_summaries(self) -> List[Dict[str, Any]]:
        """Get summaries for every variable in the KG."""
        var_nodes = [n for n, d in self.graph.nodes(data=True) if d.get('type') == 'variable']
        summaries = []
        for vn in var_nodes:
            name = self.graph.nodes[vn].get('name', vn.replace('var:', ''))
            summaries.append(self.variable_summary(name))
        return summaries

    # ── Persistence ──────────────────────────────────────────────

    def persist(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else '.', exist_ok=True)
        data = json_graph.node_link_data(self.graph)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, path: str) -> 'ConcurrencyKG':
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        g = json_graph.node_link_graph(data)
        obj = cls()
        obj.graph = g
        return obj

    # ── Basic queries ────────────────────────────────────────────

    def query_by_type(self, ntype: str) -> List[str]:
        return [n for n, d in self.graph.nodes(data=True) if d.get('type') == ntype]

    def find_edges(self, relation: Optional[str] = None) -> List[Dict[str, Any]]:
        out = []
        for u, v, d in self.graph.edges(data=True):
            if relation and d.get('relation') != relation:
                continue
            out.append({'src': u, 'dst': v, **d})
        return out

    def stats(self) -> Dict[str, Any]:
        """Quick statistics about the KG contents."""
        type_counts = {}
        for _, d in self.graph.nodes(data=True):
            t = d.get('type', 'unknown')
            type_counts[t] = type_counts.get(t, 0) + 1

        rel_counts = {}
        for _, _, d in self.graph.edges(data=True):
            r = d.get('relation', 'unknown')
            rel_counts[r] = rel_counts.get(r, 0) + 1

        return {
            'nodes': self.graph.number_of_nodes(),
            'edges': self.graph.number_of_edges(),
            'node_types': type_counts,
            'edge_relations': rel_counts,
        }

    # ── Cross-file analysis ──────────────────────────────────────

    def detect_cross_file_patterns(self) -> List[Dict[str, Any]]:
        """Find variables accessed from threads in different files without consistent locking.

        Returns a list of cross-file risk patterns, each with:
          - variable: the shared variable name
          - files: list of files that access it
          - threads: threads from different files accessing it
          - is_protected: whether all accesses are under locks
          - risk: 'high' if unprotected cross-file writes, 'medium' otherwise
        """
        patterns = []

        for node_id, data in self.graph.nodes(data=True):
            if data.get('type') != 'variable':
                continue

            source_files = data.get('source_files', [])
            cross_file = data.get('cross_file', False)

            if not cross_file and len(source_files) <= 1:
                continue

            var_name = data.get('name', node_id.replace('var:', ''))

            # Find all threads accessing this variable
            threads = self.threads_for_variable(var_name)
            locks = self.locks_for_variable(var_name)
            findings = self.findings_for_variable(var_name)

            # Determine files of accessing threads
            thread_files = set()
            for t in threads:
                t_data = self.graph.nodes.get(t, {})
                t_source = t_data.get('source', t_data.get('file_path', ''))
                if t_source:
                    thread_files.add(t_source)

            is_protected = len(locks) > 0
            has_findings = len(findings) > 0

            if len(source_files) > 1 or len(thread_files) > 1:
                risk = 'high' if (not is_protected and has_findings) else \
                       'medium' if not is_protected else 'low'

                patterns.append({
                    'variable': var_name,
                    'files': source_files or list(thread_files),
                    'threads': threads,
                    'locks': locks,
                    'is_protected': is_protected,
                    'finding_count': len(findings),
                    'risk': risk,
                })

        # Sort by risk
        risk_order = {'high': 0, 'medium': 1, 'low': 2}
        patterns.sort(key=lambda p: risk_order.get(p['risk'], 3))
        return patterns

    def cross_file_summary(self) -> Dict[str, Any]:
        """Quick summary of cross-file concurrency risks."""
        patterns = self.detect_cross_file_patterns()
        return {
            'total_cross_file_vars': len(patterns),
            'high_risk': sum(1 for p in patterns if p['risk'] == 'high'),
            'medium_risk': sum(1 for p in patterns if p['risk'] == 'medium'),
            'low_risk': sum(1 for p in patterns if p['risk'] == 'low'),
            'patterns': patterns,
        }

    # ── Internal helpers ─────────────────────────────────────────

    @staticmethod
    def _extract_var(issue) -> str:
        if isinstance(issue, dict):
            v = issue.get('variable', '?')
        else:
            v = getattr(issue, 'variable', '?')
        if v and hasattr(v, 'name'):
            v = v.name
        return v or '?'

    @staticmethod
    def _extract_severity(issue) -> str:
        if isinstance(issue, dict):
            return issue.get('severity', 'medium')
        return getattr(issue, 'severity', 'medium')

