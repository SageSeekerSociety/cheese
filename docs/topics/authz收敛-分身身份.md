## 目标

让每个话题分身有**自己的 agent-user 身份**，把「谁干的」写进 token；然后（分阶段）把 authz 的 agent 短路拆掉。

- 阶段一：per-topic agent-user + token 带身份 + 现场处处按身份而不是按写死的 `"cheese"` 字符串判断。**已完成**。
- 阶段二：拆 `policy.py` 的 `is_agent` 短路。只出评估，不动手。
- 阶段三：authz 策略表、收掉两个宽松档（未认证 fallback + 无 roster 即放行）。

## 阶段一：已落地

| 点 | 现状 |
|---|---|
| handle | `topic_agent_handle()` 纯函数推导 `cheese-<topic_id.hex[:12]>` |
| user 行 | `IdentityService.ensure_topic_agent_user()`，昵称仍显示「芝士」 |
| token | <&backend/app/core/sandbox_auth.py> `mint_scoped_token` 在有 topic_id 时自动带 `a=<handle>`；7 处调用点未改 |
| 落地 actor | <&backend/app/api/auth.py> 读 `token_agent_handle()`，缺失时退回 `cheese` |
| roster | 建话题 seed 分身座位；老话题 `migrate_shared_agent_seat()` 就地迁移，无数据迁移脚本 |
| 三处硬编码 | `_forbid_ai` / 文档事件显示名 / 前端过滤全部改成认 `looks_like_agent_handle` 或接口的 `agent` 标记 |

测试：`test_agent_identity.py`、`test_topic_agent_identity.py`、`test_authz_policy.py`、`test_sandbox_auth.py`。

## roster / member 语义（已定案）

四条结论见决策记录。要点：**roster ⊆ member**；**@ 只通知、不授权**；**读权限的门是项目成员，roster 管的是群播范围和名单管理权**；**私聊必须单独判定**。

## 新查到的事实：@ 提及的「项目里没有这个成员」

现场报错属实，但机制和最初的诊断不同，而且暴露的问题更大。

**1. 校验用的其实是项目成员表，不是话题 roster。** <&backend/app/domain/agent/chat.py> `_notify_mentions` 的形参虽然叫 `roster`，两个真实调用点传进来的都是 `ProjectRepository.list_members(project_id)`。所以文案和它校验的东西本来是一致的。

**2. 真正的 bug 是「我没有名单」被当成「你不是成员」。** `_resolve_mentions` 只做 `h in handles`，名单为空时所有 @ 一律判成幻觉 handle。两条路会喂进空名单：

- 私聊话题（`topic.is_private` → `roster = []`）：私聊里 @ 任何人都报这句话。
- 掉线补录路径：`_persist_assistant_message(..., roster=[])` 硬传空表 —— 被 backfill 的芝士消息里所有 @ 全标红，**而且通知一条都没发出去**（静默丢失，比标红更糟）。

**已修**：空名单不再进 unresolved（既不通知也不指控）；补录路径改传 `roster=None`，由函数自己按 topic 去查项目成员表；文案改成「项目成员里没有这个 handle」。

**3. 同一个函数里两种 @ 认两张名单。** `<@handle>` 校验项目成员表，`@all/@here` 展开话题 roster（`TopicMembershipRepository.list_for_topic`）。后果：显式 @ 一个不在 roster 的项目成员，通知照发，正文前 200 字 + 话题标题一起推过去。按上面第 ③ 条结论这是可接受的（项目成员本来就能读该话题），但**私聊话题不适用**——见下。

## ⚠️ 私聊话题的鉴权口子（新发现，未修）

`TopicService.get_or_create_private()` **不调用** `_members.seed()`，所以私聊 topic 的 roster 是空的。于是 `authorize_topic_access` 走到最后一行：

```python
return not await roster_exists(topic_id)   # 无 roster → 放行
```

**任何已认证用户只要拿得到 topic id，就能进别人的私聊**——连项目成员都不用是。policy.py 全程没看过 `private_owner` / `private_peer`。

这是阶段三「两个宽松档」里 legacy 档的真实爆炸半径，比未认证 fallback 更急。修法二选一：私聊建 topic 时 seed 一个两人 roster（干净，顺带让「无 roster」真正只剩历史遗留话题）；或在 policy 里显式判 `is_private`。倾向前者。

## 身份塌缩成 anonymous：链路查清并已修

