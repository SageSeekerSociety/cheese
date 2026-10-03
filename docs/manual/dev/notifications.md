---
title: 通知与待办
kind: 参考
summary: 一张通知表的两侧读者、投递账本、站内与邮件与浏览器推送三条渠道，以及待办为什么不读通知。
covers:
  - backend/app/domain/notification/
  - backend/app/domain/delivery/ledger.py
  - backend/app/api/routes/notifications_flat.py
---

# 通知与待办 {#notifications}

一个人收到的每一条通知都写在同一张表上，谁发的都一样；而「我现在要处理什么」是另一件事，它不读这张表。

> 讲：通知怎么产生、发给谁、三条渠道各自怎么送、推送为什么只推两种。不讲：待办清单的查询与收件人判据（见[看板](/dev/boards) 和[任务与工作目录](/dev/tasks)），平台在房间里说的那句话怎么发出来（见[一条消息怎么变成芝士的一轮](/dev/turn)），谁在名册上（见[团队、项目与成员](/dev/teams)）。

## 一张表，两侧读者 {#one-table}

`notification` 是全平台唯一的通知表（结论 58）。在这之前有两张、互不知道对方：`alerts` 是平台报告自己（房间里 @ 了谁、一轮完了、一件事等人拍板），`notification` 是人对人（回帖、邀请、审批结果）。两边各有自己的读写路径、各有自己的未读数，于是「一个人被 @ 了而人不在页面上什么都收不到」不是哪一边的 bug——是两张表都只看得见自己那一半。

并起来之后，**一行就是一个人收到的一条通知**。并表带来两个必须写下来的约定：

- **收件人有两个名字**。`recipient_handle` 是名册上的名字，`receiver_id` 是账号池里的那一行。投递这一侧只认 handle（I11）——房间的名册、@ 的目标、`cheese_notify` 的 `to` 给的都是 handle；知是那一侧的站内信按数字 id 查。项目收件箱写下的行两个都填（handle 在账号池里找不到对应行时后者为空），知是那一侧写下的行只有 `receiver_id`。
- **两侧各自列出的是各自的行**。知是的收件箱只列 `recipient_handle IS NULL` 的那些（`repositories._my_mail`，`api/routes/notifications_flat.py` 那条 int 键的 `/notifications`），带名册名字的只在项目收件箱里列出（`_mine_in`）。为什么这句不能少：房间里的 @ 若同时落进待办页的「动态」，那边渲染不了它——前端按 `type` 找模板，`MENTION` 读的是 `payload` 里的 `mentioner`/`discussionTitle`，项目通知的文字在 `title`/`body` 上，渲染出来是一句「有人提到了你 / 在讨论 未知讨论 中提到了你」、还不带跳转，而未读数照加，知是那边一点「全部已读」还会把项目角标一起清掉。按 id 点名的那几条（取一条、标一条、删一条）不带这一句：那是已经拿着行号的调用者在动自己名下的行，两侧都认它。

**广播没有自己的形状**。`alerts` 里 `target_handle IS NULL` 曾经表示「这条谁都看得见」，读的一侧于是每一处都得写成「点了我的名 **或者** 谁的名都没点」——四处查询、一处内存过滤，漏掉任何一处就是把别人的信念给了他。现在广播在**写入的时候**就展开成一人一行（`ProjectNotificationService.create`），读的那一侧只剩一句相等。展开成零行不是成功：名册上一个人都不剩时它抛错、不静默丢——并表前那种「一行谁都看得见」的形式丢不掉，展开成一人一行之后「零行」就是把整条通知扔了，而路由照样回 200。

分级 `NotificationLevel` 只有三档：`silent` 默默记下、不点角标，`light` 对话里轻提一句，`strong` 强提醒（限流按它算）。人对人的那几种不分级、`level` 为空——它们不进项目角标，而项目角标的查询本来就按 `project_id` 圈过一遍。

## 发通知只有账本一处 {#ledger}

`delivery/ledger.py` 是唯一的出口（I11）。它收一个 `DeliveryEvent`——窄到只剩账本要记的部分，`id` 是这条事件的身份（多半是房间里那条 block 的 id），`occurred_at` 是事情发生的时刻而不是投递的时刻。去重键跟着 `id` 走，所以调用点重算一遍寻址不会变成第二次打扰。

它原子地做三件事：写收件箱行、记外部渠道的意图（`ChannelDelivery`）、并回写 `sent_at`。收件人在事件发生时就**快照**下来，之后名册怎么变都不影响这一笔。补发试到 `MAX_ATTEMPTS = 5` 为止。

三条纪律，每条都有它的失败形状：

