# 设备自托管：让芝士跑在自己的机器上

这条路（self-hosted / BYO 算力，fusion-design §5）把 agent 执行放到**用户自己入册的机器**上：你在笔记本/台式机装一个瘦客户机 `cheesehost`，批准后，平台就在设备上开一个「屏幕」跑 `claude`，结构化事件经 Claude Code hooks 回流。本地容器与 Device 是两个独立 provider；它们复用 hooks、CLI 等底座，但本地容器不会自动入册成设备，也不共享文件系统。

设备是**纯算力**：它不含 agent 身份，agent 由项目/话题在请求开始时独立解析（agent = 屏幕，不是机器）。设备永远向服务器**拨出**（非入站），带退避重连 + 心跳，所以躲在 NAT/防火墙后的笔记本也能托管 agent、**零入站端口**。

---

## 0. Hosted 机器不是我们的

> 机器形态（Cloud / Hosted Sandbox / Hosted Machine）见 **#358**——三类里两类还没传输，去读那条，别从这里推。本节只讲**已落地、改代码必须守住**的一条约束。

只适用于 **Hosted**（人接入的常驻机器）：那是别人的笔记本、别人的 `claude`，他借我们算力，不是把机器交给我们。**Cloud 不适用**——平台按话题开的一次性机器，随便处置。

**判据：除了我们放进去的东西，机器上任何一样在我们来之前是什么样，走之后还是什么样。**

- 装 claude = 往 `~/.local/share/claude/versions/<pin>` 加一个版本（那目录本就为共存而设）。**不碰 `~/.local/bin/claude`**，那是他敲 `claude` 得到的东西；启动器按 `versions/<pin>` 找，symlink 一分钱不值。反过来也不行：**看到他已有够新的就不装**，等于他下次升降级成了我们的行为变更。
- 配置隔离：`CLAUDE_CONFIG_DIR` / `HOME` / 工作树都在 `~/.cheese/`、`~/cheese-workspaces/` 下；他的 `~/.claude` 不读不写。
- 装在**他能写的目录**——否则自更新永远失败，且无声（#501）。

### 为什么这条特别容易破

破坏它的代码看起来都很合理：一个 symlink 让 `claude` 可用、一个 `sudo` 让安装成功、复用系统 tmux 省一个 socket。而反馈回路是断的——#489、#501、`systemctl kill` 带走 20 个会话，**全都只存在于没人看的地方**（一个 plist 的 stderr、一条 journald、一台"健康"的离线机器），平台侧一律正常。

所以问的不是"这样能不能 work"，而是：**这步在用户机器上做错了，谁会发现？** 答案若是"他，几周后，且不会联想到我们"，换做法。

### agent 会话住在 connector 自己的 tmux server 里

启动器起的内层 `claude` 会话，在 **connector 的私有 tmux server** 上，不在机主的默认 server 上。socket 不靠任何约定传递：这段启动器本来就跑在 connector 的一个 pane 里，tmux 把 socket 路径放在 `$TMUX` 的第一段（`<socket>,<pid>,<session>`），读出来即可（读完才 `unset TMUX`——从 pane 里 attach 必须先去掉它）。于是他的 `tmux ls` 看不见我们，他的 `tmux kill-server` 带不走 agent，我们的清理也碰不到他的会话。

在默认 server 上发现同名的 `cheese_*` 会话，启动器直接杀掉再起自己的：那会话是我们放的，它握着这个话题的 rendezvous socket、spool 和工作树，留着就等于同一个话题有两个 claude 在应答。

**"重启一下 connector"是非破坏性操作**，靠三件事一起成立，缺一件就不成立：

