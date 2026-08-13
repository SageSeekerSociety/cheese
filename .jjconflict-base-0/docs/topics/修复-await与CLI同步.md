> 状态：任务二、三已完成并跑绿；**任务一（`cheese await`）在我做的过程中被别的话题抢先做完并合进 main，我的那份已整条丢弃**。前一次 PR #257 是空的，原因见文末，采纳已被撤回。

## 一句话

说明书写了 `cheese await`，实物没有；沙箱里的 `cheese` 比后端旧一版。同一个病根：说明书和实物之间**没有任何东西在把它们钉住**。

## 交付了什么

### 一、`cheese await` —— 放弃，main 上已有

我实现了一份（后台任务 + 两个端点 + CLI 子命令 + 测试），但期间另一个话题把 `awaited_tasks.py` 连同 CLI、SKILL.md、两个测试文件一起合进了 main。我这份**已整条删除**，没有强推。

两份的设计其实不同，而且**他们那份更合适**：我把命令放在质量闸门那套一次性容器里跑（无网络、无密钥），他们放在芝士自己的容器里跑（`__await-child` 分离子进程）。说明书承诺的用途包含"跑构建、长跑脚本"，那些常常要接着用芝士自己起的服务和环境——他们的做法接得上，我的接不上。他们还多了唤醒频率上限（`MAX_WAKES_PER_WINDOW`）和延后唤醒，我没有。

**这是我的流程错误**：<&CLAUDE.md> 写明"多个 agent 并发，动手前先查开着的 PR 和 main 的近期提交有没有人在修同一个问题"，我没查就开工，做完才发现撞车。

### 二、沙箱 CLI 同步 —— 断点找到了，改成结构上不可能漂

**真实断点跟简报的猜测不同**：CI 判定没问题（<&.github/scripts/plan-image-builds.sh> 明确有 `backend/sandbox/*` → 重建沙箱镜像），部署也没问题（按 commit SHA 拉镜像）。问题在挂载源：容器里的 `/usr/local/bin/cheese` 本来就是**挂进去**的（不是镜像里烤的），而挂载源是 `SANDBOX_SHIM_HOST_DIR` 指向的一个**宿主机上人工维护的目录**——没有任何东西负责让它跟后端同步。

（怎么确认的：如果这个变量是空的，代码会去挂 `/app/sandbox/cheese`，那是后端**容器内**的路径，宿主 docker 看不见，会在宿主上凭空建一个空目录、拿目录盖文件、容器直接起不来。而现场容器起来了、挂载确实存在、内容是旧的——只剩"变量设了且指向一份旧的"这一种可能。）

**改法**：挂载源换成**后端自己那一份**。`ws.session_dir()` 每轮都会重建话题的 `~/.claude` 挂载，现在顺手把 `sandbox/cheese` stage 到 `<session>/bin/cheese`；两个容器后端（SDK 的 <&backend/sandbox/claude-sbx>、tmux 的 <&backend/app/domain/agent/tmux_provider.py>）都从那儿挂。会话目录本来就是宿主可见的挂载源，所以"宿主看得见"和"跟后端同版本"一次满足，**没有第三方需要有人去同步**。

配套三条，保证不再静默：

1. **老容器会被识别并重建**。挂载在容器创建时就定死了，一个改动前建的话题容器会一直用旧挂载源——两个后端都加了比对（tmux 侧 `_cli_mount_stale`，shim 侧比对 `Source->Destination`），对不上就重建，并沿用已有的"环境重建"提示告诉用户。
2. **`cheese --version` 打源码指纹**。任何人（或芝士自己）在容器里一条命令就能核对跑的是不是后端那份。
3. **`SANDBOX_SHIM_HOST_DIR` 退休**，但不是悄悄忽略：还设着它的机器会在后端启动时打一条明确的 warning（<&backend/app/main.py>），因为"还设着它"正是当年出事的那个配置。

**这次落后一版的真实损害**：仓库版 `remember`/`recall` 会带 `topic` 字段（后端据此认出是哪个芝士在记），沙箱里那版不带——**所有 agent 写的记忆都归错池子，完全静默**。这条已写进新代码的注释里，免得以后有人觉得挂载只是个优化。

### 三、文档 vs 实现 —— 对齐了，并且钉住了

main 现在的状态：`await` 两边都有了（别的话题带来的），但 `gh-token` 仍然**有实现、没文档**，`--version` 是我这次新加的。两条都补进了命令表。

**钉住的办法**：新增 <&backend/tests/unit/test_cheese_cli_docs.py>，把 SKILL.md 的命令表和 argparse 的 subparser 集合互相比对，任一方向缺失都红（`_` 开头的内部命令如 `__await-child` 排除在外）。跟 `test_hooks_substrate.py` 钉 hook 脚本是同一招。这条测试如果早存在，`await` 那个洞根本写不进说明书。

为此把 CLI 的 `main()` 拆出了 `build_parser()`——测试要拿到 parser，顺带让这个 400 行的脚本第一次变得可测。

## 改动清单（10 个文件，+423/−36）

新增：<&backend/tests/unit/test_cheese_cli_docs.py>、<&backend/tests/unit/test_cheese_cli_staging.py>

改动：<&backend/app/domain/agent/tmux_provider.py>、<&backend/app/domain/workspace/service.py>、<&backend/app/main.py>、<&backend/app/core/config.py>、<&backend/sandbox/cheese>、<&backend/sandbox/claude-sbx>、<&backend/sandbox/skills/cheese/SKILL.md>

只动后端和 sandbox 目录，没碰前端。

## 检查

| 项 | 结果 |
|---|---|
| ruff check + format | 通过（711 文件） |
| pyright | 0 errors |
| alembic heads | 单一 head |
| pytest 全量 | **3785 通过 / 31 跳过 / 23 失败** |
| 新增的两个测试文件 | **11 条全绿** |

23 条失败全是沙箱环境缺口，跟改动无关：22 条是已知的 procps 缺失（`FileNotFoundError: 'kill'`），1 条 `test_market_api` 是缺 `ANTHROPIC_AUTH_TOKEN`（补个假 token 重跑即过，已记入项目记忆——<&CLAUDE.md> 里没记这第三个缺口）。

## 上一次 PR 是空的：原因

PR #257 检查全绿、自动合并，但**一个文件都没带**。原因不在代码：

话题工作区 `/work` 是一个**独立的 jj workspace**，旁边没有 `.git`（真 `.git` 在 `/<project-id>/`），所以**不是 colocated**——我在 `/work` 里的 jj 命令不会自动导出 git ref。jj 的 bookmark 一直正确指向我的提交，但 git 侧的 `topic/f866ec68` 始终停在建工作区时那个空提交（作者 `JJ_EMPTY_STRING`）。采纳流程读的是 git ref，于是推了个空分支、检查在"没有改动"上全绿、自动合并。

**这是平台级的坑，不是我一个人的**：验收流程无法区分"这次改动没问题"和"改动根本没到分支上"，两种情况都是绿。已记入项目记忆，含防呆步骤（递卡前必须 `jj git export` 并用 `git diff --stat main topic/<id>` 确认文件在分支上）。

顺带修了两处：提交原本**没有描述**，且作者是空身份（这个仓库的 git 和 jj 都没配 `user.*`，jj 警告"推不上远端"）。已按平台约定署名 `芝士 <cheese@zhishi.local>`。

## 遗留

现网后端镜像里的 SKILL.md 与 main 不一致，说明有一条没人看着的内容通路，值得单开一条查。
