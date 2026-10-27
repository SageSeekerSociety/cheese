<script setup lang="ts">
// 看图浮层顶上那一条：左边是哪张图、第几张；右边从左到右是「看」（缩放、适应）、
// 「调」（旋转、翻转、裁剪、亮度饱和度）、「交给右侧」（写字、打码、框选批注）、下载
// 和关闭。交给右侧的那几颗只在对话里有：别处（反馈附件）没有右侧面板可交。
import type { ImageAdjust } from '@/lib/imageEdit'

import { ref } from 'vue'

import { t } from '@/i18n'

defineProps<{
  name: string
  index: number
  total: number
  /** 当前放大倍数（1 = 整张看得见）。 */
  scale: number
  adjust: ImageAdjust
  cropping: boolean
  /** 能不能交给右侧面板（写字、打码、框选批注）。 */
  canHandOff: boolean
}>()
const emit = defineEmits<{
  zoom: [factor: number]
  fit: []
  rotate: []
  flip: []
  crop: []
  tone: [key: 'brightness' | 'saturation', value: number]
  'hand-off': [tool: 'select' | 'text' | 'redact']
  download: []
  close: []
}>()

/** 亮度、饱和度那两根滑杆收在一颗按钮后面：平时用不上，摊开占一整行。 */
const toneOpen = ref(false)

const closeButton = ref<HTMLButtonElement | null>(null)
defineExpose({ closeButton })
</script>

<template>
  <div class="lb-top" @click.stop @pointerdown.stop>
    <div class="lb-bar" data-testid="lightbox-toolbar">
      <span class="lb-bar__name" :title="name">
        {{ total > 1 ? t('work.image.position', { name, n: index + 1, total }) : name }}
      </span>
      <span class="lb-bar__space" />
      <div class="lb-bar__group" role="group" :aria-label="t('work.image.groupView')">
        <button
          type="button"
          class="lb-btn"
          :title="t('work.image.zoomOut')"
          :aria-label="t('work.image.zoomOut')"
          :disabled="scale <= 1"
          @click="emit('zoom', 1 / 1.25)"
        >
          <v-icon size="18">mdi-minus</v-icon>
        </button>
        <span class="lb-bar__pct" aria-live="polite">{{ Math.round(scale * 100) }}%</span>
        <button
          type="button"
          class="lb-btn"
          :title="t('work.image.zoomIn')"
          :aria-label="t('work.image.zoomIn')"
          @click="emit('zoom', 1.25)"
        >
          <v-icon size="18">mdi-plus</v-icon>
        </button>
        <button type="button" class="lb-btn lb-btn--text" @click="emit('fit')">{{ t('work.image.fit') }}</button>
      </div>
      <div class="lb-bar__group" role="group" :aria-label="t('work.image.groupEdit')">
        <button
          type="button"
          class="lb-btn"
          :title="t('work.image.rotate')"
          :aria-label="t('work.image.rotate')"
          @click="emit('rotate')"
        >
          <v-icon size="18">mdi-rotate-right</v-icon>
        </button>
        <button
          type="button"
          class="lb-btn"
          :class="{ 'lb-btn--on': adjust.flip }"
          :aria-pressed="adjust.flip"
          :title="t('work.image.flip')"
          :aria-label="t('work.image.flip')"
          @click="emit('flip')"
        >
          <v-icon size="18">mdi-flip-horizontal</v-icon>
        </button>
        <button
          type="button"
          class="lb-btn"
          :class="{ 'lb-btn--on': cropping }"
          :aria-pressed="cropping"
          :title="t('work.image.crop')"
          :aria-label="t('work.image.crop')"
          @click="emit('crop')"
        >
          <v-icon size="18">mdi-crop</v-icon>
        </button>
        <button
          type="button"
          class="lb-btn"
          :class="{ 'lb-btn--on': toneOpen || adjust.brightness !== 100 || adjust.saturation !== 100 }"
          :aria-expanded="toneOpen"
          :title="t('work.image.tone')"
          :aria-label="t('work.image.tone')"
          @click="toneOpen = !toneOpen"
        >
          <v-icon size="18">mdi-tune-variant</v-icon>
        </button>
        <template v-if="canHandOff">
          <button
            type="button"
            class="lb-btn"
            :title="t('work.image.textHint')"
            :aria-label="t('work.image.text')"
            @click="emit('hand-off', 'text')"
          >
            <v-icon size="18">mdi-format-text</v-icon>
          </button>
          <button
            type="button"
            class="lb-btn"
            :title="t('work.image.redactHint')"
            :aria-label="t('work.image.redact')"
            @click="emit('hand-off', 'redact')"
          >
            <v-icon size="18">mdi-blur</v-icon>
          </button>
        </template>
      </div>
      <div class="lb-bar__group">
        <button
          type="button"
          class="lb-btn"
          :title="t('work.image.download')"
          :aria-label="t('work.image.download')"
          @click="emit('download')"
        >
          <v-icon size="18">mdi-download</v-icon>
        </button>
        <button v-if="canHandOff" type="button" class="lb-btn lb-btn--primary" @click="emit('hand-off', 'select')">
          <v-icon size="16">mdi-dock-right</v-icon>
          <span class="lb-btn__label">{{ t('work.image.annotateInPanel') }}</span>
        </button>
        <button
          ref="closeButton"
          type="button"
          class="lb-btn"
          :title="t('work.image.closeHint')"
          :aria-label="t('work.image.close')"
          @click="emit('close')"
        >
          <v-icon size="18">mdi-close</v-icon>
        </button>
      </div>
    </div>
    <div v-if="toneOpen" class="lb-tone" data-testid="lightbox-tone">
      <label class="lb-tone__row">
        <span>{{ t('work.image.brightness') }}</span>
        <input
          type="range"
          min="50"
          max="150"
          :value="adjust.brightness"
          @input="emit('tone', 'brightness', Number(($event.target as HTMLInputElement).value))"
        />
        <span class="lb-tone__val">{{ adjust.brightness }}%</span>
      </label>
      <label class="lb-tone__row">
        <span>{{ t('work.image.saturation') }}</span>
        <input
          type="range"
          min="0"
          max="200"
          :value="adjust.saturation"
          @input="emit('tone', 'saturation', Number(($event.target as HTMLInputElement).value))"
        />
        <span class="lb-tone__val">{{ adjust.saturation }}%</span>
      </label>
    </div>
  </div>