| | 没有它会怎样 |
|---|---|
| 会话在我们自己的 socket 上 | 机主一句 `tmux kill-server` 就全清；反过来我们也清他的 |
| connector 退出时只**释放**不拆（放开 viewer pty / rendezvous 连接 / runtime，tmux 会话原样留着） | `systemctl stop` 走 deferred 拆除路径，屏幕全没 |
| unit 里的 `KillMode=process` | systemd 默认 `control-group`，`stop`/`restart` 一律 SIGTERM 整个 cgroup，而 tmux server 就在里面（2026-08-17 实测：一次带走 20 个会话，6 个正在干活） |

下次启动会 re-adopt 活着的会话（`HasSession` 分支），drainer 继续重投它 spool 下来的 hook，viewer 重新 attach 回原来那个 pane。

第三行只对 systemd 说话，**macOS 不需要对应物**：tmux server 一起来就 daemonize（实测 tmux 3.5a：PPID 1、自成进程组），而 launchd 拆 job 只管 job 自己的进程组，于是 server 和里面的会话原样活着，`launchctl bootout` 之后 `has-session` 仍然成立。Linux 非要那一行，是因为 cgroup 不是进程组：fork 出来的进程离不开自己所在的 unit，除非有个特权的 manager 把它搬走。所以「把 tmux server 挪出 connector 名下、让它结构上就不归我们」在不要 sudo 的前提下无处可去，那一行就是做法本身，不是权宜。

`KillMode=process` 对 `--user` unit 一样成立（systemd 252 实测：unit 停掉，它 fork 出来的 tmux server 和里面的会话照旧）——这一点值得单说，因为那是现在**每台机器都走的路**，不再是没 sudo 时的退路。它撑住的**上限是同一次开机**：重启会带走 tmux server 和里面的会话，那不是任何一行 unit 挡得住的；connector 自己能不能回来是另一件事，见下。

**真的要结束会话的动作是另外几个**，它们说了就得算数：`cheese link disconnect`、`cheese link no-auto-connect`、`cheese uninstall`（这条尤其——机器不是我们的，不能留东西），以及服务端关掉某块屏幕。

unit 文件由 `cheese link connect` 每次重写（kardianos 本身拒绝覆盖已存在的 unit，所以是先 uninstall 再 install），否则老版本装出来的 unit 会一直活着，而这类"发布悄悄没生效"正是 #501 的形状。

### connector 装在他自己的账户下，从不问 root

`cheese link connect` 只装**用户级** service——Linux 是 `systemd --user` unit（`~/.config/systemd/user/cheese.service`），macOS 是 LaunchAgent（`~/Library/LaunchAgents/cheese.plist`）。没有提权、没有 polkit 弹窗、没有 `sudo` 这个词。这是上面那条约束的直接推论：「一个 `sudo` 让安装成功」本来就写在它列出的、最容易破坏它的写法里。

「系统级 service 才能在没人登录时起来」曾经是提权的理由，在 Linux 上它不成立：**`loginctl enable-linger` 不需要管理员**。systemd 自带的 polkit 策略里 `org.freedesktop.login1.set-self-linger` 是 `allow_any=yes`（要管理员的是给**别人**开的 `set-user-linger`）。systemd 252 实测：普通用户 `loginctl enable-linger` exit 0、`Linger=yes`；`loginctl list-sessions` 空着，`user@1000.service` 仍然 active。所以 `link connect` 装完就替自己开 linger——**开不了不静默降级**，把后果和那一条修复命令印出来；入册脚本更进一步，linger 不是 `yes` 就直接判这次入册失败（那台机器没人会登录，它会绿一下然后随 ssh 一起消失）。

唯一一处 `sudo` 在**入册脚本**里，那是平台自己开的机器（§0 明说 Cloud 不适用），上面几行装 tmux/git 用的就是同一个免密 sudo：镜像里没跑 polkit 时 logind 会直接拒掉普通用户的 `enable-linger`，那就补一次 `sudo -n loginctl enable-linger`，然后仍旧只认 linger 自己的回答。**connector 里没有这条路**——它跑在别人的笔记本上。

