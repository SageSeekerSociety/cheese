# #187 记忆系统：现在是什么情况

## 一句话

**没有全部解决。** 线上跑的仍然是 `db` 后端——issue 最初量到的现象（平台几乎不长记忆）今天原样成立。三件前置里**两件已经做完**（在分支上等合并），剩下的两件**只有人能做**：买一把 key、上机改一行配置。

## 逐条对账（对着 issue 正文的每一条）

| issue 正文说的 | 现在 | 依据 |
|---|---|---|
| 默认 `memory_backend = "db"`，不做自动抽取，线上仍是「几乎不长记忆」的那个 | **仍然成立，未解决** | <&backend/app/core/config.py> 默认值仍是 `"db"`；<&backend/app/domain/agent/chat.py> 的 `_schedule_memory_extraction` 第一行就 `if settings.memory_backend != "openviking": return` |
| 一、把开关拨过去（部署里设 `MEMORY_BACKEND=openviking`） | **未做，只有人能做**（芝士上不了机器） | 挂载和关停都已就位，见下 |
| 二、一个真的 embedding key | **未做，只有人能做**（采购） | 回落到网关虚拟 key 会 401，见下 |
| 已经不是问题：数据目录没挂载 | 属实，#577 修了 | `docker-compose.base.yml` 无条件挂 `/data/viking`；`main.py` lifespan 关停时正确关闭实例 |
| 已经不是问题：接线会悄悄烂掉 | 属实，#577 加了假端点测试 | <&backend/tests/integration/test_openviking_fake_endpoint.py> |
| 三层分工那一节没过期 | 属实 | — |
| 「进度层缺一块」（checklist 状态存在对话记录里，随机器一起死） | **仍然成立，不归 #187** | 归 #184，仍开着，08-08 后无人动 |
| dreaming 归 #582，是一个还开着的 PR | **冲突已解干净，等人重跑 CI + 评审** | 见下 |

**另外两条 issue 正文没写、但已经从待办里划掉的**：#271 抛出的记忆作用域和显著性分层**都已经落地**——`MemoryScope.agent_project` 和 `MemoryLayer`/`layer` 列都在表上。任何还说这两条待拍板的文档都过期了。

## 这一轮做完了什么（三条支线）

### 1. viking 备份线 — 完成，已关闭

翻牌那一刻 `VIKING_HOST_PATH` 就成了一个新数据库，而现有备份只覆盖 PG 和 uploads。现在补上了：145 行备份脚本 + systemd unit（每 6 小时 :15，避开 DB 的 :00 和 uploads 的 :30）+ 314 行测试 + 接进 `deploy-scripts-test.yml` + README 恢复步骤。

**房间独立验证**：在房间工作区实跑 `deploy/tests/test-viking-backup.sh`，**14 条全 PASS**。

热备份一致性的解法：tar 前后各给整棵树打指纹（类型/路径/大小/亚秒 mtime），一致即干净快照；三次都撞上写入仍保留，但命名 `-hot.tar.gz`，让恢复的人看得见并优先挑安静的那份。另排除了 `ov.conf`——里面是明文模型 key，且后端每次启动都会重写它。

### 2. openviking 端点自检 — 完成，在房间分支上

原来的问题：`openviking_store.py` 里 key 未配时回落到 `anthropic_auth_token`（网关的 `sk-che...` 虚拟 key），而两个端点默认直连智谱，拿它去调会 401；而抽取跑在后台任务里，401 只落日志，**表现和「db 后端不记东西」一模一样**。翻牌之后无法凭观察判断它通没通。

现在：`endpoint_probe.py`（345 行，真打 `{base}/embeddings` 和 `{base}/chat/completions` 各一次，带超时、缓存 5 分钟且刷新在后台做）；`/health/detailed` 加 `checks.memory`，检查分成必需（database/redis，行为不变）和咨询性（memory 挂了报 degraded 但**不 503**，记忆端点挂是功能降级，不是把平台摘出流量池的理由）；`main.py` 启动自检一次；9 条无 key 无网络的测试。

**为什么不碰 `/healthz`**：发布链路的回滚闸门不看这两个接口——`deploy-docker.sh:424` 的等待循环不打任何 HTTP，只看 `docker ps` 的容器状态，而容器 healthcheck 打的是 `/healthz`；全仓库没有任何发布脚本调 `/readyz` 或 `/health/detailed`。所以自检不可能挡住或回滚一次发布。（房间已逐条核实。）

