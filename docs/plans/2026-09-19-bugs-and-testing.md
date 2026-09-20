> 状态：**研究报告，第 5 节五条已拍板**（2026-09-19 至 09-20）。材料是 09-05 以来合并的 200 个 PR、当周事故记录与 CI 运行史；文中引用的 `families-*.md` / `audit-*.md` 是研究过程文件，未入库。讨论去 issue。

# 这两周的 bug 长在哪，以及测试要怎么重建

仓库 `/Users/andyl/Projects/cheese-backend-py`，分支 `sync/main`，基线 `49a7c8177`，2026-09-19。

材料：`families-0.md` / `families-1.md`（2026-09-05 以来 200 个合并 PR 的根因族，各一半）、
`audit-tests.md`（现有测试体系）、`audit-gates.md`（合并门与部署门）、`audit-toolcall.md`（工具调用全路径）。
五条抱怨的原话在 `COMPLAINTS.md`；架构结论在 `../arch/DECISIONS-2026-09-19.md`（今天 62 条）。
下面第 1、2 节的族对照是对着**前 44 条**算的；51–62 是后来补定的。其中 **59 条直接改本报告的测试规则**
（§2.1、§2.3、§3.0、§3.1、§3.4、§4.2、§4.4），**62 条加出了 §3.6**；改到的地方都标了出处。

每条结论后面跟的是 PR 编号+标题、`path:line`、或 `bugs/` 里那份事故记忆的文件名。
没有证据的判断我标成「推论」。

---

## 1. 这两周修了什么

两份族表按机制合并之后是 **16 个族、178 次族归属、164 个不同的 PR**
（428 个合并 PR 里的 fix/perf/ci 以及正文在修 bug 的 feat；两份材料各取 100 个 PR）。

**下表每个族的「数」是族归属数，不是不同 PR 数**，两者差 14：
四个 PR 在两份材料里都作为族成员出现（`#1134`/`#1135`/`#1136`/`#1137`），其中只有 `#1137` 因为两边都并进族 1 而自然去重；
另外九个是同一个 PR 修了两件事、两半各归一族（`#1138`、`#1153`、`#1155`、`#1159`、`#1214`、`#1240`、`#1241`、`#1245`、`#1253`），
再加 `#1237` 在族 5 内部被两条源族各收了一次。
逐个列出来，跨两族的共 13 个：`#1134`（族 2 CI／族 9 能力）、`#1135`（族 2／族 7 回收）、`#1136`（族 1／族 12）、
`#1138`（族 7／族 13）、`#1153`（族 1／族 8）、`#1155`（族 3／族 4）、`#1159`（族 3／族 4）、`#1214`（族 5／族 14）、
`#1237`（族 5／族 11）、`#1240`（族 2／族 10）、`#1241`（族 2／族 10）、`#1245`（族 13／族 14）、`#1253`（族 6／族 10）。
178 − 13 − 1 = **164**。

| # | 族 | 数 | 机制 | 层 | 代表 PR |
|---|---|---|---|---|---|
| 1 | 跨进程边界上没有契约：错误的身份被抹平，发布顺序没人管 | 20 | backend / device-connection owner / cloud-control 是三个**按不同节奏发布**的进程，共享 Python 模块、共享 schema、共享一条 nginx；跨线只传状态码和 header，机器侧的故障类型（离线/超时/socket 未绑/缺凭据）序列化时被压成一个码，另一头还原不出来；新方法在老 owner 上是 403 | 进程边界与发布拓扑（`app/core/errors.py`、`device_hub_rpc.py`、`device_connection_app.py`、`deploy/release-*.sh`） | #1114 an error keeps the headers it was raised with · #1153 fix(agent): a room's executor is reached where it was installed · #1259 fix: keep executor admission compatible with unrelated room migrations · #1126 an owner release can be told to interrupt active calls |
| 2 | CI 与共享 runner 的红不是代码的红 | 17 | 三台自托管 runner 共一个 `cheese-ci` 池（`deploy/ci-runner/provision.sh:35` 的命名带 slot 字母，#1070 之后一台可同时跑两个 job），共享一个 3GB tmpfs 上的常驻 Postgres、apt 锁、出口网络、一个 500MB 的组织产物配额、8G 无 swap 的内存。任一耗尽，当时跑的每个 job 都红，**而失败读起来完全像是那个 PR 自己的错** | `.github/workflows/**`、`deploy/ci-runner/**`、`backend/tests/isolation.py` | #1130 a timeout catches a hang, not a slow run · #1122 a box that runs out of memory slows down instead of killing something · #1070 a pool machine can run two jobs at once · #1225 ci(runner): keep the resident Postgres's WAL small on its tmpfs · #1082 one runaway log cannot take the repository's CI with it |
| 3 | 一个取值同时表示两件事：不可达 = 空，未知 = 已定，对端的失败 = 我的 500 | 17 | 外部依赖不可达时适配器返回的值与「合法的空」一模一样（`[]`/`False`/`None`/缺一个 key）；远端断开落到兜底 handler 变成本服务的 `unhandled_error`；一个「已收到 / 正在准备 / 未登录」的状态由平台这一侧判定而事实在另一侧 | 外部依赖适配器 + `app/core/errors.py` + 前端渲染（`core/storage.py`、`domain/memory/openviking_store.py`、`api/routes/{connector,chat}.py`） | #1119 a bucket that did not answer is not an empty bucket · #1244 fix(errors): a handled failure on a WebSocket route closes it, answering nothing · #1231 fix(device): a machine's failure travels as its own answer, not as a 500 · #1173 fix(chat): the room stops saying 芝士 has your message before it does |
| 4 | 失败没有读者：该说的不说，不该说的对所有人说，后台异常无人读 | 14 | 日志层级与收件人从来没有按「谁能行动」设计过。后台工作 `add_done_callback(registry.discard)` 退休，异常从没被读过；告警内容由一个事先挑好的五字段白名单决定；一台关了机的笔记本每分钟五条 ERROR 进飞书群 | `app/core/{obs,alerting,background}.py`、各 poller 的 except 分支、`frontend/src/services/ErrorHandler.ts` | #1116 background work the caller holds reports its own crash · #1105 a backend error reaches a person, wherever it was raised · #1159 fix(obs): 告警说清什么时候、出了什么事 · #1128 a device that is offline is not a read failure |
| 5 | 一把锁或一条连接的作用域大过它保护的东西 | 13 | 两个方向：拿着数据库行锁或一条池连接 `await` 一次外部调用（R2、GitHub、MicroCloud、设备 RPC），外部调用挂住的那段时间连接不还；或者一个为 A 设的互斥（`cwd_lock`、advisory lock、单个事件循环）被 B 顺手用了，B 的延迟变成 A 的时长 | 后端事务边界 + 执行器 runtime（`domain/topic/transcript_stream.py`、`remote_execution/runtime.py`、`domain/agent/tool_preview.py`） | #1210 fix(transcripts): upload the chunk before taking the row lock · #1186 fix(execution): a platform tool no longer waits out a running shell command · #1215 fix(rooms): talk to the machine without holding the room row lock · #1188 fix(preview): an assignment prefix that never matches no longer hangs the loop |
| 6 | 前端：一条链断一跳，界面什么都不说 | 13 | 事件/资源链要穿过四层组件或穿过一次鉴权，中间任何一层没透出去**结果不是报错，是什么都不发生**；flex 里没人肯收缩，内容被裁掉而且没有滚动条；后端交出字节和一个 mime，前端按扩展名猜怎么画 | `frontend/src/{components,views}/**`、`plugins/vuetify.ts`、`services/account.ts` | #1206 fix(panel): wire the card's review button and cap the @ menu · #1205 fix(layout): a panel that outgrows its box scrolls instead of clipping · #1123 draw markdown files, images, and chat pictures in place · #1108 drop the previous account's caches when someone else logs in |
| 7 | 回收：没人回收，或者回收时不知道谁在用 | 13 | 房间的 `$HOME` 必须各自独立，而工具只能装进 `$HOME`，于是每房间重下一遍；回收只在「房间启动」或「房间归档」时发生，持有最多磁盘的恰恰是没人开也没人归档的那些；反方向是回收器删掉了另一个 slot 正在用的东西 | `deploy/*.sh`、`deploy/ci-runner/*`、`domain/agent/resource_cleanup.py`、`domain/topic/retire.py` | #1144 feat(machine): a project's tools are installed once per machine, not once per room · #1145 feat(deploy): a deploy reclaims the checkouts the old layout left behind · #1077 the disk guard spares what a job just built · #1232 fix(cleanup): an archived room's processes are ended once the grace runs out |
| 8 | 同一个事实在两处声明或两处推导，写端动了读端没动 | 12 | 「东西装在哪 / 这个进程是不是会话 / 这个 socket 叫什么 / 池上限是多少 / 这个域名归谁」不是被记下来传过去，是两侧各自按规则算一遍。**两份推导在各自的测试里都是对的** | 跨 Python / stdlib-only 脚本 / TypeScript / Caddyfile 四种运行时 | #1092 find the environment runner wherever the launcher put it · #1156 fix(pi): a background job is reachable from the path a room actually has · #1103 size the pool so a release cannot exhaust the database server · #1129 a plain http visit reaches the same site as https |
| 9 | 能力与环境靠推断或靠声称，从未被行为验证 | 12 | 要分 harness 的地方都在读一个**恰好相关**的事实，而不是读 harness 自己声明的能力（对 Claude Code 碰巧等价，对 pi/Codex 不等价）；有一句话声称某个东西在某个地方（工具装好了、镜像会被构建、缓存在这个路径），守着它的测试检查的是结构而不是行为 | `harness/__init__.py`（`HARNESSES` 注册表）、`api/routes/terminal.py`、`deploy/*/Dockerfile`、`.github/workflows/build.yml` | #1062 a room whose harness draws no pane keeps its timeline · #1052 let a teammate's harness be chosen · #1093 a machine carries the tools a document needs · #1064 build the browser image, so the fetch ladder has its top rung |
| 10 | 房间指向一个 agent：一个单值槽装不下第二个参与者 | 11 | 身份从**房间**推导（`cheese-<topic 的 12 位 hex>`），并且「这个房间的 agent」在好几个地方是一个单值槽：`topics.agent_instance_id`、每房间一个 remote-control session 指针、roster 上一个显示名、一个「第一个 agent」的解析函数 | 领域模型（`domain/{agent,agent_instance,topic,topic_membership,identity}/`、`api/auth.py:220`）+ 前端 roster/chat | #1196 fix(chat): the agent that was addressed is the agent that answers · #1241 feat(agents): a seat is the agent's own, and a room holds one control session per agent · #1219 feat(rooms): a room does not point at an agent · #1253 fix(chat): an AI line never shows a handle |
| 11 | 代价按房间/设备/轮次数线性放大，没有预算 | 10 | 一段「每个房间一次 / 每台设备一次 / 每轮一次」的固定代价，写下来时房间是个位数，现在 dev 上是 71 台设备、102–242 个房间。单次小到没人看，累计是主要成本，**没有任何预算会因为它增长而变红** | 重连/恢复路径、轮询循环、一轮的组装（`api/routes/connector.py`、`harness/pi/runtime.py`、`domain/block/models.py`） | #1250 fix(recovery): the fleet reconnecting recovers a few machines at a time · #1148 perf(deploy): one lsof for the whole sweep, not one per room · #1043 a reused screen no longer receives the launcher every turn · #1048 index the cloud-provisioning watermark a turn looks up every time |
| 12 | 一次发布掐断长连接与已加载的客户端资产 | 7 | 设备控制通道、在途 executor 调用、浏览器 WS、按内容哈希命名的前端 chunk，生命周期被绑在一个**发布会替换的东西**上（一个 nginx worker、一个容器、一个转发端口、一个文件）。「deploy 完网站就容易挂」不是偶发，是结构性的 | `deploy/`（`cloud-control.py`、`llm-tunnel/nginx.conf`、`deploy-docker.sh`）、`executor_transport.py`、`frontend/src/services/staleBuild.ts` | #1136 the control channel stops borrowing the proxy a release reloads · #1097 give api-front's workers a deadline and a descriptor ceiling · #1050 a tool call waits out an app deploy instead of failing the turn · #1099 recover a tab that was open across a deploy |
| 13 | 闸门没在测它声称测的东西 | 6 | 跳过七条测试仍然报绿；断言「结果是数字」而失败路径返回的 0 也是数字；YAML 折行让五个产物路径被吞进一个 scalar；断言读「下一帧」而通道上多了一种帧 | `.github/workflows/**`、`deploy/*.sh --self-test`、测试断言本身 | #1236 ci(canary): a canary that cannot reach docker fails instead of skipping · #1256 ci(acceptance): the receipts an executor failure is read from are uploaded again · #1138 fix(deploy): the cleanup could not see the two biggest things it deletes · #1197 test(chat): a publication assertion waits for its publication |
| 14 | 执行器输出：同一件事两处真相 + 读写竞态 | 5 | 一个任务的输出有两个文件可以装——我们的 `_deliver` 写的那份，和 serve 进程自己创建并边跑边填的 `<task_id>.output`。「文件在」被当成「输出在」，任务报 completed、exitCode 0、output `''` | `remote_execution/runtime.py:825-862` | #1252 fix(execution): a finished task's output is read once it is there, not once the file is · #1260 fix: recover sessions and settle executor output without races · #1258 · #1245 |
| 15 | 文档与死代码把下一个人送到错的结论 | 5 | 一句写对过的话，在它描述的东西改掉之后原地留着，**读起来和当前的句子一模一样**。死脚本、旧容器名、过期的步骤名同理 | `docs/`、`deploy/` 的脚本与注释、workflow 的 step name | #1098 retire the bare-metal release and the sentences that outlived it · #1076 the volume sweep outlived the containers it was named after · #1131 · #1112 |
| 16 | WebSocket 生命周期没人管到底 | 3 | 一个 socket 可以在对端已经走了之后长时间停留在 OPEN，没有任何一侧主动探活。死链路被当成活的一直写，期间积累的 pending future 造成二次伤害（被发现时 299 条） | `domain/agent/device_hub.py`、`api/routes/chat.py`、`frontend/src/components/ChatPanel.vue` | #1041 a dead device link no longer stalls every executor call · #1042 the browser pings its socket so a dead link is noticed while idle · #1040 |