两个细节必须同时到位，缺一个都是「装成功了，然后永远不会自己起来」：

- **`WantedBy=default.target`**。user manager 里根本没有 `multi-user.target`——systemd 252 实测：`systemctl --user enable` 照收，回一句 "added as a dependency to a non-existent unit"，之后谁也不会拉起它。于是 connector 只在 `link connect` 亲手 start 的那一次活着，重启后再不回来，而安装报的是成功。
- **unit 里不能有 `User=`**。systemd 拒绝加载带 `User=` 的 user unit，而 scope 里本来就只有一个账户。

`KillUserProcesses=` 不是这里的开关，别去调它——它管的是 login session scope，而 connector 和它的 tmux server 在 `user@.service` 下面，从来不在那里面。

**`uninstall` 不关 linger**，尽管它是我们开的。linger 是账户级设置，机主自己的 user timer / service 可能正靠着它，而我们分不清那台机器上它本来是不是就开着；关掉它的代价落在别人的东西上，留着它的代价是一个空转的 user manager。§0 那条「走之后还是原样」在这里的读法是前者更重。

**macOS 没有 linger 的对应物，也不打算造一个。** LaunchAgent 活在机主的登录会话里（实测：job 落在 `gui/501` 域，`type = login`、`creator = loginwindow`；非 root `launchctl bootstrap user/501` 直接 `Bootstrap failed: 5`）。他登录时起、登出时停、下次登录再起。**一台没人登录的 Mac 不托管**——这是「不问别人要管理员密码」的诚实代价，不是漏了一个 case。真要 headless Mac，那是 LaunchDaemon、是一个新决定，不是在这里留一个开关等着被捡起来。

**机器上已经有 root 装的 cheese service 时，`link connect` 拒绝安装**，并打印删掉它的命令。同一个账户下跑两个 connector 比一个都没有更糟：共用一份 device 凭据、共用同一个 tmux server，互相收养又互相拆掉对方的屏幕。

---

## 1. 设备入册流程

完整链路（装 → 登录 → 批准 → 上线 → 绑定 → 选用）：

1. **装连接器（目标机器上）**。在「我的设备 → 添加设备」复制这条，或在目标机器直接跑：
   ```bash
   curl -fsSL <origin>/connector/install.sh | sh
   ```
   脚本探测平台、下对应二进制到 `~/.local/bin/cheesehost`，不带任何 secret、不含业务逻辑，最后提示 `next: cheesehost auth login <origin>/connector`。

2. **`cheesehost auth login <origin>/connector`**。CLI 打 `POST /connector/auth/device/start`，拿回 `device_code`，打印一个 `approve_url`（指向前端 `/connect?code=<code>`），然后**阻塞轮询** `POST /connector/auth/device/poll`，等人批准。

3. **人在网页批准**。打开 `approve_url`，登录后落到前端 `/connect` 审批页（批准**在登录态后面**，没有裸批准按钮）：可给节点改名、可选绑定一个项目，提交即 `POST /connector/connect`——把设备绑到当前用户为 owner，签发**不过期的 durable token**（只能服务端撤销）。

4. **`cheese link connect` 上线**。CLI 轮询拿到 token，写入 `~/.config/cheese/config.json`，随即拨出 `WS /connector/agent`，用 durable token 鉴权。握手成功后这台机器在 `device_hub` 里标记为在线。`cheese link auto-connect` 可让它开机自动重连。**这一步不需要 sudo**：service 装在当前账户下（Linux 顺带 `loginctl enable-linger`，macOS 是 LaunchAgent），细节和它的边界见 §0。

5. **绑定项目/团队**。在「我的设备」页或各小队的「算力」页把设备绑到项目（`assign_to_project`）或团队（`assign_to_team`——团队下**所有项目**都能跑在这台机器上）。只有设备的 owner 能绑，且 owner 必须是该项目/团队的成员。

