---
paths:
  - ".github/**"
  - "deploy/**"
  - "cli/**"
  - ".claude/scripts/**"
---

# CI and PR — pitfalls in checks and delivery

## stale-shell-test-assertion-after-merge

`deploy/tests/test-app-tier-health.sh` 里大量断言是拿 `grep -Fqx` 去精确整行匹配
`deploy/tests/fakes/app-tier/*` 这些假 docker 打印的日志行（`compose-up-env …`、`slot-up-env …` 之类）。

main 上有人给这类打印行加了一个字段时，同一批断言会跟着改，但**跨合并保留下来的分支自己的断言会停在旧格式**，
于是整行匹配不再命中，CI 的 `deploy / shell-tests` 变红——看起来像实现坏了，其实行为是对的。

**Why:** 2026-10-08 #3073 就是这条：main 的 #3070 给 `compose-up-env` 加了 `APP_RELEASE`，
分支上后加的 `test_unready_backend_without_slots_still_rolls_back` 保留了旧串，回滚明明做了却断言失败。

**How to apply:** shell 测试在合并后转红时，先 `git log -S '<那行日志的前缀>' -- <fake 路径>` 看打印格式的改动史，
再本地按测试同样的 env 手跑一遍 deploy，拿真实日志和断言串逐字比对；确认是格式漂移就改断言，别去改实现。

## report-tables-need-biting-self-tests

规则：给报告类脚本（.claude/scripts 下出表格、出统计的工具）写自测时，必须能用一次变异证明它会变红——至少钉住统计口径（中位数 vs 平均数）和排序。只断言「跑完没报错」不算保护，因为这类工具的产物就是那几张表。

**Why:** 2026-10-12 的 PR #3549（trace_retro.py 的复盘表）上，wangchangxin 因此挡住验收：「请给这几条管线补上真的会变红的自测，最少钉两种改动：统计口径（中位数 vs 平均数）和排序」，并点名 join_cards、_stats、summarize、render_markdown、first_pr_lag 五处没有任何保护。他说完「补完推上去、对新 head 重钉审查再贴回来，我当场采纳」——这是一条他会真的卡的验收条件。

**How to apply:** 仓库棘轮只覆盖「为修复写的测试要在没有修复时见过红」（.claude/scripts/prove-regression.py）；当修复本身就在被测文件里时它表达不了，改用 /tmp 下的一次性变异脚本：改一处、跑 `--self-test`、确认真的红。变异脚本不要提交进仓库（属于本次改动范围之外）。自测要接进 CI（.github/workflows/repo-guards.yml），不然没人跑。

## ci-fast-suite-needs-precommit-env

按 `.github/workflows/ci-fast.yml` 的命令在这台工作机上跑自测
（`python3 -m unittest discover -s .github/scripts -p 'test_ci_fast.py'`）时，
要跟 workflow 一样带上 `CI_FAST_PRECOMMIT="uvx pre-commit"`。不带的话 110 条里
有 33 条以 `pre-commit unavailable: blocked` 失败——这台机器 PATH 上没有
`pre-commit`，只有 `uvx`/`uv`（在 `~/.cheese/store/<项目>/env/.local/bin`）。
带上之后 110 条全过，约 260 秒。

**Why:** 2026-10-13 核 PR #3592 时先把这 33 条当成了自己改出来的红，白查一轮；
那个环境变量就写在 workflow 的 `env:` 里，跑之前照抄一遍就不会踩。

**How to apply:** 本地跑这个文件的自测，env 照 workflow 抄；看到
`pre-commit unavailable: blocked` 就是没带，不是改动把它弄红的。

## procscreen-windows-tempdir-cleanup-flake

`remote / windows`（跑 `go test ./internal/procscreen/...`）偶发红时，先认这个症状：
失败信息是 `TempDir RemoveAll cleanup: unlinkat ...\001\s1.log: The process cannot
access the file because it is being used by another process.`，测试体
（`TestAScreenRunsWithItsEnvironmentAndEndsWhenClosed`）本身全过，红在收尾。测试结束时它发起的
`ping -n 60` 子进程还没退出，占着日志文件句柄；job 日志里紧接着能看到 runner
`Terminate orphan process: pid (...) (PING)`。是 Windows 上进程树回收的时序竞争。

**Why:** 2026-10-14 #3764 合进 main 后报了这条（Required CI run 38626780540），把任务重新打开；
核实与改动无关——同一份 procscreen 代码（blob 全同）在之后 PR #3767 的合并队列运行
（head aab22dee7）上是通过的，最近几轮合并队列也只红了这一发。

**How to apply:** 看到这条别去改代码。先核改动有没有碰 `cli/`，没碰就按偶发处理：在任务里说明
理由后关闭。也别指望重跑——失败的 run 是 merge_group 触发的，仓库的 rerun-failed 按设计拒重跑
merge_group（「already taken its PR out of the queue」）。
