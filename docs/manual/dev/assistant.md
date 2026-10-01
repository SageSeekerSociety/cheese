---
title: 个人芝士
kind: 流程
summary: 项目之外的芝士：一个人在某个地方和芝士的对话怎么存、怎么问模型、怎么扣钱。
covers:
  - backend/app/domain/assistant/
  - backend/app/api/routes/assistant.py
  - frontend/src/components/assistant/AssistantPanel.vue
  - frontend/src/views/tasks/composables/useAssistant.ts
---

# 个人芝士 {#assistant}

项目之外的芝士：一个人在某个地方和芝士的对话怎么存、怎么问模型、怎么扣钱。方向与规则见 #2285。

> 讲：对话的两份记录、一次提问从准入到扣费、压缩。不讲：项目里的 AI 队友（见[一轮对话](/dev/turn)），额度本身（见[计费流程](/dev/billing#personal)）。

## 对话 {#conversations}

每人一个芝士、多段对话。一段对话属于开始它的地方（`place_kind` + `place_id`，现在只有题目：`("task", "<题目 id>")`），只有本人看得到。别人的对话和不存在的对话答同一个 404，id 不泄露任何东西。

同一段对话存两份，因为两者在压缩之后不再一样：

| 记录 | 表 | 给谁 | 会不会缩短 |
|---|---|---|---|
| 说过的话 | `assistant_messages` | 面板显示 | 不会，只追加 |
| 模型的历史 | `assistant_conversations.history`（Pydantic AI 的消息 JSON，含工具调用）和 `summary` | 下一次提问发给模型 | 超过上限时压缩 |

## 一次提问 {#ask}

`POST /api/assistant/conversations/{id}/ask`，以 server-sent events 流式返回：`delta`（文字）、`tool`（芝士在用哪个工具）、`error`、`done`。

1. **准入**：对话是本人的；对话所在的题目本人打得开（和题目页同一个判断，`_ensure_task_readable`）；网关上 `ASSISTANT_MODEL` 有单价、设置了 `LLM_GATEWAY_CREDIT_USD`，否则无法计费，拒绝；个人额度有剩余，否则 429 并写明哪天重置；同一段对话同一时间只答一个问题（Valkey 锁，180 秒过期）。
2. **跑**：Pydantic AI 在后端进程里跑工具调用循环，经网关调模型，用一把专用的虚拟 key（`assistant-gateway-key`，不设预算）。请求照文档站问芝士已经验证过的形状发：工具定义不带 `strict`，用 `max_tokens`，关掉 thinking。模型的说明由两段组成，都放在最前面、每次一样，同一道题上的提问共用前缀缓存：规则，和这道题的说明（`task_brief`，只有题目页上公开的内容，不含附件）。
3. **工具**：只读，以提问人本人的身份：`my_tasks`（他参与的题，含截止时间）、`search_docs` / `read_doc`（公开文档）。
4. **收尾**：这一轮在后台任务里跑，响应只从队列里读。读者中途离开，这一轮照样答完、存下、扣费。收尾用自己的数据库会话：追加回答、写回模型历史、按每一轮的用量扣个人额度（缓存命中的部分按缓存价，`kind = "assistant"`）。

## 压缩 {#fold}

一次提问的输入超过 `ASSISTANT_HISTORY_CAP_TOKENS`（默认 1.6 万）时，收尾时再调一次模型，把最近两个问题之前的内容写成摘要，模型历史从「摘要 + 最近两个问题」重新开始。切点总在某个问题开始的地方，工具调用不会和它的结果分开。这次调用的用量算在触发它的那个问题上。

两次压缩之间历史只往后追加：前缀不变，缓存一直命中。滑动窗口每次都会改前缀，不用。压缩失败只意味着下一次的输入更长，历史保持完整。

## 面板 {#panel}

题目页「问芝士」打开 `AssistantPanel`：宽屏是停靠在右边的抽屉（打开时才挂，挤窄页面），窄屏是底部弹层。打开时接着这道题最近的那段对话；「新对话」不先建空对话，第一句话发出去才建。面板只拿 props、发事件，取数和流式读取在 `useAssistant`。
