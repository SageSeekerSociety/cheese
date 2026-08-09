## 目标

把"采纳"从单次"合并+归档"改成两阶段：人点一次采纳 → 有可用已连接 GitHub token 则推分支开真实 PR、卡片转 `pr_open`、话题不归档 → 系统轮询 PR 的真实 CI（GitHub Actions）检查状态 → 全绿则自动调 API 合并、这时才真正归档；检查红了唤醒芝士在 PR 分支上继续修。没有可用 token / GitHub API 故障 → 直接走现在的 `merge_topic()+push_back()` 老路径正常归档，不阻塞采纳（这是降级，不是错误）。

## 约束（已拍板，不重新讨论）

1. 不等 #195（PR-based accept，处于停滞状态，全部 CI 失败几小时未推进）合并，参考它公开的设计思路自己实现一遍等价机制。
2. 降级路径（无 token/API 故障 → 走老的 `merge_topic()+push_back()`）本次要留着，不用管以后何时删除。
3. 不做二次人工确认，CI 真正转绿后系统自动合并 PR 才算完成；话题归档卡在"真的 merge 成功"，不能卡在"点了采纳"。点了采纳到 PR 真正合并之间，话题保持 active，容器不停，芝士能在里面继续改代码。
4. 内部闸门（`review/gate.py`，跑 ruff/pyright，不跑重的 pytest/e2e）保持不变，继续在人点采纳按钮之前起作用；开 PR 之后跑真实的重 CI（Backend Test/E2E Tests）。两层不互相替代。
5. 本次明确不做：CI 一直不转绿的超时升级；降级路径未来何时删除。不自行加范围。

## 依赖

- 前置修复（并行进行中，id=12147a29-a681-4b6d-8dea-4f0dc669e49b）：账号连接的 OAuth token 持久化，未完工不阻塞开工——没有可用 token 时走降级路径就是正常情况。

## 待确认的代码现状（研究子任务进行中）

- `AcceptStatus`/`AcceptService.accept()` 当前实现细节、`gate.py` 里 `runner.submit(..., summon=True)` 的确切调用方式
- #195/`ACCEPT_VIA_PR`/`write_token()`/`pr_number` 是否已合并进本仓库（简报说未合并，需要在当前 checkout 里核实一遍，不能只信简报）
- `UserOAuthConnection` 是否真的没有持久化 access_token 字段
- `SchedulerService` 现有轮询任务（如 `reap_idle_containers`）的写法，供新增 PR 检查轮询任务参考
- `git_http.py` 的 committer-only 写法（供 PR commit/PR 描述里区分"芝士代表谁"参考）
- `docs/topics/两阶段采纳-PR迭代式设计.md` 设计文档本仓库里是否存在

## 下一步

1. 只读代码现状核查完成后，落地改动：
   - `review/models.py`：`AcceptStatus` 加 `pr_open`
   - `review/services.py`：`AcceptService.accept()` 分支——有 token 开 PR 转 `pr_open` 不归档；无 token/故障走老路径正常归档
   - 新增轮询+自动合并机制（`SchedulerService` 或类似位置）：查 `pr_open` 卡片对应 PR 的 check-runs，全绿则 API 合并→转 `accepted`→归档；红了复用 `gate.py` 的 summon 模式唤醒芝士修
   - PR commit/描述里标清"芝士代表谁"（人的身份 vs 芝士本身），参考 trailer 方案
2. 测试覆盖两条路径（有 token 用 fake GitHub client 模拟；无 token 验证行为与现在完全一致），归档时机测试尤其重要
3. `ruff`/`pyright`/`pytest` 全绿（沙箱用用户态 pgserver 起真实 Postgres，参考卡1做法）
4. 递验收卡给 <@wangchangxin>，routing_reason 里说明这是核心状态机改动，不要当成普通小改动
5. 验收卡里提一句：本卡的轮询+唤醒机制大概率覆盖了卡2"PR 事件唤醒"原本要做的事，卡2要不要撤掉交给 <@wangchangxin> 判断

## 传话方式

拿不准的事实性问题：`cheese api POST /topics/e593d59c-ca17-4dcc-b0ff-57980823fd22/comments --data '{"author": "...", "content": "..."}'` 问回父话题。
