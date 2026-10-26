# 设备自托管：让芝士跑在自己的机器上

Enrolled devices provide execution for ordinary rooms: project files, shell commands, environment scripts, custom stdio MCP processes and preview. Claude Code runs, under its runner, on the central session host configured by `AGENT_SESSION_DEVICE_ID`. Both locations use the existing connector, with separate recorded placement and storage. See `remote-execution.md` for setup and migration.

设备是**纯算力**：它不含 agent 身份，agent 由项目/频道在请求开始时独立解析（agent = 屏幕，不是机器）。设备永远向服务器**拨出**（非入站），带退避重连 + 心跳，所以躲在 NAT/防火墙后的笔记本也能托管 agent、**零入站端口**。

---

## 0. Hosted 机器不是我们的

> 执行环境（沙箱，还是整台机器）见 **#2320** 和 `where-a-turn-runs.md` §四。本节只讲**已落地、改代码必须守住**的一条约束。

只适用于 **Hosted**（人接入的常驻机器）：那是别人的笔记本、别人的 `claude`，他借我们算力，不是把机器交给我们。**Cloud 不适用**——平台按频道开的一次性机器，随便处置。

**判据：除了我们放进去的东西，机器上任何一样在我们来之前是什么样，走之后还是什么样。**

- Cheese installs its Claude build at `~/.cheese/claude/versions/<pin>`. It leaves the owner's version store and `~/.local/bin/claude` unchanged.
- Session configuration, home and worktrees live under Cheese-owned directories. Machine credentials are read from the owner's configuration under the existing authorization; the launcher writes neither the owner's `~/.claude` nor project configuration. Native skills live in the session's `$CLAUDE_CONFIG_DIR/skills/`.
- This boundary applies to the Cheese integration. A session on an enrolled machine runs its tools in a bubblewrap sandbox (a `team_devices` spec), and over the whole machine only when an administrator of the device's team chose that machine for it (`self_hosted`, `where-a-turn-runs.md` §四). Nothing is installed with the owner's sudo: a Linux machine without unprivileged user namespaces says so on its team's devices page and is given no sandbox.
- 装在**他能写的目录**——否则自更新永远失败，且无声（#501）。

### 为什么这条特别容易破

破坏它的代码看起来都很合理：一个 symlink 让 `claude` 可用、一个 `sudo` 让安装成功、复用系统 tmux 省一个 socket。而反馈回路是断的——#489、#501、`systemctl kill` 带走 20 个会话，**全都只存在于没人看的地方**（一个 plist 的 stderr、一条 journald、一台"健康"的离线机器），平台侧一律正常。

所以问的不是"这样能不能 work"，而是：**这步在用户机器上做错了，谁会发现？** 答案若是"他，几周后，且不会联想到我们"，换做法。

### agent 会话住在 connector 自己的 tmux server 里

On the central session host, the launcher keeps each session's runner, and the headless Claude Code it holds, in the connector's private tmux server. It reads that server's socket from `$TMUX` before attaching the inner session. The execution device runs its service independently of the session. Sessions awaiting migration retain their original private tmux server until transfer.

The launcher neither queries nor terminates sessions on the default server. A `cheese_*` name alone does not establish ownership.

**"重启一下 connector"是非破坏性操作**，靠三件事一起成立，缺一件就不成立：

| | 没有它会怎样 |
|---|---|
| 会话在我们自己的 socket 上 | 机主一句 `tmux kill-server` 就全清；反过来我们也清他的 |
| connector 退出时只**释放**不拆（放开 viewer pty / runtime，tmux 会话原样留着） | `systemctl stop` 走 deferred 拆除路径，屏幕全没 |
| unit 里的 `KillMode=process` | systemd 默认 `control-group`，`stop`/`restart` 一律 SIGTERM 整个 cgroup，而 tmux server 就在里面（2026-08-17 实测：一次带走 20 个会话，6 个正在干活） |

下次启动会 re-adopt 活着的会话（`HasSession` 分支），会话里的 runner 一直在记它的日志，后端重连之后从上次读到的地方接着读。

