> 状态：改完、测完，可以验收。范围只限 Git 面板（P1-7）与资源面板（P1-8）。唯一没跑成的是前端 `vue-tsc` 全量类型检查——沙箱 cgroup 只有 2GB，1GB 堆也 OOM，`vite build` 同样被 137 杀掉，**需要 CI 或本地补跑**（已用 SFC 编译校验兜底，见下）。

## 目标

两个面板都在一本正经地显示错的数字/内容——比空着更坏，人会照着它做判断。改到「说的每个数都对，说不准的就明说不准」。

## 核账结论（读代码 + 对照写入路径）

### Git 面板（P1-7）

| 现象 | 根因 |
|---|---|
| 显示的是主干提交，不是本话题的 | <&frontend/src/components/DocPanel.vue> `loadTool` 的 git 分支没传 `topic`；<&frontend/src/api.ts> 的 `getGitLog`/`getGitDiff` 签名里压根没有 topic 参数 |
| **简报说「后端已支持 topic」只对了一半** | <&backend/app/api/routes/workspace.py>：`git/diff` 本地模式确实按 topic 走 `ws.topic_diff`，但 `git/log` 本地模式无条件调 `ws.git_log(project_id)`，而 <&backend/app/domain/workspace/service.py> 里这个函数**根本没有 topic 参数**。只补前端不够 |
| 「工作区干净，无未提交改动」是假话 | 无 ref 时走的是 `git show HEAD`（主干最后一次提交）。传 topic 后拿到的是 `base...branch` 全量分支 diff——是「采纳会合并的改动」，不是「未提交改动」 |

### 资源面板（P1-8）

| 现象 | 根因 |
|---|---|
| 3 轮显示成 43 | <&backend/app/domain/usage/repositories.py> `_agg` 用 `func.count()` 数**表行数**当轮次。一轮会写多行：订阅代理每个 `/v1/messages` 响应一行，gateway 延迟补账再一行 |
| 228 万 token 显示 `$0.0000` | `subscription_ingest._land_row` 写死 `cost_usd=0.0`——订阅按月计费，本来就没有逐 token 单价。聚合层无法区分「真的没花钱」和「花了但不知道多少」 |
| sdk 路径 token 低估一个数量级 | <&backend/app/domain/agent/service.py> 只读 `input_tokens`/`output_tokens`，丢掉两个 cache 桶。另两条路径都折算了——**同一件事有三份各写各的算术** |

## 已做的改动

**后端**

- <&backend/app/domain/usage/tokens.py>（新）：cache token 折算收成一处，三条供给（sdk / hooks / 订阅代理）各自的字段方言在这里登记。<&backend/app/domain/agent/service.py>、<&backend/app/domain/agent/hook_events.py>、<&backend/app/domain/usage/subscription_ingest.py> 全部改调它，sdk 那条漏掉的两个 cache 桶补上。
- <&backend/app/domain/usage/models.py> + <&backend/alembic/versions/d4a1b6f27c90_resource_usage_turn_id.py>：`resource_usage.turn_id`（可空 + 索引）。**down_revision 已从 `c4e8f19b0d73` 改挂到 `b91c4d7e2a05`**——本分支开着的时候主干落了 `b91c4d7e2a05_machine_last_seen_at`，它和我这条挂在同一个父节点上，合并后就是两个头，PR #246 的 `migration-heads` 因此红了。按 `.claude/rules/migrations.md` 的规矩重挂到新头（不动主干上那条），已按「本工作区 + 主干新增那条」的合并后拓扑验证：单头、无悬空 down_revision。
- <&backend/app/domain/agent/chat.py>：5 处 usage 写入（含 gateway 延迟补账）都带上本轮 turn_id。
- <&backend/app/domain/usage/subscription_ingest.py>：`TurnIndex` 用代理日志自带的 `ts` 把每行归属到该话题当时在跑的那一轮；超出 6 小时窗口不归属（宁可算独立一轮，也不硬套到别人头上）。
- <&backend/app/domain/usage/repositories.py>：`turns = count(distinct turn_id) + 无 turn_id 的行数`；新增 `unpriced_tokens`（有 token 却无单价的量）。
- <&backend/app/domain/workspace/service.py> + <&backend/app/api/routes/workspace.py>：`git_log` 支持 `topic_id`，取 `base..branch`（本话题自己的提交）。

**前端**

- <&frontend/src/api.ts>：`getGitLog`/`getGitDiff` 接受 topic 并透传；<&frontend/src/components/DocPanel.vue> 两个请求都带上。
- Git 面板文案：「本话题提交」/「本话题改动（相对主干）」，空态「本话题还没有自己的提交（采纳后它们会并入主干）」。
- <&frontend/src/lib/usageFormat.ts>（新）：千分位、按量级选精度的 `fmtCost`、以及**费用未知就写「未知」**的规则（`costLabel`/`costNote`）。
- 抽屉头加刷新按钮；Git/资源面板打开期间 20s 静默轮询（页面隐藏时跳过），并在一轮跑完时立刻重取——静默刷新不闪 loading。

## 测试

- <&backend/tests/unit/test_usage_cache_folding.py>：三条路径对同一轮给出同一答案。
- <&backend/tests/integration/test_usage_panel_numbers.py>：42 条代理日志跨 3 轮 = 3 轮；延迟补账不算新一轮；无法归属的量各算一轮、不硬塞给最近那轮；订阅 228 万 token 报 `unpriced_tokens` 而 cost 为 0；两个接口都暴露新字段。
- <&backend/tests/integration/test_git_panel_scope.py>：采纳前本话题提交可见；采纳后不串别的话题的提交；空话题不借主干历史。
- <&frontend/src/lib/usageFormat.spec.ts>：`$0.0000` 这类写法被钉死。

## 检查状态

| 检查 | 结果 |
|---|---|
| 后端 ruff | 通过 |
| 后端 pyright | 0 errors, 0 warnings |
| 后端全量 pytest（`-n 4`） | **3648 passed / 23 failed**，失败全部是沙箱环境问题，与本改动无关（见下） |
| 前端 eslint（改动文件） | 通过 |
| 前端 vitest 全量 | 22 文件 / 226 项全绿（含新增 9 项） |
| 前端 vue-tsc / vite build | **跑不了**：沙箱 cgroup 上限 2GB，node 堆给到 1GB 仍 OOM（build 退出码 137）。改用 `@vue/compiler-sfc` 对 <&frontend/src/components/DocPanel.vue> 做 parse + compileScript + compileTemplate，全部干净，新增绑定都正确暴露给模板。**类型层面仍需 CI 复核** |

### 那 23 条失败是什么

- 22 条：`test_machine_service.py`(21) + `test_tmux_control.py`(1)——CLAUDE.md 已记录的沙箱缺 procps（`ps`/`kill` 不存在）。
- 1 条：`test_market_api.py::test_market_lists_ai_and_compute_pools`——沙箱没配 LLM 凭证，默认 AI 池 `available=False`。同样是环境，不是代码。
- 顺带一提：CLAUDE.md 里写的「无 git identity → 43 条失败」这次**没有复现**，本次新增的 Git 面板集成测试是真跑真过的。

## 下一步

递验收卡给 <@wangchangxin>。合并前请让 CI 补跑一次前端 `vue-tsc`（本沙箱内存跑不动）。
