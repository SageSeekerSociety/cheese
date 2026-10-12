<script setup lang="ts">
// 一台设备：一个 iframe，里面按真 px 渲染，外面等比缩到栏宽。
//
// 为什么非得是 iframe：产品里按宽度切的那几档，判断的是 `window`/`matchMedia`
// （`useWorkspaceLayout.ts` 的 `SPLIT_MIN_WIDTH 768`、`COMPACT_DESKTOP_MAX_WIDTH 1180`，
// Vuetify 的 `mdAndUp`）。把一个 div 用 CSS `transform: scale()` 缩一下，这些一个都
// 不会变——里面看到的是 1280 的布局，却按 390 的样子缩着，演示是假的。iframe 里
// window 宽度是真的，缩的只是显示。
import { onBeforeUnmount, onMounted, ref } from 'vue'

import { FRAME_MESSAGE } from './demoCatalogStudio'

const props = defineProps<{
  href: string
  /** 框里的真宽度。 */
  width: number
  label: string
  title: string
}>()

/** 里面量出来的内容高度；量到之前先立个占位，免得框是空的。 */
const height = ref(320)
const scale = ref(1)

const box = ref<HTMLElement | null>(null)
const frame = ref<HTMLIFrameElement | null>(null)
let ruler: ResizeObserver | null = null

function fit(): void {
  const available = box.value?.clientWidth ?? 0
  if (available > 0) scale.value = Math.min(1, available / props.width)
}

function measure(event?: Event): void {
  if (event && 'data' in event) {
    const message = event as MessageEvent
    if (message.source !== frame.value?.contentWindow) return
    const data = message.data as { source?: string; height?: number } | null
    if (!data || data.source !== FRAME_MESSAGE || typeof data.height !== 'number') return
    height.value = Math.max(120, Math.round(data.height))
    return
  }
  // 同一个源，直接读一把更快：不等到它把高度报回来。
  try {
    const measured = frame.value?.contentDocument?.documentElement?.scrollHeight ?? 0
    if (measured > 0) height.value = Math.max(120, measured)
  } catch {
    // 读不到就等它自己报。
  }
}

onMounted(() => {
  fit()
  if (typeof ResizeObserver !== 'undefined' && box.value) {
    ruler = new ResizeObserver(fit)
    ruler.observe(box.value)
  }
  window.addEventListener('message', measure)
})

onBeforeUnmount(() => {
  ruler?.disconnect()
  ruler = null
  window.removeEventListener('message', measure)
})
</script>

<template>
  <div class="frame">
    <p class="frame-head">
      <span class="frame-label">{{ label }}</span>
      <span class="frame-width">{{ width }}px</span>
      <span class="frame-scale">{{ scale < 1 ? `显示为 ${Math.round(scale * 100)}%` : '原始大小' }}</span>
    </p>
    <div ref="box" class="frame-box">
      <div class="frame-inner" :style="{ width: `${width * scale}px`, height: `${height * scale}px` }">
        <iframe
          ref="frame"
          class="frame-view"
          :src="href"
          :title="title"
          :style="{ width: `${width}px`, height: `${height}px`, transform: `scale(${scale})` }"
          @load="measure"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.frame {
  flex: 1 1 320px;
  min-width: 0;
}

.frame-head {
  display: flex;
  gap: 8px;
  align-items: baseline;
  margin: 0 0 6px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}

.frame-label {
  font-weight: 600;
  color: var(--muted);
}

.frame-scale {
  margin-left: auto;
}

.frame-box {
  overflow: hidden;
}

.frame-inner {
  position: relative;
  margin: 0 auto;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

/* 绝对定位：1280 宽的框在布局里还是 1280 宽，会把这一栏撑破，缩了也不改布局盒子。 */
.frame-view {
  display: block;
  position: absolute;
  top: 0;
  left: 0;
  background: var(--surface);
  border: 0;
  transform-origin: top left;
}
</style>