第三行只对 systemd 说话，**macOS 不需要对应物**：tmux server 一起来就 daemonize（实测 tmux 3.5a：PPID 1、自成进程组），而 launchd 拆 job 只管 job 自己的进程组，于是 server 和里面的会话原样活着，`launchctl bootout` 之后 `has-session` 仍然成立。Linux 非要那一行，是因为 cgroup 不是进程组：fork 出来的进程离不开自己所在的 unit，除非有个特权的 manager 把它搬走。所以「把 tmux server 挪出 connector 名下、让它结构上就不归我们」在不要 sudo 的前提下无处可去，那一行就是做法本身，不是权宜。

`KillMode=process` 对 `--user` unit 一样成立（systemd 252 实测：unit 停掉，它 fork 出来的 tmux server 和里面的会话照旧）——这一点值得单说，因为那是现在**每台机器都走的路**，不再是没 sudo 时的退路。它撑住的**上限是同一次开机**：重启会带走 tmux server 和里面的会话，那不是任何一行 unit 挡得住的；connector 自己能不能回来是另一件事，见下。

**真的要结束会话的动作是另外几个**，它们说了就得算数：`cheesehost link disconnect`、`cheesehost link no-auto-connect`、`cheesehost uninstall`（这条尤其——机器不是我们的，不能留东西），以及服务端关掉某块屏幕。

unit 文件由 `cheesehost link connect` 每次重写（kardianos 本身拒绝覆盖已存在的 unit，所以是先 uninstall 再 install），否则老版本装出来的 unit 会一直活着，而这类"发布悄悄没生效"正是 #501 的形状。

### connector 装在他自己的账户下，从不问 root

`cheesehost link connect` 只装**用户级** service——Linux 是 `systemd --user` unit（`~/.config/systemd/user/cheese.service`），macOS 是 LaunchAgent（`~/Library/LaunchAgents/cheese.plist`）。没有提权、没有 polkit 弹窗、没有 `sudo` 这个词。这是上面那条约束的直接推论：「一个 `sudo` 让安装成功」本来就写在它列出的、最容易破坏它的写法里。

「系统级 service 才能在没人登录时起来」曾经是提权的理由，在 Linux 上它不成立：**`loginctl enable-linger` 不需要管理员**。systemd 自带的 polkit 策略里 `org.freedesktop.login1.set-self-linger` 是 `allow_any=yes`（要管理员的是给**别人**开的 `set-user-linger`）。systemd 252 实测：普通用户 `loginctl enable-linger` exit 0、`Linger=yes`；`loginctl list-sessions` 空着，`user@1000.service` 仍然 active。所以 `link connect` 装完就替自己开 linger——**开不了不静默降级**，把后果和那一条修复命令印出来；入册脚本更进一步，linger 不是 `yes` 就直接判这次入册失败（那台机器没人会登录，它会绿一下然后随 ssh 一起消失）。

唯一一处 `sudo` 在**入册脚本**里，那是平台自己开的机器（§0 明说 Cloud 不适用），上面几行装 tmux/git 用的就是同一个免密 sudo：镜像里没跑 polkit 时 logind 会直接拒掉普通用户的 `enable-linger`，那就补一次 `sudo -n loginctl enable-linger`，然后仍旧只认 linger 自己的回答。**connector 里没有这条路**——它跑在别人的笔记本上。

两个细节必须同时到位，缺一个都是「装成功了，然后永远不会自己起来」：

- **`WantedBy=default.target`**。user manager 里根本没有 `multi-user.target`——systemd 252 实测：`systemctl --user enable` 照收，回一句 "added as a dependency to a non-existent unit"，之后谁也不会拉起它。于是 connector 只在 `link connect` 亲手 start 的那一次活着，重启后再不回来，而安装报的是成功。
- **unit 里不能有 `User=`**。systemd 拒绝加载带 `User=` 的 user unit，而 scope 里本来就只有一个账户。

`KillUserProcesses=` 不是这里的开关，别去调它——它管的是 login session scope，而 connector 和它的 tmux server 在 `user@.service` 下面，从来不在那里面。

