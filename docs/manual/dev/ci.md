---
title: CI 设计
kind: 流程
summary: 一次改动从推送到上线要过哪些检查：按路径挑选的必过套件、合并后构建镜像、自动部署测试环境、正式环境人工批准，以及定时巡检。
covers:
  - .github/workflows/required-ci.yml
  - .github/scripts/required-ci.py
  - .github/scripts/required-ci-paths.json
  - .github/workflows/ci-fast.yml
  - .github/workflows/build.yml
  - .github/workflows/deploy-dev.yml
  - .github/workflows/deploy-prod.yml
---

# CI 设计 {#ci}

一次改动从推送到上线要过哪些检查：按路径挑选的必过套件、合并后构建镜像、自动部署测试环境、正式环境人工批准，以及定时巡检。

> 讲：检查怎么组织、为什么这样组织。不讲：每个工作流的细节，全表见 [CI 工作流全表](/dev/ref-ci)。

## 一条必过检查，按路径挑套件 {#required}

分支保护只要求一个检查：工作流 `Required CI`（`.github/workflows/required-ci.yml`）里的 job `CI required`。它先跑 `scope`，按这次合并改到的路径决定跑哪些套件（`.github/scripts/required-ci-paths.json`），再把选中的套件当作可复用工作流调用：

| 套件 | 工作流 | 查什么 |
|---|---|---|
| backend | `test.yml` | 后端 pytest，带 Postgres 和 Valkey 服务容器 |
| frontend | `frontend.yml` | 类型检查、eslint、vitest |
| e2e | `e2e.yml` | Playwright 端到端测试 |
| cli | `cli.yml` | Go 连接器的测试 |
| guards | `repo-guards.yml` | 仓库规矩的机器检查和文档站构建（链接与锚点、每页的类型/摘要/涉及代码、参考页生成器），每次都跑 |
| deploy | `deploy-scripts-test.yml` | 部署脚本的测试 |
| harness | `harness-contract.yml` | 骨架请求契约 |
| mcp | `mcp-contract.yml` | Claude Code 构建契约 |
| equivalence | `mcp-equivalence.yml` | 房间里的远程 Bash 和机器上直接跑的 Claude Code 行为一致（`equivalence.py`），每种运行方式、每种 shell 各一个 job |
| remote | `remote-execution.yml` | 远端执行验收 |
| cifast | `ci-fast.yml` | `ci:fast` 本地快检的黑盒测试，驱动真实 pre-commit |

最后一步 `required` 核对每个套件的结果：选中的必须成功，没选中的必须是跳过，缺失或状态不对都判失败（`required-ci.py check`）。改到这套检查本身时，所有套件都会跑。

这样做的好处：分支保护里只有一个名字，加减套件不用改仓库设置；只改文档不会触发后端测试，改了后端也不可能漏跑。

```demo-ci
title: 勾几行路径，看这套检查选了什么
note: 左边勾上这次改到的路径，右边就是 Required CI 会要求成功的套件。选中的必须成功，没选中的必须是跳过。
source: ci-scope
expect: backend, frontend, e2e, cli, guards, deploy, harness, remote, mcp, equivalence, cifast
paths:
  - path: docs/manual/dev/ci.md
    label: 改文档
  - path: frontend/src/views/MyDevicesView.vue
    label: 改前端组件
  - path: backend/app/domain/agent/harness/prompt.py
    label: 改骨架提示词
  - path: deploy/deploy-docker.sh
    label: 改部署脚本
  - path: cli/cheese
    label: 改 CLI 连接器
  - path: .github/scripts/required-ci.py
    label: 改这套检查本身
scenarios:
  - key: docs
    label: 只改文档
    paths: docs/manual/dev/ci.md
  - key: frontend
    label: 改前端组件
    paths: frontend/src/views/MyDevicesView.vue
  - key: prompt
    label: 改骨架提示词
    paths: backend/app/domain/agent/harness/prompt.py
  - key: deploy
    label: 改部署脚本
    paths: deploy/deploy-docker.sh
  - key: cli
    label: 改 CLI 连接器
    paths: cli/cheese
  - key: gate
    label: 改这套检查本身
    paths: .github/scripts/required-ci.py
```