### 一句话总结病根分布

**这是本报告的估计，不是实测**：178 次族归属里，前 44 条架构结论能根除的**估计**是 28 个（16%），
能把另外 29 个（16%）从「线上发现」降级成「矩阵少一格」，剩下 121 个（68%）一条结论都没碰到。
估计的方法是逐族读结论正文判断「这个机制还在不在」，没有一条是把结论真的落地之后重跑这两周的 bug 验出来的；
分母 178 是族归属数，见上表。结论 51–62 之后没有重算这三个数。
（这三个数按族归属算，因为同一个 PR 的两半可能一半消失一半不消失；按不同 PR 算的分母是 164。）

不是结论写错了。结论讲的是领域模型（参与者、记忆、事件、租约、骨架契约），
而这两周实际在修的 bug 主要长在**领域模型之下的那一层**：
谁在什么事务里 `await` 了谁、哪两个进程按不同节奏发布却共享代码、哪条闸门绿着但没跑。
数量最大的族（1、2、3）里，只有族 3 的一半会因为某条结论消失。

---

## 2. 为什么会有这么多

### 2.1 族对到架构病根

`../arch/ARCHITECTURE-v2-2026-09-19.md` 第 0.3 节那张不变量表，是这些族的另一种写法（本报告全文的 I 编号都按 v2；v1 的 I23「fallback 掩盖的是物理事实还是配置错误」在 v2 里没有对应条，见下表族 3）。对照：

| 族 | 架构病根 | 不变量 | 落地后怎样 |
|---|---|---|---|
| 10 房间指向一个 agent | 身份从容器推导；一个单值槽当作一对多的关系 | I9（收件人是 handle，人和 agent 同一张表） | **根除**。DECISIONS 1/2/4/5/31/32：参与者 = 唯一身份 + 席位，`cheese split` 的第二身份整个干掉，房间推导身份这件事按结论不存在 |
| 8 同一事实两处声明 | 一个事实没有唯一的所有者 | I4a（同一个事实只有一个声明处） | **一半**。DECISIONS 29 把「装在哪、叫什么」纳入骨架契约，消掉跨运行时那一半；池上限、Caddyfile、授权入口那一半（#1103/#1129/#1028）没有对应结论 |
| 9 能力靠推断 | 能力没有被声明，读的是「恰好相关」的事实 | I6（矩阵每一格要么「有」要么一条差异码）、I7（每个能力位所有读者读同一个函数） | **降级成编译期**。DECISIONS 29/30/43 定死骨架契约与差异码矩阵。注册表已经建起来（`harness/__init__.py:456` `HARNESSES`，四个能力位），缺的是强制所有分支走它的那条测试——今天 `terminal.py` 仍然用「有没有开着的 screen」冒充 `draws_on_its_screen` |
| 3 一个取值两件事 | fallback 掩盖失败；`None` 当默认分支入口 | I26（fallback 的可见度和它掩盖的失败一样高）、I28（`None` 不许当默认分支）（v1 那条「掩盖物理事实的留、掩盖配置错误的删」在 v2 里已删，它的那一半落进了 I26 的四种出口） | **一小半**。DECISIONS 23 把「机器够不着」从返回值搬进事件；其余（S3 桶不可达、死 FUSE 挂载、上游同步失败）是实现纪律，44 条里没有 |
| 4 失败没有读者 | 收件人从来没有按「谁能行动」设计过 | I11（通知只有一条规则）、I25（机器状态以事件抵达，不以工具报错抵达） | **根除一半**。DECISIONS 14/15 定了事件落点与唯一收件人规则，#1128（平台自己在等一台机器开机，却每两秒告诉所有人）和 #1105（后台任务崩了，下一步在人手上，通知数是 0）两个方向同时消失。后台异常无人读那一半（#1116）不在结论里 |
| 5 锁的作用域 | 持锁跨网络 | 无 | **不消失**。结论 55 把结论 40 界定成「平台不因为到期自动换资源」，期限、心跳、取消都保留——三样都与「持不持锁」无关；结论 59 只改这一族怎么测（§3.1） |
| 1 跨进程无契约 | 一房一机的残留 + 三个发布单元共享 schema | I3（内层的死亡不得改变外层的任何一行） | **一小半**。DECISIONS 23 消掉离线那一半的编解码链；发布拓扑、发布顺序、方法白名单，44 条完全不谈 |
| 12 发布掐断长连接 | 地点不是租约，连接断了是事故不是状态 | I19（销毁前先取回） | **一半**。DECISIONS 24/39 让「连接断了」变成租约语义里的正常状态；前端 chunk 那一半（#1099）不在 |
| 7 回收 | 地点没有期限 | I19 | **房间那一半**。DECISIONS 24 的三张收据 + 39 的休眠态；CI runner 那一半不在 |
| 11 代价线性放大 | 平台自召唤与定时全量扫描 | 无 | **一半**。DECISIONS 13/16 解散 `domain/scheduler/`、把 5 处平台自召唤改写成事件，去掉一批轮询；**设备重连时的恢复扇出不在任何一条里** |
| 2 CI / 6 前端 / 13 闸门 / 14 输出两处真相 / 15 文档 / 16 WS | — | — | **无**。前 44 条是产品架构，不谈 CI、不谈前端渲染契约、不谈交付流程。结论 59 碰到的是**怎么测、怎么放行**（CI 环境故障报 failure、部署等同一提交的测试通过），这些族的代码机制一个都不改 |

**卡与房间混层**这条病根，今天的表现是族 10 和族 1 各一半：`topics.agent_instance_id`（L0 的表上挂着 L1 的指针，#1219 拆掉）、
`session_placement`（L0 的表上挂着 L2 的租约，`routes/execution.py:54` 读它做准入判断，#1259 因此被一个无关迁移打挂）。
I3 的后半（「`topics` 表不许出现执行层的列」）在上一版里明确写着**未定**，因为它需要一份逐列的层归属登记表而那份表不存在。
这是族 1 里 `#1259` 那类 bug 唯一的结构性止血点，而它现在既不在结论里，也不在守卫里。

### 2.2 测试缺口：哪条缝没有替身

`audit-tests.md` 的结论一句话：**这套测试是按事故长出来的，不是按系统的缝设计出来的。**
两周新增 155 个测试文件（占仓库 542 个的 29%），6838 条用例跑 13 分 25 秒，
而新增的每一个文件都落在**已经有替身的那些格子里**。

七条没有替身的缝，和它们各自漏掉的族：

1. **中心会话 → transport → hub → 连接器 → 执行器**（族 1、5、14、16；`audit-toolcall.md` 第四节）。
   每一段各自都有测试，没有任何一处让相邻两段真接在一起。
   唯一的端到端 `scripts/remote_execution/room_fixture.py:174-177` 是一行：
   ```python
   async def call_executor(self, device, state, method, params, *, trace_id=None):
       assert device == "executor"
       assert Path(state) == self.state
       return await asyncio.to_thread(runtime.request, self.state, method, params)
   ```
   nginx、路由与 advisory 锁、owner 与 DeviceHub、WebSocket、Go 连接器——**五跳被这一行替掉**，
   而那一步在 workflow 里的名字叫 `Verify ordinary room turns through the connector`。
   同一条验收链上还有第二个同形的替身：`scripts/remote_execution/acceptance.py:363` 的 `LocalDeviceHub`，
   它的 `exec` 就是 `subprocess.run` 在本机跑一条命令——**DeviceHub 整段同样被摘掉**（`audit-tests.md` 第 1 节的层表记了这一条）。
   也就是说「真执行器验收」这个名字下面，链路上最容易出事的那两段都不在场。
2. **backend ↔ owner 的跨进程 HTTP**（族 1、3）。测试进程里只有 `app.main:app`；
   owner 是独立 ASGI app（`app/device_connection_app.py`），两个 app 从不在同一个测试进程里。
   `project-cheese-device-offline-header-dropped.md` 是这条缝最完整的证据：#1111 的测试用
   `httpx.MockTransport` **自己造了一个带 `X-Device-Id` 的 409**，而真实 owner 发不出这个 header
   （`core/errors.py` 的 `http_exception_handler` 丢掉 `exc.headers`），于是测试全绿合入、线上一点没变。
   这三个 MockTransport 用例今天还在 `backend/tests/unit/test_owner_rpc_unexpected_409.py:18-25`。
3. **Go ↔ Python 的线格式**（族 1）。`execution.call` / `execution.data` / `execution.result` 三帧
   不在 `device_link.py` 里，是 `device_hub.py:616` 直接拼的 dict，
   恰好落在那个文件 docstring 承诺的「field-for-field 对 Go struct，drift 由测试发现」之外。
   `cli/internal/link` 连一个 `_test.go` 都没有。两侧的假连接器可以随便撒谎：
   `test_device_connection_lifecycle.py:47` 发的是 `{"t":"hello","v":3}`，而两边的 `PROTOCOL_VERSION` 都是 1。
4. **生产的连接池**（族 5、11）。`backend/tests/conftest.py:83` 设 `CHEESEX_TEST_NULLPOOL=1`，
   `backend/app/core/db.py:46` 据此换成 `NullPool`。**压垮生产的那个对象在测试里根本不存在**。
5. **部署后的业务健康**（族 12）。`deploy/deploy-docker.sh:593` 打 `/healthz`，
   而 `backend/app/api/routes/health.py:26` 的 `/healthz` 只检查路由模块有没有 import 失败，不碰 DB、不碰 redis。
   查 DB+Redis 的 `/readyz` 就在同一个文件 `health.py:111`，部署不看它。
   四层健康检查（部署脚本、容器 healthcheck、`check-app-tier.sh`、外部探针）**没有一层碰数据库**。
6. **真浏览器里的布局**（族 6）。jsdom/happy-dom 没有 layout，
   所以今天那三个布局 PR 的新用例大多是「源码断言」（断言 CSS 里写了 `overflow-y: auto`）——按实现写的测试，改个写法就失效。
   这条缝**不是完全空的**：`e2e/tests/layout-invariants.spec.ts`（106 行）已经在真浏览器里量 `.v-field` 的包围盒，
   断的正是「相邻字段的浮动 label 不得压在上方字段的边框盒上 / 不得被滚动容器裁掉」，
   还带了「范围里一个字段都没有就抛错」的空断言防护。
   缺的是覆盖面：它**手工枚举了两个屏幕**（`/account/signin`、「修改 AI 队友」对话框），
   没有对路由参数化，没有第二个视口，也没有「可滚容器必须能滚」那条——它自己就是「修修补补」的样板。
7. **规模**（族 11）。没有任何一层能同时放多台设备。71 台设备同时重连这件事只能在 dev 上发现。

### 2.3 哪些族会随结论消失，哪些不会

**会消失的（28 个 PR，一条测试都不用写）**：族 10 全部（11）、族 3 里对端状态那一半（#1244/#1230/#1231/#1155/#1175/#1159，6）、
族 3 里「平台声称一件它还不知道的事」（#1173/#1143/#1211/#1177，4）、族 4 里收件人那一半（#1105/#1081/#1058/#1063/#1132/#1128/#1055，7）。

**会降级的（29 个，从线上发现降级成 CI 里矩阵少一格）**：族 9 全部（12，靠 DECISIONS 30 的差异码矩阵）、
族 8 的骨架那一半（6，靠 DECISIONS 29 把「装在哪、叫什么」纳入契约）、
族 1 的离线编解码链（6，靠 DECISIONS 23）、族 7 的房间磁盘那一半（5，靠 DECISIONS 24 的三张收据）。

**不会消失、必须由测试兜住的（121 个，这张表逐族认领到 121，和上面两张单子加起来正好 178）**：

