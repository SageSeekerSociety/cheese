---
title: CI 设计
kind: 流程
summary: 一次改动从推送到上线要过哪些检查：按路径挑选的必过套件、合并后构建镜像、自动部署测试环境、正式环境人工批准，以及定时巡检。
covers:
  - .github/workflows/required-ci.yml
  - .github/scripts/required-ci.py
  - .github/scripts/required-ci-paths.json
  - .github/workflows/build.yml
  - .github/workflows/deploy-dev.yml
  - .github/workflows/deploy-prod.yml
---

# CI 设计 {#ci}

一次改动从推送到上线要过哪些检查：按路径挑选的必过套件、合并后构建镜像、自动部署测试环境、正式环境人工批准，以及定时巡检。

> 讲：检查怎么组织、为什么这样组织。不讲：每个工作流的细节，全表见 [CI 工作流全表](/dev/ref-ci)。

## 一条必过检查，按路径挑套件 {#required}

分支保护只要求一个检查：`Required CI`（`.github/workflows/required-ci.yml`）。它先跑 `scope`，按这次合并改到的路径决定跑哪些套件（`.github/scripts/required-ci-paths.json`），再把选中的套件当作可复用工作流调用：

| 套件 | 工作流 | 查什么 |
|---|---|---|
| backend | `test.yml` | 后端 pytest，带 Postgres 和 Valkey 服务容器 |
| frontend | `frontend.yml` | 类型检查、eslint、vitest |
| e2e | `e2e.yml` | Playwright 端到端测试 |
| cli | `cli.yml` | Go 连接器的测试 |
| guards | `repo-guards.yml` | 仓库规矩的机器检查，每次都跑 |
| deploy | `deploy-scripts-test.yml` | 部署脚本的测试 |
| harness | `harness-contract.yml` | 骨架请求契约 |
| mcp | `mcp-contract.yml` | Claude Code 构建契约 |
| remote | `remote-execution.yml` | 远端执行验收 |

最后一步 `required` 核对每个套件的结果：选中的必须成功，没选中的必须是跳过，缺失或状态不对都判失败（`required-ci.py failures`）。改到这套检查本身时，所有套件都会跑。

这样做的好处：分支保护里只有一个名字，加减套件不用改仓库设置；只改文档不会触发后端测试，改了后端也不可能漏跑。

## 为什么大多跑在 GitHub 托管的 runner 上 {#runners}

仓库是公开的，托管 runner 不限时长，所以测试默认用 `ubuntu-latest`。只有需要主机上的镜像、真实设备或内网访问的任务（部署、端到端的部分场景、`CLAUDE.md` 审批）才用自托管 runner（标签 `cheese-ci`、`cheese-dev`、`cheese-prod`）。

## 合并之后 {#after-merge}

1. `build.yml`：main 上的每个提交构建一整套镜像，以提交号为标签；没变的镜像直接给旧镜像加上新标签。这一步也把文档站构建进前端镜像。
2. `deploy-dev.yml`：构建成功后自动把这个提交部署到测试环境，见[部署拓扑](/dev/topology)。
3. `deploy-prod.yml`：只在发布 GitHub Release 或手动指定提交时触发，并且要有人批准才会执行。

## PR 上的两个机器人 {#bots}

- `claude-review.yml`：每个 PR 打开或更新时自动评审一次；在 PR 里写 `@claude` 可以追问。
- `claude-md-review.yml`：改了 `CLAUDE.md` 的 PR 需要人批准。`CLAUDE.md` 每一轮都会整份加载，一行错的指令会被每个 agent 执行，所以这是全仓库唯一一个需要人批准的文件。

## 定时巡检 {#scheduled}

备份新鲜度、每周恢复演练、主机与网站可达性、部署漂移和网关健康、runner 维护，都由定时工作流完成，失败会发邮件给管理员。定时规则见 [CI 工作流全表](/dev/ref-ci#workflows)。
