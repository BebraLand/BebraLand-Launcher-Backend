from __future__ import annotations

from pathlib import Path
import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from . import config
from . import storage
from .shell import run_shell


console = Console()
app = typer.Typer(
    help="BebraLand launcher backend: create, build, host modpacks.",
    invoke_without_command=True,
)
profile_app = typer.Typer(help="Manage modpack profiles.")
release_app = typer.Typer(help="Manage launcher update metadata.")


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        run_shell()


@profile_app.command("create")
def profile_create(
    minecraft_version: str,
    mod_loader: str,
    loader_version: str,
    name: str,
) -> None:
    profile = storage.create_profile(minecraft_version, mod_loader, loader_version, name)
    console.print(f"Created [green]{profile['slug']}[/green]")
    console.print(f"Source dir: {profile['source_dir']}")


@profile_app.command("list")
def profile_list() -> None:
    table = Table(title="BebraLand profiles")
    for column in ("slug", "name", "minecraft", "loader", "loader version", "latest build"):
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


@profile_app.command("path")
def profile_path(slug: str) -> None:
    console.print(storage.get_profile(slug)["source_dir"])


@profile_app.command("clone")
def profile_clone(source: str, new_name: str) -> None:
    profile = storage.clone_profile(source, new_name)
    console.print(f"Cloned [green]{source}[/green] -> [green]{profile['slug']}[/green]")
    console.print(f"Source dir: {profile['source_dir']}")


@profile_app.command("delete")
def profile_delete(name: str, yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    if not yes:
        typer.confirm(f"Delete profile {name} and all its files forever?", abort=True)
    profile = storage.delete_profile(name)
    console.print(f"Deleted [red]{profile['name']}[/red]")


@profile_app.command("whitelist-add")
def whitelist_add(slug: str, pattern: str) -> None:
    profile = storage.set_rule(slug, "whitelist", pattern, True)
    console.print(profile["whitelist"])


@profile_app.command("whitelist-remove")
def whitelist_remove(slug: str, pattern: str) -> None:
    profile = storage.set_rule(slug, "whitelist", pattern, False)
    console.print(profile["whitelist"])


@profile_app.command("blacklist-add")
def blacklist_add(slug: str, pattern: str) -> None:
    profile = storage.set_rule(slug, "blacklist", pattern, True)
    console.print(profile["blacklist"])


@profile_app.command("blacklist-remove")
def blacklist_remove(slug: str, pattern: str) -> None:
    profile = storage.set_rule(slug, "blacklist", pattern, False)
    console.print(profile["blacklist"])


@profile_app.command("allow-add", hidden=True)
def allow_add(slug: str, pattern: str) -> None:
    whitelist_add(slug, pattern)


@profile_app.command("allow-remove", hidden=True)
def allow_remove(slug: str, pattern: str) -> None:
    whitelist_remove(slug, pattern)


@profile_app.command("deny-add", hidden=True)
def deny_add(slug: str, pattern: str) -> None:
    blacklist_add(slug, pattern)


@profile_app.command("deny-remove", hidden=True)
def deny_remove(slug: str, pattern: str) -> None:
    blacklist_remove(slug, pattern)


@profile_app.command("import-files")
def import_files(
    slug: str,
    source: Path,
    replace: bool = typer.Option(False, "--replace", help="Clear old source folder first."),
) -> None:
    target = storage.copy_source(slug, source, replace=replace)
    console.print(f"Imported into {target}")


@app.command("build")
def build(
    slug: str,
    base_url: str | None = typer.Option(None, "--base-url"),
) -> None:
    manifest = storage.build_profile(slug, base_url or config.public_base_url())
    console.print(f"Build: [green]{manifest['build_id']}[/green]")
    console.print(f"Files: {manifest['file_count']}, bytes: {manifest['total_size']}")
    console.print(f"SHA256: {manifest['content_hash']}")


@app.command("serve")
def serve(
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    uvicorn.run(
        "bebraland_backend.api:app",
        host=host or config.server_host(),
        port=port or config.server_port(),
        reload=reload,
    )


@release_app.command("write")
def release_write(
    version: str,
    url: str,
    sha256: str,
    platform: str = typer.Option("windows", "--platform"),
    notes: str = typer.Option("", "--notes"),
) -> None:
    release = storage.write_release(version, url, sha256, platform, notes)
    console.print(release)


@release_app.command("show")
def release_show() -> None:
    release = storage.latest_release()
    console.print(release or "No release metadata yet")


app.add_typer(profile_app, name="profile")
app.add_typer(release_app, name="release")
