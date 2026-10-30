"""Verify IMPLEMENTATION.md claims against the actual source tree.

Static checks only: confirms the named symbol exists where the doc says it
does. Behavioural proof lives in tests/ and scripts/.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (item, file, symbol/attribute that must exist)
CLAIMS: list[tuple[str, str, str]] = [
    # --- MUST: features
    ("M2", "scaffolder_app/scaffolder.py", "delete_project"),
    ("M2", "scaffolder_app/project_memory.py", "find_project_path"),
    ("M3", "scaffolder_app/template_engine.py", "sanitize_placeholder_value"),
    ("M3", "scaffolder_app/template_engine.py", "safe_join"),
    ("M4", "scaffolder_app/ui/dialogs/base.py", "confirm_destructive"),
    ("M5", "scaffolder_app/github_api.py", "parse_remote"),
    ("M5", "scaffolder_app/scaffolder.py", "_warn_if_unparseable"),
    ("M6", "scaffolder_app/errors.py", "ScaffolderError"),
    ("M6", "scaffolder_app/ui/dialogs/base.py", "ErrorDialog"),
    ("M7", "scaffolder_app/scaffolder.py", "_rollback_project"),
    ("M8", "scaffolder_app/scaffolder.py", "validate_paths"),
    ("M8", "scaffolder_app/ui/dialogs/project_dialogs.py", "RelinkDialog"),
    ("M9", "scaffolder_app/scaffolder.py", "_ensure_clean"),
    ("M10", "scaffolder_app/template_engine.py", "iter_files"),
    ("M11", "scaffolder_app/credentials.py", "_DpapiBackend"),
    ("M11", "scaffolder_app/credentials.py", "migrate_legacy"),
    ("M12", "scaffolder_app/scaffolder.py", "check_token"),
    ("M12", "scaffolder_app/ui/dialogs/base.py", "TokenUpdateDialog"),
    # --- MUST: design
    ("M14", "scaffolder_app/ui/widgets.py", "FormBuilder"),
    ("M14", "scaffolder_app/ui/dialogs/base.py", "AppDialog"),
    ("M15", "scaffolder_app/ui/theme.py", "Typography"),
    ("M16", "scaffolder_app/ui/theme.py", "build_theme"),
    ("M16", "scaffolder_app/ui/styles.py", "build_stylesheet"),
    ("M17", "scaffolder_app/ui/platform.py", "WindowState"),
    ("M19", "scaffolder_app/ui/widgets.py", "EmptyState"),
    ("M19", "scaffolder_app/ui/widgets.py", "LoadingState"),
    ("M20", "scaffolder_app/ui/widgets.py", "KeycapLabel"),
    # --- SHOULD
    ("S1", "scaffolder_app/git_ops.py", "diff_text"),
    ("S2", "scaffolder_app/git_ops.py", "stage"),
    ("S2", "scaffolder_app/git_ops.py", "unstage"),
    ("S3", "scaffolder_app/git_ops.py", "log"),
    ("S3", "scaffolder_app/scaffolder.py", "undo_last_commit"),
    ("S4", "scaffolder_app/ui/dialogs/account_dialogs.py", "ConflictedDialog"),
    ("S5", "scaffolder_app/ui/dialogs/workflow_dialogs.py", "SettingsDialog"),
    ("S6", "scaffolder_app/editor.py", "detect_available_editors"),
    ("S7", "scaffolder_app/ui/dialogs/workflow_dialogs.py", "_next_label"),
    ("S9", "scaffolder_app/github_api.py", "DeviceFlow"),
    ("S9", "scaffolder_app/ui/dialogs/account_dialogs.py", "DeviceFlowDialog"),
    ("S11", "scaffolder_app/scaffolder.py", "push_all"),
    ("S12", "scaffolder_app/env_setup.py", "_stream"),
    ("S13", "scaffolder_app/locking.py", "file_lock"),
    ("S13", "scaffolder_app/ui/platform.py", "SingleInstanceGuard"),
    ("S14", "scaffolder_app/ui/dialogs/workflow_dialogs.py", "TrashDialog"),
    ("S14", "scaffolder_app/scaffolder.py", "empty_trash"),
    ("S16", "scaffolder_app/ui/widgets.py", "Badge"),
    ("S17", "scaffolder_app/ui/panels/project_view.py", "update_state"),
    ("S18", "scaffolder_app/ui/async_runner.py", "TaskRunner"),
    ("S20", "scaffolder_app/ui/main_window.py", "MainWindow"),
    ("S21", "scaffolder_app/ui/platform.py", "set_scale_policy"),
    ("S22", "scaffolder_app/ui/panels/sidebar.py", "Sidebar"),
    ("S23", "scaffolder_app/ui/dialogs/project_dialogs.py", "NewProjectDialog"),
    ("S23", "scaffolder_app/scaffolder.py", "check_name_available"),
    ("S24", "scaffolder_app/paths.py", "data_dir"),
    ("S25", "scaffolder_app/template_engine.py", "_render_text"),
    ("S26", "scaffolder_app/scaffolder.py", "clean_commit_message"),
    # --- NICE TO HAVE
    ("N1", "scaffolder_app/scaffolder.py", "stash_changes"),
    ("N1", "scaffolder_app/ui/dialogs/account_dialogs.py", "StashDialog"),
    ("N2", "scaffolder_app/env_setup.py", "RunTask"),
    ("N2", "scaffolder_app/ui/dialogs/account_dialogs.py", "TaskRunDialog"),
    ("N3", "scaffolder_app/scaffolder.py", "fetch_github_summary"),
    ("N4", "scaffolder_app/scaffolder.py", "create_release"),
    ("N4", "scaffolder_app/ui/dialogs/workflow_dialogs.py", "ReleaseDialog"),
    ("N6", "scaffolder_app/project_memory.py", "set_pinned"),
    ("N6", "scaffolder_app/project_memory.py", "set_group"),
    ("N7", "scaffolder_app/ui/palette.py", "CommandPalette"),
    ("N9", "scaffolder_app/scaffolder.py", "refresh_all_versions"),
    ("N10", "scaffolder_app/scaffolder.py", "export_template_manifest"),
    ("N10", "scaffolder_app/scaffolder.py", "import_template_manifest"),
    ("N11", "scaffolder_app/editor.py", "EditorLauncher"),
    ("N13", "scaffolder_app/project_memory.py", "_write_atomic"),
    ("N15", "scaffolder_app/ui/panels/project_view.py", "_tree_order"),
]


def defined_symbols(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def check() -> int:
    missing: list[tuple[str, str, str]] = []
    cache: dict[Path, set[str]] = {}
    for item, rel, symbol in CLAIMS:
        path = ROOT / rel
        if not path.exists():
            missing.append((item, rel, f"{symbol} (FILE MISSING)"))
            continue
        if path not in cache:
            cache[path] = defined_symbols(path)
        if symbol not in cache[path]:
            missing.append((item, rel, symbol))
    print(f"claimed symbols: {len(CLAIMS)}")
    if missing:
        print(f"MISSING: {len(missing)}")
        for item, rel, symbol in missing:
            print(f"  {item}: {rel}::{symbol}")
        return 1
    print("all claimed symbols exist")
    return 0


if __name__ == "__main__":
    sys.exit(check())
