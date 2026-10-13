/**
 * AdminLineChart（看板折线图 v2）。
 *
 * 这一组钉四件「读不出数」的修法，每一件都对着重设计前的一种坏法：
 *
 * * **hover 有读数**：mousemove 之后 tooltip 显示该日各系列的 `fmtNum` 全值
 *   （此前 hover 只有一条竖参考线，想读数只能展开折叠的数据表）。
 * * **y 轴四个刻度、相邻等值去重**：max=1 时中间档会和两端撞成同一个数
 *   （1/1/0/0），重画一遍只是在同一个位置叠两遍字。
 * * **x 轴抽稀**：7 天全标、30 天标 7 个、90 天不画点（90 个点在 1400px 宽下
 *   间距 ~15px，实心圆会糊成一条粗线）。
 * * **真实像素渲染**：画布宽由 ResizeObserver 驱动（不再 viewBox 等比缩放 ——
 *   宽档容器会把 12px 轴字放大到 16px，字号脱离档位）。
 *
 * happy-dom 不做布局，两个环境缺件由这个文件自己补：ResizeObserver 换成**能手动
 * 触发**的实现（画布宽由它驱动，见 `FakeResizeObserver.trigger`），SVG 的
 * `getBoundingClientRect` 给一个固定宽（hover 的「指针落在哪一天」才算得出来）。
 */
import type { ChartSeries } from './AdminLineChart.vue'

import { nextTick } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import AdminLineChart from './AdminLineChart.vue'

import i18n, { setLocale } from '@/i18n'

/** 能手动触发的 ResizeObserver（范本：`ChatPanel.keyboardScroll.spec.ts` 的那一份）：
 *  组件挂载时注册回调，用例按 `trigger(宽)` 模拟容器变宽。 */
class FakeResizeObserver {
  static instances: FakeResizeObserver[] = []

  private readonly cb: ResizeObserverCallback

  constructor(cb: ResizeObserverCallback) {
    this.cb = cb
    FakeResizeObserver.instances.push(this)
  }

  observe() {}
  unobserve() {}
  disconnect() {}

  trigger(width: number) {
    this.cb([{ contentRect: { width } } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver)
  }
}

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', FakeResizeObserver)
  setLocale('zh-CN')
  // happy-dom 的 `getBoundingClientRect` 全是 0：画布 628 宽、左边在 0，hover 的
  // 「指针落在哪一天」按它换算（组件的画布默认宽也是 628，两个 628 是同一把尺）。
  SVGElement.prototype.getBoundingClientRect = () =>
    ({
      width: 628,
      height: 180,
      left: 0,
      top: 0,
      right: 628,
      bottom: 180,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    }) as DOMRect
})

beforeEach(() => {
  FakeResizeObserver.instances = []
})

function mountChart(props: { xLabels: string[]; series: ChartSeries[]; title?: string }) {
  const vuetify = createVuetify({ components, directives })
  return render(AdminLineChart, {
    props: { title: props.title ?? '测试图', xLabels: props.xLabels, series: props.series },
    global: { plugins: [vuetify, i18n] },
  })
}

/** 七个点的窗口（7 天全标的那一档）。 */
const SEVEN = {
  xLabels: ['d1', 'd2', 'd3', 'd4', 'd5', 'd6', 'd7'],
  series: [{ name: '新增', values: [1, 2, 3, 4, 5, 6, 7], style: 'solid' as const }],
}

/** 轴上某个刻字出现了几次（按精确文本匹配，x 轴与 y 轴都算；模板插值自带换行
 *  缩进，所以比之前先 trim）。**只数轴字**（`.alc__ink`）：末点直标（`.alc__endlabel`）
 *  是结论不是刻度，混进来会把「最后一天」数成两个。 */
function tickCount(container: Element, text: string): number {
  return Array.from(container.querySelectorAll('.alc__plot text.alc__ink')).filter(
    (el) => el.textContent?.trim() === text
  ).length
}

