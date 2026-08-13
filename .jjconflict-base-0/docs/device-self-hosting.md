# 设备自托管：让芝士跑在自己的机器上

这条路（self-hosted / BYO 算力，fusion-design §5）把一个 agent 轮次搬到**用户自己入册的机器**上跑：你在笔记本/台式机上装一个瘦客户机 `cheesehost`，登录批准后，平台就在这台机器上开一个「屏幕」跑 `claude`，结构化事件照样经 Claude Code hooks 回流。本地容器与用户设备**同走一套 cc-脚本-服务端底座**，只差入册方式——本地自动入册，远程走设备流。

设备是**纯算力**：它不含 agent 身份，agent 由项目/话题在轮次开始时独立解析（agent = 屏幕，不是机器）。设备永远向服务器**拨出**（非入站），带退避重连 + 心跳，所以躲在 NAT/防火墙后的笔记本也能托管 agent、**零入站端口**。

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

4. **`cheese link connect` 上线**。CLI 轮询拿到 token，写入 `~/.config/cheese/config.json`，随即拨出 `WS /connector/agent`，用 durable token 鉴权。握手成功后这台机器在 `device_hub` 里标记为在线。`cheese link auto-connect` 可让它开机自动重连。

5. **绑定项目/团队**。在「我的设备」页或各小队的「算力」页把设备绑到项目（`assign_to_project`）或团队（`assign_to_team`——团队下**所有项目**都能跑在这台机器上）。只有设备的 owner 能绑，且 owner 必须是该项目/团队的成员。

6. **话题选 device 算力**。两种姿势：
   - **全局**：`AGENT_BACKEND=device`。整个算力池就是 DeviceProvider，每个 turn 都落到一台在线的、绑定了该项目的设备。
   - **会话级**（默认 sdk/local 池下）：DeviceProvider 与 local-docker 并列在算力池里，话题/项目通过 `compute_profile` / provider_id 选用 `device`。**仅当有在线设备时才可选**（市场 listing 里 `available` 按 `device_online` gating），默认仍是 local-docker，纯增量。
   - **话题亲和（关键）**：一个话题的首个 turn 落在哪台设备就**写死 pin** 在那台，之后该话题所有 turn 都回到同一台——工作树 + 可恢复 claude 会话都在那台机器上。pinned 设备离线时**绝不漂到别的在线设备**（否则工作树清零、resume 错乱，这是原始漂移 bug 的定稿修复）。

> 一个 gotcha：`approve_url` 用的是 `frontend_url`（前端 SPA 的 `/connect`），**不是** `CONNECTOR_PUBLIC_BASE`——后者是后端/webhook 基址（默认 localhost:8099），在浏览器里会 404。

---

## 2. 设备需要装什么

设备是用户自己的机器，`claude` 直接跑在上面，**不入容器，不需要 Docker**。启动器（`device_launch.build_launch_script`）是一个自包含的 `bash -lc` 脚本，运行时依赖：

| 依赖 | 用途 | 是否必须 |
|---|---|---|
| **bash** | 启动器本身就是 `bash -lc` 脚本 | 必须 |
| **node** | 启动器用 node 写 `~/.claude.json` 的 per-project trust 闸门（动态 key，shell heredoc 做不到） | 必须 |
| **curl** | hook 转发器用 curl 把每个 hook JSON POST 回后端；`install.sh` 也用 curl 下二进制 | 必须 |
| **tmux** | 把 `claude` 养在持久会话 `cheese` 里，链路/屏幕掉线不丢进程，重开屏幕即 re-attach | 推荐（没装则退化为直接 `exec claude`，掉线即丢会话） |
| **claude**（Claude Code CLI） | 真正干活的 agent | 必须 |
| **cheesehost** 连接器 | `install.sh` 自动装到 `~/.local/bin/`；可选再放一个私有 tmux 到 `~/.config/cheese/bin/tmux` | 必须（连接器） |

平台：Linux / macOS（需 pty），**无 Windows**。除 tmux 外其余均为硬依赖；缺 tmux 能跑但失去掉线续跑能力。

---

## 3. 后端需要哪些配置

### `CONNECTOR_PUBLIC_BASE`

- **含义**：设备回连后端的公网基址，**不带 `/api` 后缀**。设备的 `cheese-hook` 把 Claude Code hooks POST 到 `{CONNECTOR_PUBLIC_BASE}/sandbox/hooks/{topic_id}`；`install.sh` 也按它生成回连地址与（必要时）WS 控制信道地址。默认 `http://localhost:8099`（开发用）。
- **何时该设**：只要设备不在本机、或后端在前置/反代后面，就必须设成**设备能从外部访问到的公网 origin**。注意：值里含 `localhost`/`127.0.0.1` 会被视为"未配置"，`install.sh` 会回退用请求自身的 base_url——所以生产一定要写成真实公网 origin，别留 localhost。
- **NAT 朋友**：控制信道是设备**拨出**的 WS、hook POST 是普通出站请求，设备不需要任何入站端口；但后端这个 base 必须对设备可达。

### `CONNECTOR_WS_OVERRIDES`（顺带）

当友好公网 origin 前置了**会剥掉 WebSocket Upgrade** 的反代（如校园前置 rucfd）时，把友好 origin 映射到一个能跑 WS 的 origin。`install.sh` 会预写 CLI 的 `ws` 配置键——登录/批准/下载/hook 仍走友好 origin，**只有持久控制信道**走映射地址。默认空 = 行为不变。

### `DEVICE_SHARED_WORKSPACE_HOST_ROOT`