- **`delivery_key` 必填，全表唯一**。「恰好一次」靠的就是它：一次发送在回写 `sent_at` 之前崩掉，补发会把同一笔再发一遍，插入撞上唯一约束，收件人手里仍然只有一条。搬进来的旧 alert 行也带着键（`alert:<原 uuid>:<收件人>`），搬家那个迁移靠它**整条跳过**已经落过的通知，而不是逐行去撞约束——第二遍跑的时候名册可能已经变了，逐行撞约束拦不住那批「窗口期里才进房间的人」凭空多出来的行。
- **入库时间是「现在」，不是事件发生的时刻**。收件箱按 `created_at DESC` 翻页，落一个旧时间戳会把这一条插进二十分钟前的位置——未读数加一，人打开收件箱却看不到新东西。事件发生的时刻记在账本的 `deliveries.event_at` 上。
- **渠道说没收到，就不算送到**。`NotificationEventHandler.dispatch` 的返回值是「每个渠道都收下了」，而账本靠这个返回值决定回不回写 `sent_at`；吞掉一个渠道的异常还报成功，账本就会记下一笔根本没发出去的投递，而那正是补发要救的那一档。

`build_notification_event_handler`（`publisher.py`）装配两个渠道：`InAppNotificationHandler` 和 `ChannelIntentHandler`。前者写站内行，**包在一个 savepoint 里**——只 try/except 不够：一个写库的 handler 可能在 flush 中途失败，那样整个共享 session 的事务在 Postgres 里已经废了，即使 Python 层抓住了异常，调用方之后的 commit 也会无声地坏掉；savepoint 把这个失败圈在它自己的写入里。后者（`outbox.py` 的 `ChannelIntentHandler`）只**记意图**：把每一笔展开成 `email` 行（类型在 `MAILBOX_ONLY` 里的除外）、以及（`push_enabled` 且类型在 `PUSHABLE` 里时）一行 `push`，带按收件人语言写好的标题正文和 project/topic id，`on_conflict_do_nothing` 撞唯一约束 `uq_delivery_channel`。真正打网络是别的循环的事。

`MAILBOX_ONLY` 目前只有 `SPACE_ANNOUNCEMENT`：一条空间公告同时发给全空间，按人发邮件就是一个班的信箱各收一封，它要的只是人回到平台时在「动态」里看得见。它也不在 `PUSHABLE` 里，所以只落站内那一行。

账本另有三个事后的动作，都按事件 id 找回那件事发出去的每一笔（`deliveries.event_id` 上有索引）：`amend` 把已发出的收件箱行和账本行的 `payload` 换成新的说法，不改已读未读，也不再发一遍；`retract` 删掉收件箱行、外发意图和账本行；账本行留着的话，补发会把收件箱那一行写回来。`settle` 是那件事办完了：结果并进收件箱行和账本行的 `payload`，收件箱那一行标为已读并记下 `resolved_at`。公告的修改和删除走 `amend` 和 `retract`；芝士的提问答掉时走 `settle`（并进 `answered`），通知随之改说「已回答：…」，不再说「待你回答」，图标也从警示色换成已回答的对勾。答掉有两条路：点选项，`answered` 是那一项；或者被问的人在同一条线上打字回了一句，问出口之后他说的第一句话就是回答，`answered` 是这句话去掉 `<@handle>` 后的前 40 个字（`settle_questions_answered_by`，判据和看板「待回答」的同一条）。

## 外发渠道：租约、重试、死信 {#outbox}

`outbox.drain_channel` 一次取一条（`with_for_update(skip_locked=True)`），改状态为 `sending`、拿一个 `claim_token`、租到 `LEASE_SECONDS = 120` 秒，**提交之后才去打网络**。打的时候起一个心跳任务每 40 秒续租一次（`LEASE_SECONDS / 3`）。收尾那一步带 `claim_token` 与状态两个条件再查一次：查不到就说明另一个 worker 已经回收了这笔，直接跳过——**一个卡住的 worker 不能去确认别人已经接管的 claim**。没有持有任何 DB 事务去联系服务商。

结果只有三种：成功写 `sent`；失败且还有次数就回到 `pending`、30 秒后再来；到顶写 `dead` 并留 `last_error`。状态一共四个：`pending` / `sending` / `sent` / `dead`。`drain_channel` 的返回值里另有一个 `expired` 计数，那是 `push` 渠道发现的、服务商说已经不存在的订阅数（它们被当场删掉），不是一种状态。

`legacy_queue.py` 是 Redis 那份旧队列的搬家闸门：新生产者从不写那些键，它用旧消费者的锁把 pending/processing 的东西搬进带唯一 id 的 staging 记录，**SQL 先提交、才回 Redis 的 ack**，所以重放 staging 不会重复一笔意图。搬不动也不影响新通知——`drain_push_queue` 把它的异常吞在日志里，下一跳再试。

## 浏览器推送：只推「该我动手」的两种 {#push}

`push.py` 里 `PUSHABLE` 只有两个码：`ROOM_NOTICE` 和 `CHEESE_QUESTION`——平台在房间里说的那一句要人动手的话，和芝士停在那里等回答的一个问题。别的类别码一个都不推。

