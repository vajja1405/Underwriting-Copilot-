"""The extraction pipeline never imports the SYNTHETIC generator or the ground truth (Phase 2 rule).

Every module under src/uwcopilot/docintel/ except synthetic/, truth.py and evaluation.py (which compares the
pipeline's output with the truth) is checked in two ways:
1. statically: every import statement in the syntax tree, including relative imports ("from . import truth",
   "from .synthetic import ...") and module names passed to importlib.import_module / __import__ as string
   literals;
2. at run time: a fresh Python process imports all of those modules and then must not have loaded
   uwcopilot.docintel.truth or any uwcopilot.docintel.synthetic module.
A mention in a docstring or comment does not count.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

from uwcopilot.common.paths import REPO_ROOT

DOCINTEL = REPO_ROOT / "src" / "uwcopilot" / "docintel"
PACKAGE = "uwcopilot.docintel"
FORBIDDEN = ("uwcopilot.docintel.synthetic", "uwcopilot.docintel.truth")
ALLOWED_TO_IMPORT_TRUTH = ("truth.py", "evaluation.py")
DYNAMIC_IMPORTERS = ("import_module", "__import__")


def checked_files() -> list[Path]:
    """docintel modules outside synthetic/, truth.py and evaluation.py."""
    files = [path for path in DOCINTEL.rglob("*.py") if "synthetic" not in path.relative_to(DOCINTEL).parts]
    return sorted(path for path in files if path.name not in ALLOWED_TO_IMPORT_TRUTH)


def module_name(path: Path) -> str:
    """The dotted module name of a docintel file."""
    relative = path.relative_to(DOCINTEL).with_suffix("")
    parts = [part for part in relative.parts if part != "__init__"]
    return ".".join([PACKAGE, *parts])


def resolve_relative(path: Path, node: ast.ImportFrom) -> str:
    """The absolute module named by a (possibly relative) "from ... import" statement."""
    if node.level == 0:
        return node.module or ""
    package = module_name(path).split(".")
    if path.name != "__init__.py":
        package = package[:-1]
    base = package[: len(package) - (node.level - 1)]
    return ".".join([*base, node.module] if node.module else base)


def imported_modules(path: Path) -> set[str]:
    """Every module a file imports, statically or through importlib with a literal name."""
    return imported_modules_in(path.read_text(encoding="utf-8"), path)


def imported_modules_in(source: str, path: Path) -> set[str]:
    """Every module that source code imports; path places it in the package (for relative imports)."""
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = resolve_relative(path, node)
            modules.add(base)
            modules.update(f"{base}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            literal = node.args[0] if node.args else None
            if name in DYNAMIC_IMPORTERS and isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                modules.add(literal.value)
    return modules


def test_pipeline_modules_do_not_import_generator_or_truth() -> None:
    files = checked_files()
    names = {path.name for path in files}
    assert {"pipeline.py", "extract_rules.py", "to_application.py", "ocr.py", "classify.py"} <= names
    offenders = []
    for path in files:
        for module in imported_modules(path):
            if module.startswith(FORBIDDEN):
                offenders.append(f"{path.relative_to(REPO_ROOT)} imports {module}")
    assert offenders == []


def test_importing_every_pipeline_module_loads_neither_generator_nor_truth() -> None:
    modules = [module_name(path) for path in checked_files()]
    script = (
        "import importlib, json, sys\n"
        f"for name in {modules!r}:\n"
        "    importlib.import_module(name)\n"
        f"print(json.dumps(sorted(m for m in sys.modules if m.startswith({FORBIDDEN!r}))))\n"
    )
    completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120, check=False)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == []


def test_the_scanner_sees_relative_and_dynamic_imports() -> None:
    source = (
        '"""uwcopilot.docintel.truth in a docstring is fine."""\n'
        "import importlib\n"
        "from . import truth\n"
        "from .synthetic import build_packet\n"
        "importlib.import_module('uwcopilot.docintel.synthetic.common')\n"
    )
    modules = imported_modules_in(source, DOCINTEL / "example.py")
    assert "uwcopilot.docintel.truth" in modules
    assert "uwcopilot.docintel.synthetic.build_packet" in modules
    assert "uwcopilot.docintel.synthetic.common" in modules
    docstring_only = imported_modules_in('"""uwcopilot.docintel.truth"""\n', DOCINTEL / "example.py")
    assert not any(module.startswith(FORBIDDEN) for module in docstring_only)
