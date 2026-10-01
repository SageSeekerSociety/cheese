---
title: 技能
kind: 参考
summary: 平台技能库、阶段说明、项目自定义技能、沙盒技能和分身定义怎么选、怎么装进会话。
covers:
  - backend/app/domain/agent/skills.py
  - backend/app/domain/agent/stages.py
  - backend/app/domain/agent/skill_library/
  - backend/app/domain/project_skill/
  - backend/sandbox/skills/
  - backend/app/api/routes/project_skills.py
  - backend/app/domain/repository/service.py
---

# 技能 {#skills}

技能是给芝士的一份操作说明：这一轮该怎么做，写在 markdown 里，和这一轮一起进系统提示词。有平台自带的、有项目自己攒的，都装在会话的 `skills/` 目录下。

> 讲：技能库怎么选、阶段说明怎么算、项目自定义技能怎么确认与落地、分身定义怎么生成。不讲：系统提示词整段由什么组成，见[提示词注入与上下文管理](/dev/context)。

## 库：一个标签选一份 {#library}

`backend/app/domain/agent/skill_library/` 下 9 个 markdown，每份的 frontmatter 有一个 `scenarios:`，选择器就是它：

| 文件 | `scenarios:` |
| --- | --- |
| `chat.md`、`chat_detail.md` | `chat` |
| `doc_form.md` | `chat`、`doc` |
| `private_chat.md` | `private` |
| `stage_delegating.md` / `stage_gate.md` / `stage_awaiting.md` / `stage_conflict.md` / `stage_archived.md` | `stage:<阶段>` |

`skills_for_scenario(scenario)` 按标签选、按文件名排序；`load_scenario(scenario)` 把这些正文拼起来（`\n\n---\n\n` 相连）。`load_scenario` 每一轮都跑，所以它一遍读完整个目录，而不是先解析名字再回头重读文件取正文。

`load_skills(names)` 是另一条路：按显式名字取，认不出的名字跳过。今天走这条的有房间那一份 `NATIVE_CHAT_GUIDANCE`（`["chat"]`）、私聊（`PRIVATE_SKILLS = ["private-chat"]`）。

`scenarios:` 这个字段从库写出来那天就在，但一直到 `skills_for_scenario` 出现之前没人读过它——`load_skills` 只认显式名字，一个技能想服务两个场景只能被抄进两张名单。现在它是真的选择器。

## 阶段：注入哪一段是平台算的 {#stage}

一个话题在任一时刻处在流程的某一段，而每段该知道的东西不一样。`stages.py` 把它算成一个 `TopicStage`：`delegating`（还没递卡，派活和干活是同一段）、`gate`（闸门在跑或刚红）、`awaiting`（等人采纳）、`conflict`（采纳时撞了冲突）、`archived`。

`resolve_stage(finished=…, card_statuses=…)` 只用**已经在手**的事实——话题的 kind/status，以及 `chat.py` 早就为「盲飞防护」查出来的 open 卡列表——不额外打一次库。多张活卡同时在时按 `_CARD_PRECEDENCE` 选：冲突、红闸门排在等人的前面，因为前者在等芝士动手、后者只是在等人。`gate_failed`、`gate_blocked`、`pending_gate` 合成一段：闸门红了要做的事和它正在跑时该知道的事是同一段知识。

`stage_scenario(stage)` 把阶段翻成 `stage:<值>` 这个标签——带前缀是为了和 `chat`/`doc` 这些场景分开命名空间，一个技能想同时服务多个阶段只要多写一个标签。注入点在 `chat.py`：`load_scenario(stage_scenario(topic_stage))` 进 `build_system_prompt(..., stage_guide=…)`。

**渐进的是「平台注入哪一段」，不是「模型决定读哪一段」。** 算出来的那一段静态拼进系统提示词，模型没有「要不要读」的选择权——懒加载在弱模型上不成立。

## 平台技能和仓库自己的规矩 {#platform}

`backend/sandbox/skills/cheese/SKILL.md` 是平台自己的那份说明：平台的动作是哪些工具、`cheese` CLI 干什么、房间里的那个人是产品用户不是运维。它对**所有托管仓库**都成立，托管仓库不需要为平台做任何改动。

仓库自己的 `CLAUDE.md` 是另一回事，它讲这个仓库本身的结构与约定，跟着仓库走。两者的分工与注入场合见[提示词注入与上下文管理](/dev/context)的「不同场景」一节。

