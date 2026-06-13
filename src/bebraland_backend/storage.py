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


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_RECOMMENDED_RAM_MB = 2048
MIN_RECOMMENDED_RAM_MB = 512
MAX_RECOMMENDED_RAM_MB = 65536
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
    "launcher_accounts.json"
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


def data_dir() -> Path:
    return Path(os.environ.get("BEBRALAND_DATA_DIR", ROOT_DIR / "data")).resolve()


def profiles_file() -> Path:
    return data_dir() / "profiles.json"


def ensure_data_dirs() -> None:
    for child in ("sources", "builds", "releases"):
        (data_dir() / child).mkdir(parents=True, exist_ok=True)


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


def load_profiles() -> dict[str, dict[str, Any]]:
    ensure_data_dirs()
    profiles = read_json(profiles_file(), {})
    if not isinstance(profiles, dict):
        raise ValueError("profiles.json must contain object")
    dirty = False
    for profile in profiles.values():
        if "whitelist" not in profile:
            profile["whitelist"] = list(DEFAULT_WHITELIST)
            dirty = True
        if "blacklist" not in profile:
            profile["blacklist"] = list(DEFAULT_BLACKLIST)
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
        for pattern in DEFAULT_WHITELIST:
            if pattern not in profile["whitelist"]:
                profile["whitelist"].append(pattern)
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


def create_profile(
    minecraft_version: str,
    mod_loader: str,
    loader_version: str,
    name: str,
    recommended_ram_mb: int = DEFAULT_RECOMMENDED_RAM_MB,
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
        "minecraft_version": minecraft_version,
        "mod_loader": mod_loader.lower(),
        "loader_version": loader_version,
        "source_dir": str(source_dir),
        "whitelist": list(DEFAULT_WHITELIST),
        "blacklist": list(DEFAULT_BLACKLIST),
        "recommended_ram_mb": recommended_ram_mb,
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


def list_profiles() -> list[dict[str, Any]]:
    return sorted(load_profiles().values(), key=lambda item: item["slug"])


def public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": profile["slug"],
        "name": profile["name"],
        "minecraft_version": profile["minecraft_version"],
        "mod_loader": profile["mod_loader"],
        "loader_version": profile["loader_version"],
        "recommended_ram_mb": normalize_recommended_ram_mb(
            profile.get("recommended_ram_mb", DEFAULT_RECOMMENDED_RAM_MB)
        ),
        "latest_build": profile.get("latest_build"),
        "created_at": profile["created_at"],
        "updated_at": profile["updated_at"],
    }


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
    source_root = assert_inside(Path(source_profile["source_dir"]), data_dir() / "sources")
    target_root = profile_source_dir(new_slug)
    if target_root.exists():
        raise ValueError(f"Target source directory already exists: {target_root}")
    shutil.copytree(source_root, target_root)

    cloned = {
        **source_profile,
        "slug": new_slug,
        "name": new_name,
        "source_dir": str(target_root),
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
    source_root = assert_inside(Path(profile["source_dir"]), data_dir() / "sources")
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
    target_root = assert_inside(Path(profile["source_dir"]), data_dir() / "sources")
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_source_files(profile: dict[str, Any]) -> list[tuple[Path, str]]:
    source_root = Path(profile["source_dir"]).resolve()
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


def build_profile(slug: str, base_url: str) -> dict[str, Any]:
    profiles = load_profiles()
    slug = profile_key(slug)
    if slug not in profiles:
        raise KeyError(f"Profile not found: {slug}")
    profile = profiles[slug]
    entries: list[dict[str, Any]] = []

    for source, rel in iter_source_files(profile):
        file_hash = sha256_file(source)
        stat = source.stat()
        entries.append({"path": rel, "size": stat.st_size, "sha256": file_hash, "mode": file_mode(rel, profile)})

    digest = hashlib.sha256()
    for value in (
        profile["slug"],
        profile["minecraft_version"],
        profile["mod_loader"],
        profile["loader_version"],
        str(profile.get("recommended_ram_mb", DEFAULT_RECOMMENDED_RAM_MB)),
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    for entry in entries:
        digest.update(entry["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry["sha256"].encode("ascii"))
        digest.update(entry["mode"].encode("ascii"))
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
        "profile": public_profile(profile),
        "build_id": build_id,
        "content_hash": content_hash,
        "created_at": build_time,
        "file_count": len(public_entries),
        "total_size": sum(item["size"] for item in public_entries),
        "rules": {
            "whitelist": profile.get("whitelist", []),
            "blacklist": profile.get("blacklist", []),
            "internal_exclude": list(DEFAULT_INTERNAL_EXCLUDE),
        },
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

    root = assert_inside(Path(profile["source_dir"]), data_dir() / "sources")
    target = assert_inside(root / rel, root)
    if not target.is_file():
        raise FileNotFoundError(file_path)
    return target


def write_release(version: str, url: str, sha256: str, platform: str, notes: str = "") -> dict[str, Any]:
    release = {
        "version": version,
        "platform": platform,
        "url": url,
        "sha256": sha256,
        "notes": notes,
        "created_at": now_iso(),
    }
    write_json(data_dir() / "releases" / "latest.json", release)
    return release


def latest_release() -> dict[str, Any] | None:
    path = data_dir() / "releases" / "latest.json"
    if not path.exists():
        return None
    return read_json(path, {})