**附带抓到一个真 bug**（房间读装机的 openviking 包独立核实）：`openai_embedders.py` 的 `_should_send_dimensions()` 在 `provider == "openai"` 时直接 `return False`，而我们的 `ov.conf` 配的就是它。所以请求不带 `dimensions`，向量宽度由厂商模型决定，而索引按 `OPENVIKING_EMBEDDING_DIMENSION` 建——**这个配置项读起来像控制请求宽度，实际只控制索引宽度**。默认 2048 恰好等于智谱 embedding-3 的原生宽度所以没事，一改就是「key 是好的、库是坏的」，且一声不吭。探针现在会比对两者并报错。

### 3. #582 改挂回收器 — 冲突已解，等人重跑 CI

不是普通的解冲突：`c45826d5f`（#630「retire the platform's own box」）把 `reap_idle_containers` 连同整个容器载体删了，而 #582 的设计原话就是「挂在 `reap_idle_containers` 上」。方案 A（<@caisongyang> 拍板）：改挂到活着的 `reap_idle_device_screens`。

分支 `topic/109efa7c`，头 `f649bc71f`，15 文件 / +1563 / −19。**房间独立验证**：`git merge-tree topic/109efa7c main` 退出码 0、冲突 0；GitHub 侧 `mergeable=true`。

**已知并已写进代码的缺口**（`service.py:121` 原文）：一轮不一定跑在 device 上，也可能跑在 Cloud 上，而 scheduler 里没有 Cloud 侧回收器——所以记忆整理只覆盖跑在自建设备上的话题。这是方案 A 接受的代价，没有粉饰成全覆盖。

**CI 现在 6 绿 2 红，两条都与改动无关**：
- `test`：5450 passed / 1 failed，失败的是 `test_mid_run_message_is_consumed_before_the_run_succeeds` 的 `TimeoutError`，那条用例写死 `asyncio.wait_for(..., 1)` 一秒。**决定性证据**：同一分支上一个提交 `10b0cb462` 的 CI `test` 是 success（03:25Z），红的是 `f649bc71f`（03:43Z），而两者 `git diff` 只有 `.env.example` 和 `config.py`——**房间复核过，去掉注释行只剩一个空行，一行代码没变**。
- `empty-pr-guard`：`actions/checkout` 的 git fetch 报 GnuTLS handshake failed，重试三次全挂，退出码 128。runner 网络问题。

所以 `mergeable_state` 是 `unstable` 而不是 `clean`——**不是冲突，是这两条红把状态拉下来了**。

## 还差什么（谁做）

| # | 事 | 谁 | 状态 |
|---|---|---|---|
| 1 | 弄到一把有 **embedding + chat** 权限的智谱 key | **必须人**（采购） | 未开始，卡着 3 |
| 2 | 在 GitHub 上点一次 re-run，把 #582 那两条环境性 CI 红刷掉 | **必须人**（芝士的 token 只有 `actions: read`） | 待办。**别为刷绿造空提交**——这条分支被「空 tip 覆盖」咬过一次 |
| 3 | 上机改 `backend/.env` 三行、重部当前 sha、跑迁移脚本 | **必须人**（芝士上不了机器） | 依赖 1；步骤见 <&docs/infrastructure.md> |
| 4 | 房间递一张验收卡，带走备份线 + 端点自检（共用分支，只能递一张） | 芝士 | 等端点自检 conclude |
| 5 | #582 评审合并 | 人 | 依赖 2 |
| 6 | 「进度层」那条边 | — | 归 #184，不在本 issue |

**翻牌前必须先确认第 2 步之外的一件事**：`cheese-viking-backup.timer` 已经在那台机器上真的装好并在跑。备份线的代码进了仓库不等于机器上装了 timer，这跟 DB 备份一样属于手工 runbook。

## 一条贯穿始终的平台风险（不属于 #187，但吃掉了这轮的工）

端点自检那条支线的工作区被平台**整个重建两次**，两次都发生在「写完文件」和「push」之间那几分钟：`.git` 连同提交一起被换掉，`git status` 全程显示干净、无任何提示。`commit` 不构成保护，**只有 push 出去的才算存在**。现在所有支线的简报都写死「每写完一个文件就 add + commit + push，中间不插任何别的工具调用」。是否单独开一条支线追这个触发条件，问题已递给 <@caisongyang>，尚未回复。
