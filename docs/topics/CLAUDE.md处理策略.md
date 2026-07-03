# CLAUDE.md 处理策略

## 目标

回答：平台是否用 CLAUDE.md？用户 repo 自带一份会怎样？

## 现状结论（已核实代码）

CLAUDE.md 在我们这儿出现在**三个不同层面**，只有第一个真正"在用"：

1. **开发侧（在用）**：我们自己的 repo 根目录有一份 `CLAUDE.md`，给开发者本地用 Claude Code 开发平台时加载——写了技术栈、架构映射和"不解析自然语言"红线。这和平台运行时无关。
2. **运行时（不加载）**：芝士的会话通过 Agent SDK 启动，`setting_sources` 只开了 `["user"]`（沙箱模式，user 目录是容器内隔离的会话目录，只挂了 cheese skill）或 `[]`（非沙箱）——**都不含 `"project"`，所以项目目录里的 CLAUDE.md 不会被自动加载**（见 `backend/app/domain/agent/service.py` 的 ClaudeAgentOptions）。芝士的人格和规则全部走我们自己拼的 system prompt（基础人格 + skills + 记忆注入）。
3. **spec 里的规划（与实现有偏差）**：`docs/spec.md` §9 的映射表写"人格底线/不变规则 → CLAUDE.md（每次都加载）"，但实际实现选择了 system prompt 注入，没走 CLAUDE.md。

## 用户 repo 自带 CLAUDE.md 会怎样

- **不会被自动吃进芝士的上下文**——它只是工作区里的一个普通文件。
- 这是件好事：外来 repo 的 CLAUDE.md 可能带任意指令（相当于 prompt injection 入口），自动加载会让它劫持/覆盖芝士的行为。现在的隔离设计天然挡住了这条路。
- 芝士需要了解该 repo 的规范时，可以**主动读**它当参考资料，和读 README 一样，不具备指令效力。

## 已核实：CLI 的 system prompt 机制

- Claude CLI（2.1.197）有 `--system-prompt`（整体替换默认 system prompt）和 `--append-system-prompt`（追加）；`--setting-sources user,project,local` 控制加载哪些设置（repo 的 CLAUDE.md 属于 project 源）。
- 我们后端走 SDK 的 `system_prompt` 参数 = `--system-prompt` 替换式。**人格底线进 system prompt 已是现状**，spec §9 "放 CLAUDE.md"那行过时，待更新。

## 决策（张衡已拍板，2026-07-02）

- 人格底线：维持 **system prompt 注入**（= CLI `--system-prompt` 替换式），spec §9 已更新。
- 用户 repo 的 CLAUDE.md：**方案 A 原生自动加载**——`setting_sources` 开 `project` 源，Claude Code 自动读工作区的 CLAUDE.md。
  - 备选 B（平台显式注入）、C（按需读取）不采用。

## 已落地

- `backend/app/domain/agent/service.py`：沙箱会话 `setting_sources` 由 `["user"]` 改为 `["user", "project"]`（非沙箱纯聊天模式保持隔离不变）。
- `docs/spec.md` §9 映射表：人格底线一行改为 system prompt；新增"用户 repo 的 CLAUDE.md → project 源原生加载"一行。

## 待验证

- 实测：替换式 system prompt + project 源组合下，repo 的 CLAUDE.md 是否真的进上下文（起个带 CLAUDE.md 的话题验证一遍）。
- 留意：repo 的 `.claude/settings.json` 也会随 project 源加载，观察是否有副作用。

## 相关子话题

- @子话题自动起标题 ：拆子话题不该手动起标题，对齐"起标题是会话首步"的新逻辑。
- @修复决策卡不显示 ：决策请求卡在现场没渲染出来的 bug。
- @分身开工带简报 ：**已被本话题的直接修复取代**（张衡选了"直接在这修"），该子话题可以关掉。
- @子话题自动起标题 、@修复决策卡不显示 ：仍在等人踢一脚（是在本修复**之前**拆的，不会自动开工）——进话题发条消息即可，或干脆重新拆一次让它们带简报自动开工。

## 分身机制修复（方案 2，已落地本话题分支，待验收）

问题：拆出的子话题分身"不知道要干啥"且"根本不开工"。修复三件套：

1. **拆分带简报**：`cheese split` 新增 `--brief`；简报（拆分意图 + 父话题活文档快照 + 来源）自动预置成子话题的活文档——分身睁眼就有任务书。人从界面拆、没写简报时也会预置来源和父文档快照。
2. **模板开场白删了**：原来平台替芝士说"我先确认理解，再开始推进"（其实它啥也不知道，还踩了"语义内容必须 AI 生成"的红线）。现在子话题里第一条可见消息是分身自己写的开场白。
3. **拆分即开工**：split 完成后自动触发分身首轮（不落假消息，纯内部指令）：复述任务确认理解 → 把简报改写成自己的状态摘要 → 能干就开干、缺信息就 @ 拆分人。

改动文件：`topic/services.py`（预置简报文档）、`topic/schemas.py` + `api/routes/topics.py`（brief 参数 + 拆分后提交 kickoff 轮）、`agent/chat.py`（kickoff 首轮，无假消息）、`agent/runtime.py`（submit_kickoff）、`sandbox/cheese` + `SKILL.md`（--brief）。测试：新增 3 个用例（简报落文档 / 无简报兜底 / 自动开工且无模板开场白），全量 216 过 ×5 次，ruff/pyright 零错误。

遗留（不在本次范围）：
- 讨论升级（upgrade）出来的话题仍是模板开场白、不自动开工——和 split 同病，待一并治。
- market 有一个测试因沙箱没配网关凭证而失败，与本改动无关。

## 与 Claude Code subagent 逻辑的对照（张衡问，已核对本地 Agent SDK 定义）

Claude Code 的 subagent：父写任务 prompt → Task 工具当场拉起子 agent（无待机态）→ 子 agent 全新上下文只靠 prompt → 干完最后一条消息**自动**作为工具结果返回父 → 父被唤醒继续推理；每类 subagent 可定义人格/工具/模型（AgentDefinition：description/prompt/tools/model/skills/maxTurns/background…）。

对照结论：
- ✅ 去程已对齐（本次修复）：brief=任务 prompt、拆分即开工、全新上下文靠预置文档携带信息。
- ⚠️ 回程半对齐：结论回流靠分身自觉 conclude，不是结构性保证。
- ❌ 回程缺一块：conclude 只落消息+通知，**父话题不会被自动唤醒**消化结论（Claude Code 里父 agent 拿到工具结果会继续干）。建议：return-conclusion 端点复用 kickoff 机制，自动触发父话题一轮。待张衡拍板是否本分支顺手补。
- 我们强于它的：分身持久可插话、过程对人透明、结论织进父文档；它是黑盒一次性。
