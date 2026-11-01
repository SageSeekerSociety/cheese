"""架构守卫：领域包和 HTTP 路由都不许直接 import 别的领域的 repository 模块。

正路是 service → 对方的 service。直接摸对方的 repository 会把「数据访问」这层的
约束（谁能读、读完要不要补别的字段、要不要发通知）绕过去。本领域内部随便 import，
不受限。

## 管辖范围

三处：``app/domain``、``app/api/routes``，以及点名的几个路由共享依赖模块
（``_SCANNED_MODULES``）。后两处都不属于任何一个领域，所以它们 import 的每一条
repository 都是跨域的，存量全在下面的白名单里。守卫只看 ``app/domain`` 的时候这些边
CI 一条也看不见，而它们恰恰是这层约束最容易被绕过去的地方——HTTP 处理函数手上就有
session，拿对方的 repository 直接查一行比找对方的 service 省事。

共享依赖模块本来也在这道守卫的账上，只是 2026-10 那轮重构把路由里共用的依赖从
``routes/`` 搬进 ``app/api/*_deps.py`` 时，新家不在任何一棵被扫的树里，账上删掉的
豁免就没有人再看着了：往那个文件里新加一条跨域 repository import，全绿。所以这里
把它们点名扫上——**搬出路由不等于掉出管辖**。

**这道守卫治的是分层纪律，不是依赖环——别指望它把环拆没。** 按正路改完，包和包之间
那条边还在，只是从 repository 层挪到了 services 层；包级依赖图一条边都不会少。真要解
环得消除依赖本身（倒置控制、把数据传进来、把逻辑搬家），那是另一件事，成本高得多。

实测（2026-08-11，42 个领域包 / 224 条跨包 import）：``models`` 77 条、
``repositories`` 77 条、``services`` 16 条且无环。把跨域 repository 边整个**删掉**
（不是改道），双向依赖对 20 → 9、强连通分量 26 个包 → 18 个；把 ``models`` 边也删掉
才降到两个 3 包的小环。也就是说 ORM 关系那半边贡献的环一样多，而那半边是设计上难免
的。所以：**不要拿本守卫的通过率当解环进度。**

## 白名单

存量违规全部写死在下面的 ``_EXEMPT`` 里，**故意不放配置文件**：加一条豁免必须改
代码、走 review，而不是偷偷加一行配置。

白名单是**棘轮**——多一条会红（新违规），少一条也会红（还完债忘了删）。所以还完
一对依赖，必须把对应的行删掉，红了就是提醒你删。

粒度是 (发起方模块, 被摸的 repository 模块)，不到类名。理由：同一个文件从同一个
领域的 repository 里多拿一个类，架构上还是同一处违规，不值得再走一次 review。
"""

import ast
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]

#: C2 那一段所在的配置文件。下面两条自检拿它和 `_SCANNED_MODULES` 对账。
_IMPORTLINTER = BACKEND_ROOT / ".importlinter"

#: 扫哪几棵树，相对 backend 根。见上面「管辖范围」。
_SCANNED = ("app/domain", "app/api/routes")

#: 另外点名扫描的文件：路由的共享依赖模块。它们和路由一样不属于任何领域，摸谁的
#: repository 都算跨域。**点名而不是扫整个 ``app/api``**：那棵树里还有别的模块，
#: 历史导入与本类纪律无关，一起扫进来只会多出一批要单独解释的行。往里面新增共享
#: 依赖模块时，把路径写在这里——同时要在 ``.importlinter`` 的 C2 ``source_modules``
#: 里加同一项。两处由 ``test_c2_sources_and_the_guard_scan_the_same_modules`` 逐条
#: 对账，也由 ``test_every_named_source_has_a_file`` 确认文件都在。
_SCANNED_MODULES = (
    "app/api/admin_deps.py",
    "app/api/users_deps.py",
    "app/api/space_deps.py",
    "app/api/space_payloads.py",
    "app/api/task_deps.py",
    "app/api/topics_deps.py",
    # 2026-10 的第二轮：拆路由之间的边。前三个是新的共享依赖模块（`awaiting_deps`
    # 装 `waiting_items`，`teams_deps` 装 teams 的四个共享名，`legal_deps` 装
    # `client_context`），`topics_file_sources` 是从 `routes/` 挪到 `app/api/` 的
    # 整个文件，`ws_pipe` 是新的（隧道的两个泵）。判据就一条：这个模块是不是
    # 2026-10 这轮从 `routes/` 里出来的——是就得点名，否则搬家的那几条账没人看着。
    # `app/api/doc_access.py` 和 `app/api/room_task_rows.py` 不在这里：它们在这轮之前
    # 就生在 `app/api/` 下，是那棵树的普通成员，不是从路由里搬出来的。
    "app/api/awaiting_deps.py",
    "app/api/legal_deps.py",
    "app/api/teams_deps.py",
    "app/api/topics_file_sources.py",
    "app/api/ws_pipe.py",
    # 2026-10 的第三轮（最后一轮）：把经过 `app.api` 助手绕出来的名字也挪走。
    # 前四个是新的行构造模块——`topic_member_rows`、`topic_pin_rows`、
    # `review_comment_rows`、`project_skill_rows`，`room_snapshot` 原先是回它们各自
    # 的路由里去拿的；`preview_relay` 装预览隧道浏览端的那半边和两端共用的 hub。
    # 判据不变：从 `routes/` 里出来的就点名。`preview_host`、`preview_runtime`、
    # `room_snapshot` 这些不点，因为它们在这轮之前就生在 `app/api/` 下。
    "app/api/preview_relay.py",
    "app/api/project_skill_rows.py",
    "app/api/review_comment_rows.py",
    "app/api/topic_member_rows.py",
    "app/api/topic_pin_rows.py",
)

#: 路由那棵树的「领域」。它不是领域包，这个值只用来让路由发出的每一条 repository
#: import 都算跨域——它不等于任何一个领域包的名字。
_ROUTES = "<api.routes>"

