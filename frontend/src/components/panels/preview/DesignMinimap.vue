<script setup lang="ts">
// 超长图右边那一条定位：整张图缩成一条，浅色那块是此刻看得到的范围，亮点是图上的
// 批注。拖它就滚到整张的任意一段。
//
// 只在一张图明显比视口长的时候才摆：普通的照片铺得下，摆一条只会碍事。
import { computed, ref } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{
  /** 这一格滚动的那个元素（`.design-image__pane`）。 */
  pane: HTMLElement | null
  /** 屏幕上这一格的可见高度与内容总高（像素）。 */
  viewportHeight: number
  contentHeight: number
  /** 图上那几处批注，按 0–1 落在整张的哪儿。 */
  marks: readonly number[]
}>()
const emit = defineEmits<{ jump: [ratio: number] }>()

const dragging = ref(false)

/** 可见窗口落在整张的哪儿（0–1）与它占多大。 */
const window = computed(() => {
  const total = props.contentHeight
  if (!(total > 0)) return { top: 0, height: 1 }
  const height = Math.min(1, props.viewportHeight / total)
  const top = Math.max(0, Math.min(1 - height, (props.pane?.scrollTop ?? 0) / total))
  return { top, height }
})

function ratioAt(event: PointerEvent) {
  const el = event.currentTarget as HTMLElement
  const rect = el.getBoundingClientRect()
  if (!rect.height) return 0
  return Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))
}
function down(event: PointerEvent) {
  dragging.value = true
  ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
  emit('jump', ratioAt(event))
}
function move(event: PointerEvent) {
  if (!dragging.value) return
  emit('jump', ratioAt(event))
}
function up() {
  dragging.value = false
}
</script>

<template>
  <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- a scroll bar; the pane's own wheel and keyboard scrolling are the accessible path -->
  <div
    class="design-minimap"
    :title="t('design.minimapHint')"
    :aria-label="t('design.minimap')"
    @pointerdown="down"
    @pointermove="move"
    @pointerup="up"
    @pointercancel="up"
  >
    <div class="design-minimap__track">
      <span
        v-for="(mark, index) in marks"
        :key="index"
        class="design-minimap__mark"
        :style="{ top: `${Math.max(0, Math.min(1, mark)) * 100}%` }"
      />
      <span
        class="design-minimap__window"
        :style="{ top: `${window.top * 100}%`, height: `${window.height * 100}%` }"
      />
    </div>
  </div>
</template>

<style scoped>
.design-minimap {
  position: absolute;
  top: 8px;
  right: 6px;
  bottom: 8px;
  z-index: 4;
  width: 16px;
  cursor: pointer;
  touch-action: none;
}
.design-minimap__track {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: color-mix(in srgb, var(--ink) 8%, transparent);
  border-radius: var(--radius-pill);
}
/* 此刻看得到的那一段：半透明的强调色块。 */
.design-minimap__window {
  position: absolute;
  right: 2px;
  left: 2px;
  background: color-mix(in srgb, var(--accent) 50%, transparent);
  border: 1px solid var(--accent);
  border-radius: var(--radius-pill);
}
/* 图上的批注：一颗小亮点。 */
.design-minimap__mark {
  position: absolute;
  left: 50%;
  width: 7px;
  height: 7px;
  margin-top: -3.5px;
  margin-left: -3.5px;
  background: var(--surface);
  border: 2px solid var(--accent);
  border-radius: 50%;
}
</style>
