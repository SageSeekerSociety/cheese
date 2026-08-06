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

### 第一次递验收卡被质量闸门打回，追加了一处相关修复

平台质量闸门在真实执行时（一个没有可继承 `.venv`、也没有网络/本地 Python 解释器缓存的宿主上）跑出了闸门日志里的失败：

```
==> ruff check + format
error: ... Failed to download https://github.com/astral-sh/python-build-standalone/... dns error
  FAIL: ruff (lint or format)
==> pyright
error: ... 同样的下载失败 ...
  SKIP: pyright (scratch-venv sync couldn't get it running in time — environment limitation, not a code issue)
```

**先排查是不是这次改动导致的**：本地对比过，`uv run` 无论带不带 `--no-sync`，只要指向的 scratch venv 是全新的，都要先解析/下载一个匹配的 Python 解释器——这一步跟 `--no-sync` 无关，在没有解释器缓存又没有网络的宿主上，改动前的旧代码（`uv run --no-sync ruff ...`）会在同一步以同样方式失败。也就是说，**这不是这次改动引入的新问题**，闸门宿主本来就没有可用的 `.venv/bin/ruff`，落进了 `run_ruff()` 的兜底分支，暴露了一个原本就存在、跟"硬编码 --no-sync"平行的另一个缺口：`pyright` 那一段早就为"scratch-venv 网络不可用"这个场景设计了 `timeout` + 降级为 SKIP 的兜底（见 `check.sh:105-119` 原有逻辑），但 `run_ruff()` 一直没有对等的兜底，同样的失败在 ruff 这一步只会是硬 FAIL，把整个闸门拖垮。

**追加修复**（仍在 `run_ruff()` 和它的调用处，没有碰其它函数）：`run_ruff()` 兜底分支里，只有走到 scratch-venv 路径（`UV_RUN` 不是 `uv run --no-sync`）时才套 `timeout 120`；调用处对应加了跟 pyright 完全一致的判断——`.venv/bin/ruff` 存在或探测成功（继承 venv 可用）时失败按真实 FAIL 处理，走 scratch-venv 路径时失败按环境限制降级为 SKIP。这是复用代码里已有的既定模式，不是新设计。

**验证**：
- 正常路径（真实 `uv`，有网络/有解释器缓存）：`check.sh --no-tests` 跑两遍，ruff/pyright 均 `PASS`，跟改动前行为一致。
- 复现闸门失败场景：用一个整体屏蔽了 uv 网络下载路径的 `uv` 桩程序（返回跟闸门日志一致的 DNS 错误）重跑，改动前会是 `FAIL: ruff` → `exit 1`；改动后是 `SKIP: ruff (... environment limitation ...)`，整体 `exit 0`，跟 pyright 现在的降级行为一致。

这算是对原任务范围（只改 `run_ruff()` 兜底分支这一个函数）的小幅延伸，不是顺手重构其它部分——延伸原因是闸门用真实执行验证出了同一个函数里的一个相邻缺口，且按现有代码已经确立的 pyright 处理方式对齐，判断不需要另外发起决策请求。

### 下一步

wangchangxin 已拍板：本地跑不了 DB/Redis 依赖的验证属于环境限制，真正的验证交给 CI（`.github/workflows/test.yml`，PR/合并到 main 时用真实 Postgres/Valkey 跑全量 pytest），不必再等有 DB 的环境补跑；已重新递验收卡给他采纳合并。

递交前核对过本次改动范围：跟共享 main 相比，工作区里的实质改动集中在 `.claude/scripts/check.sh` 的 `run_ruff()` 函数和它的调用处（硬编码 `--no-sync` 的原始 bug + 上面追加的 ruff 环境限制降级），没有夹带无关改动；另外把话题拆分时自动生成、放在仓库根目录的 `doc-topic.md` 清理掉了（内容已迁到规范位置 `docs/topics/check-sh-ruff-no-sync.md`，属于本话题文档整理）。合并时仍需按前面的提醒对一下「沙箱切换杀进程」子话题是否有 `--no-sync` 那一行的重复实现，去重后再应用；追加的 ruff 降级逻辑是本子话题独有的，预计对方那边不会有重复。
