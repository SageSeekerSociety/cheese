## 状态：核心改动已通过对话验证，卡在质量闸门的沙箱基础设施问题上

## 问题

`backend/app/domain/agent/skill_library/conversation_style.md` 开篇讲"这不是 AI 聊天软件，是协作平台"，但通篇没说"用户不能执行命令"。agent 带着 Claude Code 的默认认知，会让用户去跑 `/permissions`、`!` 开头的命令、终端指令等——用户输进去只是纯文本，什么都不会发生。本轮实测复现过。

## 已做的改动

在 `<&backend/app/domain/agent/skill_library/conversation_style.md>` 里，"先回应，再干活"和"产出进文档，聊天只报信"两节之间，新增一节「用户看的是消息面板，不是终端」，明确三点：

- 用户不能执行命令、不能用斜杠命令、不能跑任何 shell/CLI 指令
- 内部工具（包括 `cheese` 系列命令）绝不能让用户去跑——那是 agent 自己的工具
- 需要用户拍板时用带选项的提问（对应 cheese ask），别让人打字

这是纯 system prompt 文案改动，每个 turn 重新组装 system prompt，改完即生效，不需要重启后端。已确认没有测试或代码对这个文件做内容快照断言（`skills.py` / `chat.py` 只按技能名 `conversation-style` 动态加载），改动安全。

## 约束

- 遵守 CLAUDE.md
- 所有提交走 PR（这条改动本身也要走 PR，不直接合 main）

## 验收方式（跟其他子话题不同）

不写 backend 测试。验收方式是**对话验证**：在话题里问 agent"怎么换模型"之类的问题，观察它是否还会让用户去执行命令/跑终端指令。**已完成，通过**——回答改成了指向项目设置页的 UI 操作，没有出现"跑命令""敲 /xxx"。

## 卡点：质量闸门跑不完，非本话题改动导致

递验收卡触发的质量闸门要跑 `<&.claude/scripts/check.sh>`：

1. **git 依赖 bug（已在本话题修复）**：脚本原来第 9 行用 `git rev-parse --show-toplevel` 定位仓库根，但本仓库版本控制是 jj，闸门执行沙箱里没有 `.git`，脚本一开头就 fatal 退出——所有验收卡都会这样，跟本话题的改动内容无关。已改成用脚本自身路径推算 `REPO_ROOT`，`bash -x` 验证过不再报 git 错误，正常进入 ruff/pyright/pytest 三段。
2. **Postgres/Docker 不可达（未解决，非本话题职责）**：本话题工作区沙箱镜像是通用镜像，没有 `docker`、没有 `task`，`localhost:5432`/`5433` 都连不上，`pytest` 跑到集成测试就会一直挂起等 DB 连接，无法在这个沙箱里跑绿。这和父话题里排查过的"项目沙箱镜像 current=null，需要切到 cheesex-dev:v0"是同一个根因——但切镜像是另一条子话题的职责范围，本话题不动它。

ruff（lint + format）、pyright 已经在本地手动跑过，全绿；pytest 因为上面第 2 点跑不完。

## 下一步

需要 @wangchangxin 拍板：本话题这条纯文档改动，要不要因为沙箱缺 Postgres 这个共享基础设施问题被卡住？还是先递验收卡（gate 大概率因 pytest 挂起而超时/红），或者等负责切镜像的子话题先落地。
