// A chart block in the editor: the chart drawn from its table, the table
// itself under it, and a label at the top that changes what kind of chart it is.
//
// The table is the block's content and is edited like any other table (its
// row and column handles come from tableHandles.ts); the chart redraws from it.
// A reader sees the chart, with the table one click away.
//
// ECharts is loaded the first time a chart is on screen, so a document without
// one never downloads it.
import type { NodeViewRendererProps } from '@tiptap/core'
import type { Node as PMNode } from '@tiptap/pm/model'
import type { NodeView } from '@tiptap/pm/view'
import type { ChartKind } from '../../../../lib/docSchema/blocks'
import type { ChartTheme } from './chartOption'

import { CHART_HORIZONTAL, CHART_KINDS } from '../../../../lib/docSchema/blocks'

import { chartData, chartHeight, chartOption } from './chartOption'
import { controlButton, menuAt } from './popover'
import { onThemeChange, posOf, token } from './viewKit'

import { t } from '@/i18n'

type ECharts = { setOption: (option: object, notMerge: boolean) => void; resize: () => void; dispose: () => void }
type Init = (el: HTMLElement, theme: null, opts: { renderer: 'svg' }) => ECharts

let loading: Promise<Init> | null = null

/** ECharts with only the charts a block draws, loaded once. */
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

function chartTheme(): ChartTheme {
  return {
    colors: [1, 2, 3, 4, 5, 6].map((i) => token(`--chart-${i}`)),
    text: token('--text'),
    muted: token('--muted'),
    line: token('--line'),
    surface: token('--surface'),
    font: getComputedStyle(document.body).fontFamily,
  }
}

function tableRows(chart: PMNode): string[][] {
  const rows: string[][] = []
  chart.firstChild?.forEach((row) => {
    const cells: string[] = []
    row.forEach((cell) => cells.push(cell.textContent))
    rows.push(cells)
  })
  return rows
}

const kindKey = (kind: string, horizontal: boolean) => (horizontal && kind === 'bar' ? 'horizontal' : kind)
const kindLabel = (kind: string, horizontal: boolean) =>
  t(`work.room.doc.blocks.chartKinds.${kindKey(kind, horizontal)}`)

export function chartView({ node, editor, getPos }: NodeViewRendererProps): NodeView {
  let current = node
  let chart: ECharts | null = null
  let timer = 0
  let alive = true

  const dom = document.createElement('div')
  dom.dataset.block = 'chart'
  const bar = document.createElement('div')
  bar.className = 'doc-chart__bar'
  bar.contentEditable = 'false'
  const kind = controlButton('doc-chart__kind', t('work.room.doc.blocks.chartKind'))
  const toggle = controlButton('doc-chart__toggle', t('work.room.doc.blocks.chartData'))
  toggle.textContent = t('work.room.doc.blocks.chartData')
  bar.append(kind, toggle)
  const canvas = document.createElement('div')
  canvas.className = 'doc-chart__canvas'
  canvas.contentEditable = 'false'
  canvas.setAttribute('role', 'img')
  const data = document.createElement('div')
  data.className = 'doc-chart__data'
  dom.append(bar, canvas, data)

  const paintBar = () => {
    kind.textContent = kindLabel(current.attrs.kind as string, current.attrs.horizontal as boolean)
  }
  paintBar()

  // While editing the table shows unless folded; while reading it stays folded
  // unless opened. The stylesheet picks by which of the two the editor is in.
  toggle.addEventListener('click', () => {
    const shown = getComputedStyle(data).display !== 'none'
    dom.classList.toggle('doc-chart--open', !shown)
    dom.classList.toggle('doc-chart--closed', shown)
    toggle.setAttribute('aria-expanded', String(!shown))
  })

  kind.addEventListener('click', () => {
    const pos = posOf(getPos)
    if (pos === null || !editor.isEditable) return
    const set = (k: ChartKind, horizontal: boolean) =>
      editor.view.dispatch(
        editor.state.tr.setNodeMarkup(pos, null, { ...editor.state.doc.nodeAt(pos)?.attrs, kind: k, horizontal })
      )
    const choices: [ChartKind, boolean][] = []
    for (const k of CHART_KINDS) {
      choices.push([k, false])
      if (k === 'bar') choices.push([k, true])
    }
    menuAt(
      kind,
      choices.map(([k, horizontal]) => ({
        label: kindLabel(k, horizontal),
        hint: t(`work.room.doc.blocks.chartHints.${kindKey(k, horizontal)}`),
        pressed: current.attrs.kind === k && Boolean(current.attrs.horizontal) === horizontal,
        run: () => set(k, horizontal && CHART_HORIZONTAL.has(k)),
      }))
    )
  })

  // A chart in something folded has no width yet: it is drawn once it has one.
  let waiting = false
  const draw = async () => {
    const init = await loadECharts()
    if (!alive) return
    waiting = !canvas.clientWidth
    if (waiting) return
    const k = current.attrs.kind as ChartKind
    const horizontal = Boolean(current.attrs.horizontal)
    const rows = chartData(tableRows(current))
    canvas.style.height = `${chartHeight(k, horizontal, rows)}px`
    canvas.setAttribute('aria-label', kindLabel(k, horizontal))
    chart ??= init(canvas, null, { renderer: 'svg' })
    chart.resize()
    chart.setOption(chartOption(k, horizontal, rows, chartTheme(), canvas.clientWidth < 420), true)
  }
  const later = () => {
    clearTimeout(timer)
    timer = window.setTimeout(() => void draw(), 120)
  }
  requestAnimationFrame(() => void draw())
  const resize = new ResizeObserver(() => {
    if (waiting && canvas.clientWidth) void draw()
    else chart?.resize()
  })
  resize.observe(canvas)
  const stopTheme = onThemeChange(() => void draw())

  return {
    dom,
    contentDOM: data,
    update(next) {
      if (next.type !== node.type) return false
      const head = next.attrs.kind !== current.attrs.kind || next.attrs.horizontal !== current.attrs.horizontal
      current = next
      if (head) paintBar()
      later()
      return true
    },
    ignoreMutation: (m) =>
      (m.type === 'attributes' && (m.target === dom || m.target === data)) ||
      bar.contains(m.target) ||
      canvas.contains(m.target),
    stopEvent: (e) => canvas.contains(e.target as Node),
    destroy() {
      alive = false
      clearTimeout(timer)
      resize.disconnect()
      stopTheme()
      chart?.dispose()
    },
  }
}