**`uninstall` 不关 linger**，尽管它是我们开的。linger 是账户级设置，机主自己的 user timer / service 可能正靠着它，而我们分不清那台机器上它本来是不是就开着；关掉它的代价落在别人的东西上，留着它的代价是一个空转的 user manager。§0 那条「走之后还是原样」在这里的读法是前者更重。

**macOS 没有 linger 的对应物，也不打算造一个。** LaunchAgent 活在机主的登录会话里（实测：job 落在 `gui/501` 域，`type = login`、`creator = loginwindow`；非 root `launchctl bootstrap user/501` 直接 `Bootstrap failed: 5`）。他登录时起、登出时停、下次登录再起。**一台没人登录的 Mac 不托管**——这是「不问别人要管理员密码」的诚实代价，不是漏了一个 case。真要 headless Mac，那是 LaunchDaemon、是一个新决定，不是在这里留一个开关等着被捡起来。

If a machine-wide cheese service is already installed, `link connect` refuses installation and prints its removal commands. The Mac desktop app removes that service through the administrator dialog before connecting. Separate enrollments run as separate user services; two processes using the same profile would still share a device credential and tmux server.

### Enrollment profiles

An enrollment is an execution resource. Each profile keeps its own device credential, service, workspace, tmux socket and runtime state. The existing installation is the `default` profile and keeps its config, service name and socket without re-enrollment.

To add another enrollment, run `cheesehost auth login <origin>/connector` again. It keeps the existing enrollment and prints the new name (`profile-2`, then `profile-3`, and so on). To choose a name, use:

```sh
cheesehost --profile research auth login <origin>/connector
cheesehost --profile research link connect
cheesehost --profile research status
cheesehost --profile research uninstall
```

Commands without `--profile` address `default`. An explicit login to an already enrolled profile refuses to replace its credential. `--config` remains available for a specific config file and cannot be combined with `--profile`.

| Resource | Default profile | Named profile `research` |
| --- | --- | --- |
| Config | `<user-config>/cheese/config.json` | `<user-config>/cheese/profiles/research/config.json` |
| Workspace | `~/.cheese/` | `~/.cheese/profiles/research/` |
| Service | `cheese` | `cheese-research` |
| State | Beside the config | Beside the profile config |
| tmux | Original per-user socket | Separate stable socket for this profile |

`<user-config>` is `~/.config` on Linux unless `XDG_CONFIG_HOME` is set, `~/Library/Application Support` on macOS, and `%APPDATA%` on Windows. The desktop app reuses an enrollment of the selected team, or creates a named `team-<id>` profile for a new team.

The connector supplies `CHEESE_PROFILE_ROOT` to execution commands and screens. Backend paths, harness state, executor releases, sandbox records and cleanup resolve that root on the machine. The connector announces `profiles` in `hello`; a connector that does not (one built before profiles) is a default-profile machine, and the hub writes `.cheese` into the paths it resolves itself until its update lands. Uninstall removes the selected profile's service, config and workspace. Uninstalling `default` preserves the other profiles under `profiles/`.

Claude credentials record their own team and personal owner, independently of any execution-device enrollment. A profile chooses their local storage and connector transport. Subscription directories live at `<profile-root>/logins/claude-logins/<id>`; an existing first login keeps `<profile-root>/logins/claude-login`. Login, status, logout, probes and session launches select that profile. On first start, the connector moves the old shared store into the single profile, preserving its macOS Keychain entries; when several profiles exist, the default profile inherits it. Old paths are removed after the move; one recreated afterwards by a session started before it is left in place and no longer read. Uninstall removes the selected profile's logins. While another profile remains, shared binaries, runtime skill cache and the connector's PATH entry stay; the last uninstall removes the whole installation.

---

## 1. 设备入册流程

完整链路（装 → 选择团队并批准 → 上线 → 选用）：

