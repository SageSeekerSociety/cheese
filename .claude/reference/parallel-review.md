---
name: parallel-review
description: 审查流程 — 支持并行（Claude Code）和串行（其他 AI），输出简洁
---

# Review Workflow

无论用什么 AI 工具，审查流程的目标一致：代码质量 > 速度，但两者都要。

## 模式 A：并行（Claude Code 专用）

后台跑测试，前台同步读代码，互不阻塞。

```
Step 1: git diff / git status
         │
         ├──→ 后台启动 general-purpose agent（check-runner prompt）
         │    run_in_background: true, timeout: 5min
         │
         └──→ Step 2→3: 读改动文件、对照 reference/
              读完后若 agent 未完成，继续深入审查
                    ↓
              收到 agent 报告 → 汇总输出
```

## 模式 B：串行（所有 AI 通用）

没有后台能力的 AI（Copilot、Cursor、Windsurf 等）直接用脚本，输出足够短不会浪费 token：

```bash
bash .claude/scripts/check.sh
# 或
task check
```

脚本只输出 3 行结果：

```
==> ruff check
  PASS: ruff
==> pyright
  PASS: pyright
==> pytest
  PASS: pytest
Result: 3/3 passed
```

**审查流程：**
1. 先跑 `task check`（~30 秒）
2. 等结果的同时读 `git diff` 看改动范围
3. 收到结果后，只 review 改动文件 + 对照规范
4. 汇总输出

## 关键原则

- 检查脚本输出极简（~10 行），任何 AI 都能快速理解。
- 测试 FAIL 时，审查结论必须包含 "Tests failed — commit blocked"。
- 不要在测试跑完前下结论。
