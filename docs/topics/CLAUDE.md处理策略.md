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
- @分身开工带简报 ：分身开工不知道要干啥——拆分时只存标题、首轮不注入父话题上下文（根因在 split_to_subtopic + _build_system_prompt），修法是拆分时把任务简报预置为子话题活文档。与「子话题自动起标题」同一代码路径，需协调。
  - **追加发现（更严重）**：拆出的子话题**不会自动开工**——后端唯一触发 AI 的入口是"有人发消息"（converse），split 不踢首轮。所以拆完的分身全在空转，得有人进子话题发条消息才动。修复范围加一条：拆分后自动触发分身首轮。
  - 临时用法（机制修好前）：打开子话题、发一句"开工"，分身即启动；任务简报已在项目记忆里，它能看到。
