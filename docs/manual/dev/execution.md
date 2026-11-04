---
title: 执行通道
kind: 参考
summary: 会话进程和干活的机器分开时，工具调用怎么落到机器上、怎么判「够不着」。
covers:
  - backend/app/domain/agent/executor_transport.py
  - backend/app/domain/machine/session_work.py
  - backend/app/domain/machine/holder.py
  - backend/app/domain/agent/place.py
  - backend/app/domain/agent/environment_runner.py
  - backend/app/domain/agent/toolchain.py
  - backend/app/domain/agent/capability/matrix.py
  - backend/app/domain/agent/harness/channel.py
  - backend/app/domain/agent/harness/claude_code/remote_execution/client.py
---

# 执行通道 {#execution}

agent 的对话进程跑在一边，真正干活的机器在另一边：这一页说的是工具调用怎么落到那台机器上、租约什么时候才真的去要、以及「够不着」是怎么判出来的。

> 讲：会话进程与工作机器的分工、传输层的模式与超时、租约、地点能力位、环境与工具的发放。不讲：机器怎么被选中与准备（见[机器](/dev/machines)），模型请求怎么出去（见[计量代理](/dev/metering-proxy)），会话本身的形状（见[会话](/dev/session)）。

## 两半是怎么分开的 {#split}

- 会话进程（对话、记忆、平台工具、聊天的收发）不依赖工作机器在不在；干活的那半（文件、命令、项目 MCP）要机器。
- 这句话是被写进错误文本的：机器够不着时 agent 读到的是 `MACHINE_OUT_OF_REACH`——「这台机器现在够不着：文件、命令、项目 MCP 不可用；对话、记忆、平台工具可用。」它替掉的是 `Executor HTTP request failed: 502` 这种裸状态码，因为状态码告诉 agent「有东西坏了」，它这一轮需要知道的是还剩哪些通路。
- 会话还没租到机器时看到的工作区是占位路径 `/unavailable-project`（`DEFERRED_WORKSPACE`）；路径在离开这台机器时才是那台机器上的路径。

### 会话的 cwd 必须是**执行机上**的路径 {#cwd}

会话（`client.py` 的 `enter`）给执行器的每条命令都带着自己的 cwd，执行器先 `cwd.is_dir()` 才决定用不用它（`runtime.py` 的 `shell`）。所以那个 cwd 必须落在**执行器那台机器**上，且落在执行器沙箱的可读集里：

- **Linux**：`enter` 用 user + mount namespace 把平台侧的项目视图挂到执行器自己的路径上（`_place`），cwd 就是那个路径。正常。
- **macOS**：没有 user/mount namespace（没装 macFUSE 时连挂载都没有），`_unplaceable` 直接判不可能。退让的那条路优先拿执行器自己的 workspace 当 cwd（`target["workspace"]`，即执行器的 `root`）；机器还没租到、那个路径还是占位符时就仍落在 `view`（见下）。
- **不许**退让到平台侧的视图路径（`central_workspace`、席位里的 `forwarded-project`）：它在会话机上，不在执行器的可读集内。`confinement.seatbelt_profile` 是 `(deny file-read* (subpath owner))` + 白名单，**按前缀拒、与路径存不存在无关**；执行器 `cwd.is_dir()` 于是拿到 EPERM（`pathlib` 只吞 ENOENT/ENOTDIR/EBADF/ELOOP，**不吞 EPERM**）→ 抛 → 502 → agent 读成 `MACHINE_OUT_OF_REACH`，屏幕上整轮报「这台机器现在够不着」。
- 2026-10-11 实测（macOS 自有设备 + 隔离档，跨机与同机都会中）：`/usr/bin/log show --predicate 'eventMessage CONTAINS "forwarded-project"'` 给出 `Sandbox: Python(<执行器 pid>) deny(1) file-read-metadata …/seats/<席位>/remote-session/forwarded-project`，就是这一条。

**兜底落在两处，只有第二处是那剂解药**（2026-10-11 实测定案）：

