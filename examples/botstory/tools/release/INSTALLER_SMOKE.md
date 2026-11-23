# Installer smoke run (design, not run) - WORLD_SYSTEMS_PLAN I4.2

Second smoke run beside `public-smoke.ps1`: the client made by an installer instead of by hand. Two installers
exist, and they differ in what "both ways" means:

| Installer | Where | WZ choice | Side effects on the PC |
|---|---|---|---|
| Public self-host client installer | `BotStory Server\installer\setup.iss` (in the public tree; `installer\build.ps1`) | none: it *detects* the folder's set (`DetectBase`: cosmic / repack / stock / other) | edits only the chosen client folder (cleanup, LAA flag + `botstory-backup\`, `config.ini`), optional Residents step writes the server's `.env` |
| Friends installer | `FriendServer\installer\setup.iss` (private, `build_installer.bat`, `make_release.ps1`) | `/BASE=repack` (default) or `/BASE=cosmic` (writes `base=cosmic` to `config.ini`; the launcher then takes 11 WZ files from `<patch_url>/bases/cosmic/manifest.txt`) | Defender exclusions, Tailscale install + `tailscale up`, `%ProgramData%\MapleCommons`, a weekly scheduled task, shortcuts |

The friends installer changes security settings and joins a tailnet, so it must never run on the host. Its
smoke run belongs in **Windows Sandbox** (or a throwaway VM) only.

## Run A: public installer, both WZ sets (host is fine, scratch folders only)

1. `public-smoke.ps1 -KeepServer` (server half on 18584, fresh DB).
2. Build the installer from the exported tree: `tree\installer\build.ps1` (Inno Setup 6). Check that the build
   needs nothing the scrub dropped.
3. Make two client copies under `%TEMP%\botstory-installer-smoke\`: `cosmic\` (v83 exe and dlls plus the cosmic-wz
   root WZ, hardlinked) and `repack\` (the same exe plus the Community Repack WZ, hardlinked). A third,
   `stock\` (plain v83 WZ), checks that the installer warns and stops.
4. For each one: `BotStory-Setup-x.y.z.exe /VERYSILENT /SUPPRESSMSGBOXES /DIR=<copy> /HOST=127.0.0.1 /PORT=18584 /LOG=<copy>\setup.log`.
   Then assert:
   - the log's detected base matches the copy;
   - `config.ini` has host/port and no `patch_url` or `base` line;
   - `HShield`, `ASPLnchr.exe` and `Patcher.exe` are gone;
   - the LAA bit is set and the original is in `botstory-backup\`;
   - `Launch Game.exe` and its dll and `Custom.wz` are present.
   Skip the Residents step: `resident_files` stays off, which is the public default.
5. Run `public-smoke-client.ps1 -ClientDir <copy>` for each one. Each run logs in, opens the Resident menu and
   the Crew window, walks three towns and asks the Coke Town gate. It fails on a client exit or on a
   `STOCK-CLIENT-LACKS` line. On the repack copy, also answer "Repack files" at the gate and enter Coke Town
   (219000000). There the map line is expected, so the check needs an allow-list for that one step.
6. Back up `HKCU\Software\Wizet\MapleStory` before each run and restore it afterwards. Delete the copies.

## Run B: friends installer `/BASE=` both ways (Windows Sandbox only)

1. A `.wsb` file maps `make_release.ps1`'s output read-only, plus an output folder. Networking stays on and
   reaches only a patch server the host starts on a non-live port (`python patch-server\serve.py --root <a
   small fixture Game Files> --port 18585`).
2. In the sandbox, run `BotStory-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /BASE=cosmic /LOG=out\cosmic.log`,
   then a second fresh sandbox with `/BASE=repack`. Set `installer_config.ini` keymint blank, so the
   Tailscale step fails visibly, which is expected and asserted.
3. Assert:
   - the install log has `WZ set preselected from /BASE: cosmic` or `repack`;
   - `config.ini` has `base=cosmic`, or no base line for repack;
   - on the first `Launch Game.exe` run, the patch server's access log shows a request for
     `bases/cosmic/manifest.txt` (cosmic run) or none (repack run);
   - also `/BASE=bogus` logs `Ignoring /BASE=bogus`.
4. The game itself is not played in the sandbox (no GPU or DirectX guarantees there). Run A covers the
   in-game half.

## Not covered

A GitHub runner cannot run the client or Inno Setup's output against a server, so both runs stay local. CI
(`run-build.yml`) builds and tests only the pushed server branch.
