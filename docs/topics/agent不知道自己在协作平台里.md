## 状态：核心改动已验证通过；venv/构建/pyright 都过了，ruff 缓存权限已修，卡在 pytest 因沙箱缺 Postgres 超时（环境限制，已知）

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

## 卡点 4 解决：换成 `--no-sync` 复用现有 venv，不再触发重建/编译

采纳了协作者的思路：既然只是权限问题、venv 本身没坏，就不该换新 venv 强迫重建（那条路才是逼出 `srp-rs` 编译失败的原因），应该原地复用现有 venv、跳过 uv 的 sync/校验。

`<&.claude/scripts/check.sh>` 改成：检测到 `.venv/share` 不可写时，给三个 `uv run` 调用统一加 `--no-sync`（`uv run --no-sync ...`），完全跳过 uv 的环境校验/重建逻辑，直接用已经装好的 venv 跑。本地验证：

1. `chmod 500 .venv/share` 模拟只读场景。
2. `uv run --no-sync python3 -c "import srp_rs, ruff"` —— 不触发任何删除/重建，`srp_rs` 直接可用。
3. 完整跑一遍 `check.sh`：`ruff`、`pyright` 都在 `.venv/share` 只读的情况下 PASS，没有任何编译/重建动作。pytest 卡在沙箱没有 Postgres（第 26-33 节说过的老问题，跟这次改动无关）。

顺带把 ruff/pyright 两步过度截断的 `tail -1`/`tail -2` 放宽到 `tail -20`（pytest 那步原本就是 `tail -3`，也一并放宽），下次再出问题能看到真实报错而不是只剩 hint。

## 卡点 5：`--no-sync` 没生效——它只跳过"装依赖"，跳不过"校验 venv 本身"

递卡后闸门报了同样的错，且带着 `--no-sync` 的 note 已经打出来了：

```
warning: Ignoring existing virtual environment linked to non-existent Python interpreter: .venv/bin/python3 -> python
error: failed to remove directory `.../backend/.venv/share`: Permission denied (os error 13)
```

判断错了一步：`--no-sync` 只跳过"把依赖同步进 venv"这一步，跳不过更早的"校验 venv 本身是否可用"——uv 发现 `.venv/bin/python3` 最终指向的解释器路径在这个容器里不存在，就无条件要整个删掉重建 `.venv`（含 `share`），这一步不受 `--no-sync` 影响。之前只在本地模拟了"`share` 只读"，没模拟"解释器符号链接真的悬空"，所以本地测出来是绿的，闸门上还是红。

**这次用两个模拟叠加，精确复现了闸门的报错**（`.venv/bin/python` 指向 `/nonexistent/...` + `.venv/share` 只读），改成：

1. 检测 `.venv/bin/python` 是不是悬空符号链接（`[ -L ... ] && ! [ -e ... ]`），是的话用 `uv python find` 解析出这台机器上实际可用的解释器，把符号链接重新指过去——uv 就会认为这个 venv 有效，不再触碰它，也就不会要求重新编译 `srp-rs`。
2. `.venv/share` 不可写时仍然叠加 `--no-sync` 做第二层保险。

两个模拟叠加后完整跑 `check.sh`：ruff、pyright 都 PASS，没有任何删除/重建/编译动作。

## 卡点 6：`.venv/bin` 也不可写，`ln -sf` 直接把脚本崩了

递卡后闸门这次报的是：

```
note: .venv/bin/python is a dangling symlink (venv built in a different container) — repointing at /data/apphome/.local/share/uv/python/cpython-3.13-linux-x86_64-gnu/bin/python3.13
ln: failed to create symbolic link '.venv/bin/python': Permission denied
```

输出到这就断了，后面 ruff/pyright/pytest 一个都没跑。原因：`.venv/bin` 在闸门 worktree 里跟 `.venv/share` 一样不可写（不是只有 `share` 有问题，整棵 `.venv` 都不是当前执行用户能写的），而脚本里 `ln -sf` 失败时没做保护，`set -euo pipefail` 下直接把整个脚本杀死，连 "FAIL: ruff" 都没来得及打。

