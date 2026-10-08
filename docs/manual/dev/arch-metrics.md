---
title: 架构指标
kind: 参考
summary: 一张能持续看的架构看板：组件 A/B/C/D 分级、后端三条契约的冻结条目、超标文件和两端热点，每个数怎么算、现在是多少、该往哪边走，以及怎么和另一个版本比。
covers:
  - .claude/scripts/arch-metrics.py
  - .github/workflows/arch-metrics.yml
  - .claude/scripts/check-file-sizes.py
  - backend/.importlinter
  - frontend/import-boundary-baseline.json
  - .claude/rules/architecture.md
---

# 架构指标 {#arch-metrics}

三道检查挡的是「新违规」：模块边界（`backend/.importlinter` 的三条契约）、组件不能自己取数（`frontend/import-boundary-baseline.json` 的棘轮）、文件大小上限（`.claude/scripts/check-file-sizes.py`）。三道都拿一个冻结的基线做棘轮，所以它们会一直绿——绿到看不出一棵树是不是在变好。这一页讲的是另一半：`.claude/scripts/arch-metrics.py` 读同样的几份声明，把每个数现在是多少、和一个月前差多少打出来。

> 讲：有哪些数字、每个数怎么算、现在是多少、要往哪个方向走，以及怎么和另一个版本比。不讲：三道闸各自的规则和为什么那样定（见仓库里的 `.claude/rules/architecture.md`），CI 的整体设计（见 [CI 设计](/dev/ci#required)）。

## 为什么是看板，不是门禁 {#board-not-gate}

这个脚本永远退出 0，除非它自己跑不起来。这不是偷懒：每个门禁都要有人认领，而这里能红的理由大多不归代码管。

看板的输入有一半是别人给的东西——一个会从 checkout 里老掉的 git 引用、一份正在迁移的基线文件、凌晨四点的定时任务碰上一台状态不好的机器。这些情况里报红，等于说「架构变差了」，而这句话这个脚本没有资格说。所以工作流（`.github/workflows/arch-metrics.yml`）里每一步都写着 `continue-on-error: true`：它在 main 上和每周一跑一次，不在 PR 上跑，也不会让任何一次运行失败。数字落进 job summary 和 JSON artifact，**读它是人的事**。

规则本身的门禁仍然只有三道，而且只拦新增。

## 怎么跑 {#run}

```
python3 .claude/scripts/arch-metrics.py                        # 写 ./arch-metrics.{json,md}，stdout 也打一遍
python3 .claude/scripts/arch-metrics.py --compare 721f7b04     # 另一个版本，带差值表
python3 .claude/scripts/arch-metrics.py --out /tmp/board       # 换输出目录
python3 .claude/scripts/arch-metrics.py --self-test            # 证明每个数都数得对
```

全程只用标准库和已有的依赖，在仓库根跑，约 11 秒。`--compare` 会为那个版本开一个临时 worktree，跑同一份代码，再算差值；跑完就把 worktree 删掉（`--self-test` 里有一条断言专门看着它别留下东西）。那个版本没有的输入——比如 `.importlinter` 还没落地的版本——在差值表里是 `n/a`，不是 0。

`--self-test` 种一个只有十几行的小仓库，逐项核对：四个等级各算对没有、函数内导入数不数嵌套函数、超标行数是不是超出部分的和、同一天两次提交算不算一次并行编辑、差值的符号是不是两边相减。`repo-guards` 那次 `--self-test` 也在 CI 里跑。

## 每个数是什么意思 {#metrics}

| JSON 里的键 | 是什么 | 怎么算 | 方向 |
|---|---|---|---|
| `frontend.grade_counts.components` | 前端组件数 | `frontend/src` 下的 `.vue` 文件数 | 中性 |
| `frontend.grade_counts.grades` / `grade_pct` | A/B/C/D 各多少个、占几成 | 见下面的分级方法 | A 升，D 降 |
| `frontend.grade_counts.grade_lines` | 每一级的行数 | 同级组件的行数之和 | A 升 |
| `frontend.boundary.violations` | 组件直接取数 / 读路由的处数 | 直接读 `frontend/import-boundary-baseline.json`，不重算 | 只降 |
| `backend.contracts.per_contract` | 三条契约各自冻结了多少条 | 解析 `backend/.importlinter` 的 `ignore_imports`，一条 `importer -> imported` 算一条 | 只降 |
| `backend.deferred_imports` | 函数内导入的条数 | `ast` 遍历 `backend/app`，只数词法上落在函数体里的 `import` / `from ... import`（嵌套函数算一次，模块级的 `if TYPE_CHECKING:` 不算） | 只降 |
| `backend.files.files_over_1000` | 后端超过 1000 行的文件数 | `backend/app/**/*.py` 的行数分布 | 只降 |
| `backend.files.files_over_1500` | 超过上限（1500 行）的 | 同上 | 只降 |
| `backend.files.chat_py_lines` | `agent/chat.py` 的行数 | 试点单独跟一条线 | 只降 |
| `size.per_cap.<树>.over_cap` / `excess_lines` | 超标文件数、合计超出行数 | 上限从 `.claude/scripts/check-file-sizes.py` 里 import 进来，不在这儿重写一遍 | 只降 |
| `hotspots.<30d\|90d>.top` | 改动次数 × 行数前 10 的文件 | 近 30 / 90 天的提交按文件计数，乘当前行数；每条带 fix 提交数和占该文件的比 | 热点变轻 |
| `hotspots.<窗口>.fix_commits` | 窗口里 fix/revert 提交数 | 提交主题按 `fix(` / `fix:` / `revert` 开头判定 | 占比降 |
| `shared_days.shared_days` | 同一天被两个以上不同提交改过的文件-天 | 90 天窗口内按（文件，日期）分组数不同的提交 | 只降 |

**分级方法**（从 B 数据那份体检脚本 `analyze.py` 移植并简化）：

| 级别 | 判据 |
|---|---|
| **D** | 绑在挂载位置或别的通道上：读了路由（`useRoute`/`useRouter`/`$router`/`vue-router`）、`$parent`/`$root`、事件总线、`provide`/`inject` |
| **C** | 自己取数：直接 import `@/api`、`@/services/*`、`@/network/*`，或者沿着一条能解析出来的链**间接**够到这些，或者自己写 `fetch`/`axios`；或者读业务 store |
| **B** | 只读应用外壳的 store（`usePageTitleStore`、`useNavigationStore`） |
| **A** | 以上都没有：只靠 props 和事件就能渲染 |

**误差说清**：这是正则不是编译器（原始体检报告用的也是同一套办法）。`import type` 一律不算，它在构建时就被抹掉；解析不出来的模块说明符当成外部依赖，不当成一条边——所以一个组件如果真的隔着一条解析不出来的链在取数，它会被判高一档。`defineProps` / `defineEmits` 根本不数，四个等级都用不到它们（原来那份报告里的 props 普查没有移植）。热点只覆盖 `backend/app/**/*.py` 和 `frontend/src/**/*.{vue,ts,js}`，去掉 `*.spec.ts` 和测试目录：测试跟着它的主语改，不单独算热点；合并提交（`--no-merges`）本来就没有自己的 diff。

## 当前基线 {#baseline}

2026-10-07，`main@a523f1ae`。数取自 CI 在这个版本上跑出的 `arch-metrics.json`。

| 前端 | 值 |
|---|---|
| 组件总数 | 619（131,203 行） |
| A / B / C / D | 357（57.7%）/ 2（0.3%）/ 162（26.2%）/ 98（15.8%） |
| A 级行数 | 68,441（52.2% 的行） |
| 冻结的组件边界违规 | 24 处，24 个文件 |

| 后端 | 值 |
|---|---|
| 冻结契约条目 | C1 29 + C2 56 + C3 164 = **249** |
| 函数内导入 | 850 |
| > 1000 行文件 | 33 |
| > 1500 行文件 | 7 |
| `agent/chat.py` | 2,772 行 |

| 文件大小 | 上限 | 超标 | 合计超出行数 |
|---|---|---|---|
| `frontend/src/` | 1000 | 4 | 4,423 |
| `backend/app/` | 1500 | 7 | 4,036 |
| **合计** | | **11** | **8,459** |

最超的五处：`frontend/src/api.ts`（+1779）、`frontend/src/proto-feedback-fixtures.ts`（+1629）、`agent/chat.py`（+1272）、`agent/runtime.py`（+1231）、`frontend/src/stores/feedback.ts`（+708）。

热点前五（按 30 天排；改次数 × 行数，fix 占比）：

| 文件 | 30 天 | 90 天 |
|---|---|---|
| `backend/app/domain/agent/chat.py` | 195 次，57 次 fix（29%） | 272 次，86 次 fix（32%） |
| `frontend/src/api.ts` | 167 次，32 次 fix（19%） | 227 次，45 次 fix（20%） |
| `backend/app/api/routes/topics.py` | 135 次，35 次 fix（26%） | 188 次，46 次 fix（24%） |
| `backend/app/domain/agent/runtime.py` | 61 次，23 次 fix（38%） | 104 次，41 次 fix（39%） |
| `frontend/src/cx_types.ts` | 125 次，29 次 fix（23%） | 171 次，37 次 fix（22%） |

按 90 天排，第五是 `backend/app/core/config.py`（185 次，40 次 fix，22%），`cx_types.ts` 排第六。窗口里的提交：30 天 2,073 个（fix 956），90 天 2,877 个（fix 1,206）。

同一天被多个提交改：90 天里 11,016 个文件-天中有 **3,352** 个是这样；最多的是 `agent/chat.py` 的 45 天，然后 `frontend/src/api.ts` 和 `backend/app/core/config.py` 各 38 天。

## 和上一版基线比 {#delta}

对照点是上一版基线：2026-09-29，`main@33f6ed5c`。「之前」列是那一版脚本的输出，「现在」列是上面的 `main@a523f1ae`。差值是两份输出相减，不是一次 `--compare` 跑出来的。自己要比两个版本，用：

```
python3 .claude/scripts/arch-metrics.py --compare 33f6ed5c
```

| 指标 | 之前 | 现在 | 差 |
|---|---|---|---|
| `frontend.grade_counts.components` | 414 | 619 | +205 |
| `frontend.grade_counts.lines` | 117754 | 131203 | +13449 |
| `frontend.grade_counts.grades.A` | 172 | 357 | **+185** |
| `frontend.grade_counts.grades.B` | 1 | 2 | +1 |
| `frontend.grade_counts.grades.C` | 113 | 162 | +49 |
| `frontend.grade_counts.grades.D` | 128 | 98 | **−30** |
| `frontend.grade_counts.grade_lines.A` | 28151 | 68441 | +40290 |
| `frontend.boundary.violations` | 91 | 24 | **−67** |
| `frontend.boundary.files` | 57 | 24 | −33 |
| `backend.contracts.per_contract.api-domain-core`（C1） | 26 | 29 | +3 |
| `backend.contracts.per_contract.routes-touch-no-models`（C2） | 56 | 56 | 0 |
| `backend.contracts.per_contract.domains-acyclic`（C3） | 178 | 164 | −14 |
| `backend.contracts.total` | 260 | 249 | −11 |
| `backend.deferred_imports` | 729 | 850 | +121 |
| `backend.files.files_over_1000` | 27 | 33 | +6 |
| `backend.files.files_over_1500` | 16 | 7 | −9 |
| `backend.files.chat_py_lines` | 6943 | 2772 | **−4171** |
| `size.offenders` | 35 | 11 | −24 |
| `size.excess_lines` | 35767 | 8459 | **−27308** |
| `hotspots.90d.commits` | 2128 | 2877 | +749 |
| `shared_days.shared_days` | 1674 | 3352 | +1678 |

几点要读清楚：

- 超标总行数从 35,767 降到 8,459，`chat.py` 一个文件就降了 4,171 行。超过 1500 行的后端文件从 16 个降到 7 个，但超过 1000 行的从 27 个升到 33 个：拆出来的文件大多落在 1000–1500 之间，没有落到 1000 以下。
- 组件多了 205 个，A 多了 185 个，D 少了 30 个。A 的占比从 41.5% 到 57.7%，A 级行数占比从 23.9% 到 52.2%。组件边界违规从 91 处降到 24 处。
- 两条往反方向走的线：函数内导入多了 121 条；C1 契约多冻了 3 条（C3 少了 14 条，总数仍降 11）。
- 热点和同日多提交两项随提交量涨（90 天提交数多了 749 个），不单独说明结构变差。

## 下一步看什么 {#next}

看下面四个方向就够了：A 级占比（现在是 57.7%）；两条「只降」的线——249 和 24——有没有一个月内继续掉；超标的总行数 8,459 有没有往下走，以及 1000–1500 行之间那 26 个后端文件会不会再长过上限；热点里 `chat.py` 的 fix 占比（30 天 29%、90 天 32%）有没有降。函数内导入（850）在涨，也值得看一眼。哪一项都不许卡死（`.claude/rules/architecture.md` 里写的理由），这个脚本也不为它们报红。
