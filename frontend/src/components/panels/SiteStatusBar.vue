<script setup lang="ts">
// 现场顶上那一行：此刻它在干什么、这一轮跑了多久、多久没动静了。说什么由
// lib/siteStatus 定，这里只管把它说出来和让时间走。
import type { Block } from '../../cx_types'
import type { SiteState } from '../../lib/siteStatus'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { formatSpan } from '../../lib/siteLog'
import { siteStatus } from '../../lib/siteStatus'
import { siteStatusLabel } from '../../lib/siteStatusLabel'

import UserRef from '@/components/common/UserRefLink.vue'
import { t } from '@/i18n'

const props = defineProps<{
  blocks: Block[]
  working: boolean
  turns: Record<string, number>
  /** 房间里不止一个队友时，这一行说的是哪一个：名字，和点它要去的 handle。 */
  agent?: string
  agentHandle?: string | null
}>()

// 一轮在跑的时候每秒走一格，「已用」和「最近活动」才是活的；闲着的时候没有要走的
// 秒数，也就不必每秒重画。
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined
// 发出消息到这一轮真正开始之间，后端还没给开始时间，就从这一刻算。
const workingSince = ref<number | null>(null)
watch(
  () => props.working,
  (on) => {
    now.value = Date.now()
    workingSince.value = on ? now.value : null
    clearInterval(timer)
    timer = on ? setInterval(() => (now.value = Date.now()), 1000) : undefined
  },
  { immediate: true }
)
onBeforeUnmount(() => clearInterval(timer))

const status = computed(() => siteStatus(props.blocks, props.working, props.turns))

const label = computed(() => siteStatusLabel(status.value))

const TONE: Record<SiteState, string> = {
  thinking: 'ok',
  acting: 'ok',
  retrying: 'warn',
  compacting: 'ok',
  waiting: 'warn',
  stopped: 'danger',
  idle: 'muted',
}

function seconds(from: number | null): number | null {
  return from === null ? null : Math.max(0, Math.floor((now.value - from) / 1000))
}

const elapsed = computed(() => (props.working ? seconds(status.value.startedAt ?? workingSince.value) : null))
// 「多久没动静了」是一轮在跑时判断它卡没卡住的读数，只在跑的时候说。闲着时秒表是
// 停的（见上面的计时器），说出来就是一个停在某一刻、却写着「前」的数：「最近活动
// 4 分 07 秒前」挂上三分钟不变，读起来是错的。闲着的时候那个词已经说了「空闲」。
// 刚有过动静的那几秒也不说：「最近活动 0 秒前」每秒一跳，说的却是「它在动」，而这
// 一点前面那个词已经说了。
const quiet = computed(() => {
  if (!props.working) return null
  const s = seconds(status.value.lastAt)
  return s !== null && s >= 5 ? s : null
})

// 一轮都没跑过、时间线也是空的：没有可说的，下面那句「暂无」就够了。
const shown = computed(() => props.working || status.value.lastAt !== null)
</script>

<template>
  <div v-if="shown" class="site-status" data-testid="site-status" :data-state="status.state">
    <i
      class="status-dot site-status__dot"
      :class="[`status-dot--${TONE[status.state]}`, { 'site-status__dot--live': working }]"
    />
    <span v-if="agent" class="site-status__agent"><UserRef :handle="agentHandle" :name="agent" /></span>
    <span class="site-status__state" aria-live="polite">{{ label }}</span>
    <span v-if="elapsed !== null" class="t-meta">{{
      t('work.room.site.status.elapsed', { span: formatSpan(elapsed) })
    }}</span>
    <span v-if="quiet !== null" class="t-meta site-status__quiet">{{
      t('work.room.site.status.quiet', { span: formatSpan(quiet) })
    }}</span>
  </div>
</template>

<style scoped>
/* 贴在这一栏的顶上：往上翻旧的记录时，它仍然说着此刻的事。 */
.site-status {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}
.site-status__agent {
  font-size: 13px;
}
.site-status__state {
  font-size: 13px;
  color: var(--text);
}
.site-status__quiet {
  margin-left: auto;
}
/* 在跑的时候圆点呼吸：这一格里唯一在动的东西，说的是「机器正在做事」。 */
.site-status__dot--live {
  animation: site-status-breathe 1.6s ease-in-out infinite;
}
/* 全局的减弱动效兜底会把 1.6s 压成频闪，所以自己关掉；关掉之后点和那个词都还在。 */
@media (prefers-reduced-motion: reduce) {
  .site-status__dot--live {
    animation: none;
  }
}
@keyframes site-status-breathe {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}
</style>
