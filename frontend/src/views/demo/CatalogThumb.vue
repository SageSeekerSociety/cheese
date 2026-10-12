<script setup lang="ts">
// 目录页每张卡上面那一小格预览：挂的是真组件，但只在看得见的时候挂。
//
// 这一页有 273 格。全挂上就是 273 个真组件同时在跑（各自的取数、定时器、图表、监听
// 都在），滚到底的内存和帧率都撑不住；这里用 IntersectionObserver 看着自己，进视口
// 才挂，出视口就卸掉。
//
// 画的不是「设备预览」：固定按 420px 宽画一张，再等比缩到卡宽。真的按窗口宽度切的
// 那几档在单组件页，那里用的是真 px 的 iframe（见 CatalogFrame.vue）。
import type { CatalogEntry } from './catalog'

import { onBeforeUnmount, onMounted, ref } from 'vue'

import { stateProps } from './catalog'

const props = defineProps<{ entry: CatalogEntry }>()

const SHOT_WIDTH = 420

/**
 * 传送到 body 的那些（对话框、菜单）不能当缩略图画：它们会盖在整个目录页上。这种
 * 格子留一块空底，链接照常点得动。
 */
const drawable = props.entry.teleport !== true

const host = ref<HTMLElement | null>(null)
const live = ref(false)
const scale = ref(1)

let watcher: IntersectionObserver | null = null
let ruler: ResizeObserver | null = null

function fit(): void {
  const width = host.value?.clientWidth ?? 0
  scale.value = width > 0 ? Math.min(1, width / SHOT_WIDTH) : 1
}

onMounted(() => {
  if (!drawable) return
  fit()
  if (typeof ResizeObserver !== 'undefined' && host.value) {
    ruler = new ResizeObserver(fit)
    ruler.observe(host.value)
  }
  // 没有 IntersectionObserver 的环境（老浏览器、测试里的替身）：一直挂着，宁可费
  // 一点也不能空着。
  if (typeof IntersectionObserver === 'undefined') {
    live.value = true
    return
  }
  watcher = new IntersectionObserver((records) => records.forEach((record) => (live.value = record.isIntersecting)), {
    rootMargin: '240px',
  })
  if (host.value) watcher.observe(host.value)
})

onBeforeUnmount(() => {
  watcher?.disconnect()
  ruler?.disconnect()
  watcher = null
  ruler = null
})
</script>

<template>
  <!-- 缩略图是装饰：读屏软件读的是卡片上的名字和说明。里面挂着真组件，所以整块
       inert —— 里面的按钮不该抢 Tab，也不该点得到。 -->
  <span ref="host" class="thumb" inert aria-hidden="true">
    <span
      v-if="live && entry.states.length"
      class="thumb-shot"
      :style="{ width: `${SHOT_WIDTH}px`, transform: `scale(${scale})` }"
    >
      <v-layout v-if="entry.layout">
        <component :is="entry.component" v-bind="stateProps(entry, entry.states[0])">{{
          entry.states[0].slot
        }}</component>
      </v-layout>
      <component :is="entry.component" v-else v-bind="stateProps(entry, entry.states[0])">{{
        entry.states[0].slot
      }}</component>
    </span>
  </span>
</template>

<style scoped>
.thumb {
  display: block;
  height: 132px;
  overflow: hidden;
  background: var(--fill-2);
  border-bottom: 1px solid var(--line-2);
}

/* 按 420px 画，缩到卡宽。缩的是画出来的那一张，不是布局：这一格只表示「大概长什么
   样」，要按真宽度切的那几档在单组件页用 iframe。 */
.thumb-shot {
  display: block;
  transform-origin: top left;
}
</style>