结论：**原地修复这条路彻底走不通**——当前执行用户对 `.venv` 树完全没有写权限，不只是某个子目录。改成：

1. `ln -sf` 加保护（`... 2>/dev/null` + 判断返回值），失败就不再假装能原地修，直接降级到 scratch venv（`UV_PROJECT_ENVIRONMENT` 指到 `mktemp -d` 新目录），而不是让脚本崩掉。
2. 本地验证：用"悬空符号链接 + `.venv/bin` 也设成不可写"精确复现闸门场景，跑 `check.sh`，触发 scratch venv 分支后 `uv` 在 3 秒内装完 196 个包（含 `srp-rs`），ruff PASS——说明本地这台机器 `uv` 的全局缓存里已经有构建好的 `srp-rs` wheel，不需要临时调用 cargo。

**说清楚这条我判断不了的部分**：本地能这么快装完，是因为复用了 uv 的全局构建缓存（这台机器之前构建过一次）。闸门那边执行用户的 HOME 是 `/data/apphome/...`，跟我本地、跟之前 `srp-rs` 编译失败时用的应该是同一个环境——如果那边的 uv 缓存是冷的、且真的没有 cargo，scratch venv 这条路径大概率还是会在 `srp-rs` 上失败（回到卡点 4 的错误）。这个我在自己沙箱里验证不了，只能试了再看这次报的是不是同一个错。

## 卡点 7：`--no-sync` 那条分支自己又想删 `.venv/share`——检测式修法不可靠，换成"失败了才重试"

递卡后又不一样了：这次没报"悬空符号链接"，走的是 `.venv/share` 不可写那条分支（打出了 `--no-sync` 的 note），但 `uv run --no-sync` 本身还是尝试删 `.venv/share` 并报 Permission denied，三步全 FAIL。跟卡点 5 本地验证过的"符号链接修好之后 + share 只读 + --no-sync = 能过"对不上——说明闸门那个 worktree 里 `.venv` 损坏的具体形状，每次递卡看到的都不完全一样（有时是符号链接悬空，有时不是，`--no-sync` 该生效的条件也没法从我这边稳定复现）。

连续 3 轮（卡点 5/6/7）针对"具体哪里坏"做检测再对症下药，每次都猜错一部分——说明这条路本身就不可靠：我在本地永远只能模拟"我能想到的坏法"，猜不全闸门那边实际的状态。

**换思路**：不再检测、不再猜"哪里坏"，而是"先按正常方式跑，真跑失败了、且是权限错误，才重试一次、退到 scratch venv"。改动：

```
run_uv() {
    if out="$(uv run "$@" 2>&1)"; then ...; return 0; fi
    if [ -z "${UV_PROJECT_ENVIRONMENT:-}" ] && echo "$out" | grep -qi "permission denied"; then
        export UV_PROJECT_ENVIRONMENT="$(mktemp -d)/venv"
        export CARGO_TARGET_DIR="$(mktemp -d)/cargo-target"
        if out="$(uv run "$@" 2>&1)"; then ...; return 0; fi
    fi
    ...; return 1
}
```

三个检查步骤都套一层 `run_uv`。本地用"悬空符号链接 + `.venv/bin` 和 `.venv/share` 都不可写"（比之前任何一次模拟都更彻底）验证：

- ruff 第一次跑失败（权限错误）→ 自动重试一次，退到 scratch venv+scratch cargo target dir → PASS。
- pyright 复用同一个 scratch venv（`UV_PROJECT_ENVIRONMENT` 在 shell 里 export 过，后续步骤第一次就直接用 scratch venv，不再重试、不再重复建 venv）→ PASS，没有二次 "note"。
- 全程不需要预判 `.venv` 具体是符号链接坏还是哪个子目录不可写——只要失败信息里有 "permission denied" 就统一处理。

这个策略不管闸门那边 `.venv` 到底以哪种具体形式损坏，只要报的是权限错误就能兜住，不用再一轮一轮对症下药。