describe('AdminLineChart', () => {
  it('hover 后 tooltip 显示该日各系列的 fmtNum 全值，移走消失', async () => {
    const { container } = mountChart({
      xLabels: ['d1', 'd2', 'd3', 'd4', 'd5', 'd6', 'd7'],
      series: [
        { name: '新增', values: [1, 2, 1234, 4, 5, 6, 7], style: 'solid' },
        { name: '解决', values: [7, 6, 5, 4, 3, 2, 1], style: 'dashed' },
      ],
    })

    // 画布 628、padLeft = 8 + 7 × 5（最长刻度「1,234」五个字符）= 43，plotW = 577，
    // step ≈ 96.17：第三天（index 2）的 x ≈ 235。
    const hit = container.querySelector('.alc__hit')!
    await fireEvent.mouseMove(hit, { clientX: 235, clientY: 40 })

    const tip = container.querySelector('.alc__tooltip')
    expect(tip).toBeTruthy()
    expect(tip!.textContent).toContain('d3')
    expect(tip!.textContent).toContain('新增')
    // fmtNum 全值（不是 SI 缩写）：1,234 一个字符都不少。
    expect(tip!.textContent).toContain('1,234')
    expect(tip!.textContent).toContain('解决')
    expect(tip!.textContent).toContain('5')

    await fireEvent.mouseLeave(hit)
    expect(container.querySelector('.alc__tooltip')).toBeNull()
  })

  it('max=1 时 y 轴不出现重复刻度', () => {
    const { container } = mountChart({
      xLabels: SEVEN.xLabels,
      series: [{ name: '新增', values: [0, 1, 0, 1, 0, 1, 0], style: 'solid' }],
    })

    // 刻度表本来是 1/1/0/0：相邻等值去重后只剩 1 与 0 各一遍 —— 在同一个位置
    // 叠两遍字对谁都没有好处。
    expect(tickCount(container, '1')).toBe(1)
    expect(tickCount(container, '0')).toBe(1)
  })

  it('x 轴抽稀：7 点全标，30 点标 7 个，最后一天总在轴上（有末点直标也一样）', () => {
    // 末点直标画在最后一个点上方，它不替最后一天的日期：点画到了哪一天，轴上就要读得出
    // 那一天 —— 读的人问的正是「今天 / 最近这个小时是多少」。
    const { container } = mountChart(SEVEN)
    for (const label of SEVEN.xLabels) expect(tickCount(container, label)).toBe(1)
    // 直标是主系列的最后一天值（fmtNum 全值），不开 tooltip 也有结论。
    const endLabel = container.querySelector('.alc__endlabel')
    expect(endLabel).toBeTruthy()
    expect(endLabel!.textContent?.trim()).toBe('7')

    const thirty = {
      xLabels: Array.from({ length: 30 }, (_, i) => `x${i}`),
      series: [{ name: '新增', values: Array.from({ length: 30 }, (_, i) => i), style: 'solid' as const }],
    }
    const again = mountChart(thirty)
    // stride = ceil(30/6) = 5：0/5/10/15/20/25 六个 + 最后一天（x29）共 7 个刻度。
    const shown = thirty.xLabels.filter((label) => tickCount(again.container, label) > 0)
    expect(shown).toEqual(['x0', 'x5', 'x10', 'x15', 'x20', 'x25', 'x29'])
    expect(again.container.querySelector('.alc__endlabel')!.textContent?.trim()).toBe('29')
  })

  it('每小时那张图：最后一个小时的标签在轴上，hover 也够得到它', async () => {
    // 7 天窗口的每小时序列 ~160 个点，标签是「10-12 17:00」这种长串。线画到了
    // 10-12 17:00，轴字曾经停在 10-11 18:00。
    const start = Date.UTC(2026, 9, 5, 16)
    const hours = Array.from({ length: 162 }, (_, i) => {
      const d = new Date(start + i * 3600_000)
      const mm = String(d.getUTCMonth() + 1).padStart(2, '0')
      const dd = String(d.getUTCDate()).padStart(2, '0')
      return `${mm}-${dd} ${String(d.getUTCHours()).padStart(2, '0')}:00`
    })
    const last = hours[hours.length - 1]!
    const { container } = mountChart({
      xLabels: hours,
      series: [{ name: '用量', values: hours.map((_, i) => (i * 37) % 101), style: 'solid' }],
    })
    expect(tickCount(container, last)).toBe(1)

    // 指针移到画布最右缘，读数是最后一个小时。
    await fireEvent.mouseMove(container.querySelector('.alc__hit')!, { clientX: 627, clientY: 40 })
    expect(container.querySelector('.alc__tooltip')!.textContent).toContain(last)

    // 手机宽度上两端都在，且轴上的字两两不重叠（12px 轴字一个字符约 7px 宽，第一个
    // 字从它的 x 往右写、最后一个往左写、其余以 x 居中）。
    FakeResizeObserver.instances[0]!.trigger(330)
    await nextTick()
    const ticks = Array.from(container.querySelectorAll('.alc__plot text.alc__ink'))
      .filter((el) => hours.includes(el.textContent!.trim()))
      .map((el) => {
        const at = Number(el.getAttribute('x'))
        const w = 7 * el.textContent!.trim().length
        const anchor = el.getAttribute('text-anchor')
        const left = anchor === 'start' ? at : anchor === 'end' ? at - w : at - w / 2
        return { label: el.textContent!.trim(), left, right: left + w }
      })
    expect(ticks[0]!.label).toBe(hours[0])
    expect(ticks[ticks.length - 1]!.label).toBe(last)
    for (let k = 1; k < ticks.length; k++) expect(ticks[k]!.left).toBeGreaterThanOrEqual(ticks[k - 1]!.right)
  })

  it('窄画布上相邻的日期字不叠在一起，两端仍在', async () => {
    // 390px 宽的统计页：7 天窗口跨进两位数日期，「10/10」和「10/11」曾经画在同一处。
    const week = {
      xLabels: ['10/5', '10/6', '10/7', '10/8', '10/9', '10/10', '10/11'],
      series: [{ name: '用量', values: [3, 3, 3, 3, 3, 3, 3], style: 'solid' as const }],
    }
    const { container } = mountChart(week)
    FakeResizeObserver.instances[0]!.trigger(280)
    await nextTick()

    const shown = week.xLabels.filter((label) => tickCount(container, label) > 0)
    expect(shown[0]).toBe('10/5')
    expect(shown[shown.length - 1]).toBe('10/11')
    expect(shown).not.toContain('10/10')
    expect(shown.length).toBeLessThan(week.xLabels.length)
  })

  it('主系列恒值（含全 0）时不画末点直标', () => {
    // 零流量周标一个「0」像在说「没有数据」，压着底的那条线本身就是答案 —— 恒值线
    // 同理，直标只在「走到哪」有信息量时出现。
    const { container } = mountChart({
      xLabels: SEVEN.xLabels,
      series: [{ name: '新增', values: [0, 0, 0, 0, 0, 0, 0], style: 'solid' }],
    })
    expect(container.querySelector('.alc__endlabel')).toBeNull()
    for (const label of SEVEN.xLabels) expect(tickCount(container, label)).toBe(1)
  })

  it('点数 ≤ 45 画点，> 45 只画线', () => {
    const { container } = mountChart(SEVEN)
    expect(container.querySelectorAll('circle')).toHaveLength(7)

    const ninety = {
      xLabels: Array.from({ length: 90 }, (_, i) => `x${i}`),
      series: [{ name: '新增', values: Array.from({ length: 90 }, (_, i) => i % 10), style: 'solid' as const }],
    }
    const again = mountChart(ninety)
    // 90 个点在 1400px 宽下间距 ~15px，实心圆会糊成一条粗线 —— 只画线。
    expect(again.container.querySelectorAll('circle')).toHaveLength(0)
    expect(again.container.querySelectorAll('.alc__line').length).toBeGreaterThan(0)
  })

  it('画布宽由 ResizeObserver 驱动（并夹在 280–1400）', async () => {
    const { container } = mountChart(SEVEN)
    const svg = container.querySelector('.alc__plot')!
    // RO 回调前是设计宽（也是 stub 了布局的环境里的工作值）。
    expect(svg.getAttribute('width')).toBe('628')

    FakeResizeObserver.instances[0]!.trigger(900)
    await nextTick()
    expect(svg.getAttribute('width')).toBe('900')

    // 两端收口：窄于 280 轴字会互相压，宽于 1400 折线被拉成平坡。
    FakeResizeObserver.instances[0]!.trigger(100)
    await nextTick()
    expect(svg.getAttribute('width')).toBe('280')
    FakeResizeObserver.instances[0]!.trigger(5000)
    await nextTick()
    expect(svg.getAttribute('width')).toBe('1400')
  })
})
