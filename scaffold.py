"""Headless command-line interface (handy for scripting and CI).

Examples
--------
    python scaffold.py list
    python scaffold.py create demo --template basic --dest ~/projects/demo
    python scaffold.py changes demo
    python scaffold.py commit demo -m "message"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from scaffolder_app.errors import ScaffolderError  # noqa: E402
from scaffolder_app.ui.services import Services  # noqa: E402


def _services() -> Services:
    return Services.bootstrap()


def cmd_list(args: argparse.Namespace) -> int:
    services = _services()
    projects = services.memory.list_projects()
    if args.json:
        print(json.dumps(projects, indent=2))
        return 0
    if not projects:
        print("No projects.")
        return 0
    for project in projects:
        summary = services.memory.get_project_summary(project["name"])
        print(f"{project['name']:<28} {project['repo_state']:<13} "
              f"{summary['current_version'] or '-':<8} "
              f"↑{summary['ahead_total']} ↓{summary['behind_total']}  "
              f"{project['path']}")
    return 0


def cmd_templates(args: argparse.Namespace) -> int:
    services = _services()
    for template in services.memory.list_templates():
        print(f"{template['name']:<24} {template['path']}")
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    services = _services()
    values = {}
    for item in args.placeholder or []:
        key, _, value = item.partition("=")
        values[key] = value
    project = services.scaffolder.create_project(
        args.name, args.template, args.dest, values,
        visibility=args.visibility, account=args.account,
        create_repo=args.create_repo,
        progress=lambda message: print(f"  {message}"))
    print(f"Created {project['name']} at {project['path']}")
    return 0


def cmd_changes(args: argparse.Namespace) -> int:
    services = _services()
    for entry in services.scaffolder.get_changes(args.project):
        flag = "staged" if entry.get("staged") and not entry.get("untracked") else "      "
        print(f"{entry['kind']:<12} {flag} {entry['path']}")
    return 0


def cmd_commit(args: argparse.Namespace) -> int:
    services = _services()
    result = services.scaffolder.commit(args.project, args.message,
                                        push=False)
    print(f"Committed {result['sha']}: {result['message'].splitlines()[0]}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    services = _services()
    print(json.dumps(services.scaffolder.project_status(args.project), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scaffold",
                                     description="Project Scaffolder CLI")
    parser.add_argument("--json", action="store_true", help="machine output")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list projects").set_defaults(func=cmd_list)
    sub.add_parser("templates", help="list templates").set_defaults(
        func=cmd_templates)

    create = sub.add_parser("create", help="create a project from a template")
    create.add_argument("name")
    create.add_argument("--template", required=True)
    create.add_argument("--dest", required=True)
    create.add_argument("--visibility", default="private",
                        choices=["private", "public"])
    create.add_argument("--account", default=None)
    create.add_argument("--create-repo", action="store_true")
    create.add_argument("--placeholder", action="append", metavar="KEY=VALUE")
    create.set_defaults(func=cmd_create)

    changes = sub.add_parser("changes", help="show uncommitted changes")
    changes.add_argument("project")
    changes.set_defaults(func=cmd_changes)

    commit = sub.add_parser("commit", help="commit all changes")
    commit.add_argument("project")
    commit.add_argument("-m", "--message", required=True)
    commit.set_defaults(func=cmd_commit)

    status = sub.add_parser("status", help="project status as JSON")
    status.add_argument("project")
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to a legacy codepage; arrows and box characters
    # would raise UnicodeEncodeError while printing.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ScaffolderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if getattr(exc, "hint", ""):
            print(f"hint: {exc.hint}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
