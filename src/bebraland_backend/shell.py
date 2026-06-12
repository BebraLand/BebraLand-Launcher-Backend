from __future__ import annotations

import cmd
import shlex
from pathlib import Path

import uvicorn
from rich.console import Console
from rich.table import Table

from . import config
from . import storage


console = Console()


class BebraLandShell(cmd.Cmd):
    intro = "BebraLand backend shell. Type help or ?. Type exit to quit."
    prompt = "bebraland> "

    def do_profile(self, line: str) -> None:
        """profile create <mc_version> <mod_loader> <loader_version> <name> | profile clone <old> <new> | profile delete <name> | profile list | profile path <name>"""
        args = shlex.split(line)
        if not args:
            console.print(self.do_profile.__doc__)
            return
        action = args[0]
        try:
            if action == "create":
                if len(args) < 5:
                    console.print("Usage: profile create <mc_version> <mod_loader> <loader_version> <name>")
                    return
                name = " ".join(args[4:])
                profile = storage.create_profile(args[1], args[2], args[3], name)
                console.print(f"Created {profile['slug']}: {profile['source_dir']}")
            elif action == "clone":
                if len(args) < 3:
                    console.print("Usage: profile clone <old_name> <new_name>")
                    return
                profile = storage.clone_profile(args[1], " ".join(args[2:]))
                console.print(f"Cloned {args[1]} -> {profile['slug']}: {profile['source_dir']}")
            elif action == "delete":
                if len(args) != 2:
                    console.print("Usage: profile delete <name>")
                    return
                profile = storage.delete_profile(args[1])
                console.print(f"Deleted {profile['name']}")
            elif action == "list":
                print_profiles()
            elif action == "path":
                profile = storage.get_profile(args[1])
                console.print(profile["source_dir"])
            else:
                console.print(f"Unknown profile action: {action}")
        except Exception as exc:
            console.print(f"[red]{exc}[/red]")

    def do_whitelist(self, line: str) -> None:
        """whitelist add <profile> <pattern> | whitelist remove <profile> <pattern>"""
        self._rule("whitelist", line)

    def do_blacklist(self, line: str) -> None:
        """blacklist add <profile> <pattern> | blacklist remove <profile> <pattern>"""
        self._rule("blacklist", line)

    def do_allow(self, line: str) -> None:
        """Alias for whitelist."""
        self._rule("whitelist", line)

    def do_deny(self, line: str) -> None:
        """Alias for blacklist."""
        self._rule("blacklist", line)

    def _rule(self, kind: str, line: str) -> None:
        args = shlex.split(line)
        if len(args) != 3 or args[0] not in {"add", "remove"}:
            console.print(f"Usage: {kind} add|remove <slug> <pattern>")
            return
        try:
            profile = storage.set_rule(args[1], kind, args[2], args[0] == "add")
            console.print(f"{kind}: {profile[kind]}")
        except Exception as exc:
            console.print(f"[red]{exc}[/red]")

    def do_import(self, line: str) -> None:
        """import <slug> <path> [--replace]"""
        args = shlex.split(line)
        if len(args) not in {2, 3}:
            console.print("Usage: import <slug> <path> [--replace]")
            return
        try:
            target = storage.copy_source(args[0], Path(args[1]), replace="--replace" in args)
            console.print(f"Imported into {target}")
        except Exception as exc:
            console.print(f"[red]{exc}[/red]")

    def do_build(self, line: str) -> None:
        """build <slug> [base_url]"""
        args = shlex.split(line)
        if not args:
            console.print("Usage: build <slug> [base_url]")
            return
        try:
            manifest = storage.build_profile(args[0], args[1] if len(args) > 1 else config.public_base_url())
            console.print(
                f"Build {manifest['build_id']} files={manifest['file_count']} sha={manifest['content_hash']}"
            )
        except Exception as exc:
            console.print(f"[red]{exc}[/red]")

    def do_serve(self, line: str) -> None:
        """serve [host] [port]"""
        args = shlex.split(line)
        host = args[0] if len(args) >= 1 else config.server_host()
        port = int(args[1]) if len(args) >= 2 else config.server_port()
        uvicorn.run("bebraland_backend.api:app", host=host, port=port)

    def do_exit(self, line: str) -> bool:
        """exit"""
        return True

    def do_EOF(self, line: str) -> bool:
        console.print()
        return True


def print_profiles() -> None:
    table = Table(title="Profiles")
    for column in ("slug", "name", "mc", "loader", "loader_ver", "latest"):
        table.add_column(column)
    for profile in storage.list_profiles():
        table.add_row(
            profile["slug"],
            profile["name"],
            profile["minecraft_version"],
            profile["mod_loader"],
            profile["loader_version"],
            str(profile.get("latest_build") or "-"),
        )
    console.print(table)


def run_shell() -> None:
    storage.ensure_data_dirs()
    BebraLandShell().cmdloop()