| 族 | 数 | 为什么结论管不到 | 必须由哪条缝兜住 |
|---|---|---|---|
| 2 CI 的红 | 17 | 结论是产品架构 | 不是测试能抓的，是第 4.3 节的门 |
| 1 发布拓扑那一半 | 14 | 结论不谈进程拓扑与发布顺序 | 缝 C-2 的版本错配矩阵（acceptance 层，拉上一个已发布 owner 镜像）+ 一条静态守卫（owner 的 import 闭包清单） |
| 5 锁的作用域 | 13 | 结论不谈事务边界 | 一条全局不变量（下文「连接不跨 I/O 边界」）+ 每把锁一条「**外部 I/O 进行时**对该行 `SELECT … FOR UPDATE NOWAIT` 必须成功」（结论 59） |
| 6 前端 | 13 | 结论不谈前端渲染契约 | 真浏览器的布局不变量（`layout-invariants.spec.ts` 参数化到全部路由）+ **mime → 渲染分支的表驱动断言**：后端的 `DOCUMENT_TYPES`/mime 表与前端渲染分支由同一份 JSON 驱动，后端每一种会产出的 mime 一行，断言「渲染结果不是错误文案」；后端加一种而前端没分支就红（#1123 的真实机制是「后端交字节+mime、前端按扩展名猜」）。外加两条同源：走 raw 端点的资源断言取数路径带 `Authorization`（#1123 的 `apiAttachmentImage.spec.ts` 已是这个形状）、缓存键用两个身份跑同一 URL 断言读不到对方的条目（#1108） |
| 11 代价线性放大 | 10 | 结论 13/16 去掉的是轮询与平台自召唤，**设备重连扇出与每轮固定代价不在任何一条里** | **规模替身 = 进程内的计数型 fake device channel**（不起 71 条真连接，不量耗时）：71 台设备同时 attach，断言任一时刻在飞的恢复数 ≤ `_RECOVERY_AT_ONCE`（`routes/connector.py:98`）且 71 台全部完成（#1250）；空闲房间 2.5 秒内对设备的调用次数 ≤ 10（#1217）；N 个房间的清扫里 `lsof` 调用次数是 O(1)（#1148）。读数从 fake channel 的调用计数器和 SQLAlchemy `before_cursor_execute` 钩子取，不从墙钟取 |
| 7 CI runner 那一半 | 8 | 同上 | 回收器每个两条：**(a)「谁在用」**——构造一个「正在被另一个 slot 使用」的对象（刚 `--load` 进来还没 run 的镜像、mtime 在 grace 内的层），断言回收器不碰它，且 grace 长过任何 job 的 `timeout-minutes`（#1077 的形状）；**(b)「真的回收了它声称的那一层」**——fake `df` 必须**随回收动作改变读数**，不能是「第二次调用返回第二个读数」那种用调用次数冒充回收效果的假货（#1135 原话：没有这一条，每一层在测试里都过自己的前置条件，在生产里一层都不过）。再加 (c) 调度存在性：每个 reclaim 脚本必须有一个 timer/hook 指向它（#1133） |
| 3 适配器取值那一半 | 7 | 是实现纪律 | 一张共用的故障注入表（连接拒绝/403/500/超时/合法的空/合法的有 → 至少两种可区分输出） |
| 4 后台异常无人读那一半 | 7 | DECISIONS 14/15 只管收件人，不管「异常有没有人读」 | 后台循环基元的一条共用契约测试：第 N 次抛非预期异常 → 第 N+1 次仍跑 + 恰好一条带调用方名字的 ERROR + 被拒条目 10 秒内不超过 6 次请求（#1116、#1101 的合并形状） |
| 12 发布掐断长连接 | 7 | 结论 24/39 给了租约语义，但前端 chunk、在途调用、nginx worker 这三种不在里面 | 缝 C-2 的 acceptance 里加一格「发布期间的在途调用」；前端那半是一条 e2e：跨一次构建产物替换后，已加载的页面必须恢复而不是白屏（#1099） |
| 8 池/Caddyfile/授权入口那一半 | 6 | DECISIONS 29 只纳入「装在哪、叫什么」 | 「同一个算术只有一处」的静态不变量：三个池的总和 ≤ Postgres `max_connections`（#1103 已是这个形状，留着推广）；http 与 https 的站点归属由同一份表生成（#1129）；授权入口一处声明（#1028） |
| 13 闸门 | 6 | 同上 | 「测测试」：跳过即失败、执行用例数下限、自检喂固定 fixture |
| 14 输出两处真相 | 5 | 是实现细节 | 一条 property 测试穷举四格（我们写/serve 写/两边都写/两边都空） |
| 15 文档死代码 | 5 | 36/37 减少新增不消存量 | 一条静态检查，**先定义调用图的边**：`.github/workflows/**` 的 `run:` 正文、`docker-compose*.yml` 的 `command`/`entrypoint`/`healthcheck`、systemd unit 的 `ExecStart`/`ExecStop`、以及任何 `deploy/**/*.sh` 里对另一个脚本的调用，这四类构成边集；`docs/` 与 `deploy/` 文本里出现的脚本名、容器名、端口、unit 名、workflow 名必须在仓库里存在**且至少出现在一条边上**。删掉调用方而不删文档就红（`.claude/scripts/check-repo-rules.sh` 的形状扩到 docs/deploy） |
| 16 WS 生命周期 | 3 | 结论不谈连接活性——结论 55 保留了心跳与取消，但它定的是「到期由谁决定」，不是死链路怎么被发现 | 缝 1 的 acceptance（真连接器进程）+ 一条 ASGI 消息序列断言：`accept` 之后抛 `DeviceOffline`，发出的消息序列必须是 `[websocket.accept, websocket.close]`（见 §3.3 的第五种形状） |

三条口径说明：族 9 和族 10 不在这张表里，因为它们整族落在上面「会消失」和「会降级」两张单子上；
族 11 的 10 个这里整族算，是因为结论 13/16 覆盖的那一部分在那两张单子里没有 PR 级枚举；
族 12 同理（结论 24/39 覆盖其中一部分，但没有 PR 级枚举），整族算。

---

## 3. 从第一性原理建测试

他的四条要求：**一定要测试；从第一性原理；要快；不要有修修补补的测试。**
下面这一节是对这四条的直接回答，第一句就是那个「第一性原理」。

### 3.0 原理：测试的单位是缝，不是文件，也不是事故

一个系统的 bug 长在**接缝**上——两个东西之间关于「你给我什么、我答你什么」的那个约定。
这两周 178 个 bug 里，长在一个函数内部的极少；绝大多数是两侧各自都对、接起来不对。
所以：

1. **测试的单位是一条缝（一份契约），不是一个文件、不是一次事故。**
   一条缝一套测试，新缺陷落在这条缝上时它红，而不是为这个缺陷新建一个文件。
2. **每条缝恰好一个替身，而这个替身是契约的执行者，不是 mock。**
   替身的职责是**强制**契约：它自己就是那份契约的可执行定义，两侧的真实现都要过它。
   **判据是「替身遵守真实协议」** [已定] 结论 59：一个替身只能产出真实现也产得出来的东西；
   它答得出而真实现答不出的，就是撒谎。用什么库无所谓——判据在替身的行为上，不在工具名上。
   今天不是这样——`audit-toolcall.md` 数出来同一条缝上有三个互不相干的手搓假货
   （`SocketDevice`、`_OwnerExecutorTransport`、`ExecutorTransport`）；
   而 `test_owner_rpc_unexpected_409.py` 那三条造了一个**带 `X-Device-Id` 的 409**，
   真 owner 当时根本发不出这个 header（`core/errors.py` 丢掉 `exc.headers`）——
   替身撒了谎，没有东西拦它，于是测试全绿合入、线上一点没变。
3. **缝的清单分两种来源，不要混。**
   缝 A（结论 29/30/43）、B（23/24/39/40/55/57）、E-1（13/14/15）、E-2（58）、F（21）、G（42/8/54）是 DECISIONS **已经定死的接口**，
   一个接口就是一条缝；接口之外的东西不许有跨模块的约定。
   缝 C 和缝 D 不是：结论 21/22 说的是**平台 MCP 在哪一侧、项目工具在哪一侧**，
   它没有定义 `execution.call/data/result` 这条线的帧格式；
   「托管方契约」在结论里只有**结论 51**（评审发生在用户所在的地方）这一条产品规则，
   **没有条目定义这条缝的动作集与帧格式**
   （下表那一格的判据引的 I23「托管方身份在卡生成的那一刻就在卡上」是不变量，不是 DECISIONS 条目）。
   这两条的口径是**今天的实现契约**——今天代码里确实存在、必须被冻结下来的那份约定，
   要么后面在 DECISIONS 里补条目，要么就一直按「实现契约」对待。
4. **一条缝的测试合入时必须附一次负向对照的证据。**
   把被测的守卫拆掉（改一行、删一个判断），这条测试必须红；把证据贴进 PR 正文。
   这是「不要修修补补的测试」和族 13「闸门没在测它声称测的东西」唯一**可机械执行**的判据，
   也是本批 200 个 PR 里已经在用的做法：#1141/#1144/#1145/#1202/#1203 都做了负向对照，
   **#1144 的作者因此发现自己第一版测试根本没走到那条路径**；
   #1202 的作者用同样的手段证明了 i18n 闸门管不了选词。
   没有这一条，一个测试「有没有在测它声称测的东西」永远只能靠读代码判断。

**本批 200 个 PR 里真正抓住过东西的五种形状**（`families-0.md` 的附录；新写的测试从这五种里选，不要发明第六种）：

1. **「X 进行的时候 Y 仍可用」**——外部 I/O 进行时对那一行 `FOR UPDATE NOWAIT` 仍然成功（结论 59）、
   占住 shell 的时候仍问得到工具列表。判据机械，可对每把锁机械生成（族 5、族 1）。
2. **「两处真实来源比对」**——从 argparse 命令树和 TS 字面量两边读，比对第三份（#1158）；从启动器和探针两边读。抓的是漂移，不是当前值（族 8）。
3. **「拆掉守卫、看测试挂」作为交付的一部分**——就是上面第 4 条（#1141/#1144/#1145/#1202/#1203）。
4. **「规模」测试用生产的数量级当输入**——71 台设备、182 字节路径、4000 字符输入，而不是 `tmp_path` 加两个对象（族 11、族 7）。
5. **「消息序列在 ASGI 层断言」**——不要用 `TestClient` 的高层断言，它看不见消息序列：
   `accept` 之后抛 `DeviceOffline`，发出的序列必须是 `[websocket.accept, websocket.close]`；
   `accept` 之前抛 `ForbiddenError`，必须是 `[websocket.close]`（#1244 的 `test_websocket_error_handlers.py` 正是这个，改前发的是 `http.response.start`）。
   HTTP 侧同形：同一个异常必须答 409/502、带 `X-Device-Id` 或机器原话、**且 caplog 里 ERROR 计数为 0**（#1231）。
   这两条要做成**对 `register_exception_handlers` 注册的每一个异常类型参数化**，而不是只测当天出事的那一个——
   #1244 正文点明「同样的形状等在这个模块注册的每一个 handler 上」。这是缝 C-1 与族 16 的形状。

反过来，一条新测试要过的是**两条判据**，不是一份禁用工具清单 [已定] 结论 59：

1. **替身遵守真实协议**：它产出的东西，真实现也产得出来；它拒绝的东西，真实现也会拒绝。
   造一个真实现发不出的响应，测的就是这个替身自己。
2. **断言验的是行为，不是结构**：断产品可观察的结果（渲染出来的是什么、答的是哪个码、下一次调用成不成功），
   不断源码文本、不断类型、不断「文件在不在」。

这两条判据不点名任何工具——一个 mock 库、一个 `tmp_path`、一份金样本本身都不是错，
错的是拿它们搭出一个**改名就红、改行为不红**的断言。本批里被负向对照证明没抓住东西的五个例子，
都是这两条各自的反面：源码断言（断言 CSS 里写了 `overflow-y: auto`，该断的是元素真的能滚）、
「结果是数字」这类只验类型的自检（#1138，失败路径返回的 0 也是数字）、
跑在 `tmp_path` 上而生产路径另有其形的路径测试（#1156）、
读「下一帧」而不是「它要的那一帧」的 socket 断言（#1197）、
只比键和占位符不比渲染结果的 i18n 闸门（#1202/#1166）。

### 3.1 七条缝（十行，A、C、E 各拆两行），各自的替身、内容、速度、层、门槛

**先看最后一列。**「PR 必过」在今天是一句空话：`branch-protection.json` 是 403（这个 plan 开不了 required status check）、
428 次合并里 370 次是命令行 `gh pr merge`、平台自己的 `required_checks` 是空元组
（`backend/app/domain/project/protection.py:73`、`backend/app/domain/review/merge_state.py:323`）。
**这一列要等 §4.3 已定的那条（GitHub 侧的分支保护）落地才成立**；在那之前它是「应当必过」，不是「会被拦住」。
§3.4 那句「隔离套件超 5 条就停止合并新功能」同理。

倒数第二列是今天的被测对象：标「有」的缝，178 次族归属里有 PR 作证，可以现在就动工；
标「随实现交付」的缝，被测的代码今天还不存在（DECISIONS 里的新设计），
**它们不占 PR 的时间预算，也不进门，跟着那部分实现一起交付**——否则就是把大半个测试体系压在还没写的代码上。

