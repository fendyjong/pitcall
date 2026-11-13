#!/usr/bin/env python3
"""deprecated(0.16.0): the 0.15.0 name of a plugin script. Goes in 0.17.0; see alias.py."""
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import alias
    alias.old_root_script(Path(__file__).name, sys.argv[1:])