6. **话题选 device 算力**。两种姿势：
   - **全局**：`AGENT_BACKEND=device`。整个算力池就是 DeviceChannel，每次 agent 请求都落到一台在线的、绑定了该项目的设备。
   - **话题/项目级**：DeviceChannel 与 local-docker 并列在算力池里，通过 `compute_profile` / provider_id 选用 `device`。**仅当有在线设备时才可选**（市场 listing 里 `available` 按 `device_online` gating）。
   - **话题亲和（关键）**：一个话题第一次落在哪台设备就**写死 pin** 在那台，之后仍回到同一台——工作树 + 可恢复 Claude Code 会话都在那台机器上。pinned 设备离线时**绝不漂到别的在线设备**（否则工作树清零、resume 错乱）。

> 一个 gotcha：`approve_url` 用的是 `frontend_url`（前端 SPA 的 `/connect`），**不是** `CONNECTOR_PUBLIC_BASE`——后者是后端/webhook 基址（默认 localhost:8099），在浏览器里会 404。

---

## 2. 设备需要装什么

设备是用户自己的机器，`claude` 直接跑在上面，**不入容器，不需要 Docker**。启动器（`device_launch.build_launch_script`）是一个自包含的 `bash -lc` 脚本，运行时依赖：

| 依赖 | 用途 | 是否必须 |
|---|---|---|
| **bash** | 启动器本身就是 `bash -lc` 脚本 | 必须 |
| **node** | 启动器用 node 写 `~/.claude.json` 的 per-project trust 闸门（动态 key，shell heredoc 做不到） | 必须 |
| **curl** | hook 转发器用 curl 把每个 hook JSON POST 回后端；`install.sh` 也用 curl 下二进制 | 必须 |
| **tmux** | 把 `claude` 养在持久会话里，链路/屏幕掉线不丢进程，重开屏幕即 re-attach | 必须。连接器**没有 tmux 就直接退出**，而 `link connect` 仍报成功（systemd 在进程倒下之前就返回了），所以缺它表现为"机器永远不上线"，不是任何一条错误信息 |
| **git** | agent 把项目 clone 进工作目录、把话题分支推回来 | 必须。缺它则轮次在**空目录**里跑完并报成功，工作没人看得见 |
| **claude**（Claude Code CLI） | 真正干活的 agent | 必须，**由平台装**（见下） |
| **cheesehost** 连接器 | `install.sh` 装到 `~/.local/bin/`；必须装在**该服务自己能写的目录**里，否则自更新永远失败且无声（#501） |
| **能用的用户级 service manager** | 后台常驻靠它，而我们只用当前账户的那一个 | 必须。Linux 上是 `systemd --user`（要 logind：ssh 进来得有 `XDG_RUNTIME_DIR`，还要能 `loginctl enable-linger`），macOS 上是 launchd。装不上不是无声的：`link connect` 直接报错，入册脚本判失败 |

平台：Linux / macOS（需 pty），**无 Windows**。

**claude 由平台安装，不由机器去厂商那里下。** 入册（`bootstrap_script`）和启动器共用同一个 pin，二进制从 `<origin>/connector/claude/<version>/<platform>/claude` 取——平台拉一次、按厂商发布的 SHA-256 校验、缓存、本地供给。三个理由每个都单独成立：机器未必到得了 `claude.ai`（云节点在私有子网、自托管机器在我们看不见的网里，而厂商安装脚本把"你所在地区不可用"列为一种失败）；拿到的版本未必过门槛，而**低于门槛启动器拒绝启动**；只有平台自己发二进制，pin 才从"希望机器下到对的版本"变成"我们递给它的就是那个"。

平台字符串用**厂商的词汇**（`linux-x64`、`linux-arm64-musl`），不是我们的 `<os>-<arch>`——我们的命名表达不了 musl，而只有机器知道自己的 libc。

**入册时缺任何一样都是 fatal**，理由同 §0：留到后面发现的失败，全都出现在没人看的地方。