这不是省事，是判据：芝士跑一轮会产生几十条消息，按消息推送就是几十条推送，人会直接把这个渠道关掉，之后真正要他动手的那一条也就收不到了。推送的价值全在「收到就意味着该我动了」。

- **文字就是通知里的文字**。`push_text` 不另写一套措辞：`ROOM_NOTICE` 的标题是 `payload.content`（房间里那一行），`CHEESE_QUESTION` 的标题是芝士问的原话；正文只说「在哪个房间」，因为推送脱离上下文出现在系统通知栏里，而「是哪件工作」正是人判断要不要立刻打开的依据。站内通知和房间里那一行在读者的屏幕上按他选的语言渲染同一个键（`payload.message`，见 `backend/app/core/sentences.py`）；推送和桌面端通知由后端在记意图（`outbox.py`）和发帧（`live.py`）时按收件人的 `user.language` 渲染这个键，因为推送由服务端加密后发出，浏览器收到就原样显示。没选过语言的人、没带键或那种语言缺这一句的行，整条用存下的中文。
- **服务端看不到内容**。Web Push 的正文由**浏览器的**密钥加密（`p256dh` / `auth`），服务端加密完就再也解不开。所以那张表存的不是「发过什么」，只是「往哪儿发、用哪把钥匙」。
- **订阅是 upsert，而且要覆盖**（`PushSubscriptionRepository.save`）。同一个浏览器会反复订阅（权限重新授予、service worker 换代、清了站点数据又装回来），每次都换回同一个 endpoint；两把加密材料**可能变了**，拿旧钥匙加密的内容新浏览器解不开，症状是推送静默地不出现。`user_id` 也要覆盖：同一台机器换人登录，那个 endpoint 就属于新的人了。
- **退订要带上自己的 id**。endpoint 唯一但它不是秘密——只按 endpoint 删，等于任何登录用户拿到别人的 endpoint 就能替他关掉推送。而投递发现订阅没了（404/410）时**不带**：那时判据来自服务商、和哪个人无关，那一行本来就该消失。
- **发一条是同步库，丢进线程里**。`pywebpush` 内部用 requests，一次投递要做一次椭圆曲线加密再打一次 HTTP，放在事件循环上会把整个后端卡住。一次投递的成败不牵连别的订阅：只要不是**所有**目标都失败，这一笔就算渠道收下了，不会给已经收下的浏览器重发。

## 邮件 {#email}

`maintenance.py` 只做一件事：让人知道发生了什么、并且能回到平台上去看。所以它不解析每种类型的 payload（那要把 entity resolver 那一套依赖都拖进来），只给类型的人话（`_SUBJECT_LINES`，收件人在自己邮箱里读到它，那里没有任何上下文，`TEAM_REQUEST_APPROVED` 这种代号对他等于乱码）、一段可能有的摘要（`_SUMMARY_KEYS` 里第一个有字的），和一个链接。摘要只从几个常见键里取——猜错一个键的代价是邮件少一行，猜整个结构的代价是发错内容。每一段用户内容都转义过：`payload` 里装的是别人写的字，而这段 HTML 会落进某个人的邮件客户端。

## 待办不是通知 {#todo}

`GET /awaiting-me` 读的是当下的事实，不是通知表——为什么必须这样，写在 `room_task/awaiting.py` 的模块说明里（通知是一条条事件记录：卡递上来发一条，之后它被驳回、作废、或者别人先处理掉了，那条记录还躺着，从它身上读不出已经不作数）。而跨项目的那份清单和项目看板读的是同一份 `presentation`、同一份 `address()` 判据。

通知里另有两种「不走」的行：`DECISION_REQUEST` 在被答复之前不离开收件箱（读过不等于答过，选的哪一项写在 `metadata_payload["resolved_choice"]`），`ACCEPT_REQUEST` 是验收卡点名。两者都是平台报告自己的那四种之一，值保持小写原样——`cheese_notify --kind` 和前端的标签表按它写，存量行里也是那几个字。

## 边界与坑 {#traps}

- **通知表不能拿来算待办**。见上，这是这一页最容易被写错的一条。
- **一行只对一个人**。任何「广播」「全体」的写法都必须在写入时展开，读的那一侧没有这一档。
- **`delivery_key` 是全表唯一，NULL 除外**。Postgres 的唯一索引不认为两个 NULL 相等，所以账本之前写下的旧行互不排斥——新增代码不许再写进一个没有键的通知行。
- **推送不是降级通道**。没有配置 Web Push 时 `drain_push_queue` 直接返回全零，那些 `push` 意图留在表里；它们不会转成邮件，也不会补发。
- **站内「全部已读」只清一侧**。知是那一侧的标记按 `receiver_id`，项目那一侧按 handle——这是同一条索引服务的两种读法，不要指望一次点击把两边都清了。
- **搬家是单向的**。`import_legacy_queue` 只把 Redis 里旧队列搬进 SQL，从不往回写；搬完那个键就空了。