1. **装连接器（目标机器上）**。在团队的「设备 → 接入设备」复制这条，或在目标机器直接跑：
   ```bash
   curl -fsSL <origin>/connector/install.sh | sh
   ```
   脚本探测平台、下对应二进制到 `~/.local/bin/cheesehost`，不带任何 secret、不含业务逻辑，最后提示 `next: cheesehost link connect <origin>/connector`。**下完先校验**：算文件的 sha256，与同一次响应里 `X-Checksum-SHA256` 声明的值比对，不符或没这个头就不安装、非零退出——中间的 relay 截断或替换了字节，不能因此落到 PATH 上变成可执行文件。

   Windows 上（没装桌面端的机器，比如服务器）在 PowerShell 里运行，作用相同：
   ```powershell
   irm <origin>/connector/install.ps1 | iex
   ```
   It puts `cheesehost.exe` in `%LOCALAPPDATA%\cheese\bin` and adds it to the user's PATH without administrator rights. Uninstalling the last enrollment removes the binary and that PATH entry; uninstalling one of several leaves them. Checksum verification follows `install.sh`.

2. **`cheesehost link connect <origin>/connector`**。这台机器还没登录，它先走登录：CLI 打 `POST /connector/auth/device/start`，拿回 `device_code`，打印一个 `approve_url`（指向前端 `/connect?code=<code>`），然后**阻塞轮询** `POST /connector/auth/device/poll`，等人批准。

3. **人在网页批准**。打开 `approve_url`，登录后落到前端 `/connect` 审批页（批准**在登录态后面**，没有裸批准按钮）：可给节点改名、选择所属团队（默认个人团队），提交即 `POST /connector/connect`，写入唯一的 `team_id` 并保留接入人的身份作为来源记录，设备从此有了**不过期的 durable token**（只能服务端撤销），令牌在 CLI 下一次轮询时生成并交出，只交一次，服务端只存摘要。

4. **同一条命令接着上线**。CLI 轮询拿到 token，写入 `~/.config/cheese/config.json`，随即拨出 `WS /connector/agent`，用 durable token 鉴权。握手成功后这台机器在 `device_hub` 里标记为在线。`cheesehost link auto-connect` 可让它开机自动重连。**这一步不需要 sudo**：service 装在当前账户下（Linux 顺带 `loginctl enable-linger`，macOS 是 LaunchAgent），细节和它的边界见 §0。

5. **团队管理**。设备接入后即属于所选团队。团队管理员在「设备」页改名或移除接入，所有团队项目均可使用。接入人离开团队后没有额外管理权。没有项目级绑定或借给其他团队的操作；同一台电脑为另一团队接入独立 profile。

6. **频道选 device 算力**。两种姿势：
   - **全局**：不配 Cloud 的部署，整个算力池就是 DeviceChannel，每次 agent 请求都落到一台在线的、绑定了该项目的设备。
   - Room and project settings select `device` or `cloud` for execution. The catalogue requires an online enrolled device before offering it.
   - Each room retains its selected execution device and its separately recorded session host. An offline device prevents further execution; the platform does not move its worktree to another machine automatically.

> 一个 gotcha：`approve_url` 用的是 `frontend_url`（前端 SPA 的 `/connect`），**不是** `CONNECTOR_PUBLIC_BASE`——后者是后端/webhook 基址（默认 localhost:8099），在浏览器里会 404。

---

## 2. 设备需要装什么

Ordinary execution devices run a persistent Python service, which runs a room's shell commands as its own processes, and the pinned `claude mcp serve` process for native file tools and the shell snapshot those commands start from. They do not require Docker. Central hosts also run the session launcher and need Docker for private chat containers. The connector and launch helpers use these dependencies:

