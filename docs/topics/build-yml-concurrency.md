## 状态：已改完，待核实并递交采纳

## 目标
main 上连续 merge 不再互相取消 build（`build.yml`），消除 `deploy-dev.yml` 里 `build-did-not-produce-images` 这类失败（历史占比 55 次失败里 37 次，67%）。**只改 `build.yml` 的 `concurrency` 段落**，不碰其它文件/job 结构/backend/frontend 代码。

## 关键前提已核实（官方文档，见下方 Sources）
GitHub 官方文档明确：**同一个 concurrency group 里，pending（排队中）的 run 在新 run 入队时默认会被取消**——不止取消 in-progress 的那个。所以简报里最初设想的"只把 `cancel-in-progress` 改成条件表达式"确实不够用，会议解决不了问题（队列里堆积的 pending run 照样被砍）。

GitHub 在 2026-05-07 新增了 `queue: max`（最多排队 100 个、FIFO 顺序执行、不取消），但它和 `cancel-in-progress: true` 静态互斥，要在同一个 workflow 里"main 用排队、其它 ref 保留取消"就得处理这个互斥、且这是刚上线不久的新特性，行为细节（尤其和条件表达式组合时的校验时机）没有把握，风险面比按 commit 分组更大。

## 采用的方案（比简报建议更省一行）
只改 `group` 那一行，让 **main 上每次 push 按 commit sha 单独分组**——同一分支上不同 commit 之间根本不存在"同 group 竞争"，无论 `cancel-in-progress` 是什么值都不会被取消。`cancel-in-progress: true` 保持原样不动（对 main 而言现在永远碰不到同组冲突，形同虚设；对 tag push / 非 main 场景，行为和以前完全一致，仍然会互相取消）。

```yaml
concurrency:
  group: ci-build-${{ github.ref }}-${{ github.ref == 'refs/heads/main' && github.sha || '' }}
  cancel-in-progress: true
```

跟简报建议的 `github.event_name == 'push' && github.sha || ''` 相比，改用 `github.ref == 'refs/heads/main'` 判断——精确只对 main 生效，manual `workflow_dispatch` 打在 main 上同一个 commit 时也能正确复用同一分组（预期内、可接受），tag push 分组不受影响（tag 本身已经唯一）。

## 已完成的验证
- `actionlint v1.7.12`（GitHub 官方 linter，临时下载到沙箱跑的）过了整份 `build.yml`，唯一告警是无关的预置 `cheese-dev` 自定义 runner label 未知（跟这次改动无关，之前就有）。
- Python `yaml.safe_load` 解析通过，`concurrency` 块内容跟预期一致（含 embedded 单引号被 YAML 正确当纯量文本处理，没有破坏解析）。
- `ruff check .` 和 `pyright`（backend）全绿——没碰 Python 代码，用来确认沙箱基线没有连带问题。
- `git diff`/`jj diff` 在这个沙箱里跑不了（已知的沙箱 uid 权限问题，跟这次改动无关），改动范围靠"只用 Edit 工具碰过一个文件"这一点保证干净，已用 Read 复核过整份文件确认无其它改动。
- 拿沙箱里的只读 GitHub token（`/sandbox/github-token`）查了 main 上 `build.yml` 最近 20 次 push 触发的 run：**7/20（35%）是 `cancelled`**，且落地前一刻还能看到一个 `in_progress` + 一个 `pending` 并存（`36066a94`、`b36b961b`）——活生生的问题现场，作为落地前基线。

## 待做
1. 递交采纳（`accept-request` → wangchangxin），理由里逐条覆盖简报的 6 条验收标准。
2. **落地方式按简报已知路径**：这条改动碰了 `.github/workflows/`，GitHub App installation token 没有 `workflows: write`，走两阶段采纳的 PR 路径大概率会在 push 分支这步失败，预期会降级成本地 merge + 直推 main（wangchangxin 已批准的路径，8/9 两张卡也是这么落地的）。**新情况**：两阶段采纳当天晚些时候（20:01）已证实用批准人个人 GitHub token 真能开出 PR（#209）——如果批准人的个人 token 有 `workflow` scope，这次没准能走通真实 PR，不必默认它一定会降级；不确定就交给平台自己判断，不是我要解决的事。
3. 落地后按上面基线做效果验证：查 main 上 `build.yml` 之后的 push run，`cancelled` 计数应该趋近 0（除非确实有人对同一 commit 重复手动 dispatch）；只要落地后短时间内出现两次间隔够近的 main 合并，就能看到"前一个 build 没被取消"。观察命令：
   ```
   TOKEN=$(curl -s -H "Authorization: Bearer $CHEESE_TOKEN" http://host.docker.internal:8081/sandbox/github-token | python3 -c "import json,sys;print(json.load(sys.stdin)['data']['token'])")
   curl -s -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" \
     "https://api.github.com/repos/SageSeekerSociety/cheese/actions/workflows/build.yml/runs?branch=main&event=push&per_page=20" \
     | python3 -c "import json,sys;d=json.load(sys.stdin);[print(r['head_sha'][:8],r['status'],r['conclusion'],r['created_at']) for r in d['workflow_runs']]"
   ```
4. **回滚**：如果落地后仍然观察到 main 上相邻 push 的 build 互相 `cancelled`（说明分组表达式没生效，需要回去查 `github.sha`/`github.ref` 求值是否符合预期），单行回滚——把 `group` 改回：
   ```yaml
   group: ci-build-${{ github.ref }}
   ```
   （`cancel-in-progress` 本来就没动过，不用回滚它。）

## 严禁改动（继承自简报，未违反）
只碰 `build.yml` 的 `concurrency` 几行；没动 `on:`/`runs-on:`/job 结构/prune 逻辑；没动 `test.yml`/`e2e.yml`/`deploy-dev.yml`/`deploy-drift.yml`；没动 backend/frontend 代码。
