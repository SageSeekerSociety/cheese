> 状态：待验收（2026-08-12）。基线 = `main@upstream` fe949e17（#284），已 rebase，无落后。
> alembic 单 head = 本主题新增的 `c1d7e0a4b839`（已核，用 ast 遍历 65 个迁移确认；
> 注意 `alembic heads` 命令在沙箱里会挂住等 DB，别用它）。
>
> rebase 记录：开工基线 2e72632e → 45b6169a(#279) → fe949e17(#284)。最后一次 rebase 在
> `services.py` 的「note 前缀家族」处撞了一个两边纯加法的冲突（main 加
> `_DEPLOY_STALLED_PREFIX` / `_MAX_SUPERSEDE_COMPARES`，本主题加 `GATE_ABANDONED_PREFIX`
> / `VOIDED_PREFIX`），两边都保留即可。

## 目标

一张 `pending_gate` 的验收卡在闸门任务丢失后没有任何出口，连带 `create_card` 的互斥让**整个话题**再也递不出卡。做三件事解开它：

1. **扫底判死**：启动时 + 周期性地把超时仍在 `pending_gate` 的卡判死，措辞是「闸门没跑完」，与「检查未通过」在数据上可区分。
2. **`gate_started_at`**：卡上记闸门实际开跑的时刻（alembic 迁移），让「从没跑起来」和「跑一半死了」能被区分。
3. **人工作废出口**：验收人 / owner / lead 可把任何未决卡置终态，解开互斥、允许重递。

## 关键判断（会随实现更新）

- **周期性兜底：做。** 启动扫底盖不住「进程活着但任务死了」，而这条路径在代码里是实存的：`_settle` 重试 100×0.1s 拿不到卡就 `logger.error("gate result dropped")` 直接放弃；`_run` 只 `except Exception`，`BaseException`（含任务被取消）会静默逃逸；后端连跑数周不重启时启动扫底一次都不触发。
- **`gate_started_at` 写在闸门 runner 真正开跑那一刻**，不是建卡那一刻——写在建卡等于 `created_at`，白记。这样 `pending_gate + gate_started_at IS NULL` = 任务压根没起来（dispatch 丢了），`pending_gate + gate_started_at 很旧` = 跑到一半进程死了。扫底的计时钟用 `coalesce(gate_started_at, created_at)`。
- **作废复用 `revoked` 终态，不新增状态。** `archive.py` 已经把同一批非终态卡收敛成 `revoked`，前端也已渲染；新增 `voided` 只是让状态机和前端都多一格。区分靠具名 note 前缀（本仓库既有约定，见 `services.py` 的「note 前缀家族」注释）。
- **作废不给芝士**：不进 `_CHEESE_WRITE_PATHS`，并且**另外**在服务层用 `_forbid_ai` 挡一道——本仓库已实证「没列进白名单的写路由压根不过中间件，症状是静默放行而不是 401」，所以不加白名单 ≠ 拦住了。

## 硬边界（来自拆分简报）

- 不碰闸门跑什么 / 怎么判绿（`@门禁SKIP不该判绿` 在做）。
- 不动 `accept` / `revoke` 对 `accepted` 卡的既有语义。
- 不做「强行放行到 `pending`」——那等于让绿勾替没被检查过的代码背书。

## 下一步

- [x] 迁移 + 模型：`accept_cards.gate_started_at`
- [x] `gate.py`：开跑打点；抽掉 `_settle` 里那段重试的重复（提成 `_once_visible`）
- [x] `gate_sweep.py`：扫底判死（启动 + 周期），note/gate_output 里把「没跑完」和「没通过」分开
- [x] `void`：service + 路由 + 授权
- [x] 测试：`tests/integration/test_accept_gate_orphan.py`，12 条，含端到端（孤儿被判死 → 同话题重递成功）
- [x] 检查全绿 → 递卡给 <@wangchangxin>

## 验证记录（2026-08-12，rebase 到 fe949e17 之后）

- `check.sh --no-tests`：6/6 —— ruff、pyright 0 error、alembic 单 head、repo guards、action pin。
- 全量 pytest：`4044 passed, 31 skipped, 23 failed`。23 条**全部**是沙箱的宿主环境缺口，
  与本主题无关，逐条核过：
  - 22 条 = 无 procps（`test_machine_service.py` 21 + `test_tmux_control.py` 1），
    报 `FileNotFoundError: [Errno 2] ... 'kill'`，CLAUDE.md 已记在案。
  - 1 条 = `test_cheese_cli.py::test_await_log_lives_outside_the_worktree`。**这条 CLAUDE.md
    还没记**：`_await_log_path` 优先读 `CHEESE_AWAIT_LOGS`，而 agent 容器里设了它
    （`/home/node/.claude/cheese-await`），于是该测试只 `setenv("HOME")` 就被架空。
    同文件里两条更新的同类测试都显式 `monkeypatch.delenv("CHEESE_AWAIT_LOGS", raising=False)`
    才躲过。属于 main 上的测试环境依赖，不是本主题的改动引起的，也没在这里顺手改
    （不同主题的文件）。
- 前端未跑：这个沙箱没有 `pnpm`。本主题 0 个前端文件，故不影响结论。