---

## 3. 后端需要哪些配置

### `CONNECTOR_PUBLIC_BASE`

- **含义**：设备回连后端的公网基址，**不带 `/api` 后缀**。设备的 `cheese-hook` 把 Claude Code hooks POST 到 `{CONNECTOR_PUBLIC_BASE}/sandbox/hooks/{topic_id}`；`install.sh` 也按它生成回连地址与（必要时）WS 控制信道地址。默认 `http://localhost:8099`（开发用）。
- **何时该设**：只要设备不在本机、或后端在前置/反代后面，就必须设成**设备能从外部访问到的公网 origin**。注意：值里含 `localhost`/`127.0.0.1` 会被视为"未配置"，`install.sh` 会回退用请求自身的 base_url——所以生产一定要写成真实公网 origin，别留 localhost。
- **NAT 朋友**：控制信道是设备**拨出**的 WS、hook POST 是普通出站请求，设备不需要任何入站端口；但后端这个 base 必须对设备可达。

### `CONNECTOR_WS_OVERRIDES`（顺带）

当友好公网 origin 前置了**会剥掉 WebSocket Upgrade** 的反代（如校园前置 rucfd）时，把友好 origin 映射到一个能跑 WS 的 origin。`install.sh` 会预写 CLI 的 `ws` 配置键——登录/批准/下载/hook 仍走友好 origin，**只有持久控制信道**走映射地址。默认空 = 行为不变。

### 设备工作区边界

每台 Device 都有自己的文件系统边界，物理上是否碰巧与后端同机不改变协议：

- Claude Code 的隔离 home 是 `$HOME/.cheese/home/{project_id}/{topic_id}`；项目 checkout 是 `$HOME/.cheese/work/{project_id}/{topic_id}`。
- 首次开屏时，设备通过带 scoped token 的 smart HTTP clone 话题分支；请求结束时 `cheese-sync` 提交并 push 回同一分支。
- 图片附件先由后端通过控制信道 `file.put` 写进这个 checkout，收到设备确认后，再用 `@相对路径` 送进 rendezvous。
- 后端从不把自己的 topic workspace 路径翻译成设备路径，也不跳过复制。仓库中没有“后端与设备共享 workspace”的配置或分支。
- DeviceChannel 不快照后端 worktree；后端以设备 push 回来的分支作为结果。

---

## 4. 两个验证工作流的用途区别

| | Device wiring check | Device self-hosting smoke |
|---|---|---|
| **目的** | 验证零模型消耗的设备接线：在线、smart HTTP clone/push、CLI | 端到端真机实证：真 connector + 真 Claude Code + 真 hook 回流 |
| **形态** | `.github/workflows/device-wiring.yml`（真 dev 设备，不发模型请求） | `.github/workflows/device-smoke.yml`（真 dev 设备、真模型请求） |
| **模型消耗** | **零**——不打真模型，不花一分钱额度 | **真实消耗额度**——真给真 claude 发了一条 prompt |
| **怎么跑** | 手动触发 GitHub Actions 的 `Device wiring check (no model spend)` | 手动触发 `Device self-hosting smoke` |
| **验证什么** | sandbox routes、设备在线、设备独立 clone/commit/push、后端读到分支、设备侧 CLI 递验收卡 | 真消息 → rendezvous → Claude Code → hooks/聊天帧 → device commit/push → 验收卡 |

**纪律**：日常回归 / CI 用 wiring check（零额度）；只有改了 device 链路、想确认"真通了"才跑 smoke（花额度）。两者互补，不可互替——wiring check 过但 smoke 挂，多半是真 `claude`/真 cli 与我们的 hook 接线对不上。

---

## 5. 常见故障与判断方法

### 5.1 屏幕面板显示什么

