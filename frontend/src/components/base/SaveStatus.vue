<script setup lang="ts">
/**
 * 保存结果的就地回执：保存中… / 已保存 / 保存失败（docs/design-system.md §3.11）。
 *
 * 设置页里那一块内容一直在屏幕上，保存的结果就该留在它旁边 —— 一条几秒后消失的
 * toast，人回头已经看不见了。这三态由 `useSaveState` 给（`saving` / `saved` /
 * `error`），这里只负责画出来。
 *
 * - 保存中：`--muted` 的一行「保存中…」。
 * - 已保存：`--ok-ink` 的一行「已保存」，`useSaveState` 到点把它灭掉，这里淡出。
 * - 保存失败：`--danger-ink` 的一行「保存失败」，错误带了原因就用冒号接在后面。
 *
 * 读屏：成功/进行中用 `role="status"`（不打断），失败用 `role="alert"`（要出声）。
 *
 * 文案可被调用方覆盖（`savedText` / `failedText`），给那些已经有自己说法的地方用；
 * 不给就是全局的「已保存」/「保存失败」。
 */
import { computed } from 'vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    saving?: boolean
    saved?: boolean
    /** 失败原因；给空串或 null 时不显示失败态。 */
    error?: string | null
    /** 覆盖「已保存」。 */
    savedText?: string
    /** 覆盖「保存失败」（也用来判断原因是不是和它重复）。 */
    failedText?: string
  }>(),
  { saving: false, saved: false, error: null, savedText: undefined, failedText: undefined }
)

const kind = computed<'idle' | 'saving' | 'saved' | 'error'>(() => {
  if (props.error) return 'error'
  if (props.saving) return 'saving'
  if (props.saved) return 'saved'
  return 'idle'
})

const text = computed(() => {
  const failed = props.failedText ?? t('global.saveState.failed')
  if (kind.value === 'error') {
    const reason = props.error?.trim() ?? ''
    return reason && reason !== failed ? t('global.saveState.failedWith', { reason }) : failed
  }
  if (kind.value === 'saving') return t('global.saveState.saving')
  return props.savedText ?? t('global.saveState.saved')
})
</script>

<template>
  <Transition name="save-status">
    <span
      v-if="kind !== 'idle'"
      class="save-status"
      :class="`save-status--${kind}`"
      :role="kind === 'error' ? 'alert' : 'status'"
    >
      {{ text }}
    </span>
  </Transition>
</template>

<style scoped>
.save-status {
  display: inline-block;
  font-size: 13px;
  line-height: var(--lh-13);
}

.save-status--saved {
  color: var(--ok-ink);
}

.save-status--saving {
  color: var(--muted);
}

.save-status--error {
  color: var(--danger-ink);
}

/* 淡出，不推挤旁边的东西（§9.2 位置的变化要连续）。 */
.save-status-enter-active {
  transition: opacity var(--dur-base) var(--ease-out);
}

.save-status-leave-active {
  transition: opacity var(--dur-quick) var(--ease-in);
}

.save-status-enter-from,
.save-status-leave-to {
  opacity: 0;
}
</style>
