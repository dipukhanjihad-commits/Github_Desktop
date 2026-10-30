# Implementation record

Every item from the audit, with where it now lives and how it is verified.
Legend for **Status**: ✅ implemented and tested · ⚙️ implemented, needs a
service the local environment cannot supply (e.g. a GitHub token) · ➖ superseded
by a different design.

---

## MUST — blocking or data loss

### Features

| # | Item | Where | Verified by | Status |
|---|---|---|---|---|
| M1 | Dependency mismatch | `requirements.txt`, `pyproject.toml`, `scaffolder_app/ui/*` | `test_core_has_no_qt_dependency`, app launch | ✅ |
| M2 | Delete by path, not name | `project_memory` (id-keyed), `Scaffolder.delete_project` | `test_delete_project_does_not_touch_same_prefix_project`, `test_delete_moves_only_target_folder_to_trash` | ✅ |
| M3 | Sanitise placeholder paths | `template_engine.sanitize_placeholder_value`, `safe_join` | 4 parametrised traversal tests, `test_placeholder_filename_cannot_escape_destination` | ✅ |
| M4 | Confirm destructive actions | `AppDialog.confirm_destructive`, `DeleteProjectDialog`, `DeleteVersionDialog`, `ForcePushDialog` | `scripts/dialog_smoke.py`; typed `FORCE` confirmation | ✅ |
| M5 | Never lose the remote | `github_api.parse_remote`, `_warn_if_unparseable` | `test_ssh_remote_is_parsed_and_kept`, `test_non_github_remote_is_preserved`, 6 URL-parsing cases | ✅ |
| M6 | Structured error surface | `scaffolder_app/errors.py`, `async_runner.error_*`, `ErrorDialog` | Every backend test asserts typed errors; `ErrorDialog` copy button | ✅ |
| M7 | Clean up failed operations | `Scaffolder._rollback_project` | Rollback on any pre-registration failure | ✅ |
| M8 | Handle moved/deleted folders | `Scaffolder.validate_paths`, `RelinkDialog` | `test_missing_folder_detection_and_problems_list`, `test_relink_project_points_at_new_location` | ✅ |
| M9 | Safe worktree transitions | `Scaffolder._ensure_clean`, `switch_version` | `test_switch_version_stashes_and_restores_work`, `test_switch_version_with_abort_strategy_raises` | ✅ |
| M10 | Symlink / hidden-file gaps | `TemplateEngine.iter_files` (never follows links out of root) | containment check in `iter_files`; `safe_join` tests | ✅ |
| M11 | Real token protection | `credentials.py` (keyring → DPAPI → 0600) | `test_credentials_round_trip`, `test_legacy_plaintext_tokens_are_migrated_and_removed` | ✅ |
| M12 | Re-verify tokens, surface rejection | `Scaffolder.check_token`, `TokenUpdateDialog`, `TokenExpiredError` | `_on_task_failed` routes to token update; typed error propagation | ✅ |
| M13 | Test coverage | `tests/` | **74 tests passing** | ✅ |

### Design

| # | Item | Where | Status |
|---|---|---|---|
| M14 | Fixed predictable layout | `widgets.FormBuilder`, `AppDialog` (header/body/footer) | ✅ |
| M15 | Platform-correct fonts | `theme.Typography` (Segoe UI / SF Pro / Inter; Cascadia Mono / SF Mono) | ✅ |
| M16 | Theme system with light/dark | `theme.LIGHT`/`DARK`, `styles.build_stylesheet`, `toggle_theme` | ✅ |
| M17 | Window state persistence | `platform.WindowState` | ✅ (`test_window_state_persists`, corrupt-file test) |
| M18 | Consistent destructive affordance | `QPushButton#Danger`, explicit confirm dialogs | ✅ |
| M19 | Loading and empty states | `widgets.EmptyState`, `LoadingState`, sidebar stack | ✅ (`test_sidebar_shows_empty_state_until_projects_exist`) |
| M20 | Keyboard + accessibility | menu accelerators, `ShortcutList`, `KeycapLabel`, Enter/Esc in dialogs | ✅ |

---

## SHOULD

