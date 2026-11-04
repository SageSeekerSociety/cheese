---
title: CI 设计
kind: 流程
summary: 一次改动从推送到上线要过哪些检查：按路径挑选的必过套件、合并后构建镜像、自动部署测试环境、正式环境人工批准，以及定时巡检。
covers:
  - .github/workflows/required-ci.yml
  - .github/scripts/required-ci.py
  - .github/scripts/required-ci-paths.json
  - .github/scripts/backend_imports.py
  - .github/scripts/coverage_map.py
  - .github/workflows/coverage-map.yml
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
| backend | `test.yml` | 后端 pytest，带 Postgres 和 Valkey 服务容器；`backend/tests/forgejo` 要真的 Forgejo，不在这里跑 |
| frontend | `frontend.yml` | 类型检查、eslint、vitest |
| e2e | `e2e.yml` | Playwright 端到端测试；`forgejo live` job 起一个固定版本的 Forgejo 容器（`backend/tests/forgejo/start.sh`），对着它跑 `backend/tests/forgejo` 的后端测试（都只在合并后跑） |
| cli | `cli.yml` | Go 连接器的测试 |
| guards | `repo-guards.yml` | 仓库规矩的机器检查和文档站构建（链接与锚点、每页的类型/摘要/涉及代码、参考页生成器），每次都跑 |
| deploy | `deploy-scripts-test.yml` | 部署脚本的测试 |
| harness | `harness-contract.yml` | 骨架请求契约 |
| mcp | `mcp-contract.yml` | Claude Code 构建契约（只在合并后跑） |
| equivalence | `mcp-equivalence.yml` | 房间里的远程 Bash 和机器上直接跑的 Claude Code 行为一致（`equivalence.py`），每种运行方式覆盖一次，每种 shell 两个 job（各跑一组运行方式）；只在合并后跑 |
| remote | `remote-execution.yml` | 远端执行验收（只在合并后跑） |

最后一步 `required` 核对每个套件的结果：选中的必须成功，没选中的必须是跳过，缺失或状态不对都判失败（`required-ci.py check`）；后端套件选中时，它还核对每个分片跑过的用例加起来正好是独立收集出来的那份（`assert_shards_complete`）。改到这套检查本身时，所有套件都会跑。`required` 要等最慢的套件跑完才能开始，所以一个套件早早红了，`CI required` 也要到最后才显示红。聚合 job 有两个：`required`（`name: CI required`）给正式 PR 和主干 push，`required-draft`（`name: CI required (draft quick)`）给草稿，名字都写死在 workflow 里，不是用表达式拼的（表达式只在 GitHub 求值时才渲染，被取消的 run 会把表达式原文当成检查名显示）。

前端套件还有一个不看路径的入口：前端的类型是从后端的 OpenAPI 文档生成的，后端改动可能改了这份文档，而没有哪个文件能说明它变没变。所以路径选中了后端，`api` job 就在这次 diff 的基点和这次提交上各打印一次文档（`export_openapi --stdout`）逐字节比较，不同就跑前端套件。`required` 按它的回答要求前端套件成功或跳过；`api` 自己没给出回答（失败、取消、输出缺失）也判失败。这件事原来靠仓库里提交的一行文档摘要 `backend/openapi.sha256`：每个改 API 的 PR 都要改这一行，任意两个这样的 PR 都会在这一行上冲突。

同一个 job 还拿这两份文档问另一件事：这次改动会不会让照基点那版接口造的、还开着的页面说不通（`.github/scripts/api_compat.py`：接口删了；请求字段删了、改名、能收的类型变少或新增为必填；响应字段删了、改名、可能缺席或多出没见过的类型；枚举值删了）。会的话，`backend/api-generation` 里的数必须在同一个 diff 里调高，否则 job 失败。调高之后，后端拒掉带着更低代数的请求（409 `ClientOutdated`，房间连接以 4409 关闭），开着的旧页面就弹出提示请人刷新（`frontend/src/lib/apiGeneration.ts`）。前后端一起改的 PR 也问这一句，所以这个 job 现在只看后端有没有被选中。两个 PR 各把数从 N 调到 N+1，合并时不冲突、落成同一个数；前一个先部署了的话，main 上的 push 运行（基点是上一次全绿的提交）会在这里变红，再调高一次即可。

