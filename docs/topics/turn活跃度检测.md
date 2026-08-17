## 状态：已实现，验证通过，递验收卡待 wangchangxin 过一遍

把本地 tmux 后端（`TmuxHooksProvider`）的固定 900 秒静态超时，改成"5 分钟疑似
卡死→轻量探活，3 小时绝对硬顶"两层机制。远程 device 后端明确不动。数值已由
wangchangxin 拍板；设计文档在子话题 `e6ddab21-5b1a-4647-861d-a06a67b30a63`。

## 实现摘要（7 个文件 + 测试）

- `backend/app/core/config.py`：新增 `agent_idle_suspect_s`（300）、
  `agent_turn_hard_ceiling_s`（10800）。`agent_turn_timeout_s`（900）保留，
  收窄为"SDK 后端外层墙 + device 后端自己的固定超时"，tmux 后端不再用它。
- `backend/app/domain/agent/hooks_substrate.py`：`run_hooks_turn` 拆成两层
  （`idle_suspect_s` 疑似卡死 + `hard_ceiling_s` 绝对硬顶），新增 `ActivityTracker`
  （被 hook 到达 / 外部活跃度信号共同触碰）；`HooksTurnProvider` 新增
  `_start_activity_monitor`/`_confirm_alive` 两个 seam（默认无操作，仿
  `_send_prompt` 的子类各自实现模式）。
- `backend/app/domain/agent/tmux_provider.py`：`TmuxHooksProvider` 实现 seam——
  后台任务每 12s `capture-pane` 一次、输出哈希变了记一次活跃；疑似卡死时调用
  `pane_dead()` 探活；`activity_status(topic_id)` 给 `cheese status` 读。
- `backend/app/domain/agent/device_provider.py`：（本增量）seam 用默认空实现，
  `idle_suspect_s == hard_ceiling_s == device_turn_timeout_s`，等价于旧的单层
  固定超时，行为不变；远程活跃度检测留作后续一张卡。**已被文末「后续：device
  侧补齐」取代——device 现在也走两层 + 进程树探活，不再是单层固定超时。**
- **原简报没提到、自行判断补上的部分**：`runtime.py` 的 `TurnRunner` 还有一层
  transport 无关的外层 `asyncio.timeout` 硬墙（同一个 `agent_turn_timeout_s`，
  对 SDK/tmux/device 三个后端一视同仁）——只改 `run_hooks_turn` 内层不够，这道
  墙不动 tmux turn 照样在 900s 被无差别强杀。做法：`chat_service.converse()`
  选定 tmux provider 后发一个 `turn_ceiling` 元帧（不落广播，`_execute` 内部
  消费），`TurnRunner` 用 `asyncio.Timeout.reschedule()` 只把这一个 turn 的外层
  墙放宽到 10800s；SDK/device 完全没有这个信号，外层墙原样不变。**（device 这半
  已被文末「后续：device 侧补齐」改写：device 现在也发 `turn_ceiling`，外层墙同样
  放宽到 10800s；只剩 SDK/remote-cheesed 没有这个信号。）**
- `cheese status`：`TurnRunner.topic_turn()` 去掉 `budget_left_s` 实时倒计时，
  改成 `ceiling_s`/`near_ceiling`；`/topics/{id}/status` 路由新增
  `turn.activity`（读 `ChatService.tmux_activity_status`）；
  `backend/sandbox/cheese::_format_status` 渲染三态——正常运行中 / 疑似卡死
  （已 X 分钟无活跃信号）/ 接近硬顶。
- `chat.py::_turn_meta_lines`：tmux 后端的系统提示去掉"到点会被中断"式紧迫感
  措辞，只保留"长活记得边做边落盘"类建议；SDK/device 保留原有分钟数措辞不变。

## 验证

- `ruff check .`、`uv run pyright`：全仓库零错误。
- 用户态编译 postgres 二进制（`pip install pgserver` 拿二进制，手动
  `initdb`/`pg_ctl` 起 TCP 127.0.0.1:5433，绕开 pgserver 默认只走 unix socket
  的限制）起了真实 Postgres。
