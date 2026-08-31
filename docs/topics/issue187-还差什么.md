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
