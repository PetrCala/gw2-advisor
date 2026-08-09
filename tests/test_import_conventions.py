"""Every invest/* module must import with stdlib and first-party code only.

The convention invest/index.py's docstring states and invest/nav.py,
invest/luxury.py, invest/craft.py all follow: requests, boto3, pyarrow and
friends are imported inside the function that needs them, never at module
top level. M6.d shipped a module that broke it and took CI down at
test-collection time, before a single test ran, which is exactly the failure
mode a per-module test cannot catch: the collector never got that far.

Checked by reading the source rather than by importing, so the test itself
cannot be the thing that pulls a heavy package in and then reports success.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIRST_PARTY = {
    "account", "collector", "features", "invest", "report", "scorers", "season",
}


def _top_level_imports(path):
    """Module names imported at module scope, ignoring anything nested inside
    a function, a class or a try block."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.append(node.module)
    return out


def _offenders(package):
    bad = {}
    for path in sorted((ROOT / package).glob("*.py")):
        for name in _top_level_imports(path):
            root = name.split(".")[0]
            if root in FIRST_PARTY or root in sys.stdlib_module_names:
                continue
            bad.setdefault(f"{package}/{path.name}", []).append(name)
    return bad


def test_invest_modules_import_with_stdlib_at_module_scope():
    assert _offenders("invest") == {}


def test_the_report_and_season_layers_hold_the_same_line():
    # Same reason: report/build.py and report/track.py are imported by the
    # test suite and by each other, so a top-level boto3 there fails CI the
    # same way.
    assert _offenders("report") == {}
    assert _offenders("season") == {}


def test_the_convention_check_would_actually_catch_a_violation(tmp_path):
    # A guard that cannot fail is not a guard. This is the shape of the
    # M6.d regression: a heavy import promoted to module scope.
    module = tmp_path / "offender.py"
    module.write_text("import json\nimport requests\nfrom invest import tags\n",
                      encoding="utf-8")
    names = _top_level_imports(module)
    assert names == ["json", "requests", "invest"]
    assert [n for n in names
            if n.split(".")[0] not in FIRST_PARTY
            and n.split(".")[0] not in sys.stdlib_module_names] == ["requests"]
