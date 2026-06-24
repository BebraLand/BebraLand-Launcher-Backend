from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import server_status
from . import config


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_RECOMMENDED_RAM_MB = 2048
MIN_RECOMMENDED_RAM_MB = 512
MAX_RECOMMENDED_RAM_MB = 65536
DEFAULT_PROFILE_PRIORITY = 0
DEFAULT_WHITELIST: list[str] = [
    "options.txt",
    "optionsof.txt",
    "servers.dat",
    "servers.dat_old",
    "resourcepacks/**",
    "shaderpacks/**",
    "config/**",
    "logs/**",
    "usercache.json",
    "usernamecache.json",
    "screenshots/**",
    "saves/**",
    "launcher_profiles.json",
    "launcher_accounts.json",
    "schematics/**",
]
DEFAULT_BLACKLIST: list[str] = []
DEFAULT_INTERNAL_EXCLUDE = [
    ".git/**",
    "**/.git/**",
    "__pycache__/**",
    "**/__pycache__/**",
    "*.tmp",
    "*.bak",
    "logs/**",
    "crash-reports/**",
    "assets/**",
    "libraries/**",
    "versions/**",
    "runtime/**",
    "runtimes/**",
    ".minecraft/**",
    "launcher_profiles.json",
    "launcher_accounts.json",
    "usercache.json",
]
VANILLA_LOADERS = {"vanilla", "minecraft", "none"}
OPTIONAL_MOD_ID_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")
PROFILE_ASSET_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
PROFILE_ASSET_KINDS = {"icon", "background"}


def data_dir() -> Path:
    return Path(os.environ.get("BEBRALAND_DATA_DIR", ROOT_DIR / "data")).resolve()


def profiles_file() -> Path:
    return data_dir() / "profiles.json"


def admins_file() -> Path:
    return data_dir() / "admins.json"


def ensure_data_dirs() -> None:
    for child in ("sources", "builds", "releases", "assets"):
        (data_dir() / child).mkdir(parents=True, exist_ok=True)


def profile_assets_root() -> Path:
    return data_dir() / "assets" / "profiles"


def profile_assets_dir(slug: str) -> Path:
    return assert_inside(profile_assets_root() / slug, profile_assets_root())


def normalize_profile_asset_name(value: Any) -> str:
    name = Path(str(value or "").replace("\\", "/")).name.strip()
    if not name:
        raise ValueError("Profile asset name is required")
    suffix = Path(name).suffix.lower()
    if suffix not in PROFILE_ASSET_EXTENSIONS:
        raise ValueError("Profile asset must be png, jpg, jpeg, or webp")
    return name


def profile_asset_url(profile: dict[str, Any], kind: str) -> str:
    def local_asset_url(slug_value: str, asset_path: Path) -> str:
        version = sha256_file(asset_path)[:16]
        return f"{config.public_base_url()}/assets/profiles/{quote(slug_value)}/{quote(asset_path.name)}?v={version}"

    kind = str(kind or "").strip().lower()
    if kind not in PROFILE_ASSET_KINDS:
        return ""
    explicit = str(profile.get(f"{kind}_url") or "").strip()
    if explicit:
        return explicit

    slug = str(profile.get("slug") or "").strip()
    if not slug:
        return ""

    asset_name = str(profile.get(f"{kind}_asset") or "").strip()
    if asset_name:
        asset_name = normalize_profile_asset_name(asset_name)
        asset_path = profile_assets_dir(slug) / asset_name
        if asset_path.is_file():
            return local_asset_url(slug, asset_path)
        return f"{config.public_base_url()}/assets/profiles/{quote(slug)}/{quote(asset_name)}"

    assets_dir = profile_assets_dir(slug)
    if assets_dir.exists():
        for suffix in (".png", ".jpg", ".jpeg", ".webp"):
            candidate = assets_dir / f"{kind}{suffix}"
            if candidate.is_file():
                return local_asset_url(slug, candidate)
    return ""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_recommended_ram_mb(value: Any) -> int:
    try:
        ram_mb = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Recommended RAM must be an integer number of MB") from exc
    if ram_mb < MIN_RECOMMENDED_RAM_MB or ram_mb > MAX_RECOMMENDED_RAM_MB:
        raise ValueError(
            f"Recommended RAM must be between {MIN_RECOMMENDED_RAM_MB} and {MAX_RECOMMENDED_RAM_MB} MB"
        )
    return ram_mb


def normalize_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    return bool(value)


def normalize_string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        if text not in result:
            result.append(text)
    return result