| 缝 | 替身 | 测什么 | 多快 | 层 | 今天有被测对象吗 | 门槛 |
|---|---|---|---|---|---|---|
| **A-1. 骨架契约：五个动词 + hook 词汇表**（结论 29） | `ContractHarness`：一个什么能力都不声明的最小实现 | 五个动词的语义（`ensure` / `send` / `backlog` / `interrupt` / `close`，`harness/__init__.py` 的 docstring 已经把它们定义好了）；**hook 词汇表**——每种 hook 事件必须出现、字段齐、`PostToolUseFailure` 与 `PostToolUse` 分开（#1096 那条：工具出错在界面上和成功长得一样）；**transcript 每轮结束前增量上交**，且恢复从平台副本恢复；能力矩阵每一格要么「有」要么指向封闭枚举里的一条差异码 | 全套 ≤ 60s | contract | **有**（族 9 的 12 个、`harness/__init__.py:456` 的 `HARNESSES` 注册表已在） | PR 必过。**夹具落点已经存在**：pi 的 TS 侧今天有 `.github/workflows/test.yml:322` 的 `extension` job 跑 `node --test backend/tests/extension/platform.test.ts`（412 行、2.7 秒、无 bundler），「pi 跑同一份夹具」就落在这个 job 里。夹具格式：一份 `harness-contract/*.json`，每个文件一个场景（输入事件序列 + 期望的 hook 事件序列 + 期望的能力矩阵行），由 Python 侧生成并 commit，Python 用 pytest 参数化读它、TS 用 `node --test` 读同一批文件。今天 `backend/scripts/test_harness_contracts.py:31` 的 packages 只有 claude-code 和 codex，pi 不在 |
| **A-2. 骨架契约：原生子 agent 三项**（结论 43） | `FakeSubagent`：可被要求「指定模型 / 发一条带线程标识的事件 / 被父线程改指令 / 被停掉」 | 三项硬性要求各一条；hook 按线程标识归卡；**卡上「这条活花了多少」由这些 hook 的用量按线程标识累加算出来，而模型请求上没有「这是哪张卡」这个字段**（结论 53）；子 agent 与父进程同生同死 | ≤ 20s | contract | **没有**（结论 43 是新设计，178 里一个 PR 都不落在它身上） | 随该部分实现一起交付，在那之前不占 PR 预算、不进门 |
| **B. 地点契约**（结论 23/24/39/40/55/57） | `LedgerPlace`：一个记账的假地点，能被要求进入 租出 / 离线 / 休眠 / 被回收 四态，并记下每一次租、问能力、归还 | 租 → 问能力 → 归还三步各自的收据；**离线是一条事件而不是一次工具报错**（I25② 的断言：agent 读到的字符串里不含裸 HTTP 状态码）；那一轮的工具表里项目工具直接标不可用、**不让它们各自超时**；**期限只有一个来源、到期只有一种错误表达、并作为一条事件进 agent 的下一轮输入**，平台不据此换资源（结论 55）；回收前三张收据（transcript 落库、记忆整理跑过、未提交工作已推）；**突然损坏取不到收据，那一条单独断言两件事：重派路径在发出任何重试之前**先读**平台侧的执行记录，并据它把「确定没做」与「结果未知」分开；「结果未知」的操作不被自动重发，而是交人确认**（结论 57）；休眠后在 reconnect window 内恢复同一台、工作区原样 | ≤ 30s | contract | **一半**。「离线是事件」这一半有被测对象（族 3 的对端状态那 6 个），四态租约与休眠（结论 39）今天不存在 | 离线那一半 PR 必过；租约四态随实现交付 |
| **C-1. 执行器线格式**（今天的实现契约，不是 DECISIONS 条目） | `FrameFixtures`：Go 与 Python 共享的一份 JSON 夹具，Python 生成 / Go 解析、Go 生成 / Python 解析。**纯夹具对拍，不起任何进程** | `execution.call/data/result` 三帧两语言对拍；**对端状态编码表**（离线 / 在线不答 / socket 不存在 / 拒绝凭据 / 机器自己报错 五种输入 × backend 侧还原出的异常类型、日志等级、agent 看到的话 三列）；**平台工具不经过执行器**（结论 21，一条守卫） | 几十毫秒，单条在 contract 的 200ms 上限内 | contract | **有**（族 1 的 20 个、族 3 的 17 个） | PR 必过 |
| **C-2. 执行器协议的真往返**（今天的实现契约） | `WireExecutor`：真 owner ASGI app + **真的 Go 连接器进程** + 真 runtime，只有机器是本机容器 | 一次 `Bash` 的完整往返；**版本错配矩阵**——CI 里起**上一个已发布的 owner 镜像**（从 ghcr 按 tag 拉，不在本 job 里构建）+ 本 PR 的 backend，跑一遍工具调用、会话恢复、执行端点鉴权；老 owner 遇到新方法必须答一个可区分的「这个 owner 不认识它」而不是 403，且调用方不得因为一个方法被拒就丢掉整份目录（#1026 的真实损失）。#1259 的测试形状是对的（改名一列、调执行端点、再改回来），但它跑在**同一个进程**里，所以抓不到版本差 | ≤ 12 分钟（含拉镜像） | acceptance | **有**（族 1、5、14、16 全部） | **触碰即跑**（`remote_execution/`、`cli/`、`device_hub*`、`routes/execution.py`、`executor_transport.py`）。判据里加一条：`acceptance.py:363` 的 `LocalDeviceHub` 消失，`room_fixture.py:174-177` 那一行消失 |
| **D. 托管方契约**（今天的实现契约；结论里只有 51 这一条产品规则，判据里的 I23 是不变量，不是结论） | `LedgerForge`：一个记账的假 forge，能被要求答 绿 / 红 / 还在跑 / 未知 | 合并、提案、读检查结论三个动作；**平台读结论不算结论**（喂一个红结论，断言采纳被拒；喂一个「还在跑」，断言采纳等待而不是放行）；卡上带 forge 身份；**评审落点由「这个项目的用户在哪」决定，不由 forge 实现决定**（结论 51）——同一个假 forge 配代码项目时卡上是一条提案页链接、配文档项目时评审在房间和卡片里，forge 只收存档 | ≤ 20s | contract | **有**（`merge_state.py:323` 那个空循环、采纳路径 16/58 在检查跑完前合，见 §4.3） | PR 必过 |
| **E-1. 投递寻址**（结论 13/14/15） | **无替身——它是纯函数** | 输入 `(事件, 名册, 下一步在谁手上)` → 输出 `(收件人集合, 原因)`。对每一类事件断言：下一步在平台手上时收件人为空，转到某个参与者手上那一刻恰好一次（结论 15）；事件落在它「关于」的那个东西上——卡的事落卡、房间的事落时间线、项目的事落总览（结论 14）；平台从不发起一轮（结论 13：今天 5 处平台自召唤，断言它们在这张表里都变成了「事件 + 收件人」） | 全表 ≤ 2s | pure | **没有**——「投递寻址是一个纯函数」这件事今天不成立，收件人散在各处。族 4 里被结论 14/15 根除的那 7 个是这条缝将来的回归集 | 随实现交付；那 7 个 PR 的行为在纯函数出现的同一个 PR 里变成表里的 7 行 |
| **E-2. 投递记录**（结论 58） | 一份**可被要求在「写入之后」与「发出之后」两个点崩掉**的投递账本 + 假时钟——寻址是纯函数，投递记录不是：它有状态，而且必须能被从中间打断 | 结论 58 点名的三条：①**落库后崩**——投递记录已写、还没发就崩，重启必须补发，且只发一次；②**发出后崩**——已经发出、确认还没写回就崩，重启必须靠去重键不发第二次；③**重复投递**——同一条事件被算了两遍，每个收件人仍然只收到一次。另加同属 58 的两条：去重键跟着**事件**走而不是跟着这一次发送尝试走；补发时名册按**事件发生的时刻**取，不按补发的时刻取 | ≤ 10s | contract | **没有**——投递记录今天不存在（`../arch/ARCHITECTURE-v2-2026-09-19.md` §9.2 把它列在「今天不存在的东西」里） | 随实现交付 |
| **F. 平台 MCP 工具**（结论 21） | `ToolTable`：会话侧的一张常量表，**不问执行器** | 工具表在机器离线时仍然完整（#1051 那族的根治）；六个工具各一条（chat_send、cheese ask、反馈、要一台机器、同 handle 便条、定时投递）；一条守卫断言这六个的调用路径上没有 `client.call("invoke", …)` | ≤ 10s | pure + contract | **有**（`client.py:940-957`、`client.py:710-745` 今天就在，#1051/#1186 作证） | PR 必过 |
| **G. 记忆文件**（结论 42/8/54） | `MemoryDir`：一个临时目录 + 假时钟 | 一个事实一个文件、元数据带「关于谁、何时观察到」；回忆规则按 I16①②：关于人的池**不以那个人在不在这个房间为条件**（守卫：取记忆的代码里没有一处按在场名册过滤池），而跨项目仍然读不到——池按 (实例, 项目) 关着；写入分类（任何提到某个人的判断只能进那个人的池）；可见性（关于某个人的只对当事人可见，不进房间、不进时间线、改了不通知）；整理 = agent 重写自己的文件，历史可翻、可删 | ≤ 15s | pure | **没有**——今天记忆在库里（`MemoryScope`），不是目录里的文件；178 里没有一个 PR 落在它身上 | 随结论 42 的实现一起交付，在那之前不占 PR 预算、不进门 |

**缝之外还要两条全局不变量**，它们不属于任何一条缝，但族 5 和族 11 只能靠它们：

- **连接不跨 I/O 边界**：一个计数型 session factory，断言在任何 provider client 的 I/O 边界上打开的 session 数为 0。
  #1243 的 `test_screens_are_adopted_with_no_transaction_open` 已经是这个形状——把它做成对**所有** provider client 生效的 fixture，
  而不是一个文件里的一条用例。任何新写的「持锁 await」在它自己的 PR 上就红。
  配一条机械生成的对偶 [已定] 结论 59：对每一把行锁，
  **在那段外部 I/O 进行的时候**，另一个连接对同一行 `SELECT … FOR UPDATE NOWAIT` 必须成功——
  断的是「这条外部调用没有把这一行锁在手里」，不是「持有锁期间别人也能拿到锁」（那句话自相矛盾，写不成测试）。
- **每轮预算**：一次完整的 turn 组装里统计并设上限——对设备的 exec 次数、传输字节数、DB 查询数、
  其中的 seq scan 数。替身是一个计数的 fake device channel + SQLAlchemy 的 `before_cursor_execute` 钩子。
  这一条会同时抓住 #1043 的 450KB 每轮重传、#1048 的 1094 行全表扫、#1107 的每房间一棵依赖树——
  **三个都是先有人去量才被发现的，而量的动作本身可以是一条测试。**

### 3.2 层，和每层的速度目标

**这张表的前提**：8 worker 今天跑不起来（每 worker 两个数据库，翻倍就是 Postgres 连接翻倍，
`project-cheese-dev-box-saturation-queuepool-20260918.md`），
所以下面凡是标 `@ 8 worker` 的数字**都要等第 5 节第 2 条（加 runner）拍板之后才拿得到**。
在那之前按 4 worker 算，pure 与 integration 两层的墙钟翻倍，「PR 必过 ≤ 10 分钟」不成立。

第二个前提：**PR 上跑的不只是 pytest**。`.github/workflows/` 里在 `pull_request` 上点火的是
**12 个 workflow**——`test`、`frontend`、`e2e`、`cli`、`harness-contract`、`mcp-contract`、
`deploy-scripts-test`、`repo-guards`、`ops-guard`、`empty-pr-guard`、`remote-execution`、`claude-md-review`。
§4.3 那个「中位 22 分钟」正是它们一起挤同一个 `cheese-ci` 池的结果，不是 pytest 自己慢（`audit-gates.md:179` 算这个数时按三台机器三个 slot 算；#1070 之后一台可跑两个，所以真实并发在 3 到 6 之间，池的大小本身就是 §5 第 2 条要定的事）。
所以下表按**语言/入口**分行，不是只分 pytest 的四层。

