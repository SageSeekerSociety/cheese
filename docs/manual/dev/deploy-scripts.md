---
title: 部署脚本与主机定时任务
kind: 参考
summary: deploy/ 下的发版脚本、镜像计划、systemd 定时器和主机维护脚本各管什么。
covers:
  - deploy/deploy-docker.sh
  - deploy/deploy.sh
  - deploy/image-tag.sh
  - deploy/check-app-tier.sh
  - deploy/check-auto-deploy.py
  - deploy/check-forge-workspace-writers.py
  - deploy/release-device-connection.sh
  - deploy/release-gateway.sh
  - deploy/release-metering-proxy.sh
  - deploy/release-cloud-control.sh
  - deploy/db-backup.sh
  - deploy/db-restore-test.sh
  - deploy/r2-upload.py
  - deploy/r2-sync-uploads.py
  - deploy/etrip-backup.sh
  - deploy/dev-box-disk-cleanup.sh
  - deploy/cheesex-disk-pressure-guard.sh
  - deploy/cheesex-healthcheck.sh
  - deploy/evict-foreign-container.sh
  - deploy/fix-workspace-ownership.sh
  - deploy/reclaim-room-caches.sh
  - deploy/reclaim-legacy-room-checkouts.py
  - deploy/trigger-room-cleanup.sh
  - deploy/install-disk-cleanup-timer.sh
  - deploy/install-room-cleanup-timer.sh
  - deploy/install-cloud-control.sh
  - deploy/cloud-control.py
  - deploy/bootstrap-forgejo.py
  - deploy/cutover-sqlascii-to-utf8.sh
  - deploy/migrate-device-routes.py
  - deploy/compose/
  - deploy/systemd/
  - deploy/tests/
  - .github/scripts/plan-image-builds.sh
  - .github/workflows/deploy-scripts-test.yml
  - .github/workflows/deploy-dev.yml
  - .github/workflows/deploy-prod.yml
  - .github/workflows/deploy.yml
---

# 部署脚本与主机定时任务 {#deploy-scripts}

`deploy/` 是发版和主机维护的全部入口：一个统一发版脚本、一套按提交号决定重建哪些镜像的计划、九个 systemd unit（八个定时器加一个常驻服务）、一批一次性运维脚本，以及这些脚本自己的测试。