| 依赖 | 用途 | 是否必须 |
|---|---|---|
| **bash** | 启动器本身就是 `bash -lc` 脚本 | 必须 |
| **node** | 启动器用 node 写 `~/.claude.json` 的 per-project trust 闸门（动态 key，shell heredoc 做不到） | 必须 |
| **curl** | `install.sh` 用 curl 下二进制 | 必须 |
| **sha256sum**（coreutils）/ **shasum** / **openssl**（任一） | `install.sh` 算下载文件的 sha256，与响应头 `X-Checksum-SHA256` 比对 | 必须，Linux 上有前两个、macOS 上有后两个，三个都没有则拒绝安装 |
| **tmux** | 把 runner 和它握着的 `claude` 养在持久会话里，链路掉线不丢进程 | 必须。连接器没有 tmux 就直接退出，所以 `link connect` 在批准之前先找一遍 tmux，找不到就停下并说明怎么装；桌面端在 Mac 上自带一份 |
| **git** | agent 把项目 clone 进工作目录、把频道分支推回来 | 必须。缺它则轮次在**空目录**里跑完并报成功，工作没人看得见 |
| **python3**（3.11 以上） | 会话的运行程序（runner）、执行器，以及平台发到机器上跑的小工具（计量隧道、`cheese serve` 的预览隧道）。只用标准库，机器上不需要 venv、不需要 `pip install` | 必须。机器上的 `python3` 低于 3.11 或者没有时（每台 Mac 自带的 `/usr/bin/python3` 都是 3.9），连接器从服务器的 toolchain 路由取一份固定版本（python-build-standalone）放到 `~/.cheese/runtime/python`，排到自己 PATH 的最前面；会话窗口也带着这个 PATH 启动。取不到时连接照常，后台服务下次启动再取 |
| **claude** (Claude Code CLI) | Central model session, or native file tools on an executor | Required; supplied by the platform |
| **cheesehost** 连接器 | `install.sh` 装到 `~/.local/bin/`；必须装在**该服务自己能写的目录**里，否则自更新永远失败且无声（#501） |
| **能用的用户级 service manager** | 后台常驻靠它，而我们只用当前账户的那一个 | 必须。Linux 上是 `systemd --user`（要 logind：ssh 进来得有 `XDG_RUNTIME_DIR`，还要能 `loginctl enable-linger`），macOS 上是 launchd。装不上不是无声的：`link connect` 直接报错，入册脚本判失败 |

平台：Linux / macOS / Windows。Windows 上不用 WSL：连接器原生运行，没有终端屏幕，成员自己的 Claude Code 以后台进程运行（`internal/procscreen`）；首次 `link connect` 从服务器的 toolchain 路由取 python3 和 Git for Windows（bash、git、curl、coreutils）放到 `~/.cheese/runtime`，并排到自己 PATH 的最前面；后台常驻靠当前用户的登录启动项（`HKCU\...\Run`），不需要管理员。上表的依赖在 Windows 上都由它带来。

**claude 由平台安装，不由机器去厂商那里下。** 入册（`bootstrap_script`）和启动器共用同一个 pin，二进制从 `<origin>/connector/claude/<version>/<platform>/claude` 取——平台拉一次、按厂商发布的 SHA-256 校验、缓存、本地供给。三个理由每个都单独成立：机器未必到得了 `claude.ai`（云节点在私有子网、自托管机器在我们看不见的网里，而厂商安装脚本把"你所在地区不可用"列为一种失败）；拿到的版本未必过门槛，而**低于门槛启动器拒绝启动**；只有平台自己发二进制，pin 才从"希望机器下到对的版本"变成"我们递给它的就是那个"。

平台字符串用**厂商的词汇**（`linux-x64`、`linux-arm64-musl`），不是我们的 `<os>-<arch>`——我们的命名表达不了 musl，而只有机器知道自己的 libc。

### Claude subscription credentials

Add a subscription from the team's Models page. The desktop app runs the login on the current computer with its current profile. On the web, run the displayed `cheesehost claude login --server <server> --ticket <ticket>` command on the computer that will store the login. The single-use authorization expires after 15 minutes. This creates a model credential without enrolling an execution device, and each authorization creates an independent login directory.

The connector asks `GET <origin>/connector/claude/pin` for the pinned version, downloads it to `~/.cheese/claude/versions/<version>` when needed, and runs `claude auth login` with the login's own `CLAUDE_CONFIG_DIR`. `cheesehost --profile <profile> claude login --login <id>` signs an existing login in again. `claude status` lists the selected profile's logins; `claude logout <id>` removes one, with the id optional when there is only one. Each login has its own refresh cycle; the owner's `~/.claude` is untouched.

Add API keys and Anthropic-compatible model services through the team's Models page. Their keys are encrypted on the platform and have no storage computer.