#: 共享依赖模块的「领域」，同理。两者分开取值，是为了让这两处各自的账在
#: ``_EXEMPT`` 里一眼分得清。
_SHARED_API = "<api.shared>"

#: ``app/api/admin_deps`` 这样的模块名集合，供 ``_domain_of`` 查。
_SHARED_API_MODULES = frozenset(
    path[: -len(".py")].replace("/", ".") for path in _SCANNED_MODULES
)

# ---------------------------------------------------------------------------
# 存量豁免。格式：(发起方模块, 被 import 的 repository 模块)
#
# 还债进度见 docs/topics/领域包解环.md。删行的正确姿势：把 service 改成调对方的
# service，然后删掉这里对应的行，跑本测试确认不红。
# ---------------------------------------------------------------------------
_EXEMPT: frozenset[tuple[str, str]] = frozenset(
    {
        # --- agent_credential --- (plain get + the epoch lives in
        # project.settings; no project/topic *service* exposes those reads
        # without dragging their full DI graph into credential minting)
        ("app.domain.agent_credential.services", "app.domain.project.repositories"),
        # --- agent --- (announce writes the room line itself: 平台说的每一句
        # 都从这一个函数出去，走 block 领域的 service 就要把整条 DI 图拖进来)
        ("app.domain.agent.announce", "app.domain.block.repositories"),
        # chat_admission / chat_lifecycle / chat_places 是从 agent.chat 里拆出来的
        # 那三块（准入与并发、停与「还在不在」、会场与 session 指针）。原先记在
        # `agent.chat` 名下的一对 repository 跟着代码搬了家：读 block（准入时要看
        # 这个座位说了什么、停的时候要认这条活真干出了东西；block 领域没有 service
        # 层，`platform_stats.pipeline` 那条注释讲的是同一件事）落在
        # chat_admission 与 chat_lifecycle；读项目表（会场里那个座位归谁，决定记
        # 在谁名下）落在 chat_places。拆模块没有新增跨包的边，只是发起方从 chat.py
        # 换成了这三个模块，所以按同一笔债入账。
        ("app.domain.agent.chat_admission", "app.domain.block.repositories"),
        ("app.domain.agent.chat_lifecycle", "app.domain.block.repositories"),
        ("app.domain.agent.chat_places", "app.domain.project.repositories"),
        # Mention resolution uses the topic repository to canonicalize references.
        ("app.domain.agent.mentions", "app.domain.topic.repositories"),
        # agent.queries 是从 agent.chat 里拆出来的那一块（不碰实例状态的问答：
        # 这一轮谁答、项目 key 带多少额度、这条记忆改动说进哪间房）。它摸的三个
        # repository 正是原先 chat.py 那一组里跟着它走的：读 block（block 领域
        # 没有 service 层，`platform_stats.pipeline` 那条注释讲的是同一件事）、
        # 读项目表、读额度（网关额度那一处本来就是 READ path，见
        # `queries._gateway_budget_target` 的 docstring）。拆模块没有新增跨包的
        # 边，只是发起方从 chat.py 换成了 queries.py，所以按同一笔债入账。
        ("app.domain.agent.queries", "app.domain.block.repositories"),
        ("app.domain.agent.queries", "app.domain.project.repositories"),
        # agent.hook_stream 是从 agent.chat 里拆出来的那一块（屏幕订阅送进来的
        # 那一条线：读一条事件、落块、重试与整理的提示、关这一轮的书）。它摸的
        # repository 只有 block 一个，正是原先 chat.py 那一组里跟着它走的：block
        # 领域没有 service 层，`platform_stats.pipeline` 那条注释讲的是同一件事。
        # 拆模块没有新增跨包的边，只是发起方从 chat.py 换成了 hook_stream.py，
        # 所以按同一笔债入账。
        # agent.gateway_usage 是从 agent.chat 里拆出来的那一块（这一轮的模型与
        # 它的用量账：准入前解析模型、环境与项目的网关 key，轮次结束后把网关的
        # 用量落成行、扣掉额度）。它摸的三个 repository 正是原先 chat.py 那一组
        # 里跟着它走的：读项目表、读话题表、读额度与用量。拆模块没有新增跨包的
        # 边，只是发起方从 chat.py 换成了 gateway_usage.py，所以按同一笔债入账。
        ("app.domain.agent.gateway_usage", "app.domain.project.repositories"),
        ("app.domain.agent.gateway_usage", "app.domain.topic.repositories"),
        # agent.work_policy 是从 agent.chat 里拆出来的那一块（准入前的那组事
        # 实：项目、并发上限、额度、这一轮的会话落不落在中心机上）。它摸的三个
        # repository 正是原先 chat.py 里 `work_policy` 用的那三个：读话题表、读
        # 项目表、读额度。拆模块没有新增跨包的边，只是发起方从 chat.py 换成了
        # work_policy.py，所以按同一笔债入账。
        ("app.domain.agent.work_policy", "app.domain.project.repositories"),
        # agent.memory_ledger 是从 agent.chat 里拆出来的那一块（这一间房的记忆
        # 账：每轮对一次账、以及平台自己过一遍的整理）。它摸的三个 repository
        # 正是原先 chat.py 那一组里跟着它走的：读话题表（这一间房是谁的）、读
        # 项目表（整理的判据与项目的主人）、写 block（整理的结论说进总览那一行；
        # block 领域没有 service 层，`platform_stats.pipeline` 那条注释讲的是同
        # 一件事）。拆模块没有新增跨包的边，只是发起方从 chat.py 换成了
        # memory_ledger.py，所以按同一笔债入账。
        ("app.domain.agent.memory_ledger", "app.domain.project.repositories"),
        ("app.domain.agent.memory_ledger", "app.domain.topic.repositories"),
        # agent.document.question 是文档里的芝士从 api/ 挪进来的那一块（每个问题
        # 带上的上下文）。它读房间最近的消息，读的是 block：block 领域没有
        # service 层，`platform_stats.pipeline` 那条注释讲的是同一件事。这条边在
        # api/doc_agent.py 时就有，挪进领域层才进了这本账。
        ("app.domain.agent.document.question", "app.domain.block.repositories"),
        # --- answers / comments / discussion / groups ---
        ("app.domain.answers.services", "app.domain.user.repositories"),
        ("app.domain.answers.services", "app.domain.questions.repositories"),
        ("app.domain.comments.services", "app.domain.user.repositories"),
        ("app.domain.groups.services", "app.domain.user.repositories"),
        # --- dashboard（读模型，横跨 7 个领域聚合，单独还） ---
        ("app.domain.dashboard.services", "app.domain.notification.repositories"),
        # --- platform_stats（看板的读模型聚合，和 dashboard 同构，单独还）。
        #     block 领域**没有** service 层（只有 repositories / models），
        #     pipeline 那一块问的是「哪几条活/房间停在未回答的提问上」——
        #     正是 block.repositories.awaiting_an_answer 的那个读，
        #     走不了「调对方的 service」。等 block 长出 service 就把这行删掉。
        ("app.domain.platform_stats.pipeline", "app.domain.block.repositories"),
        ("app.domain.dashboard.services", "app.domain.project.repositories"),
        ("app.domain.dashboard.services", "app.domain.space.repositories"),
        ("app.domain.dashboard.services", "app.domain.topic.repositories"),
        ("app.domain.dashboard.services", "app.domain.user.repositories"),
        # --- identity / knowledge / machine / membership / oauth ---
        ("app.domain.identity.services", "app.domain.user.repositories"),
        ("app.domain.knowledge.services", "app.domain.team.repositories"),
        ("app.domain.knowledge.services", "app.domain.user.repositories"),
        ("app.domain.membership.services", "app.domain.project.repositories"),
        # --- notification（项目收件箱：写入前核项目在不在。拍板时把决定发回房间
        #     那一条已经收进 `notification/ports.py` 的口子，落话的是
        #     `block/adapters.py`，本模块不再读 room 的表） ---
        ("app.domain.notification.services", "app.domain.project.repositories"),
        # oauth.services 拆成了包（`services/__init__.py` 门面 + 三个子模块）。两条
        # 按 handle 取身份的模块级函数留在门面里，函数内那句
        # `from app.domain.user.repositories import UserRepository` 也跟着留在那儿，
        # 于是发起方从 `app.domain.oauth.services` 换成包的 `__init__` —— 守卫按文件
        # 路径取名，包的入口就是 `…services.__init__`。同一份债换了导入方，不是新债，
        # 和 `review.services` 拆包时 `_shared` 接手那三条是同一回事。
        ("app.domain.oauth.services.__init__", "app.domain.user.repositories"),
        # --- pin --- 置顶要确认那一块在这个频道主线上，再在主线写一行「置顶了…」：
        # 读一块、写一块，block 领域没有 service 层，和 announce 同一个理由入账。
        ("app.domain.pin.services", "app.domain.block.repositories"),
        # --- project ---
        ("app.domain.project.services", "app.domain.topic.repositories"),
        ("app.domain.project.services", "app.domain.user.repositories"),
        # --- routine ---
        # 一次执行的那条消息变了（开始、跑完）要整行推给开着频道的人，整行里
        # 有它的表情回应；block 领域没有 service 层，和 pin 同一个理由入账。
        # `routine.service` 拆成了包，这条边现在由 `service/sweep.py` 发起（整行
        # 推送就在 `publish_run_messages`）；被摸的 module 一个没变，同一笔债
        # 挪了发起方。拆分前这条在 `app.domain.routine.service` 名下。
        ("app.domain.routine.service.sweep", "app.domain.block.repositories"),
        # --- questions ---
        ("app.domain.questions.services", "app.domain.user.repositories"),
        ("app.domain.questions.services", "app.domain.answers.repositories"),
        # --- review ---
        # review.archive / review.gate_sweep 是本分支挂起期间从 main 进来的
        # （#286 闸门孤儿清扫等），不是本轮新欠的债，按存量入账。
        ("app.domain.review.archive", "app.domain.block.repositories"),
        ("app.domain.review.gate_sweep", "app.domain.block.repositories"),
        ("app.domain.review.gate_sweep", "app.domain.topic.repositories"),
        # review.services 拆成了包。`_shared.py` 是包内唯一直接摸这三个
        # repository 的地方（`AcceptService.__init__` 建会话、`_refresh_stale_card`
        # 刷新时另开一条读、`void` 写一块），子模块从它这里取名字。拆分前这三条边
        # 在 `review.services` 名下，拆包后发起方换成 `review.services._shared`——
        # 被摸的 module 一个没变，同一笔债挪了发起方。
        ("app.domain.review.services._shared", "app.domain.block.repositories"),
        ("app.domain.review.services._shared", "app.domain.project.repositories"),
        ("app.domain.review.services._shared", "app.domain.topic.repositories"),
        # --- room_task ---
        # 任务是一条从房间里长出来的活，它落户时房间里要说的那两句（开一条支线的
        # 标题、这里开了一条活）由本包写。`block` 没有 service 层，只有 repository，
        # 和上面 thread 那条同形。2026-10-16 这批函数从 `topic.services` 搬来时
        # 发起方换了名字，被摸的 module 一个没多 —— 同一笔债挪了发起方。挪过来
        # 的这一半要按 id 取一行、再写一句，而 `block` 没有 service 层可让，所以
        # 入账；留在 `topic.services` 名下的那一半同一天还掉了，见下面 topic 段。
        ("app.domain.room_task.dispatch", "app.domain.block.repositories"),
        # --- space ---
        ("app.domain.space.analytics_service", "app.domain.task.repositories"),
        ("app.domain.space.analytics_service", "app.domain.user.repositories"),
        ("app.domain.space.analytics_view_service", "app.domain.user.repositories"),
        # 学习那一格是读模型：课程 → 它名下的赛题 → 从赛题开的项目。这一跳走的是
        # project 领域 `list_ids_for_space_tasks`（它自己的注释：「One query rather
        # than a walk」），而 project 领域没有 service 暴露它 —— `ProjectService` 只
        # 按 id /（课, 小队）取项目。搬不出来，和 dashboard 那条「横跨多领域聚合的读
        # 模型」同形，按存量入账。
        ("app.domain.space.learning_service", "app.domain.project.repositories"),
        ("app.domain.space.services", "app.domain.task.repositories"),
        # --- task ---
        ("app.domain.task.services", "app.domain.space.repositories"),
        ("app.domain.task.services", "app.domain.team.repositories"),
        ("app.domain.task.services", "app.domain.user.repositories"),
        ("app.domain.task.visibility_service", "app.domain.space.repositories"),
        ("app.domain.task.visibility_service", "app.domain.user.repositories"),
        # --- thread --- 支线摘要要每条支线最后说的那一句：一次查询读一批块，
        # block 领域没有 service 层。
        ("app.domain.thread.reads", "app.domain.block.repositories"),
        # --- topic / topic_membership ---
        # `app.domain.topic.services → app.domain.block.repositories` 是 2026-10-16
        # 还掉的：本包要写一句平台的话走 `topic/ports.py` 的 `PlatformLine`，不再
        # 摸 `block` 的 repository。
        # 2026-10-16：`topic_membership/services.py` 变成了包（门面在 `__init__.py`，
        # 名册与席位那一半在 `housekeeping.py`）。这两条边本来就在，发起方是门面的
        # 入口文件；守卫按文件路径取名，包的入口就是 `…services.__init__`。跟搬走的
        # 两半没关系——两条读的 repository 都还在门面里（`__init__` 建它们）。
        (
            "app.domain.topic_membership.services.__init__",
            "app.domain.topic.repositories",
        ),
        # --- webhook / workspace ---
        ("app.domain.usage.subscription_ingest", "app.domain.project.repositories"),
        # --- app/api/routes ---
        # 路由一个领域都不属于，所以它碰的每一个 repository 都是别人家的。下面
        # 这些是守卫扩到这棵树那一刻就在的存量（当时 98 条，其余都已还），和上面的
        # 领域内债一样入账：还法是路由改调对方的 service，然后把这里对应的行删掉。
        ("app.api.routes.agent_credential", "app.domain.project.repositories"),
        ("app.api.routes.answers", "app.domain.answers.repositories"),
        ("app.api.routes.answers", "app.domain.questions.repositories"),
        ("app.api.routes.answers", "app.domain.user.repositories"),
        ("app.api.routes.avatars", "app.domain.avatars.repositories"),
        # `waiting_items` 的发起方换了名字：那个函数连同它的四个码搬去了
        # `app/api/awaiting_deps.py`（推送也读它，而路由模块之间不互相 import）。
        # 被摸的 repository 一个没多，所以按同一笔债入账，只是换个发起方——记法同下面的
        # users_team / topics_compute。
        ("app.api.awaiting_deps", "app.domain.block.repositories"),
        ("app.api.awaiting_deps", "app.domain.project.repositories"),
        ("app.api.awaiting_deps", "app.domain.room_task.repositories"),
        ("app.api.awaiting_deps", "app.domain.topic.repositories"),
        ("app.api.routes.blocks", "app.domain.block.repositories"),
        ("app.api.routes.comments", "app.domain.comments.repositories"),
        ("app.api.routes.comments", "app.domain.questions.repositories"),
        ("app.api.routes.comments", "app.domain.user.repositories"),
        ("app.api.routes.connector.connections", "app.domain.device.repository"),
        ("app.api.routes.connector.devices", "app.domain.device.sql_repository"),
        ("app.api.routes.connector.connections", "app.domain.team.repositories"),
        ("app.api.routes.dashboard", "app.domain.project.repositories"),
        ("app.api.routes.dashboard", "app.domain.usage.repositories"),
        ("app.api.routes.feedback_proposals", "app.domain.block.repositories"),
        ("app.api.routes.github_install", "app.domain.oauth.repositories"),
        ("app.api.routes.github_install", "app.domain.project.repositories"),
        ("app.api.routes.github_install", "app.domain.user.repositories"),
        ("app.api.routes.groups", "app.domain.groups.repositories"),
        ("app.api.routes.groups", "app.domain.user.repositories"),
        ("app.api.routes.knowledge", "app.domain.knowledge.repositories"),
        ("app.api.routes.knowledge", "app.domain.team.repositories"),
        ("app.api.routes.knowledge", "app.domain.user.repositories"),
        ("app.api.routes.llm_proxy", "app.domain.project.repositories"),
        ("app.api.routes.materialbundles", "app.domain.materials.repositories"),
        ("app.api.routes.materials", "app.domain.materials.repositories"),
        ("app.api.routes.notifications_flat", "app.domain.team.repositories"),
        ("app.api.routes.notifications_flat", "app.domain.user.repositories"),
        ("app.api.routes.project_environment", "app.domain.user.repositories"),
        ("app.api.routes.questions", "app.domain.answers.repositories"),
        ("app.api.routes.questions", "app.domain.questions.repositories"),
        ("app.api.routes.questions", "app.domain.user.repositories"),
        ("app.api.routes.recruitment", "app.domain.user.repositories"),
        ("app.api.routes.spaces", "app.domain.user.repositories"),
        ("app.api.routes.tags", "app.domain.tag.repositories"),
        ("app.api.routes.tasks._common", "app.domain.space.repositories"),
        # Moved with the body of PATCH /tasks/{id} out of lifecycle, which still
        # reads them for its other routes: the same debt, a second importer.
        ("app.api.routes.tasks._writes", "app.domain.space.repositories"),
        ("app.api.routes.tasks.lifecycle", "app.domain.space.repositories"),
        ("app.api.routes.tasks.lifecycle", "app.domain.team.repositories"),
        ("app.api.routes.tasks.lifecycle", "app.domain.user.repositories"),
        ("app.api.routes.tasks.participation", "app.domain.space.repositories"),
        ("app.api.routes.tasks.participation", "app.domain.task.repositories"),
        ("app.api.routes.tasks.roster", "app.domain.task.repositories"),
        ("app.api.routes.tasks.roster", "app.domain.team.repositories"),
        ("app.api.routes.tasks.roster", "app.domain.user.repositories"),
        # teams 的两个共享名（`application_to_api_model` / `load_application_maps`）
        # 连同 `get_team_service` / `_user_payload` 搬进了 `app/api/teams_deps.py`
        # （team 那一路路由和个人的 `/users/me/team*` 都在用，而路由模块之间不互相
        # import）。同一笔债换发起方：teams_deps 接走 user.repositories 的一条，
        # `routes/teams.py` 自己留着 `UserRealNameRepository` 那条（它没搬）。
        ("app.api.routes.teams", "app.domain.user.repositories"),
        ("app.api.teams_deps", "app.domain.user.repositories"),
        ("app.api.routes.topics", "app.domain.block.repositories"),
        ("app.api.routes.topics", "app.domain.project.repositories"),
        ("app.api.routes.topics", "app.domain.usage.repositories"),
        # topics_compute 是从 routes/topics.py 里拆出来的那一块（房间的工作电脑：读
        # 和写那一条选择，外加一条会话对那台机器的租约）。它摸的 repository 只有
        # machine 一个，正是原先 topics.py 里跟着它走的那条边：`ProjectRepository`
        # 它从 `app/api/topics_deps.py` 拿（那是另一笔债，记在 topics_deps 名下），
        # 名字也跟着从那里的 import 里读；`ProjectMachineRepository` 只在
        # `set_topic_compute_profile` 里读一次，随它搬走。拆模块没有新增跨包的边，
        # 只是发起方从 topics.py 换成了 topics_compute.py，所以按同一笔债入账。
        # --- users 包 --- users.py 拆成 routes/users/ 之后（第 4 步重构），原先记在
        # users.py 名下的三条 repository 债按代码现在住的地方重新入账：账户读写、用户
        # 目录查询、用户偏好各自摸自己摸的那几条。拆模块没有新增跨包的边，也没还掉任何
        # 一条，只是发起方换了名字，所以按同一笔债入账（记法同下面的 users_team）。
        ("app.api.routes.users.directory", "app.domain.answers.repositories"),
        ("app.api.routes.users.directory", "app.domain.questions.repositories"),
        ("app.api.routes.users.directory", "app.domain.user.repositories"),
        ("app.api.routes.users.preferences", "app.domain.answers.repositories"),
        ("app.api.routes.users.preferences", "app.domain.questions.repositories"),
        ("app.api.routes.users.preferences", "app.domain.user.repositories"),
        ("app.api.routes.users.registration", "app.domain.user.repositories"),
        # --- users_team --- (#2143) the six /users/me/team* routes left
        # users.py; `leave_team` still builds a TeamRepository itself and the
        # `get_team_membership_service` dependency that moved with them builds
        # TeamMembershipApplicationRepository. The same debt as the users/package
        # lines above, re-attributed to the module the code now lives in.
        ("app.api.routes.users_team", "app.domain.team.repositories"),
        ("app.api.routes.webhooks", "app.domain.topic.repositories"),
        # --- app/api 共享依赖模块 ---
        # 2026-10 那轮重构把路由里共用的依赖从 routes/ 搬进 app/api/*_deps.py。
        # 下面六条是随之搬家的存量债：被摸的 repository 一个没多，只是发起方从某个
        # 路由模块换成了共享依赖模块——`space_deps` / `space_payloads` 接走的是
        # routes/spaces.py 的那一对（spaces.py 自己留着 user.repositories 那条，所以
        # user 这条按两个发起方各记一行），`task_deps` 接走的是
        # routes/tasks/_common.py 的 team 与 user 两条，外加 space.repositories 里
        # 跟着 `get_task_submission_service` 走的那两个名字（`_common.py` 自己留着
        # 同模块的另外三个名字，故 space 这条同样按两个发起方各记一行）。守卫的扫描
        # 面同时补上了这几个模块（`_SCANNED_MODULES`）：搬出路由不等于掉出管辖，
        # 否则账上删掉的三条就再没有人看着了。还法与路由那批相同——改成调对方的
        # service，然后删行。
        ("app.api.space_deps", "app.domain.task.repositories"),
        ("app.api.space_deps", "app.domain.user.repositories"),
        ("app.api.space_payloads", "app.domain.user.repositories"),
        ("app.api.task_deps", "app.domain.space.repositories"),
        ("app.api.task_deps", "app.domain.team.repositories"),
        ("app.api.task_deps", "app.domain.user.repositories"),
        # --- topics_deps --- 同一轮重构的下一片：routes/topics.py 曾把
        # `Block` / `BlockKind` / `BlockOut` / `BlockRepository` /
        # `ProjectRepository` / `UsageRepository` 转发给各 `topics_*`，转发本身正是
        # C4 不许的形状（一个路由成了另一个路由的依赖）。转发点挪到
        # `app/api/topics_deps.py` 之后，三个领域 repository 的发起方从
        # `app.api.routes.topics` 换成了 `app.api.topics_deps`——被摸的 repository
        # 一个没多，所以按同一笔债入账，只是换了个名字。`routes/topics.py` 自己
        # 仍在摸这三个（上面那三行），因为它的处理函数直接读它们。还法同上一批：
        # 改成调对方的 service，然后删行。
        ("app.api.topics_deps", "app.domain.block.repositories"),
        ("app.api.topics_deps", "app.domain.project.repositories"),
        ("app.api.topics_deps", "app.domain.usage.repositories"),
        # --- topic_member_rows --- 2026-10 的第三轮：`room_snapshot` 要的房间成员
        # 那一段原先住在 `routes/topic_members.py` 里，而 `room_snapshot` 不是路由、
        # 却要 import 它，正是 C4 不许的形状。`roster_rows` 连同它那两个 repository
        # （identity 的 `AgentBindingRepository`、user 的 `UserRepository`）搬到
        # `app/api/topic_member_rows.py`，发起方从 `routes/topic_members` 换成新模块。
        # 被摸的 repository 一个没多，所以按同一笔债入账，只是换了个名字。
        ("app.api.topic_member_rows", "app.domain.identity.repositories"),
        ("app.api.topic_member_rows", "app.domain.user.repositories"),
    }
)


