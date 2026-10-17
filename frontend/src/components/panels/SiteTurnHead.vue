<script setup lang="ts">
// 一轮的组头：这一轮从什么时候开始、几步、多久，底下一根细条写这一轮的时间花在了
// 哪几段（排队、等环境、重试、干活）。悬停细条看每段多久。
//
// 触发这一轮的那句话在对话栏，现场读不到它（人写的块不带 turn_id），所以这里不写
// 标题。名字只在「全部」下面、且这一排有两个以上队友时才写 —— 交错的两段没有名字
// 就分不开；只有一个队友时名字是多余的。
import type { Block } from '../../cx_types'
import type { SiteTurn } from '../../lib/siteLog'
import type { Phase, PhaseSpan } from '../../lib/sitePhases'

import { computed } from 'vue'

import { formatSpan } from '../../lib/siteLog'
import { turnPhases } from '../../lib/sitePhases'

import { t } from '@/i18n'
import { clockTime } from '@/lib/dateText'

const props = defineProps<{
  turn: SiteTurn<Block>
  /** 组头上署谁，不署就是 null（只看一个队友、或者房间里只有一个）。 */
  who: string | null
  /** 这一组还在跑吗：它的轮次在对话栏听到的在跑的轮次里。 */
  live: boolean
  /** 这一轮开始的时刻（毫秒），对话栏从 socket 上记的；不知道就是 undefined。 */
  startsAtMs?: number
}>()

// 每一轮顶上那根细条（lib/sitePhases）：分段和悬停时那一句。
const PHASE_LABEL: Record<Phase, string> = {
  queue: 'work.room.site.phase.queue',
  environment: 'work.room.site.phase.environment',
  retry: 'work.room.site.phase.retry',
  work: 'work.room.site.phase.work',
}

const phases = computed<PhaseSpan[]>(() => turnPhases(props.turn.entries, props.startsAtMs))
const phasesLabel = computed(() =>
  phases.value
    .map((p) => t('work.room.site.phase.span', { phase: t(PHASE_LABEL[p.phase]), span: formatSpan(p.seconds) }))
    .join(t('work.room.site.phase.sep'))
)

function fmtTime(iso: string): string {
  // Local 24-hour HH:mm, not raw UTC slice, and not the browser's default locale.
  return clockTime(new Date(iso).getTime())
}
</script>

<template>
  <!-- When this turn started, how many steps, how long it took. -->
  <div class="turn__head">
    <span v-if="who" class="turn__who" data-testid="turn-who">{{ who }}</span>
    <span class="turn__time">{{ fmtTime(turn.startedAt) }}</span>
    <span v-if="live" class="turn__live">
      <i class="turn__pulse" />
      {{ t('work.room.site.live') }}
    </span>
    <span class="turn__meta">
      {{ t('work.room.site.steps', { count: turn.steps })
      }}<template v-if="turn.seconds > 0"> · {{ formatSpan(turn.seconds) }}</template>
    </span>
  </div>
  <!-- Where this turn's time went, as a bar. Hover it for each span. -->
  <div
    v-if="phases.length"
    class="turn__phases"
    role="img"
    :aria-label="phasesLabel"
    :title="phasesLabel"
    data-testid="turn-phases"
  >
    <span
      v-for="p in phases"
      :key="p.phase"
      class="turn__phase"
      :class="`turn__phase--${p.phase}`"
      :style="{ flexGrow: p.seconds }"
    />
  </div>
</template>

<style scoped>
.turn__who {
  font-weight: 600;
  color: var(--ink);
}
.turn__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--faint);
}
.turn__phases {
  display: flex;
  gap: 2px;
  height: 4px;
  margin: -2px 0 8px;
}
.turn__phase {
  flex-basis: 0;
  min-width: 4px;
  border-radius: var(--radius-pill);
  background: var(--line-2);
}
.turn__phase--environment {
  background: var(--faint);
}
.turn__phase--retry {
  background: var(--warn);
}
.turn__phase--work {
  background: var(--muted);
}
.turn__time {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
/* 「3 步 · 2 分钟」里有中文，不走等宽：中文没有等宽字形，会掉到别的字体上。数字
   靠 tabular-nums 对齐就够了。 */
.turn__meta {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}
.turn__live {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--ok-ink);
}
.turn__pulse {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-pill);
  background: var(--ok);
  animation: site-breathe 1.6s ease-in-out infinite;
}
/* 全局的减弱动效兜底会把时长压到 0.001ms，1.6s 的呼吸就变成频闪 —— 比不动更
   糟，所以这里自己关掉。关掉之后那个点还在，「进行中」三个字也还在。 */
@media (prefers-reduced-motion: reduce) {
  .turn__pulse {
    animation: none;
  }
}
@keyframes site-breathe {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}
</style>
