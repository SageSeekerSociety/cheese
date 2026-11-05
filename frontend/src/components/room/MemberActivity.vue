<script setup lang="ts">
// 「Cedar 正在工作… / Alice 正在输入…」那一行。房间自己没有状态，有的是成员在做
// 什么，所以这里说的永远是某一位成员：在打字的人、有一轮在跑的 AI 队友。
//
// 坐在输入框上面自己的一格，小字一行，没人在忙时这一格不存在；打字的人合成一句（一位 / 两位 /
// 好几位），在干活的队友各占一句，带上它此刻在做的那一步和干了多久。调用模型出错、在等平台
// 自动重试的队友也占一句，同一个位置和字号，只换开头那颗点（黄）和那句话，不说几点重试。
// 只凭 props 画。
//
// 读屏那一句和眼睛看的那一句分开：眼睛看的那截带着「执行命令 pnpm test」这样从
// live 帧来的当前一步、还有每秒往前走的用时 —— 帧一来就变、秒一秒地变，全塞进
// aria-live 会把读屏的人淹掉。所以屏幕上那截 aria-hidden，另外留一句只有谁、在
// 哪一相（思考中/在干活），它才变。
import type { MemberActivityLine } from '@/lib/memberActivity'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { t } from '@/i18n'
import { formatSpan } from '@/lib/siteLog'

const props = defineProps<{ lines: MemberActivityLine[] }>()

// 在干活的和在等自动重试的：都是一位队友的这一轮，各占一行、上下对齐。
const working = computed(() => props.lines.filter((l) => l.kind !== 'typing'))
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

/** 上一轮留下的帧：比这一轮还早。live 帧那本账只在换房间时清，一轮结束后它还在，
 *  新一轮刚起来、还没吐出第一条时，上一轮收尾那一刻会串进来。 */
function fromAnEarlierTurn(line: MemberActivityLine): boolean {
  return line.lastFrameAt != null && line.lastFrameAt < line.since * 1000
}

/** 谁、此刻那一步 —— 会变长的那一截，用省略号收在它自己身上。 */
function workingHead(line: MemberActivityLine): string {
  if (line.kind === 'retrying') return t('work.room.activity.retrying', { name: line.name })
  const parts = [t('work.room.activity.working', { name: line.name })]
  // 有从 live 帧读来的当前一步，就用它顶掉时间线上那一个相（「思考中」）—— 它更
  // 具体，而且一次长工具调用期间时间线上根本不长新行，那个相早就是旧的了。
  // 上一轮留下的帧（比这一轮还早）不是这一轮的当前一步：那本账只在换房间时清，
  // 一轮结束后它还在，新一轮刚起来时会串进来。那时退回时间线上那一个相。
  const live = fromAnEarlierTurn(line) ? null : line.step
  const step = live ?? line.detail
  if (step) parts.push(step)
  return parts.join(separator.value)
}

/** 干了多久。单独一格，长的那一截被省略号截掉时它还在。 */
function workingTotal(line: MemberActivityLine): string | null {
  if (!line.since) return null
  return formatSpan(Math.max(0, Math.floor((now.value - line.since * 1000) / 1000)))
}

// 读屏听到的那一句：只有谁、在哪一相，不带当前一步、不带秒数 —— 帧和秒都不进
// 这一句，它才只在相变时变。和 ChatTimeline 的 faceStatus 是同一个道理。
const workingAria = computed(() =>
  working.value
    .map((line) => {
      if (line.kind === 'retrying') return t('work.room.activity.retrying', { name: line.name })
      const parts = [t('work.room.activity.working', { name: line.name })]
      if (line.detail) parts.push(line.detail)
      return parts.join(separator.value)
    })
    .join(separator.value)
)
</script>

<template>
  <div v-if="lines.length > 0" class="member-activity" data-testid="member-activity" aria-live="polite">
    <div v-for="line in working" :key="line.handle" class="member-activity__line" :data-kind="line.kind">
      <span
        class="status-dot member-activity__dot"
        :class="line.kind === 'retrying' ? 'status-dot--warn' : 'status-dot--ok'"
        aria-hidden="true"
      />
      <!-- The step and the elapsed seconds churn on their own; keep both out of the live region. -->
      <span class="member-activity__text" aria-hidden="true">{{ workingHead(line) }}</span>
      <span v-if="workingTotal(line)" class="member-activity__meta" aria-hidden="true"
        >{{ separator }}{{ workingTotal(line) }}</span
      >
    </div>
    <!-- What a screen reader hears: who and which phase only, so it changes on the phase, not per frame. -->
    <p v-if="workingAria" class="visually-hidden">{{ workingAria }}</p>
    <div v-if="typingText" class="member-activity__line" data-kind="typing">
      <span class="member-activity__dots" aria-hidden="true"><span /><span /><span /></span>{{ typingText }}
    </div>
  </div>
</template>

<style scoped>
/* 一行小字，不是一块面板：没有底色、没有边框。flex: none 和每一行的 flex: none 都
   不能省：放进 flex 列时，每一行 overflow: hidden、最小高度是 0，两三位队友的行会被
   挤成一行叠在一起。 */
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
/* 用时不参与截断：上面那截再长，这个数也还在。 */
.member-activity__meta {
  flex: none;
}
.member-activity__dots {
  flex: none;
  display: inline-flex;
  gap: 2px;
}
.member-activity__dots span {
  width: 4px;
  height: 4px;
  border-radius: var(--radius-pill);
  background: var(--faint);
}
@media (prefers-reduced-motion: no-preference) {
  .member-activity__dots span {
    animation: activity-dot 1.2s var(--ease-standard) infinite;
  }
  .member-activity__dots span:nth-child(2) {
    animation-delay: 0.2s;
  }
  .member-activity__dots span:nth-child(3) {
    animation-delay: 0.4s;
  }
}
@keyframes activity-dot {
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
