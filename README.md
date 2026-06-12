# BebraLand Launcher Backend

Python backend for BebraLand modpack builds.

## Run with uv

```powershell
cd "C:\Users\aurum\Desktop\custom bebraland launcher\BebraLand Launcher Backend"
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
$env:UV_PYTHON_INSTALL_DIR = "$PWD\.uv-python"
uv sync
uv run bebraland-backend
```

No args opens interactive shell.

Config lives in `.env`:

```env
BEBRALAND_HOST=127.0.0.1
BEBRALAND_PORT=8765
BEBRALAND_PUBLIC_BASE_URL=http://127.0.0.1:8765
AZURIOM_URL=https://your-azuriom-site.example
```

## Profiles

Create profile:

```text
profile create <mc_version> <modloader> <modloader_version> <name> [--ram-mb 2048]
```

Example:

```text
profile create 1.21.1 neoforge 21.1.227 PaperlandIterion --ram-mb 2048
profile path PaperlandIterion
```

Backend creates a pack folder only. Put server-side `mods`, `config`, `defaultconfigs`, and other pack files into printed folder. Minecraft client, assets, libraries, and modloader are installed by frontend on each player's PC through `minecraft-launcher-lib`. `--ram-mb` stores recommended RAM for launcher UI; default is `2048`.

Other commands:

```text
profile ram PaperlandIterion 4096
profile clone PaperlandIterion NewPaperlandIterion
profile list
profile delete PaperlandIterion
build PaperlandIterion
serve
```

Frontend asks backend for latest manifest on Play. Backend rebuilds manifest from current profile folder, hashes files, and frontend downloads only missing or changed pack files. Backend stores only current hash manifest in `data/builds/<slug>/latest.json`; pack files stay only in `data/sources/<slug>` and `/files/...` serves them from there. Old build folders are removed on next build. Backend never serves Minecraft `assets`, `libraries`, or `versions`.

## Sync Rules

By default every pack file is enforced: if player file is missing or hash differs, launcher downloads server version. Extra local pack files are removed unless they match whitelist.

Whitelist means "seed once and do not delete": if file is missing, launcher downloads it once; if player changes it later, launcher keeps player version. Extra local files inside whitelisted folders are kept.

Blacklist means "always enforce", and it wins over whitelist.

```text
profile whitelist-add paperland-iterion config
profile whitelist-add paperland-iterion options.txt
profile blacklist-add paperland-iterion config/locked.json
```

Internal exclude always skips `.git`, cache, temp, logs, crash reports, and Minecraft client folders: `assets`, `libraries`, `versions`, `runtime`, `runtimes`.

## Azuriom auth

Enable AzAuth in Azuriom admin: Settings -> Authentication.

Backend uses Azuriom HTTP API:

- `POST <AZURIOM_URL>/api/auth/authenticate`
- `POST <AZURIOM_URL>/api/auth/verify`
- `POST <AZURIOM_URL>/api/auth/logout`

Set site URL in `.env` before starting backend:

```env
AZURIOM_URL=https://your-azuriom-site.example
```

Frontend sends email/password/2FA to backend. Backend authenticates against Azuriom and verifies token server-side.

## Launcher updates

Frontend asks:

```text
GET /api/v1/launcher/update?current_version=0.1.0&platform=windows
```

Write update metadata after publishing new EXE:

```powershell
uv run bebraland-backend release write 0.1.1 "https://github.com/ORG/REPO/releases/download/v0.1.1/BebraLandLauncher.exe" "SHA256_HERE"
```

## Backend runtime

Backend is intended to run directly through `uv`:

```powershell
uv run bebraland-backend
```

No backend EXE is required.
