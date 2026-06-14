# CheeseX (Cheese 2.0)

AI 全过程学生项目平台。产品 spec 在 `docs/spec.md`，必读。

## 技术栈

- **后端**: Python, FastAPI, SQLAlchemy 2.0, PostgreSQL
- **前端**: Vue 3, TypeScript
- **AI 集成**: Python Agent SDK (`claude-agent-sdk`)，通过 LiteLLM 网关支持多模型
- **记忆**: OpenViking
- **实时通信**: WebSocket (SSE 备选)
- **所有内容底层**: git repo (每个 Project = 一个 repo)

## 架构

```
backend/
  app/
    api/routes/         # FastAPI 路由
    domain/
      topic/            # 话题 (= session = PR)
      project/          # 项目 (= 根话题 = git repo)  
      block/            # 块 (万物皆块：消息、文档节点、附件)
      agent/            # AI 集成 (Agent SDK 封装)
      memory/           # OpenViking 记忆管理
      space/            # Space (机构)
      task/             # Task Template + Task
    core/
      config.py
      errors.py
      db.py
frontend/
  src/
    views/
    components/
docs/
  spec.md               # 产品 spec (必读)
  evals.md              # 验收场景
```

## 核心概念映射 (spec → code)

| 产品概念 | 代码实体 | 说明 |
|---|---|---|
| 话题 | Topic | = 一个 session = 一个 git branch。有对话、文档、状态 |
| 子话题 | Topic (parent_id) | 芝士的**分身**在这里工作。异步，结论回流父话题 |
| 块 | Block | 万物皆块。有 reply_to (对话树) + struct_parent (文档树) + refs[] |
| 项目 | Project | = 根话题 = 一个 git repo |
| 芝士本体 | 根话题的 agent session | 协调全局 |
| 芝士分身 | 子话题的 agent session | 专注做一件事 |

## MVP 阶段 (当前: Phase 0)

Phase 0 目标: 一个话题里能和芝士对话，芝士带记忆回答问题。

优先实现:
1. Agent SDK 接入 — 能和 AI 对话
2. Topic + Block 模型 — 对话存储
3. WebSocket 流式响应 — 实时显示 AI 回复
4. 基础前端 — 话题列表 + 聊天窗口

## 开发规范

- Python >=3.11，类型标注，Pydantic v2
- async/await 全程，AsyncSession
- `uv` 管理依赖，`uv add` 不用 `pip install`
- ruff + pyright 零错误
- 测试先行: 先写测试定义行为，再实现
- Commit messages in English
- 中文沟通

## 关键设计决策 (来自 spec 讨论)

1. **文档是核心界面** — 不是聊天。对话是过程，文档是状态 (spec §2.2)
2. **本体/分身模型** — 根话题 = 本体，子话题 = 分身。分身之间不感知，靠文档保持一致 (spec §8.4)
3. **所有产出都是 git** — 代码、报告、设计稿在实现层面都是文件，采纳 = merge (spec §6.3)
4. **平台通过工具调用感知 AI** — 不靠 regex 解析输出 (spec §9.1)
5. **不绑定模型** — Agent SDK + LiteLLM 网关，按场景选模型 (spec §9)
6. **Space/Task/Project 解耦** — Task Template 是协议，Project 自治 (spec §4)
