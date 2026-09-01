# #187 现在还差什么

## 目标

把「记忆系统」这个 issue 推到能关掉的状态。今天的现象一句话：**线上跑的还是 `db` 后端，它不做自动抽取，所以平台几乎不长记忆**——这正是 #187 最初量到的那件事。

## 核实过的现状（2026-08-29 对着 main 6f6af8971 逐条读码）

| 事项 | 状态 | 依据 |
|---|---|---|
| 默认后端 | 仍是 `db` | <&backend/app/core/config.py:473> `memory_backend: str = "db"` |
| 自动抽取只在 openviking 上有 | 属实 | <&backend/app/domain/agent/chat.py:4059> `if settings.memory_backend != "openviking": return`；db 后端只能靠 `cheese remember` 手动写 |
| 数据目录挂载 | 已就位 | <&deploy/compose/docker-compose.base.yml> 有 `OPENVIKING_DATA_DIR=/data/viking` + 无条件绑定挂载 |
| 关停一致性 | 已就位 | <&backend/app/main.py:219> lifespan 退出时关 OpenViking |
| 迁移脚本进生产镜像 | 已就位 | <&backend/Dockerfile> 单独 COPY `scripts/migrate_memory_to_openviking.py` |
| 无 key 回归测试 | 已就位 | <&backend/tests/integration/test_openviking_fake_endpoint.py> + <&backend/tests/support/fake_model_endpoint.py> |
| 运维步骤文档 | 已就位 | <&docs/infrastructure.md:234> 《Turning on the openviking memory backend》 |
| #577（挂载 + 关停 + 迁移脚本 + 测试） | 已合并关闭 | GitHub |
| #582（dreaming：沙箱回收前整理记忆） | **PR 还开着，且 `mergeable_state=dirty`，08-18 后没动过** | GitHub，分支 topic/109efa7c，6 commits / 15 文件 |
| #271 抛出的两个决定 | **已经落地了，不再是待办** | 作用域=per-agent 池（`MemoryScope.agent_project`，<&backend/app/domain/memory/models.py:28>）；显著性分层=`MemoryLayer`（同文件 :31，`layer` 列已在表上） |

## 要开起来，还差三件（前两件是 issue 正文说的，第三件是我新查出来的）

**一、把开关拨过去 + 一把真的智谱 key。** 在目标机器的 `backend/.env` 里加三行，然后**重部**当前 sha（`docker restart` 不生效，env_file 是建容器时读的），再跑一次迁移脚本。步骤见 <&docs/infrastructure.md:242>。
key 必须同时能调 chat 和 embedding：抽取器每记一条事实要花一次 chat 调用，只在 embedding 上有效的 key 换来一个什么都存不下的后端。网关那把 `sk-che...` 是虚拟 key，直连智谱会 401——**它是回落默认值**（<&backend/app/domain/memory/openviking_store.py:113-114>），所以「忘了配 key」不会报错，只会静默走到 401。

**二、`.viking` 没有任何备份。** 翻牌那一刻，`/home/nictheboy/cheese-viking` 就成了一个新的数据库——不在 Postgres 里、不在镜像里。现有备份作业只覆盖 PG 和 uploads（<&deploy/README-backup.md>：`db-backup.sh` 每小时 :00、`r2-sync-uploads.py` 每小时 :30，没有第三条）。**这一条应该在翻牌之前做完**，否则一次机器重建就把记忆全清了。

**三、配错 key 会静默失败，需要一道启动自检。** 自动抽取跑在后台任务里（`_schedule_memory_extraction` → `ingest_turn` → OpenViking 自己的 background task），端点 401 只会写日志，界面上看不出任何异常——表现和「db 后端不记东西」一模一样，也就是说**翻牌之后你无法凭观察判断它到底通没通**。建议加一个启动期或 `/healthz` 层面的探针：切到 openviking 时打一次 embedding + chat，失败就吵。

## 平行的一条：#582 dreaming

「定期整理记忆」这条边是 PR #582，从 08-18 起停在冲突状态（dirty）11 天。它和翻牌互不阻塞，但记忆池开始长东西之后，没有整理机制会越堆越脏。**要么有人接手把 main 合进去解冲突、要么明确关掉。**

## issue 正文里仍然成立、但不归 #187 的

「进度层缺一块」：芝士干活的 checklist 状态存在对话记录里，随机器一起死。这条边归 #184，还开着，08-08 后没人动。

## 待办 / 谁做

