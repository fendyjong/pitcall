"""The installed garage imports nothing from the plugin (decision 11).

`garage start` copies `garage/` to `$XDG_DATA_HOME/pitcall/garage/` and the service runs
from there, because a plugin update can remove the versioned directory it came from. A
module under `garage/` that imported from `scripts/` would work in every test run from the
repository and crash-loop on the first machine whose plugin was updated.
"""

import ast
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GARAGE = ROOT / "garage"


def imported(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            yield "." if node.level else node.module.split(".")[0]


def test_the_garage_has_its_modules():
    """A scan over no files passes exactly like a clean one."""
    assert len(list(GARAGE.glob("garage_*.py"))) >= 10


def test_garage_imports_only_the_standard_library_and_itself():
    own = {path.stem for path in GARAGE.glob("*.py")}
    foreign = [f"{path.name}: {name}"
               for path in sorted(GARAGE.glob("*.py"))
               for name in imported(path)
               if name not in own and name not in sys.stdlib_module_names]
    assert not foreign, "garage/ imports from outside itself:\n" + "\n".join(foreign)


def test_a_copy_of_the_garage_imports_with_the_plugin_out_of_reach(tmp_path):
    copy = tmp_path / "garage"
    shutil.copytree(GARAGE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    probe = ("import sys; sys.path.insert(0, sys.argv[1]); import garage_service; "
             "assert 'tracker' not in sys.modules and 'lane_config' not in sys.modules")
    result = subprocess.run([sys.executable, "-I", "-c", probe, str(copy)],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
