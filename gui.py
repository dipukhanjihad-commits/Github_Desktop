"""
gui.py — Beautified Tkinter GUI. Launch with `python gui.py`.
All actions are click-only. All long operations run in background threads.
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from project_memory import ProjectMemory, RepoState, SyncState
from scaffolder import (
    Scaffolder, ScaffolderError, TokenExpiredError, DivergedError,
)


# ============================================================================
# Theme
# ============================================================================

COLORS = {
    "bg":            "#f8fafc",
    "card":          "#ffffff",
    "border":        "#e2e8f0",
    "text":          "#0f172a",
    "muted":         "#64748b",
    "primary":       "#2563eb",
    "primary_hover": "#1d4ed8",
    "success":       "#16a34a",
    "warning":       "#ea580c",
    "warning_bg":    "#fef3c7",
    "error":         "#dc2626",
    "error_bg":      "#fee2e2",
    "disabled":      "#94a3b8",
}

FONT_BODY = ("Helvetica", 10)
FONT_BOLD = ("Helvetica", 10, "bold")
FONT_TITLE = ("Helvetica", 18, "bold")
FONT_SUB = ("Helvetica", 11, "bold")
FONT_MONO = ("Consolas", 9)


def _apply_theme(root):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(bg=COLORS["bg"])

    style.configure(".", background=COLORS["bg"], foreground=COLORS["text"],
                    font=FONT_BODY)
    style.configure("TFrame", background=COLORS["bg"])
    style.configure("Card.TFrame", background=COLORS["card"],
                    relief="flat", borderwidth=1)
    style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
    style.configure("Card.TLabel", background=COLORS["card"])
    style.configure("Muted.TLabel", foreground=COLORS["muted"],
                    background=COLORS["bg"])
    style.configure("CardMuted.TLabel", foreground=COLORS["muted"],
                    background=COLORS["card"])
    style.configure("Header.TLabel", font=FONT_TITLE, background=COLORS["bg"])
    style.configure("Subheader.TLabel", font=FONT_SUB, background=COLORS["bg"])
    style.configure("Section.TLabel", font=FONT_BOLD,
                    foreground=COLORS["muted"], background=COLORS["bg"])

    style.configure("TButton", padding=(10, 6))
    style.configure("Primary.TButton",
                    background=COLORS["primary"], foreground="white",
                    padding=(14, 7), font=FONT_BOLD, borderwidth=0)
    style.map("Primary.TButton",
              background=[("active", COLORS["primary_hover"]),
                          ("disabled", COLORS["disabled"])],
              foreground=[("disabled", "#e2e8f0")])
    style.configure("Danger.TButton",
                    background=COLORS["error"], foreground="white",
                    padding=(12, 6), font=FONT_BOLD, borderwidth=0)
    style.map("Danger.TButton",
              background=[("active", "#b91c1c")])

    style.configure("TEntry", padding=6)
    style.configure("TCombobox", padding=4)

    style.configure("Treeview",
                    background=COLORS["card"], fieldbackground=COLORS["card"],
                    rowheight=28, borderwidth=1, relief="flat")
    style.configure("Treeview.Heading", font=("Helvetica", 9, "bold"),
                    background=COLORS["bg"], foreground=COLORS["muted"],
                    borderwidth=0)
    style.map("Treeview",
              background=[("selected", "#dbeafe")],
              foreground=[("selected", COLORS["text"])])

    style.configure("TNotebook", background=COLORS["bg"])
    style.configure("TNotebook.Tab", padding=(14, 7), font=FONT_BOLD)
    style.map("TNotebook.Tab",
              background=[("selected", COLORS["card"])])

    style.configure("Horizontal.TProgressbar",
                    troughcolor=COLORS["border"],
                    background=COLORS["primary"], borderwidth=0)

    style.configure("TSeparator", background=COLORS["border"])


# ============================================================================
# Base dialog
# ============================================================================

class BaseDialog(tk.Toplevel):
    def __init__(self, app, title, size: Optional[str] = None):
        super().__init__(app)
        self.app = app
        self.title(title)
        self.transient(app)
        self.grab_set()
        if size:
            self.geometry(size)
        self.resizable(False, False)
        self.configure(bg=COLORS["bg"])
        self.body = ttk.Frame(self, padding=18)
        self.body.pack(fill=tk.BOTH, expand=True)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def buttons(self, confirm_text="OK", on_confirm=None):
        btns = ttk.Frame(self.body)
        btns.grid(row=99, column=0, columnspan=10, sticky="e", pady=(18, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        if on_confirm:
            ttk.Button(btns, text=confirm_text, style="Primary.TButton",
                       command=on_confirm).pack(side=tk.RIGHT, padx=(0, 8))
        return btns


# ============================================================================
# Main application
# ============================================================================

class ScaffolderApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Project Scaffolder")
        self.geometry("1350x840")
        self.minsize(1080, 680)

        _apply_theme(self)

        self.memory = ProjectMemory()
        self.scaffolder = Scaffolder(self.memory)

        self._current_project: Optional[str] = None
        self._filtered_projects = []
        self._filtered_templates = []
        self._busy = False
        self._last_auto_refresh = 0.0

        self._build_menu()
        self._build_layout()
        self._refresh_all()

        import shutil
        self._vscode_available = self._detect_vscode()

        self.bind("<FocusIn>", self._on_focus_in)

    # ------------------------------------------------------------- menu

    def _build_menu(self):
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="New Template…",  command=self._dlg_new_template)
        file_menu.add_command(label="New Project…",   command=self._dlg_new_project)
        file_menu.add_command(label="Clone from GitHub…", command=self._dlg_clone_repo)
        file_menu.add_command(label="Import Existing Folder…",
                                command=self._dlg_import_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Settings…",  command=self._dlg_settings)
        file_menu.add_command(label="Error Log…", command=self._open_error_log)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        gh_menu = tk.Menu(menubar, tearoff=0)
        gh_menu.add_command(label="Add GitHub Account…", command=self._dlg_add_account)
        gh_menu.add_command(label="Manage Accounts…",    command=self._dlg_manage_accounts)
        menubar.add_cascade(label="GitHub", menu=gh_menu)

    # ------------------------------------------------------------- layout

    def _build_layout(self):
        # Status bar (bottom)
        status = ttk.Frame(self, padding=(12, 6))
        status.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value="Ready")
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=120)
        ttk.Label(status, textvariable=self.status_var).pack(side=tk.LEFT)
        self.progress_label = ttk.Label(status, text="", foreground=COLORS["muted"])

        # Main paned
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        sidebar = ttk.Frame(paned, width=360)
        paned.add(sidebar, weight=0)
        self._build_sidebar(sidebar)

        right = ttk.Frame(paned)
        paned.add(right, weight=1)
        self._build_detail(right)

    def _build_sidebar(self, parent):
        # Section title + New / Clone buttons
        title_row = ttk.Frame(parent, padding=(14, 14, 14, 6))
        title_row.pack(fill=tk.X)
        ttk.Label(title_row, text="Projects", style="Subheader.TLabel"
                  ).pack(side=tk.LEFT)
        ttk.Button(title_row, text="＋ New",
                   command=self._dlg_new_project).pack(side=tk.RIGHT)

        clone_row = ttk.Frame(parent, padding=(14, 0, 14, 10))
        clone_row.pack(fill=tk.X)
        ttk.Button(clone_row, text="⬇ Clone from GitHub…",
                   style="Primary.TButton",
                   command=self._dlg_clone_repo).pack(fill=tk.X)

        # Search
        search_row = ttk.Frame(parent, padding=(14, 0, 14, 6))
        search_row.pack(fill=tk.X)
        self.search_projects_var = tk.StringVar()
        self.search_projects_var.trace_add(
            "write", lambda *_a: self._refresh_projects())
        ttk.Entry(search_row, textvariable=self.search_projects_var
                  ).pack(fill=tk.X)

        # Project list
        proj_frame = ttk.Frame(parent, padding=(14, 0, 14, 10))
        proj_frame.pack(fill=tk.BOTH, expand=True)
        cols = ("state", "name", "vis", "sync")
        self.projects_tree = ttk.Treeview(proj_frame, columns=cols,
                                            show="headings", height=14)
        self.projects_tree.heading("state", text="")
        self.projects_tree.heading("name", text="Project")
        self.projects_tree.heading("vis", text="")
        self.projects_tree.heading("sync", text="Sync")
        self.projects_tree.column("state", width=28, anchor="center")
        self.projects_tree.column("name", width=200, anchor="w")
        self.projects_tree.column("vis", width=36, anchor="center")
        self.projects_tree.column("sync", width=70, anchor="center")

        scroll = ttk.Scrollbar(proj_frame, orient=tk.VERTICAL,
                                command=self.projects_tree.yview)
        self.projects_tree.configure(yscrollcommand=scroll.set)
        self.projects_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.projects_tree.tag_configure("pending", foreground=COLORS["warning"])
        self.projects_tree.tag_configure("local", foreground=COLORS["muted"])
        self.projects_tree.bind("<<TreeviewSelect>>", self._on_project_select)
        self.projects_tree.bind("<Double-Button-1>", self._on_project_double_click)

        # Templates section
        ttk.Separator(parent, orient="horizontal").pack(fill=tk.X, padx=14, pady=(6, 8))

        tpl_row = ttk.Frame(parent, padding=(14, 0, 14, 6))
        tpl_row.pack(fill=tk.X)
        ttk.Label(tpl_row, text="Templates", style="Subheader.TLabel"
                  ).pack(side=tk.LEFT)
        ttk.Button(tpl_row, text="＋ New",
                   command=self._dlg_new_template).pack(side=tk.RIGHT)

        tpl_search = ttk.Frame(parent, padding=(14, 0, 14, 6))
        tpl_search.pack(fill=tk.X)
        self.search_templates_var = tk.StringVar()
        self.search_templates_var.trace_add(
            "write", lambda *_a: self._refresh_templates())
        ttk.Entry(tpl_search, textvariable=self.search_templates_var
                  ).pack(fill=tk.X)

        self.templates_list = tk.Listbox(parent, height=6, activestyle="none",
                                          bg=COLORS["card"], relief="flat",
                                          highlightthickness=0,
                                          font=FONT_BODY)
        self.templates_list.pack(fill=tk.X, padx=14, pady=(0, 14))
        self.templates_list.bind("<Double-Button-1>",
                                    lambda _e: self._edit_selected_template())

    def _build_detail(self, parent):
        # Header card
        header_card = ttk.Frame(parent, style="Card.TFrame", padding=16)
        header_card.pack(fill=tk.X, padx=14, pady=(14, 8))

        self.detail_project_var = tk.StringVar(value="(select a project)")
        self.detail_visibility_var = tk.StringVar(value="")
        self.detail_repo_var = tk.StringVar(value="")

        top = ttk.Frame(header_card, style="Card.TFrame")
        top.pack(fill=tk.X)
        ttk.Label(top, textvariable=self.detail_project_var,
                  font=FONT_TITLE, style="Card.TLabel").pack(side=tk.LEFT)
        self.vis_badge = ttk.Label(top, textvariable=self.detail_visibility_var,
                                     style="Card.TLabel", padding=(8, 2))
        self.vis_badge.pack(side=tk.LEFT, padx=(14, 0))

        repo_link = ttk.Label(header_card, textvariable=self.detail_repo_var,
                                foreground=COLORS["primary"],
                                style="Card.TLabel", cursor="hand2")
        repo_link.pack(anchor="w", pady=(4, 0))
        repo_link.bind("<Button-1>", lambda _e: self._on_open_github())

        # Action buttons
        actions = ttk.Frame(header_card, style="Card.TFrame")
        actions.pack(fill=tk.X, pady=(12, 0))
        self.open_folder_btn = ttk.Button(actions, text="📁 Open Folder",
                                            command=self._on_open_folder)
        self.open_folder_btn.pack(side=tk.LEFT)
        self.vscode_btn = ttk.Button(actions, text="💻 Open in VS Code",
                                       command=self._on_open_vscode)
        self.vscode_btn.pack(side=tk.LEFT, padx=(6, 0))
        self.env_btn = ttk.Button(actions, text="⚙ Setup Env",
                                    command=self._on_setup_env)
        self.env_btn.pack(side=tk.LEFT, padx=(6, 0))
        self.github_btn = ttk.Button(actions, text="🔗 GitHub",
                                       command=self._on_open_github)
        self.github_btn.pack(side=tk.LEFT, padx=(6, 0))
        
        self.changes_btn = ttk.Button(actions, text="📝 View Changes…",
                                        command=self._dlg_view_changes)
        self.changes_btn.pack(side=tk.LEFT, padx=(6, 0))

        # Changes banner
        self.changes_banner = tk.Frame(parent, bg=COLORS["warning_bg"],
                                         highlightbackground=COLORS["warning"],
                                         highlightthickness=1)
        banner_inner = ttk.Frame(self.changes_banner)
        banner_inner.pack(fill=tk.X, padx=14, pady=10)
        row = ttk.Frame(banner_inner)
        row.pack(fill=tk.X)
        self.changes_label = tk.Label(row, bg=COLORS["warning_bg"],
                                        fg=COLORS["warning"], font=FONT_BOLD)
        self.changes_label.pack(side=tk.LEFT)
        ttk.Button(row, text="View Changes…",
                   command=self._dlg_view_changes).pack(side=tk.RIGHT)
        ttk.Button(row, text="Commit…",
                   command=self._dlg_commit).pack(side=tk.RIGHT, padx=(0, 6))

        self.changes_files_label = tk.Label(banner_inner, bg=COLORS["warning_bg"],
                                              fg=COLORS["muted"], font=FONT_MONO,
                                              anchor="w", justify="left")
        self.changes_files_label.pack(fill=tk.X, pady=(4, 0))

        # Version / git action bar
        bar = ttk.Frame(parent, padding=(14, 4, 14, 6))
        bar.pack(fill=tk.X)

        ttk.Label(bar, text="Version:").pack(side=tk.LEFT)
        self.version_combo = ttk.Combobox(bar, state="readonly", width=12)
        self.version_combo.pack(side=tk.LEFT, padx=(4, 8))
        self.version_combo.bind("<<ComboboxSelected>>", self._on_version_combo)

        self.sync_badge = ttk.Label(bar, text="", foreground=COLORS["muted"])
        self.sync_badge.pack(side=tk.LEFT, padx=(0, 12))

        # Right side action buttons
        self.more_btn = ttk.Button(bar, text="More ▾",
                                     command=self._show_more_menu)
        self.more_btn.pack(side=tk.RIGHT, padx=(6, 0))
        self.push_drop_btn = ttk.Button(bar, text="▾", width=2,
                                          command=self._show_push_menu)
        self.push_drop_btn.pack(side=tk.RIGHT, padx=(2, 0))
        self.push_btn = ttk.Button(bar, text="⬆ Push",
                                     command=self._on_push)
        self.push_btn.pack(side=tk.RIGHT, padx=(2, 0))
        self.pull_btn = ttk.Button(bar, text="⬇ Pull",
                                     command=self._on_pull)
        self.pull_btn.pack(side=tk.RIGHT, padx=(2, 0))
        self.merge_btn = ttk.Button(bar, text="Merge / PR…",
                                      command=self._dlg_merge)
        self.merge_btn.pack(side=tk.RIGHT, padx=(2, 0))
        self.commit_btn = ttk.Button(bar, text="Commit…",
                                       command=self._dlg_commit)
        self.commit_btn.pack(side=tk.RIGHT, padx=(2, 0))
        self.create_repo_btn = ttk.Button(bar, text="Create Repository…",
                                            command=self._dlg_create_repo)
        self.create_repo_btn.pack(side=tk.RIGHT, padx=(2, 0))

        # Versions tree
        ttk.Label(parent, text="Versions", style="Section.TLabel"
                  ).pack(anchor="w", padx=14, pady=(8, 4))
        tree_frame = ttk.Frame(parent, padding=(14, 0, 14, 8))
        tree_frame.pack(fill=tk.BOTH, expand=True)
        cols = ("label", "branch", "parent", "sync", "ahead", "behind")
        self.versions_tree = ttk.Treeview(tree_frame, columns=cols,
                                            show="headings", height=8)
        for c, h, w, a in [
            ("label",  "Version", 80,  "w"),
            ("branch", "Branch",  240, "w"),
            ("parent", "Parent",  80,  "w"),
            ("sync",   "Sync",    110, "w"),
            ("ahead",  "↑",        40,  "center"),
            ("behind", "↓",        40,  "center"),
        ]:
            self.versions_tree.heading(c, text=h)
            self.versions_tree.column(c, width=w, anchor=a)
        self.versions_tree.pack(fill=tk.BOTH, expand=True)
        self.versions_tree.bind("<Button-3>", self._on_version_right_click)
        self.versions_tree.bind("<Button-2>", self._on_version_right_click)

        # Version actions
        va = ttk.Frame(parent, padding=(14, 0, 14, 12))
        va.pack(fill=tk.X)
        ttk.Button(va, text="New Version…",
                   command=self._dlg_new_version).pack(side=tk.LEFT)
        ttk.Button(va, text="Switch to Selected",
                   command=self._on_switch_version).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(va, text="Delete Selected…",
                   command=self._dlg_delete_version).pack(side=tk.LEFT, padx=(8, 0))

    # ------------------------------------------------------------- refresh

    def _refresh_all(self):
        self._refresh_projects()
        self._refresh_templates()

    def _refresh_projects(self):
        q = self.search_projects_var.get() if hasattr(self, "search_projects_var") else ""
        self._filtered_projects = self.memory.list_projects(q)
        self.projects_tree.delete(*self.projects_tree.get_children())
        for p in self._filtered_projects:
            state_marker = {
                RepoState.SYNCED.value: "●",
                RepoState.REPO_PENDING.value: "⚠",
                RepoState.REPO_CREATED.value: "◐",
                RepoState.LOCAL_ONLY.value: "○",
            }.get(p["repo_state"], "·")
            lock = "🔒" if p["visibility"] == "private" else "🌐"
            # aggregate sync
            s = self.memory.get_project_summary(p["name"])
            sync_text = ""
            if s["ahead_total"]:
                sync_text += f"↑{s['ahead_total']}"
            if s["behind_total"]:
                sync_text += f"↓{s['behind_total']}"
            tag = ""
            if p["repo_state"] == RepoState.REPO_PENDING.value:
                tag = "pending"
            elif p["repo_state"] == RepoState.LOCAL_ONLY.value:
                tag = "local"
            self.projects_tree.insert("", "end", iid=p["name"], values=(
                state_marker, p["name"], lock, sync_text,
            ), tags=(tag,) if tag else ())

    def _refresh_templates(self):
        q = self.search_templates_var.get() if hasattr(self, "search_templates_var") else ""
        self._filtered_templates = self.memory.list_templates(q)
        self.templates_list.delete(0, tk.END)
        for t in self._filtered_templates:
            self.templates_list.insert(tk.END, f"  {t['name']}")

    def _select_project_by_name(self, name):
        if self.projects_tree.exists(name):
            self.projects_tree.selection_set(name)
            self.projects_tree.focus(name)
            self._current_project = name
            self._show_project(name)

    # ------------------------------------------------------------- selection

    def _selected_project(self):
        sel = self.projects_tree.selection()
        if not sel: return None
        return sel[0]

    def _on_project_select(self, _e=None):
        name = self._selected_project()
        if name and name != self._current_project:
            self._current_project = name
            self._show_project(name)

    def _on_project_double_click(self, _e=None):
        name = self._selected_project()
        if not name: return
        self._current_project = name
        self._on_open_vscode()

    def _show_project(self, name):
        proj = self.memory.get_project(name)
        if not proj: return

        self.detail_project_var.set(proj["name"])
        if proj["visibility"] == "private":
            self.detail_visibility_var.set("🔒 Private")
            self.vis_badge.configure(foreground=COLORS["muted"])
        else:
            self.detail_visibility_var.set("🌐 Public")
            self.vis_badge.configure(foreground=COLORS["primary"])
        self.detail_repo_var.set(proj.get("repo_url") or "(no repository yet)")

        labels = list(proj["versions"].keys())
        self.version_combo["values"] = labels
        current = proj.get("current_version")
        if current in labels:
            self.version_combo.set(current)
        elif labels:
            self.version_combo.set(labels[0])

        for row in self.versions_tree.get_children():
            self.versions_tree.delete(row)
        for label, ver in proj["versions"].items():
            self.versions_tree.insert("", "end", iid=label, values=(
                label, ver["branch"], ver.get("parent") or "—",
                ver.get("sync_state", "UNKNOWN"),
                ver.get("ahead", 0), ver.get("behind", 0),
            ))

        summary = self.memory.get_project_summary(name)
        self.sync_badge.config(
            text=f"↑ {summary['ahead_total']}    ↓ {summary['behind_total']}"
        )

        state = proj["repo_state"]
        has_repo = state in (RepoState.SYNCED.value, RepoState.REPO_CREATED.value)
        needs_repo = state in (RepoState.LOCAL_ONLY.value,
                                 RepoState.REPO_PENDING.value)

        self._set_enabled(has_repo,
                            self.push_btn, self.pull_btn,
                            self.merge_btn, self.push_drop_btn)
        # Commit works without a repo — local commits are valid
        self.commit_btn.configure(state=tk.NORMAL)
        self.create_repo_btn.configure(
            state=tk.NORMAL if needs_repo else tk.DISABLED)

        # Always re-enable these — _set_busy_ui disables them during ops
        self.open_folder_btn.configure(state=tk.NORMAL)
        self.vscode_btn.configure(state=tk.NORMAL)
        self.github_btn.configure(state=tk.NORMAL if proj.get("repo_url") else tk.DISABLED)
        self.changes_btn.configure(state=tk.NORMAL)
        # Env button state
        self._refresh_env_button(name)
        # Changes banner
        self._refresh_changes_banner(name)

    def _refresh_env_button(self, name):
        try:
            kind = self.scaffolder.detect_env_type(name)
            has = self.scaffolder.has_env(name)
        except Exception:
            return
        if kind == "none":
            self.env_btn.configure(state=tk.DISABLED, text="⚙ Setup Env")
        elif has:
            self.env_btn.configure(state=tk.DISABLED,
                                     text=f"⚙ Env ready ({kind})")
        else:
            self.env_btn.configure(state=tk.NORMAL,
                                     text=f"⚙ Setup {kind} env")

    def _refresh_changes_banner(self, name):
        try:
            changes = self.scaffolder.get_changes_detailed(name)
        except Exception:
            changes = []
        if changes:
            n = len(changes)
            self.changes_label.config(
                text=f"⚠ {n} uncommitted change{'s' if n != 1 else ''}"
            )
            preview = ", ".join(c["path"] for c in changes[:3])
            if n > 3:
                preview += f" … +{n - 3} more"
            self.changes_files_label.config(text=preview)
            self.changes_banner.pack(fill=tk.X, padx=14, pady=(0, 8),
                                       before=self.version_combo.master)
        else:
            self.changes_banner.pack_forget()

    def _set_enabled(self, enabled, *widgets):
        state = tk.NORMAL if enabled else tk.DISABLED
        for w in widgets:
            try: w.configure(state=state)
            except Exception: pass
    def _detect_vscode(self):
        import shutil, sys, os
        if shutil.which("code"):
            return True
        if sys.platform.startswith("win"):
            paths = [
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "Code.exe",
                Path("C:/Program Files/Microsoft VS Code/Code.exe"),
                Path("C:/Program Files (x86)/Microsoft VS Code/Code.exe"),
            ]
        elif sys.platform == "darwin":
            paths = [
                Path("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"),
                Path.home() / "Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
            ]
        else:
            paths = [
                Path("/usr/share/code/code"), Path("/usr/bin/code"),
                Path("/snap/bin/code"),
                Path("/var/lib/flatpak/exports/bin/com.visualstudio.code"),
            ]
        return any(p.exists() for p in paths)
    # ------------------------------------------------------------- focus

    def _on_focus_in(self, _e=None):
        if not self._current_project: return
        if self._busy: return
        now = time.time()
        if now - self._last_auto_refresh < 20:
            return
        self._last_auto_refresh = now
        name = self._current_project

        def work():
            self.scaffolder.refresh_all_versions(name, fetch_first=False)

        def done(_r=None):
            if self._current_project == name:
                self._show_project(name)
                self._refresh_projects()

        self._run_async("Refreshing (local)…", work, on_done=done)

    # ------------------------------------------------------------- actions

    def _on_version_combo(self, _e=None):
        if not self._current_project: return
        label = self.version_combo.get()
        if not label: return
        proj = self.memory.get_project(self._current_project)
        if proj and proj["current_version"] == label:
            return

        name = self._current_project

        def work():
            changes = self.scaffolder.get_uncommitted_changes(name)
            if changes:
                raise UncommittedChangesError(changes)
            self.scaffolder.switch_version(name, label)

        def done():
            self.status_var.set(f"Switched to {label}")
            self._show_project(name)
            self._refresh_projects()

        def err(e):
            if isinstance(e, UncommittedChangesError):
                UncommittedDialog(self, name, on_done=done)
            else:
                messagebox.showerror("Switch failed", str(e))
                self._show_project(name)

        self._run_async(f"Switching to {label}…", work, on_done=done, on_error=err)

    def _on_switch_version(self):
        sel = self.versions_tree.selection()
        if not sel:
            messagebox.showinfo("Select a version", "Select a version in the table.")
            return
        self.version_combo.set(sel[0])
        self._on_version_combo()

    def _on_push(self):
        if not self._current_project: return
        proj = self._current_project
        label = self.version_combo.get() or None

        def work():
            self.scaffolder.push(proj, label, progress=self._set_status_ts)

        self._run_async(f"Pushing {label or 'current'}…", work,
                         on_done=lambda _r: self._after_sync("Push complete"))

    def _show_push_menu(self):
        if not self._current_project: return
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Push current version", command=self._on_push)
        menu.add_command(label="Push all versions", command=self._on_push_all)
        try:
            x = self.push_drop_btn.winfo_rootx()
            y = self.push_drop_btn.winfo_rooty() + self.push_drop_btn.winfo_height()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _on_push_all(self):
        if not self._current_project: return
        proj = self._current_project

        def work():
            return self.scaffolder.push_all(proj, progress=self._set_status_ts)

        def done(result=None):
            ok = [k for k, v in (result or {}).items() if v == "ok"]
            failed = [k for k, v in (result or {}).items() if v != "ok"]
            msg = f"Pushed: {', '.join(ok) or '—'}"
            if failed:
                msg += f"   Failed: {', '.join(failed)}"
            self.status_var.set(msg)
            self._show_project(proj)
            self._refresh_projects()

        self._run_async("Pushing all versions…", work, on_done=done)

    def _on_pull(self):
        if not self._current_project: return
        proj = self._current_project
        label = self.version_combo.get() or None

        def work():
            self.scaffolder.pull(proj, label, strategy="ff-only",
                                  progress=self._set_status_ts)

        def done(_r=None):
            self._after_sync("Pull complete")

        def err(e):
            if isinstance(e, DivergedError):
                DivergedDialog(self, proj, label,
                                on_done=lambda: self._after_sync("Resolved"))
            else:
                messagebox.showerror("Pull failed", str(e))

        self._run_async(f"Pulling {label or 'current'}…", work,
                         on_done=done, on_error=err)

    def _on_refresh_status(self):
        if not self._current_project: return
        proj = self._current_project

        def work():
            self.scaffolder.refresh_all_versions(proj, fetch_first=True)

        self._run_async("Refreshing status…", work,
                         on_done=lambda _r: self._after_sync("Status refreshed"))

    def _on_open_folder(self):
        if not self._current_project: return
        proj = self.memory.get_project(self._current_project)
        if not proj: return
        try:
            self.scaffolder.open_in_file_manager(proj["path"])
        except ScaffolderError as e:
            messagebox.showerror("Open folder", str(e))

    def _on_open_github(self):
        if not self._current_project: return
        proj = self.memory.get_project(self._current_project)
        if not proj or not proj.get("repo_url"):
            messagebox.showinfo("No repo", "This project has no GitHub repository.")
            return
        self.scaffolder.open_url(proj["repo_url"])

    def _on_open_vscode(self):
        if not self._current_project: return
        name = self._current_project
        try:
            kind = self.scaffolder.detect_env_type(name)
            has = self.scaffolder.has_env(name)
        except Exception:
            kind, has = "none", True

        if kind != "none" and not has:
            if messagebox.askyesno(
                "Setup environment?",
                f"No {kind} environment found for this project.\n\n"
                f"Create it now before opening VS Code?",
                parent=self,
            ):
                def work():
                    self.scaffolder.setup_env(name, progress=self._set_status_ts)
                def done(_r=None):
                    self.status_var.set("Environment ready")
                    self._open_vscode_now()
                self._run_async("Setting up environment…", work, on_done=done)
                return
        self._open_vscode_now()

    def _open_vscode_now(self):
        if not self._current_project: return
        proj = self.memory.get_project(self._current_project)
        if not proj: return
        try:
            self.scaffolder.open_in_vscode(proj["path"])
            self.status_var.set("Opened in VS Code")
        except ScaffolderError as e:
            messagebox.showerror("VS Code", str(e))

    def _on_setup_env(self):
        if not self._current_project: return
        name = self._current_project

        def work():
            self.scaffolder.setup_env(name, progress=self._set_status_ts)

        def done(_r=None):
            self.status_var.set("Environment ready")
            self._show_project(name)

        self._run_async("Setting up environment…", work, on_done=done)

    def _on_discard(self):
        if not self._current_project: return
        name = self._current_project
        if not messagebox.askyesno("Discard changes?",
                                    "All uncommitted changes will be lost. Continue?"):
            return

        def work():
            self.scaffolder.discard_changes(name)

        def done(_r=None):
            self.status_var.set("Changes discarded")
            self._show_project(name)

        self._run_async("Discarding…", work, on_done=done)

    def _after_sync(self, msg):
        self.status_var.set(msg)
        self._refresh_projects()
        if self._current_project:
            self._show_project(self._current_project)

    def _show_more_menu(self):
        if not self._current_project: return
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Rename Project…",    command=self._dlg_rename_project)
        menu.add_command(label="Change Visibility…", command=self._dlg_change_visibility)
        menu.add_command(label="Delete Project…",    command=self._dlg_delete_project)
        menu.add_separator()
        menu.add_command(label="Use connected account for commits",
                         command=self._dlg_fix_identity)
        menu.add_command(label="Force Push…",        command=self._dlg_force_push)
        menu.add_separator()
        menu.add_command(label="Edit Notes…",        command=self._dlg_edit_notes)
        try:
            x = self.more_btn.winfo_rootx()
            y = self.more_btn.winfo_rooty() + self.more_btn.winfo_height()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _on_version_right_click(self, event):
        row = self.versions_tree.identify_row(event.y)
        if not row: return
        self.versions_tree.selection_set(row)
        label = row
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=f"Switch to {label}",
                         command=lambda: (self.version_combo.set(label),
                                            self._on_version_combo()))
        menu.add_separator()
        menu.add_command(label="Push this version",
                         command=lambda: self._push_specific(label))
        menu.add_command(label="Pull this version",
                         command=lambda: self._pull_specific(label))
        menu.add_separator()
        menu.add_command(label="Delete this version…",
                         command=lambda: self._dlg_delete_version(specific=label))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _push_specific(self, label):
        proj = self._current_project
        if not proj: return
        self._run_async(
            f"Pushing {label}…",
            lambda: self.scaffolder.push(proj, label, progress=self._set_status_ts),
            on_done=lambda _r: self._after_sync(f"Pushed {label}"))

    def _pull_specific(self, label):
        proj = self._current_project
        if not proj: return

        def work():
            self.scaffolder.pull(proj, label, strategy="ff-only",
                                  progress=self._set_status_ts)

        def done(_r=None):
            self._after_sync(f"Pulled {label}")

        def err(e):
            if isinstance(e, DivergedError):
                DivergedDialog(self, proj, label,
                                on_done=lambda: self._after_sync("Resolved"))
            else:
                messagebox.showerror("Pull failed", str(e))

        self._run_async(f"Pulling {label}…", work, on_done=done, on_error=err)

    # ------------------------------------------------------------- dialogs

    def _dlg_new_template(self): NewTemplateDialog(self)

    def _dlg_fix_identity(self):
        if not self._current_project:
            return
        proj = self.memory.get_project(self._current_project)
        if not proj:
            return
        account = proj.get("owner")
        if not account:
            messagebox.showinfo("No account",
                                  "This project has no connected GitHub account.",
                                  parent=self)
            return
        if not messagebox.askyesno(
            "Use connected account for commits",
            f"Set git identity for this project to '{account}'?\n\n"
            "This affects FUTURE commits only. Existing commits keep their "
            "original author — to rewrite them you'd need to rebase or use "
            "git filter-repo manually.",
            parent=self,
        ):
            return
        try:
            info = self.scaffolder.fix_git_identity(self._current_project)
        except ScaffolderError as e:
            messagebox.showerror("Fix identity failed", str(e), parent=self)
            return
        self.status_var.set(
            f"Git identity → {info['name']} <{info['email']}>")
        

    def _dlg_new_project(self):
        if not self.memory.list_templates():
            messagebox.showinfo("No templates",
                                  "Create a template first (File → New Template).")
            return
        NewProjectDialog(self)
    def _dlg_clone_repo(self): CloneRepoDialog(self)
    
    def _dlg_new_version(self):
        if not self._current_project: return
        NewVersionDialog(self, self._current_project)
    def _dlg_view_changes(self):
        if not self._current_project:
            return
        name = self._current_project
        ChangesDialog(self, name,
                      on_change=lambda: self._refresh_changes_banner(name))
        
    def _dlg_commit(self):
        if not self._current_project: return
        CommitDialog(self, self._current_project,
                     on_complete=lambda: self._show_project(self._current_project))
    def _dlg_merge(self):
        if not self._current_project: return
        MergeDialog(self, self._current_project)
    def _dlg_create_repo(self):
        if not self._current_project: return
        CreateRepoDialog(self, self._current_project)
    def _dlg_add_account(self): AddAccountDialog(self)
    def _dlg_manage_accounts(self): ManageAccountsDialog(self)
    def _dlg_settings(self): SettingsDialog(self)

    def _dlg_import_folder(self):
        path = filedialog.askdirectory(title="Pick an existing git repo folder")
        if not path: return
        from tkinter import simpledialog
        name = simpledialog.askstring("Project name", "Name:", parent=self)
        if not name: return

        def work():
            return self.scaffolder.link_existing_folder(path, name)

        def done(_r=None):
            self.status_var.set(f"Imported '{name}'")
            self._refresh_all()
            self._select_project_by_name(name)

        self._run_async("Importing…", work, on_done=done)

    def _dlg_rename_project(self):
        if not self._current_project: return
        RenameProjectDialog(self, self._current_project)

    def _dlg_change_visibility(self):
        if not self._current_project: return
        ChangeVisibilityDialog(self, self._current_project)

    def _dlg_delete_project(self):
        if not self._current_project: return
        DeleteProjectDialog(self, self._current_project)

    def _dlg_delete_version(self, specific=None):
        if not self._current_project: return
        sel = specific or (self.versions_tree.selection()[0]
                            if self.versions_tree.selection() else None)
        if not sel:
            messagebox.showinfo("Select a version", "Select a version to delete.")
            return
        DeleteVersionDialog(self, self._current_project, sel)

    def _dlg_force_push(self):
        if not self._current_project: return
        ForcePushDialog(self, self._current_project,
                         self.version_combo.get() or "v1")

    def _dlg_edit_notes(self):
        if not self._current_project: return
        proj = self.memory.get_project(self._current_project)
        from tkinter import simpledialog
        notes = simpledialog.askstring("Notes", "Project notes:",
                                        initialvalue=proj.get("notes", ""),
                                        parent=self)
        if notes is not None:
            self.memory.set_notes(self._current_project, notes)

    def _edit_selected_template(self):
        sel = self.templates_list.curselection()
        if not sel or sel[0] >= len(self._filtered_templates): return
        name = self._filtered_templates[sel[0]]["name"]
        TemplateEditorDialog(self, name)

    def _open_error_log(self): ErrorLogWindow(self)

    # ---------------------------------------------------------- async model

    def _set_status_ts(self, msg):
        self.after(0, lambda: self.status_var.set(msg))

    def _run_async(self, start, work, on_done=None, on_error=None):
        if self._busy:
            self.status_var.set("Please wait — another operation is running…")
            return
        self._busy = True
        self._set_busy_ui(True, start)

        def runner():
            try:
                result = work()
                self.after(0, lambda: self._finish_async(
                    True, result, on_done, on_error))
            except Exception as e:
                self.after(0, lambda e=e: self._finish_async(
                    False, e, on_done, on_error))

        threading.Thread(target=runner, daemon=True).start()

    def _finish_async(self, ok, result, on_done, on_error):
        self._busy = False
        self._set_busy_ui(False)
        if ok:
            if on_done:
                on_done(result)
            return
        # error path
        if isinstance(result, TokenExpiredError):
            self.memory.log_error("auth", str(result))
            self.status_var.set(f"Token expired for '{result.account}'")
            messagebox.showerror(
                "Token expired",
                f"GitHub token for '{result.account}' is invalid or expired.\n\n"
                "Update it via GitHub → Manage Accounts → Replace Token…")
            return
        self.memory.log_error("operation", str(result))
        if on_error:
            on_error(result)
        else:
            messagebox.showerror("Error", str(result))
        self.status_var.set("Error — see File → Error Log")

    def _set_busy_ui(self, busy, message=""):
        if busy:
            self.progress.pack(side=tk.LEFT, padx=(10, 0))
            self.progress.start(12)
            self.status_var.set(message or "Working…")
            for w in (self.push_btn, self.pull_btn, self.commit_btn,
                      self.merge_btn, self.push_drop_btn,
                      self.create_repo_btn, self.vscode_btn,
                      self.env_btn):
                try: w.configure(state=tk.DISABLED)
                except Exception: pass
        else:
            self.progress.stop()
            self.progress.pack_forget()
            if self._current_project:
                self._show_project(self._current_project)


class UncommittedChangesError(Exception):
    def __init__(self, changes):
        self.changes = changes
        super().__init__(f"{len(changes)} uncommitted change(s)")


# ============================================================================
# Dialogs
# ============================================================================

class TokenExpiredDialog(BaseDialog):
    def __init__(self, app, account):
        super().__init__(app, "Update GitHub token", "480x220")
        self.new_token = None
        ttk.Label(self.body,
                  text=f"Enter a new Personal Access Token for '{account}':",
                  wraplength=420).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.token_var = tk.StringVar()
        ttk.Entry(self.body, textvariable=self.token_var, show="•", width=48
                  ).grid(row=1, column=0, sticky="we")

        def save():
            t = self.token_var.get().strip()
            if not t: return
            self.new_token = t
            self.destroy()

        self.buttons("Save", on_confirm=save)


class UncommittedDialog(BaseDialog):
    def __init__(self, app, project, on_done):
        super().__init__(app, "Uncommitted changes", "560x320")
        self.app, self.project, self.on_done = app, project, on_done
        ttk.Label(self.body, text="You have uncommitted changes.",
                  font=FONT_BOLD).grid(row=0, column=0, sticky="w")
        ttk.Label(self.body, text="Commit or discard before switching versions.",
                  foreground=COLORS["muted"]).grid(row=1, column=0, sticky="w",
                                                    pady=(2, 8))

        self.changes_text = tk.Text(self.body, height=8, width=66,
                                      bg=COLORS["card"], relief="flat",
                                      highlightthickness=1,
                                      highlightbackground=COLORS["border"])
        self.changes_text.grid(row=2, column=0, sticky="we")
        self._load()

        btns = ttk.Frame(self.body)
        btns.grid(row=3, column=0, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Discard all", command=self._discard
                   ).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(btns, text="Commit first…", style="Primary.TButton",
                   command=self._commit).pack(side=tk.RIGHT, padx=(0, 6))

    def _load(self):
        try:
            changes = self.app.scaffolder.get_uncommitted_changes(self.project)
        except Exception:
            changes = []
        self.changes_text.delete("1.0", tk.END)
        for c in changes:
            self.changes_text.insert(tk.END, f"{c['status']:>3}  {c['path']}\n")
        self.changes_text.config(state=tk.DISABLED)

    def _discard(self):
        if not messagebox.askyesno("Discard?",
                                    "All changes will be permanently lost.",
                                    parent=self):
            return
        def work(): self.app.scaffolder.discard_changes(self.project)
        def done(_r=None): self.destroy(); self.on_done()
        self.app._run_async("Discarding…", work, on_done=done)

    def _commit(self):
        self.destroy()
        CommitDialog(self.app, self.project, on_complete=self.on_done)


class CommitDialog(BaseDialog):
    def __init__(self, app, project, on_complete=None):
        super().__init__(app, "Commit changes", "680x520")
        self.app, self.project, self.on_complete = app, project, on_complete

        ttk.Label(self.body, text=f"Project: {project}",
                  font=FONT_BOLD).grid(row=0, column=0, sticky="w")
        ttk.Label(self.body, text="Changes:", foreground=COLORS["muted"]
                  ).grid(row=1, column=0, sticky="w", pady=(8, 2))

        tree_wrap = ttk.Frame(self.body)
        tree_wrap.grid(row=2, column=0, sticky="nsew")
        self.body.rowconfigure(2, weight=1)
        self.body.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_wrap, columns=("status", "path"),
                                  show="headings", height=10)
        self.tree.heading("status", text="Status")
        self.tree.heading("path", text="File")
        self.tree.column("status", width=90, anchor="w")
        self.tree.column("path", width=540, anchor="w")
        self.tree.pack(fill=tk.BOTH, expand=True)

        ttk.Label(self.body, text="Commit message:"
                  ).grid(row=3, column=0, sticky="w", pady=(12, 2))
        self.msg_text = tk.Text(self.body, height=4, width=74,
                                 bg=COLORS["card"], relief="flat",
                                 highlightthickness=1,
                                 highlightbackground=COLORS["border"])
        self.msg_text.grid(row=4, column=0, sticky="we")

        self.stage_all_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.body, text="Stage all changes",
                        variable=self.stage_all_var
                        ).grid(row=5, column=0, sticky="w", pady=(6, 0))

        btns = ttk.Frame(self.body)
        btns.grid(row=6, column=0, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Commit", style="Primary.TButton",
                   command=self._commit).pack(side=tk.RIGHT, padx=(0, 6))

        self._load()

    def _load(self):
        try:
            changes = self.app.scaffolder.get_uncommitted_changes(self.project)
        except Exception:
            changes = []
        for row in self.tree.get_children():
            self.tree.delete(row)
        for c in changes:
            self.tree.insert("", "end", values=(c["status"], c["path"]))
        if not changes:
            self.msg_text.insert("1.0", "(no changes)")
            self.msg_text.config(state=tk.DISABLED)

    def _commit(self):
        msg = self.msg_text.get("1.0", tk.END).strip()
        if not msg or msg == "(no changes)":
            messagebox.showwarning("No message", "Enter a commit message.",
                                    parent=self)
            return
        proj = self.project
        stage_all = self.stage_all_var.get()

        def work():
            self.app.scaffolder.commit(proj, msg, stage_all=stage_all)

        def done(_r=None):
            self.app.status_var.set("Committed")
            self.destroy()
            if self.on_complete:
                self.on_complete()

        self.app._run_async("Committing…", work, on_done=done)


class MergeDialog(BaseDialog):
    def __init__(self, app, project):
        super().__init__(app, "Merge / Pull Request", "620x460")
        self.app, self.project = app, project
        proj = app.memory.get_project(project) or {}
        versions = list(proj.get("versions", {}).keys())
        current = proj.get("current_version") or (versions[0] if versions else "")

        ttk.Label(self.body, text=f"Project: {project}",
                  font=FONT_BOLD).grid(row=0, column=0, columnspan=2, sticky="w")

        ttk.Label(self.body, text="Merge FROM:").grid(row=1, column=0, sticky="w",
                                                        pady=(12, 4))
        self.source_var = tk.StringVar(value=current)
        ttk.Combobox(self.body, textvariable=self.source_var, values=versions,
                     state="readonly", width=24
                     ).grid(row=1, column=1, sticky="w", padx=(8, 0))

        ttk.Label(self.body, text="Merge INTO:").grid(row=2, column=0, sticky="w",
                                                        pady=4)
        parents = [v for v in versions if v != current] or versions
        self.target_var = tk.StringVar(value=parents[0] if parents else "")
        ttk.Combobox(self.body, textvariable=self.target_var, values=versions,
                     state="readonly", width=24
                     ).grid(row=2, column=1, sticky="w", padx=(8, 0))

        ttk.Label(self.body, text="Strategy:").grid(row=3, column=0, sticky="w",
                                                      pady=4)
        self.strategy_var = tk.StringVar(value="merge")
        sf = ttk.Frame(self.body)
        sf.grid(row=3, column=1, sticky="w", padx=(8, 0))
        ttk.Radiobutton(sf, text="Merge commit", value="merge",
                        variable=self.strategy_var).pack(side=tk.LEFT)
        ttk.Radiobutton(sf, text="Squash", value="squash",
                        variable=self.strategy_var).pack(side=tk.LEFT, padx=(10, 0))

        ttk.Label(self.body, text="Mode:").grid(row=4, column=0, sticky="w",
                                                  pady=(12, 4))
        self.mode_var = tk.StringVar(value="local")
        mf = ttk.Frame(self.body)
        mf.grid(row=4, column=1, sticky="w", padx=(8, 0))
        ttk.Radiobutton(mf, text="Merge locally", value="local",
                        variable=self.mode_var).pack(side=tk.LEFT)
        ttk.Radiobutton(mf, text="Open Pull Request", value="pr",
                        variable=self.mode_var).pack(side=tk.LEFT, padx=(10, 0))

        ttk.Label(self.body, text="Title:").grid(row=5, column=0, sticky="w",
                                                   pady=(12, 4))
        self.title_var = tk.StringVar(
            value=f"Merge {current} into {parents[0] if parents else ''}")
        ttk.Entry(self.body, textvariable=self.title_var, width=50
                  ).grid(row=5, column=1, sticky="we", padx=(8, 0))

        ttk.Label(self.body, text="Body:").grid(row=6, column=0, sticky="nw",
                                                  pady=(8, 4))
        self.body_text = tk.Text(self.body, height=5, width=50,
                                   bg=COLORS["card"], relief="flat",
                                   highlightthickness=1,
                                   highlightbackground=COLORS["border"])
        self.body_text.grid(row=6, column=1, sticky="we", padx=(8, 0))
        self.body_text.insert("1.0",
            f"Merge version {current} into {parents[0] if parents else ''}.")

        self.buttons("Go", on_confirm=self._run)

    def _run(self):
        src, tgt = self.source_var.get(), self.target_var.get()
        strat, mode = self.strategy_var.get(), self.mode_var.get()
        if not src or not tgt or src == tgt:
            messagebox.showwarning("Invalid", "Pick two different versions.",
                                    parent=self)
            return
        proj = self.project
        title = self.title_var.get().strip()
        body = self.body_text.get("1.0", tk.END).strip()

        if mode == "local":
            def work():
                self.app.scaffolder.merge_version(
                    proj, src, tgt, strategy=strat,
                    progress=self.app._set_status_ts)
            def done(_r=None):
                self.app.status_var.set(f"Merged {src} → {tgt}")
                self.destroy()
                self.app._show_project(proj)
                self.app._refresh_projects()
            self.app._run_async(f"Merging {src} → {tgt}…", work, on_done=done)
        else:
            def work():
                return self.app.scaffolder.create_pull_request(
                    proj, src, tgt, title, body)
            def done(result=None):
                url = result.get("html_url") if result else ""
                self.app.status_var.set(f"PR opened: {url}")
                if url and messagebox.askyesno("PR created",
                                                 f"Open {url} in browser?",
                                                 parent=self.app):
                    self.app.scaffolder.open_url(url)
                self.destroy()
            self.app._run_async("Opening Pull Request…", work, on_done=done)

class ChangesDialog(BaseDialog):
    """Browse uncommitted changes with per-file actions."""

    def __init__(self, app, project, on_change=None):
        super().__init__(app, f"Changes in '{project}'", "860x620")
        self.resizable(True, True)
        self.app, self.project = app, project
        self.on_change = on_change

        # Header
        top = ttk.Frame(self.body)
        top.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 8))
        self.count_var = tk.StringVar()
        ttk.Label(top, textvariable=self.count_var, font=FONT_BOLD
                  ).pack(side=tk.LEFT)
        ttk.Button(top, text="⟳ Refresh", command=self._load
                   ).pack(side=tk.RIGHT)
        ttk.Button(top, text="Discard All", style="Danger.TButton",
                   command=self._discard_all
                   ).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(top, text="Restore All", command=self._restore_all
                   ).pack(side=tk.RIGHT, padx=(0, 6))

        # Tree
        cols = ("status", "path")
        self.tree = ttk.Treeview(self.body, columns=cols, show="headings",
                                   height=18, selectmode="extended")
        self.tree.heading("status", text="Status")
        self.tree.heading("path", text="File")
        self.tree.column("status", width=100, anchor="w")
        self.tree.column("path", width=660, anchor="w")
        self.tree.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.body.rowconfigure(1, weight=1)
        self.body.columnconfigure(0, weight=1)
        self.tree.tag_configure("untracked", foreground=COLORS["muted"])

        scroll = ttk.Scrollbar(self.body, orient=tk.VERTICAL,
                                command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=1, column=2, sticky="ns")

        self.tree.bind("<Double-Button-1>", lambda _e: self._open_selected())
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Button-2>", self._on_right_click)

        # Bottom buttons
        btns = ttk.Frame(self.body)
        btns.grid(row=2, column=0, columnspan=3, sticky="we", pady=(10, 0))
        ttk.Button(btns, text="📂 Open File", command=self._open_selected
                   ).pack(side=tk.LEFT)
        ttk.Button(btns, text="↺ Restore Selected", command=self._restore_selected
                   ).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btns, text="🗑 Discard Selected",
                   command=self._discard_selected).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btns, text="Close", command=self.destroy).pack(side=tk.RIGHT)
        self.commit_btn = ttk.Button(btns, text="Commit All…",
                                       style="Primary.TButton",
                                       command=self._commit)
        self.commit_btn.pack(side=tk.RIGHT, padx=(0, 6))

        self._load()

    def _load(self):
        try:
            changes = self.app.scaffolder.get_changes_detailed(self.project)
        except Exception as e:
            messagebox.showerror("Load failed", str(e), parent=self)
            return
        self.tree.delete(*self.tree.get_children())
        for c in changes:
            tags = ("untracked",) if c["kind"] == "untracked" else ()
            self.tree.insert("", "end", iid=c["path"],
                              values=(c["status"], c["path"]), tags=tags)
        n = len(changes)
        self.count_var.set(f"{n} uncommitted change{'s' if n != 1 else ''}")
        self.commit_btn.configure(state=tk.NORMAL if n else tk.DISABLED)

    def _selected_paths(self):
        return list(self.tree.selection())

    def _open_selected(self):
        paths = self._selected_paths()
        if not paths:
            messagebox.showinfo("Select a file", "Select a file first.",
                                  parent=self)
            return
        try:
            self.app.scaffolder.open_file(self.project, paths[0])
        except ScaffolderError as e:
            messagebox.showerror("Open failed", str(e), parent=self)

    def _restore_selected(self):
        paths = self._selected_paths()
        if not paths:
            return
        if not messagebox.askyesno(
                "Restore",
                f"Restore {len(paths)} file(s) from the last commit?\n\n"
                "Untracked files will be deleted.",
                parent=self):
            return
        errors = []
        for p in paths:
            try:
                self.app.scaffolder.restore_file(self.project, p)
            except Exception as e:
                errors.append(f"{p}: {e}")
        self._load()
        self._notify_change()
        if errors:
            messagebox.showerror("Some restores failed", "\n".join(errors),
                                  parent=self)

    def _discard_selected(self):
        paths = self._selected_paths()
        if not paths:
            return
        if not messagebox.askyesno(
                "Discard",
                f"Discard changes in {len(paths)} file(s)?\n\n"
                "Tracked files will be restored; untracked files will be deleted.",
                parent=self):
            return
        errors = []
        for p in paths:
            try:
                self.app.scaffolder.restore_file(self.project, p)
            except Exception as e:
                errors.append(f"{p}: {e}")
        self._load()
        self._notify_change()
        if errors:
            messagebox.showerror("Some discards failed", "\n".join(errors),
                                  parent=self)

    def _restore_all(self):
        if not messagebox.askyesno(
                "Restore all",
                "Restore ALL files from the last commit?\n\n"
                "Untracked files will be deleted.",
                parent=self):
            return
        try:
            self.app.scaffolder.discard_changes(self.project)
        except Exception as e:
            messagebox.showerror("Restore failed", str(e), parent=self)
        self._load()
        self._notify_change()

    def _discard_all(self):
        if not messagebox.askyesno(
                "Discard all",
                "Permanently discard ALL uncommitted changes?",
                parent=self):
            return
        try:
            self.app.scaffolder.discard_changes(self.project)
        except Exception as e:
            messagebox.showerror("Discard failed", str(e), parent=self)
        self._load()
        self._notify_change()

    def _commit(self):
        self.destroy()
        self.app._dlg_commit()

    def _on_right_click(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Open File",          command=self._open_selected)
        menu.add_separator()
        menu.add_command(label="Restore from HEAD",  command=self._restore_selected)
        menu.add_command(label="Discard",            command=self._discard_selected)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _notify_change(self):
        if self.on_change:
            self.on_change()

class DivergedDialog(BaseDialog):
    def __init__(self, app, project, version_label, on_done):
        super().__init__(app, "Branch diverged", "520x260")
        self.app, self.project = app, project
        self.version_label, self.on_done = version_label, on_done

        ttk.Label(self.body,
                  text="Local and remote branches have diverged.",
                  font=FONT_BOLD).grid(row=0, column=0, sticky="w")
        ttk.Label(self.body, text="Choose how to reconcile them:",
                  foreground=COLORS["muted"]).grid(row=1, column=0, sticky="w",
                                                     pady=(4, 12))

        btns = ttk.Frame(self.body)
        btns.grid(row=2, column=0, sticky="w")
        ttk.Button(btns, text="Merge remote into local",
                   command=lambda: self._resolve("merge")).pack(fill=tk.X, pady=2)
        ttk.Button(btns, text="Rebase local onto remote",
                   command=lambda: self._resolve("rebase")).pack(fill=tk.X, pady=2)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(fill=tk.X,
                                                                     pady=(10, 0))

    def _resolve(self, strategy):
        proj, label = self.project, self.version_label
        def work():
            self.app.scaffolder.pull(proj, label, strategy=strategy,
                                       progress=self.app._set_status_ts)
        def done(_r=None):
            self.destroy(); self.on_done()
        self.app._run_async(f"{strategy.title()}ing…", work, on_done=done)


# ----- Templates -----------------------------------------------------------

class NewTemplateDialog(BaseDialog):
    def __init__(self, app):
        super().__init__(app, "New Template", "660x420")
        self.app = app
        self.name_var = tk.StringVar()
        self.path_var = tk.StringVar()
        self.desc_var = tk.StringVar()
        self.ignore_var = tk.StringVar(
            value=", ".join(app.memory.get_setting("default_ignore") or []))
        self.placeholders_var = tk.StringVar(value="project_name, author")

        self._row(0, "Name:", self.name_var)
        self._row(1, "Source folder:", self.path_var, browse=True)
        self._row(2, "Description:", self.desc_var)
        self._row(3, "Ignore patterns:", self.ignore_var)
        self._row(4, "Placeholders:", self.placeholders_var)
        ttk.Label(self.body, foreground=COLORS["muted"], wraplength=600,
                  text="Placeholders are replaced as {{project_name}} in contents "
                       "and file names."
                  ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.buttons("Create", on_confirm=self._create)

    def _row(self, r, label, var, browse=False):
        ttk.Label(self.body, text=label).grid(row=r, column=0, sticky="w", pady=4)
        ttk.Entry(self.body, textvariable=var, width=52
                  ).grid(row=r, column=1, sticky="we", padx=(8, 4))
        if browse:
            ttk.Button(self.body, text="Browse…", command=self._browse
                       ).grid(row=r, column=2, sticky="w")

    def _browse(self):
        p = filedialog.askdirectory(title="Pick template folder", parent=self)
        if p: self.path_var.set(p)

    def _create(self):
        name, path = self.name_var.get().strip(), self.path_var.get().strip()
        if not name or not path:
            messagebox.showwarning("Missing", "Name and source folder required.",
                                    parent=self)
            return
        ignore = [x.strip() for x in self.ignore_var.get().split(",") if x.strip()]
        placeholders = {k.strip(): "" for k in self.placeholders_var.get().split(",")
                          if k.strip()}
        try:
            self.app.scaffolder.create_template_from_folder(
                name=name, source_folder=path,
                description=self.desc_var.get().strip(),
                ignore=ignore, placeholders=placeholders)
        except Exception as e:
            messagebox.showerror("Failed", str(e), parent=self)
            return
        self.app.status_var.set(f"Template '{name}' created")
        self.app._refresh_all()
        self.destroy()


class TemplateEditorDialog(BaseDialog):
    def __init__(self, app, template_name):
        super().__init__(app, f"Edit — {template_name}", "660x420")
        self.app, self.name = app, template_name
        tpl = app.memory.get_template(template_name) or {}
        self.desc_var = tk.StringVar(value=tpl.get("description", ""))
        self.ignore_var = tk.StringVar(value=", ".join(tpl.get("ignore", [])))
        self.placeholders_var = tk.StringVar(
            value=", ".join(tpl.get("placeholders", {}).keys()))

        for i, (lbl, var) in enumerate([
            ("Description:", self.desc_var),
            ("Ignore patterns:", self.ignore_var),
            ("Placeholders:", self.placeholders_var),
        ]):
            ttk.Label(self.body, text=lbl).grid(row=i, column=0, sticky="w", pady=4)
            ttk.Entry(self.body, textvariable=var, width=52
                      ).grid(row=i, column=1, sticky="we", padx=(8, 4))

        ttk.Button(self.body, text="Preview…", command=self._preview
                   ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        btns = ttk.Frame(self.body)
        btns.grid(row=4, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Save", style="Primary.TButton",
                   command=self._save).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(btns, text="Delete…", command=self._delete).pack(side=tk.LEFT)

    def _preview(self):
        try:
            data = self.app.scaffolder.preview_template(self.name)
        except Exception as e:
            messagebox.showerror("Preview failed", str(e), parent=self)
            return
        TemplatePreviewDialog(self.app, self.name, data, parent=self)

    def _save(self):
        ignore = [x.strip() for x in self.ignore_var.get().split(",") if x.strip()]
        placeholders = {k.strip(): "" for k in self.placeholders_var.get().split(",")
                          if k.strip()}
        self.app.memory.update_template(self.name,
                                          description=self.desc_var.get().strip(),
                                          ignore=ignore, placeholders=placeholders)
        self.app.status_var.set(f"Template '{self.name}' updated")
        self.app._refresh_all()
        self.destroy()

    def _delete(self):
        if not messagebox.askyesno("Delete template",
                                    f"Delete template '{self.name}'?", parent=self):
            return
        self.app.memory.remove_template(self.name)
        self.app._refresh_all()
        self.destroy()


class TemplatePreviewDialog(BaseDialog):
    def __init__(self, app, name, data, parent=None):
        super().__init__(app, f"Preview — {name}", "600x520")
        if parent: self.transient(parent)
        ttk.Label(self.body, text=f"Source: {data.get('path', '')}",
                  foreground=COLORS["muted"]).grid(row=0, column=0, sticky="w")
        ttk.Label(self.body,
                  text=f"Placeholders: {', '.join(data.get('placeholders', {}).keys()) or '—'}",
                  foreground=COLORS["muted"]).grid(row=1, column=0, sticky="w",
                                                     pady=(2, 8))

        text = tk.Text(self.body, height=22, width=72,
                        bg=COLORS["card"], relief="flat",
                        highlightthickness=1,
                        highlightbackground=COLORS["border"], font=FONT_MONO)
        text.grid(row=2, column=0, sticky="nsew")
        self.body.rowconfigure(2, weight=1)
        files = data.get("files") or []
        if not files:
            text.insert("1.0", data.get("error", "(no files)"))
        else:
            for f in files: text.insert(tk.END, f + "\n")
        text.config(state=tk.DISABLED)

        ttk.Button(self.body, text="Close", command=self.destroy
                   ).grid(row=3, column=0, sticky="e", pady=(8, 0))


# ----- New Project / Version -----------------------------------------------

class NewProjectDialog(BaseDialog):
    def __init__(self, app):
        super().__init__(app, "New Project", "700x620")
        self.app = app
        self.name_var = tk.StringVar()
        self.template_var = tk.StringVar()
        self.dest_var = tk.StringVar()
        self.visibility_var = tk.StringVar(value="private")
        self.account_var = tk.StringVar(value="")
        self.create_repo_var = tk.BooleanVar(value=True)
        self.placeholder_vars: dict[str, tk.StringVar] = {}
        self._ph_frame: Optional[ttk.Frame] = None

        row = 0
        ttk.Label(self.body, text="Name:").grid(row=row, column=0, sticky="w", pady=4)
        name_entry = ttk.Entry(self.body, textvariable=self.name_var, width=50)
        name_entry.grid(row=row, column=1, columnspan=3, sticky="we", padx=(8, 4))
        name_entry.bind("<FocusOut>", self._suggest_dest)
        row += 1

        templates = [t["name"] for t in app.memory.list_templates()]
        ttk.Label(self.body, text="Template:").grid(row=row, column=0, sticky="w", pady=4)
        cb = ttk.Combobox(self.body, textvariable=self.template_var,
                           values=templates, state="readonly", width=48)
        cb.grid(row=row, column=1, columnspan=2, sticky="we", padx=(8, 4))
        cb.bind("<<ComboboxSelected>>", self._rebuild_placeholders)
        ttk.Button(self.body, text="Preview…", command=self._preview_template
                   ).grid(row=row, column=3, sticky="w")
        if templates: self.template_var.set(templates[0])
        row += 1

        ttk.Label(self.body, text="Destination:").grid(row=row, column=0,
                                                        sticky="w", pady=4)
        ttk.Entry(self.body, textvariable=self.dest_var, width=50
                  ).grid(row=row, column=1, columnspan=2, sticky="we", padx=(8, 4))
        ttk.Button(self.body, text="Browse…", command=self._browse_dest
                   ).grid(row=row, column=3, sticky="w")
        row += 1

        ttk.Label(self.body, text="Placeholders:").grid(row=row, column=0,
                                                         sticky="nw", pady=4)
        self._ph_frame = ttk.Frame(self.body)
        self._ph_frame.grid(row=row, column=1, columnspan=3, sticky="we",
                              padx=(8, 4))
        row += 1

        ttk.Label(self.body, text="Visibility:").grid(row=row, column=0,
                                                        sticky="w", pady=4)
        vis = ttk.Frame(self.body)
        vis.grid(row=row, column=1, columnspan=3, sticky="w", padx=(8, 4))
        ttk.Radiobutton(vis, text="🔒 Private", value="private",
                        variable=self.visibility_var).pack(side=tk.LEFT)
        ttk.Radiobutton(vis, text="🌐 Public", value="public",
                        variable=self.visibility_var).pack(side=tk.LEFT, padx=(12, 0))
        row += 1

        ttk.Label(self.body, text="GitHub account:").grid(row=row, column=0,
                                                            sticky="w", pady=4)
        accounts = [a["username"] for a in app.memory.list_accounts()]
        ttk.Combobox(self.body, textvariable=self.account_var, values=accounts,
                     state="readonly", width=48
                     ).grid(row=row, column=1, columnspan=3, sticky="we",
                              padx=(8, 4))
        if accounts: self.account_var.set(accounts[0])
        else: self.create_repo_var.set(False)
        row += 1

        ttk.Checkbutton(self.body,
                        text="Create GitHub repository and push now",
                        variable=self.create_repo_var,
                        state=tk.NORMAL if accounts else tk.DISABLED
                        ).grid(row=row, column=1, columnspan=3, sticky="w",
                                padx=(8, 4))
        row += 1

        if not accounts:
            ttk.Label(self.body, foreground=COLORS["error"], wraplength=600,
                      text="⚠ No GitHub account. Add one via GitHub → Add Account."
                      ).grid(row=row, column=0, columnspan=4, sticky="w",
                              pady=(6, 0))
            row += 1

        btns = ttk.Frame(self.body)
        btns.grid(row=row, column=0, columnspan=4, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Create", style="Primary.TButton",
                   command=self._create).pack(side=tk.RIGHT, padx=(0, 6))

        self._rebuild_placeholders()

    def _suggest_dest(self, _e=None):
        if self.dest_var.get().strip(): return
        name = self.name_var.get().strip()
        if not name: return
        base = self.app.memory.get_setting("default_projects_path")
        self.dest_var.set(str(Path(base).expanduser() / name))

    def _browse_dest(self):
        p = filedialog.askdirectory(title="Pick parent folder", parent=self)
        if p:
            self.dest_var.set(str(Path(p) /
                                    (self.name_var.get().strip() or "project")))

    def _preview_template(self):
        name = self.template_var.get()
        if not name: return
        try:
            data = self.app.scaffolder.preview_template(name)
        except Exception as e:
            messagebox.showerror("Preview failed", str(e), parent=self)
            return
        TemplatePreviewDialog(self.app, name, data, parent=self)

    def _rebuild_placeholders(self, _e=None):
        if not self._ph_frame: return
        for w in self._ph_frame.winfo_children(): w.destroy()
        self.placeholder_vars.clear()
        tpl = self.app.memory.get_template(self.template_var.get())
        if not tpl: return
        for i, key in enumerate(tpl.get("placeholders", {}).keys()):
            var = tk.StringVar()
            if key == "project_name":
                var.set(self.name_var.get())
                self.name_var.trace_add(
                    "write",
                    lambda *_a, v=var: v.set(self.name_var.get()))
            self.placeholder_vars[key] = var
            ttk.Label(self._ph_frame, text=f"{key}:"
                      ).grid(row=i, column=0, sticky="w", pady=2)
            ttk.Entry(self._ph_frame, textvariable=var, width=40
                      ).grid(row=i, column=1, sticky="we", padx=(6, 0))

    def _create(self):
        name = self.name_var.get().strip()
        template = self.template_var.get()
        dest = self.dest_var.get().strip()
        if not name or not template or not dest:
            messagebox.showwarning("Missing",
                                    "Name, template, and destination are required.",
                                    parent=self)
            return
        values = {k: v.get() for k, v in self.placeholder_vars.items()}
        values.setdefault("project_name", name)
        account = self.account_var.get().strip() or None
        create_repo = self.create_repo_var.get() and bool(account)
        visibility = self.visibility_var.get()
        self.destroy()
        app = self.app

        def work():
            app.scaffolder.create_project(
                name=name, template_name=template, dest_path=dest,
                placeholder_values=values, visibility=visibility,
                account=account, create_repo=create_repo,
                progress=app._set_status_ts)

        def done(_r=None):
            app.status_var.set(f"Project '{name}' created")
            app._refresh_all()
            app._select_project_by_name(name)

        app._run_async(f"Creating '{name}'…", work, on_done=done)


class NewVersionDialog(BaseDialog):
    def __init__(self, app, project_name):
        super().__init__(app, f"New Version in '{project_name}'", "420x220")
        self.app, self.project = app, project_name
        proj = app.memory.get_project(project_name) or {}
        versions = list(proj.get("versions", {}).keys())
        current = proj.get("current_version") or (versions[-1] if versions else "")

        self.label_var = tk.StringVar(value=self._next_label(versions))
        self.parent_var = tk.StringVar(value=current)

        ttk.Label(self.body, text="Version label:"
                  ).grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(self.body, textvariable=self.label_var, width=22
                  ).grid(row=0, column=1, sticky="we", padx=(8, 4))
        ttk.Label(self.body, text="Branch from:"
                  ).grid(row=1, column=0, sticky="w", pady=4)
        ttk.Combobox(self.body, textvariable=self.parent_var, values=versions,
                     state="readonly", width=20
                     ).grid(row=1, column=1, sticky="we", padx=(8, 4))
        self.buttons("Create", on_confirm=self._create)

    def _next_label(self, versions):
        n = len(versions) + 1
        while f"v{n}" in versions: n += 1
        return f"v{n}"

    def _create(self):
        label = self.label_var.get().strip()
        parent = self.parent_var.get() or None
        if not label:
            messagebox.showwarning("Missing", "Version label required.", parent=self)
            return
        proj = self.project
        self.destroy()

        def work():
            self.app.scaffolder.create_version(
                proj, label, parent_label=parent,
                progress=self.app._set_status_ts)

        def done(_r=None):
            self.app.status_var.set(f"Version '{label}' created")
            self.app._show_project(proj)

        self.app._run_async(f"Creating version {label}…", work, on_done=done)


# ----- Clone / Create Repo -------------------------------------------------

class CloneRepoDialog(BaseDialog):
    def __init__(self, app):
        super().__init__(app, "Clone from GitHub", "900x680")
        self.resizable(True, True)
        self.app = app
        self._all_repos = []
        self._selected_repo: Optional[dict] = None

        accounts = [a["username"] for a in app.memory.list_accounts()]
        self.account_var = tk.StringVar(value=accounts[0] if accounts else "")
        self.filter_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.dest_var = tk.StringVar()
        self.count_var = tk.StringVar(value="")
        self.filter_var.trace_add("write", lambda *_a: self._render())

        # Account + refresh
        top = ttk.Frame(self.body)
        top.grid(row=0, column=0, columnspan=4, sticky="we", pady=(0, 10))
        ttk.Label(top, text="Account:").pack(side=tk.LEFT)
        acc_cb = ttk.Combobox(top, textvariable=self.account_var,
                               values=accounts, state="readonly", width=22)
        acc_cb.pack(side=tk.LEFT, padx=(6, 12))
        acc_cb.bind("<<ComboboxSelected>>", lambda _e: self._load())
        ttk.Button(top, text="⟳ Refresh", command=self._load
                   ).pack(side=tk.LEFT)
        ttk.Label(top, textvariable=self.count_var,
                  foreground=COLORS["muted"]).pack(side=tk.LEFT, padx=(12, 0))

        if not accounts:
            ttk.Label(top, text="⚠ Add a GitHub account first",
                      foreground=COLORS["error"]).pack(side=tk.LEFT, padx=(12, 0))

        # Filter
        ttk.Label(self.body, text="Filter:").grid(row=1, column=0, sticky="w")
        ttk.Entry(self.body, textvariable=self.filter_var
                  ).grid(row=1, column=1, columnspan=3, sticky="we", pady=(0, 8))

        # Repos
        cols = ("name", "owner", "vis", "updated")
        self.tree = ttk.Treeview(self.body, columns=cols, show="headings",
                                   height=16)
        for c, h, w in [("name", "Repository", 340),
                         ("owner", "Owner", 160),
                         ("vis", "Visibility", 100),
                         ("updated", "Updated", 140)]:
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="w")
        self.tree.grid(row=2, column=0, columnspan=4, sticky="nsew")
        self.body.rowconfigure(2, weight=1)
        self.body.columnconfigure(3, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-Button-1>", lambda _e: self._clone())

        # Destination
        ttk.Label(self.body, text="Name:").grid(row=3, column=0, sticky="w",
                                                 pady=(10, 0))
        ttk.Entry(self.body, textvariable=self.name_var, width=28
                  ).grid(row=3, column=1, sticky="we", padx=(6, 10), pady=(10, 0))
        ttk.Label(self.body, text="Destination:").grid(row=3, column=2,
                                                         sticky="w", pady=(10, 0))
        dest_wrap = ttk.Frame(self.body)
        dest_wrap.grid(row=3, column=3, sticky="we", pady=(10, 0))
        ttk.Entry(dest_wrap, textvariable=self.dest_var
                  ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(dest_wrap, text="Browse…", command=self._browse
                   ).pack(side=tk.LEFT, padx=(6, 0))

        btns = ttk.Frame(self.body)
        btns.grid(row=4, column=0, columnspan=4, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        self.clone_btn = ttk.Button(btns, text="Clone Selected",
                                      style="Primary.TButton",
                                      command=self._clone, state=tk.DISABLED)
        self.clone_btn.pack(side=tk.RIGHT, padx=(0, 6))

        if accounts: self._load()

    def _load(self):
        acc = self.account_var.get().strip()
        if not acc: return
        self.count_var.set("Loading…")
        self.tree.delete(*self.tree.get_children())

        def work(): return self.app.scaffolder.list_repositories(acc)
        def done(repos=None):
            self._all_repos = repos or []
            self.count_var.set(f"{len(self._all_repos)} repos")
            self._render()
        def err(e):
            self.count_var.set("Error")
            messagebox.showerror("Load failed", str(e), parent=self)

        self.app._run_async("Loading repositories…", work,
                             on_done=done, on_error=err)

    def _render(self):
        self.tree.delete(*self.tree.get_children())
        q = self.filter_var.get().lower().strip()
        for r in self._all_repos:
            if q and q not in r["full_name"].lower(): continue
            self.tree.insert("", "end", iid=r["full_name"], values=(
                r["name"], r["owner"]["login"],
                "🔒" if r["private"] else "🌐",
                r.get("updated_at", "")[:10]))

    def _on_select(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            self.clone_btn.configure(state=tk.DISABLED)
            self._selected_repo = None
            return
        full = sel[0]
        repo = next((r for r in self._all_repos
                     if r["full_name"] == full), None)
        self._selected_repo = repo
        if repo:
            self.clone_btn.configure(state=tk.NORMAL)
            if not self.name_var.get().strip():
                self.name_var.set(repo["name"])
            if not self.dest_var.get().strip():
                base = self.app.memory.get_setting("default_projects_path")
                self.dest_var.set(str(Path(base).expanduser() / repo["name"]))

    def _browse(self):
        p = filedialog.askdirectory(title="Clone into parent folder", parent=self)
        if p:
            name = (self.name_var.get().strip()
                    or (self._selected_repo["name"]
                        if self._selected_repo else "repo"))
            self.dest_var.set(str(Path(p) / name))

    def _clone(self):
        repo = self._selected_repo
        if not repo: return
        name = self.name_var.get().strip()
        dest = self.dest_var.get().strip()
        if not name or not dest:
            messagebox.showwarning("Missing", "Name and destination required.",
                                    parent=self)
            return
        account = self.account_var.get().strip()
        url = repo["clone_url"]
        self.destroy()
        app = self.app

        def work():
            return app.scaffolder.clone_repository(
                url=url, dest_path=dest, project_name=name,
                account=account, import_all_branches=False,
                progress=app._set_status_ts)

        def done(_r=None):
            app.status_var.set(f"Cloned '{name}'")
            app._refresh_all()
            app._select_project_by_name(name)

        app._run_async(f"Cloning {repo['full_name']}…", work, on_done=done)


class CreateRepoDialog(BaseDialog):
    def __init__(self, app, project_name):
        super().__init__(app, f"Create Repository for '{project_name}'", "520x280")
        self.app, self.project = app, project_name
        proj = app.memory.get_project(project_name) or {}
        accounts = [a["username"] for a in app.memory.list_accounts()]
        self.account_var = tk.StringVar(value=accounts[0] if accounts else "")
        self.visibility_var = tk.StringVar(
            value=proj.get("visibility", "private"))

        ttk.Label(self.body, text="Account:").grid(row=0, column=0, sticky="w",
                                                     pady=4)
        ttk.Combobox(self.body, textvariable=self.account_var, values=accounts,
                     state="readonly", width=26
                     ).grid(row=0, column=1, sticky="we", padx=(8, 4))

        ttk.Label(self.body, text="Visibility:").grid(row=1, column=0, sticky="w",
                                                        pady=4)
        vis = ttk.Frame(self.body)
        vis.grid(row=1, column=1, sticky="w", padx=(8, 4))
        ttk.Radiobutton(vis, text="🔒 Private", value="private",
                        variable=self.visibility_var).pack(side=tk.LEFT)
        ttk.Radiobutton(vis, text="🌐 Public", value="public",
                        variable=self.visibility_var).pack(side=tk.LEFT,
                                                             padx=(12, 0))

        if not accounts:
            ttk.Label(self.body, foreground=COLORS["error"],
                      text="⚠ No GitHub account configured."
                      ).grid(row=2, column=0, columnspan=2, sticky="w",
                              pady=(8, 0))

        btns = ttk.Frame(self.body)
        btns.grid(row=3, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        create = ttk.Button(btns, text="Create", style="Primary.TButton",
                              command=self._create)
        create.pack(side=tk.RIGHT, padx=(0, 6))
        if not accounts: create.configure(state=tk.DISABLED)

    def _create(self):
        acc = self.account_var.get().strip()
        if not acc: return
        vis = self.visibility_var.get()
        proj = self.project
        self.destroy()

        def work():
            self.app.scaffolder.create_repository_for_project(
                project_name=proj, account=acc, visibility=vis,
                progress=self.app._set_status_ts)

        def done(_r=None):
            self.app.status_var.set("Repository created and pushed")
            self.app._refresh_all()
            self.app._show_project(proj)

        self.app._run_async("Creating repository…", work, on_done=done)


# ----- Accounts ------------------------------------------------------------

class AddAccountDialog(BaseDialog):
    def __init__(self, app):
        super().__init__(app, "Add GitHub Account", "540x300")
        self.app = app
        self.username_var = tk.StringVar()
        self.token_var = tk.StringVar()

        ttk.Label(self.body, text="Username:"
                  ).grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(self.body, textvariable=self.username_var, width=42
                  ).grid(row=0, column=1, sticky="we", padx=(8, 4))

        ttk.Label(self.body, text="Personal Access Token:"
                  ).grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(self.body, textvariable=self.token_var, width=42, show="•"
                  ).grid(row=1, column=1, sticky="we", padx=(8, 4))

        ttk.Label(self.body, foreground=COLORS["muted"], wraplength=480,
                  text="Create token at github.com/settings/tokens with scopes: "
                       "repo, read:user, read:org. Stored locally (chmod 0600)."
                  ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self.buttons("Verify & Add", on_confirm=self._add)

    def _add(self):
        u = self.username_var.get().strip()
        t = self.token_var.get().strip()
        if not u or not t:
            messagebox.showwarning("Missing", "Both fields are required.",
                                    parent=self)
            return
        self.app.status_var.set("Verifying token…")
        try:
            info = self.app.scaffolder.verify_account(u, t)
        except Exception as e:
            messagebox.showerror("Verification failed", str(e), parent=self)
            return
        self.app.scaffolder.set_token(u, t)
        self.app.memory.add_account(
            info["login"],
            orgs=info.get("orgs", []),
            github_id=info.get("id"),
        )
        self.app.status_var.set(f"Account '{info['login']}' added")
        self.destroy()


class ManageAccountsDialog(BaseDialog):
    def __init__(self, app):
        super().__init__(app, "Manage GitHub Accounts", "600x420")
        self.app = app
        self.tree = ttk.Treeview(self.body,
                                   columns=("username", "orgs", "token"),
                                   show="headings", height=10)
        for c, h, w in [("username", "Username", 160),
                         ("orgs", "Orgs", 260),
                         ("token", "Token", 100)]:
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill=tk.BOTH, expand=True)

        btns = ttk.Frame(self.body)
        btns.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(btns, text="Add Account…", command=self._add
                   ).pack(side=tk.LEFT)
        ttk.Button(btns, text="Replace Token…", command=self._replace
                   ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btns, text="Remove", command=self._remove
                   ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btns, text="Close", command=self.destroy).pack(side=tk.RIGHT)
        self._refresh()

    def _refresh(self):
        for row in self.tree.get_children(): self.tree.delete(row)
        for acc in self.app.memory.list_accounts():
            has = self.app.scaffolder.has_token(acc["username"])
            self.tree.insert("", "end", iid=acc["username"], values=(
                acc["username"], ", ".join(acc.get("orgs", [])) or "—",
                "✓ stored" if has else "✗ missing"))

    def _add(self):
        AddAccountDialog(self.app)
        self.after(500, self._refresh)

    def _replace(self):
        sel = self.tree.selection()
        if not sel: return
        dlg = TokenExpiredDialog(self.app, sel[0])
        self.wait_window(dlg)
        if dlg.new_token:
            self.app.scaffolder.set_token(sel[0], dlg.new_token)
            self._refresh()

    def _remove(self):
        sel = self.tree.selection()
        if not sel: return
        if not messagebox.askyesno("Remove",
                                    f"Remove '{sel[0]}' and its token?",
                                    parent=self):
            return
        self.app.memory.remove_account(sel[0])
        self.app.scaffolder.delete_token(sel[0])
        self._refresh()
        self.app._refresh_all()


# ----- Rename / Visibility / Delete ----------------------------------------

class RenameProjectDialog(BaseDialog):
    def __init__(self, app, old_name):
        super().__init__(app, f"Rename '{old_name}'", "480x220")
        self.app, self.old = app, old_name
        self.name_var = tk.StringVar(value=old_name)
        self.rename_remote_var = tk.BooleanVar(value=True)

        ttk.Label(self.body, text="New name:").grid(row=0, column=0,
                                                      sticky="w", pady=4)
        ttk.Entry(self.body, textvariable=self.name_var, width=36
                  ).grid(row=0, column=1, sticky="we", padx=(8, 4))
        ttk.Checkbutton(self.body, text="Also rename GitHub repository",
                        variable=self.rename_remote_var
                        ).grid(row=1, column=0, columnspan=2, sticky="w",
                                pady=(8, 0))
        self.buttons("Rename", on_confirm=self._rename)

    def _rename(self):
        new = self.name_var.get().strip()
        if not new or new == self.old: return
        old, rr = self.old, self.rename_remote_var.get()
        self.destroy()

        def work(): self.app.scaffolder.rename_project(old, new, rename_remote=rr)
        def done(_r=None):
            self.app.status_var.set(f"Renamed '{old}' → '{new}'")
            self.app._refresh_all()
            self.app._select_project_by_name(new)
        self.app._run_async("Renaming…", work, on_done=done)


class ChangeVisibilityDialog(BaseDialog):
    def __init__(self, app, project_name):
        super().__init__(app, "Change Visibility", "540x280")
        self.app, self.project = app, project_name
        proj = app.memory.get_project(project_name) or {}
        current = proj.get("visibility", "private")
        self.visibility_var = tk.StringVar(value=current)

        ttk.Label(self.body, text=f"Project: {project_name}",
                  font=FONT_BOLD).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(self.body,
                  text=f"Current: {'🔒 Private' if current == 'private' else '🌐 Public'}",
                  foreground=COLORS["muted"]
                  ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 10))

        vis = ttk.Frame(self.body)
        vis.grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(vis, text="🔒 Private", value="private",
                        variable=self.visibility_var).pack(side=tk.LEFT)
        ttk.Radiobutton(vis, text="🌐 Public", value="public",
                        variable=self.visibility_var).pack(side=tk.LEFT,
                                                             padx=(12, 0))

        ttk.Label(self.body, foreground=COLORS["error"], wraplength=500,
                  text="⚠ Making a repo public is hard to undo. Forks and clones "
                       "already exist cannot be recalled."
                  ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self.buttons("Change", on_confirm=self._apply)

    def _apply(self):
        new = self.visibility_var.get()
        proj = self.project
        if new == "public" and self.app.memory.get_setting("confirm_make_public"):
            if not messagebox.askyesno("Make public?",
                                        "Expose this repository publicly?",
                                        parent=self):
                return
        self.destroy()

        def work(): self.app.scaffolder.change_visibility(proj, new)
        def done(_r=None):
            self.app.status_var.set(f"Visibility → {new}")
            self.app._show_project(proj)
            self.app._refresh_projects()
        self.app._run_async("Updating visibility…", work, on_done=done)


class DeleteProjectDialog(BaseDialog):
    def __init__(self, app, project_name):
        super().__init__(app, f"Delete '{project_name}'", "540x300")
        self.app, self.project = app, project_name
        self.remove_remote_var = tk.BooleanVar(value=False)

        ttk.Label(self.body, text=f"Delete project '{project_name}'?",
                  font=FONT_BOLD).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(self.body, foreground=COLORS["muted"], wraplength=500,
                  text="The local folder will be moved to ~/.scaffolder/trash/ "
                       "and can be recovered for a limited time."
                  ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 10))

        ttk.Checkbutton(self.body,
                        text="Also delete the GitHub repository (PERMANENT)",
                        variable=self.remove_remote_var
                        ).grid(row=2, column=0, columnspan=2, sticky="w")

        self.buttons("Delete", on_confirm=self._delete)

    def _delete(self):
        proj = self.project
        rr = self.remove_remote_var.get()
        if not messagebox.askyesno("Confirm",
                                    f"Delete project '{proj}'?"
                                    + ("\n\nThe GitHub repo will be deleted!"
                                        if rr else ""), parent=self):
            return
        self.destroy()

        def work(): self.app.scaffolder.delete_project(proj, remove_remote=rr)
        def done(_r=None):
            self.app.status_var.set(f"Project '{proj}' deleted")
            self.app._current_project = None
            self.app._refresh_all()
            for v in (self.app.detail_project_var,
                       self.app.detail_visibility_var,
                       self.app.detail_repo_var):
                v.set("")
            self.app.changes_banner.pack_forget()
        self.app._run_async("Deleting…", work, on_done=done)


class DeleteVersionDialog(BaseDialog):
    def __init__(self, app, project_name, label):
        super().__init__(app, f"Delete version '{label}'", "560x340")
        self.app, self.project, self.label = app, project_name, label
        proj = app.memory.get_project(project_name) or {}
        ver = proj.get("versions", {}).get(label, {})
        children = [k for k, v in proj.get("versions", {}).items()
                     if v.get("parent") == label]
        self.remove_remote_var = tk.BooleanVar(value=True)

        ttk.Label(self.body, text=f"Delete version '{label}'?",
                  font=FONT_BOLD).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(self.body, text=f"Branch: {ver.get('branch', '')}",
                  foreground=COLORS["muted"]
                  ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 6))

        if children:
            ttk.Label(self.body, foreground=COLORS["warning"], wraplength=500,
                      text=f"⚠ {len(children)} child version(s) will be re-parented: "
                           f"{', '.join(children)}"
                      ).grid(row=2, column=0, columnspan=2, sticky="w",
                              pady=(0, 6))

        if proj.get("current_version") == label:
            ttk.Label(self.body, foreground=COLORS["warning"], wraplength=500,
                      text="⚠ This is the current version. Will switch first."
                      ).grid(row=3, column=0, columnspan=2, sticky="w",
                              pady=(0, 6))

        ttk.Checkbutton(self.body, text="Also delete branch on GitHub",
                        variable=self.remove_remote_var
                        ).grid(row=4, column=0, columnspan=2, sticky="w",
                                pady=(8, 0))
        self.buttons("Delete", on_confirm=self._delete)

    def _delete(self):
        proj, lbl = self.project, self.label
        rr = self.remove_remote_var.get()
        self.destroy()

        def work():
            self.app.scaffolder.delete_version(proj, lbl, remove_remote=rr,
                                                progress=self.app._set_status_ts)

        def done(_r=None):
            self.app.status_var.set(f"Version '{lbl}' deleted")
            self.app._show_project(proj)
            self.app._refresh_projects()

        self.app._run_async("Deleting version…", work, on_done=done)


class ForcePushDialog(BaseDialog):
    def __init__(self, app, project_name, version_label):
        super().__init__(app, "Force Push (advanced)", "540x320")
        self.app, self.project, self.version = app, project_name, version_label
        self.confirm_var = tk.StringVar()

        ttk.Label(self.body, text="⚠ This overwrites the remote branch.",
                  font=FONT_BOLD, foreground=COLORS["error"]
                  ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(self.body, wraplength=500, foreground=COLORS["muted"],
                  text="Commits on the remote not in your local branch will be "
                       "discarded."
                  ).grid(row=1, column=0, columnspan=2, sticky="w",
                          pady=(6, 12))
        ttk.Label(self.body, text=f"Branch: {version_label}",
                  foreground=COLORS["muted"]
                  ).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(self.body, text="Type FORCE to confirm:"
                  ).grid(row=3, column=0, columnspan=2, sticky="w",
                          pady=(10, 2))
        ttk.Entry(self.body, textvariable=self.confirm_var, width=20
                  ).grid(row=4, column=0, sticky="w")
        self.buttons("Force Push", on_confirm=self._go)

    def _go(self):
        if self.confirm_var.get().strip() != "FORCE":
            messagebox.showwarning("Not confirmed", "Type FORCE exactly.",
                                    parent=self)
            return
        proj, label = self.project, self.version
        self.destroy()

        def work(): self.app.scaffolder.force_push(proj, label)
        def done(_r=None):
            self.app.status_var.set(f"Force-pushed {label}")
            self.app._show_project(proj)
        self.app._run_async("Force pushing…", work, on_done=done)


class SettingsDialog(BaseDialog):
    def __init__(self, app):
        super().__init__(app, "Settings", "660x520")
        self.app = app

        self.visibility_var = tk.StringVar(
            value=app.memory.get_setting("default_visibility"))
        self.owner_var = tk.StringVar(
            value=app.memory.get_setting("default_owner") or "")
        self.branch_var = tk.StringVar(
            value=app.memory.get_setting("default_branch") or "main")
        self.path_var = tk.StringVar(
            value=app.memory.get_setting("default_projects_path"))
        self.auto_repo_var = tk.BooleanVar(
            value=app.memory.get_setting("auto_create_repo"))
        self.auto_push_var = tk.BooleanVar(
            value=app.memory.get_setting("auto_push_after_commit"))
        self.confirm_public_var = tk.BooleanVar(
            value=app.memory.get_setting("confirm_make_public"))

        row = 0
        ttk.Label(self.body, text="Default visibility:").grid(row=row, column=0,
                                                                sticky="w", pady=4)
        vis = ttk.Frame(self.body)
        vis.grid(row=row, column=1, sticky="w", padx=(8, 4))
        ttk.Radiobutton(vis, text="🔒 Private", value="private",
                        variable=self.visibility_var).pack(side=tk.LEFT)
        ttk.Radiobutton(vis, text="🌐 Public", value="public",
                        variable=self.visibility_var).pack(side=tk.LEFT,
                                                             padx=(12, 0))
        row += 1

        ttk.Label(self.body, text="Default owner:").grid(row=row, column=0,
                                                           sticky="w", pady=4)
        owners = [""] + [a["username"] for a in app.memory.list_accounts()]
        for a in app.memory.list_accounts():
            owners.extend(a.get("orgs", []))
        ttk.Combobox(self.body, textvariable=self.owner_var, values=owners,
                     width=40
                     ).grid(row=row, column=1, sticky="we", padx=(8, 4))
        row += 1

        ttk.Label(self.body, text="Default branch:").grid(row=row, column=0,
                                                            sticky="w", pady=4)
        ttk.Entry(self.body, textvariable=self.branch_var, width=42
                  ).grid(row=row, column=1, sticky="we", padx=(8, 4))
        row += 1

        ttk.Label(self.body, text="Default projects path:"
                  ).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(self.body, textvariable=self.path_var, width=42
                  ).grid(row=row, column=1, sticky="we", padx=(8, 4))
        ttk.Button(self.body, text="Browse…", command=self._browse_path
                   ).grid(row=row, column=2, sticky="w")
        row += 1

        ttk.Checkbutton(self.body, text="Auto-create repo on project creation",
                        variable=self.auto_repo_var
                        ).grid(row=row, column=1, sticky="w", padx=(8, 4),
                                pady=(10, 0))
        row += 1
        ttk.Checkbutton(self.body, text="Auto-push after each commit",
                        variable=self.auto_push_var
                        ).grid(row=row, column=1, sticky="w", padx=(8, 4))
        row += 1
        ttk.Checkbutton(self.body, text="Confirm before making a repo public",
                        variable=self.confirm_public_var
                        ).grid(row=row, column=1, sticky="w", padx=(8, 4))
        row += 1
        ttk.Button(self.body, text="Open Error Log…",
                   command=self.app._open_error_log
                   ).grid(row=row, column=1, sticky="w", padx=(8, 4),
                           pady=(14, 0))
        row += 1
        self.buttons("Save", on_confirm=self._save)

    def _browse_path(self):
        p = filedialog.askdirectory(title="Default projects folder", parent=self)
        if p: self.path_var.set(p)

    def _save(self):
        self.app.memory.set_settings({
            "default_visibility": self.visibility_var.get(),
            "default_owner": self.owner_var.get() or None,
            "default_branch": self.branch_var.get().strip() or "main",
            "default_projects_path": self.path_var.get().strip(),
            "auto_create_repo": self.auto_repo_var.get(),
            "auto_push_after_commit": self.auto_push_var.get(),
            "confirm_make_public": self.confirm_public_var.get(),
        })
        self.app.status_var.set("Settings saved")
        self.destroy()


class ErrorLogWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Error Log")
        self.geometry("820x480")
        self.transient(app)
        self.configure(bg=COLORS["bg"])

        top = ttk.Frame(self, padding=12)
        top.pack(fill=tk.BOTH, expand=True)
        self.text = tk.Text(top, wrap=tk.WORD, height=20,
                             bg=COLORS["card"], relief="flat",
                             highlightthickness=1,
                             highlightbackground=COLORS["border"],
                             font=FONT_MONO)
        self.text.pack(fill=tk.BOTH, expand=True)
        self.text.config(state=tk.DISABLED)

        btns = ttk.Frame(top)
        btns.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btns, text="Refresh", command=self._load).pack(side=tk.LEFT)
        ttk.Button(btns, text="Copy All", command=self._copy
                   ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btns, text="Clear", command=self._clear
                   ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btns, text="Close", command=self.destroy).pack(side=tk.RIGHT)
        self._load()

    def _load(self):
        errors = self.app.memory.get_errors()
        self.text.config(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        if not errors:
            self.text.insert("1.0", "(no errors)")
        else:
            for e in reversed(errors):
                self.text.insert(tk.END, f"[{e['at']}] {e['context']}\n")
                self.text.insert(tk.END, f"    {e['message']}\n\n")
        self.text.config(state=tk.DISABLED)

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.text.get("1.0", tk.END))

    def _clear(self):
        if messagebox.askyesno("Clear log", "Delete all recorded errors?",
                                parent=self):
            self.app.memory.clear_errors()
            self._load()


# ============================================================================
# Entry point
# ============================================================================

if __name__ == "__main__":
    app = ScaffolderApp()
    app.mainloop()