def _is_repository_module(name: str) -> bool:
    """模块名的最后一段看起来是不是 repository 层。

    覆盖 ``repositories`` / ``repository`` / ``sql_repository`` /
    ``recruitment_repositories`` 这几种既有写法——不然改个名就能绕过去。
    """
    last = name.rsplit(".", 1)[-1]
    if last in ("repository", "repositories"):
        return True
    return last.endswith(("_repository", "_repositories"))


def _domain_of(module: str) -> str | None:
    """``app.domain.<pkg>.<...>`` → ``<pkg>``；路由 → ``_ROUTES``；共享依赖
    模块 → ``_SHARED_API``；其余 None。"""
    parts = module.split(".")
    if len(parts) >= 4 and parts[:3] == ["app", "api", "routes"]:
        return _ROUTES
    if module in _SHARED_API_MODULES:
        return _SHARED_API
    if len(parts) < 4 or parts[0] != "app" or parts[1] != "domain":
        return None
    return parts[2]


def _module_name(path: Path, tree: Path) -> str:
    return ".".join(path.relative_to(tree).with_suffix("").parts)


def _resolve(node: ast.ImportFrom, current: str) -> str | None:
    """把相对 import 还原成绝对模块路径。"""
    if node.level == 0:
        return node.module
    base = current.split(".")[: -node.level]
    return ".".join(base + (node.module.split(".") if node.module else []))


