<script setup lang="ts">
// 录音时的那一条：✕、计时、声波。它从麦克风的位置往左展开成一整行，盖住「+」，停在
// 麦克风的右边缘；「说完了」是发送键原地变成的 ✓（ComposerActions），所以这一条里没有
// 它——按下和说完都在右下角这一块。
//
// 按住说话时，整条跟着手指往左挪（阻尼一半）；拖过一段就变成危险色浅底，松手就丢掉这段。
// 这时候头顶冒一句「松手结束，左滑取消」——只在按着的时候出现，松手就走。
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
  /** 按住说话中（手指还按着）。 */
  holding?: boolean
  /** 手指往左拖了多少像素（负数），整条跟着挪。 */
  dragX?: number
  /** 拖过线了：松手就丢掉这段。 */
  armed?: boolean
}>()

const emit = defineEmits<{
  (e: 'cancel'): void
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
const holdTip = computed(() =>
  props.armed ? t('work.room.composer.voice.releaseToDrop') : t('work.room.composer.voice.holdTip')
)
</script>

<template>
  <div
    class="voice-bar"
    :class="{
      'voice-bar--thinking': thinking,
      'voice-bar--holding': holding,
      'voice-bar--armed': armed,
    }"
    role="group"
    :aria-label="t('work.room.composer.voice.recording')"
  >
    <!-- 按着时头顶那一句：它在会被裁剪的那一块（__body）外面，展开、收回的 clip-path
         裁不到它。 -->
    <Transition name="voice-tip">
      <div v-if="holding && !thinking" class="voice-bar__tip" :class="{ 'voice-bar__tip--armed': armed }">
        <v-icon size="14">{{ armed ? 'mdi-close' : 'mdi-chevron-left' }}</v-icon
        >{{ holdTip }}
      </div>
    </Transition>
    <div class="voice-bar__body">
      <div
        class="voice-bar__track"
        :class="{ 'voice-bar__track--drag': !!dragX }"
        :style="{ translate: `${dragX ?? 0}px 0` }"
      >
        <BaseButton
          kind="ghost"
          class="voice-bar__icon voice-bar__part voice-bar__part--3"
          icon="mdi-close"
          size="sm"
          :title="t('work.room.composer.voice.cancel')"
          :aria-label="t('work.room.composer.voice.cancel')"
          @click="emit('cancel')"
        />
        <span class="voice-bar__dot voice-bar__part voice-bar__part--2" aria-hidden="true" />
        <span
          class="voice-bar__clock voice-bar__part voice-bar__part--2"
          :class="{ 'voice-bar__clock--ending': ending }"
          aria-live="polite"
          >{{ clock }}</span
        >
        <!-- 声波：最近几秒的音量，从右往左走。整理中压成一条线，上面走一道扫光。 -->
        <div class="voice-bar__wave voice-bar__part" aria-hidden="true">
          <i
            v-for="(level, i) in levels"
            :key="i"
            :style="{ transform: `scaleY(${thinking ? 0.12 : 0.12 + level * 0.88})` }"
          />
          <span class="voice-bar__sheen" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.voice-bar {
  position: relative;
  height: 100%;
  min-height: 28px;
}
/* 展开 / 收回的过渡在外面（RoomComposer 的 voice-grow 裁的就是这一块），这是展开后的样子。 */
.voice-bar__body {
  display: flex;
  align-items: center;
  height: 100%;
  border-radius: var(--radius-md);
  background: var(--fill);
  clip-path: inset(0 0 0 0 round var(--radius-md));
  transition: background-color var(--dur-quick) var(--ease-standard);
}
/* 按住说话时手指在麦克风上，录音条不接这根指针的事件（松手、拖动由外面全局接）。 */
.voice-bar--holding {
  pointer-events: none;
}
.voice-bar--armed .voice-bar__body {
  background: var(--danger-wash);
}
.voice-bar__track {
  display: flex;
  flex: 1;
  align-items: center;
  gap: 4px;
  min-width: 0;
  height: 100%;
  overflow: hidden;
  /* 松手弹回原位：减速停稳。拖着的时候跟手，不加过渡。 */
  transition: translate var(--dur-base) var(--ease-out);
}
.voice-bar__track--drag {
  transition: none;
}
/* 展开时内容从右往左依次淡入：先声波，再计时，最后 ✕。 */
.voice-bar__part {
  animation: voice-part-in var(--dur-base) var(--ease-out) 60ms both;
}
.voice-bar__part--2 {
  animation-delay: 90ms;
}
.voice-bar__part--3 {
  animation-delay: 120ms;
}
@keyframes voice-part-in {
  from {
    opacity: 0;
    transform: translateX(4px);
  }
}
.voice-bar__icon {
  flex: none;
  width: 28px;
  height: 28px;
}
.voice-bar--armed .voice-bar__icon {
  color: var(--danger-ink);
  background: var(--surface);
}
/* 正在录：一颗呼吸的红点。整理中它不再亮——那时候已经不在收音了。 */
.voice-bar__dot {
  flex: none;
  width: 8px;
  height: 8px;
  margin: 0 2px 0 2px;
  border-radius: 50%;
  background: var(--danger);
  animation:
    voice-part-in var(--dur-base) var(--ease-out) 90ms both,
    voice-breathe 1.6s var(--ease-standard) infinite;
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
.voice-bar--armed .voice-bar__clock {
  color: var(--danger-ink);
  font-weight: 600;
}
.voice-bar__wave {
  position: relative;
  flex: 1;
  min-width: 0;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 2px;
  padding: 0 6px;
  overflow: hidden;
  /* 最左边那几根淡出去，像是从这一行外面流进来的。 */
  mask-image: linear-gradient(90deg, transparent 0, currentcolor 40px);
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
.voice-bar--armed .voice-bar__wave i {
  background: var(--danger);
}
/* 整理中：压平的那条线上走一道扫光——机器正在做事（§9.6）。 */
.voice-bar__sheen {
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg, transparent, var(--surface), transparent) no-repeat;
  background-size: 40% 100%;
  opacity: 0;
  transition: opacity var(--dur-quick) var(--ease-standard);
}
.voice-bar--thinking .voice-bar__sheen {
  opacity: 0.9;
  animation: voice-sweep 1.2s linear infinite;
}
/* 按着时头顶那一句：靠右，落在麦克风上方。 */
.voice-bar__tip {
  position: absolute;
  right: 0;
  bottom: calc(100% + 12px);
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px 6px 8px;
  border-radius: var(--radius-pill);
  background: var(--inverse-surface);
  color: var(--inverse-ink);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
  box-shadow: var(--shadow-1);
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.voice-bar__tip--armed {
  background: var(--danger-ink);
  color: var(--surface);
}
.voice-tip-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}
.voice-tip-leave-active {
  transition:
    opacity var(--dur-quick) var(--ease-in),
    transform var(--dur-quick) var(--ease-in);
}
.voice-tip-enter-from,
.voice-tip-leave-to {
  opacity: 0;
  transform: translateY(4px);
}
@keyframes voice-breathe {
  50% {
    opacity: 0.35;
  }
}
@keyframes voice-sweep {
  from {
    background-position: -60% 0;
  }
  to {
    background-position: 160% 0;
  }
}
/* 关掉动画之后意思还在：红点照样是实心的，压平的线不扫光，计时还在走。 */
@media (prefers-reduced-motion: reduce) {
  .voice-bar__dot,
  .voice-bar--thinking .voice-bar__sheen {
    animation: none;
  }
  .voice-bar__part {
    animation: none;
  }
}
@media (pointer: coarse) {
  .voice-bar__icon::before {
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
