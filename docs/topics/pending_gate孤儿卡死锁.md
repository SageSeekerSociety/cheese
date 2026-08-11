> 状态：开工中（2026-08-11）。基线 = `main@upstream` 2e72632e，无落后。alembic 单 head = `b8e1d4c70a92`（已核，用 ast 遍历 64 个迁移确认；注意 `alembic heads` 命令在沙箱里会挂住等 DB，别用它）。

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

- [ ] 迁移 + 模型：`accept_cards.gate_started_at`
- [ ] `gate.py`：开跑打点；抽掉 `_settle` 里那段重试的重复
- [ ] `gate_sweep.py`：扫底判死（启动 + 周期），note/gate_output 里把「没跑完」和「没通过」分开
- [ ] `void`：service + 路由 + 授权
- [ ] 测试：三件各自的功能测试 + 一条端到端（孤儿被判死 → 同话题重递成功）
- [ ] `task check` 全绿 → 递卡给 <@wangchangxin>