| # | 事 | 谁 | 阻塞 |
|---|---|---|---|
| 1 | 给 `.viking` 加一条备份（脚本 + timer + README-backup.md） | **已派出**：支线「viking 备份线」`c71a11fa` | 无 |
| 2 | 加「切到 openviking 就自检端点」的探针 | **已派出**：支线「openviking 端点自检」`2ce20965` | 无 |
| 3 | 弄到一把有 embedding 权限的智谱 key | **必须人**：@蔡松洋 / @andy | 采购 |
| 4 | 上机改 `.env`、重部、跑迁移 | **必须人**（芝士上不了机器） | 依赖 1、3 |
| 5 | #582 解冲突或关掉 | 芝士能做，需先拍板要不要它 | 拍板 |


## 进展

**2026-08-31：第 1、2 条已派出两条支线，正在做。**

派之前重新对着 main `6d5ff5e55` 核实了一遍前提，三条全部仍然成立：`memory_backend` 默认还是 `db`；viking 不在任何备份里（grep 过 `deploy/` 和 `scripts/`，唯一命中是 etrip 部署 rsync 的 `--exclude='.viking'`，即明确排除）；`openviking_store.py:113-114` 回落到网关虚拟 key 那两行还在。

两条支线的边界（防撞车，已写进各自简报并用 `--paths` 声明）：

| 支线 | 拥有 | 禁止碰 |
|---|---|---|
| viking 备份线 `c71a11fa` | `deploy/` 全部；`docs/infrastructure.md` 里「That directory IS the database…needs its own backup line」那一条 bullet | `backend/` |
| openviking 端点自检 `2ce20965` | `backend/` 全部；`docs/infrastructure.md` 那一节的**其余**部分 | `deploy/` |

两条都被要求：局部编辑 `docs/infrastructure.md`、不许整块覆盖；改完一小块就 commit + push；**不许自己递验收卡**（共用一条分支，卡的互斥按树算，谁先递谁把别人堵死）——递卡由本房间统一调度。

留给我的两个决定，等它们回来时要拍：
- 备份期间后端在写会拿到撕裂快照，允许到什么程度（支线会给方案和代价）。
- 自检放启动期还是只放 `/health/detailed`——放启动期意味着一次外部 API 抖动可能触发 `deploy-docker.sh` 的健康检查回滚。


## 2026-09-01：两条支线进度

**viking 备份线 `c71a11fa`：活干完了，证据我自己验过。** 三个提交已在共用分支 `topic/32137159` 上——`377fb5e6b` 备份脚本 + 两个 systemd unit（145 行脚本，每 6 小时 :15，避开 DB 的 :00 和 uploads 的 :30）、`358a8ed20` 314 行测试 + 接进 `deploy-scripts-test.yml`、`742248df5` README-backup.md 的恢复步骤 + `docs/infrastructure.md` 那条 bullet 已按现状改写。

我在房间工作区**亲自跑了** `deploy/tests/test-viking-backup.sh`：**14 条全 PASS**（不是转述它的说法）。

它对「热备份一致性」这个我留给它拍的问题给了方案：tar 前后各按「类型/路径/大小/亚秒 mtime」给整棵树打一次指纹，一致就是干净快照，三次都撞上写入就仍然保留但命名成 `cheese-viking-<ts>-hot.tar.gz`，让恢复的人看得见并优先挑安静的那份。顺手还排除了 `ov.conf`（里面是明文模型 key，且后端每次启动都会从 settings 重写，备份它等于把密钥推到 R2 却什么都不买）。这两个判断我认可。

未结：它还没 `cheese conclude`，已催它逐条补证据，另问了两件事——为什么动了 `--paths` 之外的 `.github/workflows/deploy-scripts-test.yml`，以及 `-hot` 归档在恢复时怎么取舍。

**openviking 端点自检 `2ce20965`：工作区被整个重建，改动全丢，正在重做。**

它回信说 reflog 只剩 `clone` → 一条空记录 → `reset: moving to HEAD`，落在备份线的提交上；它先前合 main 的提交和对 `openviking_store.py` 的两处改动都没了，而 `git status` 干净、无任何提示。就是简报里警告的那个坑——**教训要写进以后所有简报：「改完一小块就 push」里的 push 必须是「第一小块就 push」**，它把第一次 push 拖到了动完刀之后。

**它自己把那个决定拍了，理由过硬，我核实后采信：启动期 + `/health/detailed` 两处都做，但不碰 `/healthz`。**
我原来担心「自检失败会挡住/回滚一次发布」——**这个担心在当前代码上不成立**，我在房间工作区逐条验过：
- `deploy/deploy-docker.sh:424` 起的 health 等待循环**不打任何 HTTP**，它把 `docker ps` 的容器状态喂给 `check-app-tier.sh`，只看容器是不是 `(healthy)`；
- 容器的 healthcheck 是 `docker-compose.base.yml:80` 的 `curl -fsS http://localhost:8081/healthz`；
- 全仓库**没有任何发布脚本**调 `/readyz` 或 `/health/detailed`（grep 只命中 OpenAPI 定义，以及 `cheesex-healthcheck.sh:5` 一句说 `/readyz remains the dependency-aware deployment gate` 的注释——**那是句过时的注释，该脚本实际 curl 的是另一个端口的 `/health`**）。

