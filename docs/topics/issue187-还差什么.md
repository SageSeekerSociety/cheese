# #187 记忆系统：现在是什么情况

## 一句话

**没有全部解决，但 #582 已经落地。** 2026-09-03 commit `acae0ed29` 把 #582（记忆整理/dreaming）合进了 main（<@caisongyang> 确认，房间已核实）。可线上仍是默认配置——`memory_backend` 仍是 `"db"`，而且新发现 dreaming 这条**还有自己独立的开关 `DREAM_ENABLED`，默认 `false`**，合进 main 不等于已经在跑。issue 最初量到的现象（平台几乎不长记忆）今天原样成立。剩下的都是**只有人能做**的事。2026-09-08 <@caisongyang> 给了一把智谱 key，房间实测：**key 本身有效，但账号余额为 0**，除免费的 `glm-4-flash` 外所有模型（含全部 embedding）都被挡回 429，所以它现在还翻不动牌——见下方「这把 key 实测结果」。

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
| dreaming 归 #582，是一个还开着的 PR | **已合并 main（09-03，`acae0ed29`），但功能默认关着** | 见下 |

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

### 3. #582 改挂回收器 — 已合并 main，功能默认关着

不是普通的解冲突：`c45826d5f`（#630「retire the platform's own box」）把 `reap_idle_containers` 连同整个容器载体删了，而 #582 的设计原话就是「挂在 `reap_idle_containers` 上」。方案 A（<@caisongyang> 拍板）：改挂到活着的 `reap_idle_device_screens`。

**2026-09-03 已合并**：main 上的 `acae0ed29`（标题「修复 test 超时、empty-pr-guard 网络抖动」）落地了这条改动——`backend/app/domain/scheduler/service.py` 的 `reap_idle_device_screens` 现在在回收空闲话题的沙箱屏幕**之前**先挂一次 `记忆整理(dreaming)`：给 芝士 最后一轮机会，把这个话题学到的东西整理进项目记忆池（合并重复、把「上周」标成日期、撤掉被推翻的旧事实），然后再回收。原两条环境性 CI 红（超时用例、runner 网络抖动）在同一个 PR 里一起修掉了。房间此前对 `git merge-tree`/`mergeable=true` 的独立验证已经被这次合并印证。

**已知并已写进代码的缺口**（`service.py` 注释原文）：一轮不一定跑在 device 上，也可能跑在 Cloud 上，而 scheduler 里没有 Cloud 侧回收器——所以记忆整理只覆盖跑在自建设备上的话题。这是方案 A 接受的代价，没有粉饰成全覆盖。

**新发现，之前没写进本文档**：dreaming 这个功能自带一个独立开关 `DREAM_ENABLED`（`backend/.env.example`），**默认 `false`**，和 `MEMORY_BACKEND` 完全是两码事——**合进 main 不等于线上已经在跑**。而且这个功能读的是既有的 db 后端记忆行（`MemoryEntry`/`MemoryScope`/`MemoryLayer`），**不需要智谱 key**、也不依赖 `MEMORY_BACKEND=openviking`：只要把 `DREAM_ENABLED=true` 写进部署的 `backend/.env` 并重新部署，就能独立生效，是四步清单里成本最低的一步。配套两个旋钮：`DREAM_MAX_PER_SWEEP`（每次回收扫描最多整理几个话题，默认 1）、`DREAM_MIN_BLOCKS`（少于这么多条消息的话题不整理，默认 20）——.env.example 里说明写死了「默认关闭，因为它会花模型预算，一次整理约等于一轮对话」。

## 这把 key 实测结果（2026-09-08，房间实打实调的）

<@caisongyang> 在话题里给了一把智谱 key。**它不写进仓库、文档或任何提交**，值只在话题聊天记录里。房间拿它直连 `open.bigmodel.cn` 逐个模型试了一遍：

| 模型 | 结果 |
|---|---|
| `glm-4-flash`（免费） | **200 通** |
| `glm-4.5-air`（`OPENVIKING_LLM_MODEL` 配的就是它） | 429 `1113 余额不足或无可用资源包,请充值。` |
| `glm-4-plus` / `glm-4.6` / `glm-4-air` | 429 同上 |
| `embedding-3`（`OPENVIKING_EMBEDDING_MODEL` 配的就是它） | 429 同上 |
| `embedding-2` | 429 同上 |

**读法**：key 的签名是对的（否则会是 401 而不是 429），挡住的是**账号余额**。免费模型能过、付费模型全挡，正好把我们配置里用到的两个模型都覆盖了——所以按当前 `.env.example` 的默认值，这把 key 一个端点也调不通。

**顺带给端点自检拿到了第一次真环境验证**：以前只有假端点测试，这次用真 key 跑 `endpoint_probe.probe()`，输出 `status: "down"`、两个端点各自 down、并原样引用了厂商中文原话「余额不足或无可用资源包,请充值。」。这正是这条支线存在的理由——**如果没有这个探针，翻牌之后现场表现会和「db 后端不长记忆」一模一样，没人能看出是余额问题。**

