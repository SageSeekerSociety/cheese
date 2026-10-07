---
title: 执行通道
kind: 参考
summary: 会话进程和干活的机器分开时，工具调用怎么落到机器上、怎么判「够不着」。
covers:
  - backend/app/domain/agent/executor_transport.py
  - backend/app/domain/machine/session_work.py
  - backend/app/domain/agent/place.py
  - backend/app/domain/agent/environment_runner.py
  - backend/app/domain/agent/toolchain.py
  - backend/app/domain/agent/capability/matrix.py
  - backend/app/domain/agent/harness/channel.py
---

# 执行通道 {#execution}

agent 的对话进程跑在一边，真正干活的机器在另一边：这一页说的是工具调用怎么落到那台机器上、租约什么时候才真的去要、以及「够不着」是怎么判出来的。

> 讲：会话进程与工作机器的分工、传输层的模式与超时、租约、地点能力位、环境与工具的发放。不讲：机器怎么被选中与准备（见[机器](/dev/machines)），模型请求怎么出去（见[计量代理](/dev/metering-proxy)），会话本身的形状（见[会话](/dev/session)）。

## 两半是怎么分开的 {#split}

- 会话进程（对话、记忆、平台工具、聊天的收发）不依赖工作机器在不在；干活的那半（文件、命令、项目 MCP）要机器。
- 这句话是被写进错误文本的：机器够不着时 agent 读到的是 `MACHINE_OUT_OF_REACH`——「这台机器现在够不着：文件、命令、项目 MCP 不可用；对话、记忆、平台工具可用。」它替掉的是 `Executor HTTP request failed: 502` 这种裸状态码，因为状态码告诉 agent「有东西坏了」，它这一轮需要知道的是还剩哪些通路。
- 会话还没租到机器时看到的工作区是占位路径 `/unavailable-project`（`DEFERRED_WORKSPACE`）；路径在离开这台机器时才是那台机器上的路径。

## 传输层：三种形态 {#transport}

`executor_transport.RemoteClient` 按配置分三种形态，调用走哪条由它决定：

| 形态 | 调用怎么走 | 典型场景 |
| --- | --- | --- |
| `device` | 平台 HTTP 直连（`POST` 一条 JSON），带 `X-Cheese-Token` | 自有设备（连接器在线） |
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

- **只有真的要动手的调用才去要手**：`invoke` / `mcp` / `project_tools`，以及 `control` 里 `subtype=shell` 且 `operation=start`、`subtype=tool_hooks`、`subtype=files`（pi 的文件操作）。启动引导、上下文发现、只用平台的工具都不进这条路。读一个正在跑的命令也不需要。
- 要手的动作是 `lease_path` 上一个 `POST`：平台先等有界的一段时间让机器准备，客户端再问到期限为止。拿到之后把机器名、代际写进配置，把凭据写进 `token_file`、目标写进 `target_file`（原子替换），并关掉旧连接。返回的是「这次的手跟之前是不是同一个租约」（`changed_lease`）——换了租约、且配了 `target_file` 时，会先把上下文树与仓库指令读回来，然后**主动抛一个错**，让调用方先读新可用仓库的说明再发下一个工具。
- 换成新机器时工作区路径会重写：`file_path` / `path` / `notebook_path` / `command` / `body` / `cwd` 里原来的工作区前缀换成新的。**丢应答的调用不重放**（注释写得很直白：重连只给下一次调用用，这一次绝不重放）。
- 项目 MCP 不走房间的机器：平台持有它的凭据、由平台发 `POST /topics/{id}/mcp/{name}`，所以不为它占一台机器。
- 机器一侧的规矩在 `machine/session_work.py`：**房间的算力选择是唯一的来源**，会话行上那份 `choice` 只是副本（读名册、算力分布、清理清单的人要看到一致，而解析不去问它）。选云端的会话，手是平台云主机池里的一个沙箱：第一次要手时 `HostPool.place`（`machine/services.py`）把它放到任意一台还有空位的宿主机上，没有空位时领预热机器或现开一台，宿主机不属于房间、话题或会话。沙箱空闲后休眠、只占宿主机的盘不占空位，下次要手时在原地唤醒；休眠太久或原宿主机没有空位的，先归档再在别处恢复（`machine/lifecycle.py`）。房间选的是整台云虚拟机（`whole_machine`）时，`place` 为这条会话现开一台虚拟机，执行器装上去时不进沙箱（`_sandboxed` 返回 False）；这台虚拟机不休眠也不归档，会话闲置后推送、释放（`machine/cloud_vm.py`）。换工作电脑是**房间的动作**：每条会话先在自己离开的那台上做一次尽力而为的 checkpoint（`checkpoint`，等价 `cheese sync --all`，最多等 `PUSH_WAIT_S = 150` 秒），推没推上去、那台够不够得着都照常搬，**全部搬完才写房间那一项**；一条搬不了（它的机器还在分配中）就是房间那项不变，已经搬过的会话下一轮自己走回来。
- 会话主机不能干项目的活：租约所在的机器就是会话进程所在的那台时，`_attempt` 抛 `ForbiddenError("Project tools cannot execute on the session host")`——两处（`_attempt` 里 lease 那一处、以及 777 行附近那一处）。房间正在跑任务且 `if_idle` 时整个不换（`SessionWorking`，「正在运行任务，稍后再换」）。

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

## 环境与工具的发放 {#toolchain}

- `environment_runner.py` 是一个标准库程序，在被选中的执行边界里跑：装依赖前先拿锁（`flock`，Windows 上走 `remote-execution/portable.py` 的原语），防止两个安装器同时开工；`exec` 的那一刻把锁放掉，之后 agent 的复用由 tmux 管。
- `toolchain.py` 是**版本与摘要的唯一一处**：typst `0.15.1`、pandoc `3.11`、uv `0.12.15`、fj `0.6.0-cheese.2`、gh `2.62.0`、ripgrep `15.2.0`（pi 的 grep、find 在执行机上搜索用它）、Windows 的 Python `3.13.13` 与 git `2.55.0.windows.5`，字体按 commit 钉住（`_SANS_COMMIT` / `_SERIF_COMMIT`）。摘要写在这里而不是下载脚本里的原因也在文件里：typst 0.15.1 与 pandoc 3.11 根本没有发布校验和资产（2026-09-16 记的）。
- `capability/matrix.py` 按 harness 声明行为，不留空格（I6），连还没进注册表的 harness 也写进去，版本常数也钉在里面。

## 边界与坑 {#traps}

- 判「够不着」只看状态码是不够的，还要看 409 的形状；反过来说，把它放宽成「一切非 200」会让 agent 在机器好好的时候放弃整轮的文件与命令操作，并向人报告机器掉线——那是假话。
- 租约不是会话的属性而是「这次调用」的属性：没要手之前什么都别假设，`deferred` 形态下 `ping` 与 `context_fs tree` 之外的一切都会抛。
- 换机器、房间清理、整台云虚拟机的闲置释放都只做**一次**尽力而为的 checkpoint，之后照常进行，不拒绝、不重试、不为它留着机器：推不上去丢的只是上一轮 Stop checkpoint 之后的改动，那一轮的快照在平台上。
- 会话主机与工作机器重合是明确禁止的，不是「性能差不多的选择」。
