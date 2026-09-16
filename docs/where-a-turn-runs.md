# 一轮活跑在哪台机器上

芝士干活需要一台机器。这份文档说清楚今天有几种机器、一轮活怎么被分配到其中一台，以及没有任何一台可用时会发生什么。

面向零前置知识的读者。

---

## Central sessions and private chat execution

All Claude Code and RC sessions run on the central device configured by `AGENT_SESSION_DEVICE_ID`. Ordinary rooms keep their selected machine for project files, shell commands, environment scripts, custom stdio MCP processes and preview. Private chats use a temporary container on the central host. A missing or offline session host produces an explicit setup failure.

Each chat gets a separate execution container with a read-only image and 64 MiB of writable temporary storage. Shell commands, file operations and Cheese CLI run there. The container has no host directory mounts or model credentials. It retains drafts across turns while it lives; releasing the chat removes its scratch files. Published documents remain in platform storage.

Build the executor on the central device with `docker build -f backend/sandbox/Dockerfile.private -t cheese-private-executor:2.1.265 .` from the repository root. `PRIVATE_CHAT_EXECUTOR_IMAGE` selects the installed image. General network access remains available; backend authorization governs platform operations. The configured session host must have this image available before accepting private chats.

The remaining sections describe ordinary work topics and their selected compute providers.

---

## 一、两条执行路，都是别人的机器

代码里装配了两个（`agent/compute.py` 的 `build_compute_pool`）：

| 路 | 机器是什么 | 谁的 |
|---|---|---|
| **自托管设备** | 用户自己接进来的机器（笔记本、常驻服务器） | 别人的 |
| **Cloud** | 按话题现开的一台云主机，用完释放 | 我们开的，一次性 |

设备那条总是装上；Cloud 只在这个部署配了云平台的地址和密钥时才装。

These choices select the ordinary room's execution machine. Claude Code and RC run on the separately recorded central session host, which does not appear as a project execution choice. The two locations are described in `remote-execution.md`.

## 这些机器上有什么工具

工具分三层，落点各不相同：

| | 是什么 | 谁放的 |
|---|---|---|
| **平台必需** | tmux、git、python3、harness 本身 | 机器接入时检查，缺了当场拒绝（`machine/enrollment.py`）。没有它们平台自己就跑不起来 |
| **项目依赖** | 这个仓库要什么 | 项目自己的初始化脚本，装进房间的 HOME；包本身落在项目共用的 store 里 |
| **文档工具** | typst、pandoc、uv、一对中文可变字体 | 平台放（`machine/toolchain_dist.py` + `agent/machine_launcher.py`） |

第三层放在机器的 home 下（`$CHEESE_TOOLCHAIN`，即 `~/.cheese/toolchain`），不在房间的
session home 里：**这些属于机器，那台机器上每个房间共用一份**，而且比任何一个房间活得久。
路径里带版本号，所以升级是落在旧的旁边，下次启动才取，没有东西需要卸载。

**它们是能力，不是依赖。** 只回答问题的房间一样都不需要，所以放置是脱离启动脚本跑的，
既不会让一轮失败也不会让它变慢。代价是刚开机的房间可能还取不到——`skills/documents`
因此要求 agent 先确认工具在不在，不在就如实说这台机器现在做不了。

第二层的装法则相反：**脚本每个房间各跑一遍，包只存一份。** 房间的 HOME 是各自的（hook
事件要按房间分开落盘），而 uv、pnpm、npm、pip 默认都把自己的 store 放在 HOME 里，所以
不管的话同一个项目每开一个房间就多一份完整的依赖树。启动脚本因此把它们指向
`~/.cheese/store/<项目>/`（`agent/machine_launcher.py`，路径来自
`device_provider.device_store_dir`）——uv 和 pnpm 会从 store 里**硬链接**出来而不是拷贝。

量级不是估的：CI 上实测同一个 venv，从热 store 建出来是 56891 个共享文件加 8MB 独占，
留着不共享则是 1.3G（#1104）；容器时代同样的问题让一个项目的 220 个 worktree 吃掉
236GB。

**按项目分，不按机器分。** 同一个项目的房间本来就共用一个仓库、能读彼此的 checkout，
store 在这条线以内；跨项目则是一条真实存在的线。今天设备房间之间其实毫无隔离
（`device/supply.py`：每个 screen 都是 `host`），所以这么分眼下不多挡什么——是为 #358
留的挂载点。但 #358 要自己解决一件事：**硬链接跨不过 bind mount**（`link(2)` → EXDEV，
同一个文件系统内也一样，dev 上实测过），所以把 store 只读挂进隔离房间会把去重原样还
回去。隔离和去重不是同一个开关。

`store/<项目>/uv-python` 是这里唯一不能当缓存清的东西：它不是缓存，是 venv **指向**的
解释器。清掉 `uv-cache` 只是下次重下；清掉 `uv-python` 会让这个项目所有 venv 指空，
直到某次 `uv run` 把它们重建出来。

