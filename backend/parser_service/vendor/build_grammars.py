#!/usr/bin/env python
"""Build script for Tree-sitter grammars.

This script manages cloning and building Tree-sitter language grammars
into compiled shared libraries (.so/.dll files) that can be loaded dynamically
by the parser service.

Grammars are stored in vendor/grammars/ and compiled to vendor/lib/ 
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path


def run_cmd(cmd, cwd=None):
    """Run a shell command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True)
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return 1, '', str(e)


def clone_grammar(repo_url, target_dir):
    """Clone a grammar repo if it doesn't already exist."""
    if os.path.isdir(target_dir):
        print(f"  {target_dir} already exists, skipping clone")
        return True
    
    print(f"  Cloning {repo_url} to {target_dir}...")
    code, out, err = run_cmd(f'git clone {repo_url} "{target_dir}"')
    if code != 0:
        print(f"  ERROR: Failed to clone {repo_url}")
        print(f"  stderr: {err}")
        return False
    print(f"  Cloned successfully")
    return True


def build_languages(vendor_dir):
    """Build compiled language libraries using tree-sitter CLI."""
    import platform
    
    vendor_path = Path(vendor_dir).resolve()
    grammars_dir = vendor_path / "grammars"
    lib_dir = vendor_path / "lib"
    
    lib_dir.mkdir(exist_ok=True)
    
    # Define languages to build: (language_name, repo_dir_name)
    languages = [
        ('c', 'tree-sitter-c'),
        ('python', 'tree-sitter-python'),
    ]
    
    for lang_name, repo_dir_name in languages:
        repo_path = grammars_dir / repo_dir_name
        if not repo_path.is_dir():
            print(f"  WARNING: {repo_path} does not exist, skipping {lang_name}")
            continue
        
        # Determine output filename based on platform
        if platform.system() == 'Windows':
            lib_file = lib_dir / f"{lang_name}.dll"
        else:
            lib_file = lib_dir / f"{lang_name}.so"
        
        if lib_file.exists():
            print(f"  {lib_file} already exists, skipping build")
            continue
        
        print(f"  Building {lang_name} language from {repo_path}...")
        
        # Use tree-sitter CLI to build the language: tree-sitter build-wasm <path>
        # For dynamic library on current platform, use: npm run build (which requires node)
        # Since we may not have node/npm, we'll use Python to compile the grammar
        
        # Alternative: use a simple ctypes/cffi approach or invoke cc directly
        # For now, try using tree-sitter-cli if available, or use a fallback approach
        
        # Try using tree-sitter build-wasm or similar
        code, out, err = run_cmd(f'tree-sitter generate', cwd=str(repo_path))
        if code != 0:
            print(f"  INFO: tree-sitter generate failed, trying alternative approach...")
        
        # For each language, compile src/parser.c and src/scanner.c into a shared lib
        src_dir = repo_path / "src"
        if not (src_dir / "parser.c").exists():
            print(f"  ERROR: No parser.c found in {src_dir}")
            return False
        
        # Compile using cc/gcc/clang into a shared library
        sources = [src_dir / "parser.c"]
        if (src_dir / "scanner.c").exists():
            sources.append(src_dir / "scanner.c")
        
        # Build shared library using the C compiler
        compile_cmd = _build_compile_cmd(lib_file, sources, repo_path)
        print(f"  Running: {compile_cmd}")
        code, out, err = run_cmd(compile_cmd)
        if code != 0:
            print(f"  WARNING: Compile may have failed: {err}")
            if lib_file.exists():
                print(f"  But library file exists, continuing...")
            else:
                print(f"  ERROR: Failed to build {lang_name}")
                return False
        
        if lib_file.exists():
            print(f"  Built {lib_file}")
        else:
            print(f"  ERROR: Library file {lib_file} was not created")
            return False
    
    return True


def _build_compile_cmd(lib_file, sources, repo_path):
    """Generate a C compile command to build a shared library."""
    import platform
    import shutil
    
    sources_str = ' '.join(f'"{s}"' for s in sources)
    repo_path_str = str(repo_path)
    
    # Find C compiler
    cc = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
    if not cc:
        cc = 'cc'  # fallback, assume it's in PATH
    
    if platform.system() == 'Windows':
        # MSVC or MinGW
        cc = shutil.which('cl') or shutil.which('gcc') or cc
        if 'cl' in cc or 'msvc' in cc.lower():
            # MSVC
            return f'{cc} /LD /I"{repo_path_str}/src" {sources_str} /link /OUT:"{lib_file}"'
        else:
            # MinGW/GCC
            return f'{cc} -shared -fPIC -I"{repo_path_str}/src" {sources_str} -o "{lib_file}"'
    else:
        # Unix-like (Linux, macOS)
        return f'{cc} -shared -fPIC -I"{repo_path_str}/src" {sources_str} -o "{lib_file}"'


def main():
    # Determine vendor directory based on script location (absolute path)
    script_path = Path(__file__).resolve()
    vendor_dir = script_path.parent  # This is backend/parser_service/vendor
    grammars_dir = vendor_dir / "grammars"
    
    print(f"Tree-sitter Grammar Build Script")
    print(f"================================")
    print(f"Script location: {script_path}")
    print(f"Vendor directory: {vendor_dir}")
    print(f"Grammars directory: {grammars_dir}")
    grammars_dir.mkdir(parents=True, exist_ok=True)
    
    # Define grammar repos to clone
    grammar_repos = [
        ('https://github.com/tree-sitter/tree-sitter-c.git', grammars_dir / 'tree-sitter-c'),
        ('https://github.com/tree-sitter/tree-sitter-python.git', grammars_dir / 'tree-sitter-python'),
    ]
    
    print("\n1. Cloning grammar repositories...")
    for repo_url, target_dir in grammar_repos:
        if not clone_grammar(repo_url, str(target_dir)):
            print(f"Failed to clone {repo_url}")
            return 1
    
    print("\n2. Building language libraries...")
    if not build_languages(vendor_dir):
        print("Failed to build one or more languages")
        return 1
    
    print("\n✓ Build complete!")
    print(f"Built libraries are in: {vendor_dir / 'lib'}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
