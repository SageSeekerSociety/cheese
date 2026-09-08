<script setup lang="ts">
// 一张卡 —— 房间派出去的一件活，连着它自己的对话。
//
// 它替代了「点开一条活就跳到一个新地点」那套。一件活不是地点：做它的分身住在房间
// 的会话里，它没有名册、没有归档、没有自己的一轮。所以点开一张卡不该离开房间——
// 你还在这个房间里，只是从看板往下钻了一层，`?card=` 把这一层写进地址。
//
// 屏幕上每一个状态词都是后端 `presentation` 算好的，这一段一个都不推。
import type { Block, RoomTask } from '../../cx_types'

import { computed, nextTick, ref, watch } from 'vue'
import DOMPurify from 'dompurify'

import { getRoomTask, sayOnRoomTask } from '../../api'
import { columnDotStyle } from '../../lib/board'
import { markdown } from '../../lib/markdown'
import { relTime } from '../../lib/relTime'
import { myHandle } from '../../me'

const props = withDefaults(
  defineProps<{
    /** 这张卡所在的房间。 */
    roomId: string | null
    /** 哪张卡。 */
    cardId: string | null
    /** 这一格在屏幕上。折起来的时候不去拉。 */
    active?: boolean
    /** 每有一轮动静就加一 —— 分身干活的每一步都记在这张卡上。 */
    refreshTick?: number
  }>(),
  { active: false, refreshTick: 0 }
)

const emit = defineEmits<{ (e: 'back'): void }>()

const card = ref<(RoomTask & { blocks: Block[] }) | null>(null)
const loading = ref(false)
const errorMsg = ref<string | null>(null)
const draft = ref('')
const sending = ref(false)
const timelineRef = ref<HTMLElement | null>(null)

async function load(silent = false) {
  const room = props.roomId
  const id = props.cardId
  if (!room || !id) {
    card.value = null
    return
  }
  if (!silent) loading.value = true
  errorMsg.value = null
  try {
    // limit：卡下的对话是一个分身干活的全过程，一条跑久了的活能有上千块。底部对齐
    // 的窗口和聊天面板同一个道理——先给最近的，够看「它现在在干什么」。
    const payload = await getRoomTask(room, id, { limit: 200 })
    if (props.roomId !== room || props.cardId !== id) return
    card.value = payload
    void nextTick(scrollToBottom)
  } catch {
    if (props.roomId !== room || props.cardId !== id) return
    errorMsg.value = '这张卡打不开了'
    card.value = null
  } finally {
    if (props.roomId === room && props.cardId === id) loading.value = false
  }
}

function scrollToBottom() {
  const el = timelineRef.value
  if (el) el.scrollTop = el.scrollHeight
}

watch(
  () => [props.roomId, props.cardId, props.active, props.refreshTick] as const,
  ([, , isActive], prev) => {
    const cardChanged = prev?.[1] !== props.cardId
    if (cardChanged) card.value = null
    // 换了卡要给加载态，同一张卡跟着房间的动静重取就不要——闪一下空白比不刷新更糟。
    if (isActive) void load(!cardChanged)
  },
  { immediate: true }
)

const dotStyle = computed(() => (card.value ? columnDotStyle(card.value.presentation.column) : {}))

/** 对话里值得显示的块。事件（工具调用）留给「现场」，这里只放说过的话。 */
const said = computed(() => (card.value?.blocks ?? []).filter((b) => b.kind === 'message' && (b.content || '').trim()))

function renderMarkdown(text: string): string {
  return DOMPurify.sanitize(markdown.parse(text, { async: false, breaks: true }))
}

async function send() {
  const room = props.roomId
  const id = props.cardId
  const text = draft.value.trim()
  if (!room || !id || !text || sending.value) return
  sending.value = true
  try {
    await sayOnRoomTask(room, id, text, myHandle())
    draft.value = ''
    await load(true)
  } catch {
    errorMsg.value = '没发出去，再试一次'
  } finally {
    sending.value = false
  }
}
</script>