## 卡点 8 解决：拿到真正根因了——闸门容器和沙箱 `$HOME` 不同

协作者 B 独立排查出了比"属主权限"更准的根因：闸门容器和交互沙箱是两个独立容器，只共享 `/work` 这个挂载，但 `$HOME` 不一样（`/data/apphome` vs `/home/node`）。`.venv/bin/python` 这个符号链接是相对某个具体 `$HOME` 里 uv 管理的 python 装的路径生成的——换一个 `$HOME`，它就指向一个根本不存在的路径。这才是"non-existent Python interpreter"和后面一串 Permission denied / build failure 的真正源头，不是单纯的属主权限问题（虽然表现上很像）。这也解释了为什么我前几轮"检测哪个子目录不可写"屡次猜错：权限报错只是这个根因的下游症状，具体在哪一步炸取决于 uv 内部走到哪，不是稳定可预测的。

B 的修法（已经让 B 的验收卡真正过了闸门，变成 pending）：

1. 先探测 `.venv/bin/python3` 在**当前这个容器**里能不能真的执行——直接跑一下，不经过 uv 的校验逻辑。
2. 能执行：说明 `$HOME` 一致（闸门连续跑、或者本地正常开发都会走这条），直接 `--no-sync` 复用现成 venv，走快路径。
3. 不能执行：同步一份到临时目录，大概 40 秒——**这不是重新从源码编译 `srp-rs`**，是把 uv 已经解析好、缓存好的 wheel/构建产物同步到新位置，所以不需要 cargo。
4. 顺带把 `RUFF_CACHE_DIR`、pytest 的 `cache_dir` 也钉到临时目录，避免被别的 uid 留下的缓存卡住（这两个之前没注意到，也是潜在的权限坑）。

`<&.claude/scripts/check.sh>` 换成了这个思路，放弃了之前几轮"检测 share/bin 具体哪里不可写"的猜测式判断。本地验证：

- 健康 venv：探测通过，直接走 `--no-sync` 快路径，没有多余 note。
- 模拟"符号链接悬空 + `.venv/bin`、`.venv/share` 都不可写"（$HOME 不一致的效果）：探测失败，同步到临时目录，ruff/pyright 都 PASS。

## 卡点 9：`$HOME` 修法验证成功——venv/构建都过了，卡在 `.ruff_cache` 权限 + pytest 真超时

之前那张"run_uv 重试"版本的卡排队排到现在才出结果（不是新提交的）。这次进展明显：

- venv 同步成功，**`srp-rs` 真的从源码构建过了**（"Building srp-rs ... Built srp-rs"），说明闸门容器里是有 cargo 的，之前一直没走到这一步纯粹是被 venv/权限问题挡住。
- `pyright` **PASS 了**。
- `ruff` 卡在一个新的权限点：`.ruff_cache/0.15.17/.tmpxxxxx` 建不了临时文件，Permission denied——`ruff` 默认把缓存写在项目目录下的 `.ruff_cache/`，跟之前 `.venv` 一个模式，也是被闸门 worktree 里遗留的旧属主挡住了。**这条我已经在上一轮改 `check.sh` 时顺手解决了**（探测到需要走 scratch 路径时会把 `RUFF_CACHE_DIR` 也钉到临时目录）——当前 `<&.claude/scripts/check.sh>` 已经带这个修复，这次报错的是排队中的旧版本，不是当前版本。
- `pytest` 这次不是快速失败，是跑到 **600 秒硬超时被强制中止**——跟我在自己沙箱里验证的"connection refused 快速失败"不一样，说明闸门容器和我交互沙箱的网络拓扑也不同（闸门那边可能是 TCP 连接超时而不是立即拒绝）。根因还是同一个：这个话题的沙箱/闸门环境连不上 Postgres，不是新问题。

## 下一步

用当前已经带 `RUFF_CACHE_DIR` 修复的 `check.sh` 重新递验收卡，附言说明 ruff 的权限问题已经修过、pyright 已经验证 PASS、pytest 超时是环境限制（沙箱缺 Postgres）不是代码问题。
