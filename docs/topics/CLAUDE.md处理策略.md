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

## 待定 / 下一步

- spec §9 那行"人格底线 → CLAUDE.md"与实现不一致：建议明确"人格底线走 system prompt，不用 CLAUDE.md（不绑定模型 + 防外来 repo 覆盖）"，并更新 spec。等 @张衡 / @Andy 确认后记决策。
