# Tree-Sitter Integration Guide

## Current Status

The project has a modular Tree-sitter integration architecture:
- **Regex-based parser** (active): Fully functional C and Python parsing with OpenMP awareness
- **Tree-sitter wrapper** (prepared): Ready to load compiled language libraries when available
- **Vendor management** (established): Build system in place to manage and compile grammars

## Directory Structure

```
backend/parser_service/
├── vendor/
│   ├── build_grammars.py       # Build script for compiling grammars
│   ├── grammars/               # Cloned grammar repositories
│   │   ├── tree-sitter-c/
│   │   └── tree-sitter-python/
│   ├── lib/                    # Compiled language libraries (.dll/.so)
│   │   ├── c.dll
│   │   └── python.dll
│   ├── tree_sitter_c.py        # Tree-sitter language loader
│   └── parser.py               # Main parser with graceful fallback
```

## How It Works

1. **At startup**, the parser attempts to load compiled Tree-sitter language libraries from `vendor/lib/`.
2. **If Tree-sitter unavailable** or libraries fail to load, the parser falls back to the regex-based heuristics.
3. **Results are merged** when both systems are active, improving accuracy.

## Setup Instructions

### Option 1: Use Pre-built Wheels (Recommended)

```bash
pip install tree-sitter-languages
```

This package provides pre-compiled language bindings. The wrapper will auto-detect them.

### Option 2: Manual Compilation (Current Project Setup)

The project includes a build script that compiles Tree-sitter grammars locally:

```bash
# Build compiled language libraries
python backend/parser_service/vendor/build_grammars.py
```

This will:
1. Clone tree-sitter-c and tree-sitter-python from GitHub
2. Compile them into platform-specific shared libraries (.dll on Windows, .so on Unix)
3. Store them in `vendor/lib/`

**Requirements for manual compilation:**
- `git` (for cloning grammar repos)
- A C compiler (`gcc`, `clang`, or MSVC)
- tree-sitter Python package (`pip install tree-sitter`)

### Option 3: Future Enhancement - Python Bindings

For production use, consider:
- Using `tree-sitter-languages` package (PyPI)
- Or building language libraries using `Language.build_library()` (requires tree-sitter 0.20.8+)
- Or compiling via npm + tree-sitter-cli (requires Node.js)

## Testing Tree-Sitter Integration

To verify Tree-sitter is active:

```python
from backend.parser_service.parser import TS_PARSER
print(f"Tree-sitter available: {TS_PARSER is not None}")
if TS_PARSER:
    print(f"Active: {TS_PARSER.is_available()}")
```

If `False`, the parser will still work using regex-based heuristics.

## Architecture Benefits

- **Modular**: Gracefully falls back if Tree-sitter unavailable
- **Scalable**: Easy to add more languages (Java, C++, Rust, etc.)
- **Controlled**: Full management of grammar versions and builds
- **Portable**: Self-contained vendor libraries don't depend on system packages

## Future Work

- [ ] Integrate full AST-based read/write detection via Tree-sitter
- [ ] Add support for additional languages (Java, C++, Rust)
- [ ] Optimize performance by caching parsed ASTs
- [ ] Add precise function-level and scope-aware analysis
- [ ] Support for custom pragma/annotation parsing per language

## Current Limitations

- Regex-based heuristics have precision limits (false positives/negatives)
- OpenMP clause parsing is line-based and may miss complex multi-macro patterns
- No support for C++ yet (requires tree-sitter-cpp)
- No inter-procedural analysis across function boundaries