上面这张表里的套件名、pattern 和勾选结果都是构建时从 `.github/scripts/required-ci-paths.json` 读出来的，而且和真跑一遍 `required-ci.py` 的结果逐条对过：对不上，构建就失败。

## 哪些跑在托管 runner，哪些跑在自己的机器上 {#runners}

`Required CI` 选中的套件全部用 GitHub 托管的 `ubuntu-latest`，cli 套件和远端执行验收也在内：每台机器一次只跑一件事，单个 job 最长 6 小时。组织是 Free 计划，文档写的托管并发上限是 20 个 job，但这个仓库实际不受它约束：2026-09-30 到 10-01 的一天里同时在跑的托管 job 最多 61 个，合并队列的 job 等 runner 的 90 分位是 0.1 分钟。

自托管 runner（标签 `cheese-ci`、`cheese-dev`、`cheese-prod`）上只跑本来就要主机资源的：部署（`cheese-dev` 在测试机，`cheese-prod` 在正式机）、部分端到端场景、备份新鲜度与恢复演练、心跳、漂移巡检和 runner 维护，它们要读机器上的文件或连内网。

## 合并之后 {#after-merge}

1. `build.yml`：main 上的每个提交构建一整套镜像，以提交号为标签；没变的镜像直接给旧镜像加上新标签。这一步也把文档站构建进前端镜像。
2. `deploy-dev.yml`：镜像构建结束时触发，确认这个提交的镜像构建成功、它在合并队列里跑的 `Required CI` 也成功，就自动部署到测试环境（也可以手动触发），见[部署拓扑](/dev/topology)。`Required CI` 不在 main 上重跑：队列把 PR squash 到测试过的基底上再快进 main，队列里测的就是落到 main 的那个提交。这个提交的镜像构建或 `Required CI` 没有成功的最新一次时，这次运行失败。GitHub 的运行列表有时比运行本身晚一分钟左右才显示构建已成功，所以判定失败之前会每 15 秒重读一次，最多读两分钟；测试环境已经在跑同一个或更新的版本时，跳过部署，运行成功。`desktop.yml` 发布桌面端安装包之后会重建一遍镜像，再手动触发这个工作流，点名它重建的那个提交（不是触发时 main 的最新提交）；这时测试环境在跑同一个提交也照样部署，因为这个提交的镜像已经换成了新的。部署组里同时只能有一个运行在等，后来的会取消先在等的那个；镜像构建又不按合并顺序结束，所以如果 main 上已经有一个更新的提交可以部署（镜像和 `Required CI` 都成功），这个提交的运行就不再去排队，摘要写明由哪个更新的提交连它一起部署。每次运行的摘要都写明测试环境实际在跑哪个提交。
3. `deploy-prod.yml`：只在发布 GitHub Release 或手动指定提交时触发，并且要有人批准才会执行。
4. `warm-caches.yml`：依赖锁文件或版本 pin 变了的时候（另外每天一次），在 main 上照各套件的 key 装一遍依赖并存下缓存，不跑测试。PR 和合并队列里的套件只恢复、不保存：从这两类 ref 存的缓存，别的 ref 读不到。

## PR 上的两个机器人 {#bots}

- `claude-review.yml`：在 PR 里写 `@claude` 可以让它评审或回答问题。每个 PR 自动评审一次的触发器 2026-08-09 起停用了（它在所有 PR 上失败，而失败的检查会卡住采纳）；要恢复，把 `pull_request:` 加回 `on:`，`auto-review` job 原样可用。
- `claude-md-review.yml`：改了 `CLAUDE.md` 的 PR 需要人批准。`CLAUDE.md` 每一轮都会整份加载，一行错的指令会被每个 agent 执行，所以这是全仓库唯一一个需要人批准的文件。

## 定时巡检 {#scheduled}

备份新鲜度、每周恢复演练、主机与网站可达性、部署漂移和网关健康、runner 维护，都由定时工作流完成，失败会发邮件给管理员。定时规则见 [CI 工作流全表](/dev/ref-ci#workflows)。
