> 状态：**代码完成，已 rebase 到 main #299（`f9a77284`）并解完语义冲突**。基线先后挪过五次。
> 范围收窄为两件事：SKIP 不该判绿（本话题原题）、守卫把 check.sh 打死（rebase 后发现的 main bug）。
> **`pending_gate` 出边已撤出本卡**——`void` 已随 #298 进 main，那件事就此归它。

## 问题

平台质量门禁跑 `check.sh --no-tests`，而脚本的汇总行是 `Result: $PASS/$((PASS+FAIL)) passed`——**SKIP 不进分母，也不影响退出码**。门禁容器 DNS 挂掉、Python 工具链全下不动时，实际只跑了 ruff，输出 `Result: 1/1 passed, 3 skipped`，退出 0，**门禁判绿放行**。假的正是给人当采纳依据的那块绿。

**这不是理论风险，2026-08-11 13:30 前后线上实发两例**（`gate_output` 里 SKIP 原因均为 `failed to lookup address information: Temporary failure in name resolution`，盒子 DNS 挂了 uv 拉不到 CPython；同时段 Actions 的 Build/Deploy 也一直 queued）：

| 卡 | 闸门结论 | 实际 |
|---|---|---|
| `14a2f2d3`（对标 Buzz 找差距） | `Result: 0/0 passed, 4 skipped` (exit 0) | **一条检查都没跑**，卡已放行进 pending 等采纳 |
| `048e7ec8`（记忆机制形同虚设） | `Result: 1/1 passed, 3 skipped` (exit 0) | 只跑成了 ruff |

所以**采纳的必要条件不能只看 `PASS>0`**：`1/1` 和 `0/0` 只差一个量级，不差性质——两者都是没验证 pyright/pytest 就放行。

## 改法：把「没跑成」从「跳过」和「不通过」里拆出来

三态，语义互不重叠：

| | 含义 | 进分母 | 退出码影响 |
|---|---|---|---|
| PASS / FAIL | 检查跑了，对代码有结论 | 是 | FAIL → 1 |
| **BLOCKED** | 检查**没能跑起来**，对代码没有结论 | **是** | 仅 `--strict` 下 → **2** |
| SKIP | 我们主动要求跳过（`--no-tests`） | 否 | 无 |

`--strict` / `CHECK_STRICT=1` 只有门禁开，本地不开——工具装不上照样不拦开发者。退出码 **2** 刻意区别于 1：它说的不是"代码坏了"，是"检查没发生"。

另加一条**空转兜底**：strict 下 `PASS+FAIL == 0`（一项真检查都没跑过）同样 exit 2。当前所有跑不起来的路径都已归到 BLOCKED，所以这条今天走不到；它防的是将来某个 `--skip-<x>` 让全部检查落进 SKIP，`Result: 0/0 passed` 再次判绿——正是 `14a2f2d3` 那张卡的形态。写法照 <&.claude/scripts/check-action-pins.sh>（`.github/workflows` 不存在就直接 FAIL 并说明什么都没扫到），而不是"没扫出违规就算过"。

计分与退出码这段逻辑抽成了纯函数 `verdict()`，`bash .claude/scripts/check.sh --self-test` 直接驱动它——不需要工具链、不需要仓库，任何机器都能跑，9 个用例含上面两张线上卡的形态。

**调用方跟上**：<&backend/app/domain/review/gate.py> 把 exit 2 和 exit -1（命令压根没起来）都归到 `GateOutcome.blocked` → 卡片进 `gate_blocked`，不递给验收人，也不说成"未通过"；给芝士的 nudge 分两种话说——红了是去改代码，没跑成是去把环境弄起来。<&frontend/src/views/WorkspaceView.vue> 有独立卡面「平台检查没跑成」，明写"对这次改动没有结论"。

## rebase 到最新 main 后发现的真 bug（本轮新增）

main 的 #264 往 check.sh 里加了三个仓库守卫（repo rules / action pins / migration fork）。它的写法在 `set -euo pipefail` 下有两个缺陷：

1. **守卫报违规 → check.sh 当场暴毙**。`else` 分支里为了打印输出又跑了一次守卫，这个未加保护的非零状态触发 errexit：`FAIL: repo rules` 印不出来，汇总行印不出来，后面的 migration fork 和 pytest 全不跑，退出码是守卫脚本自己的码而不是 1。
2. **守卫自己不见了（127）→ 被当成代码不通过**。芝士收到"你的代码红了，去修"，而真相是检查器缺失——正是本话题要消灭的那种混淆换了个入口。

修法：守卫只跑一次、捕获输出；0 → PASS，127 → **BLOCKED**，其余 → FAIL。见 <&.claude/scripts/check.sh> 的 `run_guard`。