| 层 | 入口 | 允许碰什么 | 单条上限 | 全层上限 | 今天的墙钟 | 门槛 |
|---|---|---|---|---|---|---|
| **pure** | pytest `-m pure` | 纯函数、假时钟、临时目录。**禁** DB、**禁** event loop、**禁** `sleep`、**禁** `client` fixture | 10 ms | **30 秒** @ 8 worker | 今天不存在（混在 `test` 里） | PR 必过 |
| **contract** | pytest `-m contract` | 一条缝的两端，一端真一端替身。真 DB，但用**事务回滚**而不是 TRUNCATE | 200 ms | **3 分钟** | 今天 `backend/tests/contract/` 17 文件 62 个 def，与 unit/integration 混跑 | PR 必过 |
| **integration** | pytest `-m integration` | 真 Postgres + 真 alembic + 真 ASGI + **生产的 QueuePool** | 2 s | **6 分钟** @ 8 worker | `test.yml` 整体 13 分 25 秒（6838 条） | PR 必过 |
| **frontend 单元** | `frontend.yml` vitest + eslint + vue-tsc | jsdom，组件与 lib | — | **2 分钟** | install ~40s + eslint ~90s + vitest ~56s ≈ 3 分 06 秒 | PR 必过。**族 6 的 13 个整族长在这一层和下面的 e2e 层**，四层表里原本没有它们的位置 |
| **pi 扩展** | `test.yml:322` 的 `extension` job，`node --test backend/tests/extension/platform.test.ts` | 无 bundler、无 package.json，手写 pi stub | — | **10 秒** | 2.7 秒 | PR 必过。**缝 A-1 的 TS 侧夹具落在这里** |
| **Go 单元** | `cli.yml` 的 `go test -race ./...`，`timeout-minutes: 15` | `cli/**/*_test.go` 13 文件 | — | **3 分钟** | 在 15 分钟预算内 | PR 必过。**族 1 的 Go 侧长在这里**；`cli/internal/link` 今天连一个 `_test.go` 都没有，缝 C-1 的夹具解析侧要加在这 |
| **deploy shell** | `deploy-scripts-test.yml`，`deploy/tests/*.sh` | 真 bash，fake `df`/fake `docker` | — | **2 分钟** | 小 | PR 必过。**族 7 回收器的两条测试落在这里** |
| **acceptance** | `remote-execution.yml`（`suite.py` → `acceptance.py`）、`harness-contract.yml` | 钉住的二进制、真容器、真连接器进程、上一个已发布的 owner 镜像 | — | **12 分钟**（不含预热） | 中位 19.8 分钟、最长 192 分钟；`timeout-minutes: 20` 里含 7-13 分钟镜像构建 | 触碰即跑；main 连续 20 次绿之后才当必过门（见第 5 节第 4 条） |
| **Go 交付 e2e** | `cli.yml` 的 `go test -tags claudee2e -timeout 35m ./e2e/`，MockServer 扮 Anthropic API | 真 Claude Code 进程 + 真 tmux 层 | — | **10 分钟** | `timeout-minutes: 40` | 触碰即跑。**这是全仓唯一证明过「一条 prompt 变成一次真 Bash 工具调用写出文件」的东西**，缝 C-2 要接在它和 `acceptance.py` 之间，不要另起炉灶 |
| **浏览器 e2e** | `e2e.yml`，Playwright | 真前端生产构建 + 真后端 + 真 Postgres | 15 s | **4 分钟** @ 4 worker | 单 worker 5.1 分钟，`retries: 2` | PR 必过（见 §4.2 的三条硬约束） |
| **夜跑** | `evals/`（今天不在任何 workflow 里）、`device-smoke.yml`、规模替身、真浏览器跑全部路由的布局不变量 | 真模型、真机器 | — | 无上限 | `device-smoke` 最后一次运行是 2026-07-26 | **不阻塞合并**；红了开 issue |

**PR 必过的那几行加起来 ≤ 10 分钟墙钟**：pure 0.5 + contract 3 + integration 6 是同一台机器上的一个 job（串起来 ≤ 10 分钟），
frontend 2、extension 0.2、Go 单元 3、deploy shell 2、浏览器 e2e 4 是并行的另外五个 job，各自都在 10 分钟内。
今天 `test.yml` 在 PR 上从触发到绿的中位数是 22 分钟、
p90 94 分钟、最长 339 分钟（150 次运行，`audit-gates.md` §1.5）；job 本身 13 分 25 秒，其余全是池里的排队——
**也就是说墙钟的大头不在测试设计，在 slot 数**，这也是为什么第 5 节第 2 条（加 runner）是这一整节的前置条件。

**怎么达到，逐条带数：**

1. **pure 层今天不存在，它是最大的一块。** 6838 条用例跑 805 秒，top-20 durations 加起来只有约 165 秒（20%），
   剩下 6818 条摊掉 3055 个 worker-秒，**平均 0.45 秒一条**。对一套以单测为主的套件来说这个均值本身就是结论。
   这些用例慢不是因为它们在算什么，是因为它们跟 `client` fixture 混在一个 run 里。
   把不需要 DB 也不需要 ASGI 的那部分（估计 4000 条以上，推论）搬进 pure 层，目标 30 秒内跑完。
2. **砍每条 client 用例的固定开销，不是砍用例。** `backend/tests/conftest.py:434` 每次：
   建新 engine（NullPool）→ `_truncate_all` **TRUNCATE 109 张表** RESTART IDENTITY CASCADE →
   `ensure_agent_user` 重新 seed → 起 `TestClient(app)` 进 lifespan（启动所有周期任务）→
   退出时 `wait_work_idle()` 轮询 → `pytest_runtest_teardown` 再开一条到维护库的连接查 `idle in transaction`。
   **1274 个 test def 的签名点名 `client`/`python_client`。**
   改法：contract 层一条用例一个事务、结束 rollback（不 TRUNCATE）；integration 层保留 TRUNCATE 但只清用例声明的表子集；
   lifespan 每个 worker 起一次而不是每条用例一次。
3. **DB 模板克隆已经到位，别再投入。** `conftest.py:645` 按 alembic versions 目录的 sha256 建一次
   `cheesex_tpl_<fingerprint>`，其余全部 `CREATE DATABASE ... TEMPLATE` 克隆。
   这就是「integration 的并行模板库」，收益已经吃掉了；代价只在 migration 变了的第一次。
4. **237 处 `sleep` 换成假时钟**，其中 83 处 ≥0.5 秒。
   单是 `unit/test_hooks_substrate.py:2037` 那一条（用生产值 `idle_suspect_s=30, hard_ceiling_s=30` 真等，CI 上 **30.03 秒**）
   就占全套 805 秒的 3.7%。还有 183 处 `for _ in range(...)` 轮询循环，同样换时钟。
5. **worker 从 4 提到 8。** `CHEESE_CI_TEST_WORKERS` 实际是 4；每 worker 两个数据库，
   翻倍就是 Postgres 连接翻倍，今天被机器资源卡住（`project-cheese-dev-box-saturation-queuepool-20260918.md`）。
   这不是测试设计问题，是机器，见第 5 节第 2 条。
6. **acceptance 的 12 分钟里不许包含 `uv sync` 和镜像构建。**
   `harness-contract.yml:22` 和 `remote-execution.yml:28` 的注释实测记着
   「`uv sync` on the shared pool takes 9-14 minutes under contention」——
   这两个 job 的墙钟里测试本身只占约 2 分钟。把预热挪进 runner 的 job-started hook，不计进 `timeout-minutes`。
   `remote-execution.yml` 的 `timeout-minutes: 20` 今天包含 7-13 分钟的镜像构建，所以帽子在清理阶段触发，报成 *cancelled*，而每一步都是绿的。

### 3.3 删什么：修修补补的测试怎么处置

**不是删覆盖，是把它们的断言搬到缝上，然后删文件。** 四类，各自的处置：

**(1) 按事故命名的（两周新增 155 个文件，35 个只有 1 条用例，75 个 ≤2 条）。**
每个文件问一句：它断言的那个事实属于哪条缝？搬过去做成参数化表里的一行，再删文件。举例：

| 今天的文件 | 它断言的事实 | 搬到哪 |
|---|---|---|
| `unit/test_owner_timeout_status.py`（#1118）、`test_owner_rpc_unexpected_409.py`（#1111）、`test_owner_device_answer.py`（#1231）、`test_error_response_headers.py`（#1114）、`test_recovery_waits_for_the_machine.py`（#1248） | 对端的五种状态各自还原成什么 | 缝 C-1 的「对端状态编码表」**一张表五行** |
| `unit/test_recovery_burst_is_bounded.py`（#1250）、`test_device_carries_a_batch_onto_the_next_one.py` | 扇出有上限 | 规模替身（71 台设备）一条 |
| `unit/test_device_snapshot_poller_survives.py`、`unit/test_event_drain_refusal.py`（#1101） | 后台循环不会静默死掉 | 后台循环基元的一条共用契约测试（第 N 次抛非预期异常 → 第 N+1 次仍跑 + 恰好一条带调用方名字的 ERROR + 被拒条目 10 秒内不超过 6 次请求） |
| `unit/test_db_pool_fits_the_server.py`（#1103） | 三个池塞得进一个 Postgres | 留着，它已经是正确形状（全仓唯一能写下这个算术的地方） |
| `unit/test_regression_round12.py` | 九个互不相干的东西，唯一共同点是同一轮修的 | 九条各自归位，文件删 |
| `integration/test_bug{3,5,7,8,11,12}_*.py` | 六个更老的同类 | 同上 |

**(2) 断实现的（203 处直接断言私有成员）。**
最集中的 `unit/test_analytics_view_helpers.py` 整个文件在测 `_to_timestamp_ms` / `_safe_ratio` / `_parse_approved` 等
六个私有静态方法的返回值，一条 HTTP 行为都没有——**改名就红，改行为不红**。
处置：把它们的输入输出搬到那条 HTTP 路由的 contract 用例上，文件删。
同类还有两种：金样本比对（`unit/test_harness_prompt_contract.py` 整个文件是
`assert {...} == json.load(fixtures/harness-prompts.json)`，提示词改坏了它一样绿，只要同步改 fixture）——
换成缝 A-1 的 hook 词汇表断言；读源码文本来断言（`unit/test_tool_labels.py:66`、
`contract/test_api_addressing_contract.py:375`）——这一类反而要**留下并推广**，
因为它是「两处真实来源比对」，抓的是漂移不是当前值（#1158 从 argparse 命令树和 `registerTool` 字面量两处读，比对 `toolLabels.ts`）。

**(3) 靠 sleep 撑的（237 处）。** 见 3.2 第 4 条：换假时钟。
换不掉的只有一种——「它不是返回错答案，是不返回」（#1188 的指数回溯），那一类用一个硬上限当断言，不是用 sleep 当等待。

**(4) skip 的（37 处，CI 上 33 条 skipped）。** 三类分开处置：

- **最坏的一类，断言不成立就 skip，于是永远绿**：`integration/test_discussion.py:50`
  （`if task_resp.status_code != 200: pytest.skip(...)`）、`integration/test_task.py:3032/3051/3154/3234`
  （`if custom_category_id is None: pytest.skip("Custom category was not created")`）。
  **立刻改成 fail。** 这几条的语义是「前置接口坏了就当这条测试不存在」。
- **整文件永久 skip，因为被测的东西压根没迁过来**：`integration/test_project.py:9`、
  `integration/test_notification.py:74`。**删掉**，它们测的东西不存在。
  `contract/test_projects_contract.py:18` 看着同类，**但不能删**：它的 skip 理由是文件自己写的
  「`python_client` 没有凭据，所有探针在到达形状之前先 401；port 到 `authed_client` 就是修法，
  **这是测试夹具的问题，不是契约问题**」——被测的 `/team-projects` 接口存在，
  `tests/integration/test_team_projects.py` 还在端到端跑它。删了就是删覆盖。
  处置是**换 `authed_client` 重新打开，缺件即 fail**。
- **环境缺件就 skip**：改成「缺件即 fail」，并在 job 开头一个显式的 precondition step 里装。
  `test_harness_contracts.py:90` 已经意识到这个坑（检查 junit xml 里有没有 `skipped`，有就退 1 说 acceptance is incomplete），
  但它只管那一个 job。推广成一条通用规则：**任何套件在环境要求不满足时必须失败，不是 skip；
  并且 job 结束时断言「实际执行的用例数 ≥ 预期条数」**（#1236 的教训：夜间 canary 两天报绿，八条测试只跑了一条）。

### 3.4 flaky 政策

**不重试、不加 sleep、隔离 + 根因。**

- **删掉重试**：`e2e/playwright.config.ts:25` 的 `retries: process.env.CI ? 2 : 0`；
  删掉 worktree 里的 `tmp/rerun_until_green.sh`（它的注释是
  「Re-run infrastructure-cancelled jobs on a PR until every check passes」——它就是「CI 红了照样合」的实现）。
- **不加 sleep**：任何新增的 `sleep ≥ 50ms` 在 lint 里红，除非同一行注释写明它等的是哪个真实的物理事实。
- **判据**：同一个 commit 连跑 20 次，红 ≥ 1 次 = flaky。这是一条 job，不是一次人工判断。
- **处置**：24 小时内根因。修不完就从门上摘到一个隔离套件——**隔离套件每天跑一次，
  名单贴进日报，条目超过 5 条就停止合并新功能**。摘掉不是删掉。
- **基础设施失败与测试失败要一眼分得开，但两种都报 failure** [已定] 结论 59：
  **环境故障报 failure，并写清原因，不报 neutral。** neutral 的意思是「这次不算数」，
  而一次 OOM、一次磁盘满、一次上游拉不下来**正是一次真的失败**——只是失败的不是这个 PR。
  报成 neutral 就等于让它不阻塞、也不被读，于是三台 runner 的问题永远没人修，
  这正是族 2「红不携带信息」的来源。
  做法：每个 job 开头打印一行资源快照（磁盘余量、tmpfs 用量、产物配额、上游可达性），失败时这行就在日志顶上；
  环境前置做成显式的 precondition step，**它失败就以那个 step 的名字 failure**，
  失败摘要里第一行写明「这是环境，不是这个 PR」以及是哪一项；
  这类失败单独计数，进日报——它要的是有人去修那台机器，不是被静音。
  今天判断「这是不是我的错」要靠人去三台机器上 `df`，而
  「一晚上 runner-1 四次 OOM，slot 1 把盒子填满、slot 1b 的 esbuild 替它死，
  从死掉那个 job 里看到的是 `exit code 137` / `[vite] Internal server error` / `gw0..gw3 node down` / `The operation was canceled`——
  四种症状没有一种提到内存」（#1122）。

### 3.5 真模型

**不在任何门上。evals 是回归，不是门。**

- `harness-contract.yml` 装真二进制（`@anthropic-ai/claude-code@<PINNED>`、`@openai/codex@0.154.0`），
  但它测的是这些二进制**发出的请求形状**，不是模型输出——那是协议契约，留在门上，并按结论 29 补上 pi。
