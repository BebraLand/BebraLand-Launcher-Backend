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
    recommended_ram_mb: int = typer.Option(
        storage.DEFAULT_RECOMMENDED_RAM_MB,
        "--ram-mb",
        "--recommended-ram-mb",
        help="Recommended launcher RAM in MB.",
    ),
    description: str = typer.Option("", "--description", "--desc", help="Short profile description shown on launcher home."),
    icon: Path | None = typer.Option(None, "--icon", help="Profile icon image: png, jpg, jpeg, or webp."),
    background: Path | None = typer.Option(None, "--background", help="Profile background image: png, jpg, jpeg, or webp."),
) -> None:
    profile = storage.create_profile(minecraft_version, mod_loader, loader_version, name, recommended_ram_mb, description)
    if icon or background:
        profile = storage.set_profile_assets(profile["slug"], icon=icon, background=background)
    console.print(f"Created [green]{profile['slug']}[/green]")
    console.print(f"Source dir: {profile['source_dir']}")
    console.print(f"Recommended RAM: {profile['recommended_ram_mb']} MB")
    if profile.get("icon_asset"):
        console.print(f"Icon: {profile['icon_asset']}")
    if profile.get("background_asset"):
        console.print(f"Background: {profile['background_asset']}")


@profile_app.command("list")
def profile_list() -> None:
    table = Table(title="BebraLand profiles")
    for column in (
        "priority",
        "enabled",
        "slug",
        "name",
        "minecraft",
        "loader",
        "loader version",
        "ram MB",
        "allowed users",
        "server",
        "latest build",
    ):
        table.add_column(column)
    for profile in storage.list_profiles(include_hidden=True):
        server = storage.normalize_profile_server(profile.get("server"))
        server_label = f"{server['host']}:{server['port']}" if server else "-"
        table.add_row(
            str(profile.get("priority", storage.DEFAULT_PROFILE_PRIORITY)),
            "yes" if storage.normalize_bool(profile.get("enabled"), True) else "no",
            profile["slug"],
            profile["name"],
            profile["minecraft_version"],
            profile["mod_loader"],
            profile["loader_version"],
            str(profile.get("recommended_ram_mb", storage.DEFAULT_RECOMMENDED_RAM_MB)),
            ", ".join(storage.normalize_allowed_users(profile.get("allowed_users"))) or "-",
            server_label,
            str(profile.get("latest_build") or "-"),
        )
    console.print(table)


@profile_app.command("path")
def profile_path(slug: str) -> None:
    console.print(storage.get_profile(slug)["source_dir"])


@profile_app.command("ram")
def profile_ram(slug: str, ram_mb: int) -> None:
    profile = storage.set_recommended_ram(slug, ram_mb)
    console.print(f"{profile['slug']} recommended RAM: {profile['recommended_ram_mb']} MB")


@profile_app.command("description")
def profile_description(slug: str, description: str = typer.Argument("", help="Short profile description.")) -> None:
    profile = storage.set_profile_description(slug, description)
    console.print(f"{profile['slug']} description updated")


@profile_app.command("priority")
def profile_priority(slug: str, priority: int) -> None:
    profile = storage.set_profile_priority(slug, priority)
    console.print(f"{profile['slug']} priority: {profile['priority']}")


@profile_app.command("enable")
def profile_enable(slug: str) -> None:
    profile = storage.set_profile_enabled(slug, True)
    console.print(f"{profile['slug']} enabled")


@profile_app.command("disable")
def profile_disable(slug: str) -> None:
    profile = storage.set_profile_enabled(slug, False)
    console.print(f"{profile['slug']} disabled")


@profile_app.command("user-add")
def profile_user_add(slug: str, username: str) -> None:
    profile = storage.set_profile_allowed_user(slug, username, True)
    console.print(f"{profile['slug']} allowed users: {profile['allowed_users']}")


@profile_app.command("user-remove")
def profile_user_remove(slug: str, username: str) -> None:
    profile = storage.set_profile_allowed_user(slug, username, False)
    console.print(f"{profile['slug']} allowed users: {profile['allowed_users']}")


