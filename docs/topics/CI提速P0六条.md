## 状态：六条全部完工，已跑通本地可跑的检查，验收卡已递给 wangchangxin，等待其批准

执行「CI 提速执行方案」里的 P0 六条（零成本配置修复），只做 P0，未碰 P1~P4 和禁区。改动只涉及 `.github/workflows/{e2e,test,build}.yml`、`deploy/deploy-docker.sh`、`deploy/tests/test-app-tier-health.sh`、`CLAUDE.md`（仅一行）。

## 环境核实（开工前）

- 平台本地 main（`d47638c8`,2026-08-09 19:05 UTC 刚同步上游）与本沙箱 checkout 逐字节一致，不存在"本地落后上游"问题。
- `backend/pyproject.toml:4` = `requires-python = ">=3.13"`；`backend/.python-version` = `3.13`。
- PR #208 确认未合并（CLAUDE.md 最近改动是 #63），所以只动 Python 版本那一行是安全的。
- GitHub token 实测无 `pulls`/`commits` 读权限（403），核实全部改用共享工作区 `git show main:<path>` 完成。

## 逐条完工情况 + 验收证据

**P0-1 · e2e.yml concurrency 块** — `group: ci-e2e-${{ github.ref }}` 与 test.yml(`ci-test-...`)/build.yml(`ci-build-...`)同构命名（`ci-<workflow>-<ref>`）。e2e.yml 的触发条件（push/PR 到 main，paths 含 backend/frontend/e2e）与 test/build 完全同构，同一次 backend 改动会同时触发三条 workflow，此前只有 e2e 没有 cancel-in-progress，新 push 不会取消它的僵尸 run。

**P0-2 · 8 个 job 补 timeout-minutes** — build.yml(plan 10m / build-backend 45m / build-sandbox 60m / build-frontend 30m)、test.yml(scope 5m / migration-heads 10m / test 20m)、e2e.yml(scope 5m)。scope/migration-heads/test 三个值直接对应诊断报告的 step 级中位数（0.2m/1.8m/5.1m）留出 3~4 倍余量；build.yml 四个 job 没有 step 级剖析数据，按 job 语义定（plan 纯 gh api；build-backend 单次 docker build+push；build-sandbox 连续两次 docker build 所以给更多；build-frontend 单次 build），已用 `cheese decision` 记录这条依据的性质（合理默认值，非实测）。

**P0-3 · deploy-docker.sh prune 保留规则** — `docker image prune -af` 全脚本只有一处真实调用（`reclaim_docker_disk()` 内部），但该函数被 3 处调用（失败路径 trap / pull 重试前 / 成功收尾），改这一处 `reclaim_docker_disk()` 已覆盖全部 3 个调用点。新增 `retain_ci_service_images()`：对 `CI_POSTGRES_IMAGE`/`CI_REDIS_IMAGE`（默认值与 test.yml/e2e.yml 的 `services:` 镜像 digest 保持一致）分别检查本地是否已存在（`docker image inspect`，不强制拉取，不增加部署耗时），存在则用 `docker create --label com.cheese.image-retainer=ci-<kind> --entrypoint /bin/true` 建一个停止态引用容器——复用这份脚本里 buildkit/sandbox 镜像已验证可行的先例。**没有字面照抄"filter label!="**：docker `image prune` 的 label filter 只认镜像自身的 label，够不到引用它的容器，第三方拉取镜像也无法安全地补自定义镜像 label（会产生新 tag，CI service container 引用的还是原 digest，起不到保护作用）；retainer 容器机制不需要 filter，`prune`(含 `-a`)本就从不清理被任何容器(含已停止)引用的镜像。此判断已用 `cheese decision` 记录。

新增测试 `test_deploy_retains_ci_service_images`（`deploy/tests/test-app-tier-health.sh`，接入 `all`）：用现有 fake-docker 测试框架断言 retainer 创建发生在 `image prune -af` 之前，且引用的镜像与 CI workflow 里的 digest 一致。`bash deploy/tests/test-app-tier-health.sh all` 全部 15/15 PASS（含新增这条），`test-disk-pressure-guard.sh` 11/11 PASS，无回归。

**P0-4 · 删三段过期注释** — test.yml/build.yml 顶部注释、e2e.yml 的 e2e job 注释，三处"hosted minutes ran out mid-2026-07-26"/"account payments have failed" 的过期说法全部改写为准确描述：hosted runner 本身能用，只是私有仓库按分钟计费；重活（docker build、service containers、Playwright）留在 box 上省钱免仿真，轻活（scope 的 gh api 调用）现在挪去 hosted（呼应 P0-5）。

**P0-5 · scope job 挪 ubuntu-latest** — 核对过 test.yml 和 e2e.yml 的 scope job 内容完全一致：只有一步 `gh api` 调用（无 `actions/checkout`，无 box 本地网络/文件依赖），两个都挪了（简报原文只分析了 test.yml 那个，e2e.yml 的是我在核对后按同一逻辑追加的，已用 `cheese decision` 说明理由——同一份代码只挪一半会造成不一致，且 e2e 的 scope 同样会在 box 单 runner 上排队）。

**P0-6 · uv 版本修复** — `uv python install 3.11` → `3.13`（三处：test.yml 的 migration-heads/test，e2e.yml 的 e2e），与 `backend/pyproject.toml`/`backend/.python-version` 的 `>=3.13`/`3.13` 对齐。`setup-uv` 的 `version: "latest"` → `"0.12.1"`（三处同上）——WebSearch 核实的 astral-sh/uv 真实版本，2026-07-31 发布，非编造。CLAUDE.md 只改了"Python >=3.11"→">=3.13"这一行，PR #208 未合并，其余结构完全没动。

**整体：`task check` 真实结果** —`task` CLI 本身在本沙箱不可用，改用等效脚本：`bash .claude/scripts/check.sh --full`——ruff PASS、pyright PASS（0 errors）、pytest FAIL：`database unreachable at localhost:5433`（沙箱没有可用 Postgres，环境限制，不是代码改动导致——这次改动完全没碰 backend/ 的 Python 代码，只碰了 workflow yml / 部署脚本 / 一行 CLAUDE.md）。没有为了让它变绿而删检查或改断言。`deploy/tests/` 下两个 shell 测试套件（`test-app-tier-health.sh all`、`test-disk-pressure-guard.sh`）已实际跑过，全部 PASS。frontend 未改动，未跑 `fe:check`。

## 未做 / 明确排除

- P1~P4、禁区列出的所有条目（`-n 4`、e2e 分片、`enable-cache`、定时任务挪 runner、`claude-review.yml`、`build.yml`/`deploy-dev.yml` 的既有并发/门禁策略）——一律没碰。
