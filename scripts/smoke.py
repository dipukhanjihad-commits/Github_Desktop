"""Manual smoke script: exercises the backend end-to-end without the UI.

Usage:  python scripts/smoke.py [workdir]
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scaffolder_app.credentials import CredentialStore  # noqa: E402
from scaffolder_app.project_memory import ProjectMemory  # noqa: E402
from scaffolder_app.scaffolder import Scaffolder  # noqa: E402


def main() -> int:
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(
        prefix="scaffold-smoke-"))
    base.mkdir(parents=True, exist_ok=True)
    print(f"workdir: {base}")

    memory = ProjectMemory(path=base / "memory.json", use_file_lock=False)
    service = Scaffolder(memory, CredentialStore(base, prefer_keyring=False),
                         root=base)

    template = base / "template"
    (template / "src").mkdir(parents=True)
    (template / "{{project_name}}.txt").write_text("hi {{author}}\n", encoding="utf-8")
    (template / "src" / "main.py").write_text(
        "print('{{project_name}}')\n", encoding="utf-8")
    memory.add_template(name="basic", path=template, ignore=[".git"],
                        placeholders={"author": ""})

    project = service.create_project(
        "demo", "basic", base / "work" / "demo", {"author": "Jihad"},
        account=None, create_repo=False)
    print("project:", project["name"], project["repo_state"],
          "versions:", list(project["versions"]))
    print("files:", sorted(p.name for p in (base / "work" / "demo").iterdir()))

    main_py = base / "work" / "demo" / "src" / "main.py"
    main_py.write_text("print('changed')\n", encoding="utf-8")
    changes = service.get_changes("demo")
    print("changes:", [(c["path"], c["kind"]) for c in changes])
    diff = service.diff("demo", "src/main.py")
    print("diff shows change:", "+print('changed')" in diff)

    commit = service.commit("demo", "change main")
    print("commit:", commit["sha"])
    print("history:", [c["subject"] for c in service.history("demo")])

    version = service.create_version("demo", "v2")
    print("v2 branch:", version["branch"], "| current:",
          memory.get_project("demo")["current_version"])
    switched = service.switch_version("demo", "v1")
    print("switched back to v1:", switched["switched"],
          "| branch:", service.git("demo").current_branch(), "| content:",
          main_py.read_text(encoding="utf-8").strip())

    result = service.delete_project("demo")
    print("deleted -> trash:", bool(result["trashed"]),
          "| projects left:", len(memory.list_projects()))
    service.purge_trash_entry(Path(result["trashed"]).name)

    if len(sys.argv) <= 1:
        shutil.rmtree(base, ignore_errors=True)
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