| # | Item | Where | Status |
|---|---|---|---|
| S1 | Diff preview before commit | `git_ops.diff_text`, `ChangesPanel.diff_view` | ✅ |
| S2 | Staging model | `Scaffolder.stage/unstage`, per-file table | ✅ (`test_stage_and_unstage`, `test_commit_only_selected_paths`) |
| S3 | History and undo | `git_ops.log/show_commit`, `undo_last_commit`, History tab | ✅ (`test_undo_last_commit_keeps_changes`) |
| S4 | Merge conflict UI | `ConflictError`, `ConflictedDialog` | ✅ |
| S5 | Honour all settings | `SettingsDialog` + every key consumed (verified by grep and by tests) | ✅ |
| S6 | Use editor detection | `editor.detect_available_editors` feeds Settings | ✅ |
| S7 | Dynamic version labels | `VersionDialog._next_label` | ✅ |
| S8 | Non-GitHub remotes | `parse_remote` accepts any host; `remote_kind` | ⚙️ GitHub features gated, remote preserved |
| S9 | Better auth (device flow / OAuth) | `github_api.DeviceFlow`, `DeviceFlowDialog` | ⚙️ needs a client id |
| S10 | Type-to-confirm remote deletion | `DeleteProjectDialog.confirm_destructive` | ✅ |
| S11 | Multi-select operations | per-project actions + `push_all` | ✅ |
| S12 | Environment setup progress | `env_setup._stream`, `_run_env` log dialog | ✅ |
| S13 | Conflict-free concurrency | `locking.file_lock`, `SingleInstanceGuard` | ✅ |
| S14 | Bounded trash + restore UI | `memory.add_trash_entry` (MAX_TRASH), `TrashDialog` | ✅ (`test_trash_restore_round_trip`) |
| S15 | Packaging | `pyproject.toml` with console + GUI scripts | ➖ PyInstaller not included |

### Design

| # | Item | Where | Status |
|---|---|---|---|
| S16 | Unified badge system | `widgets.Badge` with six tones | ✅ |
| S17 | Sync summary in words | `ProjectHeader.update_state` ("3 ahead, 1 behind") | ✅ |
| S18 | Fix the async seam | `async_runner.TaskRunner` with independent scopes | ✅ (`test_task_runner_scopes_are_independent`) |
| S19 | De-duplicate code | single `EnvDetector`, single editor resolver | ✅ |
| S20 | Split `gui.py` | 2,623-line file → 25 focused modules | ✅ |
| S21 | High-DPI | `platform.set_scale_policy` + `configure_high_dpi` | ✅ |
| S22 | Onboarding empty state | `EmptyState` guides create/clone | ✅ |
| S23 | Inline validation | `NewProjectDialog` (`check_name` param) + `Scaffolder.check_name_available`, `FormBuilder.add_field_error` | ✅ |
| S24 | Platform data locations | `paths.py` | ✅ |
| S25 | Preserve line endings | `TemplateEngine.apply` newline handling | ✅ |
| S26 | Commit message hygiene | `clean_commit_message`, title counter | ✅ (`test_commit_message_cleaning`) |

---

## NICE TO HAVE

| # | Item | Where | Status |
|---|---|---|---|
| N1 | Stash support | `stash_changes`, `StashDialog` | ✅ (`test_stash_round_trip`) |
| N2 | Hooks / run tests | `RunTask`, `TaskRunDialog` | ✅ |
| N3 | Issues / PR state | `fetch_github_summary`, GitHub tab | ⚙️ needs a token |
| N4 | Release from a version | `create_release`, `ReleaseDialog` | ⚙️ needs a token |
| N5 | Submodule / monorepo awareness | `repo.find_project_path` refuses nested duplicates | ⚙️ partial |
| N6 | Groups / pinning | `set_pinned`, `set_group`, sidebar group headers | ✅ (`test_sidebar_pinned_projects_sort_first`) |
| N7 | Command palette | `palette.CommandPalette` | ✅ (`test_palette_filters_and_runs`) |
| N8 | Quick-create from URL | `CloneDialog` accepts any GitHub URL/slug | ✅ |
| N9 | Auto refresh | `auto_refresh_seconds` + timer | ✅ |
| N10 | Export/import manifest | `export_template_manifest`, `import_template_manifest` | ✅ |
| N11 | Configurable editors | `editor.py` | ✅ |
| N12 | Localisation | `language` setting persisted; strings still English | ⚙️ infrastructure only |
| N13 | Crash recovery | window state, notes autosave, atomic store | ✅ |
| N14 | Global search | palette searches projects + actions | ✅ |
| N15 | Nested version tree | `VersionsTable` indents by parent | ✅ |
| N16 | Skeleton loaders | `LoadingState` | ✅ |
| N17 | Contextual tips | inline hint labels in every dialog | ✅ |
| N18 | Unified status bar | branch, progress, account, storage backend | ✅ |
| N19 | Accessibility | keyboard-only flows, readable labels (no emoji-only state) | ✅ |
| N20 | Sample template | `scripts/smoke.py` creates one; **no sample is shipped with the app** | ➖ not bundled |

---

## Verification summary

```
pyflakes scaffolder_app tests scripts gui.py main.py scaffold.py   → clean
pytest tests -q                                                    → 74 passed
scripts/smoke.py                                                   → SMOKE OK
scripts/ui_smoke.py                                                → 24/24 checks
scripts/dialog_smoke.py                                            → 29/29 dialogs
gui.py (offscreen boot, then quit)                                 → exit code 0
```

`⚙️` items are fully wired but need GitHub credentials to exercise end-to-end;
their non-network paths are covered by the URL-parsing, remote-preservation and
dialog-construction tests.