**安全提醒**：这把 key 是明文发在话题聊天里的，任何能看到本话题的人都能取走。充值之后它就成了一把能花钱的 key，建议 <@caisongyang> **充值前先轮换一次**，新 key 别再走聊天。

## dreaming 这一步的前置核实（2026-09-08，房间在真机上查的）

<@caisongyang> 问「上机怎么操作」，房间借着这轮**正好跑在 dev app host（`cheese-dev-env1-app` / 192.168.16.5）上**，把四个前置逐条查了一遍——全部就位，真的只差一行 `.env`：

| 前置 | 结果 | 怎么查的 |
|---|---|---|
| 线上 sha 含不含 #582 | ✅ 含 | 容器镜像 tag 是 `addd224`，`git merge-base --is-ancestor acae0ed29 addd224` 通过 |
| 数据库迁移跑没跑 | ✅ 跑了 | 连线上库查到 `memory_dreams` 表存在，`memory_entries` 上有 `retired_at`/`layer`/`scope` 三列 |
| 有没有在线 device（没有则永不触发） | ✅ 有，`984da7f7affb` | 后端日志 `launcher shipped to device`，本话题这轮就跑在它上面 |
| `.env` 里现在有没有这行 | ✅ 没有，干净默认值 | `grep DREAM /home/nictheboy/cheese-backend-py/backend/.env` 无输出 |

**开了之后不会立刻有反应，这是设计如此**：dreaming 挂在空闲屏幕回收器上，扫描间隔 1h（`SANDBOX_REAP_INTERVAL_SECONDS`），话题要**静默满 8h**（`IDLE_REAP_HOURS`）才算候选，每次扫描最多整理 **1 个**话题，少于 20 条消息的话题跳过。所以**第一次整理最快也在翻牌 8 小时之后**，之后大约每小时消化一个。一下午没动静是正常的，不是部署失败——查 `docker logs cheese-backend-1 | grep 记忆整理`，别反复翻开关。

**顺带修掉一处会误导操作的过期文档**：<&docs/infrastructure.md> 顶部环境表里 dev/prod 的 Stack 列写着 `bare-metal (systemd + local .venv)`，而实际跑的是 Docker 容器（同一份文档下一节自己就这么说）。照那行上机会去找根本不存在的 systemd 服务。已改。

## 还差什么（谁做）

| # | 事 | 谁 | 状态 |
|---|---|---|---|
| 0 | 上机把 `DREAM_ENABLED=true` 写进 `backend/.env` 并重部当前 sha | **必须人**（芝士上不了机器） | **四个前置房间已在真机核实全部就位**，见下方「dreaming 这一步的前置核实」；操作步骤在 <&docs/infrastructure.md> 的「Turning on 记忆整理 / dreaming」一节 |
| 1 | 让这把智谱 key 真的能用：**给账号充值 / 买资源包**（key 本身有效，缺的是余额），充值前建议先轮换 | **必须人**（采购） | **进了一半**：key 09-08 已拿到，实测 429 余额不足，仍卡着 3 |
| 2 | ~~在 GitHub 上点一次 re-run，把 #582 那两条环境性 CI 红刷掉~~ | — | **已完成**：09-03 `acae0ed29` 把两条 CI 修复和 #582 一起合并了，不用再单独重跑 |
| 3 | 上机改 `backend/.env` 三行（`MEMORY_BACKEND`/两把 key）、重部当前 sha、跑迁移脚本 | **必须人**（芝士上不了机器） | 依赖 1；步骤见 <&docs/infrastructure.md> |
| 4 | 房间递一张验收卡，带走备份线 + 端点自检（共用分支，只能递一张） | 芝士 | 等端点自检 conclude |
| 5 | ~~#582 评审合并~~ | — | **已完成**，见上方「一句话」 |
| 6 | 「进度层」那条边 | — | 归 #184，不在本 issue |

**翻牌前必须先确认第 2 步之外的一件事**：`cheese-viking-backup.timer` 已经在那台机器上真的装好并在跑。备份线的代码进了仓库不等于机器上装了 timer，这跟 DB 备份一样属于手工 runbook。

## 一条贯穿始终的平台风险（不属于 #187，但吃掉了这轮的工）

端点自检那条支线的工作区被平台**整个重建两次**，两次都发生在「写完文件」和「push」之间那几分钟：`.git` 连同提交一起被换掉，`git status` 全程显示干净、无任何提示。`commit` 不构成保护，**只有 push 出去的才算存在**。现在所有支线的简报都写死「每写完一个文件就 add + commit + push，中间不插任何别的工具调用」。是否单独开一条支线追这个触发条件，问题已递给 <@caisongyang>，尚未回复。



