"""Background task execution for the Qt UI (S18).

The old app used a single global ``_busy`` flag: one slow push froze every
control. Here each task has its own *scope* (``"project-action"``,
``"list-load"``, ``"background"``). Controls are disabled per scope, and
read-only background refreshes never block user actions.
"""

from __future__ import annotations

import itertools
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..errors import ScaffolderError

_task_ids = itertools.count(1)


class TaskSignals(QObject):
    finished = Signal(int, object)   # task id, result
    failed = Signal(int, object)     # task id, exception
    progress = Signal(int, str)      # task id, message


_shared_signals: TaskSignals | None = None


def shared_signals() -> TaskSignals:
    """A process-lifetime signal hub so worker threads never emit into a
    deleted Qt object (Qt emits from the worker thread)."""
    global _shared_signals
    if _shared_signals is None:
        _shared_signals = TaskSignals()
    return _shared_signals


class _Task(QRunnable):
    def __init__(self, task_id: int, work: Callable[..., Any],
                 signals: TaskSignals) -> None:
        super().__init__()
        self.task_id = task_id
        self._work = work
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D401 - QRunnable API
        try:
            result = self._work()
            self._signals.finished.emit(self.task_id, result)
        except BaseException as exc:  # noqa: BLE001 - surfaced on the UI thread
            exc._traceback = traceback.format_exc()  # type: ignore[attr-defined]
            try:
                self._signals.failed.emit(self.task_id, exc)
            except RuntimeError:
                # The application is shutting down; nothing to report to.
                pass


@dataclass
class _Handle:
    task_id: int
    scope: str
    label: str
    on_done: Callable[[Any], None] | None = None
    on_error: Callable[[BaseException], None] | None = None
    on_progress: Callable[[str], None] | None = None
    on_finally: Callable[[], None] | None = None


@dataclass
class _Scope:
    label: str = ""
    count: int = 0
    handles: dict = field(default_factory=dict)


class TaskRunner(QObject):
    """Runs work off the UI thread and dispatches results back on it."""

    scope_changed = Signal(str, bool, str)  # scope, busy, label
    task_failed = Signal(object)            # ScaffolderError or Exception

    SCOPES = ("action", "list", "background")

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)
        self._signals = shared_signals()
        self._signals.finished.connect(self._on_finished)
        self._signals.failed.connect(self._on_failed)
        self._signals.progress.connect(self._on_progress)
        self._handles: dict[int, _Handle] = {}
        self._scopes: dict[str, _Scope] = {
            scope: _Scope() for scope in self.SCOPES}

    # ------------------------------------------------------------------ public

    def busy(self, scope: str = "action") -> bool:
        return self._scopes.get(scope, _Scope()).count > 0

    def any_busy(self) -> bool:
        return any(scope.count for scope in self._scopes.values())

    def run(self, work: Callable[..., Any], *, scope: str = "action",
            label: str = "", on_done: Callable[[Any], None] | None = None,
            on_error: Callable[[BaseException], None] | None = None,
            on_progress: Callable[[str], None] | None = None,
            on_finally: Callable[[], None] | None = None) -> int | None:
        """Schedule ``work``.

        ``work`` is always called with a single ``progress(message)`` callable,
        so streaming operations can report intermediate lines.
        """
        scope_state = self._scopes.setdefault(scope, _Scope())
        if scope == "action" and scope_state.count > 0:
            return None  # caller decides how to communicate the refusal
        task_id = next(_task_ids)
        handle = _Handle(task_id, scope, label or "Working\u2026", on_done,
                         on_error, on_progress, on_finally)
        self._handles[task_id] = handle
        scope_state.count += 1
        scope_state.label = handle.label
        self.scope_changed.emit(scope, True, handle.label)

        def _emit_progress(message: str) -> None:
            self._signals.progress.emit(task_id, str(message))

        task = _Task(task_id, lambda: work(_emit_progress), self._signals)
        self._pool.start(task)
        return task_id

    def cancel_all(self) -> None:
        self._pool.clear()

    def wait_for(self, timeout_ms: int = 15000) -> None:
        self._pool.waitForDone(timeout_ms)

    # ----------------------------------------------------------------- slots

    def _finish(self, task_id: int) -> _Handle | None:
        handle = self._handles.pop(task_id, None)
        if handle is None:
            return None
        scope_state = self._scopes.get(handle.scope)
        if scope_state:
            scope_state.count = max(0, scope_state.count - 1)
            if scope_state.count == 0:
                self.scope_changed.emit(handle.scope, False, "")
            else:
                self.scope_changed.emit(handle.scope, True, handle.label)
        return handle

    def _on_finished(self, task_id: int, result: object) -> None:
        handle = self._finish(task_id)
        if handle is None:
            return
        try:
            if handle.on_done:
                handle.on_done(result)
        finally:
            if handle.on_finally:
                handle.on_finally()

    def _on_failed(self, task_id: int, exc: BaseException) -> None:
        handle = self._finish(task_id)
        if handle is None:
            return
        try:
            if handle.on_error:
                handle.on_error(exc)
            else:
                self.task_failed.emit(exc)
        finally:
            if handle.on_finally:
                handle.on_finally()

    def _on_progress(self, task_id: int, message: str) -> None:
        handle = self._handles.get(task_id)
        if handle and handle.on_progress:
            handle.on_progress(message)


def error_title(exc: BaseException) -> str:
    if isinstance(exc, ScaffolderError):
        return exc.title
    return "Unexpected error"


def error_message(exc: BaseException) -> str:
    if isinstance(exc, ScaffolderError):
        return str(exc)
    detail = getattr(exc, "_traceback", "")
    return f"{type(exc).__name__}: {exc}" + (f"\n\n{detail}" if detail else "")


def error_hint(exc: BaseException) -> str:
    return getattr(exc, "hint", "") if isinstance(exc, ScaffolderError) else ""


def spawn_detached(fn: Callable[[], None]) -> threading.Thread:
    thread = threading.Thread(target=fn, daemon=True)
    thread.start()
    return thread