def _scan(tree: Path = BACKEND_ROOT) -> list[tuple[str, str, int]]:
    """返回全部跨领域 repository import：(发起方模块, 目标模块, 行号)。

    ``tree`` 指向 ``backend/``，也就是 ``app/`` 的上一级。默认扫真树；自检用它扫
    tmp 里的假树。
    """
    found: list[tuple[str, str, int]] = []
    paths: list[Path] = []
    for scanned in _SCANNED:
        root = tree / scanned
        if not root.is_dir():
            continue  # 假树只搭用得上的那半棵
        paths.extend(root.rglob("*.py"))
    for rel in _SCANNED_MODULES:
        named = tree / rel
        if named.is_file():  # 假树只搭用得上的那半棵
            paths.append(named)
    for py in sorted(paths):
        src_mod = _module_name(py, tree)
        src_dom = _domain_of(src_mod)
        if src_dom is None:
            continue  # domain/common.py 这类顶层文件，不属于任何领域包
        parsed = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(parsed):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mod = _resolve(node, src_mod)
                if not mod:
                    continue
                # 两种写法都要抓：
                #   from app.domain.x.repositories import Foo
                #   from app.domain.x import repositories
                targets = [mod] + [f"{mod}.{a.name}" for a in node.names]
            for t in targets:
                if not _is_repository_module(t):
                    continue
                dst_dom = _domain_of(t)
                if dst_dom is None or dst_dom == src_dom:
                    continue
                found.append((src_mod, t, node.lineno))
    return found


