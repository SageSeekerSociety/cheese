---
title: 运行记录
kind: 参考
summary: 平台运行一段对话时做了什么、遇到了什么，不进对话，记在 run_records 里，给现场、输入框上方的状态和管理后台读。
covers:
  - backend/app/domain/run_record/
  - backend/app/domain/agent/run_records.py
  - backend/app/domain/agent/platform_notices.py
  - backend/app/domain/agent/room_events.py
  - backend/app/api/routes/admin_run_records.py
  - backend/app/api/routes/run_records.py
  - backend/app/api/routes/topics_transcript.py
  - frontend/src/components/room/composables/useRunRecords.ts
  - frontend/src/views/admin/AdminRunRecordsPage.vue
---

# 运行记录 {#run-records}

平台运行一段对话时做了什么、遇到了什么，不进对话，记在 `run_records` 里，给现场、输入框上方的状态和管理后台读。

> 讲：哪些事记成运行记录、它们在哪里出现、留多久。不讲：对话里仍然出现的那些平台提示怎么渲染，见[前端结构](/dev/frontend)。

## 记什么、不记什么 {#kinds}

对话里只留人要知道或者要动手的事；平台自己的运行过程记成运行记录。`notice_meta.RUN_RECORD_EVENTS` 是那张清单，`post_system_event` 遇到清单里的类别就改记，调用点不用自己判断：

| 类别 | `kind` | 在哪出现 |
|---|---|---|
| 排队、断线后接回记录、输入已登记在核对、转入队列 | `turn_queued`、`delivery_checking`、`delivery_fallback` | 现场；还没开始的一轮在输入框上方写「排队中」 |
| 工具通道接回、同一批消息重投 | `tools_recovered`、`prompt_replayed` | 现场 |
| AI 服务重试、整理上下文、等机器 | `api_retry`、`context_compact`、`device_waiting` | 现场；在跑的一轮的状态行读它们 |
| 环境的准备、就绪、空闲释放 | `cloud_startup`、`cloud_provisioning`、`sandbox_asleep` | 现场 |
| 记忆改动、整理 | `memory_changed` | 现场，见[记忆 · 改动记在哪](/dev/memory#events) |
| 有人改了房间里 agent 读过的一条消息 | `message_edited` | 现场；agent 读的是同一句旁边那条隐藏提示（`block/editing.py`） |
| 定时投递到点 | `timed_delivery` | 现场；记录的 id 就是那条定时投递的 id，账本的事件指着它 |
| 后端报错、前端报错 | `backend_error`、`frontend_error` | 只在管理后台；不属于任何对话，发生在哪个对话写在 `meta.conversation` |
| 2026-10-07 之前支线以外没跑完的轮次（迁移 `52fee3dd7773` 搬过来的） | `turn_failed`、`platform_error`、`turn_timeout` | 现场；管理后台算在「报错」里 |

额度用完环境被释放、环境不再响应被换掉、一轮最终没答上这些要人动手的事仍然说在对话里。在频道主线上叫芝士，它在支线里回答，没答上的那一行落在支线里，主线那条消息下面写「没能回复，点开看原因」。

## 一轮一行：分母 {#turns}

上面那种读法是「一种事一行」，答不了「这段时间一共起了多少轮、其中多少轮没跑完」。`GET /run-records/turns` 答的就是这个分母：一行一轮，新的在前，`limit`（默认 200，最多 500）之外的行只算进 `total`。

窗口跟运行记录同一套（24h / 7d / 30d），锚在这一轮自己的 `started_at` 上——一轮跨过窗口边界时算它开始的那一刻，分母问的是「这段时间起了多少轮」。

一行给这些：轮次号、对话号、开始时间、结束时间（空 = 这一轮还开着，`stopped_at IS NULL` 就是「在跑」）、这一轮的终态失败事件类别、这一轮的提示被投了几次。失败类别从 `blocks.meta.event_type` 读：`turn_failed`、`platform_error`、`turn_timeout`、`deploy_interrupted`；同一轮落过两条时报最后落的那一条。它比房间里摆红卡的口径宽——超时和部署中断不算红卡里的失败，但这一页问的是「这一轮跑完了没有」，所以算。

**`meta.severity` 是 `info` 的不算失败。** 平台把 `turn_failed` 这个码也用在几条只是要等的提示上（主人不在线、这一轮在等人、消息没送到任何人），它们和真正的失败共用一个类别码，只在 severity 上分得开。不分开，一条健康的轮次会被报成失败，虚高分母。

**「没有失败事件」是平台没记录，不是记了成功。** 成功不写事件，这一列空只说明平台没为这一轮落过终态事件，不说明这一轮做成了什么。有几条失败读不到，因为它们落事件时没有轮次号可传：卡死轮次的超时（孤儿扫底）、孤儿扫底认下的部署中断、投递彻底放弃。要数它们去看运行记录那一层。

「提示被投了几次」（`meta.prompt_attempts`）是**这一批消息到这一轮为止一共被喂了几次**，不是「这一轮喂了几次」：计数写在消息块上，每组建一次提示就把认领改写成当前这一轮，而一轮失败时认领会撤回——所以失败的轮次这里常常是空，空不代表没喂过。

## 怎么写、怎么推 {#writing}

`run_record.service.record` 在调用方的事务里写一行；`agent.run_records.record_now` 自己开事务、提交后推一帧 `run_record` 到对话的通道上。帧里那一份长得和一条事件块一样（`as_payload`，带 `run_record: true`），现场按块的样子排它，聊天区不收。重试到第几次、整理完没有、机器连上没有这几种会原地改写同一条（`restate_now`），前端按 id 换掉旧的那一份。

支线里的记录同时推一份 `thread_status` 帧到频道主线（`agent.run_records.publish`）：主线那条消息下面那一行据此写芝士在排队、重试、整理上下文还是等环境。这一帧只实时推，不进断线重放的缓冲。

`GET /topics/{id}/transcript` 把这一页时间范围里的会话原始记录（[原始记录](/dev/session-records)）、上线前轮次的那一行说明和运行记录合在一起按时间排好返回。侧栏的「在等机器」、消息有没有开始处理、孤儿轮次判断，都同时读块和运行记录。

## 留多久、谁能看 {#retention}

运行记录留 30 天（`run_record.models.RETENTION`），巡检 `run record expiry` 每小时删一次过期的。管理后台「运行记录」（`/admin/run-records`，只给平台管理员）按种类合并：报错按指纹，别的按把数字抹掉的那句话，给出次数、涉及几个项目、首次和最近时间、24 段分布，点开看最近一次的全文和出现在哪些项目、对话里。平台自己项目里的 AI 队友用 `cheese_run_records` 读同一份（列表 `GET /run-records`、某一种的详情 `GET /run-records/detail`、[一轮一行](/dev/run-records#turns) `GET /run-records/turns`，都带上它所在的频道）；别的项目的频道调它会被拒，因为报错里有其他项目的名字和对话。

现场每一轮顶上那根细条（`lib/sitePhases.ts`）也读运行记录：排队从排队那条记录到这一轮开始，等环境从等机器那条到它说连上了，重试从重试那条到下一步出现，剩下的算工作。
