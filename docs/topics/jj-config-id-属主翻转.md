## 结论先说

1. **父话题的推断全部成立，而且比推断更糟**：不是"`_repair_modes` 够不着"，而是**"修 mode"这条路结构上就走不通**——jj 每次重建 `config-id` 都是 `tmp + rename`（新 inode、硬编码 0600、属主＝调用者），任何事后 chmod 都只是在给一个马上会被替换掉的 inode 补权限。
2. **方案 B（收紧 `.jj/repo` 权限）已被实验否决**：`.jj/repo` 只要对沙箱不可写，沙箱里**所有** jj 命令（含只读的 `jj log`）当场 `exit 255` 硬失败。B 是把"后端停摆"换成"agent 全废"，直接违背 #237。
3. **方案 C（统一 uid）有效，但已经不是必需了**——我找到了一条不需要管理员、不需要改 uid 的根治法。
4. **推荐方案 E（新增，本卡主张）：让这个仓库根本不存在 `config-id`。** 它是 `jj config set --repo` 才会创建的东西，删掉之后 jj 一切正常（实测），身份改用 `JJ_USER`/`JJ_EMAIL` 环境变量注入（实测生效）。**没有文件，就没有属主可翻转。**
5. **关于"修完之后会不会被 agent 一次 jj 调用打回去"**：方案 E 下不会——沙箱里的 jj 只在有人执行 `jj config set --repo` 时才会创建这个文件，普通 `jj log`/`status`/`workspace add` 都不会。

> ⚠️ 一条纪律更新：父话题说"别在沙箱里跑 `jj log`/`jj status`"——**范围要再扩大**。我实测 `jj --version` 也会触发（它根本不需要仓库）。**在沙箱里，任何 jj 子命令都会触发。**（我自己在查 jj 版本时踩中了一次，13:03:15 把共享仓库的 `config-id` 又打回 `node:node 0600`；已立刻 `chmod 0666` 修复——见文末"我自己踩了一次"。）

## 第一步：基于真实代码的答案（不再是推断）

**先解决"读不到代码"的问题**：本地 main 停在 `5fbf903c`，确实没有 #237。但 **#237 的实现就在共享仓库的 `topic/d80e8798` 分支上**，不用 GitHub 也能读（沙箱里 `gh` 未登录、仓库是私有的，API 404）：

```bash
git -c safe.directory='*' -C /de808b13-… diff main...topic/d80e8798 -- backend/app/domain/workspace/service.py
```

以下答案基于这份真实源码。

### Q1：`_repair_modes()` 遇到"属主 1000、mode 0600"的文件会怎样？

**被吞掉，不崩，也不修**。原文：

```python
try:
    os.chmod(entry.path, entry.stat().st_mode | 0o444)
except OSError:
    pass
```

后端是 uid 1001，对 node 属主的文件 chmod 抛 `PermissionError`（`OSError` 子类）→ 命中 `except OSError: pass`。所以既不会报错，也没有任何日志——**这正是它难被发现的原因**。

补充两点，比"够不着"更关键：

- `_repair_modes` **只加读位**（`| 0o444`），从不加写位。所以哪怕 chmod 成功了，一个 node 属主的 `config-id` 对后端**仍然不可写**。
- 而 jj 需要的不只是"读得到"：它会在 `<store>/.tmpXXXX` 写临时文件再 rename 覆盖。**所以"修 mode"永远赢不了"换 inode"**。

### Q2：`_share_jj_modes()` 是只在 `_jj()` 成功后调用，还是失败后也调用？

**失败后也调用**，#237 特意处理过，注释写得很清楚：

```python
# Before the returncode check: a FAILED jj call still writes operations, and
# those unreadable files break the sandbox just as thoroughly.
_share_jj_modes(repo, since=started)
if result.returncode != 0:
    raise ValidationError(...)
```

**所以父话题担心的那个死锁不存在**（这条推断不成立，以代码为准）。但结论不变——因为真正的死锁在另一处：**修复动作本身没有权限**（Q1）。`_share_jj_modes` 跑了、跑完了、什么也没修成。

## 机制（已完整复现，不是推断）

### `config-id` 到底是什么

实测（jj 0.42.0）：

