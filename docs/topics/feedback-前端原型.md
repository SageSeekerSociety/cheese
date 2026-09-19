# Feedback 前端原型

## 目标

给「反馈」功能定形：先做一套**可点的前端原型**（不是稿图），确认三块界面——用户提交/浏览反馈、反馈详情、管理员处理台——的信息结构和操作是否合适；再据此写一份**能照着实现的后端设计稿**。

产出物有两件：原型页面本身，以及 <&docs/topics/反馈功能后端设计-方案稿.md>。

## 现状（2026-09-19）

原型四页（三块界面 + 一页设计稿）已按设计系统打磨完；后端方案稿已写完、逐条对照代码核对过，已合入需求方答复的三条结论，并补上了「每个 harness 都能主动提反馈」的那一节。全部在 PR [#1222](https://github.com/SageSeekerSociety/cheese/pull/1222) 上，**待人工验收**（验收人 wangchangxin，当前无有效批准）。分支上的提交：

- `91d76fdcb` 原型页面按设计系统返工
- `9050f1a47` 让原型可被预览的构建与打包工具
- `448261cae` 后端设计稿（对照仓库事实核对过）
- `02cd1eb40` 修 CI 报的 3 个格式错误（只动格式）
- `9f0b5ba46` 把需求方答复的三条结论合进方案稿
- `20cc7a1b`、`1790f6d4a` 方案稿按需求方第二轮答复加深；作者可见自己的私密反馈、已解决项沉底
- `0499ec3df`、`9ae3e0645` 把数据关系与数据流画成图（新增设计页）
- `d58b7fa82` 把三条卡开工的问题放在图下面
- `468bf1494` 方案稿 §5.9：一个 CLI 子命令如何落到三个 harness

**看原型**：PR 里带的打包工具把页面打成单文件 HTML，用本机 4712 端口的静态服务 + 话题自带的预览隧道提供。页面是**可交互的**（能点进详情、能切角色、能筛选搜索、能在「数据关系」和「数据流」两个标签间切图），不是截图。仓库里的 <&pack-feedback-prototype.py> 就是打包脚本，改动原型后重跑即可。

**读方案稿**：把文件直接发给人的通道报 401（试过两次），所以方案稿另外渲染了一份带样式的 HTML，挂在本机 4712 端口的同一个静态服务上：预览地址 + `/feedback-backend-design.html`。这条通道是临时的（靠一个本机进程活着），长期入口仍然是仓库里的 markdown。

## 原型包含什么

四个页面 + 六个组件，都在 `frontend/src/views/feedback/` 与 `frontend/src/components/feedback/`：

| 页面 | 作用 |
| --- | --- |
| `FeedbackCenterPage` | 反馈中心：列表、筛选、搜索、提交入口 |
| `FeedbackDetailPage` | 单条反馈：正文、证据、支持、评论时间线 |
| `AdminFeedbackPage` | 管理员台：表格、分派、改状态、内部备注 |
| `FeedbackDesignPage` | 设计稿页：数据关系图（ER）+ 数据流图（架构），以及三条待定问题 |

打磨时定下的几条，之后改原型要沿用：

- **一屏只有一个琥珀色主操作**，且主操作按钮必须显式写 `color="primary"`——Vuetify 的裸 `<v-btn>` 渲染出来是白底按钮，不会用主题色。三屏各自的落点：反馈中心「提交反馈」、详情页「发表评论」、管理员台没有（状态/优先级/指派是选完即生效的选择框，琥珀只用在当前选项卡上）。
- 支持按钮做成中性 tonal（`secondary` + `mdi-thumb-up-outline` / `mdi-thumb-up`），把琥珀色让给主操作；三个非颜色信号（形状、图标、文案）都随状态变。
- 正文级文字不用 `--faint`（对比度 2.6:1，只够放元信息），用 `--muted`。
- 管理员表格用 `:deep()` 选择器压过 Vuetify 自带的 th/td 规则，不写 `!important`。
- 顺手修了一个仓库级 bug：`color="primary"` 的禁用按钮因为 `.bg-primary` 带了 `!important`，看起来仍然可点，而 `pointer-events: none` 又把点击吞掉。修在 `frontend/src/style.css`，对所有实心按钮生效。

## 设计页里的两张图

图是**按方案稿的数据画的**，不是示意：`frontend/src/lib/feedbackDiagram.ts` 是唯一数据源，`ErDiagram.vue` / `ArchDiagram.vue` 只负责画。改了表或改了链路就改这一处，图会跟着变；`diagramSpec.spec.ts` 用测试守住几条硬约束（新表必须有连线、标了「待定」就得写清没定什么、每层不超过六个节点、边上的文字要短——都是会被撑破或盖住的量）。

- **数据关系**：12 张表 / 4 个分组 / 12 条关系。六张新表都挂在 `feedback` 上，话题、项目、会话只是可空的上下文指针；虚线表示没有真外键，虚线框 + 「待定」表示方案稿里还没定的表（`feedback_proposal`、`feedback_read_states`）。
- **数据流**：6 条泳道 / 28 个节点。四条提交通道最后都收敛到同一条 `POST /feedback`；agent 那条先成为提案，等人拍板。

图下面是「还差三句话，图就完整了」——把 §8.1、§8.3、§8.23 三条摆在这里，因为只核图的人正好会经过这一页，而「图看着挺完整」正是这三条最容易沉底的地方。

## 后端设计稿的结论

方案稿的正文在 <&docs/topics/反馈功能后端设计-方案稿.md>，这里只记它定下来的方向：

- **主表就是 `feedback`**，不做「用户反馈」和「机器人反馈」两套——用 `author_is_agent` 区分，`submitted_by_handle` 记录代提交的人（agent 提单、人确认时两者不同）。
- **分类字段一律 `native_enum=False, length=16`**，沿用 `AlertKind` 的既有做法：加枚举值不需要迁移，也没有 CHECK 约束挡着。
- **支持数不做 toggle 接口**，用 `POST` / `DELETE /feedback/{id}/supports`，让重试幂等。
- **看不见的反馈返回 404 而不是 403**，避免把「存在但你没权限」这件事泄漏出去。
- **权限走 `ActorResolver`，不用 `require_auth_user`**——因为提交者可能是平台账号而不是登录用户。
- 通知不复用 `alerts` 表：`alerts.project_id` 是 NOT NULL，反馈可以挂在话题上、也可以不挂项目。

## 每个 harness 怎么主动提反馈（方案稿 §5.9）

需求方要求「每个不同的 harness 都要有能主动提反馈的渠道」。结论比预想的短：**加一个 CLI 叶子命令就够，不用写三遍。**

三个 harness 取的是**同一份东西**——平台 CLI（`backend/sandbox/cheese`）的 argparse 树：

- claude_code：会话里的 MCP 服务器把 `cli {method:"tools/list"}` 发给 executor，由 `cli_worker` 把 argparse 树转成工具 schema。
- pi：没有 MCP，`harness/pi/catalog.py` 直接读机器上装着的那个 CLI，`platform.ts` 再把每个注册成 pi 的原生工具。
- codex：从 executor 的 `native` 服务器和其余 MCP 服务器发现，平台工具随它那份 CLI 一起来。

所以 `cheese feedback propose` 加进那棵树，三个 harness 同时就有了。两条值得记住的推论：**工具名是算出来的**（`feedback propose` → `cheese_feedback_propose`），**`help=` 就是工具 schema**（写进 argparse 的措辞直接是模型看到的那份，不存在文档和实现漂移）。另外，「无屏巡检轮次不给这个工具」不需要专门实现——该命令和 `cheese chat send` 一样要 `CHEESE_TOPIC`，巡检轮次没有话题，命令自己就拒绝；**门在服务端，不在工具清单上**。

## 已定的决策（2026-09-18，需求方答复）

- **私密反馈，提交者本人可见**（原 ★8.2）。可见的人是「提交者本人 + 平台管理员」；查询条件要同时算 `author_handle` 与 `submitted_by_handle`，因为 agent 提案、人确认提交的那一类两者不同。
- **反馈中心是平台共享的，不以组织（Space）为单位**（原 ★8.10）。列表查询里没有 Space/项目维度，「公开」= 对所有登录用户公开；`project_id`/`topic_id` 只是**出处**，不是可见性范围。
- **默认不展示已解决的 bug**；「隐藏所有已解决的」还是「只隐藏已解决的 bug」还没定，见下。

## 仍然待定

- **§8.1 谁算平台管理员**——唯一还卡着开工的一条。代码里 `SystemRole.SUPER_ADMIN` 有定义、有读取，但**生产路径上没有任何一处给它赋值**，所以现在没人真的拥有这个角色。方案给的建议是照 `dogfood_owner_handles`（设置项）和 `branch_protection.override_handles`（项目设置，已经在 `review/services.py` 当鉴权闸门用）的先例，用一个 handle 白名单；不要点亮 `SUPER_ADMIN`，那要动 1.0 的权限框架，而它连 FEEDBACK 这个资源都没有。
- **§8.3 `security` 与 `visibility` 的关系**（同样卡开工）：`security` 应当是 `private` 之下的子类，而不是第二个开关。
- **默认筛选的确切口径**（§8.23）：隐藏「所有已解决的」还是「只隐藏已解决的 bug」。
- 其余十几条细节问题见方案稿 §8。

## 下一步

1. 把最后四个提交推上去（`cheese_push_fix`），然后请 wangchangxin 重新验收——推新提交会清掉上一轮的批准。上一次推送时六个检查（`check`、`guards`、`guard`、`scope`、`empty-pr-guard`、`e2e`）在 PR 头上全绿。
2. §8.1 拍板后，方案稿 §8 收敛，就可以拆实现任务。
3. 原型侧还有几个小问题没定，见方案稿与聊天：v-card 圆角在仓库里 24px 与文档写的 12px 不一致、原型里两处「（原型：…）」的括注是否保留、列表页页脚的可见性说明要不要留、以及视图层 i18n 要不要做（现状 128 个视图里 117 个含中文字面量，规则没被强制）。