</template>

<style scoped>
.lb-top {
  position: absolute;
  top: 0;
  right: 0;
  left: 0;
  z-index: 2;
}
.lb-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 52px;
  padding: 0 calc(12px + env(safe-area-inset-right, 0px)) 0 calc(14px + env(safe-area-inset-left, 0px));
  margin-top: env(safe-area-inset-top, 0px);
  color: var(--lightbox-ink);
}
.lb-bar__name {
  min-width: 0;
  overflow: hidden;
  font-size: 14px;
  line-height: var(--lh-14);
  white-space: nowrap;
  text-overflow: ellipsis;
}
.lb-bar__space {
  flex: 1;
}
.lb-bar__group {
  display: flex;
  flex: none;
  align-items: center;
  gap: 6px;
}
.lb-bar__group + .lb-bar__group {
  padding-left: 12px;
  border-left: 1px solid var(--lightbox-fill-2);
}
.lb-bar__pct {
  min-width: 44px;
  font-size: 13px;
  line-height: var(--lh-13);
  text-align: center;
  font-variant-numeric: tabular-nums;
}
.lb-btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-width: 32px;
  height: 32px;
  padding: 0 8px;
  color: var(--lightbox-ink);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
  background: var(--lightbox-fill);
  border: 0;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.lb-btn:hover:not(:disabled),
.lb-btn--on {
  background: var(--lightbox-fill-2);
}
.lb-btn:disabled {
  opacity: 0.4;
  cursor: default;
}
.lb-btn:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
.lb-btn--primary {
  color: var(--accent-on);
  background: var(--accent);
}
.lb-btn--primary:hover:not(:disabled) {
  background: var(--accent-press);
}
.lb-tone {
  position: absolute;
  top: calc(52px + env(safe-area-inset-top, 0px));
  right: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 260px;
  padding: 12px 14px;
  color: var(--lightbox-ink);
  background: var(--lightbox-ground);
  border: 1px solid var(--lightbox-fill-2);
  border-radius: var(--radius-lg);
}
.lb-tone__row {
  display: grid;
  grid-template-columns: 48px 1fr 44px;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  line-height: var(--lh-13);
}
.lb-tone__row input {
  accent-color: var(--accent);
}
.lb-tone__val {
  color: var(--lightbox-muted);
  text-align: right;
  font-variant-numeric: tabular-nums;
}
/* 窄屏：名字让位，按钮可以横着滑；主按钮只留图标。 */
@media (max-width: 760px) {
  .lb-bar {
    overflow-x: auto;
  }
  .lb-bar__name {
    flex: none;
    max-width: 40vw;
  }
  .lb-btn__label {
    display: none;
  }
}
/* 触屏上手指点得中：画出来 32px，能点的范围撑到 44×44。 */
@media (pointer: coarse) {
  .lb-btn::before {
    content: '';
    position: absolute;
    top: 50%;
    left: 50%;
    width: max(100%, 44px);
    height: max(100%, 44px);
    transform: translate(-50%, -50%);
  }
}
</style>