- 全新 `jj git init` **不会**产生 `config-id`；`jj log`/`jj status` 也不会。**只有 `jj config set --repo` 会创建它**——也就是 `_ensure_jj()` 里那两行设 `user.name`/`user.email`。
- 它的内容是一个 20 位十六进制 id（本仓库是 `7dedcf902b99dde332d6`），指向**调用者自己 HOME 下**的 `~/.config/jj/repos/<id>/`（`metadata.binpb` + `config.toml`）。`metadata.binpb` 里存的是**该用户看到的 `.jj/repo` 绝对路径**（protobuf 字段 1）。jj 自己的说明：`Per-repo config is stored in the same directory as your user config for security reasons.`

**即：per-repo 配置是按 $HOME 存的，而 store 是跨 uid 共享的——这是设计层面的错配，不是谁写错了代码。**

### 触发条件（精确到"什么时候会、什么时候不会"）

| 当前用户 `~/.config/jj/repos/<id>/` | jj 的行为 |
|---|---|
| 存在，且 `metadata` 里的路径 **等于**当前 `.jj/repo` 路径 | 什么都不做，`config-id` **不被触碰**（mode/mtime 都不变） |
| **不存在** | 打印 `Warning: Per-repo config not found. Generating an empty one.`，并**用 tmp+rename 重建 `config-id`**（id 内容不变，但 inode 换新 → **属主＝调用者、mode 硬编码 0600**） |
| 存在但路径**不匹配** | **硬失败 `exit 255`**：`Internal error: Failed to determine the secure config for a repo` |

沙箱容器每轮是新的、HOME 是新的 → **每个新容器里第一条 jj 命令必然落进第二行** → `config-id` 变成 `node:node 0600` → 后端（1001）读不到 → 后端侧**所有** jj 调用 `exit 255`。

### 后端侧的爆炸半径（比"新话题起不来"更大）

`config-id` 不可读时，**每一条** jj 命令都会 255，包括 `jj --version`。而 worktree 的 `.jj/repo` 只是一个指向共享 store 的相对路径文件，所以：

- `_ensure_worktree` → `jj workspace add` 失败 → **新话题起不来**（11:53 / 12:28 就是这个）；
- `_catch_up_with_branch` → `jj diff -s` / `jj log` 失败 → **老话题读文件也炸**；
- `snapshot_worktree` → `jj commit` 失败 → **agent 这轮的改动存不进版本库**；
- `git_http.py` 的 `jj git import` 失败 → **推送路径也受影响**。

**"全项目停摆"是字面意思。**

### #237 是这个新形态的前提（代码级证据，不再是"可能性"）

父话题说的因果**成立**，而且比"agent 终于会跑 jj 了"更直接——#237 主动把 store 目录放成了 777：

```python
try:  # jj writes `<store>/.tmpXXXX` before every command it runs
    os.chmod(store, os.stat(store).st_mode | 0o777)
except OSError:
    pass
```

**沙箱能在共享 store 里 rename 出一个新 `config-id`，正是这一行给的能力。** #237 之前 store 目录是 0755/1001，沙箱的 jj 会在那里硬失败（就是它要修的症状），而不是"悄悄把文件占为己有"。所以：**#237 把一个"agent 侧的显性失败"换成了"后端侧的隐性停摆"**——它的方向没错，只是权限模型不完整。

## 四个候选方案的裁决

### A. 后端按自己身份重建该文件 —— ✅ 可行，且"可再生"已证实

父话题要求先论证可再生。**结论比"可再生"更强：它是可有可无的。**

实测：把 `config-id` 直接 `rm` 掉之后，`jj log` / `jj workspace add` / `jj commit` **全部 exit 0、无警告、也不会把它重新创建出来**。它不是仓库数据（op_store/store 才是），只是一个指向"某个用户 HOME 里的配置目录"的索引。删掉唯一的损失是**那份 per-repo 配置（也就是 `user.name=芝士` / `user.email`）不再生效**。

后端**有能力**重建它：`.jj/repo` 目录属主是 1001（后端自己），POSIX 下 **unlink 只看目录的写权限，不看文件属主**——所以后端删不掉它是假象，它只是"改不动"，**删得掉**。

**但 A 单独用不够**（父话题第 3 问）：后端重建完，下一个新容器的第一条 jj 又会把它翻回去 → 无限翻烧饼，每次翻烧饼都有一个"后端调用会失败"的窗口。A 只能当**兜底自愈**，不能当主方案。