所以只要不碰 `/healthz`，自检不可能影响发布。它另外拍的一条也合理：`/readyz` 里把检查分成「必需」（database/redis，行为完全不变）和「咨询性」（memory）——memory 挂了 `/health/detailed` 报 degraded 并显示具体错误（这就是给人看的信号），但**不 503**，因为记忆端点挂是功能降级，不是把整个平台摘出流量池的理由。

## #582（dreaming）：不是解冲突，是挂载点被删了

实际 merge 跑过（`git merge-tree main topic/109efa7c`），冲突只有三个文件，但性质完全不同：

| 文件 | 性质 | 处理 |
|---|---|---|
| `backend/.env.example` | **机械冲突**：两边各在同一位置追加了一段新配置（582 加 dreaming 三项，main 加 `MEMORY_BACKEND`/`OPENVIKING_*` 一节） | 双方都保留即可 |
| `backend/alembic/HEAD` | **标准迁移分叉** | 把 `b1d47f0a3c25` 的 `down_revision` 改指 main 的 `e4c9a2f60b18`，HEAD 写 `b1d47f0a3c25` |
| `backend/app/domain/scheduler/service.py` | **真问题**：582 侧 156 行 vs main 侧 0 行 | 见下 |

**根因**：#582 的整个设计是「把整理挂在沙箱回收那一刻，因为那是最后一次还能拿记忆里的说法去对工作区」（PR 正文原话：*Hence the hook in `reap_idle_containers` rather than a job of its own*）。而 `c45826d5f`（#630「retire the platform's own box」，8-28 合入）**把 `reap_idle_containers` 连同整个容器载体一起删了**——`list_sandbox_containers` / `remove_container` 在 main 的 `backend/` 里现在是**零命中**。scheduler 里活下来的只有 `reap_idle_device_screens`（`jobs.py:58` 调度，走 `device_hub.all_online_screens()`，回收方式是 `release_topic_screen`）。

好消息：dreaming 的**逻辑本身与载体无关**——`_start_dream_if_worthwhile` 最终只调 `get_work_runner().submit_kickoff(...)`，不碰 Docker。所以 1200 行里真正被打死的只是那个挂载块。

**受影响 / 不受影响的清单**（15 个文件里）：
- 不受影响：`memory/dream.py`(384 行)、`memory/models.py`、`memory/store.py`、`memory/schemas.py`、`routes/memory_dreams.py`、`routes/memory.py`、迁移、`main.py`、`config.py`、`test_memory_dream.py`(336 行)、`test_memory_dream_api.py`(113 行)。
- 要重做：`scheduler/service.py` 的挂载块（~157 行）、`test_dream_reap.py`（241 行 / 7 条测试，**每一条都在 monkeypatch `ws.list_sandbox_containers` 和 `ws.remove_container`**，全部要改成 device screen）。

### 三条路（等 <@caisongyang> 选）

**A. 改挂到 `reap_idle_device_screens`** —— 保住原设计意图（临死前在机器上整理）。改动面就是上面那两块；`all_online_screens()` 直接给 `(project_id, topic_id)`，比 582 原来「容器名反查 topic hex」还简单些。
**缺口（必须知道再选）**：#630 之后一轮落在 device 或 Cloud 上，而 scheduler 里 **Cloud 侧没有任何回收钩子**。所以整理只覆盖跑在自建设备上的话题，Cloud 话题永远轮不到。工作量：一条支线一轮左右。

**B. 脱钩成独立后台作业** —— PR 正文论证过这条不行（「后端作业只能做字符串去重」）。#630 之后这论证削弱了一半（平台自己已经没盒子了，工作区在设备那边，后端照样够不着），但结论不变：放弃「对着工作区核实」这一半价值。好处是覆盖全部话题、不再跟着载体变。不推荐单做。

**C. 关掉 #582，代码留在分支上，等真翻牌了再重开** —— dreaming 是给「会自动长记忆」的后端擦屁股的。**线上现在还是 `db` 后端，不自动抽取，记忆池不会自己变长变乱——它现在解决的是一个还没发生的问题。** 而且这个 PR 已经被分支覆盖 bug 咬过一次（它的 tip 提交名就叫 *Merge the empty tip that overwrote this branch*），放着只会继续烂。代价：1200+ 行含 690 行测试失去 PR 上下文，但分支还在，重开时 cherry-pick。

**我的判断**：翻牌若在近期排期，选 **A**，让 dreaming 和翻牌同一批上线；翻牌若还没排期，选 **C**，别现在付「重做挂载点 + 重写 7 条测试」的钱去修一个暂时用不上的东西。
