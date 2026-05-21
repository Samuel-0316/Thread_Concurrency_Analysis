import os
from pathlib import Path

try:
    from tree_sitter import Language, Parser
    TS_AVAILABLE = True
except Exception:
    TS_AVAILABLE = False


def _get_vendor_lib_path(lang_name):
    """Get the path to a compiled vendor language library.
    
    Returns the path to the .dll or .so file for the given language name,
    or None if the library doesn't exist.
    """
    vendor_dir = Path(__file__).parent / "vendor" / "lib"
    
    # Try platform-specific extensions
    for ext in ['.dll', '.so', '.dylib']:
        lib_path = vendor_dir / f"{lang_name}{ext}"
        if lib_path.exists():
            return str(lib_path.resolve())
    
    return None


class TreeSitterCParser:
    """Lightweight Tree-sitter wrapper for C to begin integration.

    Behavior:
    - Loads compiled C language from backend/parser_service/vendor/lib/ (built by build_grammars.py).
    - Provides `is_available()` and `parse_file()`.
    - Currently extracts OpenMP pragmas via preprocessor nodes and returns
      the same keys used by the regex-based parser (so we can merge results).
    - Falls back gracefully when Tree-sitter or compiled libraries are not available.
    """

    def __init__(self):
        self.available = False
        self.parser = None
        if not TS_AVAILABLE:
            return

        try:
            self.Parser = Parser
            
            # Load compiled C language from vendor/lib/
            c_lib_path = _get_vendor_lib_path('c')
            if c_lib_path:
                C_LANG = Language(c_lib_path, 'c')
                self.parser = Parser()
                self.parser.set_language(C_LANG)
                self.available = True
            
        except Exception as e:
            self.available = False

    def is_available(self):
        return self.available

    def parse_file(self, path: str):
        if not self.available:
            return None
        with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
            code = fh.read()
        return self.parse_code(code)

    def parse_code(self, code: str):
        if not self.available:
            return None
        tree = self.parser.parse(bytes(code, 'utf8'))
        root = tree.root_node

        omp_pragmas = []
        omp_shared = set()
        omp_private = set()
        omp_firstprivate = set()
        omp_lastprivate = set()
        omp_reduction = set()
        omp_critical_vars = set()

        # walk the tree and collect preprocessor nodes that contain '#pragma omp'
        stack = [root]
        while stack:
            node = stack.pop()
            # add children
            try:
                stack.extend(node.children)
            except Exception:
                pass

            # many C grammars expose preprocessor directives as 'preproc' or 'preproc_directive'
            ntype = getattr(node, 'type', '')
            if 'preproc' in ntype or ntype == 'preproc_directive':
                start = node.start_byte
                end = node.end_byte
                try:
                    text = code[start:end]
                except Exception:
                    # defensive
                    continue
                if '#pragma omp' in text:
                    text_str = text.strip().replace('\\\n', ' ').replace('\n', ' ')
                    lineno = code[:start].count('\n') + 1

                    kind = 'unknown'
                    if 'parallel for' in text_str:
                        kind = 'parallel_for'
                    elif 'parallel' in text_str:
                        kind = 'parallel'
                    elif 'for' in text_str:
                        kind = 'for'
                    elif 'single' in text_str:
                        kind = 'single'
                    elif 'critical' in text_str:
                        kind = 'critical'
                    elif 'atomic' in text_str:
                        kind = 'atomic'
                    elif 'reduction' in text_str:
                        kind = 'reduction'

                    omp_pragmas.append({'kind': kind, 'line': lineno, 'text': text_str})

                    # extract clause vars using regex (simple, consistent with existing parser)
                    import re

                    def _split_clause_vars(raw):
                        return [v.strip() for v in raw.split(',') if v.strip()]

                    for clause_name, target_set in (
                        ('shared', omp_shared),
                        ('private', omp_private),
                        ('firstprivate', omp_firstprivate),
                        ('lastprivate', omp_lastprivate),
                        ('reduction', omp_reduction),
                    ):
                        match = re.search(rf'{clause_name}\s*\(([^)]*)\)', text_str)
                        if match:
                            vars_ = _split_clause_vars(match.group(1))
                            if clause_name == 'reduction':
                                cleaned = []
                                for item in vars_:
                                    cleaned.append(item.split(':', 1)[-1].strip())
                                vars_ = cleaned
                            target_set.update(vars_)

        return {
            'omp_pragmas': omp_pragmas,
            'omp_shared': list(omp_shared),
            'omp_private': list(omp_private),
            'omp_firstprivate': list(omp_firstprivate),
            'omp_lastprivate': list(omp_lastprivate),
            'omp_reduction': list(omp_reduction),
            'omp_critical_vars': list(omp_critical_vars),
        }