CI 只能比较 OpenAPI 的形状；已有接口或推送消息的含义变化（包括默认行为和实时／WebSocket 消息）由作者通过 PR 模板的必填问题判断，回答“是”就在同一个 PR 调高 `backend/api-generation`。

**主干是提交后门禁**：PR squash 进 main 之后，每次落码触发一次 `push` run，它判的是**一批**而不是这一条提交。`scope` 的基点不是 `github.event.before`（那只是上一个 tip，被顶掉的排队 run 里的提交会落在所有后续 diff 之外），而是 `refs/heads/main-green`：上一次这道理检查通过的提交。`mark-green` job 只在 main push 的 `required` 绿之后把锚点推到这个提交，红了不动，下一批自然把这段一起盖上。push 的并发组固定为 `required-ci-push-main`、`cancel-in-progress` 为 `false`：GitHub 同时只让一个 run 在跑、最多一个排队，新的排队顶掉旧的排队，所以攒批不丢提交。第一次落地时还没有 `main-green`，`scope` 把所有已跟踪路径都算作改动、选全部套件——宁宽不窄。部署门禁认 main 上这次 `push` run。

**三档**（`required-ci.py` 的 `tier_for`）：

- **草稿 PR 不跑套件**：只有 `scope` 和 `required-draft`，几秒钟结束。草稿是还没做完的东西，平台递卡时会把它转正式（`pr_publish.py` 调 `mark_ready_for_review`），`ready_for_review` 触发下一档。
- **正式 PR**：路径选中的套件都跑，只有 e2e、remote、mcp、equivalence 留到合并后。这四个要专门的机器或耗时是其余套件的几倍，2026-10-18 到 10-20 里合进 main 之后把 main 弄红的次数也远少于其余套件。后端的 integration 层只跑执行过这次改动的测试文件。依据是覆盖率映射表：`coverage-map.yml` 每天两次在 `main-green` 上带覆盖率跑整层 integration，按测试记下每个函数被哪些测试文件执行过（`coverage_map.py`）。改了函数体，就跑执行过这个函数的测试文件；改了函数外面的代码（常量、类字段这类只在 import 时执行一次的），就找出读这些名字的函数，再跑这些函数的测试文件；函数外不绑定任何名字的语句（比如注册调用），跑 import 这个模块的所有模块的测试文件。PR 改到的测试文件一律跑，映射表里没有对应测试的测试文件每次都跑。迁移、`pyproject.toml`、`uv.lock`、`alembic.ini`、`backend/sandbox/`（镜像的 Dockerfile 除外）、任何 conftest，所有测试都靠它们，改到就整层跑；测试数据只跑读这个文件的模块能到达的测试，conftest 读的数据整层跑；改到选择规则自己、改到 gate、从 `backend/` 以外的路径选中了后端套件，或者读不到映射表，也整层跑。选中的文件按每片 144 个分片，最多 5 片；一个也没选中时 integration 不跑。漏选由合并后 main 上的整层跑兜底。
- **主干 push**：选中的套件全跑、整层跑，这是部署认的结果。

事件读不出时按主干那一档处理（fail closed）。

这样做的好处：分支保护里只有一个名字，加减套件不用改仓库设置；只改文档不会触发后端测试，改了后端也不可能漏跑。

```demo-ci
title: 勾几行路径，看这套检查选了什么
note: 左边勾上这次改到的路径，右边就是 Required CI 会要求成功的套件。选中的必须成功，没选中的必须是跳过。
source: ci-scope
expect: backend, frontend, e2e, cli, guards, deploy, harness, remote, mcp, equivalence
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
```

上面这张表里的套件名、pattern 和勾选结果都是构建时从 `.github/scripts/required-ci-paths.json` 读出来的，而且和真跑一遍 `required-ci.py` 的结果逐条对过：对不上，构建就失败。

## 哪些跑在托管 runner，哪些跑在自己的机器上 {#runners}