@wangchangxin 独立复现确认了这条（守卫是他写进 #264/#266 的），并划清了边界：只有 `:170` 和 `:179` 两处受影响，`migration fork` 那段用的是 `X="$(...)" && RC=0 || RC=$?`（在 `||` 左侧，`set -e` 不生效）本来就安全，`((++FAIL))` 前缀自增也安全。他补的约束——**守卫违规时汇总行必须还能印出来、退出码必须是 check.sh 自己的 1 而不是守卫的原始码**——由 `test_a_repo_guard_violation_is_a_failure_and_does_not_abort_the_run` 钉住。这条最阴的地方不是少印一行 `FAIL:`，是让 `Result:` 行彻底消失，而闸门正是靠那行判读的。

## 撤出本卡的一处：`pending_gate` 的人工出边

@wangchangxin 实测的卡 `b020d5d3`（话题 `fbea1a5e`，芝士 337 条消息的活）卡死在 `pending_gate`：`gate_passed_at` 为 null、`gate_output` 为空串——闸门进程没跑完就没了，而 `finish_gate` 是这个状态**唯一**的写入方。三个终结操作全被拒：`accept` 说"检查还在进行中"、`reject` 只收 `pending`、`revoke` 只收 `accepted`。递不出来也扔不掉。这和前两头同源：闸门跑挂了，系统没有一个诚实的词描述"我没能判定"，于是要么假装通过，要么假装还在跑。

我一度把 `reject` 从递卡互斥集合派生放宽到 `pending_gate`。**已撤回**，理由是我自己写的那条原则打自己：排除 `gate_failed`/`gate_blocked` 用的是「没有验收人见过这张卡，不能标成 rejected」，而这句话对 `pending_gate` 只会更成立——闸门都还没跑完。我当时的判据（会不会挡住话题重递新卡）能自洽，但它证明的是**该有出口**，不是**该叫 rejected**。

出口归卡 `77dd4005`（话题 `cadefa9d`）的 `void`：新动词让"作废"≠"驳回"、复用 `revoked` 终态不增状态格；授权到验收人/owner/lead（卡死时往往正是验收人不在，只认 `reviewer_handle` 那版仍然是死的）；并且带了根因那半边（`gate_started_at` + 扫底自动判死），让人工出口成为兜底而不是主路。一个死锁不该有两个出口。

同时撤回的还有 <&backend/app/domain/review/gate.py> 里 `_settle` 的"丢弃迟到闸门结果"保护——两张卡都写了同一份、语义一致，留 `cadefa9d` 那一份，避免文本冲突。

## rebase 到 #298：四处语义冲突怎么解的

`void` 那一整套（`void` 动词、`gate_started_at`、`gate_sweep` 扫底）已经随 #298 进了 main，它同时重构了 `gate.py`，和本卡的三态改动正面撞上。@wangchangxin 把冲突点列了出来，解法：

| 冲突 | main 的意图 | 本卡的意图 | 解法 |
|---|---|---|---|
| `_settle` 调用点 | 拿返回值 `settled` 判断"卡还在不在" | 传 `outcome` 而不是 `passed` | 两个都留：`settled = await _settle(..., outcome=outcome, tail=tail)` |
| `_once_visible` 签名 | 抽成通用回调 `action: Callable[[AcceptService], Awaitable[object]] -> bool` | 原来写死 `finish_gate` | **按 main 的形状**，三态结果包成 `lambda svc: svc.finish_gate(card_id=..., outcome=outcome, ...)` 传进去 |
| `_once_visible` 循环体 | `await action(...)` | 写死 `AcceptService(session).finish_gate(...)` | 同上，取 main |
| `services.py` import 块 | 新增 `from app.domain.review import archive` | `models` 那行加 `GateOutcome` | 两行都留 |

所以本卡在 `gate.py` 上最终只剩：模块 docstring 补第三种结局、`BLOCKED_EXIT_CODE`、`_run` 里的三态判定、`_settle` 的 `passed: bool → outcome: GateOutcome`、以及两段不同的 nudge 文案。`_once_visible` / `_mark_started` / `if not settled: return` 全部原样用 main 的。

@wangchangxin 记的一笔（值得留）：`git apply -3 --check` 报 CLEAN **只说明能做三方合并，不说明合并结果里没有冲突标记**——真落下来才看得见。

解完之后 #299（领域包解环与 import 守卫）也落地了，**再 rebase 到它是干净的**：上面四处的解法被 jj 带着走，不用解第二遍；#299 动的 `review/services.py` / `agent/chat.py` 和本卡那两处（import 一行 + `_OPEN_CARD_STATUSES` 加一个枚举值）自动合上，新加的 import 守卫在 `check.sh` 里报 PASS。