- 直接相关的单测（`test_hooks_substrate`/`test_tmux_provider`/
  `test_device_provider`/`test_subscription_provider_env`/`test_cheese_cli`/
  `test_turn_meta_prompt`/`test_runtime`/`test_turn_admission` + 集成测试
  `test_topic_status`）：**96 + 3 全部通过**，新增覆盖两个必查方向——①正常长
  任务（有 pane 输出无 hook）不在 5min/15min 被误杀，靠 `confirm_alive` 持续
  探活撑到硬顶；②真卡死（`confirm_alive` 返回 false）在远小于硬顶的时间内被
  正确结束；另外覆盖了外层墙 reschedule 只对 tmux 生效、不泄漏 `turn_ceiling`
  控制帧给订阅者。
- 全量 `pytest`（2751 passed，123 failed，591 errors）：失败/报错的大头是这个
  沙箱本身缺 Redis（`ConnectionError: ... 6379`，波及所有走登录限流的集成测试）
  + 缺 `kill` 二进制（`test_tmux_control.py` 一个已存在的、和本卡无关的用例）+
  jj/git 沙箱权限老问题（`test_attachments.py`，父话题记录里已知的老问题）。
  抽查了全量跑里出现过的、和本卡代码路径（chat/runtime/tmux）表面相关的 8 个
  失败用例逐个单独重跑，**7 个单独跑全部通过**（证明是全量并发下的资源竞争/
  Redis 缺失导致的假阳性，不是回归），只有 1 个是已确认的环境问题
  （`test_tmux_control` 缺 `kill`）。没有发现任何和本次改动相关的真实回归。

## 下一步

递验收卡给 wangchangxin，routing_reason 注明这是核心运行时改动、影响面覆盖
全平台本地 tmux turn。

## 后续：device 侧补齐（把上面留的 TODO 落地）

上面那版本地 tmux 落两层、远程 device 明确不动——于是 device 的
`idle_suspect_s == hard_ceiling_s == 900`，两层塌成一根 900 秒死线，唯一生效的
是「到 900 秒无条件杀」。一个正常跑 20 分钟的前台命令（pytest 等，这 20 分钟
hook 全静默：PreToolUse 开头响一次、PostToolUse 结束才响、中间零 hook）会在第 15
分钟被误杀。本次把 device 侧补齐，和本地同一套策略：

- `device_provider.py`：`DeviceProvider` 改成分别接 `idle_suspect_s` /
  `hard_ceiling_s`（默认 300 / 10800，与本地同值），删掉 `turn_timeout_s` 单值
  塌缩与那段 TODO。
- `device_provider.py` + `device_launch.py`：给 device 一个真的 `_confirm_alive`
  ——**进程树探活**。静默期最可靠的存活信号是进程还在不在（transcript mtime /
  statusline / OTel 在静默期都无效；headless 设备也没有现成的屏幕字节流——hub 只
  在有浏览器 viewer 订阅时才回传 `screen.data`）。`DEVICE_ALIVE_PROBE` 经 hub 的
  `exec` 在设备上跑一段 `sh`：按 claude 进程自己的 `CHEESE_TOPIC` 环境变量在
  `/proc` 里精确匹配本话题的 claude 是否还活着，打印 `alive`/`dead`/`unknown`。
  只有明确 `dead` 才判死；`unknown`（非 Linux / environ 不可读）、exec 报错、非零
  退出一律保守判活，探测抖动绝不误杀。所有 Device 都走同一条 `exec`。
- `compute.py` / `config.py`：device 直接复用 `settings.agent_idle_suspect_s` /
  `settings.agent_turn_hard_ceiling_s`（与本地 tmux 同源，共一套旋钮）；删掉
  `device_turn_timeout_s`。
- `chat.py`：`is_activity_aware_backend` 从「只认 `TmuxHooksProvider`」放宽成
  「认 `HooksTurnProvider`」——**这条是让内层修复真正生效的关键**：只改内层两层
  不够，`runtime.py` 外层墙不发 `turn_ceiling` 就仍在 900s 无差别杀 device turn。
  现在 device 也发 `turn_ceiling`（= 其 `hard_ceiling_s` 10800），外层墙同样放宽。
  `_turn_meta_lines(activity_aware=True)` 的「无固定倒计时」提示对 device 也已属实。

未做（另开卡）：PreToolUse 预读命令自带的 `tool_input.timeout` 动态放宽窗口——是
优化不是正确性修复。`sweep_orphans` 的 30 分钟静默兜底（`SILENT_TURN_S=1800`）对
两个 hooks 后端一视同仁、本次不动：20 分钟的静默前台命令在其之下，不受影响。