- 任何需要真推理的东西（`evals/`、`device-smoke.yml` 的真 turn）只当回归：每天一次、结果进报告、红了开 issue，不阻塞合并。
- `evals/` 今天 3 个场景、**不在任何 workflow 里**（`grep -rn evals .github/workflows/` 是空的），
  而且 `AGENT_SANDBOX_ENABLED=false`，所以那个 turn 里**没有任何平台工具**。
  接进夜跑之前先把平台工具打开，否则它测的不是这个产品。
- 判模型输出好坏的东西永远不当门，理由是它会把「模型今天心情不好」变成「这个 PR 不能合」，
  而这正是族 2 制造出「红不携带信息」的那条路。

### 3.6 agent 这一侧的四层，四层都要有 [已定] 结论 62

他 2026-09-20：「得做，都得有」。§3.1 那十行缝管的是平台自己的接口；一个 agent 产品还有四层是那十行盖不到的：

1. **真骨架配假模型。** 起真的 Claude Code 进程，模型侧是一份**按工具结果分支**的剧本
   （不是按轮次序号分支——按序号的剧本在工具改了行为之后照样绿），transcript 由真骨架写出来。
   落点已经在库里：`cli.yml` 的 `claudee2e`（MockServer 扮 Anthropic API，§3.2「Go 交付 e2e」那一行）
   是全仓唯一证明过「一条 prompt 变成一次真 Bash 工具调用」的东西，剧本分支加在它上面，不另起炉灶。
2. **契约与轨迹测试。** 契约就是 §3.1 那十行；**轨迹**是它们盖不到的那一半——一轮里事件的**顺序与配对**：
   `PostToolUse` 必跟在它自己那条 `PreToolUse` 之后、一次打断之后没有第二条结果帧、
   hook 的线程标识从头到尾归到同一张卡（缝 A-2）。断的是序列不是单帧的字段，
   形状同 §3.0 第五种「消息序列在 ASGI 层断言」。
3. **录制回放**：真 provider 的形状要紧时，录一份真请求／真响应存成夹具，之后回放。
   它管的是「我们发出去的请求长得对不对」，不是模型答得好不好，所以它可以进门。
   这一层今天在 `backend/`、`cli/`、`e2e/` 里没有任何落点（`vcr`/`cassette` 零命中）；
   夹具过期由 §3.5 的夜跑（真二进制、真 provider）提前预告。
4. **故障注入**：工具超时、坏结果（空输出、截断的 JSON）、模型拒绝、上下文溢出、重试风暴、schema 漂移，各一条。
   §2.3 那张「一张共用的故障注入表」是它在适配器那一侧的形状，这一层把它推到**整轮**上。
   **重试带来的重复副作用靠两样挡**：幂等键，和「先查状态再重试」——后者正是结论 57 的执行记录，
   它的断言写在缝 B 里。

**真模型只进定期评测，不进合并门**——§3.5 一个字不改。
这四层里第 1、2、4 层今天各有一小块落点（`claudee2e`、`backend/tests/contract/`、§2.3 那张表），第 3 层没有；
四层都随各自那部分实现交付，不占今天 PR 的时间预算。

---

## 4. 五条抱怨各自的根治

`COMPLAINTS.md` 是五行，不是四行。下面按原话顺序，第一条是最容易被跳过的那一条。

### 4.0 「从环境构建到产出，每一个请求调用之类的」

这句话覆盖的是**一个房间从无到有、到交出东西**的整段，而它在 178 次族归属里的落点很集中：
族 9「能力与环境靠推断或靠声称，从未被行为验证」12 个、族 7 的工具安装那一段（#1144 每房间重装一遍工具）、
#1093「机器缺文档要的工具」、#1064「浏览器镜像没被构建」。
共同机制一句话：**有一句话声称某个东西在某个地方（工具装好了、镜像会被构建、缓存在这个路径、setup 脚本生效了），
而守着它的测试检查的是结构——YAML 里有这一步、Dockerfile 里含这个字符串——不是行为。**
#1093 的原话最直白：没人发现，是因为守着它的测试检查的是结构。

**判据两条，都不是新造的：**

1. **差异码矩阵**（缝 A-1，结论 30/43）。
   环境能力不许再由「恰好相关的事实」推断出来——今天 `api/routes/terminal.py` 还在用
   「有没有开着的 screen」冒充 `draws_on_its_screen`。
   矩阵每一格要么「有」，要么指向封闭枚举里的一条差异码；
   读这个能力位的**每一个分支**都必须走 `harness/__init__.py:456` 的 `HARNESSES` 注册表，一条守卫断言没有旁路。
   这一条把族 9 的 12 个从「线上发现」降级成「CI 里矩阵少一格」。
2. **`device-smoke.yml` 断言的那三件事，就是这句抱怨的验收**：
   #94 设备 clone/push、#95 agent 自己填验收卡、#96 chat WS 转帧——
   从环境构建（clone）到产出（填卡）到每一个请求调用（WS 转帧）三段各一条。
   它今天只有 `workflow_dispatch`，**最后一次运行是 2026-07-26，八周前**。
   接到 `deploy-dev.yml` 成功之后自动跑（见 §4.4(a)2），失败报警。

**第三条，今天完全没有**：一间新房间的环境构建本身没有任何预算。
#1144（每房间重装一遍工具）、#1107（每房间一棵依赖树，同一个项目 220 个 worktree = 236GB）
都是先有人去量才被发现的，而量的动作可以是一条测试——见 §3.1 末尾的「每轮预算」，
把它的统计项扩到「一间房从创建到第一个可用工具」这一段：装包次数、下载字节数、磁盘增量。

### 4.1 「toolcall 感觉也经常挂」

一次 `Bash` 从模型吐出到结果回到模型，今天要穿过 **13 跳、10 个进程、5 种传输、至少 15 个各自独立的超时**
（清单见 `audit-toolcall.md` §1.1）。428 个合并 PR 里 **76 个（17.8%）落在这条路上**，其中 32 个是 fix。
「经常挂」不是某一跳坏了，是这条路太长、每一跳自己发明超时和错误编码、而接缝上没有人。

**三件事，缺一不可：**

**(a) 路径变短**（结论 21/22）。
平台的 MCP 永远在 claude 进程所在的机器上——这件事已经做了一半：
`chat_send`、`platform_request` 和 `cheese.DIRECT_MCP_TOOLS` 里的 `cheese_*` 今天已经从 `client.py` 直接打平台 HTTP。
没做完的两处：不在 `DIRECT_MCP_TOOLS` 里的 `cheese_*` 仍然 `client.call("invoke", …)` 下机器（`client.py:940-957`），
`tools/list` 仍然要问执行器要目录（`client.py:710-745`）。做完之后直接消失的：
- #1186 那三小时（`Executor.cli` 抢 `cwd_lock`，平台工具的列表请求排在一条前台 shell 命令后面，Claude Code 30 秒超时就把整个 server 丢掉，
  一个 dev 房间三小时里拒绝了它的每一个工具）——平台工具根本不经过执行器，`cwd_lock` 与它无关。
- #1051（列表短一截，`No such tool available: mcp__native__cheese_status`）——平台工具的名单是会话侧自己的常量。
- 机器离线时聊天发不出去。

结论 22 的另一半是**拿掉会话侧的替身**：`forwarded_fs.py` 把项目 FUSE 挂到中心机，
以及随之而来的整层路径翻译（`proxy.js:7-14`、`executor_transport.py:360-364`、
`agent/execution.py:17-45` 那个 FROZEN 的 `UNRECORDED_EXECUTOR_STATE`）——#950、#1149、#1153 三个 PR 全是这层的税。
**一条待核，不算在根治清单里**：静态上看，`Glob`/`Grep` 在 device 房间里恒被 deny——
`client.py:35-45` 的 `NATIVE_TOOLS` 含它们而 `proxy.js:2-4` 的 `native` 集合不含，
guard 回一句 `Central execution is disabled`。
两处集合的差我核过，成立；**但「活房间里真的每次都 deny」没有实测过**，
所以它不进「做完之后直接消失的」那张单子，先去一个活的 device 房间跑一次 `Grep` 确认。
若属实，按结论 22 处理：要么真在机器上跑，要么如实标不可用。

**(b) 每跳有契约**（缝 C-1 与 C-2）。两条最要紧的：
1. `execution.call/data/result` 三帧写进 `device_link.py`，加 Go↔Python 的夹具对拍。
   判据：`device_hub.py:616` 那个手拼 dict 消失；`cli/internal/link` 有 `_test.go`。
2. `room_fixture.py:174-177` 那一行换成真的连接器进程 → 真的 owner app → 真的 runtime，
   同时 `acceptance.py:363` 的 `LocalDeviceHub` 消失（它的 `exec` 就是本机 `subprocess.run`，
   等于把 DeviceHub 整段摘掉）。
   判据：那一步的名字（`Verify ordinary room turns through the connector`）变成真的；
   #1050、#1111、#1153、#1259 这四个故障各自有一个会在这套里红的用例。
   接的地方是 `cli.yml` 已有的 `claudee2e` 交付 e2e（MockServer 扮 Anthropic API，
   全仓唯一证明过「一条 prompt 变成一次真 Bash 工具调用写出文件」的东西）和 `acceptance.py` 之间，不另起炉灶。

**(c) 机器状态是一条事件；超时保留，但它是感知不是决策**（结论 23/40/55）。
今天「离线」这一个事实在路上被编码/解码了四次：`DeviceOffline` → HTTP 409 + `X-Device-Id` header → 解回 `DeviceOffline` → 各调用方再翻成自己的话。
**#1111 / #1114 / #1118 / #1231 这四个 PR 的全部内容，就是让这条编解码链不撒谎。**
而「在线但不答」必须靠**有没有那个 header** 才能和「离线」区分开。
结论 23 落地后这条链整个不存在：机器状态是平台送到 agent 面前的一条事件，那一轮工具表里项目工具直接标不可用。
**超时不会消失，它变成一个** [已定] 结论 55：超时是**感知**，不是决策。
`CONNECT_RETRY_WINDOW_S=180`、`OWNER_CONNECT_RETRY_WINDOW_S=60`、`LISTING_DEADLINE_S=20`、
pi 的 `STARTUP_WAIT_S=120` 这十五个以上的常量，问题不在于它们**存在**，
而在于它们是十五个各自为政的「不知道机器状态，只好等」，到期各翻各的话。
目标形状（不是过渡形状）三句：

- **期限、心跳、取消都保留**——跨机器的调用必须能被截断，否则一条死链路会把这一轮永远挂在那里（族 16）。
- **统一成一个期限、一种错误表达**：一份「这一轮还剩多少时间」的预算从 H4 一路带到 H10，
  替掉那十五个常量；到期只有一个码、一句话。
- **到期变成一条送到 agent 面前的事件**，由它决定下一步；平台不因为到期替它换机器、换模型或重跑这一轮（结论 40 说的是这一半）。

**这一条改的是测试判据**：缝 B 的断言从「断言没有超时器」改成
「所有跨机器调用的期限来自同一个预算对象，到期产生同一种错误码，且这个码在 agent 的下一轮输入里是一条事件」；
另一条守卫断言全仓没有第二个自己写死的超时常量。

**老实说不会消失的**：跨机器本身不会。WS 断线重连、执行器进程被杀、两个进程各写一份输出（族 14）
在 21/22/23/43 之后统统还在——它们是「工具在另一台机器上」的固有代价。
21/22/23 拿掉的是替身、编解码链和各自为政的超时，不是跳数本身。

### 4.2 「感觉现在的 e2e 测试是不是还不太行」

**这句是准确的，原因具体。** 20 条 Playwright 用例、单 worker、5.1 分钟、`retries: 2`。
在整个 `e2e/tests/` 里：`grep -rn 'executor|device|machine|机器|执行器|连接器'` → **零命中**；
`grep -rn 'reply|回复|assistant'` → **零命中**。
e2e 从来没有断言过「agent 回了一句话」，更没有跑过一次工具调用。
`agent-harness.spec.ts` 名字看着像在测 harness，实际测的是新建队友对话框里的两个下拉框，
模型列表是 `playwright.config.ts:45` 用环境变量喂进去的固定值，没有任何推理供给。
`room-work.spec.ts` 通过 API 派活然后去界面确认它显示出来了——派出去的活有没有人做、做出什么，不在断言里。

**e2e 该测什么：「人在浏览器里能不能走完一条路」，外加少量走完全程的那一条路。**
登录、进房间、发一句话看见它出现、点一个按钮看见状态变了、**布局不变量**；
**再加少量用固定假模型响应跑完整条路的用例** [已定] 结论 59——
发一句话 → agent 那一轮回来 → **用户看见回复**，其中至少一条里有一次工具调用 → **用户看见工具结果渲染出来**。
「固定假模型响应」是关键：模型侧是一份钉死的脚本（`cli.yml` 的 `claudee2e` 已经在用 MockServer 扮 Anthropic API），
所以它不判模型输出好坏、不进 §3.5 那条「真模型不当门」的禁区，测的是**渲染与投递这条路通不通**。
少量的意思是个位数，不是把缝的验收搬进浏览器。

