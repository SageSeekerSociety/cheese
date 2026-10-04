# 读 CI

PR 上的检查失败了，自己去读，别等人贴日志。

## GitHub 项目

- 看检查：`gh api repos/<o>/<r>/commits/<sha>/check-runs`。
- 读日志：`gh api repos/<o>/<r>/actions/jobs/<job_id>/logs`。`<job_id>` 是 Actions 检查的 `html_url` 里 `/job/` 后面那串数字，不是 run id，也不是别的 App 的 check-run id。
- `output.summary` 为空时，看 `/check-runs/<id>/annotations` 和 job 日志。
- 不要只筛 `##[error]`，失败那一步前后的日志也要读。日志的重定向 `gh api` 会自己处理。

## Forgejo 项目

用 `fj`，不用 `gh`（在 Forgejo 项目里调 `gh` 会提示改用 `fj`）。命令和参数看 `fj --help`。

## 检查被取消

被取消的运行说明检查没有跑完。先看有没有替代它的运行正在跑，再决定要不要重跑；不要为了触发检查去造无关的提交。
