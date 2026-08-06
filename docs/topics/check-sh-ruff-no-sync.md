## 状态：已完成，待合并

### 目标

修复 `.claude/scripts/check.sh` 里 `run_ruff()` 兜底分支硬编码 `uv run --no-sync` 的 bug——全新 checkout（没有可继承的 `.venv`）场景下，scratch venv 还没 `uv sync` 过，`--no-sync` 会让 ruff 这一步必然假性 FAIL。

### 约束

范围很小，只改 `run_ruff()` 里 `.venv/bin/ruff` 不存在时的那一个兜底分支，不碰其它逻辑，不顺手重构 check.sh。

### 改动

`.claude/scripts/check.sh:83`：把硬编码的 `uv run --no-sync ruff "$@" --cache-dir "$RUFF_CACHE_DIR"` 改成复用第 67-73 行探测出的 `UV_RUN` 数组：`"${UV_RUN[@]}" ruff "$@" --cache-dir "$RUFF_CACHE_DIR"`——跟 pyright/pytest 已有的处理方式（`"${UV_RUN[@]}" pyright` / `"${UV_RUN[@]}" pytest ...`）保持一致。`.venv/bin/ruff` 存在时的快路径（`check.sh:80-81`）原样未动。

### 验证

本沙箱本身就没有任何可继承的 `.venv`，天然就是 bug 描述的那个"全新 checkout"场景，不用额外搭建：

- 复现了旧 bug：手动跑 `uv run --no-sync ruff ...`（指向一个全新 scratch venv）报 `error: Failed to spawn: ruff — No such file or directory`——证实这不是假设性问题，是真实会触发的失败。
- 验证修复：跑 `bash .claude/scripts/check.sh --no-tests`，ruff 走的正是新建 scratch venv 的兜底分支，`PASS: ruff`；pyright 同样 `PASS`；pytest 因沙箱无 DB 走 `--no-tests` 的 SKIP（与本次改动无关）。连续跑两次结果一致。
- `.venv/bin/ruff` 存在时的快路径：本沙箱没有真实继承 `.venv`，无法端到端跑这条路径；用一个 mock 的可执行 `.venv/bin/ruff` 单独验证了分支选择逻辑本身没被这次改动影响（改动只在 `else` 分支里，`if [ -x ".venv/bin/ruff" ]` 这一行完全未动）。如实说明：这条路径没有做到完全端到端验证，只做了逻辑层面的确认。

### 已知情况：与「沙箱切换杀进程」子话题的重复修复

主话题通知：「沙箱切换杀进程」子话题在自己的工作区跑 task check 时，顺手修了同一处 `check.sh` 的 `--no-sync` bug。两个子话题在各自隔离的工作区里独立发现、独立修了同一行代码。

- 已用 `cheese api GET /projects/{project_id}/file/raw?path=.claude/scripts/check.sh` 直接读了共享 main 上的内容确认：`check.sh:83` 目前仍是修复前的旧代码（`uv run --no-sync ruff ...`）——**两边的修复目前都还没有合并到共享 main，此刻没有真正冲突**。
- 无法拿到「沙箱切换杀进程」子话题那份具体 diff 做逐行对比——子话题工作区互相隔离，这是本项目里已经反复确认过的限制（同类情况在父话题文档里对「登录双身份收尾」等其它子话题也有记录）。只能说明本子话题自己的修法和理由（见上）；如果对方的改法在语义上不同（比如改的行号、改的表达方式不一样），需要合并的人自己去比对两份 diff。
- **提醒合并负责人**：`check.sh` 这一处（`run_ruff()` 兜底分支复用 `UV_RUN`）大概率有两份功能等价的独立实现，合并时需要去重，只保留一份，不要两份都应用（会产生冲突或冗余改动）。

### 下一步

改动已验证完毕，可以合回共享 main；无待决策事项。合并时按上面的提醒对一下另一份重复实现。