- **「我的设备」页**（`/my/devices`）：每台设备一个绿/灰圆点（在线/离线，来自 `device_hub.is_online`）、算力节点 id、在为哪些小队提供算力、运行中的 agent 现场 chip（每个 screen 带自己的 agent handle——设备本身不是 agent）。空白 = 还没连接的设备。
- **「现场」面板**：浏览器里**只读**的真终端，字节级 relay 设备上 `claude` 的 TUI；只能发 `resize` 控制帧，**不能敲键**。空白/连不上 = screen 没开，或你无权看（无权与未知 screen 以同样的 1008 关闭，不可枚举）。

### 5.2 钩子事件走哪条路径

```
设备侧                                          后端侧
claude COMMAND hooks                            /sandbox/hooks/{topic_id}
  → ~/.claude/cheese-hook 转发器                  → 先写 topic 的服务端 spool
  → 先写本地 spool (~/.claude/cheese-spool,        → 再推给活 turn（hook_router →
     CHEESE_HOOK_SPOOL_ONLY=1)                       translate_hook → AgentEvent）
  → 后台 drainer 用 curl POST                     → code:200 = 已落到我们盘上
     {CONNECTOR_PUBLIC_BASE}/sandbox/hooks/{topic}    （drainer 见 200 才删本地副本）
     带 X-Cheese-Token + X-Cheese-Event-Id      （按 event-id 去重）
```

**200 = 这条事件已经在我们自己的盘上。** drainer 见 200 就删掉设备上的副本，所以后端在落盘之前给的任何 ack 都是在拿一台**别人的机器**（§0）当我们的持久层：它可以离线、被擦、被机主删掉。落盘失败、缺 `X-Cheese-Event-Id`、token 里没有项目，一律回非 200——事件留在它还存在的地方，drainer 下一轮再来。

- **事件没回来**：先看设备侧 spool 目录有没有堆积——后端不可达时 drainer 会一直重试，24h 过期清理；spool 堆积 = 设备到后端的回连断了。再看 `CONNECTOR_PUBLIC_BASE` 设备是否可达、scoped token 对不对。
- **事件丢序/重复**：后端按 `X-Cheese-Event-Id` 去重；durable 投递保证一次 link/后端抖动不丢事件，但可能重投，消费侧需幂等。

### 5.3 设备离线时的表现

- **设备掉线**：`device_hub.is_online` 转 false，「我的设备」该设备变灰「离线」。CLI 侧带退避重连 + 心跳，NAT 后也能恢复；持久 tmux 会话 `cheese` 在掉线期间**继续跑**，重连后重开屏幕即 re-attach，工作树/会话不丢。
- **已 pin 该设备的话题发 turn**：`resolve_pinned_device` 发现 pinned 设备离线 → 抛 `ScreenSetupError`「话题绑定的算力设备已离线，请重新连接该设备再继续本轮（不会漂到别的设备，以免工作树/会话错乱）」→ 该轮排队/失败重试，**绝不漂到别的在线设备**。
- **话题还没 pin、且没有任何绑定设备在线**：报「没有在线的绑定设备可运行本轮（self-hosted 设备未连接）」。
- **解绑/撤销 token**：`DELETE /my/devices/{id}` 或服务端撤销 durable token → 该设备所有 screen 失效、`device_hub` 标记离线、`DeviceChannel.available` 转 false、市场 listing 里 `device` 变为不可选。

---

## 速查：一次完整接入

```bash
# 目标机器上
curl -fsSL https://<你的站点>/connector/install.sh | sh
cheesehost auth login https://<你的站点>/connector      # 打印 approve_url，阻塞轮询
# 人浏览器打开 approve_url → 登录 → 批准（可命名/绑项目）
# CLI 自动 link connect 上线——全程不需要 sudo

# 平台侧：在小队「算力」页把设备绑给团队（或「我的设备」绑项目）
# 话题选 device 算力（全局 AGENT_BACKEND=device，或用 compute_profile/provider_id 选 device）
```
