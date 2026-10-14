<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

import { chartTheme, onTheme } from '../theme'
const props = withDefaults(defineProps<{ option?: object; height?: number }>(), { height: 260 })
const box = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null
const shot = () => document.documentElement.dataset.mode === 'shot'
type Opt = Record<string, unknown>
const list = (x: unknown): Opt[] => (Array.isArray(x) ? x : x ? [x] : []) as Opt[]

/** What a chart in a document column needs that ECharts does not default to:
 *  the legend above the plot (at the bottom it lands on the axis labels), the x
 *  axis's name under the axis, and a mark line's label inside the plot. The
 *  app's own settings win wherever it gives them. */
function settled(o: Opt): Opt {
  const legend = list(o.legend).map((l) => ({ top: 0, type: 'scroll', ...l }))
  // A name at the end of the x axis runs off a narrow column: under it, centred.
  const xAxes = list(o.xAxis).map((a) =>
    a.name && !a.nameLocation ? { nameLocation: 'middle', nameGap: 28, ...a } : a
  )
  const xNamed = xAxes.some((a) => a.name)
  const series = list(o.series).map((one) => {
    const line = one.markLine as Opt | undefined
    if (!line) return one
    return { ...one, markLine: { ...line, label: { position: 'insideEndTop', ...(line.label as Opt) } } }
  })
  return {
    grid: { left: 12, right: 20, top: legend.length ? 40 : 28, bottom: xNamed ? 32 : 12, containLabel: true },
    ...(shot() ? { animation: false } : {}),
    ...o,
    ...(legend.length ? { legend: Array.isArray(o.legend) ? legend : legend[0] } : {}),
    ...(xAxes.length ? { xAxis: Array.isArray(o.xAxis) ? xAxes : xAxes[0] } : {}),
    ...(series.length ? { series } : {}),
  }
}
function draw() {
  if (!box.value) return
  chart?.dispose()
  chart = echarts.init(box.value, chartTheme(), { renderer: 'svg' })
  chart.setOption(settled((props.option ?? {}) as Opt), true)
}
let seen: ResizeObserver | null = null
let off = () => {}
onMounted(() => {
  seen = new ResizeObserver(() => (chart ? chart.resize() : box.value?.clientWidth && draw()))
  if (box.value) seen.observe(box.value)
  off = onTheme(draw)
})
watch(
  () => props.option,
  (o) => (chart ? chart.setOption(settled((o ?? {}) as Opt), true) : draw()),
  { deep: true }
)
onBeforeUnmount(() => {
  seen?.disconnect()
  off()
  chart?.dispose()
})
</script>
<template><div ref="box" :style="{ height: `${height}px` }" /></template>
<style>
*,
*::before,
*::after {
  box-sizing: border-box;
}
:host {
  display: block;
  width: 100%;
  margin: 8px 0;
}
</style>
