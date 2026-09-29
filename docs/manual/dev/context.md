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

```demo-context
title: 芝士一轮里的上下文窗口
note: 从会话开场到窗口快满、骨架自动压缩。平台那十几块是 build_system_prompt 真跑出来的（字符数 ÷ 1.6），其余是代表数
topic: 登录页改版
window: 200000
compact_line: 0.9
data: prompt-blocks
takeaway: 你还没说话，窗口里已经有两万多 token：一半是骨架自带的，一半是平台拼的规则、项目状态和记忆。
room: 什么都还没有。这些都是开场时在幕后装进去的。
before:
  - label: 骨架自己的系统提示词和工具定义
    value: 12000
    phase: 开口之前
    desc: Claude Code 自己的那一份，加上内置工具和平台工具的定义。随骨架版本变，这里是代表数。平台拼的那十几块接在它后面（`--append-system-prompt-file`）。
steps:
  - label: 底稿
    check: 底稿
    desc: 部署时配置的那一段开头（`settings.agent_system_prompt`），后面每一块都接在它后面。
  - label: 起名
    check: 起名
    skip: true
    desc: 平台起不了名时才有这一块，让主 agent 第一轮先起名。平台能起名时由它在后台做，窗口里没有这一块。
  - label: 随时 push
    check: 随时 push
    desc: 「随时推送」的约定，所有 agent 都一样。
  - label: todo 清单
    check: 步骤清单
    desc: 步骤清单（`todo_write`）的用法：多步的活开工时先写计划。
  - label: 提问
    check: 向人提问
    desc: 要人回答或拍板时只用 `cheese_ask`；自带的提问工具问出去的话只落在现场，房间里没人看得到。
  - label: 写作
    check: 写作
    desc: 写作规则：先写结论、写短、不写推理过程。聊天、文档、记忆、PR 说明都按它写。
  - label: 专家角色
    check: 角色
    desc: 这个 AI 队友的专家角色。项目没给它角色时，这一段就不在。
  - label: 教学范围
    check: 教学范围
    skip: true
    desc: 课程项目才有：本周进度和这周还不该用的知识点。
  - label: 协作技能
    check: collaborator
    desc: 这个场景的操作技能，`skill_library/` 里按场景标签选出的文件全部拼上。房间里怎么说话、什么时候说，都写在这里，是平台拼的最大的一块之一。
    tip: 技能按场景选：巡检只拿三份，文档站的问芝士一份都不拿。
  - label: 阶段说明
    check: 阶段的操作说明
    desc: 当前阶段的操作说明，`stages.py` 按话题所处阶段静态拼进来，模型没有「要不要读」的选择权。
  - label: 话题列表
    check: 项目话题
    cat: state
    desc: 项目里活跃的话题，用来交叉引用。
  - label: 产物清单
    check: 产物清单
    cat: state
    desc: 这个项目交出去的东西，一项一行。
  - label: 成员名册
    check: 成员
    cat: state
    desc: 项目成员和怎么点名。
  - label: 项目总览
    check: 项目总览
    cat: state
    desc: 项目总览的实况文档。
    tip: 限 6000 字（`OVERVIEW_DOC_CHAR_BUDGET`），超了压缩并提示用 `cheese_doc_get` 读全文。
  - label: 实况文档
    check: 实况文档
    cat: state
    desc: 当前话题的实况文档。
    tip: 同样限 6000 字（`TOPIC_DOC_CHAR_BUDGET`）。
  - label: 记忆说明
    check: 记忆（memory）
    cat: memory
    desc: 记忆怎么写、写什么、写在哪。只对会把记忆对账回平台的骨架注入。
    link: /dev/memory#layers
  - label: 记忆索引
    check: 你的记忆
    cat: memory
    desc: 只有索引：`team/MEMORY.md` 加本轮说话那个人的 `private/<handle>/MEMORY.md`。正文在会话目录的文件里，芝士要用时自己读。
    tip: 索引 200 行 / 25KB 是注入预算，超了照样写，注入时截断并带一句警告。
    link: /dev/memory#scopes
  - label: 运行环境
    check: 运行环境
    desc: 会话开场时的机器和限制，标明是平台元信息，不是谁说的话。
then:
  - label: 「错误提示改成红色，手机上也看一下」
    kind: you
    cat: you
    seen: chat
    value: 60
    phase: 有人说话
    who: 王长鑫
    gate: @芝士 登录页的错误提示改成红色，手机上也看一下
    desc: 用户消息里只放待读的消息，每条前面带说话人：`[wangchangxin]: 正文`（`prompt_line`）。回复某条时附上被回复的原文。
    takeaway: 你这句话才几十个 token，和已经在窗口里的相比很小。芝士知道的大部分是项目本身，不是你这句话。
    room: 你刚发的这条消息。
    link: /dev/context#turn-prompt
  - label: 读 SignIn.vue
    kind: cheese
    cat: work
    seen: site
    value: 2400
    phase: 芝士干活
    desc: 文件内容整份进窗口。外部内容（文件、网页、别人写的文档）只以工具结果的身份出现，不混进人的消息。
    takeaway: 每读一个文件，窗口就长一截。
    room: 对话里还没动静；「现场」里一行行出现「读取文件」「搜索」。
    link: /dev/context#injection
  - label: 读 useFormErrors.ts
    kind: cheese
    cat: work
    seen: site
    value: 1100
    desc: 同上。
  - label: 搜索 "error-text"
    kind: cheese
    cat: work
    seen: site
    value: 600
    desc: 搜索结果进窗口，现场里只记一行。
  - label: 改 SignIn.vue
    kind: cheese
    cat: work
    seen: site
    value: 400
    desc: Edit 的参数和结果都进窗口。
  - label: 「按钮也换成主色」
    kind: you
    cat: you
    seen: chat
    value: 40
    who: 王长鑫
    gate: @芝士 按钮也换成主色
    desc: 这位队友已经在跑一轮，新消息并进正在跑的会话（`merge_into_running_turn`），下一步之前读到，不另开一轮。
    takeaway: 插话并进正在跑的这一轮，之前读过的都还在窗口里。
    room: 你的第二条消息。芝士没有重新开始。
    link: /dev/turn#serialize
  - label: 跑测试
    kind: cheese
    cat: work
    seen: site
    value: 1800
    desc: 命令输出整段进窗口，是干活时最占地方的一类。
    tip: 只看失败的那几条（`-x`、`| tail`）能省下一大截。
  - label: 沉默提醒
    kind: platform
    cat: you
    seen: none
    value: 90
    desc: 一轮太久没发言，平台投一条内部提醒，以「【平台】以下是平台自动发出的指令」开头、作者记为 system，不冒充任何人。
    takeaway: 平台的话也进窗口，但带着固定前缀，芝士分得清这不是人说的。
  - label: chat_send：「错误提示和按钮都改好了」
    kind: cheese
    cat: say
    seen: chat
    value: 300
    desc: 普通输出不进房间。要让人看见，必须调 `chat_send`。
    takeaway: 芝士的话只有经过 `chat_send` 才进对话。
    room: 芝士的一条回复，附两张截图。
    link: /dev/turn#publish
  - label: 「查查登录超时为什么总是 30 分钟，用分身去翻日志」
    kind: you
    cat: you
    seen: chat
    value: 70
    phase: 让分身去读
    who: 李甘
    gate: @芝士 查查登录超时为什么总是 30 分钟，用分身去翻日志
    desc: 另一个人的消息，同样带着说话人标签进来。
  - label: 派一个分身
    kind: cheese
    cat: work
    seen: site
    value: 80
    desc: 大块的阅读交给分身，读进来的东西留在分身自己的窗口里。
  - label: 分身的系统提示词
    kind: sub
    seen: none
    value: 9000
    desc: 分身有自己的一整份开场。
  - label: 读 32 个日志文件
    kind: sub
    seen: none
    value: 38000
    desc: 这些 token 在分身的窗口里，主窗口的横条不长。
  - label: 读 session 配置
    kind: sub
    seen: none
    value: 6000
    desc: 同上。
  - label: 分身带回的总结
    kind: cheese
    cat: work
    seen: site
    value: 420
    desc: 回到主窗口的只有这一段。
    takeaway: 分身读的五万多 token 留在它自己的窗口里，这里只多了几百。
    room: 现场里一行「派分身」，然后是它的结果；看不见它读了哪些日志。
    tip: 要大面积翻代码、翻日志时交给分身，主窗口能撑得更久。
  - label: 接下来三个小时：又读了 40 个文件、跑了 15 次测试、来回 20 轮
    kind: cheese
    cat: work
    seen: site
    value: 150000
    big: true
    phase: 很多轮以后
    desc: 一个话题可以连着干很久，会话续跑的是同一个，窗口一直在长。
    takeaway: 窗口快到上限了。房间里没有手动压缩的按钮，什么时候压缩由骨架自己定。
    room: 一长串对话和现场记录。
  - label: 自动压缩
    kind: compact
    value: 12000
    keeps: harness, rules, state, memory
    gate: 房间里没人能手动压缩，骨架自己把之前的对话压成一段摘要。
    desc: 骨架（这里是 Claude Code）自己把之前的对话压成一段结构化摘要，平台不参与。摘要留下要求、改过的文件、出过的错和还没做完的事；工具输出的原文和中间过程都没了。
    after: 留下来的：骨架自带的一份和平台拼的系统提示词（它们不在对话历史里，压缩不动它们），加上一段对话摘要和最近改过的文件。系统提示词是会话启动时读的那一份，实况文档和记忆索引要等下一次冷启动才换成新的。
    takeaway: 压缩把对话换成一段摘要。系统提示词不在对话历史里，原样留着。
    room: 什么都不会发生。压缩只在芝士的窗口里，房间里没有任何提示。
    link: /dev/turn#harness
  - label: 重读最近改过的文件
    kind: cheese
    cat: work
    seen: none
    value: 3000
    desc: Claude Code 压缩后会重读最近改过的几个文件，让接下来的活接得上。
```