父话题拿自己的 scoped token 往子话题发评论、作者被记成 `anonymous` —— 三个缺陷叠出来的，**未认证 fallback 正是让这次写入落地的那一环**：

1. `POST /api/topics/{id}/comments` 不在 `_CHEESE_WRITE_PATHS` 里（故意的，人也要用这条路评论），网关根本不查 token。
2. <&backend/app/api/auth.py> `cheese_valid()` 用 `verify_scoped_token(..., topic_id=子话题)` 校验，token 的 `t` 是父话题 → False。**但只是"不认这个身份"，不是"拒绝"** —— 于是掉进 Phase-0 fallback，`body["author"]` 也没有，actor 变成 `anonymous`。
3. `anonymous` 的 `via="handle"` → `authenticated == False` → `authorize_topic_access` 第一行 `if not actor.authenticated: return True` **直接放行**。

所以：**带一个错 token 比不带 token 权限还大**，写入落地、作者被抹掉。这就是那个宽松档的实际后果，不是理论。

**已修**：`ActorResolver._reject_out_of_scope_token()` —— 签名有效、未过期、但 `p`/`t` 指向别的资源的 scoped token，一律 403，不再降级成 anonymous。刻意做窄：header 缺失、格式错、已过期、全局 `SANDBOX_TOKEN`（`scoped_token_claims` 对这四种都返回 None）行为全不变；无 `t` 声明的项目级 capability token（git-http / LLM 代理）不算越界。测试见 <&backend/tests/unit/test_token_scope_violation.py>。

**留给阶段三的**：第 3 条那个 `return True` 本身还在。这次是从上游堵住了「错 token → anonymous」这一条进入路径，没有动 fallback 本身。

## 未认证 handle fallback：本话题覆盖，属阶段三

`policy.py`：`if not actor.authenticated or actor.is_agent: return True` —— 不带 token 反而全通。`can_manage_roster` 同款（`if not actor.authenticated: return True`）。

- `can_manage_roster` **目前全仓没有任何调用点**，是死代码。它注释里「服务层自己还会查 role」只对 `TopicMemberService._require_manager` 成立，那条路确实查了，所以现在没有实际漏洞——但这个函数一旦被接上就直接开洞。建议：要么删，要么接上时同批去掉 fallback。
- `authorize_topic_access` 的 fallback 是真在跑的。收法：先给这条分支加计数日志，确认线上没有真实流量走它，再改成拒绝。**顺序依赖**：必须等阶段二（拆 `is_agent` 短路）之前先确认存量话题的分身座位全部迁移完，否则分身立刻失去访问权。

## 交接：做到 X / 卡在 Y / 下一步 Z

**做到 X** —— 阶段一全部完成并可验收：per-topic 分身身份（`cheese-<topic hex>`）、token 带 `a=` 声明、actor 落地、三处硬编码 handle 改成认 `looks_like_agent_handle`；外加本轮两个真 bug：@ 提及的空名单误判 + 跨话题 token 身份塌缩成 anonymous。

**卡在 Y** —— 阶段二（拆 `is_agent` 短路）**不是卡在工作量，是卡在一个前置事实**：老话题的分身座位靠 `migrate_shared_agent_seat()` 在「下一次开轮时」就地迁移。存量话题里有多少还没迁，现在没人知道。**这个数没查清之前拆短路 = 未迁移话题的分身当场失去访问权。**

**下一步 Z**，按急迫度：

1. **私聊鉴权口子**（最急，见上）：`get_or_create_private()` 补 seed 两人 roster；测试断言非参与方的项目成员进不去别人私聊。
2. 查存量话题的分身座位迁移率 → 够了就拆短路，不够就带开关灰度。这条出结论前不要动阶段二。
3. `_ensure_member` 拒绝非项目成员入 roster（落实结论 ①）。
4. `can_manage_roster` 死代码：删，或接上时同批去掉它的 fallback。
5. 阶段三收 `authorize_topic_access` 的 fallback：先加计数日志确认无真实流量，再改成拒绝。

## 约束

- 顺序不可颠倒：阶段一没铺开就拆短路 = 所有分身立刻失去话题访问权。
- 不碰 `.github/workflows/`、不碰 <&.claude/scripts/check.sh>（另一个 agent 在那儿）。
- 鉴权改动必须有功能测试（测真实行为）。
- 沙箱无 docker，测试按 <&CLAUDE.md>「Running tests in a sandbox」跑；`jj git fetch` 在沙箱里不可用（git 2.39.5 < 2.41）。