### B. 收紧 `.jj/repo` 权限 —— ❌ 实验否决

实测（store 目录对调用者不可写、`config-id` 本身 0444 可读）：

```
$ jj log            # 只读命令
Internal error: Failed to determine the secure config for a repo
Caused by: Permission denied (os error 13) at path ".../.jj/repo/.tmpNbvU6V"
exit=255
```

**只读命令也死。** 因为"重建 config-id"发生在配置解析阶段，早于命令本身，且 jj 把写失败当致命内部错误。父话题问"jj 只读命令在无法写 config-id 时会不会直接报错"——**会，而且是 255 硬失败**。B 出局。

### C. 统一 uid —— ✅ 有效，但**已不必要**

同 uid 下谁重建都无所谓（0600 也读得到），确实从根上消灭循环。但它需要管理员改容器 uid 或 chown 整个仓库，是本卡之外的协调成本。**既然方案 E 不需要任何管理员动作就能达到同样的"根上消灭"，C 降级为备选**（若 E 出意外再启用）。
> 顺带排掉一个看着可行的思路：**加共享 group 没用**——jj 写的是 0600，group 位是 0。

### D. 准确的失败提示 —— ✅ 要做

现在用户看到的是「AI 服务返回错误：tmux 后端启动失败」。`_jj()` 把 stderr 原样塞进 `ValidationError`，一路冒泡成"AI 服务错误"。要在 `_jj()` 里识别 `Failed to determine the secure config` / `config-id` + `Permission denied`，换成一句带**具体路径 + 一句修法**的工作区错误，并且**不要**归类成 AI 服务故障。

### E. 让 `config-id` 根本不存在 —— ⭐ 推荐

`config-id` 唯一的来源是 `_ensure_jj()` 里那两行 `jj config set --repo`。改成**每次调用用环境变量注入身份**：

实测（删掉 `config-id` 和整个 `~/.config/jj/repos` 之后）：

```
$ JJ_USER=芝士 JJ_EMAIL=cheese@zhishi.local jj commit -m 'env identity test'
$ jj log -r @- -T 'author.name() ++ " <" ++ author.email() ++ ">"'
芝士 <cheese@zhishi.local>
$ ls .jj/repo/config-id
ls: cannot access '.jj/repo/config-id': No such file or directory   ← 没有被重新创建
```

**身份保住了，文件没了，属主翻转这个失败类整体消失。** 而且沙箱侧不需要任何配合——agent 怎么跑 jj 都不会把它变出来。

> 备选（若某天必须保留 per-repo 配置）：给沙箱**预置** `~/.config/jj/repos/<id>/metadata.binpb`，内容写容器侧看到的 store 路径，jj 就不会重建。实测有效（手写的 protobuf 也认）。但上表第三行是个陷阱：**路径写错 = 沙箱里所有 jj 硬失败**。E 不碰这个雷区，所以优先 E。

## 已落地的改动（代码写完了）

wangchangxin 已拍板：**不动盒子，等 PR 合并部署**。所以下面就是唯一的修复路径；在部署完成之前，盒子上的 `config-id` 仍可能被任何一次 agent 的 jj 调用打回 0600。

**`backend/app/domain/workspace/service.py`**

- 新增 `_jj_store()`（**与 #237 同名同实现**，为的是合并时直接留一份就行）和 `_drop_repo_config_id()`：**每次 `_jj()` 调用之前**删掉 `<store>/config-id`。删而不是 chmod——unlink 只看**目录**写权限（后端是目录属主），chmod 却要求是**文件**属主。放在调用**前**而不是后：投毒状态下 jj 本身已经跑不动了，事后修没有意义。
- `_ensure_jj()` 不再写 per-repo 配置（那两行 `jj config set --repo` 正是 `config-id` 的唯一来源）；身份改由 `_jj()` 注入 `JJ_USER`/`JJ_EMAIL`。**补一条实测**：这两个环境变量的优先级**高于用户级 `config.toml`**，所以盒子上后端用户 home 里有没有 jj 配置都不影响作者身份。（注意 `subprocess.run` 传 `env` 会**替换**整个环境，代码里是基于 `os.environ` 拷贝的。）
- 新增 `_jj_failure_message()`：secure-config 类失败翻译成带**具体文件路径 + 一句修法**的中文错误；其它 jj 错误措辞原样不动。

