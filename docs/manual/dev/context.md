---
title: 提示词注入与上下文管理
kind: 概念
summary: 芝士的会话开场时拿到哪些上下文、按什么顺序、各占多少预算，会话接着跑时怎么补上变化，平台怎么区分人话、平台指令和外部内容，以及不同场景下有什么不同。
covers:
  - backend/app/domain/agent/harness/prompt.py
  - backend/app/domain/agent/skills.py
  - backend/app/domain/agent/skill_library/
  - backend/sandbox/skills/cheese/SKILL.md
---

# 提示词注入与上下文管理 {#context}

芝士的会话开场时拿到哪些上下文、按什么顺序、各占多少预算，会话接着跑时怎么补上变化，平台怎么区分人话、平台指令和外部内容，以及不同场景下有什么不同。

> 讲：系统提示词和开场快照的组成、逐轮消息的格式、防注入的几道边界。不讲：一轮怎么调度，见[一条消息怎么变成芝士的一轮](/dev/turn)。每一块的原文、出现条件和预算见[系统提示词参考](/dev/ref-prompt)。

## 系统提示词由什么组成 {#system}

一个新会话开场时，芝士读到两样东西（都在 `backend/app/domain/agent/harness/prompt.py`）：

- **系统提示词**（`build_system_prompt`）：只有规矩。骨架在进程启动时读它，进程空闲退出后用 `--resume` 接着原来的对话重新拉起时再读一次，所以它在一个会话里必须一字不变：变了，从变的那个字往后、连同整段对话历史，前缀缓存全部作废。
- **开场快照**（`build_session_opening`）：项目现在的样子，放在新会话第一条消息的前面。它是对话历史的一部分，会跟着历史一起被缓存。

按下面的顺序：

```demo-context
title: 芝士一轮里的上下文窗口
note: 从会话开场到窗口快满、骨架自动压缩。平台那二十块是 build_system_prompt 和 build_session_opening 真跑出来的（字符数 ÷ 1.6），其余是代表数
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
    desc: Claude Code 自己的那一份，加上内置工具和平台工具的定义。随骨架版本变，这里是代表数。平台的系统提示词接在它后面（`--append-system-prompt-file`），开场快照在第一条消息里。
steps:
  - label: 底稿
    check: 底稿
    desc: 部署时配置的那一段开头（`settings.agent_system_prompt`），后面每一块都接在它后面。
  - label: 平台规矩
    check: 平台规矩
    desc: 每个托管仓库、每一轮都成立的几条：房间里的人是产品用户，回复里不提内部机制，不用 `git stash`，改仓库前先开任务，以及怎么加载一个技能。
  - label: 随时 push
    check: 随时 push
    desc: 「随时推送」的约定，所有 agent 都一样。
  - label: todo 清单
    check: 步骤清单
    desc: 步骤清单（`todo_write`）的用法：多步的活开工时先写计划。
  - label: 提问
    check: 向人提问
    desc: 要人回答或拍板时只用 `cheese_ask`，问完就结束这一轮，回复会开启下一轮；自带的提问工具问出去的话只落在现场，房间里没人看得到。
  - label: 写作
    check: 写作
    desc: 写作规则：先写结论、写短、不写推理过程。聊天、文档、记忆、PR 说明都按它写。
  - label: 文档指引
    check: 写给人读的文档
    desc: 写方案、报告这类给人读的文档之前，先加载 `cheese-docs` 技能。
  - label: 聊天
    check: 在房间里说话
    desc: 房间里怎么说话：什么时候发消息、怎么写、怎么用 `chat_send` 发。私聊在它后面再补几条。
  - label: 实况文档写法
    check: 当前话题的实况文档
    desc: 实况文档写哪五块、怎么改、什么时候加载 `cheese-docs`。只有有文档位的房间才有；文档的内容在开场快照里。
  - label: 记忆说明
    check: 记忆（memory）
    cat: memory
    desc: 记忆怎么写、写什么、写在哪。只对会把记忆对账回平台的骨架注入。
    link: /dev/memory#layers
  - label: 项目技能
    check: 项目技能
    desc: 什么时候提议把一套做法存成项目技能、用的时候怎么提议修改。和记忆说明讲的是同一个时刻，每个骨架都有。
    link: /dev/skills#proposals
  - label: 专家角色
    check: 角色
    desc: 这个 AI 队友的专家角色。项目没给它角色时，这一段就不在。系统提示词到这里为止。
  - label: 开场快照
    check: 本话题现在的情况
    cat: state
    desc: 从这里起是新会话第一条消息前面的开场快照，以平台的口吻说明下面是会话开始时的现状。
  - label: 教学范围
    check: 教学范围
    cat: state
    skip: true
    desc: 课程项目才有：本周进度和这周还不该用的知识点。一个会话保持开场那一份，直到下一次新会话。
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
    check: 实况文档现在的内容
    cat: state
    desc: 当前话题的实况文档。
    tip: 同样限 6000 字（`TOPIC_DOC_CHAR_BUDGET`）。
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
    after: 留下来的：骨架自带的一份和平台的系统提示词（它们不在对话历史里，压缩不动它们），加上一段对话摘要和最近改过的文件。开场快照在对话历史里，压缩后只剩摘要里的几句；之后变了的部分平台会在后面的消息里再说。
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

上面这份是真实的装配顺序：每一步就是生成器输出里的一块，按行首的 `## ` 切开，数字也是从同一份输出算出来的，只折算了单位。每一块的原文、出现条件和预算见[系统提示词参考](/dev/ref-prompt)。

**系统提示词**按读法归成这几组：