def _violations(
    tree: Path = BACKEND_ROOT, exempt: frozenset[tuple[str, str]] = _EXEMPT
) -> list[tuple[str, str]]:
    """白名单之外的违规。"""
    return sorted({(src, dst) for src, dst, _ in _scan(tree)} - exempt)


def _module_exists(tree: Path, module: str) -> bool:
    """``app.domain.x.y`` 在这棵树里有没有对应的文件（模块或包）。"""
    base = tree.joinpath(*module.split("."))
    return base.with_suffix(".py").exists() or (base / "__init__.py").exists()


def _stale_exemptions(
    tree: Path = BACKEND_ROOT, exempt: frozenset[tuple[str, str]] = _EXEMPT
) -> list[tuple[str, str]]:
    """白名单里已经没有对应违规的行。

    **发起方模块在这棵树里根本不存在的，不算陈行。** 棘轮这一半主张的是「债还完
    了，把行删掉」；文件都不在这个 checkout 里，这个主张就无从谈起——本仓的工作区
    经常落后于 main（见 AGENTS.md），照 CI 在更新的 main 上报的违规入账时，那条
    豁免在旧 base 上必然找不到对应文件。不加这层判断，棘轮就会把「你的 checkout
    落后」误报成「这笔债还完了」，而正确的动作恰恰相反。

    代价说清楚：一个领域**真被整个删掉**时，它的豁免会留在白名单里没人提醒。
    换来的是不会把落后的 checkout 误判成还清了债——后者会诱导人删掉仍然有效的
    豁免，那是会让守卫漏判的方向，比多留几行严重。
    """
    actual = {(src, dst) for src, dst, _ in _scan(tree)}
    return sorted(
        (src, dst) for src, dst in exempt - actual if _module_exists(tree, src)
    )


