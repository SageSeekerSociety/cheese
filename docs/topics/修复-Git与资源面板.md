> 状态：核账完成，开始改代码。范围只限 Git 面板（P1-7）与资源面板（P1-8）。

## 目标

两个面板都在一本正经地显示错的数字/内容——比空着更坏，人会照着它做判断。把它们改到「说的每个数都对，说不准的就明说不准」。

## 已核完的账（读代码 + 对照写入路径，未改业务代码）

### Git 面板（P1-7）

| 现象 | 根因（已定位） |
|---|---|
| 显示的是主干提交，不是本话题的 | <&frontend/src/components/DocPanel.vue> 第 639-640 行 `getGitLog(pid)` / `getGitDiff(pid)` 都没传 `topic`；<&frontend/src/api.ts> 第 788-794 行的函数签名里压根没有 topic 参数 |
| 后端「已支持 topic」只支持了一半 | <&backend/app/api/routes/workspace.py> 第 123-153 行：`git/diff` 本地模式确实按 topic 走 `ws.topic_diff`，但 `git/log` 本地模式无条件调 `ws.git_log(project_id)`（<&backend/app/domain/workspace/service.py> 第 432 行），**函数根本没有 topic 参数**。所以只补前端不够，后端 log 也要改 |
| 「工作区干净，无未提交改动」是假话 | `git_diff` 无 ref 时走的是 `git show HEAD`（service.py 第 447-458 行），即主干最后一次提交。传 topic 之后拿到的是 `base...branch` 的全量分支 diff——是「采纳会合并的改动」，不是「未提交改动」。**标题和空态文案必须跟着改**，否则换个假话继续骗人 |

### 资源面板（P1-8）

| 现象 | 根因（已定位） |
|---|---|
| 3 轮显示成 43 | <&backend/app/domain/usage/repositories.py> `_agg` 用 `func.count()` 数**表行数**当轮次。而一轮会写多行：订阅代理每个 `/v1/messages` 响应写一行（<&backend/app/domain/usage/subscription_ingest.py>），gateway 延迟补账（<&backend/app/domain/agent/chat.py> `_schedule_deferred_drain`）给同一轮再写一行 |
| 228 万 token 显示 `$0.0000` | `subscription_ingest._land_row` 写死 `cost_usd=0.0`——订阅是包月，本来就没有逐 token 单价。**这是「未知冒充零」**，聚合层现在无法区分「真的没花钱」和「花了但不知道多少」 |
| sdk 路径 token 低估一个数量级 | <&backend/app/domain/agent/service.py> 第 436 行只读 `input_tokens`/`output_tokens`，丢掉 `cache_read_input_tokens` / `cache_creation_input_tokens`。另两条路径（hooks 的 `usage_from_hook`、订阅的 `_land_row`）都折算了——**同一件事有三份各写各的算术**，这才是它漏掉的原因 |

## 改法（决定）

1. **轮次按轮聚合**：`resource_usage` 加 `turn_id` 列（可空，建索引）+ alembic 迁移（down_revision `c4e8f19b0d73`）。chat.py 的 4 处写入都带上本轮 `turn_id`；订阅代理那条日志没有轮号，用它自带的 `ts` 归属到该话题当时正在跑的那一轮。聚合改成 `count(distinct turn_id) + 无 turn_id 的行数`（历史行保持旧口径，不假装知道）。
2. **费用说不准就说不准**：订阅路由继续不编价，但聚合额外返回 `unpriced_tokens`（有 token 却无单价的量）。前端据此显示「未知」或「$X（另有 N token 未计价）」，**绝不显示 0**。
3. **cache token 折算收成一处**：新增共享折算函数，hooks / 订阅 / sdk 三条路径都调它，sdk 路径顺带补上漏掉的两个 cache 桶。测试钉住三条路径同输入同结果。
4. **Git 面板取话题级**：`ws.git_log` 加 `topic_id`（取 `base..branch`，即本话题自己的提交），路由透传，前端两个请求都带 topic；diff 区标题改成「本话题改动（相对主干）」，空态改成「本话题还没有自己的提交/改动」。
5. **面板不再陈旧**：抽屉头加刷新按钮；Git/资源面板在打开期间轮询刷新，并在一轮跑完时立刻重取（静默刷新，不闪 loading）。
6. **数字可读**：大数字加千分位；`fmtCost` 按量级选精度。

## 纪律

- 只碰这两个面板，不顺手重构 <&frontend/src/components/DocPanel.vue> 其他区域（有并发子话题在改同一文件）。
- 测试钉死三条：轮次等于真实轮数、订阅路由下费用不显示为 0、三条后端路径 cache 折算一致。
- 改完跑 `task check`；沙箱无 docker 时按 CLAUDE.md 的 dev-db.sh recipe 跑 pytest。走 PR，不直接提交 main。

## 下一步

按 1→6 顺序改，先后端（有迁移和测试），再前端。检查跑绿后把验收卡递给 <@wangchangxin>。
