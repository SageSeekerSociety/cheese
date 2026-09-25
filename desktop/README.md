# Cheese desktop

The desktop app is the web app, loaded from the server in the system's own web
view (Tauri), plus one thing a browser cannot do: connect the computer it runs
on as a device. On 「我的设备」 it offers 「接入这台电脑」, which does what the
page otherwise asks a terminal user to do — run the server's `install.sh`, then
`cheesehost link connect` — and approves the login with the session the page is
already signed in with. The page's side of that is `frontend/src/lib/desktop.ts`.

What each platform needs before cheesehost can run (`src-tauri/src/platform/`):

- **macOS.** cheesehost runs natively. A Mac without Homebrew has no tmux, so the
  app carries one (`scripts/build-tmux.sh`, system libraries only) and places it
  where cheesehost looks for a private copy. git and python3 come from Apple's
  command line tools; the app asks macOS to install them when they are missing.
- **Windows.** cheesehost runs natively. The app downloads `cheesehost.exe`
  into `%LOCALAPPDATA%\cheese\bin`, where it can update itself; at `link
  connect` cheesehost fetches what the server's commands need (python3 and
  Git for Windows' shell) from the server and keeps itself running with a
  per-user login entry. No WSL and no administrator rights.

```bash
pnpm install
sh scripts/build-tmux.sh arm64 resources/tmux          # macOS only; x86_64 for Intel
CHEESE_ORIGIN=http://localhost:5200 pnpm tauri dev     # defaults to https://okcheese.com
(cd src-tauri && cargo test)
pnpm tauri build
```

`CHEESE_ORIGIN` is read when the app is compiled: the app talks to one server,
and only that origin's pages can call it.

`.github/workflows/desktop.yml` builds the two macOS dmgs and the Windows
installer on every change here and, on main, replaces the assets of the
`desktop-latest` release, which is where the download links on 「我的设备」 point.
Each build is version `0.1.<run number>`, and the publish step uploads
`latest.json` last. An installed app checks it at every start and, when a
newer version is out, downloads it, waits for any connection in progress to
finish, installs it and restarts. The updates are signed with a key held in
the repository secrets `TAURI_SIGNING_PRIVATE_KEY` and
`TAURI_SIGNING_PRIVATE_KEY_PASSWORD`; the app trusts only its public half
(`plugins.updater.pubkey` in `tauri.conf.json`). Losing that key means no
installed app can be updated again, so keep a copy outside GitHub.
Neither is signed by a developer certificate, so the first open is stopped by
the system — on macOS until the user allows it under 系统设置 → 隐私与安全性,
on Windows at the SmartScreen prompt (更多信息 → 仍要运行).
