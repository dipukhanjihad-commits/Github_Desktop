"""Construct every dialog offscreen to catch import/wiring regressions."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from scaffolder_app.ui.dialogs.account_dialogs import (  # noqa: E402
    AddAccountDialog, ConflictedDialog, DeviceFlowDialog, ManageAccountsDialog,
    ManifestImportDialog, StashDialog, TaskRunDialog, TemplateDialog,
)
from scaffolder_app.ui.dialogs.base import (  # noqa: E402
    ConfirmDialog, ErrorDialog, InfoDialog, TokenUpdateDialog,
)
from scaffolder_app.ui.dialogs.project_dialogs import (  # noqa: E402
    CloneDialog, DeleteProjectDialog, DeleteVersionDialog, ForcePushDialog,
    ImportFolderDialog, NewProjectDialog, RelinkDialog, RenameDialog,
)
from scaffolder_app.ui.dialogs.workflow_dialogs import (  # noqa: E402
    CommitDialog, GroupDialog, MergeDialog, NotesDialog, ReleaseDialog,
    SettingsDialog, TrashDialog, VersionDialog,
)
from scaffolder_app.ui.palette import Command, CommandPalette  # noqa: E402
from scaffolder_app.ui.services import Services  # noqa: E402
from scaffolder_app.ui.styles import build_stylesheet  # noqa: E402
from scaffolder_app.ui.theme import Typography, build_theme  # noqa: E402
from scaffolder_app.errors import TokenExpiredError  # noqa: E402


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="scaffolder-dialogs-"))
    app = QApplication.instance() or QApplication(sys.argv)
    theme = build_theme("dark")
    app.setStyleSheet(build_stylesheet(theme))
    app.setFont(Typography().body())
    services = Services.bootstrap(root=root, use_legacy=False)

    template = root / "tpl"
    (template / "src").mkdir(parents=True)
    (template / "README.md").write_text("# {{project_name}}\n", encoding="utf-8")
    (template / "src" / "{{module_name}}.py").write_text("x = 1\n", encoding="utf-8")
    services.scaffolder.create_template_from_folder(
        "basic", template, "demo", ignore=[".git"],
        placeholders={"module_name": ""})
    project = services.scaffolder.create_project(
        "demo", "basic", root / "work" / "demo",
        {"module_name": "demo", "project_name": "demo"},
        account=None, create_repo=False)
    services.memory.add_account("alice", orgs=["acme"])
    (root / "work" / "demo" / "README.md").write_text("# changed\n", encoding="utf-8")
    changes = services.scaffolder.get_changes("demo")
    project = services.memory.get_project("demo")
    templates = services.memory.list_templates()
    settings = services.memory.snapshot()["settings"]

    noop = lambda *a, **k: None  # noqa: E731
    built: list[tuple[str, object]] = []

    def make(name: str, factory) -> None:
        try:
            widget = factory()
            widget.adjustSize()
            built.append((name, widget))
        except Exception as exc:  # noqa: BLE001 - reported below
            import traceback

            traceback.print_exc()
            built.append((name, exc))

    make("InfoDialog", lambda: InfoDialog(None, "Title", "Body", theme, "detail"))
    make("ConfirmDialog", lambda: ConfirmDialog(None, "T", "M", theme, "OK"))
    make("ErrorDialog", lambda: ErrorDialog(None, TokenExpiredError("alice"), theme))
    make("TokenUpdateDialog", lambda: TokenUpdateDialog(None, "alice", theme, noop))
    make("NewProjectDialog", lambda: NewProjectDialog(
        None, theme, templates=templates, accounts=["alice"],
        default_path=str(root / "work"), on_submit=noop,
        check_name=lambda n, a: {"local_free": True, "remote_free": True},
        on_preview=services.scaffolder.preview_template))
    make("CloneDialog", lambda: CloneDialog(
        None, theme, accounts=["alice"], default_path=str(root / "work"),
        on_submit=noop, load_repos=lambda a, d, f: d([])))
    make("ImportFolderDialog", lambda: ImportFolderDialog(
        None, theme, on_submit=noop))
    make("RelinkDialog", lambda: RelinkDialog(None, theme, project, noop))
    make("RenameDialog", lambda: RenameDialog(None, theme, project, noop))
    make("DeleteProjectDialog", lambda: DeleteProjectDialog(None, theme, project, noop))
    make("DeleteVersionDialog", lambda: DeleteVersionDialog(
        None, theme, project, "v1", noop))
    make("ForcePushDialog", lambda: ForcePushDialog(None, theme, "v1", noop))
    make("CommitDialog", lambda: CommitDialog(
        None, theme, "demo", changes, 72, noop))
    make("MergeDialog", lambda: MergeDialog(None, theme, project, noop, noop))
    make("NotesDialog", lambda: NotesDialog(None, theme, project, noop))
    make("GroupDialog", lambda: GroupDialog(None, theme, project, ["g"], noop))
    make("VersionDialog", lambda: VersionDialog(None, theme, project, noop))
    make("ReleaseDialog", lambda: ReleaseDialog(None, theme, project, ["v1"], noop))
    make("SettingsDialog", lambda: SettingsDialog(
        None, theme, settings, {"vscode": True}, "storage note", noop))
    make("TrashDialog", lambda: TrashDialog(None, theme, [], noop, noop, noop))
    make("AddAccountDialog", lambda: AddAccountDialog(None, theme, noop,
                                                      device_flow=noop))
    make("DeviceFlowDialog", lambda: DeviceFlowDialog(
        None, theme, {"user_code": "ABCD-1234", "interval": 30},
        lambda d, f: None))
    make("ManageAccountsDialog", lambda: ManageAccountsDialog(
        None, theme, services.memory.list_accounts(), lambda u: True,
        noop, noop, noop, noop, "dpapi"))
    make("TemplateDialog", lambda: TemplateDialog(None, theme, templates[0], noop))
    make("ManifestImportDialog", lambda: ManifestImportDialog(None, theme, noop))
    make("TaskRunDialog", lambda: TaskRunDialog(
        None, theme, {"pytest": [sys.executable, "-m", "pytest"]}, noop))
    make("StashDialog", lambda: StashDialog(None, theme, "demo", ["stash@{0}"],
                                            noop, noop))
    make("ConflictedDialog", lambda: ConflictedDialog(
        None, theme, ["a.py"], noop, noop))
    make("CommandPalette", lambda: CommandPalette(
        None, theme, [Command("New project", "Create", noop, "Ctrl+N")],
        ["demo"], on_open_project=noop))

    failures = [(name, exc) for name, exc in built if isinstance(exc, Exception)]
    print()
    print(f"{len(built) - len(failures)}/{len(built)} dialogs constructed")
    for name, exc in failures:
        print(f"  FAILED {name}: {exc}")
    for _name, widget in built:
        if not isinstance(widget, Exception):
            widget.deleteLater()
    if failures:
        return 1
    print("DIALOG SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
