---
title: 个人芝士
kind: 流程
summary: 项目之外的芝士：一个人在某个地方和芝士的对话怎么存、在哪里跑、怎么扣钱。
covers:
  - backend/app/domain/agent/personal/
  - backend/app/domain/assistant/models.py
  - backend/app/api/routes/assistant.py
  - backend/app/domain/agent/session_host/
  - frontend/src/components/assistant/AssistantPanel.vue
  - frontend/src/views/tasks/composables/useAssistant.ts
---

# 个人芝士 {#assistant}

项目之外的芝士：一个人在某个地方和芝士的对话怎么存、在哪里跑、怎么扣钱。方向与规则见 #2285。

> 讲：对话怎么存、会话在哪里跑、一次提问从准入到扣费。不讲：项目里的 AI 队友（见[一轮对话](/dev/turn)），额度本身（见[计费流程](/dev/billing#personal)）。

## 对话 {#conversations}

每人一个芝士、多段对话。一段对话属于开始它的地方（`place_kind` + `place_id`，现在只有题目：`("task", "<题目 id>")`），只有本人看得到。别人的对话和不存在的对话答同一个 404，id 不泄露任何东西。

说过的话存在 `assistant_messages`，按顺序只追加，面板显示的就是它。模型那一份对话不在数据库里，在这段对话自己的会话里。

## 会话 {#session}

和房间里的芝士是同一套底座：中心会话机上钉住版本的 pi，由 runner 守着，用同一个启动脚本起（`harness/pi/launch.py`、`host.py`），后端用同一种「等到有新东西才回」的读法读它的日志和正在写的内容（`driven/runner.py`）。区别是没有房间要的那些东西：没有项目、执行机、话题锁、工作租约和房间日志。起会话、发问题、读回答由会话核心做（`agent/session_host/`），和[文档里的芝士](/dev/doc-agent#session)、[房间里的芝士](/dev/harness#contract)共用；这里只决定会话是什么样的（`personal/session.py`）。

- **一段对话一个 pi 会话**，会话 id 就是对话 id，放在会话机的 `~/.cheese/personal/<用户 id>/<对话 id>`。
- **没有手**：不给执行目标，pi 只开平台列给它的三个工具（`--tools`），pi 自己的读写文件、跑命令都不开。系统提示词替换掉 pi 默认的那份编程助手说明。
- **空闲 3 分钟退出**，对话留在磁盘上；下一个问题把它在同一个对话上重新起来。
- **每人最多同时跑 2 个**：起第 3 个时，关掉这个人最久没用的那个；正在回答的不关，所以同时在更多地方提问时会暂时超过 2 个。
- **每个会话内存上限 512 MB**（runner 和 pi 合计，用户级 systemd scope）。起新会话前看会话机剩余内存，不够就排队等，最多 3 分钟（`HOST_WAIT_S`）：答完的会话空闲几分钟就退出，内存是这样空出来的；3 分钟后还不够就回「芝士当前繁忙，稍后重试」。已经在跑的会话不受影响。
- 关掉 thinking，单次回答最多 1500 token。对话长到一次提问超过约 1.6 万 token 时，pi 自己把较早的部分压成摘要，保留最近约 6000 token 原文，发生在触发它的那次提问里。
- 打开一段对话（`GET /api/assistant/conversations/{id}`）时，后台顺手把它的会话起起来，第一个问题就不用等启动。「新对话」第一句话发出去才建，这一步帮不上它。

**在新会话之前就有的对话**：会话第一次被问时，把这段对话之前说过的话（最近约 2.4 万字）连同问题一起交给它一次，接着答。

## 凭据 {#credential}

会话启动时拿的是个人凭据（`cxpu_`，`sandbox_auth.mint_personal_credential`），写着这个人和这段对话，不带任何项目、房间、队友的信息。它只开 `/llm/v1/*`：换上这个人自己的网关虚拟 key 转发给网关。带这张凭据调的模型都记在这个人头上；能不能问，在提问时的准入里判。房间的凭据、项目凭据、平台密钥、人自己的登录都不能代替它；它也打不开任何房间或项目的接口，打不开人自己的 API。

工具用的是每次提问另签的代行凭据（`cxdg_`，`sandbox_auth.mint_delegated_credential`）：写着这个人和这次回答的编号，只读，有效期是回答上限再加一分钟。工具调的是平台工具表里的 `cheese_my_tasks`（`GET /tasks/joined`）、`cheese_docs_search` 和 `cheese_docs_read`（`/docs/agent/search`、`/docs/agent/read`，不带房间，只读人人能读的页），按这个人的权限判断；代行凭据只在 `api/auth.py` 的 `DELEGATED_ROUTES` 列出的接口上有效。

## 一次提问 {#ask}

`POST /api/assistant/conversations/{id}/ask`，以 server-sent events 流式返回：`queued`（在等会话机空出来）、`delta`（文字）、`tool`（芝士在用哪个工具）、`error`、`done`（`stopped`：被人停下了）。

1. **准入**：对话是本人的；对话所在的题目本人打得开（和题目页同一个判断，`_ensure_task_readable`）；网关上 `ASSISTANT_MODEL` 有单价、这个人有一把只认当前 `ASSISTANT_MODEL` 的网关 key（第一次提问时开；模型换了就先把旧 key 的花费扣完，再换一把新的，旧的在网关上吊销），会话机在线，否则拒绝；个人额度有剩余，否则 429 并写明哪天重置；同一段对话同一时间只答一个问题（Valkey 里的锁，和房间、文档的芝士用同一套 `agent/admission.py`：回答期间续期，后端换了由接着读的那个后端续，没人续时 30 秒内自动释放）。会话在回答里起：等会话机时先推一条 `queued`，等不到或起不来就以 `error` 收尾。
2. **跑**：会话起来以后才签这一问的代行凭据，有效期从那时算，排队的时间不占它。问题连同凭据送进会话，pi 经 `/llm/v1` 调模型，工具带着代行凭据调平台接口。后端读会话：正在写的文字变成 `delta`，正在调的工具变成 `tool`；日志里这一问的最后一条记录是答案。答完后会话若还在压缩对话，等它做完再放开锁。
3. **收尾**：这一问由会话核心读到答完（`session_host/consumptions.py`），不靠发问的那个请求，也不靠哪一个后端进程：读它的后端每几秒续一次租约，部署时先把租约放掉，异常退出时租约 20 秒内过期，别的后端看到就接着读，从上次读到的地方往下。读者中途离开、后端重启，照样答完、存下、扣费。回答的每一步都写进这一问在 Valkey 里的一条流，响应只是在读这条流：第一条 `answering` 带这一问的 id，之后每条都带它在流里的位置（`id`）；连接断了，用 `GET /api/assistant/conversations/{id}/answers/{这一问}?after={位置}` 接着读剩下的。打开一段还在答的对话，`GET /api/assistant/conversations/{id}` 的 `answering` 就是这一问的 id，面板接上去看。`delta` 带 `at`：这段字从回答的第几个字起，接着读时重复听到的字按它放回原位。要它停只有 `POST /api/assistant/conversations/{id}/stop`（只有本人能停）：排队中的不再等，正在答的让会话停下；已经写出的部分存下，`assistant_messages.stopped` 记着它是被停下的，面板在它后面写「已停止」。答案存进 `assistant_messages`，放开锁，然后读这个人 key 上新增的网关花费，扣个人额度（`billing.py`，`kind = "assistant"`，缓存命中的部分按网关的缓存价）。网关的花费记录晚到时过几秒再读；更晚到的由下一次提问读到。

## 面板 {#panel}

题目页「问芝士」打开 `AssistantPanel`：宽屏是停靠在右边的抽屉（打开时才挂，挤窄页面），窄屏是底部弹层。打开时接着这道题最近的那段对话；「新对话」不先建空对话，第一句话发出去才建。面板只拿 props、发事件，取数和流式读取在 `useAssistant`。
