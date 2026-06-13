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
BEBRALAND_AUTHLIB_SERVER_NAME=BebraLand
BEBRALAND_SKIN_DOMAINS=
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
profile runtime PaperlandIterion 1.21.1 neoforge 21.1.227
profile hotswap PaperlandIterion 1.20.1 forge 47.4.0
profile loader PaperlandIterion 1.21.1 vanilla
profile ram PaperlandIterion 4096
profile clone PaperlandIterion NewPaperlandIterion
profile list
profile delete PaperlandIterion
build PaperlandIterion
serve
```

`profile runtime`, `profile hotswap`, and `profile loader` edit the same profile slug in place. Use them when you need to move a pack from Forge to NeoForge, change loader version, or change Minecraft version. The source folder is kept, the old generated build cache is cleared, and the next launcher `Play` request rebuilds the manifest with the new runtime metadata. Connected launchers receive `profiles.changed` from the running backend after `data/profiles.json` changes.

Frontend talks to backend over WebSocket at `/api/v1/ws`. On Play it sends `profile.latest`; backend rebuilds manifest from current profile folder, hashes files, and frontend downloads only missing or changed pack files. Backend stores only current hash manifest in `data/builds/<slug>/latest.json`; pack files stay only in `data/sources/<slug>` and `/files/...` serves them from there. Old build folders are removed on next build. Backend never serves Minecraft `assets`, `libraries`, or `versions`.

When profiles or builds change through shell/CLI, the running backend watches `data/profiles.json` and `data/builds/*/latest.json`, then pushes `profiles.changed` to every connected launcher.

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

## Optional Mods

Optional mods are configured directly in `data/profiles.json` per profile. Files stay in the normal source folder, but matching optional files are only installed when the player enables that optional mod in the launcher.

Example:

```json
{
  "paperland-iterion": {
    "optional_mods": [
      {
        "id": "voxy",
        "name": "Voxy",
        "description": "Client-side distant terrain renderer.",
        "default_enabled": true,
        "files": ["mods/voxy-0.2.14-alpha-c54de23.jar"]
      },
      {
        "id": "voxy-server",
        "name": "Voxy Server",
        "description": "Server helper for Voxy.",
        "default_enabled": false,
        "files": ["mods/VoxyServer-1.1.5.jar"],
        "requires": ["voxy"]
      }
    ]
  }
}
```

Fields:

- `id`: stable id saved by launcher settings.
- `name`: label shown to players.
- `description`: shown in launcher details/tooltip.
- `default_enabled`: default for players without saved choice.
- `files`: exact file paths or path patterns inside the pack source folder.
- `requires`: optional mod ids to auto-enable with this mod.
- `conflicts`: optional mod ids to turn off when this mod is enabled.
- `keep_on_disable`: keep matched files when player disables the mod; default is `false`.

Aliases also work: `paths`/`patterns` for `files`, `depends_on`/`dependencies` for `requires`, and `enabled_by_default`/`default` for `default_enabled`.

For best protection, use exact jar paths like `mods/voxy-0.2.14-alpha-c54de23.jar`. Wildcards like `mods/voxy-*.jar` still work, but they cannot be pinned safely because they may match a new file. Exact optional files are pinned by the previous `data/builds/<slug>/latest.json`: if the same exact path changes later, `build` fails until you remove the old build manifest intentionally or change the file path.

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

## authlib-injector / server whitelist

Backend exposes an authlib-injector compatible Yggdrasil API at:

```text
<BEBRALAND_PUBLIC_BASE_URL>/api/yggdrasil/
```

Launcher uses this API automatically. It launches Minecraft with the Azuriom access token, the Azuriom username, and a stable Minecraft UUID. The backend verifies that token when Minecraft joins a server. If a player uses normal Minecraft without this launcher, the join check is never registered in backend, so a server using this authlib API rejects the login.

Install the Azuriom Skin API resource and enable capes there if you need capes. Skins/capes are read from:

```text
GET <AZURIOM_URL>/api/skin-api/profile/{user_name}
```

For the Minecraft server:

1. Download `authlib-injector.jar` from `https://authlib-injector.yushi.moe/`.
2. Put it near your server jar.
3. Set `online-mode=true` in `server.properties`.
4. For Minecraft 1.19+, set `enforce-secure-profile=true`.
5. Add the javaagent before `-jar`:

```powershell
java -javaagent:authlib-injector.jar=https://your-backend.example/api/yggdrasil/ -jar server.jar nogui
```

Backend also exposes the exact server config:

```text
GET /api/v1/authlib/config
```

`BEBRALAND_PUBLIC_BASE_URL` must be a public URL reachable by the Minecraft server and players. Backend generates and stores its texture signing key in `data/authlib/rsa_key.json`; keep that file stable between restarts.

## Launcher updates

Frontend asks over WebSocket:

```text
{"type":"launcher.update","payload":{"current_version":"0.1.0","platform":"windows-x64"}}
```

Write update metadata after publishing each platform binary:

```powershell
uv run bebraland-backend release write 0.1.1 "https://github.com/ORG/REPO/releases/download/v0.1.1/BebraLandLauncher-windows-x64.exe" "SHA256_HERE" --platform windows-x64
uv run bebraland-backend release write 0.1.1 "https://github.com/ORG/REPO/releases/download/v0.1.1/BebraLandLauncher-linux-x64" "SHA256_HERE" --platform linux-x64
uv run bebraland-backend release write 0.1.1 "https://github.com/ORG/REPO/releases/download/v0.1.1/BebraLandLauncher-macos-arm64" "SHA256_HERE" --platform macos-arm64
uv run bebraland-backend release write 0.1.1 "https://github.com/ORG/REPO/releases/download/v0.1.1/BebraLandLauncher-macos-x64" "SHA256_HERE" --platform macos-x64
```

## Backend runtime

Backend is intended to run directly through `uv`:

```powershell
uv run bebraland-backend
```

No backend EXE is required.
