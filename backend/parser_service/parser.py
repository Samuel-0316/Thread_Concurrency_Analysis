import os
import ast
import re
import json
from typing import List, Dict

SUPPORTED_EXT = {'.py', '.c', '.h'}

# optional tree-sitter C parser (graceful fallback)
try:
    from backend.parser_service.tree_sitter_c import TreeSitterCParser
    TS_PARSER = TreeSitterCParser()
    if not TS_PARSER.is_available():
        TS_PARSER = None
except Exception:
    TS_PARSER = None


def _attach_parents(node):
    for child in ast.iter_child_nodes(node):
        child.parent = node
        _attach_parents(child)


class PythonAccessVisitor(ast.NodeVisitor):
    def __init__(self):
        self.accesses = []
        self.scope_stack = ['main']
        
    def visit_FunctionDef(self, node):
        self.scope_stack.append(node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Store):
            atype = 'write'
        elif isinstance(node.ctx, ast.Load):
            atype = 'read'
        else:
            atype = 'read_write'
            
        self.accesses.append({
            'variable_name': node.id,
            'access_type': atype,
            'line_number': node.lineno,
            'function': self.scope_stack[-1]
        })
        self.generic_visit(node)


class ParserService:
    """Parser service with tightened heuristics and optional tree-sitter support.

    - For Python: improved AST-based detection of reads/writes, thread creation, lock usage.
    - For C: enhanced regex-based detection of globals, lock ops, and read/write occurrences.
    - Tree-sitter integration is attempted if available; code falls back gracefully.
    """

    def parse_repo(self, repo_path: str) -> List[Dict]:
        results = []
        for root, _, files in os.walk(repo_path):
            for f in files:
                _, ext = os.path.splitext(f)
                if ext.lower() in SUPPORTED_EXT:
                    path = os.path.join(root, f)
                    try:
                        r = self.parse_file(path)
                        if r:
                            results.append(r)
                    except Exception as e:
                        print(f"Failed to parse {path}: {e}")
        return results

    def parse_file(self, path: str) -> Dict:
        _, ext = os.path.splitext(path)
        if ext == '.py':
            return self._parse_python(path)
        elif ext in ('.c', '.h'):
            return self._parse_c(path)
        else:
            return {}

    def _parse_python(self, path: str) -> Dict:
        with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
            src = fh.read()
        tree = ast.parse(src, filename=path)
        _attach_parents(tree)

        threads = []
        locks = []
        lock_names = set()
        shared_vars = set()
        var_reads = set()
        var_writes = set()

        # find threading.Thread targets and Lock usage
        for node in ast.walk(tree):
            # Thread instantiation: threading.Thread(...) or Thread(...)
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and getattr(func, 'attr', '') == 'Thread':
                    threads.append({'type': 'thread_call', 'lineno': node.lineno})
                    # try to capture target arg name
                    for kw in node.keywords:
                        if kw.arg == 'target' and isinstance(kw.value, ast.Name):
                            threads[-1]['target'] = kw.value.id
                elif isinstance(func, ast.Name) and func.id == 'Thread':
                    threads.append({'type': 'thread_call', 'lineno': node.lineno})
                # Lock instantiation detection
                if isinstance(func, ast.Attribute) and getattr(func, 'attr', '') == 'Lock':
                    # e.g., threading.Lock()
                    locks.append({'type': 'lock_inst', 'lineno': node.lineno})
                if isinstance(func, ast.Name) and func.id == 'Lock':
                    locks.append({'type': 'lock_inst', 'lineno': node.lineno})
                # lock acquire/release
                if isinstance(func, ast.Attribute) and func.attr in ('acquire', 'release'):
                    val = func.value
                    if isinstance(val, ast.Name):
                        locks.append({'type': 'lock_op', 'op': func.attr, 'name': val.id, 'lineno': node.lineno})

            # with lock: context
            if isinstance(node, ast.With):
                for item in node.items:
                    ctx = item.context_expr
                    if isinstance(ctx, ast.Name):
                        locks.append({'type': 'lock_use', 'name': ctx.id, 'lineno': node.lineno})
                        lock_names.add(ctx.id)

        # Variable read/write detection via AST Name contexts
        visitor = PythonAccessVisitor()
        visitor.visit(tree)
        var_accesses = visitor.accesses
        
        # Populate var_reads and var_writes for backwards compatibility
        for acc in var_accesses:
            if acc['access_type'] in ('read', 'read_write'):
                var_reads.add(acc['variable_name'])
            if acc['access_type'] in ('write', 'read_write'):
                var_writes.add(acc['variable_name'])

        # heuristics: top-level assigned names as shared vars
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        shared_vars.add(t.id)

        return {
            'path': path,
            'language': 'python',
            'threads': threads,
            'locks': locks,
            'lock_names': list(lock_names),
            'shared_variables': list(shared_vars),
            'var_reads': list(var_reads),
            'var_writes': list(var_writes),
            'var_accesses': var_accesses,
        }

    def _parse_c(self, path: str) -> Dict:
        # If tree-sitter is available, ask it for OpenMP pragmas (merge later).
        ts_res = None
        if TS_PARSER:
            try:
                ts_res = TS_PARSER.parse_file(path)
            except Exception as e:
                print(f"tree-sitter parse failed for {path}: {e}")

        with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
            src = fh.read()

        threads = []
        locks = []
        shared_vars = []
        var_reads = []
        var_writes = []
        var_accesses = []
        omp_pragmas = []
        omp_shared = set()
        omp_private = set()
        omp_firstprivate = set()
        omp_lastprivate = set()
        omp_reduction = set()
        omp_reduction_map = {}
        omp_default = None
        omp_critical_vars = set()
        omp_critical_name = None
        omp_has_nowait = False
        pending_omp_for_line = None

        c_keywords = {
            'auto', 'break', 'case', 'char', 'const', 'continue', 'default', 'do',
            'double', 'else', 'enum', 'extern', 'float', 'for', 'goto', 'if',
            'inline', 'int', 'long', 'register', 'restrict', 'return', 'short',
            'signed', 'sizeof', 'static', 'struct', 'switch', 'typedef', 'union',
            'unsigned', 'void', 'volatile', 'while', '_Bool', '_Complex', '_Imaginary'
        }

        c_builtins = {
            'printf', 'scanf', 'fprintf', 'sprintf', 'snprintf', 'puts', 'putchar',
            'malloc', 'calloc', 'realloc', 'free', 'memset', 'memcpy', 'memcmp',
            'strlen', 'strcpy', 'strncpy', 'strcmp', 'strncmp', 'exit', 'abort'
        }

        def _split_clause_vars(raw):
            return [v.strip() for v in raw.split(',') if v.strip()]

        def _strip_comments(line):
            line = re.sub(r'//.*', '', line)
            line = re.sub(r'/\*.*?\*/', '', line)
            return line

        def _record_line_accesses(line, lineno, active_omp_kind=None, active_omp_line=None):
            # Capture scalar and array writes like x =, x +=, x++, x[i] = ...
            write_hits = []
            line = _strip_comments(line)
            if not line.strip():
                return
            for m in re.finditer(r'\b([_a-zA-Z][_a-zA-Z0-9]*)\s*(?:\[([^\]]+)\])?\s*(?:\+\+|--|\+=|-=|\*=|/=|=)', line):
                var = m.group(1)
                if var in c_keywords or var in c_builtins:
                    continue
                index_expr = m.group(2) or ''
                index_vars = []
                if index_expr:
                    index_vars = re.findall(r'\b([_a-zA-Z][_a-zA-Z0-9]*)\b', index_expr)
                var_writes.append(var)
                write_hits.append(var)
                var_accesses.append({
                    'variable_name': var,
                    'line_number': lineno,
                    'access_type': 'write',
                    'thread_hint': f"omp_{active_omp_kind}_{active_omp_line}" if active_omp_kind else None,
                    'omp_kind': active_omp_kind,
                    'omp_line': active_omp_line,
                    'index_vars': index_vars,
                })

            # Coarse read capture for names appearing on a line.
            for m in re.finditer(r'\b([_a-zA-Z][_a-zA-Z0-9]*)\b', line):
                var = m.group(1)
                if var in c_keywords or var in c_builtins:
                    continue
                var_reads.append(var)
                # Skip names already recorded as writes on this same line
                if var in write_hits:
                    continue
                var_accesses.append({
                    'variable_name': var,
                    'line_number': lineno,
                    'access_type': 'read',
                    'thread_hint': f"omp_{active_omp_kind}_{active_omp_line}" if active_omp_kind else None,
                    'omp_kind': active_omp_kind,
                    'omp_line': active_omp_line,
                })

        # find pthread_create occurrences
        for m in re.finditer(r'pthread_create\s*\(', src):
            lineno = src[:m.start()].count('\n') + 1
            threads.append({'type': 'pthread_create', 'lineno': lineno})

        # mutex ops
        for m in re.finditer(r'pthread_mutex_(lock|unlock|trylock)\s*\(\s*(&?[_a-zA-Z][_a-zA-Z0-9]*)', src):
            lineno = src[:m.start()].count('\n') + 1
            lock_name = m.group(2).lstrip('&')
            locks.append({'type': 'mutex_op', 'op': m.group(1), 'lineno': lineno, 'name': lock_name})

        # OpenMP pragma extraction. Join continuation lines ending in '\\'.
        lines = src.splitlines()
        idx = 0
        while idx < len(lines):
            stripped = lines[idx].strip()
            if not stripped.startswith('#pragma omp'):
                idx += 1
                continue

            start_line = idx + 1
            pragma_parts = [stripped.rstrip('\\').strip()]
            while stripped.endswith('\\') and idx + 1 < len(lines):
                idx += 1
                stripped = lines[idx].strip()
                pragma_parts.append(stripped.rstrip('\\').strip())
            pragma_text = ' '.join(part for part in pragma_parts if part)

            kind = 'unknown'
            if 'parallel for' in pragma_text:
                kind = 'parallel_for'
            elif re.search(r'\bparallel\b', pragma_text):
                kind = 'parallel'
            elif re.search(r'\bfor\b', pragma_text):
                kind = 'for'
            elif re.search(r'\bsingle\b', pragma_text):
                kind = 'single'
            elif re.search(r'\bcritical\b', pragma_text):
                kind = 'critical'
                # extract critical region name if present: critical(name)
                crit_match = re.search(r'critical\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\)', pragma_text)
                if crit_match:
                    omp_critical_name = crit_match.group(1)
            elif re.search(r'\batomic\b', pragma_text):
                kind = 'atomic'
            elif re.search(r'\breduction\b', pragma_text):
                kind = 'reduction'
            elif re.search(r'\btaskwait\b', pragma_text):
                kind = 'taskwait'
            elif re.search(r'\btask\b', pragma_text):
                kind = 'task'
            elif re.search(r'\bbarrier\b', pragma_text):
                kind = 'barrier'

            default_match = re.search(r'default\s*\(([^)]*)\)', pragma_text)
            if default_match:
                omp_default = default_match.group(1).strip()

            # Check for nowait clause
            if 'nowait' in pragma_text:
                omp_has_nowait = True

            omp_pragmas.append({'kind': kind, 'line': start_line, 'text': pragma_text})
            if kind in ('parallel_for', 'for'):
                pending_omp_for_line = start_line
            if kind == 'critical' and omp_critical_name:
                omp_pragmas[-1]['critical_name'] = omp_critical_name
                omp_critical_name = None

            if kind == 'critical':
                # Capture identifiers used in the next few lines after a critical pragma.
                # This is a coarse way to mark variables that are likely guarded.
                lookahead = []
                cursor = idx + 1
                while cursor < len(lines) and len(lookahead) < 8:
                    candidate = lines[cursor].strip()
                    if candidate.startswith('#pragma omp'):
                        break
                    if candidate:
                        lookahead.append(candidate)
                    cursor += 1
                for body_line in lookahead:
                    for var in re.findall(r'\b([_a-zA-Z][_a-zA-Z0-9]*)\b', body_line):
                        if var not in {'if', 'for', 'while', 'return', 'sizeof', 'int', 'long', 'char', 'float', 'double'}:
                            omp_critical_vars.add(var)

            for clause_name, target_set in (
                ('shared', omp_shared),
                ('private', omp_private),
                ('firstprivate', omp_firstprivate),
                ('lastprivate', omp_lastprivate),
                ('reduction', omp_reduction),
            ):
                match = re.search(rf'{clause_name}\s*\(([^)]*)\)', pragma_text)
                if match:
                    raw = match.group(1)
                    vars_ = _split_clause_vars(raw)
                    if clause_name == 'reduction':
                        # items may be like '+:x' or 'max:val'
                        for item in vars_:
                            parts = item.split(':', 1)
                            if len(parts) == 2:
                                op = parts[0].strip()
                                var = parts[1].strip()
                            else:
                                # if no explicit op, treat as unknown
                                op = ''
                                var = parts[0].strip()
                            omp_reduction.add(var)
                            omp_reduction_map[var] = op
                    else:
                        target_set.update(vars_)

            idx += 1
            # Capture the loop induction variable after a parallel for pragma
            if pending_omp_for_line and idx < len(lines):
                next_line = lines[idx].strip()
                if next_line.startswith('for'):
                    loop_match = re.search(r'for\s*\(\s*([_a-zA-Z][_a-zA-Z0-9]*)\s*=', next_line)
                    if loop_match:
                        omp_private.add(loop_match.group(1))
                        pending_omp_for_line = None
                elif idx - pending_omp_for_line > 3:
                    pending_omp_for_line = None

        # capture writes/reads and shared globals across the file, preserving line numbers
        active_omp_kind = None
        active_omp_line = None
        parallel_brace_depth = 0  # tracks { } nesting for parallel region scope
        awaiting_open_brace = False  # True after pragma, waiting for first {
        single_stmt_mode = False  # True for braceless single-statement parallel regions
        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()

            # Handle continuation lines for multi-line pragmas
            if stripped.startswith('#pragma omp'):
                # Detect the pragma kind
                pragma_kind = None
                if 'parallel for' in stripped:
                    pragma_kind = 'parallel_for'
                elif re.search(r'\bparallel\b', stripped):
                    pragma_kind = 'parallel'
                elif re.search(r'\bfor\b', stripped):
                    pragma_kind = 'for'
                elif re.search(r'\bcritical\b', stripped):
                    pragma_kind = 'critical'
                elif re.search(r'\batomic\b', stripped):
                    pragma_kind = 'atomic'
                elif re.search(r'\breduction\b', stripped):
                    pragma_kind = 'reduction'
                elif re.search(r'\bbarrier\b', stripped):
                    pragma_kind = 'barrier'
                elif re.search(r'\bordered\b', stripped):
                    pragma_kind = 'ordered'
                elif re.search(r'\bmaster\b', stripped):
                    pragma_kind = 'master'
                elif re.search(r'\bsingle\b', stripped):
                    pragma_kind = 'single'
                active_omp_kind = pragma_kind
                active_omp_line = lineno
                # For constructs that introduce a code block, start scope tracking
                if pragma_kind in ('parallel_for', 'parallel', 'for', 'critical',
                                   'single', 'master', 'ordered', 'task'):
                    awaiting_open_brace = True
                    parallel_brace_depth = 0
                    single_stmt_mode = False
                continue

            # Scope tracking: brace depth for parallel regions
            if awaiting_open_brace and stripped:
                open_count = stripped.count('{')
                close_count = stripped.count('}')
                if open_count > 0:
                    # Found the opening brace
                    awaiting_open_brace = False
                    parallel_brace_depth = open_count - close_count
                    if parallel_brace_depth <= 0:
                        # Single-line block like: for (...) { stmt; }
                        active_omp_kind = None
                        active_omp_line = None
                        parallel_brace_depth = 0
                    _record_line_accesses(line, lineno, active_omp_kind, active_omp_line)
                    continue
                else:
                    # No brace yet — could be the `for(...)` line, or a single-statement body
                    # If it's a for/while/if header, keep waiting
                    if re.match(r'^(for|while|if)\s*\(', stripped):
                        _record_line_accesses(line, lineno, active_omp_kind, active_omp_line)
                        continue
                    else:
                        # Single-statement body (no braces)
                        single_stmt_mode = True
                        awaiting_open_brace = False
                        _record_line_accesses(line, lineno, active_omp_kind, active_omp_line)
                        # After this single statement, exit the parallel scope
                        active_omp_kind = None
                        active_omp_line = None
                        single_stmt_mode = False
                        continue

            if parallel_brace_depth > 0 and stripped:
                open_count = stripped.count('{')
                close_count = stripped.count('}')
                parallel_brace_depth += open_count - close_count
                if parallel_brace_depth <= 0:
                    # Parallel region just closed
                    _record_line_accesses(line, lineno, active_omp_kind, active_omp_line)
                    active_omp_kind = None
                    active_omp_line = None
                    parallel_brace_depth = 0
                    continue

            _record_line_accesses(line, lineno, active_omp_kind, active_omp_line)

        # simple global variable detection: scan declaration-like top-level lines only
        # This avoids mistakenly treating function names (e.g. main) as shared variables.
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('#') or stripped.startswith('/*') or stripped.startswith('*'):
                continue
            if '(' in stripped or ')' in stripped:
                continue
            # Strip trailing comments before checking for semicolon
            code_part = re.sub(r'/\*.*?\*/', '', stripped)  # remove /* ... */
            code_part = re.sub(r'//.*$', '', code_part)      # remove // ...
            code_part = code_part.rstrip()
            if not code_part.endswith(';'):
                continue
            decl_match = re.search(
                r'\b(?:volatile\s+)?(?:static\s+)?(?:unsigned\s+)?(?:int|long|char|float|double)\s+([_a-zA-Z][_a-zA-Z0-9]*)',
                stripped,
            )
            if decl_match:
                shared_vars.append(decl_match.group(1))

        # crude read/write detection for globals: search occurrences and assignment patterns
        for v in shared_vars:
            # write patterns: 'v =' or 'v+=' or 'v++' or '++v' or 'v--'
            write_re = re.compile(r'\b' + re.escape(v) + r'\s*(?:=|\+=|-=|\+\+|--)|\+\+' + re.escape(v) + r'|--' + re.escape(v))
            read_re = re.compile(r'\b' + re.escape(v) + r'\b')
            if write_re.search(src):
                var_writes.append(v)
            if read_re.search(src):
                var_reads.append(v)

        # If tree-sitter returned OpenMP info, merge/override where useful
        if ts_res:
            # prefer tree-sitter extracted pragma list if present
            if ts_res.get('omp_pragmas'):
                omp_pragmas = ts_res.get('omp_pragmas')
            for key, target in (('omp_shared', omp_shared), ('omp_private', omp_private), ('omp_firstprivate', omp_firstprivate), ('omp_lastprivate', omp_lastprivate), ('omp_reduction', omp_reduction), ('omp_critical_vars', omp_critical_vars)):
                vals = ts_res.get(key)
                if vals:
                    try:
                        target.update(vals)
                    except Exception:
                        pass

        return {
            'path': path,
            'language': 'c',
            'threads': threads,
            'locks': locks,
            'shared_variables': shared_vars,
            'var_reads': var_reads,
            'var_writes': var_writes,
            'var_accesses': var_accesses,
            'omp_pragmas': omp_pragmas,
            'omp_shared': list(omp_shared),
            'omp_private': list(omp_private),
            'omp_firstprivate': list(omp_firstprivate),
            'omp_lastprivate': list(omp_lastprivate),
            'omp_reduction': list(omp_reduction),
            'omp_reduction_map': omp_reduction_map,
            'omp_default': omp_default,
            'omp_critical_vars': list(omp_critical_vars),
            'omp_has_nowait': omp_has_nowait,
        }