When a credential host connects, the backend runs `claude auth status` through its connector for each registered subscription, without calling a model. It updates the registered login's status, including the Claude account email and organization the command reports, which the team's Models page shows to tell logins apart (a personal login's account only to its owner); it does not discover credentials from execution-device ownership. A credential-only host and an enrolled execution device can use the same transport.

**入册时缺任何一样都是 fatal**，理由同 §0：留到后面发现的失败，全都出现在没人看的地方。

---

## 3. 后端需要哪些配置

### `CONNECTOR_PUBLIC_BASE`

- **含义**：设备回连后端的公网基址，**不带 `/api` 后缀**。设备上的 `cheese` CLI 按它调后端；`install.sh` 也按它生成回连地址与（必要时）WS 控制信道地址。默认 `http://localhost:8099`（开发用）。
- **何时该设**：只要设备不在本机、或后端在前置/反代后面，就必须设成**设备能从外部访问到的公网 origin**。注意：值里含 `localhost`/`127.0.0.1` 会被视为"未配置"，`install.sh` 会回退用请求自身的 base_url——所以生产一定要写成真实公网 origin，别留 localhost。
- **NAT 朋友**：控制信道是设备**拨出**的 WS、CLI 调用是普通出站请求，设备不需要任何入站端口；但后端这个 base 必须对设备可达。

### `CONNECTOR_WS_OVERRIDES`（顺带）

当友好公网 origin 前置了**会剥掉 WebSocket Upgrade** 的反代（如校园前置 rucfd）时，把友好 origin 映射到一个能跑 WS 的 origin。`install.sh` 会预写 CLI 的 `ws` 配置键——登录/批准/下载仍走友好 origin，**只有持久控制信道**走映射地址。默认空 = 行为不变。

### 设备工作区边界

每台 Device 都有自己的文件系统边界，物理上是否碰巧与后端同机不改变协议：

- Each device stores room state under `home/{project_id}/{resource_id}` within its profile's workspace root. The room directory is `room/` under that home, and task checkouts live under the room's `.cheese/tasks/{task_id}`. A closed task's checkout is removed once its work is on the forge: when the task closes, or when the device next connects if it was offline then. One holding uncommitted files, unpushed commits or a running process is kept. A reclaimed room receives a new resource UUID when reopened.
- `cheese worktree` fetches each task branch through authenticated Git HTTP. The central Stop hook runs a checkpoint that invokes `cheese-sync` on that execution device to publish task work.
- Image attachments travel inside the message written to the session; no file is staged on either machine.
- 后端从不把自己的 topic workspace 路径翻译成设备路径，也不跳过复制。仓库中没有“后端与设备共享 workspace”的配置或分支。
- 后端不往任何工作树里写提交；它以设备 push 回来的分支作为结果。

---

## 4. 两个验证工作流的用途区别

| | Device wiring check | Device self-hosting smoke |
|---|---|---|
| **目的** | 验证零模型消耗的设备接线：在线、smart HTTP clone/push、CLI | 端到端真机实证：真 connector + 真 Claude Code + 真 runner 日志回流 |
| **形态** | `.github/workflows/device-wiring.yml`（真 dev 设备，不发模型请求） | `.github/workflows/device-smoke.yml`（真 dev 设备、真模型请求） |
| **模型消耗** | **零**——不打真模型，不花一分钱额度 | **真实消耗额度**——真给真 claude 发了一条 prompt |
| **怎么跑** | 手动触发 GitHub Actions 的 `Device wiring check (no model spend)` | 手动触发 `Device self-hosting smoke` |
| **验证什么** | sandbox routes、设备在线、设备独立 clone/commit/push、后端读到分支、设备侧 CLI 递验收卡 | 真消息 → runner → Claude Code → 日志/聊天帧 → device commit/push → 验收卡 |

**纪律**：日常回归 / CI 用 wiring check（零额度）；只有改了 device 链路、想确认"真通了"才跑 smoke（花额度）。两者互补，不可互替——wiring check 过但 smoke 挂，多半是真 `claude`/真 cli 与我们的 runner 接线对不上。

---

## 5. 常见故障与判断方法

### 5.1 屏幕面板显示什么

- **团队「设备」页**（`/teams/:handle/compute`）：列出团队的接入、在线状态和只读登录摘要。管理员可以改名、移除和接入设备。桌面端「设置 → 这台设备」只读列出本机各次接入、所属团队和管理页链接。
- **「现场」面板**：读这个会话的原始记录（`session_records`，见[会话记录](manual/dev/session-records.md)），默认是概览，也可以切到原始视图看思考、完整的工具输入输出和 JSON。screen 里跑的是 runner，`claude` 在它下面 headless 运行；产品里没有直接看那块 screen 终端的面板。

### 5.2 会话记录和生命周期事件走哪条路径

```
设备侧                                          后端侧
claude -p 的 stdout（stream-json）               读循环带着登记的 epoch 向 runner 要新东西
  → runner 按到达顺序编号、写进本地日志           → 记录：存进会话记录（同一编号只存一次），
     （records.sqlite）                             翻译成房间事件显示；什么都不由它决定
  → 子 agent / workflow 的记录文件也由它跟读      → 事件：连同它的效果在一次事务里落库，
  → runner 把看到的事实写进待确认的事件队列          推进座位的偏移，然后才确认
     （输入写入/读到/丢弃、运行/空闲、进程退出）
```

**记录和事件的持久层都是 runner，不是后端。** 后端一时够不着，runner 照记；事件在确认之前一直
留着，后端回来从头再拿，已经落过的那一条按 `(座位, epoch, 序号)` 什么都不改。日志在会话机上按
时间过期清理。

- **事件没回来**：先看 runner 活着没有（`ping`）、它的日志有没有在长；再看设备到后端的
  连接器链路是否在线。
- **事件重复**：记录按编号存，同一页读两次只存一次；翻译出的事件按记录自己的 id 去重；生命周期
  事件按座位的偏移只落一次。

### 5.3 设备离线时的表现

- **设备掉线**：`device_hub.is_online` 转 false，团队的「设备」页里该设备显示「离线」，并写着上次在线的时间（`device.last_seen_at`：连上时记一次，连接期间最多每分钟记一次）。连接断开算掉线，连接器 45 秒（三次心跳）没有消息也算：机器睡眠或断网时，代理那头的连接会一直挂着，直到机器醒来才关，所以平台按心跳判断，不等连接关闭。掉线那一刻，已经发出、正在等回话的调用立刻失败（`LinkInterrupted`，结果未知，不会重发），不会等到各自超时。掉线后 15 秒内（`RECONNECT_GRACE_S`）新来的调用等机器重连并发来 hello 再发出；过了这段时间仍没回来，或者这个进程从没见过这台机器，调用立刻失败（`DeviceOffline`）。CLI 侧带退避重连 + 心跳，NAT 后也能恢复；持久 tmux 会话在掉线期间**继续跑**，runner 照记它的日志，重连后后端接着读，工作树/会话不丢。
- **会话的环境要用的设备离线**：这次工具调用答「「设备」没有连上平台」（不进沙箱时）或「这个规格的电脑现在都不在线」（某人设备的沙箱），对话和平台工具照常，**不会换到别的规格或别人的设备上**。
- **解绑/撤销 token**：`DELETE /my/devices/{id}` 或服务端撤销 durable token → 该设备所有 screen 失效、`device_hub` 标记离线、市场 listing 里「自己的设备」在没有别的在线设备时变为不可选。

---

## 速查：一次完整接入

```bash
# 目标机器上
curl -fsSL https://<你的站点>/connector/install.sh | sh
cheesehost link connect https://<你的站点>/connector    # 先登录：打印 approve_url，阻塞轮询
# 人浏览器打开 approve_url → 登录 → 批准（可命名/绑项目）
# 同一条命令拿到 token 后装用户级 service 上线——全程不需要 sudo

# 平台侧：在批准页选择所属团队，然后在团队「设备」页管理
# 频道选 device 算力（不配 Cloud 时就是默认，也可以在房间的工作电脑选择里显式选）
```
