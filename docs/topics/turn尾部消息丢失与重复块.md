## 状态：三处修复 + 功能测试已完成，已递验收卡（pytest 因沙箱缺 Postgres 未能本地跑绿，需要有 DB 的环境/CI 复核）

## 根因回顾

链路：`cheese-hook` 转发 → `POST /sandbox/hooks/{topic_id}` → `HookRouter.push` →
turn 内的 `run_hooks_turn` 消费，或（找不到监听队列时）落 server 端 spool，等下一轮
`_reconcile_spool` 补写。三处缺陷都在这条链路上，且互相关联（同源）。

## 已完成的三处修复

1. **补写要广播，不能只落库**（`backend/app/domain/agent/chat.py`）
   `_reconcile_spool` 从纯 `async def -> None` 改成 `AsyncIterator[dict]`：为每条
   backfill 的消息/现场事件 `yield {"type": "assistant_block"/"event_block", ...}`，
   调用处（`_converse_impl`）用 `async for frame in self._reconcile_spool(...): yield frame`
   转发。`_persist_tool_event` 相应改为返回持久化后的 block payload。broadcast 走的是
   `ChatService.converse` 现有的 frame → `TurnRunner` → `Broker.publish` → WS 通道，
   和实时路径完全同一条链路，不是新开的旁路。

2. **迟到的 Stop 不能提前结束新 turn**（`backend/app/domain/agent/hook_events.py` +
   `backend/app/domain/agent/hooks_substrate.py`）
   根因不是"注册覆盖"本身（这已经被 per-topic 锁很好地序列化了），而是：
   `register()` 必须在 `_ensure_ready`（等 screen/CLI 就绪，可能要等上一轮那个被判超时
   但仍在跑的 `claude` 进程真正收尾）**之前**发生（不能漏收早到的 hook）——这就给了
   上一轮的尾巴事件（包括它自己迟到的 Stop）一个窗口，可以在新 prompt 还没发出去之前
   就落进新注册的队列。`send_prompt` 之前，队列里任何东西都不可能属于"即将开始"的这个
   turn。新增 `HookRouter.drain(topic_id)`，在 `_ensure_ready` 成功、`_send_prompt`
   之前把队列排空，排空到的内容按"这个 turn 拒绝信任"处理——落到同一个 spool（复用
   `/sandbox/hooks` 端点已有的 park 路径），不会被丢，也不会被误判成新 turn 的结果。

3. **兜底路径缺 eid 导致同段文字被判两次**（`backend/app/domain/agent/chat.py`）
   `assistant_count == 0` 的兜底分支容易撞上和修复 1/2 同源的场景：这段回复文本对应的
   `MessageDisplay` hook 因为跟 Stop 抢发生了竞态，恰好在这个 turn 运行期间才落进 spool
   （容器端是"先落盘再 curl"，本来就比 Stop 早发生）。修法：在兜底判断之前，**再 sweep
   一次 spool**（复用 `_reconcile_spool`，逻辑不重复写），把这次 sweep backfill 出来的
   文本记下来；兜底只在这次 sweep 没有覆盖到的情况下才补发（这样兜底真正无 hook 支撑的
   场景——例如非 hooks 的 SDK 后端——行为不变，仍然落库但不带 eid）。副作用是好的：这也
   把"迟到 hook 要等到下一轮才补写"的窗口，从"一整轮"收窄到"这一轮末尾"。

## 功能测试（backend/tests/，测行为不读实现）

- `backend/tests/integration/test_spool_reconcile.py`
  - `test_backfilled_events_are_broadcast_not_just_persisted`：断言 backfill 出的
    tool 事件 / 消息都能在 `converse()` 产出的 frame 流里找到对应的
    `event_block`/`assistant_block`，不是只进了 DB。
  - `test_fallback_reply_does_not_duplicate_a_late_spooled_message`：构造"回复文本
    恰好是迟到 MessageDisplay 内容"的场景，断言最终只有 1 条消息（不重复），且它带
    `eid` + `backfilled: true`（走的是 reconcile 路径，不是无 eid 兜底）。
- `backend/tests/unit/test_hooks_substrate.py`
  - `test_stale_stop_from_abandoned_turn_never_ends_the_new_turn`：用假
    `HooksTurnProvider` 子类模拟"`_ensure_ready` 等待期间上一轮的迟到 Stop 落进新
    队列"，断言新 turn 最终按自己真实的 Stop 结束（不是被旧 Stop 提前打断），且旧
    Stop 被 park 进 spool（没有静默丢失）。

## 验证现状（需要 @n1ctheboy 或知道本环境限制的人确认）

- `ruff check .` / `ruff format --check .`：全仓库通过。
- `pyright`（项目配置只覆盖 `app/`）：0 errors。
- `pytest`：**本沙箱没有 Postgres/Docker**（`docker`/`podman` 未安装，`127.0.0.1:5433`
  连不上，也没有权限装 Postgres server 包），`tests/conftest.py` 的 `_pg_schema` 是
  session 级 autouse，任何测试（哪怕纯单元测试）跑之前都会先建库，所以本地一条测试
  都跑不起来——包括 check-runner 分身也确认了同样的环境限制。三个新测试和三处修复都
  经过了逐行手工走查（追出了具体的执行路径和断言是否成立），但没有拿到一次真实的绿色
  pytest 运行结果。

确认：这是沙箱网络层面的真实限制（能看到 host.docker.internal:5433 开着端口，但那是
别的服务，认证不通，不是可用的测试库），不是代码问题，不再往这个方向排查。按约定处理：
验收卡里如实写清楚"测试代码已写好、ruff/pyright 过了，pytest 因沙箱缺 Postgres 没能
本地跑绿，需要有 DB 的环境/CI 复核"，正常递验收卡，不等连上库。
