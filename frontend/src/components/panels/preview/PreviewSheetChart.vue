<script setup lang="ts">
// 工作簿里带的一张图。
//
// 表格按字节画，图就只剩 XML 里的那点定义（`lib/sheetChart`）：这张图是柱还是折线、
// 每一条叫什么、引用了哪一片格子。这里把那点定义画成一张真图 —— 一份「有图的表」
// 在预览里看不见图，读者会以为文件里没有。
//
// 画法和文档里的图表块同一套（ECharts、同一份配色令牌、窄处收窄），所以同一个文件
// 在表格里和嵌进文档里看起来是一种东西。ECharts 第一次有图要画时才加载。
import type { ChartTheme } from '@/components/panels/doc/blocks/chartOption'
import type { SheetChart } from '@/lib/sheetChart'

import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { reducedMotion } from '@/utils/motion'

import { chartData, chartHeight, chartOption } from '@/components/panels/doc/blocks/chartOption'
import { t } from '@/i18n'

type ECharts = { setOption: (option: object, notMerge: boolean) => void; resize: () => void; dispose: () => void }
type Init = (el: HTMLElement, theme: null, opts: { renderer: 'svg' }) => ECharts

let loading: Promise<Init> | null = null
function loadECharts(): Promise<Init> {
  loading ??= Promise.all([
    import('echarts/core'),
    import('echarts/charts'),
    import('echarts/components'),
    import('echarts/renderers'),
  ]).then(([core, charts, components, renderers]) => {
    core.use([
      charts.BarChart,
      charts.LineChart,
      charts.PieChart,
      charts.ScatterChart,
      components.GridComponent,
      components.LegendComponent,
      components.TooltipComponent,
      renderers.SVGRenderer,
    ])
    return core.init as unknown as Init
  })
  return loading
}

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

/** 和文档里的图表块同一份主题：配色、字色、线色从同一套令牌来。 */
function chartTheme(): ChartTheme {
  return {
    colors: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => token(`--chart-${i}`)),
    text: token('--text'),
    muted: token('--muted'),
    line: token('--line'),
    surface: token('--surface'),
    font: getComputedStyle(document.body).fontFamily,
    still: reducedMotion(),
  }
}

/** 这张图画在哪一块画布上，和它的数据。父组件给一张图一个实例。 */
const props = defineProps<{ chart: SheetChart; narrow?: boolean }>()
const el = ref<HTMLElement | null>(null)
let drawn: ECharts | null = null
let themeWatched = false

/** 图的数据按图表块那套摆：第一行是各条的名字，之后每一行是「类别 + 各条的值」。 */
function table(): string[][] {
  const rows: string[][] = [['', ...props.chart.series.map((s) => s.name)]]
  props.chart.categories.forEach((category, i) => {
    rows.push([category, ...props.chart.series.map((s) => s.texts[i] ?? '')])
  })
  return rows
}

async function draw() {
  if (!el.value) return
  const init = await loadECharts()
  const data = chartData(table())
  const height = chartHeight(props.chart.kind, props.chart.horizontal, data)
  if (!drawn) drawn = init(el.value, null, { renderer: 'svg' })
  el.value.style.height = `${height}px`
  drawn.setOption(chartOption(props.chart.kind, props.chart.horizontal, data, chartTheme(), !!props.narrow), true)
  drawn.resize()
}

/** 深色/浅色切换的时候要重画一次：配色是从令牌里现取的，换了主题不重取就还是旧色。 */
function watchTheme() {
  if (themeWatched) return
  themeWatched = true
  const observer = new MutationObserver(() => void draw())
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
  onBeforeUnmount(() => observer.disconnect())
}

onMounted(() => {
  watchTheme()
  void draw()
})
watch(
  () => [props.chart, props.narrow],
  () => void draw(),
  { deep: true }
)
onBeforeUnmount(() => {
  drawn?.dispose()
  drawn = null
})
</script>

<template>
  <!-- 一张图：标题在图上（ECharts 自己画），这块只给画布一个尺寸和一个可访问的名字。 -->
  <figure class="sc">
    <div ref="el" class="sc__canvas" role="img" :aria-label="chart.title || t('work.room.preview.sheetChart')" />
    <figcaption v-if="chart.title" class="sc__title">{{ chart.title }}</figcaption>
  </figure>
</template>

<style scoped>
.sc {
  margin: 0;
  padding: 8px 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.sc__canvas {
  width: 100%;
  min-height: 160px;
}
.sc__title {
  font-size: 12px;
  color: var(--muted);
}
</style>
