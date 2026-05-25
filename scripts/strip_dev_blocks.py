#!/usr/bin/env python3
"""Strip development-only code blocks from Python source files before packaging.

Removes every block delimited by:
    # START — workflow remove
    ...
    # END — workflow remove
(inclusive of the marker lines themselves).
"""

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Matches a START marker, any content, and the corresponding END marker.
# re.DOTALL so '.' matches newlines. Blocks are expected to be sequential (not nested).
_BLOCK_RE = re.compile(
    r"[ \t]*# START \u2014 workflow remove\r?\n.*?[ \t]*# END \u2014 workflow remove[ \t]*\r?\n?",
    re.DOTALL,
)


def strip_file(path: str) -> bool:
    """Strip dev blocks from a single file. Returns True if the file was modified."""
    with open(path, "r", encoding="utf-8") as f:
        original = f.read()

    stripped, count = _BLOCK_RE.subn("", original)
    if count == 0:
        return False

    with open(path, "w", encoding="utf-8") as f:
        f.write(stripped)

    print(f"Stripped {count} block(s) from {os.path.relpath(path, REPO_ROOT)}")
    return True


def main() -> None:
    modified = 0
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        # Skip hidden dirs, __pycache__, scripts/, and dist/
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d not in ("__pycache__", "scripts", "dist")
        ]
        for filename in filenames:
            if filename.endswith(".py"):
                if strip_file(os.path.join(dirpath, filename)):
                    modified += 1

    print(f"Done. Modified {modified} file(s).")


if __name__ == "__main__":
    main()
