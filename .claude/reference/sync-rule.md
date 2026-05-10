---
name: sync-rule
description: CLAUDE.md 和 .claude/ 同步规则 — 每步操作前必读
---

# 同步规则（最高优先级）

## 铁律

**CLAUDE.md 和 .claude/ 是同级的项目规范。改一个必须同步另一个。**

**所有 commit 必须通过 PR。禁止直接提交到 main 分支。**

具体来说，以下任何一项变更都要同步：

| 改了什么 | 同步到哪里 |
|----------|-----------|
| 新增/修改 `.claude/skills/` | CLAUDE.md 的目录结构 + 说明 |
| 新增/修改 `.claude/agents/` | CLAUDE.md 的目录结构 + 说明 |
| 新增/修改 `.claude/scripts/` | CLAUDE.md 的目录结构 + 说明 |
| 新增/修改 `.claude/reference/` | CLAUDE.md 的目录结构 + 说明 |
| 修改 CLAUDE.md 中的规范 | 对应 skill 的 checklist |
| 修改 skill 中的检查项 | CLAUDE.md 对应规范段落 |

## 检查方法

每次修改完，自问：
1. CLAUDE.md 的 `.claude/ Directory Structure` 图是否包含了所有子目录？
2. 新增的文件是否在目录图中列出？
3. 规范变更是否两边一致？
