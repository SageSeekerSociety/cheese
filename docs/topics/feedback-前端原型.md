# Feedback 前端原型

## 目标

做一套「反馈」功能：**界面**（反馈中心、单条详情、管理员处理台、设计页）、**后端**（三条路由 + 七张表）、以及**每个 harness 主动提反馈的通道**。

顺序是先在话题里把界面口径和产品判断定完，再照一份能照着实现的方案稿落代码。标题里的「原型」是起点：界面先是可点的原型（不是稿图），确认信息结构和操作合适之后才接真接口——现在接上了，所以这份文档覆盖的是整套功能，不只是原型。

产出物：

- 代码：PR [#1222](https://github.com/SageSeekerSociety/cheese/pull/1222)，前后端在**同一个 PR**里。
- 设计依据：<&docs/topics/反馈功能后端设计-方案稿.md>，代码里每一个判断都能在这里找到出处。
- 设计页的两张图：<&frontend/src/lib/feedbackDiagram.ts> 是唯一数据源。

## 现状（2026-09-19）

功能已经实现并推上 PR #1222，**待人工验收**（验收人 wangchangxin）。这一轮推的是新提交，上一轮没有有效批准，所以没有需要作废的批准。

- **后端**：七张新表，迁移 `backend/alembic/versions/b7c2e91f4a03_feedback.py`；三条路由 `/api/feedback`、`/api/admin/feedback`、`/api/topics/{id}/feedback-proposals`；域层在 `backend/app/domain/feedback/`（models / repositories / services / schemas / proposals）。私有反馈的可见性是**读时收窄**：`services.may_see` 一处决定谁能看，仓储层不回答策略问题。
- **前端**：四个页面读真接口。`stores/feedback.ts` 调 API，上一版那个内存 mock（`lib/feedbackMock.ts`）已经删掉——界面不再是「照着自己编的数据画」。
- **CLI**：`cheese feedback propose` 进了 argparse 树，三个 harness 同时就有了这个工具（见下）。
- **合并了 main 的 35 个提交**。合并带出一处**语义**冲突，已修：main 的 `f3a8c5d2e917` 把 agent 身份从「由话题派生」改成「属于 agent 自己」（`agent_instance_handle`），而我的测试原本按话题算 handle 再断言卡片的作者。现在改成从卡片上读作者，断言「是 agent、且不是提单的人」——身份不由反馈代码算。

**看界面**：预览是开着的，形态是**真页面 + 假数据**。预览通道上没有后端，所以我在预览入口的 `fetch` 那一层接上了假数据（<&frontend/src/proto-feedback-fixtures.ts>），页面本身一行都没改、走的是真接口。页面可交互：能点进详情、能切管理端四栏、能筛选搜索、能在「表与关系」和「数据流」两张图之间切。打包脚本是 <&pack-feedback-prototype.py>，改完页面重跑即可。

## 界面打磨时定下的（之后改界面要沿用）

- **一屏只有一个琥珀色主操作**，且主操作按钮必须显式写 `color="primary"`——Vuetify 的裸 `<v-btn>` 渲染出来是白底按钮，不会用主题色。三屏各自的落点：反馈中心「提交反馈」、详情页「发表评论」、管理员台没有（状态/优先级/指派是选完即生效的选择框，琥珀只用在当前选项卡上）。
- 支持按钮做成中性 tonal（`secondary` + `mdi-thumb-up-outline` / `mdi-thumb-up`），把琥珀色让给主操作；三个非颜色信号（形状、图标、文案）都随状态变。
- 正文级文字不用 `--faint`（对比度 2.6:1，只够放元信息），用 `--muted`。
- 管理员表格用 `:deep()` 选择器压过 Vuetify 自带的 th/td 规则，不写 `!important`。
- 顺手修了一个仓库级 bug：`color="primary"` 的禁用按钮因为 `.bg-primary` 带了 `!important`，看起来仍然可点，而 `pointer-events: none` 又把点击吞掉。修在 `frontend/src/style.css`，对所有实心按钮生效。

## 设计页里的两张图

图是**按真表画的**，不是示意：`frontend/src/lib/feedbackDiagram.ts` 是唯一数据源，`ErDiagram.vue` / `ArchDiagram.vue` 只负责画。改了表或改了链路就改这一处；`diagramSpec.spec.ts` 用测试守住几条硬约束（新表必须有连线、每层不超过六个节点、边上的文字要短——都是会被撑破或盖住的量）。

- **数据关系**：新建七张表——六张挂在 `feedback` 上，拒绝记忆挂在话题上。话题与项目是**真外键**（可空，删掉只把指针置空），会话只存 id 快照；**虚线表示没有真外键**。
- **数据流**：六条泳道（提交方 / 本地草稿 / 上报契约 / 平台服务端 / 存储 / 界面）。两条提交通道最后都落到 `feedback` 表：人直接写，agent 先成为提案卡、由人在卡上按发送。

## 三条产品判断（需求方答复 + 落代码时取的默认值）

- **私密反馈，提交者本人可见**。可见的人是「提交者本人 + 平台管理员」；查询同时算 `author_handle` 与 `submitted_by_handle`，因为 agent 提案、人确认提交的那一类两者不同。
- **反馈中心是平台共享的，不以组织（Space）为单位**。「公开」= 对所有登录用户公开；`project_id`/`topic_id` 只是**出处**，不是可见性范围。
- **默认不展示已解决的 bug**，取窄读法：**只沉底已解决的 bug**，已解决的 suggestion 不沉。
- **§8.1 谁算平台管理员** → `settings.feedback_admin_handles` 白名单（照 `dogfood_owner_handles` 的先例）。不点亮 `SystemRole.SUPER_ADMIN`，那要动 1.0 的权限框架。
- **§8.3 `security` 与 `visibility`** → `security` 是 `private` 之下的一层**读时收窄**，不覆写提交者自己选的 `visibility`。

后两条是落代码时按方案稿的建议取的默认值，每处都是常量或一处判断，验收人要改随时改。

## 每个 harness 怎么主动提反馈

结论比预想的短：**加一个 CLI 叶子命令就够，不用写三遍。**

三个 harness 取的是**同一份东西**——平台 CLI（`backend/sandbox/cheese`）的 argparse 树：

- claude_code：会话里的 MCP 服务器把 `cli {method:"tools/list"}` 发给 executor，由 `cli_worker` 把 argparse 树转成工具 schema。
- pi：没有 MCP，`harness/pi/catalog.py` 直接读机器上装着的那个 CLI，`platform.ts` 再把每个注册成 pi 的原生工具。
- codex：从 executor 的 `native` 服务器和其余 MCP 服务器发现，平台工具随它那份 CLI 一起来。

所以 `cheese feedback propose` 加进那棵树，三个 harness 同时就有了。两条值得记住的推论：**工具名是算出来的**（`feedback propose` → `cheese_feedback_propose`），**`help=` 就是工具 schema**（写进 argparse 的措辞直接是模型看到的那份，不存在文档和实现漂移）。另外，「无屏巡检轮次不给这个工具」不需要专门实现——该命令和 `cheese chat send` 一样要 `CHEESE_TOPIC`，巡检轮次没有话题，命令自己就拒绝；**门在服务端，不在工具清单上**。

## agent 只能提案，不能发布

`cheese feedback propose` 落下的是一张**提案卡**，不是反馈。人在聊天里按「提交反馈」才算发布。所以发送之后 `author_handle` 是卡上那个 agent、`submitted_by_handle` 是按按钮的人——两个字段，「芝士提的反馈里有多少真的被人发出去」才答得出来。

三道限流（同话题一天两张、同一个指纹提过、被「不用」过）各自回 412，因为它们对客户端的指令是同一句：别重试。被「不用」过这件事记在 `feedback_proposal_dismissals` 上——消息块不落表，但拒绝必须留下，否则同一个问题每轮都会再问一遍。

## 预览当测试用例跑，查出两个真 bug（已修）

界面接到真接口之后，冷启动（刷新或深链）才是第一次有人真的走那条路。两个都是这样发现的：

- **直接打开 `/admin/feedback` 是一张空表**。`onMounted` 没 `await loadMeta()`，于是 `store.isAdmin` 读到的永远是「还不是管理员」，列表压根不拉。从反馈中心点进去反而正常，因为那边已经把 meta 拉过了。
- **深链进详情页点「支持」没反应**。`_find` 只找列表和 `adminItems`，而那条路上两份都是空的；补上对 `detail` 的兜底才闭环。

## 验证

- 后端：`tests/integration/test_feedback.py` 21 条 + `tests/unit/test_cli_worker.py` 19 条通过。集成测试钉的是**产品判断**（私密对第三方回 404 而不是 403、已解决 bug 沉底、agent 不能自己发布、提案三道限流），不是接口形状。
- 前端：tsc ratchet 0、eslint 0 error、stylelint 63 基线、vitest 184 文件 1457 用例通过。
- 合并 main 之后以上全部重跑过。

**本机跑测试的坑**：并发跑的测试进程会共用同名测试库，而它们是用 `DROP DATABASE ... WITH (FORCE)` 建的——两个 run 撞上会把对方的库拆掉，症状是随机 403 和 KeyError。带一个 `CHEESE_CI_SLOT=<任意串>` 就有自己的库名了（见 `backend/tests/isolation.py`）。

## 下一步

1. 等 PR #1222 的 CI 与 @符露夀 的验收。推新提交会清掉上一轮的批准，所以验收人看到的就是这一版。
2. 验收人若要改上面那两条默认值（管理员白名单、security 读时收窄），都是小改。
3. 方案稿 §8 里还剩十几条细节问题，都是实现里已经取了默认值的，可以顺验收一起过。
