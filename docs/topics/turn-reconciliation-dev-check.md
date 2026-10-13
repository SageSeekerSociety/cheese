## 目标

dev 上两个后端容器重叠运行时，浏览器连到非属主后端，房间在该轮结束后会一直显示「正在思考」。改动（#3129）让每个聊天 socket 连上时按数据库收养轮次、之后每 15 秒重读一次，判定只以数据库为准。这份记录是它在 dev 实机重叠窗口里的观测结果。

## 观测方法

一台只读观测器从公网同时挂 8 个房间的聊天 socket，逐帧记录类型与轮次 id；另每 3 秒拉一次 `/api/topics/{id}/blocks?limit=6`（块带 `turn_id` 和 `created_at`，是数据库侧的时刻）、每 4 秒拉一次 `/api/topics/{id}/status`、每 3 秒拉一次 `/api/version`。版本号一变就算一次部署切换。

## 10:50 那次切换

- **切换**：`/api/version` 从 `aff36c3` 变成 `d26c479`，轮询在 10:50:44 看到。旧容器上的房间 socket 在 10:50:47.03–47.04 收到关闭码 1012（服务重启），10:50:48 全部重连。有一个房间在 10:50:42 刚重连过，它的 socket 落在新容器上、没被关——路由切换在 10:50:41–42，旧容器约 5 秒后才清 socket，与 `HANDOVER_AFTER_SECONDS=5` 对得上。
- **收养**：重连后的第一帧就是 `turn_active` + `room_state`，带着切换时仍在跑的那一轮 `35c83b4d`（10:22:49 开始）。这与数据库一致：那一刻它确实在跑。
- **放下**：该轮在 10:51:42.36 真正结束（最后一条 event 块），10:51:43 重连后的 socket 收到 `done` + `turn_finished`，房间回到空闲（10:52:28 重连时 `room_state` 的 turns 为空）。没有卡在「正在思考」。

## 这次没有撞上的那条路径

缺陷的触发条件是「一轮在旧容器上结束，而房间 socket 已经搬到新容器」：那样它发的 `turn_finished` 没人听，得等新容器 15 秒后重读数据库才放下。这次窗口里没有轮次在这段时间结束——切换时全项目只有本任务房间在跑轮次，而它到切换后 55 秒才结束，早过了交接窗（切换 +5 秒清 socket，旧容器再最多停 20 秒，窗口约 [切换+5s, 切换+25s]）。所以「卡住 → 15 秒后放下」这个闭环这次没有自然发生。

## 那条路径由测试保证

`backend/tests/integration/test_a_turn_the_process_never_saw_start_still_runs.py` 里的 `test_a_turn_that_ends_on_the_other_container_stops_showing_as_running` 就是它：页面连着，轮次在「另一容器」结束，房间必须停止显示它在跑。本地带真数据库跑过 26 个交接与对账用例（`test_backend_handover_signal`、`test_messages_across_a_handover`、`test_teammates_across_a_handover`、`test_a_turn_end_read_too_late_still_closes`、`test_a_turn_the_process_never_saw_start_still_runs`、`test_turns_left_open_by_taken_messages`）全过。

## 沙箱里怎么把这两个服务跑起来

`.claude/scripts/dev-db.sh` 在这台机器上起不来：它要用 `make` + `cc` 现编 Valkey，沙箱没有编译器。可用的做法是：用脚本自己装好的 PG 17.11 二进制手工 `initdb` + `pg_ctl`（私有端口 5599，`postgresql.conf` 里加 `shared_preload_libraries = 'pg_search'`，否则迁移里那句 `CREATE EXTENSION pg_search` 直接报错），Redis 用 Debian 的 `valkey-server` / `valkey-tools` 包（8.1.1）解出来的二进制（私有端口 6399，`LD_LIBRARY_PATH` 要补 `libatomic1` 和 `liblzf1`），再用 `TEST_PG_BASE` / `REDIS_URL` 指过去。**缺 Redis 时 14 个交接用例会以「消息一直没被回答」的样子失败**，那不是代码问题。

## 还没做到的

没有在一次真实切换里观测到「卡住 15 秒」的完整闭环，这一步目前只有测试覆盖。要稳定复现，得让一轮恰好落在切换后 5–25 秒内结束。
