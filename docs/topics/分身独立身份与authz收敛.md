## 目标

让每个话题分身有**自己的 agent-user 身份**，把「谁干的」写进 token；然后（分阶段）把 authz 的 agent 短路拆掉。

- 阶段一（本轮必做）：per-topic agent-user + token 带身份 + 现场处处按身份而不是按写死的 `"cheese"` 字符串判断。
- 阶段二（本轮**不做**，只出评估）：拆 `policy.py:45` 的 `is_agent` 短路。
- 阶段三（视工作量）：authz 策略表、收掉两个宽松档。

## 现状核实（我逐条读过源码）

简报里三条证据属实，另外**有两个好消息**，让阶段一的改动面比预想小得多：

1. **架构早就为它留好了位置。** <&backend/app/domain/topic_membership/services.py> 已经有 `agent_handles(topic_id)` / `resolve_agent_handle(topic_id)`——「这个房间里的 agent 是谁」是从 `AgentBinding` 推导的，不是写死的 handle；<&backend/app/domain/agent/chat.py> 的 `_agent_handle()` 是唯一出口，9 处调用全走它。也就是说**发言署名这条路早就是「每个话题问一次自己是谁」**，只是答案现在恒等于 `cheese`。
2. <&backend/app/api/routes/topic_members.py> 的成员接口已经按 `AgentBinding` 返回 `agent` 标记 + profile 昵称，前端不需要认识 handle 就能画 Agent 徽章。

所以阶段一不需要新表、不需要迁移（避免和其他 agent 抢 alembic 链），只需要：给每个话题造一个 agent-user 行、把它写进 roster、让 token 带上它。

## 阶段一设计（已定，正在实现）

| 点 | 做法 |
|---|---|
| handle | `cheese-<topic_id.hex[:12]>`，**纯函数确定性推导**（`topic_agent_handle()`），不查库就能算 |
| user 行 | 复用现成的 `IdentityService.ensure_agent_user()`（幂等，自动建 `AgentBinding`），再补一条 nickname=「芝士」的 profile，**界面上仍显示「芝士」，只有身份分叉** |
| token | `mint_scoped_token` 在有 topic_id 时**自动**带上 `a=<agent handle>` 声明；7 处 mint 调用点一处都不用改 |
| 落地为 actor | <&backend/app/api/auth.py> 读 token 的 `a` 声明当 actor.handle，缺失时退回 `cheese`（老 token / 全局 dev token 不受影响） |
| roster | 建话题时 seed per-topic agent；老话题在下一次开轮时**就地迁移**（`cheese` 行换成 `cheese-<hex>`），所以不需要数据迁移脚本 |

### 顺手修掉的两个真 bug（不改就会被这次改动踩出来）

- <&backend/app/domain/review/services.py>:321 `handle == "cheese"` —— 「协作模式下 AI 不能验收自己的活」这条硬规则**认的是字符串**。分身一旦有了自己的 handle，这条规则直接被绕过。**这是安全性回归，必须同批修。**
- <&backend/app/domain/topic/services.py>:467 `"芝士" if author == "cheese"` —— 同理，否则文档编辑事件会显示成 `<@cheese-a1b2c3>`。
- 前端 <&frontend/src/components/TopicSidebar.vue>:92 用 `user_handle !== 'cheese'` 过滤 agent，改用接口已有的 `agent` 标记。

## 约束

- 顺序**不可颠倒**：阶段一没铺开就拆短路 = 所有分身立刻失去话题访问权。
- 不碰 `.github/workflows/`、不碰 <&.claude/scripts/check.sh>（另一个 agent 在那儿）。
- 鉴权改动必须有功能测试（测真实行为）。
- 沙箱无 docker，测试按 <&CLAUDE.md>「Running tests in a sandbox」跑。

## 进展

- [x] 摸清现状、定阶段一设计
- [ ] 实现 per-topic agent 身份 + token 声明 + actor 落地
- [ ] 修 `_forbid_ai` / 显示名 / 前端过滤
- [ ] 测试（token 声明、actor 解析、roster 迁移、AI 不能自验收）
- [ ] `task be:check` 跑绿
- [ ] 阶段二风险评估 → 发决策请求，**不硬推**

## 已知风险 / 待拍板

1. **handle 前缀被抢注**：`cheese-` 前缀目前没有保留，理论上真人可注册同名。展示层用前缀判断的地方有这个理论风险（鉴权层不受影响，鉴权认 `AgentBinding`）。
2. **阶段二的真实爆炸半径**：`policy.py` 的短路一拆，分身要靠 roster 成员资格过检查。老话题的 roster 迁移是「下一次开轮时」发生的——意味着**存在一个窗口，老话题的分身既不是 `cheese` 也还没换身份**。阶段二上线前必须先确认存量话题全部迁移完，或者带开关灰度。这条会在阶段一做完后单独出结论。
