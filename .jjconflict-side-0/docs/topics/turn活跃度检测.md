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
- `backend/app/domain/agent/device_provider.py`：seam 用默认空实现，
  `idle_suspect_s == hard_ceiling_s == device_turn_timeout_s`，等价于旧的单层
  固定超时，行为不变；留 TODO 注释标注远程活跃度检测是后续单独一张卡。
- **原简报没提到、自行判断补上的部分**：`runtime.py` 的 `TurnRunner` 还有一层
  transport 无关的外层 `asyncio.timeout` 硬墙（同一个 `agent_turn_timeout_s`，
  对 SDK/tmux/device 三个后端一视同仁）——只改 `run_hooks_turn` 内层不够，这道
  墙不动 tmux turn 照样在 900s 被无差别强杀。做法：`chat_service.converse()`
  选定 tmux provider 后发一个 `turn_ceiling` 元帧（不落广播，`_execute` 内部
  消费），`TurnRunner` 用 `asyncio.Timeout.reschedule()` 只把这一个 turn 的外层
  墙放宽到 10800s；SDK/device 完全没有这个信号，外层墙原样不变。
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
