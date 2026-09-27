---
title: 提示词注入与上下文管理
kind: 概念
summary: 芝士每一轮拿到哪些上下文、按什么顺序、各占多少预算，平台怎么区分人话、平台指令和外部内容，以及不同场景下有什么不同。
covers:
  - backend/app/domain/agent/harness/prompt.py
  - backend/app/domain/agent/skills.py
  - backend/app/domain/agent/stages.py
  - backend/app/domain/agent/skill_library/
  - backend/sandbox/skills/cheese/SKILL.md
---

# 提示词注入与上下文管理 {#context}

芝士每一轮拿到哪些上下文、按什么顺序、各占多少预算，平台怎么区分人话、平台指令和外部内容，以及不同场景下有什么不同。

> 讲：系统提示词的组成、逐轮消息的格式、防注入的几道边界。不讲：一轮怎么调度，见[一条消息怎么变成芝士的一轮](/dev/turn)。每一块的原文、出现条件和预算见[系统提示词参考](/dev/ref-prompt)。

## 系统提示词由什么组成 {#system}

`build_system_prompt`（`backend/app/domain/agent/harness/prompt.py`）每一轮都从数据库重新组装，按下面的顺序：

```demo-timeline
title: 芝士的一轮上下文
data: prompt-blocks
unit: tokens
estimate: true
note: 每一步的数字是估算：那块提示词的字符数 ÷ 1.6。柱子只数启动阶段装进去的这些块
steps:
  - label: 底稿
    check: 底稿
    desc: 部署时配置的那一段开头（settings.agent_system_prompt），后面每一块都接在它后面。
  - label: 起名
    check: 起名
    desc: 话题还没有名字、平台又自己起不了名时，第一条是「先给本话题起名」。平台能起名时由它起。
  - label: 随时 push
    check: 随时 push
    desc: 「随时推送」的约定，所有 agent 都一样，主 agent 也不例外。
  - label: todo 清单
    check: 步骤清单
    desc: 步骤清单（todo_write）的用法：多步的活开工时先写计划。
  - label: 专家角色
    check: 角色
    desc: 这个 AI 队友的专家角色。项目没给它角色时，这一段就不在。
  - label: 教学范围
    check: 教学范围
    desc: 课程项目的本周进度和这周还不该用的知识点。只有课程项目有这一段。
  - label: 技能
    check: collaborator
    desc: 这个场景的操作技能：skill_library/ 里按 scenarios: 标签选出的文件，同一标签的全部拼上。
  - label: 阶段说明
    check: 阶段的操作说明
    desc: 当前阶段的操作说明，由 stages.py 按话题所处阶段静态拼进来，模型没有「要不要读」的选择权。
  - label: 话题列表
    check: 项目话题
    desc: 项目里活跃的话题，用来交叉引用（标题前加 @ 会渲染成可点的链接）。
  - label: 产物清单
    check: 产物清单
    desc: 这个项目的产物清单，交出去的东西一项一行。
  - label: 成员名册
    check: 成员
    desc: 项目成员和怎么点名。
  - label: 项目总览
    check: 项目总览
    desc: 项目总览的实况文档，限 6000 字（OVERVIEW_DOC_CHAR_BUDGET），超了压缩并提示用 cheese_doc_get 读全文。
  - label: 实况文档
    check: 实况文档
    desc: 当前话题的实况文档，同样限 6000 字（TOPIC_DOC_CHAR_BUDGET）。
  - label: 记忆
    check: 记忆（memory）
    desc: 记忆这一块：跑的是会把记忆文件对账回平台的骨架（keeps_memory），或者这一轮带了记忆时才在。记忆怎么用、超限了读得到什么，写在这里。
  - label: 记忆索引
    check: 你的记忆
    desc: L1 索引，team/MEMORY.md 加本轮说话那个人的 private/<handle>/MEMORY.md，正文在会话目录的文件里，模型自己去读。
  - label: 运行环境
    check: 运行环境
    desc: 会话开场时的运行环境（机器、限制），标明是平台元信息而不是用户输入。
then:
  - label: 人的消息
    desc: 到这一步，装配好的提示词和待读的消息一起交给模型。每一轮的用户消息里只放待读的消息，每条前面带说话人标签（prompt_line）。
    gate: true
    go: 人发了消息
  - label: 工具调用与结果
    desc: 模型读文件、跑命令、调平台工具，结果一条条回到上下文里。外部内容以工具结果的形式出现，而不是混进用户消息。
  - label: 压缩
    desc: 窗口快满时，骨架自己把前面的对话压成摘要腾出位置。这一步是骨架（这里就是 Claude Code）的事，平台不参与。
```

