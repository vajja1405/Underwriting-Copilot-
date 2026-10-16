"""Who may import the SYNTHETIC generator or the ground truth (Phase 2 rule, extended by the Phase 4 simulator).

Only these may import uwcopilot.docintel.synthetic or uwcopilot.docintel.truth (see the synthetic package docstring):
- the generator package itself and truth.py;
- scripts/make_packets.py and scripts/eval_*.py;
- the evidence-request simulator (innovation S5), which is evaluation code: scripts/simulate_requests.py and
  src/uwcopilot/evidence/simulator.py, simulator_cases.py and simulator_diagnostics.py (ALLOWED_IMPORTERS below);
- tests.
Imports are read from the syntax tree, so a mention in a docstring does not count; module names passed to
importlib.import_module / __import__ as string literals count too.
"""

import ast
from pathlib import Path

from uwcopilot.common.paths import REPO_ROOT

DOCINTEL = REPO_ROOT / "src" / "uwcopilot" / "docintel"
FORBIDDEN = ("uwcopilot.docintel.synthetic", "uwcopilot.docintel.truth")
DYNAMIC_IMPORTERS = ("import_module", "__import__")
# Repo-relative files outside tests/ allowed to import the generator or the truth (an explicit list, not a pattern,
# except scripts/eval_*.py, which the Phase 2 rule allowed by name pattern from the start).
ALLOWED_IMPORTERS = (
    "scripts/make_packets.py",
    "scripts/simulate_requests.py",
    "src/uwcopilot/evidence/simulator.py",
    "src/uwcopilot/evidence/simulator_cases.py",
    "src/uwcopilot/evidence/simulator_diagnostics.py",
)
ALLOWED_SCRIPT_PREFIX = "scripts/eval_"


def imported_modules(path: Path) -> set[str]:
    """Every module a file imports (absolute names; "from uwcopilot.docintel import truth" counts as the module)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            literal = node.args[0] if node.args else None
            if name in DYNAMIC_IMPORTERS and isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                modules.add(literal.value)
    return modules


def imports_generator_or_truth(path: Path) -> bool:
    """True when the file imports the generator package or truth.py."""
    return any(module.startswith(FORBIDDEN) for module in imported_modules(path))


def pipeline_files() -> list[Path]:
    """docintel modules outside synthetic/ and truth.py."""
    files = [path for path in DOCINTEL.rglob("*.py") if "synthetic" not in path.relative_to(DOCINTEL).parts]
    return [path for path in files if path.name != "truth.py"]


def is_allowed(relative: str) -> bool:
    """True for a repo-relative path that may import the generator or the truth."""
    if relative in ALLOWED_IMPORTERS or relative.startswith(ALLOWED_SCRIPT_PREFIX):
        return True
    return relative.startswith("src/uwcopilot/docintel/synthetic/") or relative == "src/uwcopilot/docintel/truth.py"


def test_pipeline_modules_do_not_import_generator_or_truth() -> None:
    files = pipeline_files()
    assert any(path.name == "levers.py" for path in files)
    offenders = [str(path.relative_to(REPO_ROOT)) for path in files if imports_generator_or_truth(path)]
    assert offenders == []


def test_only_the_allow_listed_files_import_generator_or_truth() -> None:
    files = sorted([*(REPO_ROOT / "src").rglob("*.py"), *(REPO_ROOT / "scripts").rglob("*.py")])
    importers = {str(path.relative_to(REPO_ROOT)) for path in files if imports_generator_or_truth(path)}
    assert sorted(name for name in importers if not is_allowed(name)) == []
    # The allow-list holds no stale entry: every listed file exists and still imports the generator or the truth.
    assert set(ALLOWED_IMPORTERS) <= importers


def test_the_engine_modules_do_not_import_the_simulator_or_the_truth() -> None:
    # The evidence engine (everything in evidence/ except the simulator's evaluation modules) never sees the truth.
    evidence = REPO_ROOT / "src" / "uwcopilot" / "evidence"
    simulator_modules = {Path(name).name for name in ALLOWED_IMPORTERS if name.startswith("src/uwcopilot/evidence/")}
    offenders = []
    for path in sorted(evidence.glob("*.py")):
        if path.name in simulator_modules:
            continue
        if imports_generator_or_truth(path):
            offenders.append(path.name)
    assert offenders == []


def test_the_scanner_sees_a_forbidden_import(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text('"""uwcopilot.docintel.truth in a docstring is fine."""\nfrom uwcopilot.docintel import truth\n')
    assert "uwcopilot.docintel.truth" in imported_modules(sample)
    dynamic = tmp_path / "dynamic.py"
    dynamic.write_text('import importlib\nimportlib.import_module("uwcopilot.docintel.synthetic")\n')
    assert imports_generator_or_truth(dynamic)
