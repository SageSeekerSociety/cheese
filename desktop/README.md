# Cheese desktop

The desktop app is the web app, loaded from the server in the system's own web
view (Tauri). The window opens on the app's own page (`shell/index.html`): the
brand tile, moving while it waits for the server, and a notice with automatic
retries when the server cannot be reached. Once the server answers, the window
goes to the web app, whose first frame in the app is the same tile
(`frontend/index.html`); `frontend/src/lib/desktopSplash.ts` then lands it in
the rail. The window keeps its size and position between launches.

On macOS the title bar is drawn over the page: the window buttons sit at the
left end of the web app's top bar, which leaves them room, and the top bar and
the sign-in pages' side pane move the window. Elsewhere the system title bar
stays. The web app tells the app the theme picked in it (`set_theme`), which
colours the title bar and the app's own page from the next launch. What the
app tells the page about its window is `window.__CHEESE_APP__`
(`frontend/src/lib/desktopApp.ts`); a page never calls a command an older app
lacks. Signed out, the app opens on sign-in rather than the public home page.

Closing the window keeps the app running (`src-tauri/src/resident.rs`); the Dock
icon on macOS, the tray icon elsewhere, or opening the app again brings the
window back, and quitting is ⌘Q or 退出 in the tray menu. While it runs, it keeps its own
connection to the server for the person's notices (`src-tauri/src/notices.rs`,
`docs/topics/浏览器推送.md`): the signed-in page hands it a credential that opens
nothing else, and from then on what a browser push would have said shows as a
system notification the moment it happens, opening its room when clicked,
whether or not the window is open. The connection reconnects after sleep or a
network change and catches up on what it missed. The count of things waiting
shows on the Dock icon (a dot on the Windows taskbar, and in the tray menu). Opening at login is off until the person turns it on
under 设置 → 通用, a section only the app shows; launched that way the app
starts out of sight. What the app can do is listed in
`__CHEESE_APP__.can`, so a page never asks an older app for more.

The app keeps itself current (`src-tauri/src/updates.rs`). It looks for a new
version at launch, every six hours after, and when asked: 检查更新 in the
page's 关于知是 dialog (帮助与反馈 menu), or, on macOS, "Check for Updates…" in
the app menu, whose "About Cheese" opens the same dialog. The dialog shows the
app's version and the commit the web build was made from. A new version
downloads at once; the top bar then shows 重启以完成更新, and clicking it
restarts into the new version. Left alone, the app installs it once the window
is out of sight, and the restarted app stays out of sight. Neither happens while
this computer is being connected.

Inside the app the pages written for people who have not installed it yet — the
public home page, 了解知是, 方案 and the download page — go back into the app
(`frontend/src/router/home.ts`); the user menu offers the phone's QR code
instead of the download, and the docs site at `/docs/` opens in the browser,
since the window has no way back from it.

Signing in, signing up and resetting a password happen in the person's
browser (RFC 8252), where their saved passwords, passkeys and provider accounts
are and where Google agrees to show its page at all; so does connecting
GitHub, Feishu or an MCP server. In the app those pages only open the browser
(`frontend/src/views/account/DesktopSignIn.vue`, `appSignIn.ts`). The browser hands the result back through a `cheese://open?path=<page>` link
(`src-tauri/src/links.rs`), which brings the window back on that page of the
server and nowhere else. A connection started in the app says so on its first
request, and its callback lands the browser on `/account/to-app`, which opens
the app on the page with the result (`backend/app/api/app_return.py`). A
sign-in is handed over, once the person confirms the account in the browser,
as a code that is good once, for five minutes, and only with a secret the app
kept and the browser never saw (`POST /users/auth/app-sign-in` and
`.../finish`); the browser stays signed in too.

Beyond that the app adds one thing a browser cannot do: connect the computer it runs
on as a device. At the first sign-in it asks once whether to; after that the
choice lives under 设置 → 桌面端 → 这台设备, which also names the device, offers
it to teams, logs in the person's own Claude Code and disconnects it. Connecting
does what the page otherwise asks a terminal user to do — run the server's
`install.sh`, then `cheesehost link connect` — and approves the login with the
session the page is already signed in with. The app reports each step, how far
the connector's download has got and why it stopped as ids (`src-tauri/src/connect.rs`),
and every step can be cancelled; the page words them (`frontend/src/lib/desktop.ts`,
`frontend/src/views/desktop/DeviceConnect.vue`). A computer connected before
comes back on its own at launch. Logging in Claude Code runs `cheesehost claude
login` with no terminal: it opens the browser and finishes there.

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
`desktop-latest` release, which is where the download page (`/download`) points.
`CHANGELOG.md` goes up with each build; the download page shows it, so a
change to the app adds a line or two there, written for the people who use it.
Each build is version `0.1.<run number>`, and the publish step uploads
`latest.json` last, which is what an installed app checks (see above). The updates are signed with a key held in
the repository secrets `TAURI_SIGNING_PRIVATE_KEY` and
`TAURI_SIGNING_PRIVATE_KEY_PASSWORD`; the app trusts only its public half
(`plugins.updater.pubkey` in `tauri.conf.json`). Losing that key means no
installed app can be updated again, so keep a copy outside GitHub.
Neither is signed by a developer certificate, so the first open is stopped by
the system — on macOS until the user allows it under 系统设置 → 隐私与安全性,
on Windows at the SmartScreen prompt (更多信息 → 仍要运行).
