## 状态：**已实现，全量套件跑完**，基线是本地 main `5fcf3f33`（其上游锚点 `main@upstream` = `0e5b5fa1`）

对应 [#186](https://github.com/SageSeekerSociety/cheese/issues/186) 第一节「身体能不能用」。第二节（agent 待在哪）维持现状 A，不在本卡范围；第三节归 #187。

§3 那张落点表现在是**已改**，不是待办。闸门实测：

| 项 | 结果 |
|---|---|
| ruff check + format（全 backend，含 `alembic/`） | All checks passed / 758 files formatted |
| pyright `app/` | 0 errors |
| alembic heads | 单头 `b7e3c19d4f80` |
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
| `alembic/versions/b7e3c19d4f80_*.py` | `device_health` 迁移，挂在 `c1d7e0a4b839` 之后（单头；main 期间顶掉过两次，重挂了两次） |
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