def normalize_priority(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Profile priority must be an integer") from exc


def normalize_allowed_users(value: Any) -> list[str]:
    return normalize_string_list(value, "allowed_users")


def load_admin_users() -> list[str]:
    """Return global launcher admins stored in data/admins.json."""
    ensure_data_dirs()
    return normalize_string_list(read_json(admins_file(), []), "admins")


def save_admin_users(users: list[str]) -> None:
    write_json(admins_file(), normalize_string_list(users, "admins"))


def profile_usernames(user: dict[str, Any] | str | None) -> set[str]:
    if user is None:
        return set()
    if isinstance(user, str):
        values = [user]
    elif isinstance(user, dict):
        values = [
            user.get("username"),
            user.get("display_name"),
            user.get("name"),
            user.get("id"),
        ]
    else:
        values = []
    return {str(value).strip().casefold() for value in values if str(value or "").strip()}


def user_is_admin(user: dict[str, Any] | str | None = None) -> bool:
    return bool(profile_usernames(user) & {name.casefold() for name in load_admin_users()})


def profile_visible_to(profile: dict[str, Any], user: dict[str, Any] | str | None = None) -> bool:
    if user_is_admin(user):
        return True
    if normalize_bool(profile.get("opening_mode"), False):
        return True
    if normalize_bool(profile.get("enabled"), True):
        return True
    allowed = {name.casefold() for name in normalize_allowed_users(profile.get("allowed_users"))}
    return bool(allowed & profile_usernames(user))


def profile_launch_allowed(profile: dict[str, Any], user: dict[str, Any] | str | None = None) -> bool:
    """Opening packs are downloadable by everyone; only admins may launch them."""
    if user_is_admin(user):
        return True
    if normalize_bool(profile.get("opening_mode"), False):
        return False
    return profile_visible_to(profile, user)


def split_server_address(value: Any, default_port: int = server_status.DEFAULT_PORT) -> tuple[str, int]:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Server host is required")
    text = text.replace("minecraft://", "").replace("mc://", "")
    text = text.split("/", 1)[0].strip()

    host = text
    port = default_port
    if text.startswith("[") and "]" in text:
        end = text.index("]")
        host = text[1:end].strip()
        tail = text[end + 1 :].strip()
        if tail.startswith(":"):
            port = int(tail[1:])
    elif ":" in text and text.count(":") == 1:
        host, raw_port = text.rsplit(":", 1)
        host = host.strip()
        if raw_port.strip():
            port = int(raw_port.strip())

    if not host:
        raise ValueError("Server host is required")
    if port < 1 or port > 65535:
        raise ValueError("Server port must be between 1 and 65535")
    return host, port


def normalize_profile_server(value: Any) -> dict[str, Any] | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        host, port = split_server_address(value)
        return {"host": host, "port": port, "name": host}
    if not isinstance(value, dict):
        raise ValueError("server must be an object, string, or null")

    raw_host = value.get("host") or value.get("address") or value.get("server")
    host, address_port = split_server_address(raw_host)
    port = int(value.get("port") or address_port)
    if port < 1 or port > 65535:
        raise ValueError("Server port must be between 1 and 65535")
    name = str(value.get("name") or value.get("display_name") or host).strip() or host
    return {
        "host": host,
        "port": port,
        "name": name,
    }


def profile_server_status(profile: dict[str, Any]) -> dict[str, Any] | None:
    server = normalize_profile_server(profile.get("server"))
    if not server:
        return None
    try:
        return server_status.query_java_server(str(server["host"]), int(server["port"]))
    except Exception as exc:
        return server_status.offline_payload(exc)


def normalize_pack_pattern(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip("/")


def normalize_optional_mod_files(raw_mod: dict[str, Any], mod_id: str) -> list[str]:
    raw_files = raw_mod.get("files") or raw_mod.get("paths") or raw_mod.get("patterns")
    if raw_files is None:
        raw_files = []
    if not isinstance(raw_files, list):
        raise ValueError(f"optional_mods[{mod_id}].files must be a list")

    files: list[str] = []
    for index, item in enumerate(raw_files, start=1):
        if isinstance(item, dict):
            path = normalize_pack_pattern(item.get("path") or item.get("file") or item.get("pattern"))
            if not path:
                raise ValueError(f"optional_mods[{mod_id}].files[{index}].path is required")
        else:
            path = normalize_pack_pattern(item)
        if not path:
            continue
        if path not in files:
            files.append(path)
    return files


def normalize_optional_mod_id(value: Any, field_name: str = "optional mod id") -> str:
    mod_id = str(value or "").strip()
    if not mod_id:
        raise ValueError(f"{field_name} is required")
    if not OPTIONAL_MOD_ID_RE.fullmatch(mod_id):
        raise ValueError(f"{field_name} must contain only letters, numbers, dots, underscores, and dashes")
    return mod_id


def normalize_optional_mods(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("optional_mods must be a list")

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_mod in enumerate(value, start=1):
        if not isinstance(raw_mod, dict):
            raise ValueError(f"optional_mods[{index}] must be an object")
        mod_id = normalize_optional_mod_id(raw_mod.get("id") or raw_mod.get("slug") or raw_mod.get("key"))
        if mod_id in seen:
            raise ValueError(f"Duplicate optional mod id: {mod_id}")
        seen.add(mod_id)

        files = normalize_optional_mod_files(raw_mod, mod_id)
        if not files:
            raise ValueError(f"optional_mods[{mod_id}].files must not be empty")

        requires = [
            normalize_optional_mod_id(item, f"optional_mods[{mod_id}].requires")
            for item in normalize_string_list(
                raw_mod.get("requires") or raw_mod.get("depends_on") or raw_mod.get("dependencies"),
                f"optional_mods[{mod_id}].requires",
            )
        ]
        conflicts = [
            normalize_optional_mod_id(item, f"optional_mods[{mod_id}].conflicts")
            for item in normalize_string_list(raw_mod.get("conflicts"), f"optional_mods[{mod_id}].conflicts")
        ]
        if mod_id in requires:
            raise ValueError(f"optional_mods[{mod_id}] cannot require itself")
        if mod_id in conflicts:
            raise ValueError(f"optional_mods[{mod_id}] cannot conflict with itself")

        result.append(
            {
                "id": mod_id,
                "name": str(raw_mod.get("name") or mod_id).strip() or mod_id,
                "description": str(raw_mod.get("description") or "").strip(),
                "default_enabled": normalize_bool(
                    raw_mod.get("default_enabled", raw_mod.get("enabled_by_default", raw_mod.get("default"))),
                    False,
                ),
                "files": files,
                "requires": requires,
                "conflicts": conflicts,
                "keep_on_disable": normalize_bool(raw_mod.get("keep_on_disable"), False),
            }
        )

    known = {item["id"] for item in result}
    for item in result:
        for required_id in item["requires"]:
            if required_id not in known:
                raise ValueError(f"optional_mods[{item['id']}] requires unknown optional mod: {required_id}")
        for conflict_id in item["conflicts"]:
            if conflict_id not in known:
                raise ValueError(f"optional_mods[{item['id']}] conflicts with unknown optional mod: {conflict_id}")
    return result


def normalize_runtime(
    minecraft_version: str,
    mod_loader: str,
    loader_version: str | None,
) -> tuple[str, str, str]:
    minecraft_version = str(minecraft_version or "").strip()
    mod_loader = str(mod_loader or "").strip().lower()
    loader_version = str(loader_version or "").strip()
    if not minecraft_version:
        raise ValueError("Minecraft version is required")
    if not mod_loader:
        raise ValueError("Mod loader is required")
    if mod_loader in VANILLA_LOADERS:
        return minecraft_version, "vanilla", ""
    if not loader_version:
        raise ValueError(f"Loader version required for {mod_loader}")
    return minecraft_version, mod_loader, loader_version


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    if not slug:
        raise ValueError("Name cannot produce valid slug")
    return slug


def profile_key(identifier: str) -> str:
    profiles = load_profiles()
    if identifier in profiles:
        return identifier
    slug = slugify(identifier)
    if slug in profiles:
        return slug
    lowered = identifier.casefold()
    for key, profile in profiles.items():
        if profile["name"].casefold() == lowered:
            return key
    raise KeyError(f"Profile not found: {identifier}")


def assert_inside(path: Path, parent: Path) -> Path:
    resolved = path.resolve()
    root = parent.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Path escapes storage root: {resolved}")
    return resolved


def stored_source_dir(slug: str) -> str:
    return f"sources/{slug}"


def normalize_stored_source_dir(profile: dict[str, Any]) -> str:
    slug = slugify(str(profile.get("slug") or profile.get("name") or ""))
    raw = str(profile.get("source_dir") or "").strip()
    if not raw:
        return stored_source_dir(slug)

    normalized = raw.replace("\\", "/").strip()
    sources_marker = "/sources/"
    if sources_marker in normalized:
        normalized = f"sources/{normalized.rsplit(sources_marker, 1)[1]}"
    elif normalized.startswith("sources/"):
        pass
    elif normalized.startswith("data/sources/"):
        normalized = normalized.removeprefix("data/")

    candidate = Path(normalized)
    if candidate.is_absolute():
        candidate = assert_inside(candidate, data_dir() / "sources")
        return candidate.relative_to(data_dir()).as_posix()

    candidate = Path(normalized)
    if candidate.parts and candidate.parts[0] == "sources":
        resolved = assert_inside(data_dir() / candidate, data_dir() / "sources")
        return resolved.relative_to(data_dir()).as_posix()

    raise ValueError(f"Invalid source_dir for profile {slug}: {raw}")


def profile_source_root(profile: dict[str, Any]) -> Path:
    relative = normalize_stored_source_dir(profile)
    return assert_inside(data_dir() / relative, data_dir() / "sources")


def load_profiles() -> dict[str, dict[str, Any]]:
    ensure_data_dirs()
    profiles = read_json(profiles_file(), {})
    if not isinstance(profiles, dict):
        raise ValueError("profiles.json must contain object")
    dirty = False
    for profile in profiles.values():
        old_source_dir = profile.get("source_dir")
        profile["source_dir"] = normalize_stored_source_dir(profile)
        if old_source_dir != profile["source_dir"]:
            dirty = True
        if "whitelist" not in profile:
            profile["whitelist"] = list(DEFAULT_WHITELIST)
            dirty = True
        if "blacklist" not in profile:
            profile["blacklist"] = list(DEFAULT_BLACKLIST)
            dirty = True
        old_priority = profile.get("priority")
        try:
            profile["priority"] = normalize_priority(old_priority if old_priority is not None else DEFAULT_PROFILE_PRIORITY)
        except ValueError:
            profile["priority"] = DEFAULT_PROFILE_PRIORITY
        if old_priority != profile["priority"]:
            dirty = True
        old_enabled = profile.get("enabled")
        profile["enabled"] = normalize_bool(old_enabled, True)
        if old_enabled != profile["enabled"]:
            dirty = True
        old_opening_mode = profile.get("opening_mode")
        profile["opening_mode"] = normalize_bool(old_opening_mode, False)
        if old_opening_mode != profile["opening_mode"]:
            dirty = True
        old_allowed_users = profile.get("allowed_users")
        allowed_users = normalize_allowed_users(old_allowed_users)
        if old_allowed_users != allowed_users:
            profile["allowed_users"] = allowed_users
            dirty = True
        old_ram_mb = profile.get("recommended_ram_mb")
        try:
            profile["recommended_ram_mb"] = normalize_recommended_ram_mb(
                old_ram_mb if old_ram_mb is not None else DEFAULT_RECOMMENDED_RAM_MB
            )
        except ValueError:
            profile["recommended_ram_mb"] = DEFAULT_RECOMMENDED_RAM_MB
        if old_ram_mb != profile["recommended_ram_mb"]:
            dirty = True
        old_optional_mods = profile.get("optional_mods")
        optional_mods = normalize_optional_mods(old_optional_mods)
        if old_optional_mods != optional_mods:
            profile["optional_mods"] = optional_mods
            dirty = True
        for pattern in DEFAULT_WHITELIST:
            if pattern not in profile["whitelist"]:
                profile["whitelist"].append(pattern)
                dirty = True
        for asset_kind in PROFILE_ASSET_KINDS:
            asset_key = f"{asset_kind}_asset"
            if profile.get(asset_key):
                try:
                    profile[asset_key] = normalize_profile_asset_name(profile[asset_key])
                except ValueError:
                    profile.pop(asset_key, None)
                    dirty = True
        if "server" in profile:
            old_server = profile.get("server")
            try:
                normalized_server = normalize_profile_server(old_server)
            except (TypeError, ValueError):
                profile.pop("server", None)
                dirty = True
            else:
                if normalized_server:
                    profile["server"] = normalized_server
                else:
                    profile.pop("server", None)
                if old_server != normalized_server:
                    dirty = True
        if "local_keep" in profile:
            profile.pop("local_keep", None)
            dirty = True
    if dirty:
        save_profiles(profiles)
    return profiles


def save_profiles(profiles: dict[str, dict[str, Any]]) -> None:
    write_json(profiles_file(), profiles)


def profile_source_dir(slug: str) -> Path:
    return assert_inside(data_dir() / "sources" / slug, data_dir() / "sources")


def profile_build_dir(slug: str) -> Path:
    return assert_inside(data_dir() / "builds" / slug, data_dir() / "builds")


def profile_asset_file(slug: str, filename: str) -> Path:
    slug = profile_key(slug)
    safe_name = normalize_profile_asset_name(filename)
    root = profile_assets_dir(slug)
    path = assert_inside(root / safe_name, root)
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _copy_profile_asset(slug: str, kind: str, source: Path) -> str:
    kind = str(kind or "").strip().lower()
    if kind not in PROFILE_ASSET_KINDS:
        raise ValueError("Profile asset kind must be icon or background")
    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    suffix = source.suffix.lower()
    if suffix not in PROFILE_ASSET_EXTENSIONS:
        raise ValueError("Profile asset must be png, jpg, jpeg, or webp")
    target_dir = profile_assets_dir(slug)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_name = f"{kind}{suffix}"
    target = assert_inside(target_dir / target_name, target_dir)
    for old in target_dir.glob(f"{kind}.*"):
        if old.is_file() and old != target:
            old.unlink()
    shutil.copy2(source, target)
    return target_name


def set_profile_assets(
    slug: str,
    icon: Path | None = None,
    background: Path | None = None,
) -> dict[str, Any]:
    profiles = load_profiles()
    slug = profile_key(slug)
    if slug not in profiles:
        raise KeyError(f"Profile not found: {slug}")
    profile = profiles[slug]
    changed = False
    if icon:
        profile["icon_asset"] = _copy_profile_asset(slug, "icon", icon)
        changed = True
    if background:
        profile["background_asset"] = _copy_profile_asset(slug, "background", background)
        changed = True
    if changed:
        profile["updated_at"] = now_iso()
        profiles[slug] = profile
        save_profiles(profiles)
    return profile


def create_profile(
    minecraft_version: str,
    mod_loader: str,
    loader_version: str,
    name: str,
    recommended_ram_mb: int = DEFAULT_RECOMMENDED_RAM_MB,
    description: str = "",
) -> dict[str, Any]:
    profiles = load_profiles()
    slug = slugify(name)
    if slug in profiles:
        raise ValueError(f"Profile already exists: {slug}")
    minecraft_version, mod_loader, loader_version = normalize_runtime(
        minecraft_version,
        mod_loader,
        loader_version,
    )
    recommended_ram_mb = normalize_recommended_ram_mb(recommended_ram_mb)

    source_dir = profile_source_dir(slug)
    source_dir.mkdir(parents=True, exist_ok=True)
    for child in ("mods", "config", "defaultconfigs", "resourcepacks", "shaderpacks"):
        (source_dir / child).mkdir(exist_ok=True)

    profile = {
        "slug": slug,
        "name": name,
        "description": str(description or "").strip(),
        "minecraft_version": minecraft_version,
        "mod_loader": mod_loader.lower(),
        "loader_version": loader_version,
        "source_dir": stored_source_dir(slug),
        "whitelist": list(DEFAULT_WHITELIST),
        "blacklist": list(DEFAULT_BLACKLIST),
        "priority": DEFAULT_PROFILE_PRIORITY,
        "enabled": True,
        "opening_mode": False,
        "allowed_users": [],
        "recommended_ram_mb": recommended_ram_mb,
        "optional_mods": [],
        "latest_build": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    profiles[slug] = profile
    save_profiles(profiles)
    return profile


def get_profile(slug: str) -> dict[str, Any]:
    profiles = load_profiles()
    slug = profile_key(slug)
    try:
        return profiles[slug]
    except KeyError as exc:
        raise KeyError(f"Profile not found: {slug}") from exc


def list_profiles(
    user: dict[str, Any] | str | None = None,
    include_hidden: bool = False,
) -> list[dict[str, Any]]:
    return sorted(
        (
            profile
            for profile in load_profiles().values()
            if include_hidden or profile_visible_to(profile, user)
        ),
        key=lambda item: (-normalize_priority(item.get("priority")), item["slug"]),
    )


def public_profile(
    profile: dict[str, Any],
    include_server_status: bool = True,
    user: dict[str, Any] | str | None = None,
) -> dict[str, Any]:
    server = normalize_profile_server(profile.get("server"))
    payload = {
        "slug": profile["slug"],
        "name": profile["name"],
        "description": str(profile.get("description") or "").strip(),
        "minecraft_version": profile["minecraft_version"],
        "mod_loader": profile["mod_loader"],
        "loader_version": profile["loader_version"],
        "recommended_ram_mb": normalize_recommended_ram_mb(
            profile.get("recommended_ram_mb", DEFAULT_RECOMMENDED_RAM_MB)
        ),
        "priority": normalize_priority(profile.get("priority", DEFAULT_PROFILE_PRIORITY)),
        "enabled": normalize_bool(profile.get("enabled"), True),
        "opening_mode": normalize_bool(profile.get("opening_mode"), False),
        "launch_allowed": profile_launch_allowed(profile, user),
        "optional_mods": normalize_optional_mods(profile.get("optional_mods")),
        "latest_build": profile.get("latest_build"),
        "server": server,
        "icon_url": profile_asset_url(profile, "icon"),
        "background_url": profile_asset_url(profile, "background"),
        "created_at": profile["created_at"],
        "updated_at": profile["updated_at"],
    }
    if include_server_status and server:
        payload["server_status"] = profile_server_status(profile)
    return payload


def normalize_rule_type(rule_type: str) -> str:
    aliases = {
        "allowlist": "whitelist",
        "allow": "whitelist",
        "whitelist": "whitelist",
        "denylist": "blacklist",
        "deny": "blacklist",
        "blacklist": "blacklist",
    }
    try:
        return aliases[rule_type]
    except KeyError as exc:
        raise ValueError("rule_type must be whitelist or blacklist") from exc


def set_rule(slug: str, rule_type: str, pattern: str, enabled: bool) -> dict[str, Any]:
    rule_type = normalize_rule_type(rule_type)
    profiles = load_profiles()
    slug = profile_key(slug)
    if slug not in profiles:
        raise KeyError(f"Profile not found: {slug}")
    rules = profiles[slug].setdefault(rule_type, [])
    if enabled and pattern not in rules:
        rules.append(pattern)
    if not enabled and pattern in rules:
        rules.remove(pattern)
    profiles[slug]["updated_at"] = now_iso()
    save_profiles(profiles)
    return profiles[slug]


def set_recommended_ram(slug: str, ram_mb: int) -> dict[str, Any]:
    profiles = load_profiles()
    slug = profile_key(slug)
    if slug not in profiles:
        raise KeyError(f"Profile not found: {slug}")
    profiles[slug]["recommended_ram_mb"] = normalize_recommended_ram_mb(ram_mb)
    profiles[slug]["updated_at"] = now_iso()
    save_profiles(profiles)
    return profiles[slug]


def set_profile_description(slug: str, description: str) -> dict[str, Any]:
    profiles = load_profiles()
    slug = profile_key(slug)
    if slug not in profiles:
        raise KeyError(f"Profile not found: {slug}")
    profiles[slug]["description"] = str(description or "").strip()
    profiles[slug]["updated_at"] = now_iso()
    save_profiles(profiles)
    return profiles[slug]


def set_profile_priority(slug: str, priority: int) -> dict[str, Any]:
    profiles = load_profiles()
    slug = profile_key(slug)
    if slug not in profiles:
        raise KeyError(f"Profile not found: {slug}")
    profiles[slug]["priority"] = normalize_priority(priority)
    profiles[slug]["updated_at"] = now_iso()
    save_profiles(profiles)
    return profiles[slug]


def set_profile_enabled(slug: str, enabled: bool) -> dict[str, Any]:
    profiles = load_profiles()
    slug = profile_key(slug)
    if slug not in profiles:
        raise KeyError(f"Profile not found: {slug}")
    profiles[slug]["enabled"] = bool(enabled)
    profiles[slug]["updated_at"] = now_iso()
    save_profiles(profiles)
    return profiles[slug]


def set_profile_opening_mode(slug: str, enabled: bool) -> dict[str, Any]:
    profiles = load_profiles()
    slug = profile_key(slug)
    if slug not in profiles:
        raise KeyError(f"Profile not found: {slug}")
    profiles[slug]["opening_mode"] = bool(enabled)
    profiles[slug]["updated_at"] = now_iso()
    save_profiles(profiles)
    return profiles[slug]


def set_admin_user(username: str, enabled: bool) -> list[str]:
    username = str(username or "").strip()
    if not username:
        raise ValueError("Username is required")
    users = load_admin_users()
    matched = next((item for item in users if item.casefold() == username.casefold()), None)
    if enabled and matched is None:
        users.append(username)
    if not enabled and matched is not None:
        users.remove(matched)
    save_admin_users(users)
    return users


def set_profile_allowed_user(slug: str, username: str, enabled: bool) -> dict[str, Any]:
    profiles = load_profiles()
    slug = profile_key(slug)
    if slug not in profiles:
        raise KeyError(f"Profile not found: {slug}")
    username = str(username or "").strip()
    if not username:
        raise ValueError("Username is required")
    allowed_users = normalize_allowed_users(profiles[slug].get("allowed_users"))
    matched = next((item for item in allowed_users if item.casefold() == username.casefold()), None)
    if enabled and matched is None:
        allowed_users.append(username)
    if not enabled and matched is not None:
        allowed_users.remove(matched)
    profiles[slug]["allowed_users"] = allowed_users
    profiles[slug]["updated_at"] = now_iso()
    save_profiles(profiles)
    return profiles[slug]


def set_profile_server(
    slug: str,
    host: str,
    port: int = server_status.DEFAULT_PORT,
    name: str = "",
) -> dict[str, Any]:
    profiles = load_profiles()
    slug = profile_key(slug)
    if slug not in profiles:
        raise KeyError(f"Profile not found: {slug}")
    server = normalize_profile_server({"host": host, "port": port, "name": name})
    profiles[slug]["server"] = server
    profiles[slug]["updated_at"] = now_iso()
    save_profiles(profiles)
    return profiles[slug]


def clear_profile_server(slug: str) -> dict[str, Any]:
    profiles = load_profiles()
    slug = profile_key(slug)
    if slug not in profiles:
        raise KeyError(f"Profile not found: {slug}")
    profiles[slug].pop("server", None)
    profiles[slug]["updated_at"] = now_iso()
    save_profiles(profiles)
    return profiles[slug]


def clear_profile_build(slug: str) -> None:
    build_root = profile_build_dir(slug)
    if build_root.exists():
        shutil.rmtree(assert_inside(build_root, data_dir() / "builds"))


def set_profile_runtime(
    slug: str,
    minecraft_version: str,
    mod_loader: str,
    loader_version: str | None,
) -> dict[str, Any]:
    profiles = load_profiles()
    slug = profile_key(slug)
    if slug not in profiles:
        raise KeyError(f"Profile not found: {slug}")
    minecraft_version, mod_loader, loader_version = normalize_runtime(
        minecraft_version,
        mod_loader,
        loader_version,
    )
    profile = profiles[slug]
    previous = (
        profile.get("minecraft_version"),
        profile.get("mod_loader"),
        profile.get("loader_version"),
    )
    current = (minecraft_version, mod_loader, loader_version)
    if previous == current:
        return profile

    profile["minecraft_version"] = minecraft_version
    profile["mod_loader"] = mod_loader
    profile["loader_version"] = loader_version
    profile["latest_build"] = None
    profile["updated_at"] = now_iso()
    profiles[slug] = profile
    clear_profile_build(slug)
    save_profiles(profiles)
    return profile


def clone_profile(source_identifier: str, new_name: str) -> dict[str, Any]:
    profiles = load_profiles()
    source_slug = profile_key(source_identifier)
    if source_slug not in profiles:
        raise KeyError(f"Profile not found: {source_identifier}")
    new_slug = slugify(new_name)
    if new_slug in profiles:
        raise ValueError(f"Profile already exists: {new_slug}")

    source_profile = profiles[source_slug]
    source_root = profile_source_root(source_profile)
    target_root = profile_source_dir(new_slug)
    if target_root.exists():
        raise ValueError(f"Target source directory already exists: {target_root}")
    shutil.copytree(source_root, target_root)

    cloned = {
        **source_profile,
        "slug": new_slug,
        "name": new_name,
        "source_dir": stored_source_dir(new_slug),
        "latest_build": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    profiles[new_slug] = cloned
    save_profiles(profiles)
    return cloned


def delete_profile(identifier: str) -> dict[str, Any]:
    profiles = load_profiles()
    slug = profile_key(identifier)
    profile = profiles.pop(slug)
    source_root = profile_source_root(profile)
    build_root = assert_inside(data_dir() / "builds" / slug, data_dir() / "builds")
    if source_root.exists():
        shutil.rmtree(source_root)
    if build_root.exists():
        shutil.rmtree(build_root)
    save_profiles(profiles)
    return profile


def copy_source(slug: str, source: Path, replace: bool = False) -> Path:
    profile = get_profile(slug)
    source_root = source.resolve()
    if not source_root.exists():
        raise FileNotFoundError(source_root)
    target_root = profile_source_root(profile)
    if replace and target_root.exists():
        shutil.rmtree(target_root)
    target_root.mkdir(parents=True, exist_ok=True)

    if source_root.is_file():
        shutil.copy2(source_root, target_root / source_root.name)
        return target_root

    for item in source_root.rglob("*"):
        if item.is_dir():
            continue
        rel = item.relative_to(source_root)
        dest = assert_inside(target_root / rel, target_root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, dest)
    return target_root


def matches_pattern(path: str, pattern: str) -> bool:
    normalized = path.replace("\\", "/")
    normalized_pattern = pattern.replace("\\", "/").strip("/")
    if normalized_pattern in {"*", "**", "**/*"}:
        return True
    if not any(char in normalized_pattern for char in "*?[]"):
        return normalized == normalized_pattern or normalized.startswith(f"{normalized_pattern}/")
    return fnmatch.fnmatch(normalized, normalized_pattern)


def is_internally_excluded(path: str) -> bool:
    return any(matches_pattern(path, pattern) for pattern in DEFAULT_INTERNAL_EXCLUDE)


def file_mode(path: str, profile: dict[str, Any]) -> str:
    blacklist = profile.get("blacklist") or DEFAULT_BLACKLIST
    whitelist = profile.get("whitelist") or DEFAULT_WHITELIST
    if any(matches_pattern(path, pattern) for pattern in blacklist):
        return "enforce"
    if any(matches_pattern(path, pattern) for pattern in whitelist):
        return "seed"
    return "enforce"


def optional_mod_for_path(path: str, optional_mods: list[dict[str, Any]]) -> dict[str, Any] | None:
    matches = [mod for mod in optional_mods if any(matches_pattern(path, pattern) for pattern in mod.get("files") or [])]
    if len(matches) > 1:
        ids = ", ".join(mod["id"] for mod in matches)
        raise ValueError(f"File {path} matches multiple optional mods: {ids}")
    return matches[0] if matches else None


def is_exact_file_pattern(pattern: str, path: str) -> bool:
    normalized = normalize_pack_pattern(pattern)
    return normalized == path and not any(char in normalized for char in "*?[]")


def optional_mod_exact_path(optional_mod: dict[str, Any], path: str) -> str | None:
    for pattern in optional_mod.get("files") or []:
        normalized = normalize_pack_pattern(pattern)
        if is_exact_file_pattern(normalized, path):
            return normalized
    return None


def previous_optional_file_hashes(slug: str) -> dict[tuple[str, str], str]:
    path = profile_build_dir(slug) / "latest.json"
    if not path.exists():
        return {}
    manifest = read_json(path, {})
    result: dict[tuple[str, str], str] = {}
    for item in manifest.get("files", []):
        if not isinstance(item, dict):
            continue
        optional_mod_id = str(item.get("optional_mod") or "").strip()
        rel = normalize_pack_pattern(item.get("path"))
        sha256 = str(item.get("sha256") or "").strip().lower()
        if optional_mod_id and rel and len(sha256) == 64:
            result[(optional_mod_id, rel)] = sha256
    return result


def verify_optional_file_pin(
    optional_mod: dict[str, Any],
    path: str,
    file_hash: str,
    previous_hashes: dict[tuple[str, str], str],
) -> None:
    exact_path = optional_mod_exact_path(optional_mod, path)
    if not exact_path:
        return

    expected_hash = previous_hashes.get((str(optional_mod["id"]), exact_path))
    if expected_hash and expected_hash != file_hash:
        raise ValueError(
            f"Optional mod {optional_mod['id']} pinned hash mismatch for {path}: "
            f"expected {expected_hash}, got {file_hash}. "
            "If this file update is intentional, remove the previous build manifest or use a new exact file path."
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_source_files(profile: dict[str, Any]) -> list[tuple[Path, str]]:
    source_root = profile_source_root(profile)
    if not source_root.exists():
        return []
    result: list[tuple[Path, str]] = []
    for item in sorted(source_root.rglob("*")):
        if item.is_dir():
            continue
        rel = item.relative_to(source_root).as_posix()
        if not is_internally_excluded(rel):
            result.append((item, rel))
    return result


def profile_manifest_signature(profile: dict[str, Any], optional_mods: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for value in (
        profile["slug"],
        profile["minecraft_version"],
        profile["mod_loader"],
        profile["loader_version"],
        str(profile.get("description") or ""),
        str(profile.get("recommended_ram_mb", DEFAULT_RECOMMENDED_RAM_MB)),
        json.dumps(normalize_profile_server(profile.get("server")), sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        profile_asset_url(profile, "icon"),
        profile_asset_url(profile, "background"),
        json.dumps(profile.get("whitelist", []), sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        json.dumps(profile.get("blacklist", []), sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        json.dumps(optional_mods, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def source_file_signature(source_files: list[tuple[Path, str]]) -> str:
    digest = hashlib.sha256()
    for source, rel in source_files:
        stat = source.stat()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def cached_manifest_if_fresh(
    slug: str,
    profile_signature: str,
    source_signature: str,
    base_url: str,
) -> dict[str, Any] | None:
    path = profile_build_dir(slug) / "latest.json"
    if not path.exists():
        return None
    manifest = read_json(path, {})
    if (
        manifest.get("profile_signature") != profile_signature
        or manifest.get("source_signature") != source_signature
    ):
        return None

    base = base_url.rstrip("/")
    cached_files = manifest.get("files") or []
    rewritten_files: list[dict[str, Any]] = []
    for item in cached_files:
        if not isinstance(item, dict):
            return None
        rel = str(item.get("path") or "")
        if not rel:
            return None
        encoded_path = "/".join(quote(part) for part in rel.split("/"))
        rewritten_files.append({**item, "url": f"{base}/files/{quote(slug)}/{manifest['build_id']}/{encoded_path}"})
    return {**manifest, "files": rewritten_files}


def build_profile(slug: str, base_url: str) -> dict[str, Any]:
    profiles = load_profiles()
    slug = profile_key(slug)
    if slug not in profiles:
        raise KeyError(f"Profile not found: {slug}")
    profile = profiles[slug]
    optional_mods = normalize_optional_mods(profile.get("optional_mods"))
    source_files = iter_source_files(profile)
    profile_signature = profile_manifest_signature(profile, optional_mods)
    source_signature = source_file_signature(source_files)
    cached = cached_manifest_if_fresh(slug, profile_signature, source_signature, base_url)
    if cached:
        return cached

    previous_optional_hashes = previous_optional_file_hashes(slug)
    entries: list[dict[str, Any]] = []

    for source, rel in source_files:
        file_hash = sha256_file(source)
        stat = source.stat()
        entry = {"path": rel, "size": stat.st_size, "sha256": file_hash, "mode": file_mode(rel, profile)}
        optional_mod = optional_mod_for_path(rel, optional_mods)
        if optional_mod:
            verify_optional_file_pin(optional_mod, rel, file_hash, previous_optional_hashes)
            entry["optional_mod"] = optional_mod["id"]
            if optional_mod.get("keep_on_disable"):
                entry["optional_keep_on_disable"] = True
        entries.append(entry)

    if profile.get("optional_mods") != optional_mods:
        profile["optional_mods"] = optional_mods

    digest = hashlib.sha256()
    for value in (
        profile["slug"],
        profile["minecraft_version"],
        profile["mod_loader"],
        profile["loader_version"],
        str(profile.get("description") or ""),
        str(profile.get("recommended_ram_mb", DEFAULT_RECOMMENDED_RAM_MB)),
        json.dumps(normalize_profile_server(profile.get("server")), sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        profile_asset_url(profile, "icon"),
        profile_asset_url(profile, "background"),
        json.dumps(optional_mods, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    for entry in entries:
        digest.update(entry["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry["sha256"].encode("ascii"))
        digest.update(entry["mode"].encode("ascii"))
        digest.update(str(entry.get("optional_mod") or "").encode("utf-8"))
        digest.update(str(bool(entry.get("optional_keep_on_disable"))).encode("ascii"))
        digest.update(str(entry["size"]).encode("ascii"))
        digest.update(b"\0")

    content_hash = digest.hexdigest()
    build_id = content_hash[:16]
    build_root = profile_build_dir(slug)
    if build_root.exists():
        shutil.rmtree(assert_inside(build_root, data_dir() / "builds"))
    build_root.mkdir(parents=True, exist_ok=True)

    public_entries: list[dict[str, Any]] = []
    base = base_url.rstrip("/")
    for entry in entries:
        rel = entry["path"]
        encoded_path = "/".join(quote(part) for part in rel.split("/"))
        public_entries.append(
            {
                **entry,
                "url": f"{base}/files/{quote(slug)}/{build_id}/{encoded_path}",
            }
        )

    build_time = now_iso()
    profile["latest_build"] = build_id
    profile["updated_at"] = build_time
    profiles[slug] = profile

    manifest = {
        "schema_version": 1,
        "profile": public_profile(profile, include_server_status=False),
        "build_id": build_id,
        "content_hash": content_hash,
        "profile_signature": profile_signature,
        "source_signature": source_signature,
        "created_at": build_time,
        "file_count": len(public_entries),
        "total_size": sum(item["size"] for item in public_entries),
        "rules": {
            "whitelist": profile.get("whitelist", []),
            "blacklist": profile.get("blacklist", []),
            "internal_exclude": list(DEFAULT_INTERNAL_EXCLUDE),
        },
        "optional_mods": optional_mods,
        "files": public_entries,
    }
    write_json(build_root / "latest.json", manifest)

    save_profiles(profiles)
    return manifest


def latest_manifest(slug: str) -> dict[str, Any]:
    slug = profile_key(slug)
    path = profile_build_dir(slug) / "latest.json"
    if not path.exists():
        raise FileNotFoundError(f"No build for profile: {slug}")
    return read_json(path, {})


def manifest_for(slug: str, build_id: str) -> dict[str, Any]:
    manifest = latest_manifest(slug)
    if manifest.get("build_id") != build_id:
        raise FileNotFoundError(f"No manifest: {slug}/{build_id}")
    return manifest


def file_for(slug: str, build_id: str, file_path: str) -> Path:
    slug = profile_key(slug)
    profile = get_profile(slug)
    manifest = latest_manifest(slug)
    if manifest.get("build_id") != build_id:
        raise FileNotFoundError(f"No file build: {slug}/{build_id}")

    rel = file_path.replace("\\", "/").strip("/")
    if not rel or is_internally_excluded(rel):
        raise FileNotFoundError(file_path)
    manifest_files = {item["path"] for item in manifest.get("files", [])}
    if rel not in manifest_files:
        raise FileNotFoundError(file_path)

    root = profile_source_root(profile)
    target = assert_inside(root / rel, root)
    if not target.is_file():
        raise FileNotFoundError(file_path)
    return target


def release_platform_aliases(platform: str) -> list[str]:
    platform = str(platform or "").strip().lower()
    aliases = [platform] if platform else []
    if platform == "windows":
        aliases.append("windows-x64")
    elif platform == "linux":
        aliases.append("linux-x64")
    elif platform == "macos":
        aliases.extend(["macos-arm64", "macos-x64", "darwin"])
    elif platform.startswith("windows-"):
        aliases.append("windows")
    elif platform.startswith("macos-"):
        aliases.extend(["macos", "darwin"])
    elif platform.startswith("linux-"):
        aliases.append("linux")
    return aliases


def release_file(platform: str) -> Path:
    normalized = str(platform or "").strip().lower()
    if not normalized:
        raise ValueError("Release platform is required")
    return data_dir() / "releases" / f"latest-{normalized}.json"


def write_release(
    version: str,
    url: str,
    sha256: str,
    platform: str,
    notes: str = "",
    display_version: str = "",
    update_id: str = "",
    compat_version: str = "",
) -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    if not platform:
        raise ValueError("Release platform is required")
    display_version = str(display_version or version or "").strip().lstrip("vV")
    update_id = str(update_id or "").strip()
    compat_version = str(compat_version or "").strip().lstrip("vV")
    update_version = compat_version or (f"9999.{update_id}" if update_id.isdigit() else str(version).strip().lstrip("vV"))
    release = {
        "version": update_version,
        "display_version": display_version,
        "platform": platform,
        "url": url,
        "sha256": sha256,
        "notes": notes,
        "created_at": now_iso(),
    }
    if update_id:
        release["update_id"] = update_id
    write_json(release_file(platform), release)

    index_path = data_dir() / "releases" / "latest.json"
    index = read_json(index_path, {})
    if not isinstance(index, dict) or not isinstance(index.get("releases"), dict):
        old_release = index if isinstance(index, dict) and index.get("url") else None
        index = {"version": version, "releases": {}, "created_at": now_iso()}
        if old_release and old_release.get("platform"):
            index["releases"][str(old_release["platform"]).strip().lower()] = old_release
    index["version"] = version
    index["updated_at"] = release["created_at"]
    index.setdefault("created_at", release["created_at"])
    index["releases"][platform] = release
    write_json(index_path, index)
    return release


def latest_release(platform: str | None = None) -> dict[str, Any] | None:
    if platform:
        for alias in release_platform_aliases(platform):
            path = release_file(alias)
            if path.exists():
                return read_json(path, {})

        index_path = data_dir() / "releases" / "latest.json"
        if index_path.exists():
            index = read_json(index_path, {})
            releases = index.get("releases") if isinstance(index, dict) else None
            if isinstance(releases, dict):
                for alias in release_platform_aliases(platform):
                    release = releases.get(alias)
                    if isinstance(release, dict):
                        return release
            if isinstance(index, dict) and index.get("url"):
                release_platform = str(index.get("platform") or "windows").strip().lower()
                if release_platform in release_platform_aliases(platform):
                    return index
        return None

    path = data_dir() / "releases" / "latest.json"
    if not path.exists():
        return None
    return read_json(path, {})
