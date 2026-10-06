<script setup lang="ts">
// 主线上一条消息下面的那一行：它的支线里有几条回复、最后一句是谁说的什么（最多两
// 行）。点它打开支线。支线还没有回复、而这条消息叫了 AI 队友时，写「芝士 正在回复」：
// 回答不在主线上，这一行告诉人去哪儿等。
import type { Block } from '../../cx_types'
import type { RefNames } from '../../lib/refChip'
import type { ThreadSummary } from '../../types/threads'

import { computed } from 'vue'

import { replySnippet } from '../../lib/blockDisplay'

import { t } from '@/i18n'

const props = defineProps<{
  summary: ThreadSummary | null
  /** 正在回复的队友叫什么；没有人在回复时为 null。 */
  replying: string | null
  refs: RefNames
  nameOf: (handle: string) => string
  /** 最后一条回复的时间，已经按房间的写法格式化好。 */
  time: string | null
}>()

const emit = defineEmits<{ (e: 'open'): void }>()

const last = computed(() => {
  const reply = props.summary?.last_reply
  if (!reply) return null
  const block = {
    id: '',
    conversation_id: '',
    kind: 'message',
    author_type: 'participant',
    author: reply.author,
    content: reply.content,
    created_at: reply.created_at,
  } as Block
  return t('work.room.thread.lastReply', {
    name: props.nameOf(reply.author),
    text: replySnippet(block, props.refs, 200),
  })
})
</script>

<template>
  <button
    v-if="summary && summary.reply_count > 0"
    type="button"
    class="thread-line"
    data-testid="thread-line"
    @click="emit('open')"
  >
    <span class="thread-line__head">
      <v-icon size="14" class="thread-line__icon">mdi-forum-outline</v-icon>
      <span class="thread-line__count">{{ t('work.room.thread.replies', { count: summary.reply_count }) }}</span>
      <span v-if="time" class="thread-line__time t-meta">{{ time }}</span>
    </span>
    <span v-if="last" class="thread-line__last">{{ last }}</span>
  </button>
  <button v-else-if="replying" type="button" class="thread-line" data-testid="thread-replying" @click="emit('open')">
    <span class="thread-line__head">
      <v-icon size="14" class="thread-line__icon">mdi-forum-outline</v-icon>
      <span class="thread-line__replying">{{ t('work.room.thread.replying', { name: replying }) }}</span>
      <span class="thread-line__dots" aria-hidden="true"><span /><span /><span /></span>
    </span>
  </button>
</template>

<style scoped>
.thread-line {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  width: 100%;
  max-width: 480px;
  margin-top: 6px;
  padding: 6px 10px;
  border: 0;
  border-radius: var(--radius-md);
  background: var(--fill);
  color: var(--text);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.thread-line:hover {
  background: var(--line-2);
}
.thread-line__head {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  line-height: var(--lh-13);
}
.thread-line__icon {
  color: var(--muted);
}
.thread-line__count {
  font-weight: 600;
  color: var(--ink);
}
.thread-line__time,
.thread-line__replying {
  color: var(--muted);
}
.thread-line__last {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  overflow-wrap: anywhere;
}
.thread-line__dots {
  display: inline-flex;
  gap: 3px;
}
.thread-line__dots span {
  width: 4px;
  height: 4px;
  border-radius: var(--radius-pill);
  background: var(--muted);
}
@media (prefers-reduced-motion: no-preference) {
  .thread-line__dots span {
    animation: thread-dot 1.2s var(--ease-standard) infinite;
  }
  .thread-line__dots span:nth-child(2) {
    animation-delay: 0.2s;
  }
  .thread-line__dots span:nth-child(3) {
    animation-delay: 0.4s;
  }
}
@keyframes thread-dot {
  0%,
  80%,
  100% {
    opacity: 0.3;
  }
  40% {
    opacity: 1;
  }
}
</style>
