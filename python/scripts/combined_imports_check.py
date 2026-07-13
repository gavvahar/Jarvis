#!/usr/bin/env python3
"""Pre-commit check that flags adjacent single-module `import x` statements
that should be combined onto one line (`import x, y`), matching this
codebase's style. Imports with an `as` alias are left alone, since combining
those with plain imports reads worse and this repo doesn't do it (see
wake_daemon.py's `import numpy as np` / `import httpx` block, which stays
split). Only top-level statements are checked, so guarded imports
(try/except, TYPE_CHECKING) are untouched.
"""

import ast, os, sys

EXCLUDE_DIRS = {
    ".git",
    "venv",
    ".venv",
    "node_modules",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    "env",
}


def iter_py_files():
    """Yield paths to all Python files, skipping excluded directories."""
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(root, f)


def combinable_runs(tree):
    """Yield runs of >1 consecutive, alias-free `import x` statements in a module body."""
    run = []
    for node in tree.body:
        plain = isinstance(node, ast.Import) and len(node.names) == 1 and node.names[0].asname is None
        if plain and (not run or node.lineno == run[-1].lineno + 1):
            run.append(node)
        else:
            if len(run) > 1:
                yield run
            run = [node] if plain else []
    if len(run) > 1:
        yield run


def main():
    """Report combinable imports found in the codebase and exit non-zero if any exist."""
    violations = []
    for path in iter_py_files():
        try:
            with open(path, "rb") as fh:
                tree = ast.parse(fh.read(), filename=path)
        except SyntaxError:
            continue
        for run in combinable_runs(tree):
            names = ", ".join(n.names[0].name for n in run)
            violations.append(f"{path}:{run[0].lineno}: combine into one line: import {names}")
    if violations:
        print("❌ Error: adjacent imports found that should be combined onto one line!")
        print("\n".join(violations))
        sys.exit(1)
    print("✅ No combinable imports found. All good!")


if __name__ == "__main__":
    main()
