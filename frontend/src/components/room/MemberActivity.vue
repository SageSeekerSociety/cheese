<script setup lang="ts">
// 「Cedar 正在工作… / Alice 正在输入…」那一行。房间自己没有状态，有的是成员在做
// 什么，所以这里说的永远是某一位成员：在打字的人、有一轮在跑的 AI 队友。
//
// 和 Slack 一样贴在输入框正下方，小字一行；打字的人合成一句（一位 / 两位 / 好几
// 位），在干活的队友各占一句，带上它此刻在做的那一步和干了多久。只凭 props 画。
import type { MemberActivityLine } from '@/lib/memberActivity'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { t } from '@/i18n'
import { formatSpan } from '@/lib/siteLog'

const props = withDefaults(
  defineProps<{
    lines: MemberActivityLine[]
    /** 没人在忙时也占住一行的高度：输入框下面这一行出现、消失时，输入框不跟着跳。 */
    reserve?: boolean
  }>(),
  { reserve: false }
)

const working = computed(() => props.lines.filter((l) => l.kind === 'working'))
const typing = computed(() => props.lines.filter((l) => l.kind === 'typing'))

const typingText = computed(() => {
  const names = typing.value.map((l) => l.name)
  if (names.length === 0) return ''
  if (names.length === 1) return t('work.room.activity.typingOne', { name: names[0] })
  if (names.length === 2) return t('work.room.activity.typingTwo', { first: names[0], second: names[1] })
  return t('work.room.activity.typingMany')
})

// 干了多久每秒走一格；没有队友在干活时不必每秒重画。
const now = ref(Date.now())
let ticker: ReturnType<typeof setInterval> | undefined
watch(
  () => working.value.length > 0,
  (on) => {
    clearInterval(ticker)
    now.value = Date.now()
    ticker = on ? setInterval(() => (now.value = Date.now()), 1000) : undefined
  },
  { immediate: true }
)
onBeforeUnmount(() => clearInterval(ticker))

function workingText(line: MemberActivityLine): string {
  const parts = [t('work.room.activity.working', { name: line.name })]
  if (line.detail) parts.push(line.detail)
  if (line.since) parts.push(formatSpan(Math.max(0, Math.floor((now.value - line.since * 1000) / 1000))))
  return parts.join(t('work.room.activity.separator'))
}
</script>

<template>
  <div
    v-if="reserve || lines.length > 0"
    class="member-activity"
    :class="{ 'member-activity--reserve': reserve }"
    data-testid="member-activity"
    aria-live="polite"
  >
    <div v-for="line in working" :key="line.handle" class="member-activity__line" data-kind="working">
      <span class="status-dot status-dot--ok member-activity__dot" aria-hidden="true" />{{ workingText(line) }}
    </div>
    <div v-if="typingText" class="member-activity__line" data-kind="typing">{{ typingText }}</div>
  </div>
</template>

<style scoped>
/* 一行小字，不是一块面板：没有底色、没有边框，和输入框下沿之间只隔一点点。 */
.member-activity {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 2px 12px 4px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.member-activity--reserve {
  min-height: calc(var(--lh-13) + 6px);
}
.member-activity__line {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.member-activity__dot {
  flex: none;
}
</style>
