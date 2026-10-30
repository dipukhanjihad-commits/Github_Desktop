"""Template scanning and instantiation.

Safety guarantees
-----------------
* Placeholder values are sanitised before they touch a path, and the resolved
  target is re-checked to be inside the destination (M3).
* Symlinks are never followed out of the template root (M10).
* File contents keep the source's line endings when the platform matches, so
  templates authored with CRLF on Windows are not rewritten (S25).
* Binary files are copied byte-for-byte; only UTF-8 text is substituted.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Iterable, Iterator

from .errors import ScaffolderError, ValidationError

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_TEXT_SUFFIXES = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json",
    ".md", ".txt", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".html", ".css",
    ".scss", ".sh", ".bat", ".ps1", ".cmd", ".go", ".rs", ".c", ".h", ".cpp",
    ".hpp", ".java", ".kt", ".rb", ".php", ".sql", ".env", ".gitignore",
    ".editorconfig", ".dockerfile", ".svg", ".xml", ".lock", ".properties",
}
_TEXT_NAMES = {"Dockerfile", "Makefile", "LICENSE", "README", "Procfile",
               ".gitignore", ".gitattributes", ".editorconfig", ".npmrc",
               ".python-version", ".nvmrc", ".prettierrc", ".eslintrc"}
_MAX_TEXT_BYTES = 5 * 1024 * 1024

_ILLEGAL_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class IgnoreMatcher:
    """Gitignore-flavoured matching: exact names, globs and ``dir/`` prefixes."""

    def __init__(self, patterns: Iterable[str]) -> None:
        self.patterns = [p.strip() for p in patterns if p and p.strip()]
        self._absolute = [p.rstrip("/") for p in self.patterns
                          if "/" in p.strip("/") and not p.startswith("*")]
        self._globs = [p for p in self.patterns if any(c in p for c in "*?[")]
        self._plain = {p.rstrip("/") for p in self.patterns
                       if not any(c in p for c in "*?[") and "/" not in p}

    def matches(self, rel_parts: tuple[str, ...]) -> bool:
        if not self.patterns:
            return False
        rel = "/".join(rel_parts)
        for part in rel_parts:
            if part in self._plain:
                return True
        for absolute in self._absolute:
            if rel == absolute or rel.startswith(absolute + "/"):
                return True
        for pattern in self._globs:
            from fnmatch import fnmatch

            if fnmatch(rel, pattern) or any(fnmatch(part, pattern)
                                            for part in rel_parts):
                return True
        return False


def sanitize_placeholder_value(value: str, key: str = "value") -> str:
    """Make a placeholder value safe to embed in a file or folder name (M3)."""
    text = str(value)
    if ".." in text.split("/") or ".." in text.split("\\"):
        raise ValidationError(
            f"Placeholder '{key}' must not contain '..': {value!r}")
    cleaned = _ILLEGAL_PATH_CHARS.sub("-", text).strip()
    cleaned = cleaned.strip(". ")
    if not cleaned:
        raise ValidationError(f"Placeholder '{key}' produced an empty name.")
    if len(cleaned) > 100:
        cleaned = cleaned[:100].rstrip(". ")
    return cleaned


def safe_join(dest: Path, parts: Iterable[str]) -> Path:
    """Join ``parts`` under ``dest`` ensuring the result cannot escape (M3)."""
    dest_resolved = dest.resolve(strict=False)
    for part in parts:
        if not part or part in (".", ".."):
            raise ValidationError(f"Unsafe path segment: {part!r}")
        if Path(part).is_absolute() or ":" in part and os.name == "nt":
            raise ValidationError(f"Absolute path segment refused: {part!r}")
    target = dest_resolved.joinpath(*parts)
    resolved = target.resolve(strict=False)
    if resolved != dest_resolved and dest_resolved not in resolved.parents:
        raise ValidationError(
            f"Refusing to write outside the destination: {target}")
    return target


def find_placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(text))


def _looks_textual(path: Path) -> bool:
    if path.suffix.lower() in _TEXT_SUFFIXES:
        return True
    if path.name in _TEXT_NAMES or path.name.startswith(".env"):
        return True
    return False


def _read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > _MAX_TEXT_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:8192]:
        return None
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


class TemplateEngine:
    def __init__(self, path: Path | str, ignore: Iterable[str] | None = None,
                 placeholders: dict[str, str] | None = None) -> None:
        self.root = Path(path).expanduser()
        self.ignore = IgnoreMatcher(ignore or [])
        self.placeholders = dict(placeholders or {})

    # ------------------------------------------------------------------ scan

    def root_exists(self) -> bool:
        return self.root.is_dir()

    def iter_files(self) -> Iterator[tuple[Path, tuple[str, ...]]]:
        """Yield ``(absolute_path, relative_parts)`` skipping ignored entries."""
        if not self.root_exists():
            return
        root_resolved = self.root.resolve()
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            current = Path(dirpath)
            rel_dir = current.relative_to(self.root)
            rel_parts_dir = () if rel_dir == Path(".") else rel_dir.parts
            pruned = []
            for name in sorted(dirnames):
                candidate = rel_parts_dir + (name,)
                if self.ignore.matches(candidate):
                    continue
                # Do not descend into symlinked directories (M10).
                if (current / name).is_symlink():
                    continue
                pruned.append(name)
            dirnames[:] = pruned
            for name in sorted(filenames):
                rel_parts = rel_parts_dir + (name,)
                if self.ignore.matches(rel_parts):
                    continue
                full = current / name
                if full.is_symlink():
                    target = full.resolve()
                    if root_resolved not in target.parents:
                        continue
                yield full, rel_parts

    def relative_files(self) -> list[str]:
        return ["/".join(parts) for _abs, parts in self.iter_files()]

    def discover_placeholders(self) -> set[str]:
        found: set[str] = set()
        for abs_path, parts in self.iter_files():
            for part in parts:
                found |= find_placeholders(part)
            if _looks_textual(abs_path):
                text = _read_text(abs_path)
                if text is not None:
                    found |= find_placeholders(text)
        return found

    def preview(self) -> dict:
        files = self.relative_files()
        values = {key: "" for key in sorted(self.discover_placeholders()
                                            or self.placeholders)}
        return {
            "path": str(self.root),
            "exists": self.root_exists(),
            "files": files,
            "placeholders": values,
            "ignore": self.ignore.patterns,
            "error": "" if self.root_exists() else "Template folder is missing.",
        }

    # -------------------------------------------------------------- apply

    def _render_name(self, parts: tuple[str, ...],
                     values: dict[str, str]) -> tuple[str, ...]:
        out = []
        for part in parts:
            def _sub(match: re.Match) -> str:
                key = match.group(1)
                return sanitize_placeholder_value(values.get(key, match.group(0)),
                                                  key)
            rendered = _PLACEHOLDER_RE.sub(_sub, part)
            if rendered in ("", ".", ".."):
                raise ValidationError(f"Placeholder produced an invalid name: {part!r}")
            out.append(rendered)
        return tuple(out)

    def _render_text(self, text: str, values: dict[str, str]) -> str:
        def _sub(match: re.Match) -> str:
            key = match.group(1)
            return str(values.get(key, match.group(0)))

        return _PLACEHOLDER_RE.sub(_sub, text)

    def apply(self, destination: Path | str, values: dict[str, str],
              write_gitignore: bool = True) -> list[str]:
        """Materialise the template into ``destination``. Returns written paths."""
        if not self.root_exists():
            raise ScaffolderError(f"Template folder is missing: {self.root}")
        dest = Path(destination).expanduser()
        dest.mkdir(parents=True, exist_ok=True)
        merged = {**self.placeholders, **values}
        written: list[str] = []
        allowed_keys = set(merged) | self.discover_placeholders()
        unknown = set(values) - allowed_keys
        if unknown:
            raise ValidationError(
                "Unknown placeholder(s): " + ", ".join(sorted(unknown)))
        for abs_path, parts in self.iter_files():
            rel_out = self._render_name(parts, merged)
            target = safe_join(dest, rel_out)
            target.parent.mkdir(parents=True, exist_ok=True)
            text = _read_text(abs_path) if _looks_textual(abs_path) else None
            if text is None:
                shutil.copy2(abs_path, target)
            else:
                newline = "\r\n" if "\r\n" in text else "\n"
                rendered = self._render_text(text, merged)
                if newline == "\n":
                    rendered = rendered.replace("\r\n", "\n")
                target.write_text(rendered, encoding="utf-8", newline=newline)
                try:
                    shutil.copystat(abs_path, target)
                except OSError:
                    pass
            written.append("/".join(rel_out))
        if write_gitignore and self.ignore.patterns:
            gitignore = dest / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text("\n".join(self.ignore.patterns) + "\n",
                                     encoding="utf-8")
                written.append(".gitignore")
        return written


def default_template_manifest(name: str, description: str = "") -> str:
    """Serialise a portable template manifest (N10)."""
    return json.dumps({"name": name, "description": description,
                       "schema": 1}, indent=2)