@profile_app.command("server")
def profile_server(
    slug: str,
    host: str = typer.Argument("", help="Minecraft server host or host:port. Leave empty with --clear."),
    port: int = typer.Option(storage.server_status.DEFAULT_PORT, "--port", "-p"),
    name: str = typer.Option("", "--name", help="Display name shown in API payload."),
    clear: bool = typer.Option(False, "--clear", help="Remove server badge from this profile."),
) -> None:
    if clear:
        profile = storage.clear_profile_server(slug)
        console.print(f"{profile['slug']} server cleared")
        return
    if not host:
        raise typer.BadParameter("Pass host or --clear")
    profile = storage.set_profile_server(slug, host, port, name)
    server = storage.normalize_profile_server(profile.get("server"))
    console.print(f"{profile['slug']} server: {server['host']}:{server['port']}")


@profile_app.command("assets")
def profile_assets(
    slug: str,
    icon: Path | None = typer.Option(None, "--icon", help="Profile icon image: png, jpg, jpeg, or webp."),
    background: Path | None = typer.Option(None, "--background", help="Profile background image: png, jpg, jpeg, or webp."),
) -> None:
    if not icon and not background:
        raise typer.BadParameter("Pass --icon and/or --background")
    profile = storage.set_profile_assets(slug, icon=icon, background=background)
    console.print(f"{profile['slug']} assets updated")
    if profile.get("icon_asset"):
        console.print(f"Icon: {profile['icon_asset']}")
    if profile.get("background_asset"):
        console.print(f"Background: {profile['background_asset']}")


def print_runtime(profile: dict[str, object]) -> None:
    loader_version = str(profile.get("loader_version") or "-")
    console.print(
        f"{profile['slug']} runtime: "
        f"Minecraft {profile['minecraft_version']}, "
        f"{profile['mod_loader']} {loader_version}"
    )
    console.print("Next launch rebuilds manifest and installs this runtime for players.")


@profile_app.command("runtime")
def profile_runtime(
    slug: str,
    minecraft_version: str,
    mod_loader: str,
    loader_version: str = typer.Argument("", help="Required for Forge/NeoForge/Fabric; leave empty for vanilla."),
) -> None:
    profile = storage.set_profile_runtime(slug, minecraft_version, mod_loader, loader_version)
    print_runtime(profile)


@profile_app.command("hotswap")
def profile_hotswap(
    slug: str,
    minecraft_version: str,
    mod_loader: str,
    loader_version: str = typer.Argument("", help="Required for Forge/NeoForge/Fabric; leave empty for vanilla."),
) -> None:
    profile_runtime(slug, minecraft_version, mod_loader, loader_version)


@profile_app.command("loader")
def profile_loader(
    slug: str,
    minecraft_version: str,
    mod_loader: str,
    loader_version: str = typer.Argument("", help="Required for Forge/NeoForge/Fabric; leave empty for vanilla."),
) -> None:
    profile_runtime(slug, minecraft_version, mod_loader, loader_version)


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
    manifest = storage.build_profile(slug, base_url or config.file_base_url())
    console.print(f"Build: [green]{manifest['build_id']}[/green]")
    console.print(f"Files: {manifest['file_count']}, bytes: {manifest['total_size']}")
    console.print(f"SHA256: {manifest['content_hash']}")


@app.command("serve")
def serve(
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
    reload: bool = typer.Option(True, "--reload/--no-reload", help="Restart server when backend files change."),
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
    platform: str = typer.Option("windows-x64", "--platform"),
    update_id: str = typer.Option("", "--update-id"),
    compat_version: str = typer.Option("", "--compat-version"),
    display_version: str = typer.Option("", "--display-version"),
    notes: str = typer.Option("", "--notes"),
) -> None:
    release = storage.write_release(
        version,
        url,
        sha256,
        platform,
        notes,
        display_version=display_version,
        update_id=update_id,
        compat_version=compat_version,
    )
    console.print(release)


@release_app.command("show")
def release_show(platform: str | None = typer.Option(None, "--platform")) -> None:
    release = storage.latest_release(platform)
    console.print(release or "No release metadata yet")


app.add_typer(profile_app, name="profile")
app.add_typer(release_app, name="release")