上面这份是真实的装配顺序：每一步就是 `build_system_prompt` 输出里的一块，按行首的 `## ` 切开，数字也是从同一份输出算出来的，只折算了单位。每一块的原文、出现条件和预算见[系统提示词参考](/dev/ref-prompt)。

这些块按读法归成这几组：

1. 话题还没有名字、且平台自己起不了名（部署没配模型网关）时，第一条是「先给话题起名」。平台能起名时由它起，见[话题命名](/dev/turn#naming)。
2. 「随时推送」的约定。
3. 步骤清单（`todo_write`）的用法：多步的活开工时先写计划。
4. 这个 AI 队友的专家角色。
5. 课程的教学配置：本周进度、这周还不该用的知识点（只在课程项目里有）。
6. 这个场景的操作技能（`skill_library/` 里按 `scenarios:` 标签选出，同一标签的文件全部拼上）。
7. **当前阶段的操作说明**：由 `stages.py` 按话题所处阶段（任务执行、闸门、等采纳、合并冲突、已归档）把那一阶段的说明静态拼进去，模型没有「要不要读」的选择权。
8. 项目里活跃的话题列表、产物清单、成员名册与点名方式。
9. 项目总览的实况文档和当前话题的实况文档，各限 6000 字（`OVERVIEW_DOC_CHAR_BUDGET`、`TOPIC_DOC_CHAR_BUDGET`），超了就压缩并提示用 `cheese_doc_get` 读全文。
10. **记忆的 L1 索引**：`team/MEMORY.md` 加本轮说话那个人的 `private/<handle>/MEMORY.md`（`memory_index`）。正文在会话目录的文件里，模型自己去读（见[记忆](/dev/memory#layers)）。
11. 会话开场时的运行环境（机器、限制），标明是平台元信息而不是用户输入。

Claude Code 在会话启动时读一次系统提示词（`--append-system-prompt-file`），所以系统提示词的改动在**下一次冷启动**时生效；正在跑的会话保持它启动时的那份。

## 逐轮消息怎么标注 {#turn-prompt}

每一轮的用户消息里只放待读的消息，每条前面带说话人（`prompt_line`）：`[handle]: 正文`。回复某条消息时附上被回复的原文。图片按骨架能力说明是「已附在消息里」还是「请去读这个文件」，不会谎称附上了。

平台自己发出的指令统一以 `【平台】以下是平台自动发出的指令，不是任何人手打的话：` 开头（`PLATFORM_NOTICE`），作者记为 `system`，不会冒充某个人说话。

## 防注入的几道边界 {#injection}

提示词本身挡不住所有注入，所以真正的边界放在权限上，提示词负责让模型分得清：

- **分得清谁在说话**：人话带说话人标签，平台指令带固定前缀，外部内容（网页、文件、其他成员写的文档）以工具结果的形式出现，而不是混进用户消息。
- **能做的事有上限**：芝士拿到的会话令牌只对这个项目和话题有效，平台工具按同一套权限判定，见[席位与权限判定](/dev/seats)。注入最多能让它在这个话题里做它本来就能做的事。
- **密钥不在手里**：上游模型 key、GitHub App 私钥都不下发到会话，见[登录与令牌](/dev/auth)。
- **读网页走平台通道**：`cheese_fetch` 由平台去抓，出口不在工作机器上。
- **越权要审批**：换更贵的模型等动作要经过项目策略的审批闸门（`gate.Call`），不是模型说了算。

## 不同场景 {#scenes}

| 场景 | 上下文有什么不同 |
|---|---|
| 普通项目的话题 | 上面全部；阶段说明随话题推进变化 |
| 知是自建项目（仓库就是知是本身） | 另外还读仓库自己的 `CLAUDE.md` 和 `.claude/rules/`。仓库的规矩只写这个项目本身的事；对所有托管仓库都成立的平台知识写在平台技能 `backend/sandbox/skills/cheese/SKILL.md` 里，托管仓库不需要为平台做任何改动 |
| 课程项目 | 多一段教学配置（本周进度、不该用的知识点），只走系统提示词，会话中途不变 |
| 私聊（DM） | 在房间那份技能之上追加 `private_chat.md`（只补私聊独有的几条，发布方式不变）：查项目、记偏好、起草文档，要仓库或团队协作时建议转到工作话题 |
| 巡检 | 技能换成 `heartbeat`、`chat`、`chat-detail` 三份，按通知规则决定要不要发言；这一轮没有实况文档、成员名册、记忆和阶段说明 |
| 文档站的问芝士 | 不是 agent 会话：没有工具、没有项目上下文，只拿检索到的文档段落，只回答文档里有的内容，见[问芝士](/dev/docs-site#ask) |