`Required CI` 选中的套件都跑在 GitHub 托管的 runner 上（只有 `scope`、`CI required`、`CI required (draft quick)` 和主干 push 的 `Mark main green` 这几个只跑几秒的串行 job：主干 push 和同仓 PR 用 `cheese-ci` 池，fork 来的 PR 仍用托管机），cli 套件和远端执行验收也在内：每台机器一次只跑一件事，单个 job 最长 6 小时。后端测试的 pure、contract、integration 三层和前端的 eslint、check、unit 三个 job 用 `ubuntu-24.04-arm`，其余用 `ubuntu-latest`：arm64 机器是 4 个真实核心，x64 机器是 2 核超线程、CPU 代际不固定，这几个 job 在 arm64 上更快或者持平，耗时也更稳定。生产是 x64，所以 prod 发版（`deploy-prod.yml`）会在 `ubuntu-latest` 上对发版的 commit 再跑一遍完整后端测试，通过并经人批准后才部署。组织是 Free 计划，文档写的托管并发上限是 20 个 job，但这个仓库实际不受它约束：2026-09-30 到 10-01 的一天里同时在跑的托管 job 最多 61 个。

自托管 runner（标签 `cheese-ci`、`cheese-dev`、`cheese-prod`）上除了上面那几个汇总 job，只跑本来就要主机资源的：部署（`cheese-dev` 在测试机，`cheese-prod` 在正式机）、部分端到端场景、备份新鲜度与恢复演练、心跳、漂移巡检和 runner 维护，它们要读机器上的文件或连内网。

## 合并之后 {#after-merge}

1. `build.yml`：main 上的每个提交构建一整套镜像，以提交号为标签；没变的镜像直接给旧镜像加上新标签。这一步也把文档站构建进前端镜像。
2. `deploy-dev.yml`：`Required CI` 结束时触发，确认这个提交的镜像构建成功、它的 `Required CI` 也成功，就自动部署到测试环境（也可以手动触发），见[部署拓扑](/dev/topology)。批次先结束、镜像还在构建时，会等构建跑完（最多 10 分钟）；等不到时这次运行失败，摘要说明构建仍在进行，构建完成后重跑这次运行或由下一次合入一并部署。`Required CI` 认的是 main 上 `push` 的那次；那次被后来的批次顶掉时，认覆盖了它的那个绿批次。这个提交的镜像构建或 `Required CI` 没有成功的最新一次时，这次运行失败。GitHub 的运行列表有时比运行本身晚一分钟左右才显示构建已成功，所以判定失败之前会每 15 秒重读一次，最多读两分钟；测试环境已经在跑同一个或更新的版本时，跳过部署，运行成功。`desktop.yml` 发布桌面端安装包之后会重建一遍镜像，再手动触发这个工作流，点名它重建的那个提交（不是触发时 main 的最新提交）；这时测试环境在跑同一个提交也照样部署，因为这个提交的镜像已经换成了新的。部署组里同时只能有一个运行在等，后来的会取消先在等的那个；镜像构建又不按合并顺序结束，所以如果 main 上已经有一个更新的提交可以部署（镜像和 `Required CI` 都成功），这个提交的运行就不再去排队，摘要写明由哪个更新的提交连它一起部署。每次运行的摘要都写明测试环境实际在跑哪个提交。
3. `deploy-prod.yml`：只在发布 GitHub Release 或手动指定提交时触发，并且要有人批准才会执行。
4. `warm-caches.yml`：依赖锁文件或版本 pin 变了的时候（另外每天一次），在 main 上照各套件的 key 装一遍依赖（包括测试分片用 apt 装的那几个工具的 .deb 包）并存下缓存，不跑测试。PR 上的套件基本只恢复、不保存：从 PR 的 ref 存的缓存，别的 ref 读不到。例外是测试分片为一条快照用例装的那份 jsdom——它有自己按版本取名的 key，谁跑谁存，main 上那一份同样对所有 ref 可用。
5. `coverage-map.yml`：每天两次，在 `main-green` 上带覆盖率跑整层 integration，发布正式 PR 精选 integration 用的映射表。带覆盖率比平时慢约 37%，所以不放在任何有人等结果的运行里；缺一片就不发布，PR 继续用上一份。

## PR 上的两个机器人 {#bots}

- `claude-md-review.yml`：改了 `AGENTS.md` 的 PR 需要人批准。`AGENTS.md` 每一轮都会整份加载，一行错的指令会被每个 agent 执行，所以这是全仓库唯一一个需要人批准的文件。

## 定时巡检 {#scheduled}

备份新鲜度、每周恢复演练、主机与网站可达性、部署漂移和网关健康、runner 维护，都由定时工作流完成，失败会发邮件给管理员。定时规则见 [CI 工作流全表](/dev/ref-ci#workflows)。