1. 会话侧（`client.py` 的 `enter`）：非 Linux 时如果 `target["workspace"]` 是**真目录**（机器已租到）就在那儿起；还是占位路径 `/unavailable-project`（`kind=deferred`，机器还没租到）就**仍旧落在 `view`** —— 这一档按设计就是先起来、等租约到了再重开，在这里拒绝会让整个房间的屏幕起不来。
2. 执行器侧（`runtime.py` 的 `shell`）：`cwd.is_dir()` 外面包一层 `OSError`。会话给的 cwd **看不见**（EPERM）与「它不在了」当同一件事，退回执行器自己的 `root`。

**实测记录**：房间档位 `isolated`、屏幕在 deferred 阶段起（cwd 落在 `view`）时，内核**仍会**记下那条 `deny(1) file-read-metadata`（一次实测：一轮里 9 条），但**不再致命** —— 执行器退回自己的 root，命令照跑。所以：屏幕上「够不着」消失、Wolfram 技能能出结果，但**会话进程的 cwd 仍是那份视图**（不是执行器的 workspace），落在执行器 workspace 的是**它跑的命令**。这一点正是 `client.py` 里那句 ponytail 说的「少了命名空间那层遮挡，路径字符串与执行机不一致」，是本地开发的已知上限。

## 传输层：三种形态 {#transport}

`executor_transport.RemoteClient` 按配置分三种形态，调用走哪条由它决定：

| 形态 | 调用怎么走 | 典型场景 |
| --- | --- | --- |
| `device` | 平台 HTTP 直连（`POST` 一条 JSON），凭据在 `Authorization: Bearer` | 自有设备（连接器在线） |
| 子进程 | `subprocess.run(self.command("request"), …)`，`ssh -T -o BatchMode=yes -o ConnectTimeout=10` | 走 SSH 的执行器 |
| `deferred` / `unavailable` | 不落地：`context_fs` 的 tree 返回空、`ping` 报工作区；别的调用直接抛 `MachineOutOfReach` | 机器还没租到 / 租约不在手上 |

超时与重试的常数：

| 常数 | 值 | 为什么是这个数 |
| --- | --- | --- |
| 单次操作期限 | 660 秒 | 连接的读超时；到点抛 `TimeoutError`，被认成够不着 |
| `CONNECT_RETRY_WINDOW_S` | 180 秒 | 应用发布重建后端容器期间，每个工具调用都会 ECONNREFUSED；连接被拒意味着请求根本没发出去，等它是诚实的（2026-09-15 实测一间房四分钟里 `Bash`/`Read`/聊天全被拒） |
| `CONNECT_RETRY_MAX_DELAY_S` | 5 秒 | 重试间隔上限 |
| `MACHINE_READ_CHUNK_BYTES` | 21000 | 从机器上读文件的单块大小 |

## 够不着 vs 这次调用失败了 {#out-of-reach}

判据是 `OUT_OF_REACH_STATUSES = {502, 503, 504}` 加一个要看形状的 409：

- 502/504 是中间那一跳转不过去，503 是执行器没在听 → 够不着。
- 409 **且**带 `X-Device-Id` 头（`_device_is_offline`）→ 链路断了，同样够不着。但 409 也可能是「执行代际已经不是当前这一代」——那种机器好好的，只是租约旧了，**不算够不着**。把两者混在一起，会让一个死掉的 websocket 长得像「一次可重试的调用失败」，而聊天和平台工具还在正常工作。
- 读超时（660 秒）也算够不着：执行器一个字都没答，这一轮余下的文件与命令调用不必各自再等一次 660 秒。**连接被重置不在内**——那次请求已经发出去了，改动很可能已经做完，丢的只是应答；把一台答得出话的机器说成够不着，接下来每次工具调用都会被一次丢包当掉。
- 其余（500 是机器上某个工具处理函数抛了异常、401 是执行令牌过期、别的 4xx 是执行器比后端旧）手都好好的，抛的是 `EXECUTOR_CALL_FAILED`：「这次调用失败了，机器还在：其他工具照常可用，这一个可以重试。」
- 状态码与响应体进进程日志（`logger.warning`），agent 读不到、平台读得到。

调用方判类型而不是比字符串：`MachineOutOfReach` 的 `str()` 就是那句话，但以前只有「执行器答了 502/503/504」这一档能被认出来，真正够不着的两档（超时、连接被拒）反而认不出来。

## 租约：用的时候才要 {#lease}

