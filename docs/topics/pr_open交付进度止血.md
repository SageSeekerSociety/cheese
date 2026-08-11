> **状态**：改动完成、检查跑绿，已递验收卡给 <@wangchangxin>。沙箱里起了一份完整的 CheeseX（demo 数据）可以直接点开看。

## 目标

人点完「采纳并归档」之后，界面上不能再是一片空白。后端 2026-08-09 加的 `pr_open`（PR 已开，CI → 合并 → 部署还要跑几小时）前端完全不认识，三个 computed 一个都不匹配，整个合并框直接消失。本话题只做止血：把已有的 PR/CI 展示接到 `pr_open` 上，做成最终形态的**真子集**。

## 改了什么（纯前端四个文件，后端零改动）

1. <&frontend/src/cx_types.ts> —— `AcceptStatus` 补 `'pr_open'`；`AcceptCard` 补 `pr_repo` / `pr_head_sha` / `pr_merged_at`（后端 `AcceptCardOut` 早就在返回，只是前端没声明）。
2. <&frontend/src/lib/deliveryStage.ts>（新增）—— 阶段推导做成纯函数：`deliveryStageOf(card)` 返回 `phase / title / hint / steps`，`deliveryNoteTone(note)` 把后端写在 `note` 上的 emoji 前缀翻成颜色。配套 <&frontend/src/lib/deliveryStage.spec.ts>，6 个用例。
3. <&frontend/src/views/WorkspaceView.vue> —— `deliveringCard` / `deliveryStage` / `deliveryNote` 三个 computed；`#timeline-end` 那个总 `v-if` 加上 `deliveringCard`（不加等于白做）；`prChecks` 轮询的取卡口径从 `pendingCard` 换成 `prCheckCard = pendingCard ?? deliveringCard`，那个 15 秒 timer 在交付中顺带刷新卡本身（否则界面停在采纳那一刻的快照上）；新增只读的「交付中」渲染分支。

卡面内容：阶段标题 + 一句说明 → 阶段条（已采纳 › CI 检查 › 合并进 main › 部署）→ 卡的 `note` → PR 链接 + 短 sha + 实时 CI 各项。**没有任何按钮**——授权在人点下去那一刻已经给过了。

顺带被这个卡面接住的一件事：后端把「CI 红了 / 部署失败 / GitHub 拒绝合并 / 轮询 token 失效」全写在卡的 `note` 上，而那张卡的界面恰好就是不显示的那个（父话题 §九.A / §九.E）。现在它们至少有地方露头了——**这不是修那两个缺陷，只是不再对用户静默**。

## 那笔唯一允许的债在哪

`pr_merged_at === null ? 等CI : 等部署`——后端用一个字段盖两个阶段，这个推导被复制进了前端。它**只存在于 <&frontend/src/lib/deliveryStage.ts> 的 `deliveryStageOf` 里那一个 `if`**（源码里有 👇 标注），文件顶部的注释写明了不许外扩；模板和别的 computed 一律只读它的返回值。把它拎成独立文件而不是留在 `.vue` 里，就是为了让这条约束有个明确边界、并且能被单测钉住。将来交付轨拆成独立状态，只换这一处的取数来源，渲染分支和文案原样保留。

## 约束遵守情况

- 只读、不加操作按钮 ✅
- 不碰后端 ✅（有平行子话题在改 review/services.py）
- 不动 `AcceptStatus` 末尾的 `| string` 兜底 ✅（那条待拍板，不在本话题）
- 已归档话题上的历史卡 ✅ —— `deliveringCard` 不看话题状态，`acceptedCard` 分支要求 archived + `status='accepted'`，两者不重叠；已在沙箱里造了一个「已归档话题 + `pr_open` 且已合并」的卡（正是现存那 6 张的形状）验证渲染正常。

## 验证

| 检查 | 结果 |
|---|---|
| `bash .claude/scripts/check.sh --no-tests`（闸门跑的那条） | **3/3 PASS**（ruff / pyright / alembic heads） |
| 前端 vitest 全量 | **223/223 通过**（含新增 6 个） |
| 前端 `vue-tsc --noEmit` | 我改的 4 个文件 **0 error**（仓库另有 33 条历史报错，都在 `src/views/spaces/**` 等无关文件，与本改动无关） |
| `eslint`（含 prettier） | 我改的 4 个文件 0 error |
| SFC 能不能编译 | vite dev 实际编译 `WorkspaceView.vue` 通过（200 / 239KB） |
| 端到端取数 | 起了真后端 + PG + Redis，`GET /api/topics/{id}/accept-card` 三张 `pr_open` 卡的 `pr_repo` / `pr_head_sha` / `pr_merged_at` 都正常返回 |

**没跑成的两项，都是沙箱限制不是代码问题**：`pnpm run build` 被 cgroup 2GB 内存上限 OOM-kill（exit 137，停掉所有 dev server 后仍然如此）；Playwright 截图起不来（chromium 缺 `libglib-2.0`，装系统依赖要 root）。所以没有截图，改用下面的实时预览代替。

## 怎么自己看一眼

右侧预览里就是沙箱内起的完整 CheeseX（真后端 + PG + Redis + demo 数据）。用 `alice` / `demo12345` 登录，三个话题分别是三种形态：

- 「知是 2.0 融合演示 › 搭建第一个原型」——**等 CI** 阶段
- 「AI 系统实验室 › 设计推理服务架构」——**CI 红了**（note 以 `⚠️` 打头，红字显示）
- 「数据分析平台 › 梳理数据管线」——**已合并、等部署**，且这个话题是 **archived**（复现现存 6 张孤儿卡的形状）

PR 是假的（指向 `demo/cheesex`），所以 `/pr-checks` 返回 `available:false`、CI 明细列表为空——这本身也顺带验证了取不到 CI 时不会炸、只是少显示几行。

## 留给父话题的话

- 这个卡面是「授权卡 + 交付轨」里**交付轨的最小可见形态**，不是新概念。
- 三件待 <@wangchangxin> 拍板的事（交付轨加字段还是拆表、授权有效期默认值、`| string` 兜底去不去）本话题一件都没碰。