def test_scanned_trees_are_present_and_nonempty() -> None:
    """真树的两棵扫描目录都要在，且扫描结果各自包含已知模块。

    `_scan` 仍允许假树缺半棵；真树不能因此把缺目录、空扫描当成通过。
    """
    entry_modules = {
        "app/domain": "app.domain.project.services",
        "app/api/routes": "app.api.routes.answers",
    }
    assert set(_SCANNED) == set(entry_modules), "领域和路由两棵树都必须扫描"
    for scanned in _SCANNED:
        assert (BACKEND_ROOT / scanned).is_dir(), f"扫描目录不存在：{scanned}"

    sources = {src for src, _, _ in _scan()}
    for scanned, entry in entry_modules.items():
        assert entry in sources, f"扫描目录 {scanned} 未产出已知模块：{entry}"


def test_no_cross_domain_repository_imports() -> None:
    """跨领域 import 别人的 repository：白名单之外一律不许。"""
    violations = _violations()
    if violations:
        lines = "\n".join(f"  {src} → {dst}" for src, dst in violations)
        pytest.fail(
            "发现跨领域 repository import：\n"
            f"{lines}\n\n"
            "正路是让本领域的 service 调对方的 service，"
            "而不是直接摸对方的 repository。\n"
            "确实拆不动的，把上面的行加进本文件的 _EXEMPT，并在 PR 里说明为什么。"
        )


