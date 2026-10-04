---
title: 技能
kind: 参考
summary: 平台技能库、项目自定义技能、沙盒技能和分身定义各是什么、怎么装进会话。
covers:
  - backend/app/domain/agent/skills.py
  - backend/app/domain/agent/skill_library/
  - backend/app/domain/project_skill/
  - backend/sandbox/skills/
  - backend/app/api/routes/project_skills.py
  - backend/app/domain/repository/service.py
---

# 技能 {#skills}

技能是给芝士的一份操作说明，写在 markdown 里。会话里技能列表只列每个技能的名字和一句描述，芝士用到时才读正文：Claude Code 用 Skill 工具，Codex 和 pi 读技能列表里给出的那个文件。有平台自带的、有项目自己攒的，都装在会话的 `skills/` 目录下。Claude Code 默认把技能列表放在对话中间的 system 消息里，收不了这种消息的模型上，平台让它改放进第一条用户消息，见[网关](/dev/gateway#add-model)。几乎每轮都用得上的规矩不做成技能，直接写在系统提示词里，见[提示词注入与上下文管理](/dev/context)。

> 讲：平台的说明放在哪、哪些随会话发出去、项目自定义技能怎么确认与落地、分身定义怎么生成。不讲：系统提示词整段由什么组成，见[提示词注入与上下文管理](/dev/context)。

## 库：平台自己的几份说明 {#library}

`backend/app/domain/agent/skill_library/` 下的 markdown，各有各的用处：

| 文件 | 用在哪 |
| --- | --- |
| `chat.md` | 房间里怎么说话，整份进每个房间会话的系统提示词（`NATIVE_CHAT_GUIDANCE`） |
| `private_chat.md` | 私聊补的那几条，跟在 `chat.md` 后面（`PRIVATE_SKILLS`） |
| `doc_form.md` | 实况文档写哪几块、怎么改，进系统提示词里实况文档那一节（`prompt.DOC_FORM`） |
| `doc_writing.md` | 芝士文档的写作指南，作为 `cheese-docs` 技能发出 |
| `doc_blocks.md` | 每种块的写法，作为 `cheese-docs` 的 `references/blocks.md` 发出 |

`load_skills(names)` 按 frontmatter 里的 `name` 取正文，认不出的名字跳过。文档芝士（`document/question.py`）每次都在写文档，所以它不加载技能，而是把「写作」规则、`doc_writing.md` 和 `doc_blocks.md` 整份内联进提示词。

## 平台技能和仓库自己的规矩 {#platform}

`backend/sandbox/skills/cheese/` 是平台自己的那份说明，讲在知是里交付工作的流程：开任务、提交推送、递卡修订、合并冲突，以及 `cheese` 命令行每个子命令的用途；产物与预览、邮箱和飞书、定时与触发、读 CI 各放一份 `references/`。平台工具不在里面列：每个工具带着自己的说明进会话。它对**所有托管仓库**都成立，托管仓库不需要为平台做任何改动。每轮都用得上的那几条平台规矩（房间里的人是产品用户、回复里不提内部机制、不用 `git stash`、改仓库前先开任务）在系统提示词的「平台规矩」一节。

仓库自己的 `CLAUDE.md` 是另一回事，它讲这个仓库本身的结构与约定，跟着仓库走。两者的分工与注入场合见[提示词注入与上下文管理](/dev/context)的「不同场景」一节。

`skills.py` 里 `_SHIPPED_NATIVE_SKILLS = ("cheese", "documents", "wolfram")` 是平台随会话发的原生技能；`native_skill_files()` 把它们连同 `cheese-docs`（由 `doc_writing.md` 和 `doc_blocks.md` 生成）摊成 `{"skills/<name>/<路径>": 正文}`。技能不是一份 markdown：`documents` 还带着脚本和它们的参考文件，缺了就成「读了一条命令、跑起来说没有这个文件」，所以整个目录一起走；能走的后缀白名单是 `SKILL_FILE_SUFFIXES`，多出来的东西由 `backend/tests/unit/test_native_skill_files.py` 在构建时拦，而不是安静地不发。

`RESERVED_SKILL_NAMES` 是项目自己的技能不许占的名字：平台发的那些，加上 `cheese-docs`。平台不再发的技能，每台机器按自己记下的清单（`.cheese-platform-skills`）删掉。

## 项目技能 {#project}

项目自己的技能（界面上就叫「技能」，提示词里叫「项目技能」，和平台自带的区分开）是原生技能：一个 `ProjectSkill` 行是可编辑的源，每一版确认过的内容留一条 `ProjectSkillRevision`。字段是 `title` / `description` / `inputs` / `steps` / `outputs` / `files`（`FIELDS`）。

- **只有确认过的版本会发出去。** AI 队友起草或修改（`by_agent=True`）只是把行改成 `draft` 并记下是谁提的，行上的 `shipped_revision` 仍指上一次确认；`publish()` 按 `shipped_revision` 连 `ProjectSkillRevision` 取内容，所以等人确认的这段时间里，发出去的还是上一版。全新的一份 AI 草案 `shipped_revision` 是 0，连不上任何一版，于是它一份都不发。
- **人改的就是当场确认。** `create` / `update` 里 `by_agent=False` 直接走 `confirm()`：内容与最新一版一样就沿用那个版本号，否则加一版并记下 `confirmed_by`、`note`。`restore(revision)` 把旧版内容搬回来再确认一次，也就是「恢复」也留痕。
- **限制**（`service.py` 顶上）：名字要匹配 `^[a-z0-9][a-z0-9-]{1,47}$`；配套文件最多 `MAX_FILES = 30` 个、每个不超过 `MAX_FILE_BYTES = 200_000` 字节、后缀在白名单里、路径不许绝对、不许 `..`、不许叫 `SKILL.md`；`title`、`description`、`steps` 一个都不能空。
- **每次改动都重建镜像。** `publish()` 在 `{workspace_root}/.project-skills/<项目 id>/` 下搭一份新目录、写 `render_skill()` 的结果和配套文件，再把旧的换掉（先改名、再 `rename`、最后删），换的动作是整目录替换。接口层 `backend/app/api/routes/project_skills.py` 在 create / update / confirm / decline / restore / delete 每一处之后都调 `publish()`，并对这项技能来自的房间发 `announce_stale(…, "skills")`，房间里那张提议卡跟着重读。

`render_skill()` 产出的 `SKILL.md` 是一份做法，不是一份记录：它写上「第 N 版，由谁确认」，要求「每次使用都以这一次用户给的输入为准，不沿用以前某一次的具体材料；缺少必需的输入就先问用户」，末尾的「用的时候」一节带着这项技能的 id，告诉芝士照着做时被纠正就用 `cheese_skill_update` 提议修改这一份。

### 芝士怎么提议 {#proposals}

芝士用两个平台工具提：`cheese_skill_draft` 起草新的一份，`cheese_skill_update` 提议改已有的一份。什么时候该提写在系统提示词的「项目技能」一节，和记忆说明并列：单条规则进记忆，同一类事攒成一整套才打包成项目技能；五个条件写在 `cheese_skill_draft` 的工具说明里。

- **依据跟着草稿走。** 起草时带上 `taught`（用户为这类事教过哪几处）、`accepted`（这次怎么算做成的）、`related`（和最接近的已有技能比过没有）、`absorbs`（这项技能吸收掉的 team 记忆）；改的时候带 `reason`。它们存在行上的 `proposal` 里，房间聊天栏底部的提议卡（`SkillProposalCard`，数据在 `useSkillProposals`）把它们摆给人看，人在卡上点「保存」或「不保存」。保存或人改过之后 `proposal` 清空。
- **门槛只管芝士，不管人**（`_admit_proposal`）：人拒绝过的同名技能不再提；一个房间同时只有一份提议在等人；项目里的技能到了 `PROPOSAL_LIMIT = 20` 项，芝士不再新建，改为提议合并或修改。人自己新建不受这几条限制。
- **拒绝留痕。** `decline()` 把从没保存过的新提议记成 `declined`（列表里不出现，名字仍被它占着，人自己写同名的一份时把它替掉）；对已保存技能的改动退回保存的那一版。技能页上对芝士草稿点「不要了」走的也是这一步。
- **保存时吸收记忆。** `confirm` 时，`proposal.absorbs` 列出的 team 记忆连同 `MEMORY.md` 里指向它们的那几行一起删掉（`MemoryFileStore.forget`），同一条规则不留两份。只收 team 下的记忆：项目技能是全项目共用的，个人偏好不并进去。

## 会话里怎么落地 {#deploy}

`repository/service.py` 的 `session_dir(project_id, topic_id)` 是那个 ~/.claude 目录，它做三件事：把 `backend/sandbox/skills` 整棵拷进 `skills/`，调 `_sync_project_skills()` 把项目确认过的那几份放进来、并按 `.cheese-project-skills.json` 这份清单删掉已经不存在的，再把 `cheese` CLI 放到 `bin/cheese`。

项目技能另外还有一条路：`session_skill_files(project_id)` = `native_skill_files()` + `project_skill_files(project_id)`，按 `{"skills/<名字>/<路径>": 正文}` 交给会话。两条路读的是同一批文件，所以容器里改好的技能和设备上的那一份不会分叉——一台机器上对、另一台上是旧的，是没人会发现的那种坏法，因为两边都跑得起来。

## 分身定义：`cheese sync-agents` {#agents}

`backend/sandbox/cheese` 里的 `_sync_agents` 挂在 SessionStart / UserPromptSubmit 钩子上，把项目的模型目录写成本会话的 Claude Code 分身定义 `agents/model-<id>.md`：`name` 是清洗过的模型 id，`model` 是真 id，`description` 是主 agent 在 Agent 工具清单里读到的那一行，正文只有一句「由 cheese sync-agents 按项目模型目录生成，别手改」。

它只是发现层——真正的闸在准入，指定了目录外的模型会被拒——所以任何失败都不许打断会话：`_sync_agents` 恒退出 0。清理只动自己的东西：只删 `model-*.md` 里带那行记号的，上一版按队友生成的 `mate-*.md` 是陈迹，无条件清。分身按模型起、不按队友起，也没有平台语义。