布局不变量这一条**不是从零开始，是把已有的那一份铺开**。
`e2e/tests/layout-invariants.spec.ts`（106 行）今天已经在真浏览器里量 `.v-field` 的包围盒，
断的正是「相邻字段的浮动 label 不得压在上方字段的边框盒上」和「不得被它所在的滚动容器裁掉」，
量的是 `.v-field` 而不是 `.v-input`（后者包着空的 `.v-input__details`，会把每一对竖排字段都报成缺陷），
并且带了空断言防护：范围里一个字段都没有就抛错，不许静静地绿。形状是对的。

问题是它**只手工枚举了两个屏幕**：`/account/signin` 和「修改 AI 队友」对话框。三件事要做：
1. 把 `fieldDefects()` 提出来（今天它是 spec 文件里的一个局部函数），**对全部路由参数化**——
   新面板自动进入遍历，不需要为它写新用例。
2. **把视口压到 640×480 再跑一遍全部路由**。
3. 补一条它今天没有的断言：**任一元素 `scrollHeight > clientHeight` 时，它或它的祖先必须真的可滚**
   （`overflow-y` 是 `auto`/`scroll` 且 `scrollTop` 能被改动）。

**为什么它没抓住 #1205**：#1205 修的是简报块的 `max-height`——一个块长过它的盒子被裁掉，
这是「容器溢出」，不是「字段几何」，而 `layout-invariants.spec.ts` 只量 `.v-field` 和它的 label。
上面第 3 条正是补这一格的。#1205 的正文
「同一层以前踩过同一个坑，当时只给简报块加了 `max-height: 40vh`，验收卡这一块漏了」，
说的就是这类缺陷只能靠遍历抓，不能靠一个个补——这份 spec 自己就是那个样板。

**e2e 不该测什么：agent 回了什么内容、工具调用的正确性、机器、执行器。**
在浏览器里驱动它们既慢又无法定位，换成缝的验收：
「这句回复对不对」→ 缝 A-1 的 contract（替身骨架，断言 transcript 增量与 hook 事件）；
「工具调用真的跑起来了」→ 缝 C-2 的 acceptance；「机器离线」→ 缝 B 的 contract。
**分界是「谁的正确性」**：回复和工具结果**有没有到达用户眼前、渲染成不成立**是浏览器的事，留在上面那几条全路径里；
它们的**内容对不对**是缝的事。今天 e2e 连前者都没有——
`grep -rn 'reply|回复|assistant'` 在整个 `e2e/tests/` 里零命中。

**三条硬约束：**
1. **跑生产构建，不跑 vite dev server。** 今天单 worker 是故意的（`playwright.config.ts:24`：
   并行 worker 会同时触发冷编译把 per-test timeout 打爆），per-test timeout 拉到 60 秒、
   webServer 启动预算 180 秒，全是在给冷编译让路——**也就是说今天 e2e 跑的前端和真正部署出去的不是同一个东西**。
   换成生产构建之后并行不再触发冷编译，4 worker、per-test 15 秒、全套 ≤ 4 分钟。
2. **`retries: 0`。** 最近一次 run 是 `1 flaky / 19 passed`，flaky 的是 `login()` 的 `page.getByLabel('密码')` 60 秒超时——
   冷启动的 vite 现编路由，跟产品没关系。日常噪声压过真信号，人就不看它了。
3. **选择器限定在被测组件 root 内**，一条 lint 规则禁止 page 级 `getByRole`——
   #1069 那类 flake（Vuetify 把菜单渲染到 overlay 容器，刚关掉的菜单在淡出期间选项还在 DOM 里，读回两份合并的列表）只会再来。

### 4.3 「agent 经常 ci 红了也是直接 merge」

**字面版本偏重，但偏重的方向和这句话指的人正好相反。** 三个数：

- **合并那一刻真有红检查的是 4 个，而且这是下限**——`audit-gates.md:65`/`:493`：
  判据用的 rollup 只给每个 check 名字的**最后一次**运行，红了之后重跑到绿的这里看不见。
- **这 4 个全是 andylizf 本人，没有一个是 agent**（`audit-gates.md:84`）。
  抱怨的原话是「agent 经常 ci 红了也是直接 merge」，而**字面意义上的「agent 红着合」，实测是 0 次**。
- **agent 那一路的真实形状是「没等检查」，不是「红着合」**：425 个里 31 个在某个真检查还在跑时就合了，
  其中 **app/cheesex-app（平台采纳路径）占 16 个**，而它总共只合了 58 个 PR——
  **采纳路径上 28% 的合并发生在检查跑完之前**（`audit-gates.md:105`）。加上 main 上根本不跑重活（下面第 3 条）。

这三个数合起来说的是：他感觉到的红是真的，但机制不是「有人看见红了还按下去」，
而是**没有任何东西在他按下去之前把结论算出来**。

（口径：§1 用的 428 是两周内的合并次数，这一节用的 425 是其中**抓到 rollup**、能判断检查状态的那些，
差的 3 个没有可读的检查结论。两个数指的不是同一件事。
另外那 4 个里的三个——#652、#1008、#1169——红的都是 `review-gate`，即
`.github/workflows/claude-md-review.yml:44`「改 CLAUDE.md 必须有人批准过」这道 agent 闸门，
三次都是人自己改自己的规则文件自己合掉。
**第四个 #1103 fix(db): size the pool so a release cannot exhaust the database server 要按 `audit-gates.md:95` 的原话写**：
合并时间 09-16 17:47:21，`test` 的 `completedAt` 是 17:48:53——**合并的时候 `test` 还没红**，
合并前已经红的是 `private-chat` 和 `acceptance`，`e2e` 是 cancelled。
这个区别正好是「明知故犯」和「没等检查」的区别，而它偏偏就在连接池那条事故链上。）

真正的漏洞是三条：

1. **仓库根本配不了必跑检查。** `bugs/branch-protection.json` 是
   `{"message":"Upgrade to GitHub Pro or make this repository public to enable this feature.","status":"403"}`。
   私有仓库在当前 plan 下开不了 required status check，`allow_auto_merge: false`。**GitHub 侧没有任何东西能拦。**
2. **平台自己的补位是空的，而且只覆盖 14%。**
   `backend/app/domain/project/protection.py:73` 的 `required_checks: tuple[...] = ()` 是默认值；
   `backend/app/domain/review/merge_state.py:323` 那个 `for rc in required_checks:` 在空元组上一次都不执行，
   `running`/`red`/`missing` 三个列表全空，判决直接落到 GitHub 的 `mergeable_state`——
   而 `docs/accept-is-merge.md` 自己写着「`UNSTABLE` 意味着 merge API 用红检查回 200」。
   所以走采纳流程的 58 个 PR 里有 16 个（28%）是在检查跑完之前合的。
   而两周 428 次合并里 370 次是人和 agent 在终端里 `gh pr merge`，平台的门根本管不到。
3. **main 上 test 和 e2e 从来不跑。** 抽 12 个 main push 的 `test.yml`，每一个都是 5 个 job 里 skip 掉 3 个；
   抽 3 个 `e2e.yml`，`e2e` job 全 skip。依据是 `e2e.yml:84-107` 的 `scope` job：
   「merged squash commits contain the exact PR tree already gated」。
   **这个前提只在 PR 与 main 同步时成立**，而 `strict` 默认 false、GitHub 侧又没有 required-up-to-date、
   两周 428 次合并平均一天 30 次，PR 落后 main 是常态。
   所以 main 的 `test.yml` 98/100 success 和 `e2e.yml` 100/100 success **这两个数字是空的**——
   它们记录的是 `scope` job 跑成功了。**两个各自绿的 PR 合到一起变红，这个仓库没有任何一层会发现。**
   （这条是推论：跳过行为是抽样实测的，前提失效是三条事实推出来的，我没实测到某次语义冲突真的溜过去。）

**而 agent 学到「红 ≈ 基础设施抖了」是因为大多数时候确实是。**
`test.yml` 150 次运行里 27 次 cancelled、19 次 failure、27 次需要重跑第二次以上；
`remote-execution.yml` 在 main 上最近 20 次运行（2026-09-19 实测）是
**success 4、failure 7、cancelled 8、1 次还在跑**——当前基线 `49a7c8177` 是 failure。
按「没绿就是没绿」，这条 job 在 main 上的可用率是 **20%，不是 40%**；
cancelled 那 8 次不是「没事」，是 §3.2 说的那件事：帽子在清理阶段触发，每一步都是绿的却报成取消，
**而 cancelled 没有失败可读**。
同一份代码 PR 上绿、main 上红，说明它不是在测代码，是在测那台 runner 的负载。
**一个五次里只有一次绿的 check 不是闸门，是噪音。**

**方案与代价（已定，表留作代价记录）：**

| 方案 | 覆盖 | 代价 | 判断 |
|---|---|---|---|
| **A. 买 GitHub Pro，或把仓库转到有分支保护的 org** | **428/428**，人和 agent、命令行和采纳卡全覆盖 | $4/人/月；转 org 的话一次迁移 | **倾向这个。** 唯一一个同时覆盖两条路径、不写一行代码的方案，而且分支保护在 forge 侧，正符合 `agent-principles` 六「平台读 forge 结论，不算结论」 |
| **B. 用平台自己的采纳流程**：给这个项目配上 `branch_protection.required_checks`（最小名单 `test`/`e2e`/`empty-pr-guard`/`repo-guards`，`strict: true`） | 58/428（14%），除非同时把合并权收到平台——agent 和人都只能通过采纳卡合，个人 PAT 没有 write | 收权这一步要求 CI 先变快，否则每次合并都得等 22 分钟 | 这是**对被托管的别人家仓库**该有的能力，是对的长期形状（`accept-is-merge.md` 那句「No path depends on a member's personal token」今天只对 14% 成立），但拿它当自己这个仓库的救命稻草会先被排队打死 |
| **C. CI 侧的 merge queue** | 428/428 | GitHub 的 merge queue 同样要 Pro/Team；自建一个「排队 rebase 后跑一遍再合」的 workflow 可行，但它自己就成了一个要维护的发布系统，而且每个 PR 多一次完整 CI，三个 slot 顶不住 | 不推荐 |

**已定**：我们自己这个仓库走 **A**——门在 GitHub 的分支保护上，平台读它的结论、不自己算
（`agent-principles` 六）；**B 的 `required_checks` 是给被托管仓库的能力**（结论 50：用户开了保护就读 BLOCKED，
没开就按平台侧的项目规则判），不是这个仓库的救命稻草；**C 不做**。完整措辞在 §5 第 1 条。

**外加一条与 plan 无关、必须做的：main 上不跳过 `test`/`e2e`。**
代价是每次合并多占三个 slot，也就是把「CI 要快」从舒适问题变成硬约束——这正是第 3 节那套速度目标存在的理由。
另一个选项（开 `strict`，要求 PR 与 main 同步再合）在 428 次/两周的节奏下会让每个 PR 合并前都要 rebase 重跑，**不推荐，除非 CI 先变快**。

还有一条不花钱的：**`deploy/` 的改动今天完全不跑后端套件**。
200 个合并 PR 里 24 个改了 `deploy/` 而一个 `backend/` 文件都没动，
`test.yml` 和 `e2e.yml` 的 paths 过滤是 `backend/**`/`frontend/**`，所以这 24 个 PR 后端套件和 e2e 一条都没跑；
`deploy-docker.sh` 自己的 rollout/rollback 路径只有一步 `bash -n` 语法解析。
`deploy-scripts-test.yml` 的 header 已经把这个坑写下来了：
「2026-08-11 的 dev 事故就是这么上线的——六个绿的 CI job，没有一个碰过那个文件」。

### 4.4 「deploy 完网站就容易挂」

**这句是真的，有数。** `deploy-dev.yml` 在 main 上最近 100 次运行 = **77 成功、16 失败、5 取消、2 次还在跑**
（2026-09-19 实测），而这 100 次只覆盖约 **37 小时**——平均每 2.3 小时一次部署失败。
抽样 11 次失败，机制分三类：build 没出镜像（4）、从 box 拉 ghcr 的 DNS/TLS 超时（2）、
**新 backend 起来了但不答 `/healthz`（5）**。最后这一类是部署自己造成的。

最扎眼的一条对照：`CLAUDE.md` 说「把 dev 的共享部署当生产对待，哪怕它叫 dev」，
而 `deploy.yml`（生产 etrip）有三个 `wait-on-check-action` 加一个人工审批，
`deploy-dev.yml`（大家天天用的那个）两样都没有，build 成功即自动。**有门的那个没人用，天天用的那个没门。**

**五件事：**

**(a) 部署后的 smoke 走 DB + 执行器真路径。**
今天四层健康检查没有一层碰数据库：
`deploy-docker.sh:597` 的 `/healthz`（`health.py:26` 只看 `FAILED_ROUTE_MODULES`）、
容器 healthcheck（`docker-compose.base.yml:140`，同一个 `/healthz`）、
`check-app-tier.sh`（只看容器 image tag / state）、
外部探针 `probe-okcheese.sh`（`/` 200、http→https 跳转、OAuth provider 列表——`users.py:3599` 是**纯配置读**、证书天数）。
而每一次真实事故的症状都是「DB 碰不到，其它全绿」：
`project-cheese-dev-outage-transcript-lock-pool-exhaustion-20260918.md` 原话是
「每个 DB-touching 请求 30 s 后 500……而 `/healthz`、前端和所有容器看起来都健康，所以外部探针全程说『up』」。

三步，**次序不能反，因为第 1 步单独做会咬人**：