**这次 rebase 顺带看到的一件事，留给后人**：<&backend/app/domain/review/gate_sweep.py> 的 docstring 里专门有一节「「闸门没跑完」≠「检查未通过」」，说的正是本话题的命题；它当时没有状态格可用，只能让两者都落 `gate_failed`，靠 `GATE_ABANDONED_PREFIX` 这个文本前缀区分，并明确要求"读卡的代码请认这个前缀"。本卡加出的 `gate_blocked` 正是那个缺的状态格，`condemn()` 迁过去就能把前缀降级成纯粹的留痕。**没有在本卡里做**——`gate_sweep` 是 #298 的新代码、有自己的测试钉着 `gate_failed`，混进来只会让这张卡再被打回一次。

## 验收标准与证据

| 标准 | 证据 |
|---|---|
| 环境失败时门禁不判绿 | `test_blocked_checks_do_not_pass_under_strict`（exit 2, `3/6 passed, 3 blocked`） |
| `--no-tests` 显式跳过仍判绿 | `test_explicit_no_tests_still_green_under_strict`（`6/6 passed, 1 skipped`） |
| 本地模式行为不变 | `test_blocked_checks_are_reported_but_never_block_a_local_run`（exit 0） |
| 卡面能区分"没跑成"和"全过" | `gate_blocked` 独立状态 + 独立卡面 |
| 守卫违规不再中断全程 | `test_a_repo_guard_violation_is_a_failure_and_does_not_abort_the_run` |
| 守卫缺失算 BLOCKED 不算 FAIL | `test_a_missing_repo_guard_is_blocked_not_a_code_failure` |
| 一项都没跑不判绿（`0/0`） | `test_a_run_where_nothing_actually_ran_is_not_green_under_strict` |
| 计分逻辑可自检 | `test_the_scripts_own_self_test_passes` / `check.sh --self-test` |
| 放宽任一出口都不该绕过终态 | `test_an_accepted_card_still_cannot_be_rejected`（护栏留着，`void` 落地后同样适用） |

**全量套件这次跑完了**（main #299 基线，`dev-db.sh` 起真 PG/Redis，`pytest tests/ -n 4`）：

```
22 failed, 4201 passed, 31 skipped in 862.32s (14:22)
```

22 条失败**全部是 <&CLAUDE.md> 里记着的 no procps 缺口**（`test_machine_service.py` 21 条 + `test_tmux_control.py` 1 条，`FileNotFoundError: 'kill'`），与本卡无关。#298 基线上那一轮是 `23 failed / 4126 passed`，多出的一条见下。

**新发现的第三个沙箱缺口（CLAUDE.md 没记）**：`test_cheese_cli.py::test_await_log_lives_outside_the_worktree`。测试 monkeypatch `HOME` 之后断言日志路径跟着走，但 `_await_log_path` 先读 `CHEESE_AWAIT_LOGS`，而平台恰好往芝士容器里注了这个变量（<&backend/app/domain/agent/tmux_provider.py> 第 664 行 `"CHEESE_AWAIT_LOGS": "/home/node/.claude/cheese-await"`）。`env -u CHEESE_AWAIT_LOGS uv run pytest tests/unit/test_cheese_cli.py` → **22 passed**。测试不 hermetic，一行 `monkeypatch.delenv("CHEESE_AWAIT_LOGS", raising=False)` 就好，**不在本卡里改**（不是本话题的代码）。CI 里不会红——只有芝士容器有这个变量。

`test_market_api` 那条也在 CLAUDE.md 的已知清单里，本轮带 `ANTHROPIC_AUTH_TOKEN=dummy-for-test` 跑，没有出现。

另外单独跑的：`check.sh --self-test` → `self-test: ok`；真仓库 `check.sh --no-tests` → `Result: 6/6 passed, 1 skipped`（rc=0）；`ruff check` 全过、改动模块 `pyright` 0 errors。

沙箱侧提醒（不是代码缺陷）：`dev-db.sh start` 会让 uv 装一个一次性的 3.12 解释器，把 `backend/.venv` 指向的 3.13 托管解释器清掉，`.venv/bin/pytest` 报 `cannot execute: required file not found`，`uv sync` 重建即可——本轮又撞到一次（累计五次）。这恰好就是 `check.sh` 的探针要降级成 BLOCKED 的那种情形。跑套件前还要 `git config --global user.{name,email}`，否则约 43 条建真 worktree 的测试会红。

## 顺带核实的两件事

