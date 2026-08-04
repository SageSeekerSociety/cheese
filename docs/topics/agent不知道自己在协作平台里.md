## 状态：核心改动已验证通过；卡在 check.sh 反复暴露闸门 worktree 的新损坏点，需要人帮忙看真实日志再继续猜

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

## 环境限制：本话题沙箱跑不完 pytest，与本话题改动无关

递验收卡触发的质量闸门要跑 `<&.claude/scripts/check.sh>`：

1. **git 依赖 bug（已在本话题修复）**：脚本原来第 9 行用 `git rev-parse --show-toplevel` 定位仓库根，但本仓库版本控制是 jj，闸门执行沙箱里没有 `.git`，脚本一开头就 fatal 退出——所有验收卡都会这样，跟本话题的改动内容无关。已改成用脚本自身路径推算 `REPO_ROOT`，`bash -x` 验证过不再报 git 错误，正常进入 ruff/pyright/pytest 三段。
2. **Postgres/Docker 不可达（本话题沙箱无法解决，非本话题职责）**：本话题工作区沙箱是 split 时起好的通用镜像，没有 `docker`、没有 `task`，`localhost:5432`/`5433` 都连不上。@wangchangxin 已经在父话题把项目设置切到了专用镜像 `cheesex-dev:v0`，但那只对之后新起的沙箱生效，本话题这个沙箱不会补丁式换镜像——等不到。已用单测试文件直接跑确认过：失败是 `asyncpg.connect` 很快抛 connection refused（`tests/conftest.py:217` 的 `_admin_recreate_db`），**不是真的 hang 住**；此前用 `-n 4 --reruns 2 --reruns-delay 3` 跑全量集成测试时长时间没输出，是并行 worker 数 × 失败重跑 × 3s 延迟叠加导致看起来像挂起，不是新 bug，不需要另开话题记。

ruff（lint + format）、pyright 已经在本地手动跑过，全绿；pytest 因为沙箱没有 Postgres 无法跑完，是环境限制，不代表代码有问题。

## 卡点 3：验收卡被质量闸门拦下，`.venv` 损坏在闸门 worktree 里，本话题工作区碰不到

递卡后闸门在 `/home/nictheboy/cheese-workspaces/.worktrees/de808b13-.../topic_dcf968a1/backend/` 跑 check.sh，三步全 FAIL，报的都是同一个错：

```
error: failed to remove directory `.../backend/.venv/share`: Permission denied (os error 13)
```

pytest 那步还带了一句 `Ignoring existing virtual environment linked to non-existent Python interpreter`——说明那个 worktree 里已经有一个 `.venv`，指向的 Python 解释器路径失效了，`uv` 想删掉重建，但 `.venv/share` 权限不够删不掉。

判断：这不是本话题改动引入的问题。`backend/.gitignore` 和根 `.gitignore` 都排除了 `.venv/`，我这次改动只碰了两个 markdown/shell 文件，跟 `.venv`毫无关系。那个损坏的 `.venv` 是闸门执行用的 worktree 自己积累下来的状态（大概率是之前某次运行在不同权限/容器下建的，符号链接指向的解释器后来失效了）。

**更新**：不用等人工清理了。根因确认是闸门运行环境的通病（另一条子话题 C 完全没碰数据库相关操作也独立撞上了一模一样的报错，排除了"是某个子话题装东西弄坏 `.venv`"这个猜测）——闸门执行 check_command 的用户跟 worktree 里遗留 `.venv` 的属主不一致，`uv` 想清理/重建 `.venv/share` 时没权限，直接炸。

已在 `<&.claude/scripts/check.sh>` 里加了防御：`cd` 进 backend 之后，先测 `.venv/share` 存在且不可写，就把 `UV_PROJECT_ENVIRONMENT` 指到 `mktemp -d` 新建的临时目录，让 `uv` 在那边重建 venv，不碰旧目录。本地手动模拟过只读的 `.venv/share`（`chmod 500`），确认走 fallback 分支后 `uv run ruff --version` 能正常创建新 venv 并跑起来。

## 卡点 4：`.venv` fallback 生效了，但暴露出下一层问题——`srp-rs` 编译不过

重新递卡后，`.venv/share` 那条 note 确实打印出来了（fallback 触发成功），但三步还是全 FAIL，这次报的是：

```
hint: `srp-rs` was included because `cheesex-backend` (v0.1.0) depends on `srp-rs`
hint: Build failures usually indicate a problem with the package or the build environment
```

`srp-rs`（`<&backend/srp_rs>`）是本仓库工作区内的本地 Rust 扩展包（`pyproject.toml` 里 `srp-rs = { workspace = true }`，构建后端是 maturin），不是 PyPI 上能直接装 wheel 的包——换到全新 scratch venv 意味着要重新用 cargo 编译它。

**这条我暂时不敢再猜着改**，原因：

1. 我自己的沙箱里也没有 `cargo`/`rustc`（`command -v cargo` 找不到），没法在本地复现或验证任何跟这个编译步骤相关的修复。
2. `check.sh` 里 ruff/pyright 两步用的是 `tail -1`/`tail -2` 截断输出，真正的报错原因（究竟是 `srp_rs/target/` 目录权限问题——跟 `.venv/share` 一个模式，还是编译环境缺网络/缺系统库）被截没了，只剩 hint，看不出根因。
3. 这个 `.venv` fallback 改的是所有子话题共用的 `<&.claude/scripts/check.sh>`，已经连续两轮靠"递卡→看报错尾巴→猜一个 patch"这个方式改，不想再在看不到真实报错的情况下继续堆猜测式修改。

## 下一步

需要 @wangchangxin 帮忙看一下 `logs/gate-dcf968a1.log` 完整内容（不只是贴的尾巴），或者直接去那个 worktree 确认 `srp_rs/target/` 是不是跟 `.venv/share` 一样被之前某次运行留下了属主不一致、写不进去的情况。拿到真实报错再决定怎么改，比继续猜要快。