0. **先做下面 (c) 的「慢启动 vs 不健康」区分，再动第 1 步。**
   `/readyz`（`health.py:111`）走的是 `detailed_health_check` → `_check_database`，
   而 `_check_database`（`health.py:58-62`）用的是 `AsyncSessionLocal()`——**app 的同一个池**；
   部署脚本那边是 `deploy-docker.sh:597` 的 `curl -fsS -m 3`，超时 3 秒、没有重试语义上的宽容。
   部署自己触发的重连风暴期间，真容器要 3 分多钟才起（(c) 里的实测），
   这段时间池是满的，`/readyz` 会 503。
   **所以单把判据换成 `/readyz`，今天那 5 次「停在半部署态」会变成每次风暴都自动回滚。**
   必须先让 `BACKEND_START_TIMEOUT` 到点时去读 503 body 里的 `checks` 分辨「在慢慢起」和「死了」，
   第 1 步才安全。它不是「一行的事」。
1. **把部署的健康判据从 `/healthz` 换成 `/readyz`**——它就在同一个文件 `health.py:111`，
   `_REQUIRED_CHECKS = ("database", "redis")` 已经定义好了。
   做完之后 09-18 那种「池耗尽但 healthz 200」在部署时就被拦住。
2. **部署后跑一次真 turn。** `device-smoke.yml` 已经存在，跑一个带 marker 的 prompt 并检查卡片，
   断言的三件事正是自建依赖的那三件（#94 设备 clone/push、#95 agent 自己填验收卡、#96 chat WS 转帧）。
   它今天只有 `workflow_dispatch`（header 写着理由：「this drives a live agent turn (spends model budget), so it is not on a schedule」），
   **最后一次运行是 2026-07-26，八周前**。接到 `deploy-dev.yml` 成功之后自动跑，失败报警。
   **这一条就是「从环境构建到产出，每一个请求调用」那句抱怨的验收。**
3. **外部探活再加一个打穿 DB 的只读端点。** 今天 `okcheese-uptime.yml` 近三天只跑了 20 次（应该 ~144 次，GitHub 调度在丢），
   20/20 success——而这三天里站点 502 过至少两次。

**(b) owner 释放自动化。**
issue **#1249 仍 OPEN**，这是「deploy 完就挂」里最纯粹的一条：**部署本身设计成只部署一半。**
`deploy-docker.sh` 故意让 device-connection owner 继续跑旧镜像，同一次部署又对共享库跑了 `alembic upgrade head`。
#1240 在 09-19 08:05Z 合入，它的迁移删掉 `topics.agent_instance_id`，owner 还跑着 `b90d5f0`，
那个镜像的 `Topic` 模型仍然 select 这一列：
```
ProgrammingError: <asyncpg.exceptions.UndefinedColumnError>:
column topics.agent_instance_id does not exist
```
**三小时 1218 次，走那条路的房间的 turn 直接不跑；停下来只是因为有人为了另一个无关修复去手工 dispatch 了 owner release。**
这是结构性的：任何一次删/改 owner 模型里出现过的列的迁移都会这样，窗口长度 = 有人注意到需要多久。

两步：
1. owner 在 `/internal/device-connection/snapshot` 里报出它构建时的 alembic revision，
   部署脚本和健康检查拿它和库里的 `alembic_version` 比——**让「owner 落后两个迁移」变成一句话，而不是三小时后第一个失败的请求**。
2. revision 真的动了就由部署自己 dispatch owner release。
   注意闸门「没有在途调用」**永不成立**：`call_executor` 在 `asyncio.shield` 下每秒重新 poll 一次，
   只要有一间房里有 agent，`_active_rpc_calls` 就下不来（#1126：release 35173288891，240 次尝试 × 0.25s = 65 秒，
   `device connection owner remained busy; release stopped`，owner 停在 9 月 15 日的镜像上两天，
   **而它要发布的正是 #1114 那个丢 header 的修复**）。所以 `interrupt` 要成为默认，不是一个输入。
   再配上缝 C-2 的版本错配矩阵：老 owner 遇到新方法必须答一个可区分的「不认识它」，而不是 403 让调用方丢掉整份目录。

**(c) 失败的部署不许停在要人手工修的中间态。**
`deploy-docker.sh:626` 那句「repair the backend, then point api-front back by hand」是整个设计里唯一一处等人，
而那 5 次失败就停在这里：临时 `cheese-backend-next` 还在服务、api-front 指着它。
脚本在 `:770-800` 已经有完整的回滚逻辑，这条路径不走它。**真容器起不来时自动回滚。**
同时把「慢启动」和「不健康」分开：那 5 次里真容器不是起不来，是 3 分多钟才起来——
因为部署自己触发的重连风暴（`project-cheese-dev-reconnect-storm-sweep-saturates-pool-20260918.md`：
临时容器 27 秒答健康是因为设备还没开始重连，真容器 3 分钟是因为风暴正在进行）。
`BACKEND_START_TIMEOUT` 到了之后先问一句「它是在慢慢起还是死了」——`/readyz` 的 503 body 里已经带着 `checks`。

**(d) 连接池的容量单位是并发工具调用这一事实，怎么处理。**
这是个必须先说清楚再决定的事实：每一次 `/execution` tool call 按设计持有共享 advisory lock
和它那条池连接。持有是刻意的——`backend/app/api/routes/execution.py:81` 的注释写着
「Hold admission through the response, including background task creation.」，
下一行就是 `await execution.lock_release(db, resource_id, shared=True)`
（锁本身在 `backend/app/domain/agent/execution.py:9-14`），为的是房间清理不能在 tool call 飞行中跑。
**最长 660 秒**是另外两处各自写死的超时：`device_hub.py:590` 的 `timeout: float = 660`
和 `executor_transport.py:141` 的 `factory(hostname, address.port, timeout=660)`（`audit-toolcall.md:81-85`）。
所以**池的真实容量单位是并发飞行中的 tool call，不是打开的页面或会话**。
观察到的常态占用是 ~2/35，只有重启后的重连风暴会顶满
（`project-cheese-dev-box-saturation-queuepool-20260918.md`：`cheese-device-connection-1` 15 条池连接全满，
7 条 `idle in transaction` 停在 `pg_advisory_xact_lock_shared` 上 20–30 秒）。

**今天这个事实在测试里完全不存在**：`conftest.py:83` 设 `CHEESEX_TEST_NULLPOOL=1`，
`db.py:46` 据此换成 `NullPool`——生产是 `QueuePool(pool_size, max_overflow)`，**压垮生产的那个对象在测试里没有**。
这条无论选哪个方案都得先修：integration 层必须用生产的池。
方案 A/B 见第 5 节第 3 条。

**(e) 部署门：只部署「同一提交的测试通过了」的那个提交。** [已定] 结论 59

今天 `deploy-dev.yml` 是 **build 成功即自动**——build 出了镜像就发，
而「这个提交的 `test`/`e2e` 绿没绿」根本不在它的前置条件里；
main 上 `test`/`e2e` 又被 `scope` job 跳过（§4.3 第 3 条），所以**这个提交在 main 上从来没有被测过**。
两件事合起来，dev 上跑的那份代码没有任何一层断言过它是绿的。

判据一句：**部署的前置条件是「**这一个 commit sha** 的必过检查全部 success」**，不是「build 成功」、
不是「它的 PR 当时绿过」（PR 落后 main 是常态，§4.3 第 3 条）、也不是某个祖先提交绿过。
做法是 `deploy-dev.yml` 在发之前按 sha 查一次 check runs（`deploy.yml` 已有的 `wait-on-check-action` 就是这个形状，
只是生产用它、天天用的 dev 没有）；查不到结论就**等**，红就**不发**。
这条与「main 上不跳过 `test`/`e2e`」是同一件事的两半：不跑就永远查不到结论，
所以 §4.3 那条必须先落地，这条才有东西可查。

在方案定下来之前，**#1103 那条不变量留着并推广**：
`test_db_pool_fits_the_server`（滚动发布期间三个池的总和塞得进一个默认 Postgres 的 `max_connections=100`）——
它是全仓唯一能写下这个算术的地方，因为「Nothing in the app can read `max_connections`」。
再加一条同形的：**并发 tool call 数 × 每次占用 1 条连接 ≤ owner 池上限**，
这条不变量今天不存在，而它就是这个事实的可执行形式。

---

## 5. 待你拍板

只列真的分岔——我说不出你会选哪个，或者它要花你的钱。

**1. 合并门。** [已定 2026-09-19，不再是分岔，留在这里是因为它是第 4.3 节那三个数的出口]
两层，各管各的，**都不禁止 agent 自行合并**（`agent-principles` 二、七）：

- **我们自己这个仓库：门在 GitHub 的分支保护上**，平台读它的结论、不自己算（`agent-principles` 六）。
  这是唯一一个同时覆盖两条路径的地方——采纳卡和终端里的 `gh pr merge`（428 里 370 次）对它一视同仁，
  而平台侧的判定只管得到走采纳流程的那 14%。
- **平台侧的项目规则（`required_checks`）是给被托管仓库的** [已定] 结论 50：
  用户开了分支保护，平台读 BLOCKED；没开，就按平台自己项目设置里的那份规则判。
  它是我们该有的产品能力，不是这个仓库自己的门。
- **红的内容照旧送到写代码的芝士面前**（已有的 `pr_signals.py` 回流），
  合并判定全仓只有一处（`merge_state.py`，I24）。

**要你点的只剩钱和范围那一下**：组织 `SageSeekerSociety` 是免费计划（36 座位 / 30 成员），
私有仓库开分支保护要 **Team**（$4/人/月，约 $144/月、一年约 $1,700）；
转 public 不花钱，但要先轮换贴过的 PAT、扫一遍历史与 issue 正文，且等于把产品开源。

**2. main 上不跳过重活 [已定 2026-09-19，按倾向]，和加 runner，是同一个决定。**
不跳过 = 每次合并多占三个 slot；今天 PR 上的 `test` 墙钟中位 22 分钟 p90 94 分钟，差的全是 `cheese-ci` 池的排队（三台机器，`audit-gates.md:179` 按三个 slot 算）。
先加 runner 再开，还是先开着忍一段。
加 runner 花钱（几台机器），不加就得接受 PR 队列更长。
**我倾向先加 runner**——因为第 3 节的 8 worker 目标同样卡在机器上
（每 worker 两个数据库，翻倍就是 Postgres 连接翻倍，今天这台盒子扛不住），两件事一次解决。

**3. 连接池的容量单位怎么处理。** [已定 2026-09-19，按倾向]

| | A：tool call 不占池连接 | B：给 `/execution` 一个自己的池 |
|---|---|---|
| 做法 | 把 `/execution` 的共享 advisory lock 换成行租约（#1237 已经在清理路径上做过这个改造，迁移 `c7d2e4f60a11`，把饱和从 ~5 分钟降到 ~2 分钟、CPU 410%→38%），tool call 期间不持有任何池连接 | owner 本来就是独立部署单元，给它一个按并发 tool call 数算的池，业务池不受影响 |
| 代价 | 要重新论证「房间清理不能在 tool call 飞行中跑」这条约束在行租约下仍然成立；改的是执行路径上最热的一段 | 两个池加起来仍要塞进 `max_connections=100`（#1103 那条不变量要改）；**它只是把上限挪了个地方**，没有去掉「一次 tool call 占一条 DB 连接 660 秒」这件事本身 |

**我倾向 A**，但它是执行路径上的改动，应该等缝 C-2 的 acceptance 稳定绿了之后再动——
否则改坏了只能在 dev 上被发现，正是我们要摆脱的那条路。

**4. `acceptance` 什么时候挂上必过门。** [已定 2026-09-19，按倾向]
它是唯一端到端跑工具调用的东西，今天中位 19.8 分钟、最长 192 分钟、
**main 上最近 20 次运行只有 4 次 success**（failure 7、cancelled 8、1 次在跑；含当前基线 `49a7c8177` 是 failure）。
按「没绿就是没绿」，可用率 20%——这个数比「40% 红」难看一倍，也直接改变「什么时候挂上必过门」的答案：
20% 可用率的 check 挂上去等于停工。
现在就挂（每个 PR 多 20 分钟，且会被基础设施红卡住），还是先要求「main 连续 20 次 push 全绿」再挂。
**我倾向后者**，并且在那之前它既不当门也不当噪音——**红了必须有人看**，
因为红的内容常常是真 bug（#1242 那个 1/6 概率的空输出就是这么发现的，
然后花了 #1245/#1252/#1258/#1260 四个 PR 才收干净）。
这条要你定，是因为它决定这段时间里「acceptance 红了能不能合」这句话由谁说了算。

**5. 测试里用不用生产的连接池。** [已定 2026-09-19，按倾向]
`CHEESEX_TEST_NULLPOOL` 是为了解决「一个进程跑很多 event loop，asyncpg 连接不能跨 loop」这个问题而设的
（`db.py:40-45` 的注释写得很清楚）。去掉它要先把测试架构改成「一个 worker 一个 loop」，
代价是所有用 blocking portal 的 fixture 要重写——这是 1274 个 client 用例的地基。
不改，族 5（13 个 PR）和 #1103 那条就永远只能在 dev 上发现。
**我倾向改**，它是第 3 节 integration 层重建的一部分，不单独立项；
但它是一次伤筋动骨的改动，做之前你应该知道它在改什么。