- **不需要 alembic 迁移**。`gate_blocked` 落在 `Enum(..., native_enum=False)` 的 varchar 列上，加枚举值不涉及 schema，本分支没有任何 `alembic/versions/` 文件——2-heads 风险不适用。
- **`gate_blocked` 不能进递卡互斥集合**。main #258 把互斥从 `pending/pending_gate` 扩到了 `pr_open/conflict`，`gate_blocked` 没进去（行为正确：卡作废、修好重递，和 `gate_failed` 同理），但那是 rebase 自动合出来的巧合。已在 <&backend/app/domain/review/services.py> 把注释写明确，防止后人当漏写"补"上去——补上去就把芝士永久锁死了。

## 递卡前的已知风险

门禁命令是 `bash .claude/scripts/check.sh --no-tests`（strict 由 `run_check_command` 注入 `CHECK_STRICT=1`）。若门禁容器里 `backend/.venv` 起不来，**新逻辑下这张卡自己就会 exit 2 → `gate_blocked` → 递不出去**。这不是缺陷、正是设计意图，但意味着递卡前得确认门禁容器的 venv 可用。本轮在沙箱里已真实撞到一次：`.venv/bin/python` 指向的 uv 托管解释器被清掉，symlink 悬空，`uv sync` 重建后才恢复。

## 采纳之后：这张卡自己踩中了它写的那个缺口

卡采纳后开出 PR #313，**CI 红了**：`✖ 289 problems (2 errors, 287 warnings)`，两条 error 都在本卡改的 <&frontend/src/views/WorkspaceView.vue> 里，都是 `prettier/prettier` 排版（长三元的换行、`gate_blocked` 那张 `v-card` 的属性该拆行）。

这是对下一节那个缺口最直接的一次实证：**门禁判绿了，CI 才拦下来**——因为 `check.sh` 从头 `cd backend`，前端一行都不看。修法就是 `pnpm run lint:fix`（只动了这一个文件，287 条存量 warning 不拦），修完 `pnpm run lint` → `0 errors`、`pnpm run typecheck` → `total: 0 error(s), baseline allows 31`（rc=0）。

两条沙箱经验，都是这次现挖的：

- 芝士容器里**没有 `node_modules`、也没有 `pnpm`**。`corepack pnpm install --frozen-lockfile` 可以装（要出网，本轮通），`corepack pnpm ...` 直接调，不用先 `corepack enable`（那条会失败）。
- **`vue-tsc` 会 OOM**，默认堆不够：`FatalProcessOutOfMemory`，而且**在工作区里吐了一个 1.1 GiB 的 `frontend/core.1016`**。`NODE_OPTIONS=--max-old-space-size=8192` 之后正常。core dump 是 jj 拦下来的（`Refused to snapshot some files`，超过 1 MiB 新文件上限）——这条护栏这次真派上用场了，已手工删除。

## 不在本话题范围内的门禁缺口

- `check.sh` 里**没有前端**（新增的三个守卫都是后端与仓库规则）。**本卡的 PR #313 就是被这个缺口漏过去的**，见上一节。@wangchangxin 确认准确说法是「**PR 路径上堵住了，门禁这一层还没有**」：#264 的 <&.github/workflows/frontend.yml>（ESLint 零错误 + vue-tsc 走 `tsc-baseline.json` 棘轮，冻结 9 个文件里的 31 个存量错误，只降不升）在 PR 上直接拦，而平台采纳流程会过 PR，所以主路径的洞已经关了。`check.sh` 从头 `cd backend`，等它长出按路径分派再补；缺口已写进 <&.claude/scripts/pre-commit> 的注释，标成缺口而不是权衡。
- **「门禁至今没跑过测试」不必是永久状态**——之前写的"门禁主机无 Postgres 所以没救"是错的，仓库里就有解法只是没接上：<&.claude/scripts/dev-db.sh> 用 uv 拉预编译 wheel（`pgserver` + `redislite`）起 PG/Redis，完全不需要 docker。@wangchangxin 今天在沙箱实测跑通整套：**3924 passed / 30 skipped / 0 failed，138 秒**。
  唯一前提是门禁容器**能出网**拉那两个 wheel（它们故意不是 backend 依赖：`pgserver` 没有 cp313 wheel，声明了会破坏 `requires-python >=3.13` 的解析，脚本把它们钉在一次性的 3.12 解释器上，测试仍跑在 3.13）。
  **但上面那两张线上卡恰恰死于门禁容器 DNS 解析失败**——同一个网络故障也会让 dev-db.sh 拉不到 wheel。所以诚实的结论是：门禁跑测试可行，但只在盒子有网时可行，而今天的证据是它有时没网。这正好反过来说明 BLOCKED 不能判绿——不是"永远只能 `--no-tests`"，也不是"接上就一劳永逸"，是**得先试一次再决定**，且无论哪种结果都需要三态诚实地报出来。