def test_exemptions_are_all_still_needed() -> None:
    """白名单是棘轮：还完债必须把行删掉，不许留着虚胖。"""
    stale = _stale_exemptions()
    if stale:
        lines = "\n".join(f"  {src} → {dst}" for src, dst in stale)
        pytest.fail(
            "以下豁免已经没有对应的违规了，请从本文件的 _EXEMPT 里删掉：\n"
            f"{lines}\n\n"
            "（债还完了忘删白名单，下一个人就会以为这条依赖还在。）"
        )


#: C2 的 `source_modules` 里不属于 `_SCANNED_MODULES` 的那两项。它们管的是同一批
#: 模块，只是由两处分别扫：`app.api.routes` 是守卫扫的那棵树；
#: `app.api.deps` 不在那棵树里，只有它的 models 一半归 C2 管（见 test 顶部说明）。
_ROUTE_LAYER_SOURCES = ("app.api.routes", "app.api.deps")


def _c2_source_modules(config: Path = _IMPORTLINTER) -> list[str]:
    """`.importlinter` 里 C2 那一段的 `source_modules`，按声明顺序。

    逐行读，不引 yaml 解析器：这份配置存在的意思就是让「谁管谁」一眼看得见，
    多一个解析器就多一处会和它分叉的地方。
    """
    sources: list[str] = []
    collecting = False
    for line in config.read_text(encoding="utf-8").splitlines():
        if line.startswith("name = C2 "):
            collecting = True
        elif collecting and line.startswith("source_modules"):
            continue
        elif collecting and line.startswith("    ") and line.strip():
            sources.append(line.strip())
        elif collecting and line.strip() and sources:
            break  # 下一个键（`forbidden_modules =`）开始，本段结束
    return sources


def test_c2_sources_and_the_guard_scan_the_same_modules() -> None:
    """两本名单逐条对齐：C2 的 `source_modules` 去掉路由那两项，即
    `_SCANNED_MODULES`。

    「搬出路由不等于掉出管辖」这句话落在两处手写的名单上，而两处都只写一遍、漏了
    都不出声：C2 少一个源，那个模块的 models import 就没人管了；守卫少扫一个文件，
    它的 repository import 就没人管了——而两边都还是绿的。逐条比一次，任何一边删掉
    任何一条都会红。
    """
    declared = set(_c2_source_modules())
    scanned = set(_SHARED_API_MODULES)
    only_declared = sorted(declared - scanned - set(_ROUTE_LAYER_SOURCES))
    only_scanned = sorted(scanned - declared)
    assert not only_declared and not only_scanned, (
        "C2 的 source_modules 与 _SCANNED_MODULES 指的是同一批路由共享依赖模块，"
        "改一边就要改另一边：\n"
        f"  只在 .importlinter 里：{only_declared}\n"
        f"  只在守卫的 _SCANNED_MODULES 里：{only_scanned}"
    )


def test_every_named_source_has_a_file() -> None:
    """两本名单点名的每个模块都得有对应文件——删了文件却留着名单不会自己变红。

    `_scan` 对不存在的文件是跳过的（假树只搭用得上的那半棵，靠的就是这个），所以
    真树里少一个文件会被静默少扫一片。这里逐条查一遍。
    """
    missing = [
        module
        for module in sorted(set(_c2_source_modules()) | _SHARED_API_MODULES)
        if not _module_exists(BACKEND_ROOT, module)
    ]
    assert missing == [], f"名单上点名、树里却没有的模块：{missing}"


# ---------------------------------------------------------------------------
# 自检：先证明这道守卫会红，再让它去判真树
#
# 抄 #264 的 .claude/scripts/check-repo-rules.sh --self-test。一道永远绿的守卫
# 和没有守卫是一回事，而且更糟——它会让人以为这条约束有人守着。上面两个测试当前
# 全绿，绿本身不构成"它能抓到东西"的证据；下面这组在 tmp 里搭假树，逐个形状证明
# 它会红、以及不该红的地方不红。
# ---------------------------------------------------------------------------


def _fake_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    """在 tmp 里搭一棵 ``<tmp>/app/...`` 的假树，返回 backend 根（``<tmp>``）。

    key 是 ``app/`` 下的相对路径：``domain/alpha/services.py``、
    ``api/routes/tasks.py``。路径形状必须和真树一致——模块名是按 backend 根取相对
    路径算出来的，假树跟着这个形状走，自检才测的是真代码路径。
    """
    for rel, src in files.items():
        path = tmp_path / "app" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(src, encoding="utf-8")
    return tmp_path


#: 每一条都必须被抓到。key 是 pytest 的用例名，value 是 alpha/services.py 的内容。
_MUST_FAIL = {
    "from-x-repositories-import": "from app.domain.beta.repositories import BetaRepo\n",
    "from-x-import-repositories": "from app.domain.beta import repositories\n",
    "plain-import": "import app.domain.beta.repositories\n",
    "relative-import": "from ..beta.repositories import BetaRepo\n",
    "renamed-sql_repository": (
        "from app.domain.beta.sql_repository import SqlBetaRepo\n"
    ),
    "renamed-_repositories-suffix": (
        "from app.domain.beta.recruitment_repositories import RecruitRepo\n"
    ),
    "hidden-in-a-function-body": (
        "def f():\n"
        "    from app.domain.beta.repositories import BetaRepo\n"
        "    return BetaRepo\n"
    ),
}