上面这份是真实的装配顺序：每一步就是 `build_system_prompt` 输出里的一块，按行首的 `## ` 切开，数字也是从同一份输出算出来的，只折算了单位。每一块的原文、出现条件和预算见[系统提示词参考](/dev/ref-prompt)。

这些块按读法归成这几组：

1. 话题还没有名字、且平台自己起不了名（部署没配模型网关）时，第一条是「先给话题起名」。平台能起名时由它起，见[话题命名](/dev/turn#naming)。
2. 「随时推送」的约定。
3. 步骤清单（`todo_write`）的用法：多步的活开工时先写计划。
4. 向人提问只用 `cheese_ask`：自带的提问工具（例如 Codex 的 `request_user_input_async`）问出去的话只落在现场，房间里没人看得到。
5. 写作规则：先写结论、不反驳没人说过的话、不写推理过程和修辞、写短。聊天、文档、记忆、PR 说明都按它写。
6. 这个 AI 队友的专家角色。
7. 课程的教学配置：本周进度、这周还不该用的知识点（只在课程项目里有）。
8. 这个场景的操作技能（`skill_library/` 里按 `scenarios:` 标签选出，同一标签的文件全部拼上）。
9. **当前阶段的操作说明**：由 `stages.py` 按话题所处阶段（任务执行、闸门、等采纳、合并冲突、已归档）把那一阶段的说明静态拼进去，模型没有「要不要读」的选择权。
10. 项目里活跃的话题列表、产物清单、成员名册与点名方式。
11. 项目总览的实况文档和当前话题的实况文档，各限 6000 字（`OVERVIEW_DOC_CHAR_BUDGET`、`TOPIC_DOC_CHAR_BUDGET`），超了就压缩并提示用 `cheese_doc_get` 读全文。
12. **记忆的 L1 索引**：`team/MEMORY.md` 加本轮说话那个人的 `private/<handle>/MEMORY.md`（`memory_index`）。正文在会话目录的文件里，模型自己去读（见[记忆](/dev/memory#layers)）。
13. 会话开场时的运行环境（机器、限制），标明是平台元信息而不是用户输入。

Claude Code 在会话启动时读一次系统提示词（`--append-system-prompt-file`），所以系统提示词的改动在**下一次冷启动**时生效；正在跑的会话保持它启动时的那份。

## 逐轮消息怎么标注 {#turn-prompt}

每一轮的用户消息里只放待读的消息，每条前面带说话人（`prompt_line`）：`[handle]: 正文`。回复某条消息时附上被回复的原文。图片按骨架能力说明是「已附在消息里」还是「请去读这个文件」，不会谎称附上了。

平台自己发出的指令统一以 `【平台】以下是平台自动发出的指令，不是任何人手打的话：` 开头（`PLATFORM_NOTICE`），作者记为 `system`，不会冒充某个人说话。

## 防注入的几道边界 {#injection}

提示词本身挡不住所有注入，所以真正的边界放在权限上，提示词负责让模型分得清：

- **分得清谁在说话**：人话带说话人标签，平台指令带固定前缀，外部内容（网页、文件、其他成员写的文档）以工具结果的形式出现，而不是混进用户消息。
- **能做的事有上限**：芝士拿到的会话令牌只对这个项目和话题有效，平台工具按同一套权限判定，见[席位与权限判定](/dev/seats)。注入最多能让它在这个话题里做它本来就能做的事。
- **密钥不在手里**：上游模型 key、GitHub App 私钥都不下发到会话，见[登录与令牌](/dev/auth)。
- **读网页走平台通道**：`cheese_fetch` 由平台去抓，出口不在工作电脑上。
- **越权要审批**：换更贵的模型等动作要经过项目策略的审批闸门（`gate.Call`），不是模型说了算。

## 不同场景 {#scenes}

| 场景 | 上下文有什么不同 |
|---|---|
| 普通项目的话题 | 上面全部；阶段说明随话题推进变化 |
| 知是自建项目（仓库就是知是本身） | 另外还读仓库自己的 `CLAUDE.md` 和 `.claude/rules/`。仓库的规矩只写这个项目本身的事；对所有托管仓库都成立的平台知识写在平台技能 `backend/sandbox/skills/cheese/SKILL.md` 里，托管仓库不需要为平台做任何改动 |
| 课程项目 | 多一段教学配置（本周进度、不该用的知识点），只走系统提示词，会话中途不变 |
| 私聊（DM） | 在房间那份技能之上追加 `private_chat.md`（只补私聊独有的几条，发布方式不变）：查项目、记偏好、起草文档，要仓库或团队协作时建议转到工作话题 |
| 巡检 | 技能换成 `heartbeat`、`chat`、`chat-detail` 三份，按通知规则决定要不要发言；这一轮没有实况文档、成员名册、记忆和阶段说明 |
| 文档站的问芝士 | 不是 agent 会话：没有项目上下文、没有房间，只有三个只读的公开文档工具（搜、读、列页），回答只来自搜到和读到的内容，见[问芝士](/dev/docs-site#ask) |