`skills.py` 里 `_SHIPPED_NATIVE_SKILLS = ("documents",)` 是平台随会话发的那几份原生技能；`native_skill_files()` 把它们（连同 `doc_form.md` → `cheese-docs`、`chat_detail.md` → `chat-detail` 两份顺手改名的）摊成 `{"skills/<name>/<路径>": 正文}`。技能不是一份 markdown：`documents` 还带着脚本和它们的参考文件，缺了就成「读了一条命令、跑起来说没有这个文件」，所以整个目录一起走；能走的后缀白名单是 `SKILL_FILE_SUFFIXES`，多出来的东西由 `backend/tests/unit/test_native_skill_files.py` 在构建时拦，而不是安静地不发。

`RESERVED_SKILL_NAMES` 是项目自己的技能不许占的名字：平台发的那些，加上 `cheese`、`cheese-docs`、`chat-detail`、`cheese-chat`。

## 项目自己的工作方法 {#project}

项目攒下来的「工作方法」是原生技能：一个 `ProjectSkill` 行是可编辑的源，每一版确认过的内容留一条 `ProjectSkillRevision`。字段是 `title` / `description` / `inputs` / `steps` / `outputs` / `files`（`FIELDS`）。

- **只有确认过的版本会发出去。** AI 队友起草或修改（`by_agent=True`）只是把行改成 `draft` 并记下是谁提的，行上的 `shipped_revision` 仍指上一次确认；`publish()` 按 `shipped_revision` 连 `ProjectSkillRevision` 取内容，所以等人确认的这段时间里，发出去的还是上一版。全新的一份 AI 草案 `shipped_revision` 是 0，连不上任何一版，于是它一份都不发。
- **人改的就是当场确认。** `create` / `update` 里 `by_agent=False` 直接走 `confirm()`：内容与最新一版一样就沿用那个版本号，否则加一版并记下 `confirmed_by`、`note`。`restore(revision)` 把旧版内容搬回来再确认一次，也就是「恢复」也留痕。
- **限制**（`service.py` 顶上）：名字要匹配 `^[a-z0-9][a-z0-9-]{1,47}$`；配套文件最多 `MAX_FILES = 30` 个、每个不超过 `MAX_FILE_BYTES = 200_000` 字节、后缀在白名单里、路径不许绝对、不许 `..`、不许叫 `SKILL.md`；`title`、`description`、`steps` 一个都不能空。
- **每次改动都重建镜像。** `publish()` 在 `{workspace_root}/.project-skills/<项目 id>/` 下搭一份新目录、写 `render_skill()` 的结果和配套文件，再把旧的换掉（先改名、再 `rename`、最后删），换的动作是整目录替换。接口层 `backend/app/api/routes/project_skills.py` 在 create / update / confirm / restore / delete 每一处之后都调 `publish()`。

`render_skill()` 产出的 `SKILL.md` 是一份方法，不是一份记录：它写上「第 N 版，由谁确认」，并明确要求「每次使用都以这一次用户给的输入为准，不沿用以前某一次的具体材料；缺少必需的输入就先问用户」。

## 会话里怎么落地 {#deploy}

`repository/service.py` 的 `session_dir(project_id, topic_id)` 是那个 ~/.claude 目录，它做三件事：把 `backend/sandbox/skills` 整棵拷进 `skills/`，调 `_sync_project_skills()` 把项目确认过的那几份放进来、并按 `.cheese-project-skills.json` 这份清单删掉已经不存在的，再把 `cheese` CLI 放到 `bin/cheese`。

项目技能另外还有一条路：`session_skill_files(project_id)` = `native_skill_files()` + `project_skill_files(project_id)`，按 `{"skills/<名字>/<路径>": 正文}` 交给会话。两条路读的是同一批文件，所以容器里改好的技能和设备上的那一份不会分叉——一台机器上对、另一台上是旧的，是没人会发现的那种坏法，因为两边都跑得起来。

## 分身定义：`cheese sync-agents` {#agents}

`backend/sandbox/cheese` 里的 `_sync_agents` 挂在 SessionStart / UserPromptSubmit 钩子上，把项目的模型目录写成本会话的 Claude Code 分身定义 `agents/model-<id>.md`：`name` 是清洗过的模型 id，`model` 是真 id，`description` 是主 agent 在 Agent 工具清单里读到的那一行，正文只有一句「由 cheese sync-agents 按项目模型目录生成，别手改」。

它只是发现层——真正的闸在准入，指定了目录外的模型会被拒——所以任何失败都不许打断会话：`_sync_agents` 恒退出 0。清理只动自己的东西：只删 `model-*.md` 里带那行记号的，上一版按队友生成的 `mate-*.md` 是陈迹，无条件清。分身按模型起、不按队友起，也没有平台语义。
