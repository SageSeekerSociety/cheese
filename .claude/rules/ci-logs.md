---
paths:
  - "backend/app/domain/review/**"
  - ".github/workflows/**"
---

# 读 CI 失败日志 —— 不用找人转述

CI 红了不需要等人贴日志。沙箱里有一个**只读** GitHub token 可以自己铸，权限是
`actions:read` / `checks:read` / `metadata:read`，够拉到失败 job 的**完整日志**。

```bash
export GH_TOKEN=$(cheese gh-token)     # 约 1 小时过期；stderr 会打印仓库名
gh api repos/<owner>/<repo>/commits/<sha>/check-runs      # 哪些检查挂了
gh api repos/<owner>/<repo>/actions/jobs/<job_id>/logs    # 某个 job 的完整日志
```

几个只有踩过才知道的点：

- **仓库名从哪来**：`cheese gh-token` 自己会在 stderr 里说（它按 project 解析
  installation，仓库名是现成的）。工作区不是这个仓库的 git checkout，所以
  `gh api repos/:owner/:repo` 那种占位符写法在这里直接报错。
- **`<job_id>` 不是 run id**：它是 check-run 的 `html_url` 里 `/job/` 后面那串
  数字（`.../actions/runs/<run_id>/job/<job_id>`）。Actions 的 check-run 的
  `id` 恰好等于 job id，但只有 URL 明说这一点，别的 App（codecov 之类）也发
  check-run，把它们的 id 丢给 jobs API 只会 404。
- **`output.summary` 是空的**。GitHub 文档说失败摘要在这里，但 Actions 自己
  把两个字段都留成 null，细节走 annotations（`GET /repos/{o}/{r}/check-runs/
  {id}/annotations`）。2026-08-11 实测过。
- **只 grep `##[error]` 会一无所获**：失败的 shell step 吐的是
  `##[error]Process completed with exit code 1.`，真正说明问题的是它**上面
  那一行**。要连着前面十几行一起看。
- **日志 302 到第三方存储**（`*.blob.core.windows.net`，预签名 URL）。用
  `curl -L` 时注意别把 `Authorization` 头跟着重定向送过去；`gh api` 自己处理好了。

平台已经替你走了一遍：CI 失败推进话题的那条消息里，已经带上失败 job 名、
Actions 链接和错误行附近的日志片段（`review/services.py` 的 `_nudge_pr_fix`
＋ `review/github_pr.py` 的 `_failure_detail`）。上面这套是**要看全文**时用的。

**不要为了看日志去申请写权限**——只读 token 实测足够，`write_token()` 永远不进
沙箱方向。