- **含义（概念）**：设备上"共享工作区根目录"——即设备侧 agent 的 home 与 work 目录落在哪。当前实现里，`DeviceProvider._ensure_screen` 把 home 钉在 `$HOME/.cheese/home/{project_id}`、work 钉在 `$HOME/.cheese/work/{project_id}/{topic_id}`，启动器 `mkdir -p` 它们；设备**拥有自己的工作树**（`checkpoint` 在 device 后端是 no-op，未来的 git 快照可经 link 在设备上 exec）。
- **何时该设**（若该旋钮启用）：当设备是**多用户共享机**、或要把工作树放到一块**团队共享/挂载卷**、或 `$HOME` 不可写/空间不足时，需要把根从默认 `$HOME/.cheese` 挪到别处。
- **⚠️ 现状（以代码为准）**：本仓库后端**尚未**把 `DEVICE_SHARED_WORKSPACE_HOST_ROOT` 作为服务端配置项接入——设备侧根目前由启动器写死为 `$HOME/.cheese`，没有服务端可下发覆盖的开关。如需改根，目前只能在设备侧用 `CHEESE_HOME` / `CHEESE_WORK` 环境变量（启动器读取）或改登录用户的环境来间接调整；要把它做成服务端可配项，需在 `DeviceProvider._ensure_screen` 下发 env 处接入。本文先讲清"含义与何时该设"，落地以代码为准。

---

## 4. 两个验证工作流的用途区别

| | Device wiring check | Device self-hosting smoke |
|---|---|---|
| **目的** | 验证"接线"成立：设备流、拨出连、开屏、hook 回流通路 | 端到端真机实证：真 frozen cli + 真 claude + 真 hook 回流 |
| **形态** | 后端 unit/integration 测试（stub/fake，不启真 claude） | `backend/scripts/p3/run_spike.py`（真二进制、真进程） |
| **模型消耗** | **零**——不打真模型，不花一分钱额度 | **真实消耗额度**——真给真 claude 发了一条 prompt |
| **怎么跑** | `task be:test`（含 `test_connector_device_flow` / `test_device_hub` / `test_device_link` / `test_device_launch` / `test_device_provider` / `test_hooks_substrate` 等） | `cd backend && uv run python scripts/p3/run_spike.py`（独立 spike 后端在 127.0.0.1:8770，**不碰** :8099） |
| **验证什么** | device flow start/approve/poll、token 鉴权、`link.Msg` 分发、screen 开/复用、hook→AgentEvent 翻译、topic 亲和 pin、离线不漂移 | 起 spike 后端 → 真设备流 → 真 cli 拨出 `WS /connector/agent` → 设备上开屏跑真 `claude --dangerously-skip-permissions`（带我们的 hooks）→ attach viewer 看 现场 → 发一条真 prompt → 收 `SessionStart…Stop` 的真 hook 事件 |

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
  → ~/.claude/cheese-hook 转发器                  → 共享 hook_router
  → 先写本地 spool (~/.claude/cheese-spool,        → translate_hook → AgentEvent
     CHEESE_HOOK_SPOOL_ONLY=1)                  → code:200 = 推给活 turn 或存进 topic
  → 后台 drainer 用 curl POST                       的服务端 spool，等下次 reconcile
     {CONNECTOR_PUBLIC_BASE}/sandbox/hooks/{topic}
     带 X-Cheese-Token + X-Cheese-Event-Id      （按 event-id 去重）
```

- **事件没回来**：先看设备侧 spool 目录有没有堆积——后端不可达时 drainer 会一直重试，24h 过期清理；spool 堆积 = 设备到后端的回连断了。再看 `CONNECTOR_PUBLIC_BASE` 设备是否可达、scoped token 对不对。
- **事件丢序/重复**：后端按 `X-Cheese-Event-Id` 去重；durable 投递保证一次 link/后端抖动不丢事件，但可能重投，消费侧需幂等。

### 5.3 设备离线时的表现

- **设备掉线**：`device_hub.is_online` 转 false，「我的设备」该设备变灰「离线」。CLI 侧带退避重连 + 心跳，NAT 后也能恢复；持久 tmux 会话 `cheese` 在掉线期间**继续跑**，重连后重开屏幕即 re-attach，工作树/会话不丢。
- **已 pin 该设备的话题发 turn**：`resolve_pinned_device` 发现 pinned 设备离线 → 抛 `ScreenSetupError`「话题绑定的算力设备已离线，请重新连接该设备再继续本轮（不会漂到别的设备，以免工作树/会话错乱）」→ 该轮排队/失败重试，**绝不漂到别的在线设备**。
- **话题还没 pin、且没有任何绑定设备在线**：报「没有在线的绑定设备可运行本轮（self-hosted 设备未连接）」。
- **解绑/撤销 token**：`DELETE /my/devices/{id}` 或服务端撤销 durable token → 该设备所有 screen 失效、`device_hub` 标记离线、`DeviceProvider.available` 转 false、市场 listing 里 `device` 变为不可选。

---

## 速查：一次完整接入

```bash
# 目标机器上
curl -fsSL https://<你的站点>/connector/install.sh | sh
cheesehost auth login https://<你的站点>/connector      # 打印 approve_url，阻塞轮询
# 人浏览器打开 approve_url → 登录 → 批准（可命名/绑项目）
# CLI 自动 link connect 上线

# 平台侧：在小队「算力」页把设备绑给团队（或「我的设备」绑项目）
# 话题选 device 算力（全局 AGENT_BACKEND=device，或会话级 compute_profile 选 device）
```
