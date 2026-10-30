# Project Scaffolder

A desktop app for creating projects from templates, then keeping each one's
**versions** in sync with GitHub. Each version is a git branch, so you can
branch, merge, diff and push without leaving the app.

This is version **2.0.0**, a full rewrite of the original three-file prototype.
Every item from the audit has been implemented; see
[IMPLEMENTATION.md](IMPLEMENTATION.md) for the traceability table.

---

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt # PySide6

python gui.py                   # or: python main.py
```

Optional but recommended — real OS credential storage for tokens:

```bash
pip install keyring
```

Requirements: **Python 3.10+**, **git** on `PATH`. The editor integration
supports VS Code, Cursor, VSCodium, Zed, JetBrains and the OS default.

---

## What it does

| Area | Capabilities |
| --- | --- |
| **Projects** | Create from a template, clone from GitHub, adopt an existing folder, relink a moved folder, rename (optionally renaming the GitHub repo), delete to a recoverable trash |
| **Versions** | One git branch per version, with a parent/child tree; switch, create, merge (merge commit / squash / no-ff), rebase, open a pull request, delete |
| **Changes** | Per-file staging, side-by-side diff preview, discard per file, commit with amend, undo last commit, stash list |
| **Remote** | Push / pull with fast-forward, diverged resolution (merge, rebase, reset), fetch, force-push-with-lease, sync badges in words |
| **Templates** | Ignore patterns, `{{placeholder}}` substitution in contents *and* file names, discovery, preview, export/import a manifest |
| **GitHub** | Multiple accounts, token or device-flow sign-in, repository picker, visibility, releases, open issues and pull requests |
| **Tooling** | Environment bootstrap (Python/Node/Rust/Go/.NET/Ruby) with streamed output, run project tasks |

---

## Keyboard shortcuts

| Keys | Action |
| --- | --- |
| `Ctrl+K` | Command palette (also searches projects) |
| `Ctrl+N` | New project |
| `Ctrl+Shift+N` | Clone from GitHub |
| `Ctrl+Shift+C` | Commit |
| `Ctrl+Shift+P` | Push |
| `Ctrl+Shift+L` | Pull |
| `Ctrl+E` / `Ctrl+O` | Open in editor / open folder |
| `Ctrl+,` | Settings |
| `Ctrl+Shift+T` | Toggle light/dark |
| `Ctrl+F` | Focus project search |
| `F5` | Refresh |
| `Ctrl+/` | Shortcut list |

---

## Where your data lives

| Platform | Data | Config |
| --- | --- | --- |
| Windows | `%LOCALAPPDATA%\ProjectScaffolder` | `%APPDATA%\ProjectScaffolder` |
| macOS | `~/Library/Application Support/ProjectScaffolder` | same |
| Linux | `$XDG_DATA_HOME/project-scaffolder` | `$XDG_CONFIG_HOME/project-scaffolder` |

Overrides: `SCAFFOLDER_DATA_DIR` and `SCAFFOLDER_CONFIG_DIR`.

The layout inside the data directory:

```
memory.json            projects, templates, accounts, settings, error log
credentials.json       DPAPI-encrypted (Windows) or 0600 file tokens
trash/                 deleted project folders, restorable from File → Trash
window_state.json      geometry, splitter, last selection, filter
```

**Migration:** on first launch the old `~/.scaffolder/memory.json` is merged in
(by folder path, so it is idempotent), and any plaintext
`~/.scaffolder/credentials.json` is moved into secure storage and deleted.

---

## Command line

Useful for scripting or CI; no GUI required.

```bash
python scaffold.py list
python scaffold.py templates
python scaffold.py create demo --template base-python --dest ~/projects/demo
python scaffold.py changes demo
python scaffold.py commit demo -m "Initial work"
python scaffold.py status demo --json
```

---

## Development

```bash
pip install pytest pyflakes

python -m pytest tests -q          # 74 tests
python -m pyflakes scaffolder_app tests scripts
python scripts/smoke.py            # backend end-to-end, no UI
python scripts/ui_smoke.py         # builds the real window offscreen
python scripts/dialog_smoke.py     # constructs every dialog
python scripts/screenshot.py out light
```

The UI tests and smoke scripts set `QT_QPA_PLATFORM=offscreen`, so they run
without a display.

### Layout

```
scaffolder_app/
├── errors.py            structured error taxonomy
├── paths.py             per-platform data locations
├── locking.py           cross-process lock on memory.json
├── project_memory.py    persistent store (stable ids, migrations)
├── credentials.py       keyring → DPAPI → 0600 file
├── git_ops.py           git plumbing, secret askpass
├── github_api.py        REST client, remote parsing, device flow
├── template_engine.py   safe copy + placeholder substitution
├── env_setup.py         project type detection, installers, tasks
├── editor.py            editor discovery and launching
├── scaffolder.py        application service (the UI talks only to this)
└── ui/
    ├── app.py           entry point
    ├── main_window.py   shell, menus, action routing
    ├── theme.py         design tokens, platform fonts
    ├── styles.py        generated stylesheet
    ├── widgets.py       badges, states, toasts, form builder
    ├── async_runner.py  scoped background tasks
    ├── palette.py       command palette
    ├── panels/          sidebar, project workspace
    └── dialogs/         every dialog, grouped by purpose
```

The backend never imports Qt, which is why it can be tested head-lessly.

---

## Safety model

Several destructive operations in the original design could lose data. The
rewrite enforces these rules, each backed by a regression test:

- **Projects are deleted by stable id, never by name.** Deleting `demo-v2` can
  no longer touch `demo`'s folder.
- **Template placeholders cannot escape the destination.** Values are
  sanitised and the resolved path is re-checked against the destination.
- **Deletes go to a recoverable trash**, restorable from *File → Trash*.
- **Force push uses `--force-with-lease`** and requires typing `FORCE`.
- **Switching versions with a dirty tree stashes** your work and re-applies it.
- **Tokens are never written in plaintext** when a secure backend is available.
- **A corrupt `memory.json` is quarantined, not overwritten**, and you are told
  where it went.
