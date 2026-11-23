<script setup lang="ts">
// What a surface of the task page says about the task's environment, in that surface's own place
// (the 文件 panel's strip, the 搜索 column, a 终端 tab): every surface says it with this, so each
// state reads the same wherever it is met.
//
// - `preparing`: a progress mark and 「正在准备环境」; once it has waited `WAITED_AFTER_MS`, how
//   long it has waited, so a long bring-up does not read as a stuck one.
// - `failed` / `unanswered`: one line saying what happened, and 「重试」.
//
// `message` replaces the line for a surface that failed on its own account (a terminal whose
// socket could not be reached); `detail` is what the backend said, kept to the hover title.
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    state: 'preparing' | 'failed' | 'unanswered'
    /** When the wait started (ms since the epoch): how long it has waited is said from it. */
    since?: number | null
    /** The line said instead of the state's own. */
    message?: string
    /** What the backend said, on hover. */
    detail?: string
    /** No 「重试」: asking again would answer the same. */
    final?: boolean
    /** Centred in a surface that has nothing else to show (a terminal with no shell yet). */
    centered?: boolean
  }>(),
  { since: null, message: '', detail: '', final: false, centered: false }
)

const emit = defineEmits<{ retry: [] }>()

/** How long a wait goes before how long it has waited is said (ms). */
const WAITED_AFTER_MS = 8000

const now = ref(Date.now())
let ticking: ReturnType<typeof setInterval> | null = null
function stopTicking() {
  if (ticking) clearInterval(ticking)
  ticking = null
}
watch(
  () => [props.state, props.since] as const,
  ([state, since]) => {
    stopTicking()
    now.value = Date.now()
    if (state === 'preparing' && since !== null) ticking = setInterval(() => (now.value = Date.now()), 1000)
  },
  { immediate: true }
)
onBeforeUnmount(stopTicking)

const waited = computed(() => {
  if (props.state !== 'preparing' || props.since === null) return ''
  const ms = now.value - props.since
  if (ms < WAITED_AFTER_MS) return ''
  const total = Math.floor(ms / 1000)
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return minutes > 0
    ? t('work.room.environment.waitedMinutes', { minutes, seconds })
    : t('work.room.environment.waited', { seconds })
})

const line = computed(() => {
  if (props.message) return props.message
  if (props.state === 'preparing') return t('work.room.environment.preparing')
  return props.state === 'failed' ? t('work.room.environment.failed') : t('work.room.environment.unanswered')
})
</script>

<template>
  <div
    class="env-note"
    :class="[`env-note--${props.state}`, { 'env-note--centered': props.centered }]"
    :role="props.state === 'preparing' ? 'status' : 'alert'"
    :title="props.detail || undefined"
  >
    <v-progress-circular
      v-if="props.state === 'preparing'"
      class="env-note__spin"
      indeterminate
      size="14"
      width="2"
      aria-hidden="true"
    />
    <span class="env-note__line">{{ line }}</span>
    <span v-if="waited" class="env-note__waited">{{ waited }}</span>
    <BaseButton v-if="props.state !== 'preparing' && !props.final" kind="ghost" size="sm" @click="emit('retry')">
      {{ t('global.loadError.retry') }}
    </BaseButton>
  </div>
</template>

<style scoped>
.env-note {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
  min-width: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.env-note--centered {
  justify-content: center;
  text-align: center;
}
.env-note--failed .env-note__line {
  color: var(--danger-ink);
}
.env-note--unanswered .env-note__line {
  color: var(--text);
}
.env-note__line {
  min-width: 0;
}
.env-note__spin {
  flex: none;
  color: var(--muted);
}
@media (prefers-reduced-motion: reduce) {
  .env-note__spin :deep(.v-progress-circular__overlay),
  .env-note__spin :deep(svg) {
    animation: none;
  }
}
</style>
