---
paths:
  - "backend/openapi.sha256"
  - "backend/openapi-untyped.json"
  - "backend/scripts/export_openapi.py"
  - "backend/app/api/**"
---

# OpenAPI: keep the digest as a merge sentinel

The single line in `backend/openapi.sha256` has four jobs:

- It selects the frontend required suite through
  `.github/scripts/required-ci-paths.json`. A route source change alone does not.
- `backend/scripts/export_openapi.py --check` detects an API document whose
  digest was not refreshed. The backend workflow's static job and the fast
  checks run this gate.
- In the local/draft fast tier it is a types-only input: regenerate and check
  frontend types, without selecting every frontend unit test.
- It is backend full-suite fallback metadata and a registry file for
  `.claude/scripts/arch-metrics.py`.

Frontend code generation does not read the digest. It consumes the document
produced by `export_openapi --stdout` through
`frontend/scripts/gen-api-types.mjs`.

## Resolve the sentinel on the merged tree

The one-line conflict is intentional, like `backend/alembic/HEAD`: two branches
that change the API document must reconcile against the combined tree. Do not
remove the digest, split it or install a merge driver to hide that conflict.
GitHub's server-side merge does not execute a local custom merge driver.

Merge the target branch, resolve the source conflicts, then regenerate the
summary from that merged tree. From the repository root:

```bash
git checkout --theirs backend/openapi.sha256  # temporary value, not the resolution
(cd backend && uv run python -m scripts.export_openapi)
(cd backend && uv run python -m scripts.export_openapi --check)
git add backend/openapi.sha256
```

Stop if either generator command fails. A new response without a schema trips
its untyped ratchet: it returns 1 before writing any files, so the temporary
digest has NOT been replaced. Fix the reported response with `typed_response`
and rerun generation; do not expand `backend/openapi-untyped.json` to conceal
it. The completion criterion is `export_openapi --check` exiting 0, not merely
having run the generator. Never commit one side's digest as the final answer.

## Keep the trigger tests behavioural

`test_the_digest_is_what_carries_a_route_change_into_the_frontend_suite` in
`.github/scripts/test_required_ci.py` checks selection for the digest and for
a route source. The existing
`test_frontend_types_only_input_widens_types_not_the_test_run` in
`.github/scripts/test_ci_fast.py` guards the types-only plan. Do not add a
second test that merely repeats the metadata constants.

## openapi-digest-conflicts-need-rederiving

`backend/openapi.sha256` 是 `app.openapi()` 文档的摘要，一行。**任何新增路由的 PR 都会改这一行**，所以两个都加路由的 PR 之间必然冲突——该文件顶上的说明写着这一行「collides with nothing」，实测会撞。

解法只有重算：把 main 合进任务分支（merge，不 rebase），解冲突时先写任一侧的值占位，然后在**合并后的树**上跑 `cd backend && uv run --no-sync python -m scripts.export_openapi`（不带参数＝刷新摘要 + untyped 棘轮），提交生成器写出的值。`backend/openapi-untyped.json` 是同一个生成器的第二个产物。

2026-10-14 在 PR #3703 上实测：冲突文件只有这一个；两边都是新增路由（本支 `/throughput/seats`、main 若干），重算得 `e9582f42…`，既不等于本支的 `d809a112…` 也不等于 main 的 `2b1b6c69…`——两边都动了它。本支自己的 6 个文件在合并前后 `git rev-parse <sha>:<path>` 逐个相同。新 head 上全是绿灯，但 GitHub 在那之前一直报 CONFLICTING，所以 PR 会先离开合并队列。

**Why:** 挑任一侧的值是最自然的做法，而且看起来「就一行」。但它让合并后的树和摘要不一致，`.github/workflows/test.yml` 的后端 lint job 会跑 `export_openapi --check` 报红。摘要也是前端套件的触发路径，值对不对影响 CI 选不选中前端。

**How to apply:** 任务分支的 PR 报 CONFLICTING、且 `git diff --name-only --diff-filter=U` 只有 `backend/openapi.sha256` 时，按上面合并在树上重算再 push-fix；别手工挑值，也别以为冲突是假的。