**`backend/app/domain/agent/platform_failures.py`（D）**

新增 `workspace_vcs_perms` 分类。原来这类失败落进最后的兜底分支、渲染成「AI 服务返回错误」；现在有自己的标题「工作区版本库权限异常」，正文明确"项目文件和版本历史都没有受影响、平台会自动清掉这个文件并恢复"，`retryable=True`，且**不泄露内部路径**（路径只进后端错误/日志）。

**测试**：`backend/tests/unit/test_jj_config_id.py`（7 条）+ `backend/tests/unit/test_platform_failures.py`（新增 4 条）。unit、不碰 DB，但**依赖真的 jj 二进制**（沿用 #237 `test_sandbox_vcs_perms.py` 的做法，不 mock）。覆盖：

- 建仓之后**根本不存在** `config-id`；
- 没有 per-repo 配置，commit 作者仍然是「芝士 cheese@zhishi.local」；
- **投毒（0000 不可读）后 `_jj()` 能自愈并成功**——复现 11:39 的原始故障；
- **投毒后新话题仍然起得来**（直接测 `_ensure_worktree`，即"所有新话题起不来"那条路径）；
- 修复**只**删这一个文件：`store/`/`op_store/`/`index/` 逐项比对不变；
- 删不掉时报出准确原因（含路径）并被分类成 `workspace_vcs_perms`，用户看到的文案里**没有**「AI 服务」；
- 普通 jj 错误（`jj log -r no-such-revision`）措辞保持 `jj log failed:` 原样。

**基线提醒（合并时要注意）**：本工作区基于 `5fbf903c`（**没有 #237**）。改动正好落在 #237 也改过的 `_jj()`/`_ensure_jj()`，两阶段采纳推 PR 前会先同步 GitHub main，**届时这两处必然冲突，需要手工合并**：保留 #237 的 `_share_jj_modes(repo, since=started)` 调用，在其前后分别保留本卡的 `_drop_repo_config_id(...)` 与 env 注入；`_jj_store()` 两边同名同实现，留一份即可。

## 失败模式 / 发现时延 / 回滚

| 风险 | 症状 | 多久发现 | 回滚 |
|---|---|---|---|
| env 身份没注入成功 | `jj commit` 报 no user/email，或提交作者变成 ` <>` | 下一次 `snapshot_worktree`（一轮之内）；测试里已覆盖 | 恢复那两行 `jj config set --repo` 即可，`config-id` 会自动重建 |
| unlink 误删别的文件 | —— | —— | 只按**固定文件名** `config-id` 删，不做任何模式匹配/遍历删除 |
| 盒子上有人依赖 per-repo 配置 | 其身份配置失效 | —— | 该配置只含 name/email，改用用户级 `jj config set --user` |

**关键安全性**：本改动**不碰 `store/`、`op_store/`、`index/`**——版本历史一个字节都不动。删的只是一个"指向某人 HOME 配置目录"的索引文件。

## 怎么验证这类停摆不会再发生

落地后在盒子上（或任意沙箱里）跑：

```bash
# 1. 这个文件应当不存在
ls -l <workspace_root>/<project_id>/.jj/repo/config-id   # 期望：No such file or directory

# 2. 主动挑衅：在沙箱里跑 jj，再看它有没有被造出来
jj --version >/dev/null 2>&1
ls -l <workspace_root>/<project_id>/.jj/repo/config-id   # 期望：仍然不存在

# 3. 后端侧仍然正常
#    新话题能起来 = jj workspace add 成功
```

第 2 步是**关键判据**：今天同样这条命令会把文件打回 `node:node 0600`。

## 检查结果（如实报告）

沙箱里按 CLAUDE.md 的办法起了 PG+Redis（`.claude/scripts/dev-db.sh start`）后跑的：