> 讲：`deploy-docker.sh` 怎么替换与回滚、镜像从哪来、每个 timer/service 干什么、`deploy/` 顶层脚本各一句话、这些脚本在哪被验。不讲：部署拓扑本身（见[部署拓扑](/dev/topology#planes)）、CI 的门与 runner（见 [CI 设计](/dev/ci#runners)）、备份策略与恢复（见[数据存在哪](/dev/data#backup)）、主机磁盘的三条回收路径（见[资源回收与磁盘](/dev/cleanup#disk)）、四个后端进程各自管什么（见[后端结构与接口约定](/dev/backend-app#processes)）。

## 发版：deploy-docker.sh {#deploy-docker}

用法 `deploy/deploy-docker.sh <image-sha> [compose-file]`，第二个参数默认 `deploy/compose/docker-compose.base.yml`（`deploy/deploy-docker.sh:1-20`）。dev 和 prod 跑的是同一个脚本、同一套 compose，差别只在门禁：`.github/workflows/deploy-dev.yml:249` 与 `.github/workflows/deploy-prod.yml:148`。

一次发版按下面的顺序走，每步失败都在换流量之前退出：

| 阶段 | 依据 | 做什么 |
|---|---|---|
| 记下现状 | `service_container`/`service_image`（`:529-542`） | 存 `PREV_BACKEND_IMAGE`、`PREV_FRONTEND_IMAGE`、`PREV_SHA`；回滚只靠这三样 |
| 拉镜像 | `retry_pull`（`:496`）、`pull_backoff_delay`（`:478`） | 带退避重试；可选服务（浏览器渲染、office 渲染等）拉不到只警告 |
| 准备常驻件 | `ensure_forgejo`、`ensure_forge_events`、`ensure_application_router`（`:124-209`） | Forgejo、事件中继、app-router 不在就先起 |
| 迁移 | `:649-651` | `dc run --rm backend sh -c "alembic upgrade head"`，失败直接停 |
| 归属 | `OWNERSHIP_MIGRATED`（`:685`） | 跑 `fix-workspace-ownership.sh`，报告留给回滚用 |
| 起新槽位 | `rollout_app`、`read_slots` | 主 API 和前端各有两个槽位（`backend`/`backend-b`、`frontend`/`frontend-b`，前端在回环端口 18088/18084 上）。按 app-router 的 `backend.conf`/`frontend.conf` 认出正在服务的那个，把另一个用新镜像 `up -d --no-deps --force-recreate` 起来，等它应答健康检查；起不来就删掉新起的，**正在跑的那个一直没被动过** |
| 切流量 | `switch_app_router` | 两个上游文件一起写（先写临时文件再 `mv`，半写的文件会带塌整个 nginx 配置），**只 reload 一次**；`nginx -t` 或 reload 失败就把两个文件改回去 |
| 交接、排空 | `hand_over_running_work`、`DRAIN_SECONDS=${DEPLOY_DRAIN_SECONDS:-31}` | 切完马上换 collab；5 秒后给旧主 API 发 SIGUSR1 交出正在跑的工作；旧槽位继续答完手上的请求 31 秒 |
| 收尾 | `take_frontend_ports` | 旧前端（最多 120 秒）和旧主 API（`docker stop --time 60`）同时优雅停下再删掉；盒子的 :8080、:80 写在 app-router 的 `frontend.conf` 里，转给正在服务的前端（每个版本的 `app-router.conf` 都 include 这个文件，旧提交的发版也留得住）。最后一个自己占着这两个端口的 compose 前端只给 5 秒停下，之后才把端口加进去，那一次发版多 reload 一次；加不进去就把那个前端重新拉起来服务端口，下一次发版在切流量时接过去；下次发版回到这次空出来的槽位 |
| 判定 | `check-app-tier.sh`（`:955`） | 把 `docker ps` 的输出喂给它，断言 app 层的镜像 tag 就是本次 sha |
| 回收 | `reclaim_docker_disk`（`:434`）、`retain_ci_service_images`（`:409`）、`promote_image_retainer`（`:1016`） | 每次发版拉 6-7GB 新镜像，成功后立刻回收被顶掉的；`on_exit`（`:460`）保证失败、`set -e` 中止、runner 取消也回收 |
| 定时器 | `:1009-1013` | 装 `cheese-room-cleanup.timer`；主机没有 systemd 时只在日志里提示安排外部触发 |

**回滚**（`:968-996`）：健康检查失败、`PREV_SHA` 非空且与本次不同，就用记下的两个镜像引用重新 `up -d backend frontend`；有 app-router 的盒子则把旧镜像按同样的方式放回空出来的槽位，再切一次。如果这次发版做过归属迁移（`OWNERSHIP_MIGRATED=yes`），先把绑定挂载交还给 `PREVIOUS_AGENT_UID`（默认 1001）——否则旧镜像会起在一棵它读不了的树上。没有可回的目标时只报错退出。

镜像有两种来源：`DEPLOY_APP_IMAGE_SOURCE=registry`（默认）和 `local`；`local` 模式下 `BACKEND_IMAGE`/`FRONTEND_IMAGE` 必填，且失败路径不会回收本地构建的镜像（:609-619）。

## 镜像从哪来 {#images}

`.github/workflows/build.yml` 的 `plan` job 调 `.github/scripts/plan-image-builds.sh`，按「最近一个镜像齐全的提交」决定这次重建哪几个。产物一共八个：`backend`、`frontend`、`office_render`、`browser_render`、`gateway`、`metering_proxy`、`private_executor`、`collab`。

- **基线**：沿本分支的第一父链往前找，取第一个在 ghcr 里八个镜像 tag 都齐全的提交。基线那次 workflow 里没有构建任务的镜像不要求有 tag，这次直接构建。不查 workflow run 列表：那个接口会返回几周前的旧数据，基线随之跳回旧提交、八个镜像全部重建。手动构建推送的 tag 同样算数；构建还没跑完或中途失败的提交会被跳过。找到仓库第一个提交仍没有齐全的就整批重建；查满 200 个提交或 registry 不应答则计划失败，不输出构建计划。
- **按路径映射**（`plan-image-builds.sh:135-205`）：`backend/*`、`cli/*` → backend；`frontend/*`、`docs/manual/*`、`docs/site/*`、`backend/sandbox/cheese`、`backend/app/core/config.py` → frontend（文档站是打进前端镜像的）；`deploy/gateway/*` → gateway；`deploy/metering-proxy/*` → metering_proxy；`deploy/office-render/*`、`deploy/browser-render/*` 各自；`backend/sandbox/Dockerfile.private`、`backend/sandbox/cheese`、镜像复制进去的几个 `remote_execution/*.py` 和 `backend/app/domain/fetch/addresses.py` → private_executor；`frontend/collab/*`、`frontend/src/lib/docSchema/*`、`frontend/package.json`、`frontend/pnpm-lock.yaml` → collab。
- **没重建的镜像不丢 tag**：workflow 把基线那次留下的镜像 `pull` + `tag` + `push` 成本次的 tag（`build.yml:88-147`），所以某次提交的八个 tag 总是齐的。
- **tag 形状**由 `deploy/image-tag.sh` 定：完整 sha 的前 7 位，跟 docker/metadata-action 的 `type=sha` 一致。**不用** `git rev-parse --short=7`——那是下限，clone 里别的对象撞上前缀时它会变长，同一个提交在全量克隆和浅克隆里会得到不同的名字。
- 判定结果同时写进 stderr，不再只进 `$GITHUB_OUTPUT`：谁重建了、基线是谁，日志里读得出来。

## systemd 定时器 {#timers}

`deploy/systemd/` 里的八个定时器加一个常驻 service，都装在主机上（不是容器里）：

| timer | 频率 | 做什么 | 执行体 |
|---|---|---|---|
| `cheese-db-backup.timer` | 每小时整点（`RandomizedDelaySec=120`，`Persistent=true`） | `pg_dump -Fc`、校验、清理旧份、传 R2 | `/home/nictheboy/ops/db-backup.sh` |
| `cheese-uploads-mirror.timer` | 每小时 :30 | `uploads/` 增量镜像到 R2（`UPLOADS_PREFIX=prod-uploads`） | `~/ops/r2-sync-uploads.py` |
| `cheese-transcripts-mirror.timer` | 每小时 :45 | 同一个脚本，会话记录归档目录（`UPLOADS_PREFIX=transcripts`） | 同上 |
| `cheese-etrip-backup.timer` | 每小时 :15 | etrip 的两个 Postgres 容器 dump + 上传卷 tar → R2 | `/root/ops/etrip-backup.sh` |
| `cheese-room-cleanup.timer` | 每分钟（`AccuracySec=1s`） | 触发一次归档房间的到期清理 | `/usr/local/lib/cheese/trigger-room-cleanup.sh` |
| `cheese-disk-cleanup.timer` | 每天 04:00（±30 分钟） | 高于脚本自己的标记（75%）才回收可再生缓存 | `/usr/local/lib/cheese/dev-box-disk-cleanup.sh`，先由 `ExecCondition` 跑 `--needed` |
| `cheesex-healthcheck.timer` | 开机 2 分钟后、每 30 秒 | 探 `127.0.0.1:8099/health`，连续 3 次失败就重启 `cheese.service` | `/usr/local/sbin/cheesex-healthcheck` |
| `cheesex-disk-pressure-guard.timer` | 开机 5 分钟后、每 5 分钟 | 85% 以上、且确认没有进行中的会话，才删沙箱容器与陈旧 `/tmp` | `/usr/local/sbin/cheesex-disk-pressure-guard` |
| `cheese-cloud-control.service` | 常驻（用户级 unit） | 见下 | `~/.local/lib/cheese-cloud-control/cloud-control.py` |

几处值得单独说的：

- **执行体是盒子上的稳定副本，不是仓库里的路径**。`cheese-db-backup.service` 自己写了 "canonical source is `deploy/db-backup.sh`"，`ExecStart` 却是 `~/ops/db-backup.sh`；几个 `r2-*` 用的是 `~/ops/` 和 `~/cheese-backend-py/backend/.venv`。改仓库不会改盒子，得把那几份拷过去。
- **`@CLEANUP_USER@`/`@CLEANUP_HOME@` 是占位符**，由 `deploy/install-disk-cleanup-timer.sh` 安装时填成真实用户——缓存长在那个人的 `HOME` 下（`~/.cache/go-build`、`~/.vscode-server`、`~/.cache/pip`），以 root 跑只会清 root 的空目录然后报成功。同一个脚本把 `dev-box-disk-cleanup.sh` 装到 `/usr/local/lib/cheese/`。
- **`cheese-room-cleanup.service` 由发版脚本自己装**（`deploy-docker.sh:1009`，调 `install-room-cleanup-timer.sh`）：宿主时钟不随 app 层滚动而丢。它按 label `li.zhifei.cheese.room-cleanup=true` 找参与的房间后端，再逐个 `docker exec ... app.domain.topic.cleanup_trigger`。
- **两个 `cheesex-*` unit 在仓库里没有安装脚本**。它们的 `ExecStart` 指向 `/usr/local/sbin/`，仓库里的 `deploy/cheesex-healthcheck.sh` 与 `deploy/cheesex-disk-pressure-guard.sh` 是同名不同路径的源。这两个 unit 本身也带 `ConditionPathExists`/`ReadWritePaths` 之类的加固项，属于 out-of-band 安装。
- **`cheese-disk-cleanup.service` 的判断在脚本里**：unit 只问 `--needed`，因为要问的不只是 `/`——这些机器的 `/tmp` 是单独的 tmpfs，先满的是它（见[资源回收与磁盘](/dev/cleanup#disk)）。

## deploy/ 顶层脚本一览 {#scripts}

发版与镜像：

| 脚本 | 一句话 |
|---|---|
| `deploy-docker.sh` | dev 与 prod 共用的统一发版；滚动替换 app 层，健康检查失败会回滚 |
| `deploy.sh` | etrip 盒子的发版入口（`.github/workflows/deploy.yml` 调 `./deploy.sh <sha>`）：拉镜像、迁移、重启、健康检查 |
| `image-tag.sh` | 打印某个 rev 的镜像 tag（完整 sha 前 7 位） |
| `check-app-tier.sh` | 读 `docker ps` 的输出，断言 app 层跑着的镜像 tag 等于给定值；发版和 drift 巡检都用它 |
| `check-auto-deploy.py` | 只允许不把线上往回退的自动发版 |
| `release-device-connection.sh` | 单独替换常驻的设备 WebSocket owner（普通发版永不调它，因为替换会断连接） |
| `release-gateway.sh` | 网关平面单独发版；只在 GitHub Actions 里跑，且要 `GATEWAY_ALLOW_INTERRUPT=1` 明确承认会断流 |
| `release-metering-proxy.sh` | 计量代理平面单独发版；同样要求 `METERING_ALLOW_INTERRUPT=1` |
| `release-cloud-control.sh` | 云机器加密隧道单独发版 |

备份与磁盘：

| 脚本 | 一句话 |
|---|---|
| `db-backup.sh` | 定时逻辑备份：`pg_dump -Fc`、校验可读、保留 30 天 |
| `db-restore-test.sh` | 把最新转储恢复进一个用完即弃的 Postgres 容器，抽查 schema 与数据 |
| `r2-upload.py` | 把校验过的转储传到 Cloudflare R2（异地那一份） |
| `r2-sync-uploads.py` | 把本地 `uploads/` 增量镜像到 R2，只增不删（远端是超集） |
| `etrip-backup.sh` | etrip 专用备份：两个库 dump + 上传卷 tar → R2，保留 14 天 |
| `dev-box-disk-cleanup.sh` | 回收可再生缓存；`--needed` 判断、`--apply` 执行、`--self-test` 自检 |
| `cheesex-disk-pressure-guard.sh` | 磁盘 85% 的急刹：确认没有活跃会话后删沙箱容器与陈旧 `/tmp` |
| `cheesex-healthcheck.sh` | 探本机后端 `/health`，连续 3 次失败重启 `cheese.service` |
| `reclaim-room-caches.sh` | 回收房间在共享 store 之前各自留下的包缓存 |
| `reclaim-legacy-room-checkouts.py` | 回收旧布局留下的房间检出（`~/.cheese/work/<project>/<room>`） |
| `evict-foreign-container.sh` | 清掉占用了固定 `container_name` 的别人的容器 |
| `fix-workspace-ownership.sh` | 把绑定挂载的宿主目录一次性交还给当前镜像的 uid（幂等，留标记） |

安装与触发：

| 脚本 | 一句话 |
|---|---|
| `install-disk-cleanup-timer.sh` | 装 nightly 缓存回收，把真实用户写进 unit |
| `install-room-cleanup-timer.sh` | 装每分钟一次的归档清理触发 |
| `install-cloud-control.sh` | 装用户级 `cheese-cloud-control.service`（要求该用户已开 linger，脚本会先断言） |
| `trigger-room-cleanup.sh` | 按 label 找到参与的房间后端，逐个触发一次归档清理 |
| `cloud-control.py` | 维护云连接器到后端主机的加密 SSH 转发（unit 的实际执行体） |

一次性与迁移：

| 脚本 | 一句话 |
|---|---|
| `bootstrap-forgejo.py` | 初始化部署的 Forgejo 管理员与持久事件中继设置 |
| `cutover-sqlascii-to-utf8.sh` | 把 `SQL_ASCII` 的库重建成 UTF8（配套 `deploy/README-utf8-cutover.md`） |
| `migrate-device-routes.py` | 把受管设备链路一个设备一个设备地搬离应用代理 |
| `check-forge-workspace-writers.py` | 迁移前拒绝执行：还有宿主进程在用源目录 |

非脚本的顶层内容：compose 在 `deploy/compose/`（`base`、`etrip`、`forgejo`、`gateway`、`subscription`），镜像源码在 `deploy/gateway/`、`deploy/metering-proxy/`、`deploy/office-render/`、`deploy/browser-render/`，ingress 与 TLS 在 `deploy/llm-tunnel/`，CI runner 的 provision/prune/guard 在 `deploy/ci-runner/`，另外还有 `deploy/docker-compose.prod.yml`（etrip 的整栈）和三份 README（`README-backup.md`、`README-room-cleanup.md`、`README-utf8-cutover.md`）。

## 单独发版的四个平面 {#planes}

常驻服务不能跟着 app 层一起滚，所以各有自己的 workflow 和脚本：

| 平面 | 脚本 | workflow |
|---|---|---|
| 设备连接 ingress | `release-device-connection.sh` | `.github/workflows/release-device-connection.yml` |
| 网关 | `release-gateway.sh` | `.github/workflows/release-gateway.yml` |
| 计量代理 | `release-metering-proxy.sh` | `.github/workflows/release-metering-proxy.yml` |
| 云隧道 | `release-cloud-control.sh` | `.github/workflows/release-cloud-control.yml` |

共同点：都要求完整的 40 位 main sha（`^[0-9a-f]{40}$`），都必须显式承认会打断在跑的流；网关和计量那两个脚本干脆只认 GitHub Actions 环境加 `*_ALLOW_INTERRUPT=1`，手动在盒子上跑会被拒。dev 的发版流程在完成 app 层替换后会顺带发一次计量代理（`.github/workflows/deploy-dev.yml:255-262`，前面先过 `check-auto-deploy.py --require-ci`）。

## 这些脚本的测试 {#tests}

`deploy/tests/` 是这套脚本的测试，入口是 `.github/workflows/deploy-scripts-test.yml`——`workflow_call`，由 `.github/workflows/required-ci.yml:77` 调进来，所以它是必过检查的一部分。

为什么值得单独一个 workflow（workflow 头部自己写的）：`test.yml` 只覆盖 `backend/**`，从前 `deploy/` 下的改动**没被任何人执行过**就这么上了机器；2026-08-11 的 dev 故障（一次归属交接排在还能中止它的检查之前）就是这样带过去的——六个绿 job，没有一个碰过那个文件。

里面的套房全是 bash/python 打在假件上：不起容器、不要 registry、不要 docker，一分钟内跑完。第一件是对 `deploy/` 和 `scripts/` 下每个 `*.sh` 做 `bash -n`：发版脚本的语法错误以前只能在机器上、app 层已经换到一半的时候才发现。

| 组 | 套房 |
|---|---|
| 发版契约 | `test-app-tier-health.sh`、`test_auto_deploy.py`、`test-workspace-ownership.sh` |
| 常驻 ingress | `test_ingress_lifecycle.py`、`test_front_door_tls.py`、`deploy/llm-tunnel/verify-sites.py` |
| 网关与计量 | `test_metering_release.py`、`test_metering_health.py`、`test_gateway_release.py`、`test_gateway_health_workflow.py`、`test_claude_login.py`、`backend/scripts/test_gateway_supply_probe.py`、`.github/scripts/test-plan-image-builds.sh` |
| Forge 配置 | `test_forge_config.py`、`test_forge_workspace_writers.py` |
| 磁盘与容器 | `test-disk-pressure-guard.sh`、`test-evict-foreign-container.sh`、`test-dev-box-disk-cleanup.sh` |
| CI runner | `test-ci-runner-job-hook.sh`、`test-ci-runner-disk-guard.sh`、`test-ci-runner-provision.sh`、`test-ci-runner-prune-retirement.sh`、`test_action_archive_cache.py` |
| 云隧道 | `test_cloud_control.py` |
| 备份与 Forgejo | `test-db-restore-test.sh`、`test_forgejo-bootstrap.sh` |

假件在 `deploy/tests/fakes/`（`app-tier`、`ownership`）。备份能不能用另有每周一次的恢复演练（见[数据存在哪](/dev/data#verify)），drift 巡检见 [CI 设计](/dev/ci#scheduled)。

## 边界与坑 {#traps}

- **迁移没有反向**。`alembic upgrade head` 排在换流量之前，失败就停在旧版本，这很好；但它一旦跑过，回滚镜像不会把 schema 降回去。回滚恢复的是镜像，不是数据形状。
- **`cheese-db-backup.timer` 的描述与它实际频率不符**：`Description` 写 "every 6 hours"，`OnCalendar=*-*-* *:00:00` 是每小时；`db-backup.sh` 的注释又写 "every few hours"。实际频率以 `OnCalendar` 为准（每小时），[数据存在哪](/dev/data#backup)写的也是每小时。改的时候要同时改这三处。
- **仓库里的 `cheesex-*.sh` 改了不代表盒子上会变**。两个 unit 的 `ExecStart` 是 `/usr/local/sbin/cheesex-healthcheck` 与 `/usr/local/sbin/cheesex-disk-pressure-guard`，仓库里没有任何脚本把它们装到那里。
- **发版脚本会主动拒绝而不是自动纠正**：`read_slots` 发现 app-router 指着的端口不是两个槽位之一，就直接失败，要人工恢复。槽位之前的版本中断后留下的 `cheese-backend-next`/`cheese-frontend-next`，app-router 没指着它就由 `clear_interrupted_release` 在拉镜像、迁移之前删掉，指着它就在那里失败。手动发布两个槽位之前的旧提交时，旧脚本会先装上自己的 `app-router.conf` 并 reload（30 秒后断开 app-router 上的连接），拉镜像、跑迁移，然后在 `-b` 槽位服务期间拒绝切流量。如果回滚是在 `-b` 槽位服务期间合进 main 的，main 上已经没有带槽位的提交，此后每次自动发版都会这样走一遍再拒绝，直到有人对最后一个包含 #2770 的 SHA 手动触发发版，把服务换回第一组槽位，main 的下一次发版才能过。`deploy/tests/test-pre-slot-release.sh` 用最后一个这样的提交的脚本验证两种状态。同理 `switch_app_router` 的 `nginx -s reload` 失败会把上游文件改回去、失败整个发版，而不是让配置生效一半。
- **`check-app-tier.sh` 证明的是"跑着的镜像对不对"，不是"服务活不活"**。它读的是 `docker ps` 输出的 tag。健康探针是另一个东西：`/healthz` 是进程级探针，带依赖检查的是 `/readyz`（`backend/app/api/routes/health.py`）。
- **没有 systemd 的主机上，归档清理不会发生**：`deploy-docker.sh:1009-1013` 检测不到 `/run/systemd/system` 时只在日志里说一句"自己安排每分钟触发"，不会失败也不会兜底。
- **`deploy.sh` 与 `deploy-docker.sh` 是两条不同的路**：前者是 etrip 盒子的发版入口，只做拉镜像、迁移、重启、健康检查，没有滚动、没有排空、没有回滚；dev 和 prod 走的是后者。
