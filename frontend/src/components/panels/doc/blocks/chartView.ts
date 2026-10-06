// A chart block: the chart drawn from its table, the table itself under it,
// and a label at the top that, in the editor, changes what kind of chart it is.
// The canvas and the fold are shared with the reader (./reader.ts).
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

import { NodeSelection } from '@tiptap/pm/state'

import { reducedMotion } from '@/utils/motion'

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
    colors: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => token(`--chart-${i}`)),
    text: token('--text'),
    muted: token('--muted'),
    line: token('--line'),
    surface: token('--surface'),
    font: getComputedStyle(document.body).fontFamily,
    still: reducedMotion(),
  }
}

export function tableRows(chart: PMNode): string[][] {
  const rows: string[][] = []
  chart.firstChild?.forEach((row) => {
    const cells: string[] = []
    row.forEach((cell) => cells.push(cell.textContent))
    rows.push(cells)
  })
  return rows
}

const kindKey = (kind: string, horizontal: boolean) => (horizontal && kind === 'bar' ? 'horizontal' : kind)
export const kindLabel = (kind: string, horizontal: boolean) =>
  t(`work.room.doc.blocks.chartKinds.${kindKey(kind, horizontal)}`)

export interface ChartState {
  kind: ChartKind
  horizontal: boolean
  /** The table's rows, the head first. */
  rows: string[][]
}

/** A chart's canvas, drawn from `read()`: drawn once it has a width (a chart
 *  in something folded has none yet), resized with it, and redrawn when the
 *  theme changes or `redraw` is called. */
export function chartCanvas(read: () => ChartState): { canvas: HTMLElement; redraw: () => void; destroy: () => void } {
  let chart: ECharts | null = null
  let timer = 0
  let alive = true
  const canvas = document.createElement('div')
  canvas.className = 'doc-chart__canvas'
  canvas.contentEditable = 'false'
  canvas.setAttribute('role', 'img')

  let waiting = false
  const draw = async () => {
    const init = await loadECharts()
    if (!alive) return
    waiting = !canvas.clientWidth
    if (waiting) return
    const { kind, horizontal, rows: table } = read()
    const rows = chartData(table)
    canvas.style.height = `${chartHeight(kind, horizontal, rows)}px`
    canvas.setAttribute('aria-label', kindLabel(kind, horizontal))
    chart ??= init(canvas, null, { renderer: 'svg' })
    chart.resize()
    chart.setOption(chartOption(kind, horizontal, rows, chartTheme(), canvas.clientWidth < 420), true)
  }
  requestAnimationFrame(() => void draw())
  const resize = new ResizeObserver(() => {
    if (waiting && canvas.clientWidth) void draw()
    else chart?.resize()
  })
  resize.observe(canvas)
  const stopTheme = onThemeChange(() => void draw())
  return {
    canvas,
    redraw() {
      clearTimeout(timer)
      timer = window.setTimeout(() => void draw(), 120)
    },
    destroy() {
      alive = false
      clearTimeout(timer)
      resize.disconnect()
      stopTheme()
      chart?.dispose()
    },
  }
}

/** The table under a chart folds away behind `toggle`. */
export function chartToggle(dom: HTMLElement, data: HTMLElement): HTMLButtonElement {
  const toggle = controlButton('doc-chart__toggle', t('work.room.doc.blocks.chartData'))
  toggle.textContent = t('work.room.doc.blocks.chartData')
  // While editing the table shows unless folded; while reading it stays folded
  // unless opened. The stylesheet picks by which of the two the page is in.
  toggle.addEventListener('click', () => {
    const shown = getComputedStyle(data).display !== 'none'
    dom.classList.toggle('doc-chart--open', !shown)
    dom.classList.toggle('doc-chart--closed', shown)
    toggle.setAttribute('aria-expanded', String(!shown))
  })
  return toggle
}

export function chartView({ node, editor, getPos }: NodeViewRendererProps): NodeView {
  let current = node

  const dom = document.createElement('div')
  dom.dataset.block = 'chart'
  const bar = document.createElement('div')
  bar.className = 'doc-chart__bar'
  bar.contentEditable = 'false'
  const kind = controlButton('doc-chart__kind', t('work.room.doc.blocks.chartKind'))
  const data = document.createElement('div')
  data.className = 'doc-chart__data'
  const toggle = chartToggle(dom, data)
  bar.append(kind, toggle)
  const { canvas, redraw, destroy } = chartCanvas(() => ({
    kind: current.attrs.kind as ChartKind,
    horizontal: Boolean(current.attrs.horizontal),
    rows: tableRows(current),
  }))
  dom.append(bar, canvas, data)

  const paintBar = () => {
    kind.textContent = kindLabel(current.attrs.kind as string, current.attrs.horizontal as boolean)
  }
  paintBar()

  // Pressing the chart itself, or the bar beside its buttons, selects the whole
  // block: the caret never stays behind in a cell of the table, where the next
  // Backspace would eat a character instead of the chart.
  const select = (e: MouseEvent) => {
    const pos = posOf(getPos)
    if (e.button !== 0 || pos === null || !editor.isEditable) return
    if (e.target !== bar && !canvas.contains(e.target as Node)) return
    e.preventDefault()
    editor.view.dispatch(editor.state.tr.setSelection(NodeSelection.create(editor.state.doc, pos)))
    editor.view.focus()
  }
  canvas.addEventListener('mousedown', select)
  bar.addEventListener('mousedown', select)

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

  return {
    dom,
    contentDOM: data,
    update(next) {
      if (next.type !== node.type) return false
      const head = next.attrs.kind !== current.attrs.kind || next.attrs.horizontal !== current.attrs.horizontal
      current = next
      if (head) paintBar()
      redraw()
      return true
    },
    ignoreMutation: (m) =>
      (m.type === 'attributes' && (m.target === dom || m.target === data)) ||
      bar.contains(m.target) ||
      canvas.contains(m.target),
    stopEvent: (e) => e.target === bar || canvas.contains(e.target as Node),
    destroy,
  }
}
