# room 与 task 的设计，以及审批流程

> 目标：把「房间（room）／一件事（task）」这个模型，和「从做完到合进 main」这条审批流程，
> 按**代码里现在真的是什么样**和**文档里当初怎么设计的**分别讲清楚，并列出两边不一致的地方。
> 提问人 @fulu，2026-08-17。

## 0. 先排掉一个歧义：「task」在本仓库有三个不同含义

这三个东西同名但毫无关系，讨论时必须先指明是哪一个：

| 说法 | 是什么 | 代码位置 |
|---|---|---|
| **TopicKind.task** | 话题树里「一件活」——带分支、带验收卡，做完就结束 | <&backend/app/domain/topic/models.py> |
| **cx_task.Task** | 机构发布的**题目**（"用 AI 做推荐系统"），属于某个 Task Template（活动/项目集） | <&backend/app/domain/cx_task/models.py> |
| **domain/task/** | 老知是社区的作业任务（team/space 那套），与 CheeseX 话题体系无关 | <&backend/app/domain/task/> |

下文说 task 一律指第一个。

---

## 1. room / task 模型

### 1.1 设计意图（文档侧）

出处：<&docs/plans/2026-08-08-topic-model-places-and-actors-design.md>。

它要解决的 bug 是一句话：**"采纳之后什么都追不到了"**。原因不是缺功能，而是一个「话题」
同时是四样东西，四样东西的自然寿命完全不同：

- **房间**：有成员名册、@all 能触达 —— 应该永远活着
- **一件事**：有验收卡、会被采纳、会归档 —— 做完就结束
- **工作区**：一个 git 分支 + 一个容器 —— 可以随时销毁重建
- **会话**：一个 tmux 进程 —— 就是个进程

四个挤成一个对象，**最短的寿命赢**：一采纳，房间、工作区、记忆全跟着没了。

于是设计成四层，各自寿命分明：

```
房间          永久        消息、成员名册
 └─ 一件事      做完即止     分支/PR、验收卡、进度
     ├─ 会话     可续        --resume
     └─ 工作区    可丢        从分支重建
```

关键取舍两条：
- **一件事不能再拆一件事**（不嵌套）。要拆就在同一个房间里再开一件事，而不是把树挖深一层。
  Claude Code 的 agent team 也是这个选择（队员不能再招队员）。
- **判断某个东西该不该是平台概念，只问一句**：它的产出会不会落进房间里所有人都能看见的公共记录？
  会 → 平台概念（一件事就是）；不会 → 实现细节（子 agent、容器里的辅助进程）。

### 1.2 代码里现在是什么样

- `TopicKind` 有四个值：`root`（项目本体）、`topic`（房间）、`task`（一件事）、
  `subtopic`（`task` 的历史值，老数据用，不再新建）。
- 谁是房间谁是活，由**层级**决定，不是由谁指定的：
  `_child_kind()` —— 根话题的孩子是房间，房间的孩子是一件事。写死在
  <&backend/app/domain/topic/services.py>。
- **不嵌套已经落地**：一件事的孩子还是一件事？不会——因为父不是 root，`_child_kind` 一律给 `task`，
  但拆活入口本身没有禁止在 task 下面再 split（见下面的差异 ④）。
- agent 收到的操作说明按阶段分发：`resolve_stage()`（<&backend/app/domain/agent/stages.py>）
  把房间算成 `delegating`（拆活）、一件事算成 `working`（干活），再据此只注入对应那一段 skill。
  **这是"房间不干活、只拆活"这条规矩唯一的落地方式——靠提示词，不靠后端强制。**

---

## 2. 审批流程（从做完到进 main）

### 2.1 现在实际跑的这一条

```
芝士自己把检查跑绿（这是干活的一部分，平台不替你跑）
  → cheese accept-request <人> --subject '...'   递验收卡，卡直接是 pending
     → 平台立刻用【App 自己的 token】推分支、开 PR（不用任何人的个人 token）
        → GitHub Actions 按 .github/workflows 跑真 CI
           → 人在卡上点「采纳」＝ 授权，不是合并：卡进 pr_open，话题不归档、容器不停
              → 轮询器每 60 秒过四道阀，全过才调 GitHub 合并 API
                 → 合并成功 → 话题归档、释放算力、结果回房间
```

四道阀（都在 `_advance_pr_checks`，<&backend/app/domain/review/services.py>）：

1. **必跑检查名单**：`accept_required_check_names`，默认 `test:backend/**;.github/workflows/test.yml`。
   名单里的检查**没出现 = 还在等，不算通过**。名字后面挂的路径是"对哪些改动才要求它"——
   纯前端 PR 不该等一个只在 `backend/**` 触发的 `test`。等超过 30 分钟不会自动放行，而是**回来找人**。
2. **绿必须绿在当前基线上**：分支落后 main 就自动调 GitHub 的 update-branch 换基，等新一轮 CI；
   连换 3 次还追不上就交给人。防的是"两个各自绿在旧基上的 PR 合起来是红的"。
3. **授权范围没漂移**：人点采纳那一刻的 commit 被冻结成 `pr_authorized_sha`；之后芝士推的每个修复
   都会跟 head 一起动。合并前比对两次的文件清单，新提交越界（比如多碰了迁移、prod 配置）就回来找人。
4. **目标不是 prod**：合进 prod 永远要人自己点，机器不代劳。另外「没有任何 CI 真的跑过这次改动」
   同样不享受免人自动合并。

### 2.2 人在这条流程里的三个动作

| 动作 | 接口 | 含义 |
|---|---|---|
| 采纳 | `POST /accept-cards/{id}/accept` | **授权**："以我的名义送进 CI，全绿且没越界就合" |
| 人工放行 | `POST /accept-cards/{id}/merge-anyway` | 明知没全绿也要合。默认拒绝、显式放行，署名留痕 |
| 作废 | `POST /accept-cards/{id}/void` | 把卡片推进终态，解开 `pr_open`/`conflict` 卡死。**不是放行** |

三条都要登录，且都由 `_forbid_ai` 挡住芝士——协作模式下 **AI 不能验收自己做的东西**（spec §4.4 硬规则）。
撤销采纳（`revoke`）只有原采纳人或项目组长能做，会把话题从归档拉回 active。

### 2.3 平台的私有闸门已经退休了（重要，且与好几处文档/帮助文本冲突）

`review/gate.py` 第一行就写着 **THIS RUNNER IS NO LONGER DISPATCHED**。核实结论：

- `create_card()` 现在**总是**把卡建成 `pending`，不再进 `pending_gate`。
- <&backend/app/api/routes/accept.py> 里已经没有 `gate.dispatch` 调用，只剩 `pr_publish.dispatch`。
- `project.settings.check_command` 这个字段还在（设置页还能改），但**没有任何代码会去跑它**。
- 只有 `gate_sweep` 还在跑，作用是清理退休前留在库里的历史 `pending_gate` 行。

设计理由（<&docs/accept-is-merge.md>）：一张卡就是一个 PR 的视图，**判断改动好不好是 PR 上的真 CI 的事，
不是平台自己写一个私有检查器**。旧闸门跑在没有 Postgres 的机器上，只能 `check.sh --no-tests`，
所以"卡是绿的"历史上只等于 ruff+pyright 过了，一个测试都没跑；而且它是个纯内存
`asyncio.create_task`，dev 上 155 张卡有 29 张（19%）死在里面。

---

## 3. 文档与代码不一致的地方（按重要性排）

① **`cheese accept-request` 的帮助文本还在讲闸门**（<&backend/sandbox/cheese>）：
"配了质量闸门的项目，递卡后平台会自动跑一遍检查，红了卡片会被打回"。**这条已经不成立了。**
危害是实打实的：agent 读到它会以为递卡后还有一道检查兜底，于是不自己跑测试就递卡——而现在递卡
直接开 PR，第一次真检查发生在 GitHub 上。**建议改掉这段帮助文本。**

② **`docs/accept-is-merge.md` 的「Card states」那张表从没实现**。文档设计的是
`opening/checks_pending/checks_failed/ready/merged/closed`，实际跑的是
`pending → pr_open → accepted`。文档开头的 Status 段自己承认了这一点，但表还在正文里，容易被当成现状读。

③ **spec §6 说"子话题（三级，可以继续嵌套）"，新设计和代码都是"不嵌套"**。
<&docs/spec.md> 是较早的产品 spec，places-and-actors 明确推翻了它。

④ **"房间不带分支、不走验收卡"是约定，后端不强制**：
`branch_for_topic()` 对任何话题 id 都给一个分支名，不看 kind；`AcceptService.create_card` 也完全不看 kind。
也就是说，给一个房间递验收卡在技术上是能成功的。挡住这件事的只有注入给 agent 的阶段说明。

⑤ **设计里"一件事是房间时间线上的一张卡片"，前端还没做到**。现在 task 仍是左侧树里的节点；
房间时间线上只有一行读时派生的「已派出」标记（<&frontend/src/lib/splitMarkers.ts>），
而且它自己的注释写明：`cheese split` 不往父话题写任何 block，所以"哪几条消息/哪一项待办归了别人"
派生不出来——数据库里没有那条边。

⑥ **fusion-design §3 与 places-and-actors 直接对立**。前者（2026-07）说"队友把群聊和事项拆两层，
**我们不拆**"；后者（2026-08-08）说必须拆，理由就是不拆导致采纳后什么都追不到。
后者是更晚的结论，但前者没标注失效。

⑦ **退回验收卡不通知任何人**（代码核实）：`reject()` 只改数据库那一行，既不发消息也不 summon 芝士
——跟 CI 失败会 summon 的行为不一致。所以每次退回一张卡，必须额外往子话题里发一条评论说明理由，
不能假设对方会知道自己被打回了。

---

## 4. 待办 / 下一步

- [ ] 改 `cheese accept-request` 的帮助文本，删掉闸门那段（差异 ①）—— 这条直接在误导 agent，优先级最高
- [ ] `docs/accept-is-merge.md` 的 Card states 表加失效标注，或删掉（差异 ②）
- [ ] `reject()` 补通知（差异 ⑦）——是否要做，请 @fulu 或 @andy 拍板
- [ ] spec §6 / fusion-design §3 的失效标注（差异 ③⑥）