- **ruff**：改动的 4 个文件 `All checks passed`（`ruff format` 改过一次格式，已收进改动）。
- **pyright**：`app/domain/workspace/service.py` + `app/domain/agent/platform_failures.py` → **0 errors, 0 warnings**。
- **`pytest tests/unit`**：**2543 passed / 22 failed / 1 skipped**。22 条全部在 `test_machine_service.py`(21) 和 `test_tmux_control.py`(1)，原因是沙箱**缺宿主机命令**——`FileNotFoundError: 'ssh-keygen'` 与 `'kill'`，正是 CLAUDE.md 里记着的那两条已知沙箱缺口，**与本卡无关**。
- 起 DB 之前另有 3 条 ERROR（`test_idle_reap`、`test_task_ai_advice_routes`），是连不上 5433 导致的；**起了 PG 之后全部消失**，所以确认是环境不是代码。
- 本卡新增的 11 条测试全绿。

`bash .claude/scripts/check.sh --full`（起了 PG+Redis 之后跑的完整一轮）：

```
PASS: ruff
PASS: pyright
PASS: exactly one migration head
FAIL: pytest     23 failed, 3639 passed, 31 skipped, 46 rerun in 431.66s
Result: 3/4 passed
```

**四项里三项绿，红的只有 pytest，且没有一条 SKIP**（`Result` 行是 `3/4 passed`，不是"假绿"——这次 pyright/pytest 都真的跑了）。check.sh 只打印了最后 19 条失败，所以我把 23 条**逐条查清了**，分两次单独跑、看完整输出：

| 单独跑 | 结果 | 失败明细 |
|---|---|---|
| `tests/unit`（起了 PG+Redis） | 2543 passed / **22 failed** | 21 条 `test_machine_service.py` + 1 条 `test_tmux_control.py`，全是 `FileNotFoundError: 'ssh-keygen'` / `'kill'`——沙箱缺宿主机命令 |
| `tests/integration` + `tests/contract` | 1093 passed / **1 failed** | 只有 `test_market_api.py::test_market_lists_ai_and_compute_pools`，断在 `ai_default["available"]`（市场目录里默认 AI 池在本环境未部署），与本卡无关 |

22 + 1 = 23，**和全量跑的数字对得上，没有第 24 条**。这三个文件都跟 jj / 工作区 / 错误分类毫无关系，本卡改的 4 个文件相关的用例全绿。

### 解完冲突之后又跑了一遍（以 main 为底重贴之后）

- **ruff**：`app/` + `tests/` 全量 `All checks passed`。
- **pyright**：改动的两个 app 文件 **0 errors, 0 warnings**。
- **`pytest tests/unit`**：**2543 passed / 22 failed / 1 skipped**——失败数和文件分布跟合并前**一模一样**（21 条 `test_machine_service.py` + 1 条 `test_tmux_control.py`，缺 `ssh-keygen`/`kill`），说明这次合并没有引入任何新问题。
- **专门验证没弄坏 #237**：把 main 上的 `tests/unit/test_sandbox_vcs_perms.py`（#237 自己的 6 条测试，我的分支还没有这个文件）取过来对着合并后的代码跑，**6 passed**；跑完就把这份临时副本删了，合并时它会随 main 一起进来。

> 一个环境插曲，如实记录：中途容器被重建过一次，`~/.local/share/uv` 里的 Python 3.13 和 `.venv` 的解释器一起没了（`/work` 是挂载进来的，改动都还在）。重新 `uv run` 装回 196 个包之后继续，`dev-db.sh` 的 PG/Redis 也重下了一遍。**沙箱里的 `cheese` 仍是 8/7 那份、没有 `await`**——#240 的修复要等这个容器换代才吃得到。

> 顺带一条对 CLAUDE.md 的更正：那里记着沙箱里还会有约 43 条因**没有 git identity** 而失败的用例（`test_workspace.py`/`test_upstream.py`/`test_accept*.py`/`test_git_http.py`…）。这次**没有出现**——全量 3639 passed，说明那条环境缺口已经不存在了。

递卡时闸门跑的是 `check.sh --no-tests`（只有 ruff+pyright），这两项都绿。

## PR #242 的合并冲突（已解）

平台在"合并冲突"这条路径上只写卡片 note、不 summon，所以我一直没被叫醒；父话题手动来叫了。冲突文件是 `backend/app/domain/workspace/service.py`，处理过程：

