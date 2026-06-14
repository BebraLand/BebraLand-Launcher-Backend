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
        """profile create <mc_version> <mod_loader> <loader_version> <name> [--ram-mb MB] | profile runtime <slug> <mc_version> <mod_loader> [loader_version] | profile ram <slug> <mb> | profile server <slug> <host[:port]> [--port PORT] [--name NAME] | profile server <slug> --clear | profile clone <old> <new> | profile delete <name> | profile list | profile path <name>"""
        args = shlex.split(line)
        if not args:
            console.print(self.do_profile.__doc__)
            return
        action = args[0]
        try:
            if action == "create":
                if len(args) < 5:
                    console.print("Usage: profile create <mc_version> <mod_loader> <loader_version> <name> [--ram-mb MB]")
                    return
                ram_mb = storage.DEFAULT_RECOMMENDED_RAM_MB
                if "--ram-mb" in args:
                    index = args.index("--ram-mb")
                    try:
                        ram_mb = int(args[index + 1])
                    except (IndexError, ValueError):
                        console.print("Usage: profile create <mc_version> <mod_loader> <loader_version> <name> [--ram-mb MB]")
                        return
                    del args[index : index + 2]
                name = " ".join(args[4:])
                profile = storage.create_profile(args[1], args[2], args[3], name, ram_mb)
                console.print(f"Created {profile['slug']}: {profile['source_dir']}")
                console.print(f"Recommended RAM: {profile['recommended_ram_mb']} MB")
            elif action == "ram":
                if len(args) != 3:
                    console.print("Usage: profile ram <slug> <mb>")
                    return
                profile = storage.set_recommended_ram(args[1], int(args[2]))
                console.print(f"{profile['slug']} recommended RAM: {profile['recommended_ram_mb']} MB")
            elif action == "server":
                if len(args) < 2:
                    console.print("Usage: profile server <slug> <host[:port]> [--port PORT] [--name NAME] | profile server <slug> --clear")
                    return
                slug = args[1]
                if "--clear" in args:
                    profile = storage.clear_profile_server(slug)
                    console.print(f"{profile['slug']} server cleared")
                    return

                if len(args) < 3:
                    console.print("Usage: profile server <slug> <host[:port]> [--port PORT] [--name NAME]")
                    return
                host = args[2]
                port = storage.server_status.DEFAULT_PORT
                name = ""
                if "--port" in args:
                    index = args.index("--port")
                    try:
                        port = int(args[index + 1])
                    except (IndexError, ValueError):
                        console.print("Usage: profile server <slug> <host[:port]> [--port PORT] [--name NAME]")
                        return
                if "--name" in args:
                    index = args.index("--name")
                    try:
                        name = args[index + 1]
                    except IndexError:
                        console.print("Usage: profile server <slug> <host[:port]> [--port PORT] [--name NAME]")
                        return
                profile = storage.set_profile_server(slug, host, port, name)
                server = storage.normalize_profile_server(profile.get("server"))
                console.print(f"{profile['slug']} server: {server['host']}:{server['port']}")
            elif action in {"runtime", "hotswap", "loader"}:
                if len(args) not in {4, 5}:
                    console.print("Usage: profile runtime <slug> <mc_version> <mod_loader> [loader_version]")
                    return
                loader_version = args[4] if len(args) == 5 else ""
                profile = storage.set_profile_runtime(args[1], args[2], args[3], loader_version)
                loader_display = profile.get("loader_version") or "-"
                console.print(
                    f"{profile['slug']} runtime: Minecraft {profile['minecraft_version']}, "
                    f"{profile['mod_loader']} {loader_display}"
                )
                console.print("Next launch rebuilds manifest and installs this runtime for players.")
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
    for column in ("slug", "name", "mc", "loader", "loader_ver", "ram_mb", "server", "latest"):
        table.add_column(column)
    for profile in storage.list_profiles():
        server = storage.normalize_profile_server(profile.get("server"))
        server_label = f"{server['host']}:{server['port']}" if server else "-"
        table.add_row(
            profile["slug"],
            profile["name"],
            profile["minecraft_version"],
            profile["mod_loader"],
            profile["loader_version"],
            str(profile.get("recommended_ram_mb", storage.DEFAULT_RECOMMENDED_RAM_MB)),
            server_label,
            str(profile.get("latest_build") or "-"),
        )
    console.print(table)


def run_shell() -> None:
    storage.ensure_data_dirs()
    BebraLandShell().cmdloop()
