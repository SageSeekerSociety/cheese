<script setup lang="ts">
// 主线上一条消息下面的那一行：谁在支线里说过话（头像）、有几条回复、最后一句是谁说的
// 什么（最多两行），以及 AI 队友此刻是不是正在里面回答。点它打开支线。从这条消息出来
// 的任务列在它下面（ChatTimeline 的 TaskCard）。
import type { RefNames } from '../../lib/refChip'
import type { ThreadSummary } from '../../types/threads'

import { computed } from 'vue'

import { isAgentHandle } from '../../lib/authorship'
import { replyBlock, replySnippet } from '../../lib/blockDisplay'
import { cancelPrefetch, prefetchNow, prefetchOnHover } from '../../lib/routePrefetch'
import CheeseAvatar from '../CheeseAvatar.vue'
import UserAvatar from '../common/UserAvatar.vue'

import { t } from '@/i18n'

/** 头像最多叠几个；再多的人从回复数和支线里看。 */
const FACES = 3

const props = defineProps<{
  summary: ThreadSummary | null
  /** 正在回复的队友叫什么；没有人在回复时为 null。 */
  replying: string | null
  /** 正在回复的那几位（handle）。还没有回复时 `participants` 里只有发起的人，头像里得
   *  补上它们。 */
  replyingSeats?: string[]
  /** 正在回复的那位此刻在等什么（排队、重试……）；没有就是 null。 */
  status?: string | null
  refs: RefNames
  nameOf: (handle: string) => string
  /** 最后一条回复的时间，已经按房间的写法格式化好。 */
  time: string | null
  /** 一个人的头像图；没有就画首字母。 */
  avatarOf?: (handle: string) => string | null
}>()

const emit = defineEmits<{ (e: 'open'): void }>()

const replies = computed(() => props.summary?.reply_count ?? 0)
// 说过话的人按先后排；此刻在回复的队友一定在里面（排不下就让出后面的位置），否则
// 「芝士 正在回复」旁边画的是发起人的脸。
const faces = computed(() => {
  const answering = [...new Set(props.replyingSeats ?? [])].slice(0, FACES)
  const said = (props.summary?.participants ?? []).filter((handle) => !answering.includes(handle))
  const kept = new Set([...said.slice(0, FACES - answering.length), ...answering])
  return [...new Set([...(props.summary?.participants ?? []), ...answering])].filter((handle) => kept.has(handle))
})
// 一轮出错、还没有任何回复的支线：那句为什么没回答在支线里，这一行是去看它的入口。
const failed = computed(() => !props.replying && !!props.summary?.failed)
// 额度用完不是「回复失败」：重试没有用，主线上直接说额度用完了。
const failedText = computed(() =>
  props.summary?.failed_code === 'credits_exhausted'
    ? t('work.room.thread.failedCredits')
    : t('work.room.thread.failed')
)

// 点开之前先把支线取回来：页头那条消息和里面的回复，点下去就在（`query/room` 的
// `prefetchThread`）。指针停住、键盘移到这一行上才算想点；按下去就不再等。触屏上也在
// 按下时取——别处的列表不这么做，是怕手指滑过一整列都发请求，这一行只有一条支线，
// 同一时刻也只热一条。
function warm() {
  if (props.summary) prefetchOnHover({ threadId: props.summary.id })
}
function warmNow() {
  if (props.summary) prefetchNow({ threadId: props.summary.id })
}

const last = computed(() => {
  const reply = props.summary?.last_reply
  if (!reply) return null
  return t('work.room.thread.lastReply', {
    name: props.nameOf(reply.author),
    text: replySnippet(replyBlock(reply), props.refs, 200),
  })
})
</script>

<template>
  <button
    v-if="replies > 0 || replying || failed"
    type="button"
    class="thread-line"
    :data-testid="replies > 0 ? 'thread-line' : replying ? 'thread-replying' : failed ? 'thread-failed' : 'thread-line'"
    @pointerenter="warm"
    @pointerleave="cancelPrefetch"
    @focus="warm"
    @blur="cancelPrefetch"
    @pointerdown="warmNow"
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
      <span v-if="failed" class="thread-line__failed">{{ failedText }}</span>
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
  padding: 8px 12px;
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
