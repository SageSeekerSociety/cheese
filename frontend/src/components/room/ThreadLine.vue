<script setup lang="ts">
// 主线上一条消息下面的那一行：谁在支线里说过话（头像）、有几条回复、最后一句是谁说的
// 什么（最多两行）、AI 队友此刻是不是正在里面回答，以及它变成了哪件任务、那件任务
// 到了哪一档。点它打开支线。
import type { Block } from '../../cx_types'
import type { RefNames } from '../../lib/refChip'
import type { ProgressLevel } from '../../lib/taskProgress'
import type { ThreadSummary } from '../../types/threads'

import { computed } from 'vue'

import { isAgentHandle } from '../../lib/authorship'
import { replySnippet } from '../../lib/blockDisplay'
import { progressLabel } from '../../lib/taskProgress'
import CheeseAvatar from '../CheeseAvatar.vue'
import UserAvatar from '../common/UserAvatar.vue'

import { t } from '@/i18n'

/** 头像最多叠几个；再多的人从回复数和支线里看。 */
const FACES = 3

const props = defineProps<{
  summary: ThreadSummary | null
  /** 正在回复的队友叫什么；没有人在回复时为 null。 */
  replying: string | null
  /** 正在回复的那位此刻在等什么（排队、重试……）；没有就是 null。 */
  status?: string | null
  refs: RefNames
  nameOf: (handle: string) => string
  /** 最后一条回复的时间，已经按房间的写法格式化好。 */
  time: string | null
  /** 一个人的头像图；没有就画首字母。 */
  avatarOf?: (handle: string) => string | null
  /** 支线变成的那件任务此刻到哪一档；不认得就是 null。 */
  taskLevel?: (taskId: string) => ProgressLevel | null
}>()

const emit = defineEmits<{ (e: 'open'): void }>()

const replies = computed(() => props.summary?.reply_count ?? 0)
const faces = computed(() => (props.summary?.participants ?? []).slice(0, FACES))
const task = computed(() => props.summary?.task ?? null)
// 一轮出错、还没有任何回复的支线：那句为什么没回答在支线里，这一行是去看它的入口。
const failed = computed(() => !props.replying && !!props.summary?.failed)
const level = computed(() => (task.value ? props.taskLevel?.(task.value.id) ?? null : null))

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
    v-if="replies > 0 || replying || task || failed"
    type="button"
    class="thread-line"
    :data-testid="replies > 0 ? 'thread-line' : replying ? 'thread-replying' : failed ? 'thread-failed' : 'thread-line'"
    @click="emit('open')"
  >
    <span class="thread-line__head">
      <span v-if="faces.length" class="thread-line__faces" aria-hidden="true">
        <template v-for="handle in faces" :key="handle">
          <CheeseAvatar
            v-if="isAgentHandle(handle)"
            class="thread-line__face"
            :size="18"
            :name="nameOf(handle)"
            :handle="handle"
          />
          <!-- 人走 UserAvatar：挑过就画那张，没挑过/取不到就画按 handle 派生的彩色
               首字母，失败记忆也归它一处管。以前这里自画一份（还漏了 @error，图裂了
               就是一张破图），和别处对不上。 -->
          <UserAvatar
            v-else
            class="thread-line__face"
            :avatar="avatarOf?.(handle) ?? ''"
            :name="nameOf(handle)"
            :seed="handle"
            :size="18"
          />
        </template>
      </span>
      <v-icon v-else size="14" class="thread-line__icon">mdi-forum-outline</v-icon>
      <span v-if="replies > 0" class="thread-line__count">{{ t('work.room.thread.replies', { count: replies }) }}</span>
      <span v-if="replies > 0 && time" class="thread-line__time t-meta">{{ time }}</span>
      <span v-if="failed" class="thread-line__failed">{{ t('work.room.thread.failed') }}</span>
      <template v-if="replying">
        <span class="thread-line__replying">{{
          status
            ? t('work.room.thread.replyingStatus', { name: replying, status })
            : t('work.room.thread.replying', { name: replying })
        }}</span>
        <span class="thread-line__dots" aria-hidden="true"><span /><span /><span /></span>
      </template>
    </span>
    <span v-if="last" class="thread-line__last">{{ last }}</span>
    <span v-if="task" class="thread-line__task" data-testid="thread-task">
      <v-icon size="12" aria-hidden="true">mdi-call-split</v-icon>
      <span class="thread-line__task-title">{{ t('work.room.thread.becameTask', { title: task.title }) }}</span>
      <span v-if="level" class="thread-line__level" :data-level="level">{{ progressLabel(level) }}</span>
    </span>
  </button>
</template>

<style scoped>
.thread-line__failed {
  color: var(--danger-ink);
}
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
.thread-line__faces {
  display: inline-flex;
  align-items: center;
}
.thread-line__face {
  flex: none;
  box-shadow: 0 0 0 2px var(--fill);
}
.thread-line__face + .thread-line__face {
  margin-left: -5px;
}
/* 人这一支已经交给 UserAvatar（它自带圆形首字母样式），--person 这一份尺寸/字重
   的复刻没人用了，删掉，免得留着一份会漂移的样式。 */
.thread-line__task {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  max-width: 100%;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}
.thread-line__task-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.thread-line__level {
  flex: none;
}
.thread-line__level[data-level='review'],
.thread-line__level[data-level='waiting'] {
  color: var(--accent-ink);
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
