<script setup lang="ts">
import type { Block } from '../cx_types'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps<{ events: Block[] }>()
const now = ref(Date.now())
const lifecycle = computed(() =>
  props.events
    .filter((event) => event.meta?.event_type === 'cloud_provisioning' || event.meta?.state === 'failed')
    .at(-1)
)
const finished = computed(() => ['ready', 'failed'].includes(String(lifecycle.value?.meta?.state)))
const latest = computed(() => props.events.at(-1)!)
const steps = computed(() => props.events.filter((event) => event.meta?.event_type === 'cloud_startup'))
const title = computed(() => {
  if (lifecycle.value?.meta?.state === 'ready') return '运行环境已就绪'
  if (lifecycle.value?.meta?.state === 'failed') return lifecycle.value.content
  return steps.value.at(-1)?.content || '正在准备运行环境'
})
const end = computed(() => (finished.value ? Date.parse(lifecycle.value!.created_at) : now.value))
function duration(start: string, until = end.value) {
  const seconds = Math.max(0, Math.floor((until - Date.parse(start)) / 1000))
  return seconds < 60 ? `${seconds}秒` : `${Math.floor(seconds / 60)}分${seconds % 60}秒`
}
function time(value: string) {
  return new Date(value).toLocaleTimeString('zh-CN', { hour12: false })
}
function stepEnd(index: number) {
  const next = props.events
    .slice(index + 1)
    .find(
      (event) => event.meta?.event_type === 'cloud_startup' || ['ready', 'failed'].includes(String(event.meta?.state))
    )
  return next ? Date.parse(next.created_at) : end.value
}
const quiet = computed(() => !finished.value && now.value - Date.parse(latest.value.created_at) >= 60000)
let timer: ReturnType<typeof setInterval> | undefined
watch(
  finished,
  (done) => {
    clearInterval(timer)
    if (!done)
      timer = setInterval(() => {
        now.value = Date.now()
      }, 1000)
  },
  { immediate: true }
)
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <details class="cloud-startup" data-testid="platform-notice">
    <summary>{{ title }} · {{ finished ? '共用时' : '已等待' }} {{ duration(events[0].created_at) }}</summary>
    <div class="cloud-startup-body">
      <p v-if="quiet">
        已 {{ duration(latest.created_at) }} 没有新的启动进度，最近一条进度记录：{{ time(latest.created_at) }}。
      </p>
      <ol aria-label="启动日志">
        <li v-for="(event, index) in events" :key="event.id">
          <time :datetime="event.created_at">{{ time(event.created_at) }}</time>
          <span
            >{{ event.content }}<small v-if="event.meta?.detail">{{ event.meta.detail }}</small></span
          >
          <span v-if="event.meta?.event_type === 'cloud_startup'" class="cloud-startup-duration">{{
            duration(event.created_at, stepEnd(index))
          }}</span>
        </li>
      </ol>
    </div>
  </details>
</template>

<style scoped>
/* 和它身边的事件行同一档：13px、--muted。它说的是运行环境在做什么，不是谁的话。 */
.cloud-startup {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

summary {
  cursor: pointer;
}

.cloud-startup-body {
  padding-top: 8px;
  margin-top: 8px;
  border-top: 1px solid var(--line);
}

ol {
  padding: 0;
  margin: 0;
  list-style: none;
}

li {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 4px 0;
}

time,
small,
.cloud-startup-duration {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

time,
.cloud-startup-duration {
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

small {
  display: block;
}

.cloud-startup-duration {
  margin-left: auto;
}

p {
  margin: 0 0 8px;
}
</style>