- **只有真的要动手的调用才去要手**：`invoke` / `mcp` / `take`（pi 占机器），以及 `control` 里 `subtype=shell` 且 `operation=start`、`subtype=tool_hooks`、`subtype=files`（pi 的文件操作）。启动引导、上下文发现、只用平台的工具都不进这条路。读一个正在跑的命令也不需要。
- 要手的动作是 `lease_path` 上一个 `POST`：平台先等有界的一段时间让机器准备，客户端再问到期限为止。拿到之后把机器名、代际写进配置，把凭据写进 `token_file`、目标写进 `target_file`（原子替换），并关掉旧连接。返回的是「这次的手跟之前是不是同一个租约」（`changed_lease`）——换了租约、且配了 `target_file` 时，会先把上下文树与仓库指令读回来，然后**主动抛一个错**，让调用方先读新可用仓库的说明再发下一个工具。
- 换成新机器时工作区路径会重写：`file_path` / `path` / `notebook_path` / `command` / `body` / `cwd` 里原来的工作区前缀换成新的。**丢应答的调用不重放**（注释写得很直白：重连只给下一次调用用，这一次绝不重放）。
- 项目 MCP 不走房间的机器：平台持有它的凭据、由平台发 `POST /topics/{id}/mcp/{name}`，所以不为它占一台机器。
- 机器一侧的规矩在 `machine/session_work.py`：**这位队友自己的执行环境是唯一的来源**（`agent_environments`，见[每位队友一个执行环境](/dev/turn#seats-machine)）。规格是平台机器的沙箱，第一次要手时 `HostPool.place`（`machine/services.py`）把它放到任意一台还有空位的宿主机上，没有空位时领预热机器或现开一台，宿主机不属于房间、频道或会话；某个人的电脑这一规格，放在那个人接入项目、给得出沙箱的一台上（`environments.device_for_spec`）；不进沙箱的，就是选定的那台电脑。沙箱空闲后就销毁，什么都不留（`machine/lifecycle.py`），之后回合结束的 checkpoint 不再去问任何机器，执行路由直接答「没有要推的」（`routes/execution.py`，`sandbox_destroyed`），从没拿到过沙箱的会话也一样；下次要手时放进一个新沙箱，像任何新沙箱一样从任务分支开始，那次工具调用的结果告诉 agent 沙箱换了新的。规格是 `vm` 时，`place` 为这条会话现开一台虚拟机，那台虚拟机本身就是它的沙箱，执行器装上去时不再套 bubblewrap（`_sandboxed` 返回 False）；这台虚拟机会话闲置后推送、释放（`machine/cloud_vm.py`）。换执行环境只换这一位队友（`switch_environment`）：它先在离开的那台上做一次尽力而为的 checkpoint（`checkpoint`，等价 `cheese sync --all`，最多等 `PUSH_WAIT_S = 150` 秒），推没推上去、那台够不够得着都照常换；它的机器还在分配中时不换，答「分配仍在进行」。
- 会话主机不能干项目的活：租约所在的机器就是会话进程所在的那台时，`_attempt` 抛 `ForbiddenError("Project tools cannot execute on the session host")`——两处（`_attempt` 里 lease 那一处、以及 777 行附近那一处）。房间正在跑任务且 `if_idle` 时整个不换（`SessionWorking`，「正在运行任务，稍后再换」）。

## 沙箱归谁 {#holder}

开沙箱、计费、闲置回收和会话自己要手时，都要先知道这台沙箱是谁的。这些答案只在 `machine/holder.py` 里给，别处不自己去查项目或频道：

| 问题 | 方法 | 频道（`Room`）的答案 | 个人芝士对话（`Personal`）的答案 |
| --- | --- | --- | --- |
| 谁能让它开一台 | `require_use` | 项目名单上的人和 AI 队友 | 对话的主人 |
| 谁付钱 | `payer` / `payers` | 项目所在团队 | 主人自己的个人额度 |
| 要手时锁哪一行 | `lock_for_work` / `lock` / `look` | 频道那一行；归档的频道不接新的工具调用，但它的沙箱照样能销毁 | `assistant_conversations` 那一行 |
| 会话在哪种环境里干活 | `environment` | 会话自己选的，否则任务、频道、项目的默认 | 总是平台机器上的标准沙箱 |
| 是否正在干活 | `at_work` | 频道里任何一个对话有没跑完的一轮 | 不算（个人对话的一轮不记在 `agent_turns`），只看最近一次工具调用 |
| 机器上的目录名 | `namespace` / `namespace_of` | 项目编号，同项目的沙箱共用包缓存 | 对话编号，什么都不共用 |
| 会话凭据签给的是不是它 | `signed_for` | 凭据里的项目和频道代数与现在一致 | 凭据里的主人和对话与现在一致 |

一台沙箱的行（`sandbox_homes.topic_id`）和它的计时（`compute_runs.topic_id`）都指向对话总表 `conversations`，不指向某一种对话的表。频道里每条会话、每个任务和支线的沙箱都算频道的；个人芝士对话在总表里登记为 `personal`，`project_id` 为空，它的沙箱和计时的 `project_id` 也为空。别人的电脑和不进沙箱直接用只有频道的环境会选到，项目以外的归属方只走平台机器上的标准沙箱。

### 个人芝士对话的沙箱 {#personal-sandbox}

个人芝士（`agent/personal/session.py`）的会话在部署的会话主机上跑，自己的工具（`read`、`write`、`edit`、`ls`、`find`、`grep`、`bash`）落在这段对话自己的沙箱里。沙箱和频道的一样按需开：第一次调用这些工具时，会话用主人的个人凭据（`cxpu_`）到 `/topics/{对话}/sessions/{对话}/work-lease` 要手，只聊天、只用平台工具的问题不开机。

- 一段对话只有一个会话，会话编号就是对话编号；要手时平台按需写下这条 `agent_sessions`。
- 执行凭据是单独一种（`personal-execution+jwt`），只签给主人和这段对话；执行路由按主人核对，只放行平台自己的主机，不写派发记录。执行路由由设备连接的属主进程提供，**属主进程要随这次改动一起发布**，否则个人对话拿到沙箱后调用不通。
- 沙箱里不放平台的任何凭据：`CHEESE_PROJECT` 为空，`cheese` 命令在里面什么都做不了。
- 进度不发到任何频道，只记成运行记录。
- 闲置回收和频道的沙箱一样；回收后下一次要手告诉芝士「沙箱环境已换成新的」，并说明之前的文件都不在了（个人沙箱没有检查点）。
- 主人的个人额度用完时，沙箱随下一次清扫销毁，新的不开。
- 文件进出沙箱不经过会话机，也不给沙箱凭据。芝士的 `cheese_library(action=give)` 和 `cheese_library(action=take)`（`backend/sandbox/cheese`）先在会话机上问平台要只管一份文件、几分钟内有效的地址（传：`/assistant/agent/files/slot` 领一次上传，在沙箱里算好整份的 SHA-256 和每段的 MD5，再按段 `/files/part` 领地址；取：`/files/link`），再在沙箱里用 `curl` 往这些地址传或取。对象存储给的是预签名地址；存在本机磁盘时是 `/files/incoming/{令牌}`（一段一个）、`/files/outgoing/{令牌}`，前面补上 `frontend_url` 和 `/api`。所以沙箱要能经出口代理连到对象存储或平台的公网地址。会话机这一侧在 `pi/runner.py` 的 `_platform_tool` 里把在机器上跑命令的能力交给平台工具，只给能用这台机器干活的会话（`tools.works`）；只读房间机器的文档芝士拿不到。

## 地点：我们的东西放在哪、能给什么 {#place}

`domain/agent/place.py` 回答两个问题，答案只在这一处：

| 名字 | 值 | 说明 |
| --- | --- | --- |
| `footprint_root()` | `.cheese` | 平台在**机器主人的 `$HOME`** 下写的唯一一个目录；`cheese uninstall` 删的就是它 |
| `session_platform_dirs()` | `(".cheese", ".claude")` | **会话 home 内部**的名字，当前那个在前：`.claude` 是更早的启动器装进去的地方，房间不搬家，所以读要按这个顺序读全（只读当前的那个，会对一间真在跑执行器的房间答「这间房从来没有执行器」） |
| `STAGED_DIR` | `attachments` | 给 agent 取来的文件放这里，故意不放在工作树里 |
| `CHECKOUT_DIR` | `room` | 工作树相对会话 home 的名字，**在这里定**，建目录的代码从这里读——规则点名的东西和目录真正的名字必须是同一个字符串，否则改名之后规则还在拒绝 `room/` |

- 机器主人自己的 `~/.claude` 与平台无关：那是他的凭据、设置和全部会话记录，这里不写、不搬、不删。卸载只走足迹根目录。
- 有六个程序 import 不到这个模块，各自带着一份抄本（环境运行器、清理脚本、执行器引导、会话转移、`backend/sandbox/cheese`、Go 写的连接器）；`backend/tests/unit/test_footprint_root.py` 把它们钉在这一份上。

### Claude login directories {#claude-logins}

Each device enrollment owns its Claude logins. Its root is `~/.cheese` for the default profile and `~/.cheese/profiles/<profile>` for a named profile. The first login lives at `<root>/logins/claude-login`; further logins live at `<root>/logins/claude-logins/<local_id>`. `cheesehost --profile <profile> claude login`, `status`, and `logout` operate only on that enrollment. The desktop bridge passes the same profile to the connector. Pinned binaries remain shared at `~/.cheese/claude/versions/`.

On first start, the connector moves the old shared login directories into the single enrolled profile's store. If several profiles already exist, the original default profile inherits the old store. The move preserves local ids and removes the old directories. A destination that already exists is a login already moved: the old directory stays where it is and is reported on stderr. A session started before the move keeps its old `CLAUDE_CONFIG_DIR` and can recreate the old directory this way. The finished move is recorded in `~/.cheese/.claude-logins-migrated`, and later starts read no old directory, so a login logged out later never returns from one. A move that fails is reported and retried on the next start; it never stops the connector from starting. Subsequent starts read only the enrollment's store.

On macOS, migration writes `<login>/keychain/security` to map the moved login's OAuth and Console service names to its original Keychain items. CLI commands, probes and session launches use this command, so a session started before migration still refreshes the same items used by the enrollment. `CLAUDE_SECURESTORAGE_CONFIG_DIR` points to the new login directory, keeping `.credentials.json` fallback there. No command reads the removed directories. New logins use their own Keychain items without a mapping.

The backend probe resolves `CHEESE_PROFILE_ROOT` on the device, and session launches find the login files under that same root. `claude_logins` already identifies a login by `(device_id, local_id)`, so identical local ids in different enrollments remain separate without a database migration. Uninstall removes that enrollment's login files while retaining other profiles and shared binaries.

## 环境与工具的发放 {#toolchain}

- `environment_runner.py` 是一个标准库程序，在被选中的执行边界里跑：装依赖前先拿锁（`flock`，Windows 上走 `remote-execution/portable.py` 的原语），防止两个安装器同时开工；`exec` 的那一刻把锁放掉，之后 agent 的复用由 tmux 管。
- `toolchain.py` 是**版本与摘要的唯一一处**：typst `0.15.1`、pandoc `3.11`、uv `0.12.15`、fj `0.6.0-cheese.2`、gh `2.62.0`、ripgrep `15.2.0`（pi 的 grep、find 在执行机上搜索用它）、Windows 的 Python `3.13.13` 与 git `2.55.0.windows.5`，字体按 commit 钉住（`_SANS_COMMIT` / `_SERIF_COMMIT`）。摘要写在这里而不是下载脚本里的原因也在文件里：typst 0.15.1 与 pandoc 3.11 根本没有发布校验和资产（2026-09-16 记的）。
- `capability/matrix.py` 按 harness 声明行为，不留空格（I6），连还没进注册表的 harness 也写进去，版本常数也钉在里面。

## 边界与坑 {#traps}

- 判「够不着」只看状态码是不够的，还要看 409 的形状；反过来说，把它放宽成「一切非 200」会让 agent 在机器好好的时候放弃整轮的文件与命令操作，并向人报告机器掉线——那是假话。
- 租约不是会话的属性而是「这次调用」的属性：没要手之前什么都别假设，`deferred` 形态下 `ping` 与 `context_fs tree` 之外的一切都会抛。
- 换执行环境、房间清理、虚拟机沙箱的闲置释放都只做**一次**尽力而为的 checkpoint，之后照常进行，不拒绝、不重试、不为它留着机器：推不上去的提交和没提交的改动跟着那台机器一起没了，平台不留底。
- 会话主机与工作机器重合是明确禁止的，不是「性能差不多的选择」。
