<script setup lang="ts">
// 录音时输入框下面那一行：✕ · 计时 · 声波 · ✓。它和动作行占同一个位置，录音一开始
// 两行交换（外面做过渡），所以人的视线不用离开输入框。
//
// ✓ 站在发送键的位置、也是这一行唯一的琥珀块：录音时发不了消息，「说完了」就是这
// 一刻让事情往下走的那一个（设计系统 §1.6）。
//
// 只吃 props、只往上发事件：录不录、发不发，都在 useVoiceInput。
import type { VoiceState } from '@/composables/useVoiceInput'

import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  state: VoiceState
  elapsed: number
  levels: number[]
  maxSeconds: number
}>()

const emit = defineEmits<{
  (e: 'cancel'): void
  (e: 'done'): void
}>()

const thinking = computed(() => props.state === 'thinking')
const left = computed(() => Math.max(0, Math.ceil(props.maxSeconds - props.elapsed)))
// 最后十秒换成倒数：到点会自己结束，人得提前知道。
const ending = computed(() => !thinking.value && left.value <= 10)
const clock = computed(() => {
  if (thinking.value) return t('work.room.composer.voice.thinking')
  if (ending.value) return t('work.room.composer.voice.left', { seconds: left.value })
  const s = Math.floor(props.elapsed)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
})
</script>

<template>
  <div
    class="voice-bar"
    :class="{ 'voice-bar--thinking': thinking }"
    role="group"
    :aria-label="t('work.room.composer.voice.recording')"
  >
    <BaseButton
      kind="ghost"
      class="voice-bar__icon"
      icon="mdi-close"
      size="sm"
      :title="t('work.room.composer.voice.cancel')"
      :aria-label="t('work.room.composer.voice.cancel')"
      @click="emit('cancel')"
    />
    <span class="voice-bar__dot" aria-hidden="true" />
    <span class="voice-bar__clock" :class="{ 'voice-bar__clock--ending': ending }" aria-live="polite">{{ clock }}</span>
    <!-- 声波：最近几秒的音量，从右往左走。识别中收成一条细线。 -->
    <div class="voice-bar__wave" aria-hidden="true">
      <i
        v-for="(level, i) in levels"
        :key="i"
        :style="{ transform: `scaleY(${thinking ? 0.12 : 0.12 + level * 0.88})` }"
      />
    </div>
    <button
      type="button"
      class="voice-bar__done"
      :disabled="thinking"
      :title="t('work.room.composer.voice.done')"
      :aria-label="t('work.room.composer.voice.done')"
      @click="emit('done')"
    >
      <span v-if="thinking" class="voice-bar__spin" aria-hidden="true" />
      <v-icon v-else size="18">mdi-check</v-icon>
    </button>
  </div>
</template>

<style scoped>
.voice-bar {
  display: flex;
  align-items: center;
  gap: 4px;
  min-height: 36px;
}
.voice-bar__icon {
  width: 28px;
  height: 28px;
}
/* 正在录：一颗呼吸的红点。识别中它不再亮——那时候已经不在收音了。 */
.voice-bar__dot {
  flex: none;
  width: 8px;
  height: 8px;
  margin: 0 2px 0 4px;
  border-radius: 50%;
  background: var(--danger);
  animation: voice-breathe 1.6s var(--ease-standard) infinite;
  transition: opacity var(--dur-quick) var(--ease-standard);
}
.voice-bar--thinking .voice-bar__dot {
  opacity: 0;
  animation: none;
}
.voice-bar__clock {
  min-width: 36px;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  color: var(--muted);
  white-space: nowrap;
  transition: color var(--dur-quick) var(--ease-standard);
}
.voice-bar__clock--ending {
  color: var(--warn-ink);
}
.voice-bar__wave {
  flex: 1;
  min-width: 0;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 6px;
  overflow: hidden;
}
/* 每根条只变 scaleY：不碰布局，一帧几十根也不重排。 */
.voice-bar__wave i {
  flex: none;
  width: 3px;
  height: 100%;
  border-radius: 2px;
  background: var(--muted);
  transform-origin: center;
  transition:
    transform 90ms linear,
    background-color var(--dur-base) var(--ease-standard);
}
.voice-bar--thinking .voice-bar__wave i {
  background: var(--faint);
  transition:
    transform var(--dur-base) var(--ease-out),
    background-color var(--dur-base) var(--ease-standard);
}
.voice-bar__done {
  position: relative;
  flex: none;
  display: inline-grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: var(--radius-md);
  background: var(--accent);
  color: var(--accent-on);
  cursor: pointer;
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    transform var(--dur-press) var(--ease-standard);
}
.voice-bar__done:hover:not(:disabled) {
  background: var(--accent-press);
}
.voice-bar__done:active:not(:disabled) {
  transform: scale(0.92);
}
.voice-bar__done:disabled {
  cursor: default;
}
.voice-bar__spin {
  width: 14px;
  height: 14px;
  border: 2px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: voice-spin 0.7s linear infinite;
}
@keyframes voice-breathe {
  50% {
    opacity: 0.35;
  }
}
@keyframes voice-spin {
  to {
    transform: rotate(360deg);
  }
}
/* 关掉动画之后意思还在：红点照样是实心的，转圈换成静止的半圈。 */
@media (prefers-reduced-motion: reduce) {
  .voice-bar__dot,
  .voice-bar__spin {
    animation: none;
  }
}
@media (pointer: coarse) {
  .voice-bar {
    min-height: 44px;
  }
  .voice-bar__done::before {
    content: '';
    position: absolute;
    top: 50%;
    left: 50%;
    width: 44px;
    height: 44px;
    transform: translate(-50%, -50%);
  }
}
</style>