二进制按 digest 钉死在 `toolchain_dist` 里，而不是取上游的校验和：typst 和 pandoc
根本不发校验和（2026-09-16 实测），而钉在仓库里的 digest 还多一道 PR review，
同一个 tag 被重新打包会验不过。

字体是 `TYPST_FONT_PATHS` 指过去的，没有设 `TYPST_IGNORE_SYSTEM_FONTS`——我们的字体
本来就排在系统字体前面，屏蔽掉只会白白拿走用户自己装的字。缺中文字体的失败是静默的：
typst 退出码 0、PDF 大小正常、每个中文字是空心方框。

## 二、菜单和执行层现在是同一个答案

项目设置里能选的算力（`agent/market.py` 的 `compute_listings`）就是上面这两条。哪一条挂「默认」，和一个没有做过选择的话题实际落在哪，**是同一个函数算出来的**：`compute_default_name`。

```python
# agent/market.py
Cloud 能开机 → 默认是 Cloud；开不了 → 默认是自托管设备
```

`build_compute_pool` 拿的就是这个值。**这两个答案必须是一个函数**：分开写就会分家，而分家之后看不出来——界面上写着一台机器，话题跑在另一台上，两边各自都是自洽的。

一轮活的落点按这个顺序定（`chat.py` 的 `_resolve_compute_id`）：

1. 话题自己选的（第一轮跑完就钉住）
2. 项目记住的上一次选择
3. 团队的默认
4. 都没有 → `compute_default_name()`

## 三、一台机器都没有的时候

**没有设备连上来、也没配 Cloud 的部署，一轮活会失败，并且说清楚为什么。**

默认落到自托管设备那条路上，而那条路开工前先解析「这个项目有哪台在线的机器」，解析不到就直接停：

```
没有在线的绑定设备可运行本轮（self-hosted 设备未连接）
```

这条话进房间，是一条明确的失败，不是一次静默的降级。市场页的算力选择器同时是空的——`available` 两条都是假，没有东西可选。两边说的是同一件事。

配了 Cloud 的部署则相反：默认是 Cloud，第一轮会为这个话题开一台机器，房间里先收到「机器正在创建」，开好了自动接着跑。

## 四、可见性：两档里只有一档能跑

选定机器之后还有第二个问题：这一轮能看见那台机器上的多少东西。

| 档 | 意思 | 状态 |
|---|---|---|
| `host` | 看得见整台机器，能操作上面的服务、进得去其他房间的容器 | **唯一能跑的** |
| `isolated` | 只看得见自己那棵工作树 | **没有传输层，等于不存在** |

`has_runnable_transport` 只对 `host` 返回真，而默认档是从「哪档真能跑」推导出来的（`device/supply.py` 的 `default_visibility`），所以今天**每个话题实际都是整机可见**。

这一档不是遗漏，是明确推迟的决定（2026-08-18）：接进来的机器目前全是队友的，威胁模型是误操作不是攻击，而一个做错的沙箱比没有沙箱更糟。**重新考虑的触发条件是：第一台不属于队友的机器接进来。**

## 五、能从这些机器上拿回来什么

**给人看结果：`cheese artifact <文件>` 点名一个网页或 SVG。** 平台读那个文件、渲染进预览面板。这条路跟机器无关——文件在工作树里，工作树在哪台机器上都一样。

**看现场：**设备上的那个屏通过连接器拨出来的链路回传，房间里能实时看，也能直接输入——它是什么、给谁的，见下一节。

**看跑起来的应用：`cheese serve <端口>`。** 芝士把 dev server 起在那台机器的 `127.0.0.1` 上，报一个端口，房间的预览面板里就能直接用它——HTTP 和 WebSocket（dev server 的热更新）都走。

这条路和上面两条一样，**没有一条网络路径是通往机器的**。平台仍然打不进去，是那台机器多拨出来一条 WebSocket，浏览器的请求在这条连接上分流回去（`agent/preview_tunnel.py` 是机器那一半，`agent/preview_hub.py` 是平台这一半）。

三件事把它的边界钉死：

- **地址永远不在线上。** 机器那半只拨一个地方：`cheese serve` 在**那台机器的磁盘上**写下的那个端口。平台发下去的帧里根本没有主机字段，所以平台就算被攻陷也没法让一台笔记本去扫别的端口。
- **不是连接器那条链路。** 连接器那条是一轮活的命脉（prompt、hooks、现场中继），两端都用一把锁串行发帧；dev server 的静态资源和热更新排在 prompt 前面就是活活拖死一轮。同一条网络路径、同一个网关、同样是拨出——但另一条连接，另一个失效域。
- **能看的人，本来就能在那台机器上敲命令。** 预览的门槛跟现场同一道（话题所在项目的成员/所有者）。现场是可写的（见下一节），所以「读一个 loopback 端口」不是一道新增的权限，是已有边界里面的一件小事。反过来，页面本身是芝士写的，所以 iframe 拿不到任何凭证：不带 `?token=`（那是页面自己的 JS 读得到的），只有一个按路径限定的 HttpOnly cookie，且 sandbox 不给 `allow-same-origin`。

要给人看一个**静止**的结果，仍然是 `cheese artifact` 更省事——它连机器在不在线都不关心。