1. **先把真正的 main 拿到手**。本地共享仓库的 `main` 和 `upstream/main` 都还停在 `5fbf903c`（10:40），沙箱里也没有 GitHub 凭据（`git ls-remote` 直接 `could not read Username`）。所以走平台自己的通道触发了一次上游同步（`POST /projects/{id}/upstream/sync`，用的是后端的凭据，不是我去 fetch），拉进来 **12 个提交**，`main` 现在到 `a2691f2a`，`#237`(`8f68eed3`)、`#240`、`#241`、`#244`~`#246`、`#249` 都在里面了。
2. **解法不是"逐块挑冲突标记"，而是以 main 为底重贴**：把 `service.py` 整份换成 main 的版本，再把本卡的三处改动重新贴上去。这样**不可能**误伤别人刚落地的东西。
3. **机器核对过"只多不少"**：`diff main版 我的版` 里只有 **3 行被删**，正是本卡有意要删的那 3 行（`_ensure_jj` 里两行 `jj config set --repo`，以及被换成 `_jj_failure_message()` 的那行 `raise`）；其余全是新增。#237 的 `_jj_store`/`_repair_modes`/`_share_jj_modes` 一行没动，`_jj()` 里 `_share_jj_modes(repo, since=started)` 仍在原位（我的 `_drop_repo_config_id()` 加在**调用之前**，两者各司其职：它补读位，我防属主翻转），`sandbox_vcs_mounts` 里那句 `_share_jj_modes(wt)` 也还在。#246 给 `git_log` 加的 `topic_id` 分支同样完整保留。
4. 我碰过的另外两个非新增文件（`platform_failures.py`、`test_platform_failures.py`）**main 从我的基点之后一行都没改过**，机器核对丢行数 = 0。

> 为什么这样解之后 GitHub 那边就不冲突了：三方合并里，main 和我这边对 #237/#246 那些块做的是**完全相同**的修改，git 对"两边改得一样"不判冲突；我额外新增的行只在我这边，直接落进去。

## 顺带更正一条：#240 的根因和简报猜的不一样

父话题让我写明白——**`a0b3c789` 查出来的根因不是"镜像节奏 vs 后端节奏"**：部署环境里的 `cheese` 根本不是从沙箱镜像来的，而是宿主机 `/home/nictheboy/cheese-proxy/sandbox/cheese` 的只读 bind-mount，停在 8 月 7 日 18:37、没有任何流水线更新它；SKILL.md 却是后端从自己镜像里读的。它的修法是不再挂载、改成每轮 `docker cp` 从后端镜像推进容器。顺带它还否掉了简报的候选方案 B（"让镜像构建在 `backend/sandbox/cheese` 变化时必然触发"——那**已经是现状**）。

对本卡的直接影响：我这个容器里的 `cheese` 仍是那份 8/7 的副本、**没有 `await`**，所以长任务只能同步等；另外 `a0b3c789` 正是本卡时间线里 11:53「起不来」的那个子话题，它是这次停摆的受害者之一。

## 现状（已拍板：不动盒子）

- 共享仓库的 `config-id` 现在是 `node:node 0666`（我 13:03 误触发后立刻修回的），**平台可用，但仍然靠这个手工权限撑着**——任何 agent 的下一条 jj 命令都会再打回 0600。
- 我提过一个可以立刻止血的一次性盒子操作（删掉 `config-id` + 给后端用户配用户级 jj 身份），**wangchangxin 选了「不动盒子，等 PR 合并部署」**。所以：**在部署完成之前，这条风险仍然是敞开的**——任何一个 agent 跑一次 jj，就会再次让所有话题起不来，届时仍需要人手工 `chmod`（属主是 node 时，任何 uid 1000 的进程都能改）。
- 部署之后这条风险归零：文件不再存在，也不会被重新创建。

## 我自己踩了一次（如实记录）

查 jj 版本时我在共享仓库目录下跑了 `jj --version`，13:03:15 把 `config-id` 打回 `node:node 0600`——**父话题的纪律范围应该扩大到"任何 jj 子命令"，`--version` 也算**。发现后立刻 `chmod 0666` 修回（属主正好是 node＝我自己的 uid，所以修得动）。

坏事变好事的部分：这次误触发 + 后续在 `/tmp` 里的**一次性 throwaway 仓库**受控实验（没有再碰共享仓库），才把上面那张"什么时候会重建、什么时候硬失败"的表测全。后续所有实验都在独立仓库里做。
