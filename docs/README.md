# docs

**设计在演进中的东西不在这里，在 issues。** 这里只放两种：改代码时必须遵守的约束，和当前真实落地的样子。判据是"这段过时了谁会发现"——如果答案是"没人，直到有人照着做错事"，那它属于 issue 而不是 docs。

## 改代码前必须读的（过时 = breaking）

| 文档 | 内容 |
|---|---|
| [`agent-principles.md`](agent-principles.md) | 已经拍板、不再重新讨论的判断 |
| [`api-conventions.md`](api-conventions.md) | 调 API 该发什么 URL：`/api` 挂载点、路由裸路径与浏览器路径的差别 |
| [`design-system.md`](design-system.md) | 前端视觉唯一规范：亮/暗双色板、圆角/字号/间距档位、琥珀用在哪 |
| [`i18n.md`](i18n.md) | 界面字符串怎么存、怎么命名、缺翻译怎么办；**加界面文案或改 `frontend/src/i18n/` 前必读** |
| [`i18n-glossary.md`](i18n-glossary.md) | 核心术语的中英对照；**写英文译文前先查它**，防止同一个中文词译出两个英文词 |
| [`device-self-hosting.md`](device-self-hosting.md) | 自托管设备：**§0 是"别人的机器"约束**，其余是接入流程与排障 |
| [`workflows.md`](workflows.md) | 这个项目实际怎么开发、测试、迭代 UI |
| [`testing-without-docker.md`](testing-without-docker.md) | 没有 docker 的机器上怎么跑全量测试；provider 凭据那条红别再重新诊断 |

## 当前是什么样（描述现状）

| 文档 | 内容 |
|---|---|
| [`product-impl.md`](product-impl.md) | 当前真实落地的产品行为与实现方式 |
| [`infrastructure.md`](infrastructure.md) | 这个应用跑在哪、怎么发布、数据在哪 |
| [`agent-liveness.md`](agent-liveness.md) | 一轮什么时候算卡住、什么会结束它、结束后房间看到什么；时限只记不闸，失败不自动重跑 |
| [`where-a-turn-runs.md`](where-a-turn-runs.md) | 一轮活落在哪台机器上：两条执行路，一台机器都没有时会怎样，以及归档后机器上的东西什么时候退、怎么退 |
| [`microcloud.md`](microcloud.md) | Cloud 机器从哪来、`aiStatus` 各值对芝士意味着什么、ccproxy 在其中的位置，以及改 MicroCloud 的代码怎么合并、交给运维 agent 部署、发版 |
| [`what-the-agent-perceives.md`](what-the-agent-perceives.md) | 芝士收得到什么、收不到什么：平台的三十种事件里有多少回流给它（答案是零） |
| [`accept-is-merge.md`](accept-is-merge.md) | 采纳=当场合并那个 PR；「只在绿的时候合」靠项目配置的分支保护规则 |
| [`spec.md`](spec.md) | Cheese 2.0 产品与实现 Spec |
| [`evals.md`](evals.md) | 评测配套 |
| [`feishu-lark.md`](feishu-lark.md) | lark-cli 团队配置：飞书操作走 `--profile cheese`，含踩坑速查 |

## 历史决策与计划（**可能已被 issue 取代，先查 issue**）

| 文档 | 状态 |
|---|---|
| [`fusion-design.md`](fusion-design.md) | 2026-07-09 决策汇编 |
| [`convergence-plan.md`](convergence-plan.md) | local→device 收敛计划 |
| [`permission-audit.md`](permission-audit.md) | 一次性权限审计记录 |
| [`unification-plan.md`](unification-plan.md) | 统一化计划 |

## 目录

| | |
|---|---|
| `manual/` | **用户说明书**——唯一写给平台使用者而不是写给改代码的人的目录；站点在 `okcheese.com/docs`，芝士答流程问题时引用它的锚点 |
| `plans/` | 演进计划（带日期，文件头标注实施状态） |
| `topics/` | 话题实况文档（芝士在话题里维护的状态摘要，采纳后随之进 main） |

| 住在别处 | |
|---|---|
| 知是 Vision（愿景与需求） | [飞书](https://acnxgqu0961c.feishu.cn/wiki/TQnywMqkHi8YhikInzccMOXOnsb) — 产品方向，去那评论 |
| 产品方向反馈 | [飞书](https://acnxgqu0961c.feishu.cn/wiki/PtVAwDevmiKFnbkQiVAcuqNvnZf) |
| 机器形态（Cloud / Hosted Sandbox / Hosted Machine） | **#358**——三类里两类还没传输，是演进中的设计 |
| 算力模型为何是这个样子 | **#282**（一个字段挤着四件事）、**#442**（两种机器被建模成一种加两个开关） |

约定：产品/方向类文档住飞书（多人评论）；改代码时需要同步改的文档住 repo。
飞书文档不留本地副本（防陈旧）；需要时 `lark-cli markdown +fetch` 拉临时工作副本，改完 `+patch` 推回，副本勿提交（已 gitignore 兜底）。