## 六、现场：给开发者 debug 用的，长期保留

**现场是一个真终端**，接在芝士干活的那块屏幕上，浏览器里看到的是逐字节的真实画面。

**它是给开发者的**，不是给普通用户的产品功能。存在的理由很实际：**agent 会以事件流看不出来的方式出问题。** 房间里那套结构化事件（消息、工具调用、结果）只覆盖 harness 愿意上报的东西；当 harness 本身卡住、报错只印在屏幕上而没有变成事件、或者根本没起来，**唯一还能看见真相的地方就是那块屏幕**。这不是假设——平台有过一次十小时零输出，四个人猜了一整天，而正确答案（一行 401）一直印在屏幕上，只是没人去看。

**今天它是可写的**：连接器的 viewer socket 把按键转发进窗格。**「能直接上手操作」是权宜之计，不是设计**——它在那儿是因为 harness 不稳到需要人接管；长期形态是**能看、不能碰**。

**这一条刻意不进 harness 契约。** 现场要求那个 harness 是交互式终端程序、跑在 pty 里，而契约里其他所有东西（事件词汇表、游标、resume token）都是抽象的。**一个没有 TUI 的 harness 就是没有这个 debug 面，不该因此被拒之门外**——它可以有别的形态的 debug 面，或者干脆没有。别让一个调试工具变成接入门槛。

## 七、已定但还没做完的

**芝士自己提交、推送、开 PR。** 平台不再替它写工作树。详见 `docs/plans/2026-08-27-retire-jj-design.md`。

## 八、归档退掉什么

Open rooms retain their agent sessions and environments, including while idle or
after accepting a delivery. Archival is an authenticated owner/admin action.
It records a cleanup deadline using `TOPIC_ARCHIVE_CLEANUP_DELAY_S` (default
300 seconds). Deployment and later configuration changes do not move that deadline.

The host's `cheese-room-cleanup.timer` triggers a check every minute. Startup and
device reconnect also retry due operations. Each operation records its resource
generation, directories, progress and failure reason in PostgreSQL. It inventories
both device storage roots in one visit. Unknown historical directories are retained;
absence from the database never grants deletion permission.

Cleanup first requests a graceful agent exit and verifies that no process holds the
resource open. Stop commands share a device lock and durable completion receipt,
including subprocesses that could outlive a timed-out caller. Unpublished source or
an unconfirmed transcript keeps cleanup pending. The platform does not create a
separate backup of dirty working trees or unpushed commits.

Raw `.claude/projects/**/*.jsonl` files, including subagent files, are collected as
original byte ranges during execution by the hook sender. Hooks wake collection;
reconciliation every five seconds catches missed hooks and delayed writes. Byte-range
receipts follow object storage and database commits. Final cleanup reconciles the
file set, drains outstanding hook events, and has the backend reread and verify the
complete stored contents one bounded chunk at a time. Verification progress survives
a worker restart; a new cleanup operation verifies all chunks again. It checks the
files again immediately before removal.
`TRANSCRIPT_S3_BUCKET` must name a private bucket; the public uploads bucket is never
used implicitly. Existing S3 connection credentials are reused. Immutable source
identity records accompany the raw chunks so their database index can be rebuilt
after restoring an older database backup.

Authorized room participants can list and download original readable files through
`GET /topics/{id}/transcripts` and `GET /topics/{id}/transcripts/{file_id}`. This does
not automatically inject transcript history into later prompts. Existing tar
archives remain under `TRANSCRIPTS_DIR` and retain their hourly additive R2 mirror.
The old recurring tar-copy collector is removed.

Before cleanup takes ownership of deletion, unarchive cancels it and reuses retained
resources. If a stop or worktree move has an unresolved outcome, unarchive reports
that it must finish confirmation first. Once deletion is claimed, reopening allocates
a new resource UUID and drops only obsolete session-resume pointers. Published Git
branches, platform memory, room messages and task records remain. Old cleanup commands
keep their original UUID and parked backend worktree path; they cannot target the
replacement. Cloud machines are deleted by their recorded allocation ID.

`GET /topics/{id}/cleanup` reports the deadline, stage, progress and pending reason.
Installation and storage configuration are described in
[`deploy/README-room-cleanup.md`](../deploy/README-room-cleanup.md).

---

## 相关

- `backend/app/domain/agent/compute.py` — 两条路的装配与默认值
- `backend/app/domain/machine/toolchain_dist.py` — 文档工具钉在哪个版本、哪个 digest
- `backend/app/domain/agent/machine_launcher.py` — 把它们放到机器上的那一段
- `backend/app/domain/agent/preview_tunnel.py` — 运行环境预览：机器那一半（发到机器上跑的那份）
- `backend/app/api/routes/app_preview.py` — 运行环境预览：平台这一半，两端都在里面
- `backend/app/domain/agent/market.py` — 菜单，以及默认值这一个答案
- `backend/app/domain/device/supply.py` — 可见性两档与「哪档真能跑」
- `docs/device-self-hosting.md` — 自托管设备那条路的完整说明
