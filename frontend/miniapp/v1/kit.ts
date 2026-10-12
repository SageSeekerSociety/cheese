// `kit`: what a mini-app is written with, so 芝士 writes what the app does and
// none of how it looks. Every piece wears the document's tokens, light or dark,
// so an app reads as part of the document it sits in, and several apps in one
// document read as one family.
//
// This is shell 1's contract (`references/apps.md` in the writing skill teaches
// it). Within shell 1 it only grows: an app written against it keeps working.
// A change that would break one goes into a shell 2.

type Child = Node | string | number | null | undefined | false | Child[]
type Attrs = Record<string, unknown>
type Listener = (value: string) => void

interface ECharts {
  setOption: (option: object, notMerge?: boolean) => void
  resize: () => void
  dispose: () => void
}
interface EChartsModule {
  init: (el: HTMLElement, theme?: object, opts?: object) => ECharts
}
interface KatexModule {
  render: (tex: string, el: HTMLElement, opts: object) => void
}

export interface KitHost {
  echarts: EChartsModule
  katex: KatexModule
  mode: () => string
  on: (event: 'mode' | 'theme', fn: Listener) => void
}

export function createKit(host: KitHost) {
  function h(tag: string, attrs: Attrs | null = null, ...children: Child[]): HTMLElement {
    const el = document.createElement(tag)
    for (const [name, value] of Object.entries(attrs ?? {})) {
      if (value == null || value === false) continue
      if (name.startsWith('on') && typeof value === 'function') {
        el.addEventListener(name.slice(2).toLowerCase(), value as EventListener)
      } else if (name === 'style' && typeof value === 'object') {
        Object.assign(el.style, value)
      } else {
        el.setAttribute(name, value === true ? '' : String(value))
      }
    }
    append(el, children)
    return el
  }

  function append(el: Element, children: Child[]): void {
    for (const child of children) {
      if (child == null || child === false) continue
      if (Array.isArray(child)) append(el, child)
      else el.append(child instanceof Node ? child : String(child))
    }
  }

  /** A design token's value: `color('accent')`, `color('chart-3')`. */
  function color(name: string): string {
    return getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim()
  }

  const row = (...children: Child[]) => h('div', { class: 'k-row' }, ...children)

  const field = (label: string, control: Node) => h('label', { class: 'k-field' }, h('span', null, label), control)

  function slider(o: {
    label: string
    min: number
    max: number
    step?: number
    value: number
    format?: (v: number) => string
    onInput: (v: number) => void
  }): HTMLElement {
    const format = o.format ?? String
    const out = h('output', null, format(o.value))
    const range = h('input', {
      type: 'range',
      min: o.min,
      max: o.max,
      step: o.step ?? 1,
      value: o.value,
      'aria-label': o.label,
    }) as HTMLInputElement
    range.addEventListener('input', () => {
      out.textContent = format(Number(range.value))
      o.onInput(Number(range.value))
    })
    return h('div', { class: 'k-slider' }, h('div', { class: 'k-slider-top' }, h('span', null, o.label), out), range)
  }

  function select<T extends string | number>(options: [T, string][], value: T, onChange: (v: T) => void) {
    const el = h(
      'select',
      { class: 'k-select' },
      options.map(([v, text]) => h('option', { value: v, selected: v === value }, text))
    ) as HTMLSelectElement
    el.addEventListener('change', () => {
      const picked = options.find(([v]) => String(v) === el.value)
      if (picked) onChange(picked[0])
    })
    return el
  }

  function segmented<T extends string | number>(options: [T, string][], value: T, onChange: (v: T) => void) {
    const box = h('div', { class: 'k-seg', role: 'group' })
    let current = value
    const paint = () => {
      for (const b of box.children) b.setAttribute('aria-pressed', String(b.getAttribute('data-v') === String(current)))
    }
    for (const [v, text] of options) {
      box.append(
        h(
          'button',
          {
            type: 'button',
            'data-v': v,
            onclick: () => {
              current = v
              paint()
              onChange(v)
            },
          },
          text
        )
      )
    }
    paint()
    return box
  }

  function input(o: {
    value: string | number
    type?: 'number' | 'text'
    label?: string
    onInput: (v: string) => void
  }) {
    const el = h('input', {
      class: 'k-input',
      type: o.type ?? 'number',
      value: o.value,
      'aria-label': o.label,
    }) as HTMLInputElement
    el.addEventListener('input', () => o.onInput(el.value))
    return el
  }

  const button = (text: string, onClick: () => void, opts: { primary?: boolean } = {}) =>
    h('button', { type: 'button', class: opts.primary ? 'k-btn k-btn-primary' : 'k-btn', onclick: onClick }, text)

  const SIGNS = { ok: '✓', no: '✗', warn: '!' } as const
  const chip = (kind: keyof typeof SIGNS, text: string) =>
    h('span', { class: `k-chip k-chip-${kind}` }, `${SIGNS[kind] ?? ''} ${text}`)

  function table(heads: string[], rows: Child[][], opts: { numeric?: number[] } = {}) {
    const num = new Set(opts.numeric ?? [])
    const cls = (i: number) => (num.has(i) ? 'k-num' : null)
    return h(
      'div',
      { class: 'k-table-wrap' },
      h(
        'table',
        { class: 'k-table' },
        h(
          'thead',
          null,
          h(
            'tr',
            null,
            heads.map((x, i) => h('th', { class: cls(i) }, x))
          )
        ),
        h(
          'tbody',
          null,
          rows.map((r) =>
            h(
              'tr',
              null,
              r.map((c, i) => h('td', { class: cls(i) }, c))
            )
          )
        )
      )
    )
  }

  const stat = (name: string, value: Child, delta?: Child) =>
    h(
      'div',
      { class: 'k-stat' },
      h('div', { class: 'k-stat-name' }, name),
      h('div', { class: 'k-stat-value' }, value),
      delta == null ? null : h('div', { class: 'k-stat-delta' }, delta)
    )

  function chartTheme(): object {
    const axis = {
      axisLine: { lineStyle: { color: color('line') } },
      axisTick: { lineStyle: { color: color('line') } },
      axisLabel: { color: color('muted') },
      splitLine: { lineStyle: { color: color('line') } },
      nameTextStyle: { color: color('muted') },
    }
    return {
      color: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => color(`chart-${i}`)),
      backgroundColor: 'transparent',
      textStyle: { color: color('text'), fontFamily: color('font-sans') },
      title: { textStyle: { color: color('ink') }, subtextStyle: { color: color('muted') } },
      legend: { textStyle: { color: color('text') } },
      tooltip: { backgroundColor: color('surface'), borderColor: color('line'), textStyle: { color: color('text') } },
      categoryAxis: axis,
      valueAxis: axis,
      logAxis: axis,
      timeAxis: axis,
    }
  }

  type Chart = HTMLElement & { update: (option: object) => void }
  const charts = new Set<{ el: HTMLElement; draw: () => void }>()

  /** An ECharts chart in the document's colours; `update(option)` redraws it. */
  function chart(option: object, opts: { height?: number } = {}): Chart {
    const el = h('div', { class: 'k-chart', style: { height: `${opts.height ?? 260}px` } }) as Chart
    let current = option
    let instance: ECharts | null = null
    // ECharts' own margins leave a short chart little room to plot in; and the
    // pictures taken when a version is kept must show the chart drawn, not
    // half-way through its entrance.
    const settled = (o: object) => ({
      grid: { left: 8, right: 16, top: 28, bottom: 8, containLabel: true },
      ...(host.mode() === 'shot' ? { animation: false } : {}),
      ...o,
    })
    const draw = () => {
      instance?.dispose()
      instance = host.echarts.init(el, chartTheme(), { renderer: 'svg' })
      instance.setOption(settled(current), true)
    }
    el.update = (next) => {
      current = next
      if (instance) instance.setOption(settled(current), true)
      else draw()
    }
    // Drawn once the element is in the page and has a width.
    const sized = new ResizeObserver(() => {
      if (!el.isConnected || !el.clientWidth) return
      if (instance) instance.resize()
      else draw()
    })
    sized.observe(el)
    charts.add({ el, draw: () => instance && draw() })
    return el
  }

  /** A formula, written in LaTeX. */
  function math(tex: string, opts: { display?: boolean } = {}): HTMLElement {
    const el = h(opts.display ? 'div' : 'span', { class: 'k-math' })
    host.katex.render(tex, el, { displayMode: !!opts.display, throwOnError: false })
    return el
  }

  host.on('theme', () => {
    for (const c of charts) if (c.el.isConnected) c.draw()
  })

  return {
    h,
    row,
    field,
    slider,
    select,
    segmented,
    input,
    button,
    chip,
    table,
    stat,
    chart,
    math,
    color,
    /** 'inline' in the document, 'full' when it fills the screen. */
    mode: host.mode,
    /** `kit.on('mode', fn)`, `kit.on('theme', fn)`. */
    on: host.on,
  }
}
