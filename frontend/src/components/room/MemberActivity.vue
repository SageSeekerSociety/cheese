<script setup lang="ts">
// 「Cedar 正在工作… / Alice 正在输入…」那一行。房间自己没有状态，有的是成员在做
// 什么，所以这里说的永远是某一位成员：在打字的人、有一轮在跑的 AI 队友。
//
// 和 Slack 一样贴在输入框正下方，小字一行；打字的人合成一句（一位 / 两位 / 好几
// 位），在干活的队友各占一句，带上它此刻在做的那一步和干了多久。只凭 props 画。
//
// 读屏那一句和眼睛看的那一句分开：眼睛看的那截带着「执行命令 pnpm test」这样从
// live 帧来的当前一步、还有每秒往前走的用时 —— 帧一来就变、秒一秒地变，全塞进
// aria-live 会把读屏的人淹掉。所以屏幕上那截 aria-hidden，另外留一句只有谁、在
// 哪一相（思考中/在干活），它才变。
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

/** 多久没有新输出就算卡住，另说一句「已 N 无新输出」。纯提示，不做任何事。 */
const STALL_MS = 60_000

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

const separator = computed(() => t('work.room.activity.separator'))

/** 谁、此刻那一步 —— 会变长的那一截，用省略号收在它自己身上。 */
function workingHead(line: MemberActivityLine): string {
  const parts = [t('work.room.activity.working', { name: line.name })]
  // 有从 live 帧读来的当前一步，就用它顶掉时间线上那一个相（「思考中」）—— 它更
  // 具体，而且一次长工具调用期间时间线上根本不长新行，那个相早就是旧的了。
  const step = line.step ?? line.detail
  if (step) parts.push(step)
  return parts.join(separator.value)
}

/** 干了多久。单独一格，长的那一截被省略号截掉时它还在。 */
function workingTotal(line: MemberActivityLine): string | null {
  if (!line.since) return null
  return formatSpan(Math.max(0, Math.floor((now.value - line.since * 1000) / 1000)))
}

/** 多久没有新输出；还没到、或从没收过帧就不说。 */
function stallText(line: MemberActivityLine): string | null {
  if (!line.lastFrameAt) return null
  const quiet = Math.floor((now.value - line.lastFrameAt) / 1000)
  if (quiet * 1000 < STALL_MS) return null
  return t('work.room.activity.stalled', { span: formatSpan(quiet) })
}

// 读屏听到的那一句：只有谁、在哪一相，不带当前一步、不带秒数 —— 帧和秒都不进
// 这一句，它才只在相变时变。和 ChatTimeline 的 faceStatus 是同一个道理。
const workingAria = computed(() =>
  working.value
    .map((line) => {
      const parts = [t('work.room.activity.working', { name: line.name })]
      if (line.detail) parts.push(line.detail)
      return parts.join(separator.value)
    })
    .join(separator.value)
)
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
      <span class="status-dot status-dot--ok member-activity__dot" aria-hidden="true" />
      <!-- The step and the elapsed seconds churn on their own; keep both out of the live region. -->
      <span class="member-activity__text" aria-hidden="true">{{ workingHead(line) }}</span>
      <span v-if="workingTotal(line)" class="member-activity__meta" aria-hidden="true"
        >{{ separator }}{{ workingTotal(line) }}</span
      >
      <span v-if="stallText(line)" class="member-activity__stall" aria-hidden="true"
        >{{ separator }}{{ stallText(line) }}</span
      >
    </div>
    <!-- What a screen reader hears: who and which phase only, so it changes on the phase, not per frame. -->
    <p v-if="workingAria" class="visually-hidden">{{ workingAria }}</p>
    <div v-if="typingText" class="member-activity__line" data-kind="typing">{{ typingText }}</div>
  </div>
</template>

<style scoped>
/* 一行小字，不是一块面板：没有底色、没有边框，和输入框下沿之间只隔一点点。 */
/* flex: none 和下面每一行的 flex: none 都不能省。放它的那一列（ChatPanel）是 flex 列，
   时间线 flex-grow-1 但不禁收缩，内容一长，列里每一块按自己的高度比例往回收；
   这里的 min-height（reserve）又顶掉了 min-height: auto，于是整块被压到一行高，
   而每一行 overflow: hidden，最小高度是 0，两三位队友的行就挤成一行叠在一起。 */
.member-activity {
  flex: none;
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
  flex: none;
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
/* 当前一步可能很长：截断在它自己身上，右边的「无新输出」才不会被挤下去。 */
.member-activity__text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 用时和「无新输出」都不参与截断：上面那截再长，这两个数也还在。 */
.member-activity__meta,
.member-activity__stall {
  flex: none;
}
/* 比这一行的其余部分再淡一档：它是元信息（多久没有新输出），不是又一件在做的事。 */
.member-activity__stall {
  color: var(--faint);
}
</style>