@pytest.mark.parametrize("source", _MUST_FAIL.values(), ids=list(_MUST_FAIL))
def test_self_test_guard_goes_red_on(tmp_path: Path, source: str) -> None:
    """每种 import 写法都得抓到——改个名或者塞进函数体就绕过去的守卫不算守卫。"""
    root = _fake_tree(tmp_path, {"domain/alpha/services.py": source})
    found = _violations(root, frozenset())
    assert found, f"没抓到：{source!r}"
    src, dst = found[0]
    assert src == "app.domain.alpha.services"
    assert dst.startswith("app.domain.beta.")


def test_self_test_whitelist_actually_suppresses(tmp_path: Path) -> None:
    """白名单能压住违规——不然存量违规会把守卫直接淹掉。"""
    root = _fake_tree(
        tmp_path,
        {"domain/alpha/services.py": "from app.domain.beta.repositories import R\n"},
    )
    pair = ("app.domain.alpha.services", "app.domain.beta.repositories")
    assert _violations(root, frozenset()) == [pair]
    assert _violations(root, frozenset({pair})) == []


def test_self_test_a_route_touching_a_repository_is_caught(tmp_path: Path) -> None:
    """路由那棵树也在管辖内——它没有自己的领域，摸谁的 repository 都算跨域。"""
    root = _fake_tree(
        tmp_path,
        {"api/routes/tasks.py": "from app.domain.beta.repositories import BetaRepo\n"},
    )
    assert _violations(root, frozenset()) == [
        ("app.api.routes.tasks", "app.domain.beta.repositories")
    ]


def test_self_test_a_shared_dependency_module_is_caught(tmp_path: Path) -> None:
    """共享依赖模块和路由同管——搬出 routes/ 不是为了掉出管辖范围。

    这条自检是补面那一刻的回归：把这两处的 ``_SCANNED_MODULES`` 去掉，它会红。
    """
    root = _fake_tree(
        tmp_path,
        {"api/task_deps.py": "from app.domain.beta.repositories import BetaRepo\n"},
    )
    assert _violations(root, frozenset()) == [
        ("app.api.task_deps", "app.domain.beta.repositories")
    ]


def test_self_test_a_shared_dependency_module_calling_a_service_stays_green(
    tmp_path: Path,
) -> None:
    """共享依赖模块调对方的 service 就是正路，不许误判成违规。"""
    root = _fake_tree(
        tmp_path,
        {"api/task_deps.py": "from app.domain.beta.services import BetaService\n"},
    )
    assert _violations(root, frozenset()) == []


def test_self_test_a_route_calling_a_service_stays_green(tmp_path: Path) -> None:
    """路由调对方的 service 就是正路，不许误判成违规。"""
    root = _fake_tree(
        tmp_path,
        {"api/routes/tasks.py": "from app.domain.beta.services import BetaService\n"},
    )
    assert _violations(root, frozenset()) == []


def test_self_test_ratchet_ignores_exemptions_for_absent_modules(
    tmp_path: Path,
) -> None:
    """发起方文件不在这棵树里 → 不算陈行；在、但 import 没了 → 算。

    两条一起测才有意义：只测前者会掩盖「这层判断把棘轮整个关掉了」。
    """
    root = _fake_tree(
        tmp_path,
        {"domain/alpha/services.py": "from app.domain.beta.services import S\n"},
    )
    absent = ("app.domain.nosuch.services", "app.domain.beta.repositories")
    present = ("app.domain.alpha.services", "app.domain.beta.repositories")
    # checkout 落后：模块压根不存在，说明不了债还没还完
    assert _stale_exemptions(root, frozenset({absent})) == []
    # 真还完了：文件在，import 没了
    assert _stale_exemptions(root, frozenset({present})) == [present]


def test_self_test_ratchet_catches_a_stale_exemption(tmp_path: Path) -> None:
    """棘轮的另一半：债还完了白名单没删，也得红。"""
    root = _fake_tree(
        tmp_path, {"domain/alpha/services.py": "from app.domain.beta import x\n"}
    )
    pair = ("app.domain.alpha.services", "app.domain.beta.repositories")
    assert _stale_exemptions(root, frozenset({pair})) == [pair]
    assert _stale_exemptions(root, frozenset()) == []


#: 每一条都**不许**被抓到——守卫判过头比判不到更难发现。
_MUST_PASS = {
    "same-domain-repository": "from app.domain.alpha.repositories import AlphaRepo\n",
    "cross-domain-service": "from app.domain.beta.services import BetaService\n",
    "cross-domain-models": "from app.domain.beta.models import Beta\n",
    "not-a-repository-module": "from app.domain.beta.wiring import beta_service\n",
    "outside-app-domain": "from app.core.errors import ValidationError\n",
    "relative-same-domain": "from .repositories import AlphaRepo\n",
}


@pytest.mark.parametrize("source", _MUST_PASS.values(), ids=list(_MUST_PASS))
def test_self_test_guard_stays_green_on(tmp_path: Path, source: str) -> None:
    root = _fake_tree(tmp_path, {"domain/alpha/services.py": source})
    assert _violations(root, frozenset()) == [], f"误判：{source!r}"


def test_self_test_top_level_files_are_out_of_scope(tmp_path: Path) -> None:
    """``app/domain/common.py`` 这类不属于任何领域包的文件不在管辖范围内。

    这是设计取舍，不是漏网：它没有"自己的领域"，跨域的概念套不上去。
    """
    root = _fake_tree(
        tmp_path,
        {"domain/common.py": "from app.domain.beta.repositories import BetaRepo\n"},
    )
    assert _violations(root, frozenset()) == []


def test_self_test_clean_tree_passes(tmp_path: Path) -> None:
    """干净的树必须绿——证明上面那些红不是因为扫描本身炸了。"""
    root = _fake_tree(
        tmp_path,
        {
            "domain/alpha/services.py": (
                "from app.domain.beta.services import BetaService\n"
            ),
            "domain/alpha/repositories.py": "class AlphaRepo: ...\n",
            "domain/beta/services.py": "from .repositories import BetaRepo\n",
            "domain/beta/repositories.py": "class BetaRepo: ...\n",
        },
    )
    assert _violations(root, frozenset()) == []
