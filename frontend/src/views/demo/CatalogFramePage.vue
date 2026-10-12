<script setup lang="ts">
// iframe 里那一页：只画一格，不带站的骨架（左栏、顶栏、工具条都不进来）。
//
// 它和外面那一页是**两篇文档**，所以只看得到地址上给的那几样：主题由入口落在自己的
// `<html>` 上（`style.css` 的深色选择器是 `:root[data-theme='dark']`），动效改的是自
// 己的 `--dur-*`。画完把内容高度报回外面，外面拿它定框高。
import type { CatalogEntry, CatalogState } from './catalog'
import type { BackdropId } from './demoCatalogStudio'
import type { MotionMode } from './demoMotion'

import { onBeforeUnmount, onMounted, ref } from 'vue'

import { stateProps } from './catalog'
import { FRAME_MESSAGE } from './demoCatalogStudio'
import { applyMotion } from './demoMotion'

const props = defineProps<{
  entry: CatalogEntry
  state: CatalogState
  motion: MotionMode
  bg: BackdropId
}>()

const root = ref<HTMLElement | null>(null)
let ruler: ResizeObserver | null = null

/** 报给外面：这一格现在有多高。 */
function report(): void {
  const outer = window.parent
  if (!outer || outer === window) return
  try {
    outer.postMessage({ source: FRAME_MESSAGE, height: root.value?.offsetHeight ?? 0 }, window.location.origin)
  } catch {
    // 报不回去最多少量一次高度，不影响看。
  }
}

onMounted(() => {
  applyMotion(document.documentElement, props.motion)
  // 内容比框矮的时候，下面那截是 html 的底：不刷成同一个颜色就会露出浏览器的白。
  document.documentElement.style.background = props.bg === 'canvas' ? 'var(--canvas)' : 'var(--surface)'
  report()
  if (typeof ResizeObserver !== 'undefined' && root.value) {
    ruler = new ResizeObserver(report)
    ruler.observe(root.value)
  }
})

onBeforeUnmount(() => {
  ruler?.disconnect()
  ruler = null
})
</script>

<template>
  <div ref="root" class="frame-page" :data-bg="bg">
    <v-layout v-if="entry.layout">
      <component :is="entry.component" v-bind="stateProps(entry, state)">{{ state.slot }}</component>
    </v-layout>
    <component :is="entry.component" v-else v-bind="stateProps(entry, state)">{{ state.slot }}</component>
  </div>
</template>

<style scoped>
.frame-page {
  background: var(--surface);
}

.frame-page[data-bg='canvas'] {
  background: var(--canvas);
}
</style>
