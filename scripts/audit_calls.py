"""Static audit for calls to methods that do not exist on ``self``.

This class of bug (e.g. ``Scaffolder._create_and_push``) is invisible to
pyflakes and only blows up at runtime, so it is checked structurally: every
``self.foo()`` call must name a method/attribute defined anywhere in the class
MRO, counting methods inherited from base classes.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "scaffolder_app"


def class_index(tree: ast.Module) -> dict[str, ast.ClassDef]:
    return {node.name: node for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)}


def base_names(cls: ast.ClassDef) -> list[str]:
    names = []
    for base in cls.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
    return names


def members(cls: ast.ClassDef) -> tuple[set[str], set[str]]:
    """Return (callables, attributes) declared directly on the class."""
    callables: set[str] = set()
    attrs: set[str] = set()
    for node in ast.walk(cls):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            callables.add(node.name)
        elif isinstance(node, ast.ClassDef):
            callables.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    attrs.add(target.id)
                elif (isinstance(target, ast.Attribute)
                      and isinstance(target.value, ast.Name)
                      and target.value.id == "self"):
                    attrs.add(target.attr)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            attrs.add(node.target.id)
        elif (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
              and isinstance(node.value, ast.Name) and node.value.id == "self"):
            attrs.add(node.attr)
    # `def __init__` assigning self.x counts via the Attribute walk above.
    return callables, attrs


def known(cls_name: str, index: dict[str, ast.ClassDef],
          seen: set[str] | None = None) -> tuple[set[str], set[str]]:
    """Collect members including those inherited from resolved bases."""
    seen = seen or set()
    if cls_name in seen or cls_name not in index:
        return set(), set()
    seen.add(cls_name)
    cls = index[cls_name]
    callables, attrs = members(cls)
    for base in base_names(cls):
        if base in index:
            sub_call, sub_attr = known(base, index, seen)
            callables |= sub_call
            attrs |= sub_attr
    return callables, attrs


QT_BASES = {
    "QWidget", "QFrame", "QLabel", "QDialog", "QMainWindow", "QPushButton",
    "QLineEdit", "QComboBox", "QListWidget", "QTreeWidget", "QTableWidget",
    "QAbstractItemView", "QTextEdit", "QPlainTextEdit", "QListView",
    "QTreeView", "QTableView", "QTabWidget", "QSplitter", "QStackedWidget",
    "QScrollArea", "QSpinBox", "QRunnable", "QObject", "QDialogButtonBox",
}


def is_opaque_base(base: str) -> bool:
    """True when we cannot inspect the base's members (Qt or foreign API)."""
    if base in QT_BASES:
        return True
    # Anything else imported from outside the package is also unknowable.
    return base not in {"AppDialog", "object"}


def resolve(cls_name: str, index: dict[str, ast.ClassDef],
            seen: set[str] | None = None) -> tuple[set[str], set[str], bool]:
    """Return (callables, attributes, fully_known).

    ``fully_known`` is False when any ancestor is defined outside this package
    (e.g. ``QDialog``), because inherited members then cannot be enumerated.
    """
    seen = seen or set()
    if cls_name in seen:
        return set(), set(), True
    seen.add(cls_name)
    cls = index[cls_name]
    callables, attrs = members(cls)
    complete = True
    for base in base_names(cls):
        if base in index:
            sub_call, sub_attr, sub_complete = resolve(base, index, seen)
            callables |= sub_call
            attrs |= sub_attr
            complete = complete and sub_complete
        elif base != "object":
            complete = False
    return callables, attrs, complete


def audit(path: Path, index: dict[str, ast.ClassDef]) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        callables, attrs, complete = resolve(cls.name, index)
        if not complete:
            # Inherits from a foreign base (e.g. AppDialog -> QDialog): skip,
            # because accept()/reject()/setMinimumWidth() come from Qt.
            continue
        for node in ast.walk(cls):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)):
                continue
            target = node.func.value
            if not (isinstance(target, ast.Name) and target.id == "self"):
                continue
            name = node.func.attr
            if name not in callables and name not in attrs:
                try:
                    where = path.relative_to(ROOT)
                except ValueError:
                    where = path
                problems.append(f"{where}:{node.lineno} {cls.name}.{name}()")
    return problems


def main() -> int:
    index: dict[str, ast.ClassDef] = {}
    for path in PACKAGE.rglob("*.py"):
        index.update(class_index(ast.parse(path.read_text(encoding="utf-8"))))

    problems: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        problems.extend(audit(path, index))

    if problems:
        print(f"undefined self-methods: {len(problems)}")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("self-method audit: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
