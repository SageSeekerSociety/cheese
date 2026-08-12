## 状态：**已实现，全量套件跑完，PR #301 的迁移链冲突已修**（本地 main `b4a8a9bfed4f`）

对应 [#186](https://github.com/SageSeekerSociety/cheese/issues/186) 第一节「身体能不能用」。第二节（agent 待在哪）维持现状 A，不在本卡范围；第三节归 #187。

§3 那张落点表现在是**已改**，不是待办。闸门实测：

| 项 | 结果 |
|---|---|
| ruff check + format（全 backend，含 `alembic/`） | All checks passed / 758 files formatted |
| pyright `app/` | 0 errors |
| alembic heads | 在 CI 的 merge ref 上是单头 `b7e3c19d4f80`；**本地报 `KeyError` 是预期的，见 §6** |
| repo guards（仓库规则 + actions SHA 钉版） | PASS |
| migration fork vs main | PASS（合并不会劈叉 alembic 链） |
| **全量后端套件** | **4066 passed, 31 skipped, 24 failed** —— 24 个全部是沙箱环境问题，逐条见 §5 |

---

## 0. 现场：本卡自己曾经就是一次 #186 的复现

写设计的那一轮，沙箱所在宿主机的磁盘从 **可用 78.6G → 0**：

```
开工时    cheese status: 磁盘 可用 78.6G / 251.8G（已用 69%）
40 分钟后 cheese status: 磁盘 可用  0.0G / 251.8G（已用 100%）
                         并发: 全站运行中 27 轮
```

容器内能看到的全部内容加起来 1.8G（`/work` 71M、`/home` 308M、`/usr` 1.1G），242G 全在宿主机侧、别的话题的工作树和镜像里——**印证了 issue 第 1 点：磁盘满是宿主机级的，容器内既看不见也清不掉**。后果是实打实的：`uv sync` 两次都在 `No space left on device (os error 28)` 中断，`.venv` 停在 96K，`ruff`/`pyright`/`pytest` 一个都跑不起来，所以那一轮只交了设计。

**这个坑现在不在了**：宿主机盘 252G → 504G（可用 176G；#277 给 dev box 加了回收磁盘的手段），依赖装齐，实现和验证都做完了。

留着这段不是讲故事，它是这条设计唯一的一手证据：**真按本卡做完，那一轮本该被自动判定"这台废了"→ 换机器 → 接着跑，而不是停在那里等人来 @。**

---

## 1. 复核结论（对着当前 main 逐条核过）

### 1.1 比 issue 描述的更糟：认出来之后是**明确不重试**

`backend/app/domain/agent/runtime.py` 的 crash 分支（`except Exception`）末尾原本是：

```python
if not is_resume and platform_failure is None:
    resume_after = 5.0
```

普通 crash 会排自动续跑；**`platform_failure` 一旦被认出来，这行直接跳过**。`chat.py` 的 AgentResult 错误分支同构：`platform_failure` 命中时 `resume_after_s` 保持 `None`。

所以现状不是"重试了但没用"，是**根本不会再起来**，除非有人手动再 @ 一次。issue 那句"认得出来，爬不起来"字面成立。

这个"不重试"当初是对的（重试变不出磁盘）——**但它对的前提是"没有换机器这条路"**。§3 补上换机器之后，这里必须能重新排上续跑，否则换完了也不会自己接着干。

### 1.2 "换一台并把活接上：底层现成" ——这条要打折

现成的是**原语**：`provision`/`destroy`/`enroll`/`refresh` 都在 `machine/services.py`。但"把一个正在跑的话题从坏机器切到新机器"这个**动作本身，现有代码是刻意禁止的**：

- `device_provider.py` `resolve_pinned_device()` 的契约写死 `NEVER fall back to another device ...（the original drift bug）`；
- `device/sql_repository.py` `bind_topic_device()` 是 **write-once**。

所以要写的不是"接一条线"，是**一个跟"禁止静默漂移"显式对齐的重新绑定流程**。这也解释了 issue 第 3 点"让人看得见"为什么是必需项而不是锦上添花：**不能悄悄换，必须留痕**——静默漂移就是当年那个 drift bug。

### 1.3 已有的地基

`f9370d4a` (#160) 给 `ProjectMachine` 加了 `last_seen_at` + `touch_seen()`。`DeviceProvider` 在 `compute.py` `build_compute_pool()` 里**无条件入池**，不是只在 `AGENT_BACKEND=device` 才生效。

---

## 2. 对 issue 第五节那个"要定的问题"的答复

> **判定「这台机器废了」的标准是什么？**

### 2.1 先分清两种失败

`PlatformFailure` 上加一个字段 `host_scoped: bool`——**这次失败是"这一轮"的属性，还是"这台机器"的属性**：

| code | `host_scoped` | 理由 |
|---|---|---|
| `storage_exhausted` | ✅ | 盘是机器的。换容器没用，只有换机器有用 |
| `runtime_image_missing` | ❌ | 镜像仓库/网络问题，换机器不会变好，且往往全站同时发生 |
| `workspace_vcs_perms` | ❌ | main 新加的分类；uid/chmod 问题，跟着话题走，不是机器的病 |
| `host_unreachable`（**新增**） | ✅ | issue 第 4 点那个"更隐蔽"的网络断 |

**只有 `host_scoped` 的失败才计入"这台废了"的账。**

`host_unreachable` 的识别刻意做窄：**只匹配平台自己那句 `DEVICE_OFFLINE_MESSAGE`**，不匹配 `connection refused` / `no route to host` 这类通用网络词——后者会在**模型网关**连不上时误报，而网关不是机器的属性，误判会拿健康机器开刀。

### 2.2 判定标准

**同一 device 上，连续 2 次同一 `host_scoped` code 的失败，中间没有任何一次成功轮次，即判定该 device 不健康。**

- **按 device 计，不按话题计。** 磁盘满是**机器**的属性，按 device 计收敛快 N 倍，也不会让第二个话题重新踩一遍。
- **N=2 而不是 1。** 容忍一次抖动，代价只有一轮。
- **两次不同 code 不算连续**——那是两次意外，不是一台垂死的机器。
- **任何一次成功轮次清零**（删掉健康记录行）。这是"连续"的定义，也是自愈的出口。

### 2.3 判定之后不是"删机器"，是**隔离（quarantine）**

判不健康 → 给 device 打上带**冷却期（30 分钟）**的隔离标记，而**不是**立刻 `destroy`：磁盘满可自愈；销毁要花钱、要重建环境，正是 issue 担心的"误杀代价"，而且不可逆。冷却期一到自动回到池子里，不需要人来清。

### 2.4 换身体的完整动作（对齐"禁止静默漂移"）

1. `release_topic_device(topic_id, reason=...)` —— 显式解绑，带原因，写日志；
2. 在项目算力池里挑一台**在线且未被隔离、且不是刚判死那台**的 device，重新 pin；
3. **不假装能 resume 原 session**：工作树从 git 恢复，claude session 新开，不做 `--resume`；
4. 往房间里写一条**可见的** system event（`meta.event_type = "host_swap"`，带 from/to device）；
5. **重新排上自动续跑**（即 1.1 那个缺口）；
6. 池子里一台可用的都没有 → **不解绑**（不能解到空里去）、不静默漂移，发一条说人话的 event 说明卡在哪。

第 3 步单独标红：**换机器 = 换工作树来源（git）+ 换 session（新开），不是"把原来那台的状态搬过去"**。

---

## 3. 落点（**已改**）

| 文件 | 改了什么 |
|---|---|
| `agent/platform_failures.py` | `PlatformFailure` 加 `host_scoped`；新增 `HOST_UNREACHABLE` + `DEVICE_OFFLINE_MESSAGE`（识别锚点）；`ALL_FAILURES` / `HOST_SCOPED_CODES` |
| `device/models.py` | 新表 `device_health`（`device_id` PK/FK cascade、`consecutive_failures`、`last_failure_code`、`last_failure_at`、`quarantined_until`） |
| `device/health.py`（新） | 纯判定逻辑 `judge_failure` / `is_quarantined`，不碰 DB、不碰时钟 |
| `device/repository.py` | `HostHealth` dataclass + 5 个协议方法（含 `release_topic_device`） |
| `device/{memory,sql}_repository.py` | 两个实现都跟上 |
| `device/service.py` | `record_host_failure` / `record_host_success` / `release_topic_device(reason=)` / `healthy_devices_for_project` |
| `agent/host_swap.py`（新） | `swap_topic_device`（纯决策，可脱库测）+ `handle_host_failure`（session 管道，永不抛）+ `record_host_success` |
| `agent/device_provider.py` | 首次 pin 时过滤被隔离的机器；离线错误改用 `DEVICE_OFFLINE_MESSAGE` |
| `agent/runtime.py` | crash 分支：`host_scoped` → 记账/换绑/发 event/**恢复 `resume_after`**；成功收流 → 清零健康记录 |
| `agent/chat.py` | AgentResult 错误分支同构；换成功 → 多发一个 `host_swap` event block + `resume_hint` 帧 |
| `alembic/versions/b7e3c19d4f80_*.py` | `device_health` 迁移，挂在 **`c8b1f4a70d29`** 之后（见 §6：期间被 main 顶掉三次，重挂了三次） |
| `tests/unit/test_device_health.py`（新） | 判定标准的功能测试 |
| `tests/unit/test_host_swap.py`（新） | 换身体全流程的功能测试 |

**一个刻意的克制**：`resolve_pinned_device()` 只在**首次 pin** 时过滤隔离机器，**绝不**在这里移动一个已 pin 的话题。所有换绑都走 `host_swap` 那条留痕的路——一个"解析器能悄悄改掉的 pin"就是当年的 drift bug。代价是：判死时若池里恰好没有健康机器，话题会继续 pin 在病机器上，等下次 @ 再判一次（那时冷却期可能已过、或有新机器上线）。这是收敛的，且没有静默漂移。

---

## 4. 下一步

递验收卡（`cheese accept-request`）。注意本仓沙箱**看不到远端**（`jj git fetch` 因 git 2.39 < 2.41 不可用，`gh` token 只有 actions/checks 域），所以"已推送/已合并/CI 绿"这类话一律是**声明不是观测**，要人来确认。

---

## 5. 全量套件那 24 个失败，逐条

没有一个来自本卡的改动。三类，前两类 CLAUDE.md 已记载：

| 数量 | 失败位置 | 根因 | 记载状态 |
|---|---|---|---|
| 22 | `tests/unit/test_machine_service.py`(21)、`tests/unit/test_tmux_control.py`(1) | 沙箱缺二进制：`kill`（无 procps）、`ssh-keygen`（无 openssh） | 已记载 |
| 1 | `tests/integration/test_market_api.py::test_market_lists_ai_and_compute_pools` | 无 provider 凭据，AI 池诚实地报 unavailable | 已记载 |
| 1 | `tests/unit/test_cheese_cli.py::test_await_log_lives_outside_the_worktree` | **新发现，见下** | 未记载 |

**新发现的那个沙箱假阳性**：`backend/sandbox/cheese` 的 `_await_log_path()` 里，`CHEESE_AWAIT_LOGS` 的优先级高于 `HOME`；而 agent 沙箱恰好设了这个变量（`/home/node/.claude/cheese-await`）。测试只 monkeypatch 了 `HOME`，所以在沙箱里断言必挂，在 CI 里（没有这个变量）是绿的。

不是代码缺陷，也不是本卡碰出来的——`backend/sandbox/cheese` 是 2026-08-11 别人刚写的文件。一行 `monkeypatch.delenv("CHEESE_AWAIT_LOGS", raising=False)` 可治，但与 #186 无关，**故意不并进本卡**，留给该文件的作者决定。

### 一条方法论上的教训

第一次跑全量拿到 `28 failed / 767 errors`，看着像灾难，其实**结果作废**：我在套件跑的过程中做了 rebase 和改迁移，树被换了。767 个 error 全是 DB 连接失败。稳住树重跑就是 `24 failed / 0 errors`。

**跑长套件期间不要动工作树**——包括看起来无害的改动。代价是一次 9 分钟的空跑加一轮误判。

---

## 6. 迁移链被顶掉三次，第三次修完本地是"故意不自洽"的

`down_revision` 改过三轮：`d4a1b6f27c90` → `b8e1d4c70a92` → `c1d7e0a4b839` → **`c8b1f4a70d29`**。前两次是本地就能看到的 main 前进；第三次不是。

**第三次的特殊之处：`c8b1f4a70d29` 这个文件在沙箱工作区里根本不存在。** 本地 main 停在 `b4a8a9bfed4f`，还没同步到那条迁移，而 `jj git fetch` 不可用、`gh` 的 contents API 是 403（实测），所以我无法把它取下来，也无法读它的内容。

能修，是因为**修法不需要看到它**：规则要求把自己的迁移重挂到 origin/main 的当前链尾，而 CI 的报错行已经把链尾名字给出来了。它自己的父节点是什么无关紧要——只要它是 origin/main 唯一的头，挂在它上面就必然收成一个头。

**代价要写清楚，别让下一个人以为是回归：**

```
uv run alembic heads   →  KeyError: 'c8b1f4a70d29'
```

`tests/conftest.py` 的测试库 schema 是 `alembic upgrade head` 建的，所以**在平台把 main 同步过来之前，本地所有 DB 相关测试都跑不了**。这不是坏掉，是链尾指向了一个只存在于 origin/main 的节点。CI 那边跑的是 PR 的 merge ref，两个文件都在，所以链是连续的。

不碰库的测试不受影响：本卡的 `test_device_health.py` + `test_host_swap.py` + `test_platform_failures.py` 共 **21 passed**。

**没做也不该做的事**：本地伪造一个 `c8b1f4a70d29` 占位文件让 alembic 闭环。那会把一条假迁移落进链里，比 CI 红严重得多。

---

## 7. PR #301 第二轮 CI 红：两个失败，其中一个是本卡的真 bug

两个失败的测试**在当时的工作区里都不存在**——`live_turn_for_topic` 这个方法本身也没有。它们随 `采纳 领域包解环与 import 守卫 (#299)` 一起进的 main，在我的基线之后。本地全量跑绿过，因此不是漏跑，是**基线里没有这两个测试**。

拿到它们的办法不是猜：`jj rebase` 走不通（靠前的提交已 immutable，属于共享历史），但 **CI 测的本来就是 PR 的 merge ref**，所以在本地把 main 合进来看到的就是 CI 看到的那棵树（`jj new @ main`，新增 25 个文件、改 50 个）。合完两个失败都能本地复现。

### 7.1 跨领域 import（架构违规，我引入的）

```
app.domain.agent.host_swap → app.domain.device.sql_repository
```

`host_swap` 自己 `new` 了 device 域的 repository。`device_provider.py`、`machine/services.py` 也这么干，但它们在守卫的 `_EXEMPT` 里被祖父条款放过了——新代码不该再加一条豁免。

修法：在 device 域里加 `device_service_for_session(session)` 工厂，repository 的 import 留在自己域内，跨域的只依赖 **service**。

### 7.2 轮次收尾去等数据库（真 bug，生产同样成立）

失败的断言：

```python
finish.set()
for _ in range(50):
    await asyncio.sleep(0)          # 只给 50 个事件循环 tick
    if runner.live_turn_for_topic(topic) is None:
        break
assert runner.live_turn_for_topic(topic) is None
```

`_live` 是在轮次自己的 `finally` 里清的，而我加的 `await record_host_success(...)` 挡在它前面，会开一个真数据库会话。**CI 的 test job 里是有 Postgres 的**，所以这不是"连不上慢慢超时"，是它真的连上、真的跑了两条查询——一次真实 DB 往返远超 50 个 tick。

**这不只是测试怪癖。** `live_turn_for_topic` 是 stall 判定的心跳半边（`TopicService.stall_signal`）。让每一轮的收尾都等一次 DB 往返，等于让一个已经跑完的轮次在这段时间里被读成"还活着"。而这条记账本身在 99.99% 的轮次里是空操作——健康表只在发生过 host-scoped 失败后才有行。

修法两步：

1. `TurnRunner` 记一个进程内的 `_host_failed_topics`，只有**这个进程真的见过这台机器失败**时，成功轮次才去清零。正常轮次完全不碰库。
2. 进程重启会丢掉那个集合，于是"成功清零"可能不发生——用 `DEFAULT_STREAK_WINDOW`（30 分钟）兜底：**陈旧的一击不再算进 streak**。这条规则本身也是对的，一小时前的一次失败和现在这次不该叫"连续"。

因果是验证过的、不是推断：把那行临时改回无条件 `await`，本地精确复现了 CI 的同一个失败；改回来即绿。

**教训**：本地全量绿只证明"在我的基线上绿"。当 main 新增的测试针对的正是我改动的那个函数时，基线差异就是盲区——`jj new @ main` 合出 merge ref 是把这个盲区补上的最短路径。