1. 平台规矩：每个托管仓库、每一轮都成立的几条，以及怎么加载技能（Claude Code 用 Skill 工具，Codex 和 pi 读技能列表里给出的文件）。
2. 「随时推送」的约定；步骤清单（`todo_write`）的用法；向人提问只用 `cheese_ask`，问完就结束这一轮，有人回复时那句回复开启下一轮；自带的提问工具（例如 Codex 的 `request_user_input_async`）问出去的话只落在现场，房间里没人看得到。
3. 写作规则：先写结论、不反驳没人说过的话、不写推理过程和修辞、写短。聊天、文档、记忆、PR 说明都按它写。
4. 写给人读的文档先加载 `cheese-docs` 技能；房间里怎么说话（`skill_library/chat.md` 的全文，私聊再补 `private_chat.md`）。
5. 实况文档写哪五块、怎么改（`skill_library/doc_form.md`），只在有文档位的房间里有。
6. 记忆说明，只给会把记忆对账回平台的骨架；紧跟着是什么时候提议存一项项目技能，每个骨架都有（见[技能](/dev/skills#proposals)）。
7. 这个 AI 队友的专家角色。

几乎每轮都用得上的规矩才放进系统提示词；只有某类任务才用的（写长文档、交付流程、邮件、定时）做成技能，用到才读，见[技能](/dev/skills)。

**开场快照**里是现状：课程的教学配置、活跃的话题、产物清单、成员名册、项目总览和本话题的实况文档（各限 6000 字，超了压缩并提示用 `cheese_doc_get` 读全文）、记忆的 L1 索引、会话开场时的运行环境。

**会话接着跑时怎么补上变化。** 每个会话记下它上次听到的话题、产物清单、成员、项目总览、记忆索引这五段各是什么样（`agent_sessions.told`，每段一个摘要）。下一轮拼好现状后逐段比，变了的那几段以平台提醒放在这一轮消息的前面。没有记录（老会话、记录丢了）时整份都说一次。实况文档不在这五段里，它被人改过时平台另发一条「请重读」的提醒；教学配置有意保持开场那一份；运行环境只在开场时有意义。

哪一次是新会话，只有 `ensure` 之后才知道：续跑可能失败、改开一条新的。所以两份都随每一轮送进 `RoomSessions.send`（`session_opening`、`opening_changes` 两个参数），由它看这条对话是不是刚开的再挑一份放进消息。

## 逐轮消息怎么标注 {#turn-prompt}

每一轮的用户消息里只放待读的消息，每条前面带说话人（`prompt_line`）：`[handle]: 正文`。回复某条消息时附上被回复的原文。图片按骨架能力说明是「已附在消息里」还是「请去读这个文件」，不会谎称附上了。

平台自己发出的指令统一以 `【平台】以下是平台自动发出的指令，不是任何人手打的话：` 开头（`PLATFORM_NOTICE`），作者记为 `system`，不会冒充某个人说话。

话题还没有名字、平台又起不了名时，这一轮消息的最前面是让芝士先起名的那一句（`UNTITLED_FIRST`）。它跟着每一轮的消息走，不进系统提示词：起完名就不再说。

## 防注入的几道边界 {#injection}

提示词本身挡不住所有注入，所以真正的边界放在权限上，提示词负责让模型分得清：

- **分得清谁在说话**：人话带说话人标签，平台指令带固定前缀，外部内容（网页、文件、其他成员写的文档）以工具结果的形式出现，而不是混进用户消息。
- **能做的事有上限**：芝士拿到的会话令牌只对这个项目和话题有效，平台工具按同一套权限判定，见[席位与权限判定](/dev/seats)。注入最多能让它在这个话题里做它本来就能做的事。
- **密钥不在手里**：上游模型 key、GitHub App 私钥都不下发到会话，见[登录与令牌](/dev/auth)。
- **读网页走平台通道**：`cheese_fetch` 由平台去抓，出口不在工作电脑上。平台只读公网地址：网址或途中任何一次跳转指向内网、本机或保留地址，这次读取整个作废，不交给第三方读取服务或浏览器接着试（`app/domain/fetch/guard.py`）。机器的 DNS 把所有域名都解析成假 IP 占位地址（`198.18.0.0/15`，透明代理的做法）时，平台改用 DNS over HTTPS（`FETCH_DNS_OVER_HTTPS`）查出真实地址，按真实地址判断并直接连接；查不到就拒绝。
- **越权要审批**：换更贵的模型等动作要经过项目策略的审批闸门（`gate.Call`），不是模型说了算。

## 不同场景 {#scenes}

| 场景 | 上下文有什么不同 |
|---|---|
| 普通项目的话题 | 上面全部 |
| 知是自建项目（仓库就是知是本身） | 另外还读仓库自己的 `CLAUDE.md` 和 `.claude/rules/`。仓库的规矩只写这个项目本身的事；对所有托管仓库都成立的平台知识写在平台技能 `backend/sandbox/skills/cheese/SKILL.md` 里，托管仓库不需要为平台做任何改动 |
| 课程项目 | 开场快照里多一段教学配置（本周进度、不该用的知识点），会话中途不变 |
| 私聊（DM） | 在房间那份聊天说明之上追加 `private_chat.md`（只补私聊独有的几条，发布方式不变）：查项目、记偏好、起草文档，要仓库或团队协作时建议转到工作话题 |
| 文档站的问芝士 | 不是 agent 会话：没有项目上下文、没有房间，只有三个只读的公开文档工具（搜、读、列页），回答只来自搜到和读到的内容，见[问芝士](/dev/docs-site#ask) |