<template>
  <section class="panel-card">
    <header class="panel-card__head">
      <button type="button" class="panel-card__back t-meta" @click="emit('back')">
        <v-icon size="14">mdi-chevron-left</v-icon>
        <span>看板</span>
      </button>
    </header>

    <div v-if="loading && !card" class="d-flex justify-center py-6">
      <v-progress-circular indeterminate color="primary" size="20" />
    </div>

    <div v-else-if="!card" class="px-3 py-4 t-body c-muted">
      {{ errorMsg ?? '这个房间里没有这条活' }}
    </div>

    <template v-else>
      <div class="panel-card__title">
        <span class="board-dot" :style="dotStyle" aria-hidden="true" />
        <span class="t-body panel-card__name">{{ card.title }}</span>
      </div>
      <div class="panel-card__meta t-meta">
        <span data-testid="card-status">{{ card.presentation.display_status }}</span>
        <span class="panel-card__sep">·</span>
        <span v-if="card.owner_handle">{{ card.owner_handle }}</span>
        <span v-else class="c-faint">暂无负责人</span>
        <span class="panel-card__sep">·</span>
        <span>{{ relTime(card.updated_at) }}</span>
        <a
          v-if="card.card?.pr_number && card.card?.pr_url"
          class="panel-card__pr"
          :href="card.card.pr_url"
          target="_blank"
          rel="noopener noreferrer"
          >PR #{{ card.card.pr_number }}</a
        >
      </div>

      <!-- 简报和结论是卡自己的两列，不是对话里的两条消息：派它出去时说的那份要求，
           和它交回来的那句话。放在对话上面，因为读一张卡的顺序就是「要它做什么 →
           它说做完了什么 → 过程」。 -->
      <div v-if="card.brief" class="panel-card__block">
        <div class="panel-card__block-head t-meta">简报</div>
        <div class="panel-card__block-body card-markdown t-body" v-html="renderMarkdown(card.brief)" />
      </div>
      <div v-if="card.conclusion" class="panel-card__block" data-testid="card-conclusion">
        <div class="panel-card__block-head t-meta">结论</div>
        <div class="panel-card__block-body card-markdown t-body" v-html="renderMarkdown(card.conclusion)" />
      </div>

      <div ref="timelineRef" class="panel-card__timeline">
        <div v-if="!said.length" class="px-1 py-2 t-meta c-muted">这条活还没有人说过话。</div>
        <div v-for="b in said" :key="b.id" class="card-msg">
          <span class="card-msg__who t-meta">{{ b.author }}</span>
          <div
            v-if="b.author_type === 'ai'"
            class="card-msg__text card-markdown t-body"
            v-html="renderMarkdown(b.content)"
          />
          <span v-else class="card-msg__text t-body">{{ b.content }}</span>
        </div>
      </div>

      <!-- 卡下能说话，但说出去的话不是直接给分身的：做这条活的分身住在房间的会话
           里，人够不着它。落在这里，房间的芝士被叫来转达。 -->
      <form class="panel-card__say" @submit.prevent="send">
        <input
          v-model="draft"
          class="panel-card__input t-body"
          type="text"
          placeholder="在这条活下面说点什么（房间会转达给它）"
          :disabled="sending"
        />
        <button type="submit" class="panel-card__send t-meta" :disabled="sending || !draft.trim()">发送</button>
      </form>
    </template>
  </section>
</template>

<style scoped>
.panel-card {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
  padding: 8px 12px 10px;
}
.panel-card__head {
  display: flex;
  align-items: center;
}
.panel-card__back {
  display: flex;
  align-items: center;
  gap: 2px;
  color: var(--muted);
  cursor: pointer;
}
.panel-card__back:hover {
  color: var(--ink);
}
.panel-card__title {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding-top: 6px;
}
.panel-card__name {
  color: var(--ink);
  font-weight: 600;
}
.panel-card__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 2px 0 8px 18px;
  color: var(--muted);
}
.panel-card__sep {
  color: var(--faint);
}
.panel-card__pr {
  margin-left: auto;
  color: var(--muted);
}
.panel-card__pr:hover {
  color: var(--ink);
  text-decoration: underline;
}
.panel-card__block {
  border-top: 1px solid var(--line);
  padding: 8px 0;
}
.panel-card__block-head {
  color: var(--faint);
  padding-bottom: 2px;
}
.panel-card__block-body {
  color: var(--ink);
  white-space: pre-wrap;
  /* 简报可以是几千字：块内自滚，别把整个面板撑到时间线不可达 */
  max-height: 40vh;
  overflow-y: auto;
}
.panel-card__timeline {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  border-top: 1px solid var(--line);
  padding-top: 6px;
}
.card-msg {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 4px 0;
}
.card-msg__who {
  color: var(--faint);
}
.card-msg__text {
  color: var(--ink);
  white-space: pre-wrap;
}
.card-markdown {
  white-space: normal;
  overflow-wrap: anywhere;
}
.card-markdown :deep(p) {
  margin: 0 0 8px;
}
.card-markdown :deep(ul),
.card-markdown :deep(ol) {
  padding-left: 20px;
  margin: 4px 0;
}
.card-markdown :deep(a) {
  color: var(--ink);
  text-decoration: underline;
}
.card-markdown :deep(pre),
.card-markdown :deep(table) {
  max-width: 100%;
  overflow-x: auto;
}
.card-markdown :deep(table) {
  display: block;
  border-collapse: collapse;
}
.card-markdown :deep(th),
.card-markdown :deep(td) {
  padding: 4px 8px;
  border: 1px solid var(--line);
}
.card-markdown :deep(pre) {
  padding: 8px;
  background: var(--fill);
  white-space: pre;
}
.card-markdown :deep(img) {
  max-width: 100%;
  height: auto;
}
.panel-card__say {
  display: flex;
  gap: 6px;
  padding-top: 8px;
}
.panel-card__input {
  flex: 1 1 auto;
  min-width: 0;
  padding: 6px 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--bg);
  color: var(--ink);
}
.panel-card__send {
  flex: none;
  padding: 0 10px;
  border-radius: var(--radius-md);
  color: var(--muted);
  cursor: pointer;
}
.panel-card__send:disabled {
  cursor: default;
  color: var(--faint);
}
.panel-card__send:not(:disabled):hover {
  color: var(--ink);
}
.board-dot {
  flex: 0 0 auto;
  width: 10px;
  height: 10px;
  margin-top: 5px;
  border-radius: 50%;
  border: 2px solid var(--faint);
}
</style>
