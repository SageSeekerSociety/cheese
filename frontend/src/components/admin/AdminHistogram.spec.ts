/**
 * AdminHistogram（token 分布）。
 *
 * 这张图上「读不出来」的坏法有两种，两种都不会报错，只是看着不对：
 *
 * 1. **空桶画成没有**：某个区间一条都没有时如果不画那一行，桶与桶之间的**位置**就错
 *    位了 —— 读者看到的是一根根挨着的条，而它们其实隔着好几个空区间。这里钉的是
 *    「零桶也在、还是那一行，只是长度为 0」。
 * 2. **极小值看不见**：一根 3 像素的条和「没有这一项」在屏幕上分不出来，而桶里确实
 *    有数。所以非零桶有 2% 的下限（同 `AdminBarChart`）。
 *
 * 桶的标签用全值（`1,200–2,400`）不用缩写：缩写会让相邻两桶在窄区间时撞成同一个
 * 标签，读的人以为是重复的两行。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

// 键名透传：断言直接写键名，不跟着中文文案改。
vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import AdminHistogram from './AdminHistogram.vue'

const ROWS = [
  { from: 0, to: 1200, count: 3 },
  { from: 1200, to: 2400, count: 0 },
  { from: 2400, to: 3600, count: 1 },
]

function mount(props: Record<string, unknown> = {}) {
  return render(AdminHistogram, {
    props: { title: 'token 分布', note: '口径', rows: ROWS, ...props },
    // 卡片里的 `AdminNoteTip` / `AdminEmptyState` 是 Vuetify 组件，没有插件就报
    // 「Could not find defaults instance」。
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

/** 某一行的条宽（行按标签文本找）。 */
function barWidth(container: Element, range: string): string {
  const row = Array.from(container.querySelectorAll('.ahist__row')).find(
    (el) => el.querySelector('.ahist__range')?.textContent?.trim() === range
  )
  return (row?.querySelector('.ahist__bar') as HTMLElement | undefined)?.style.width ?? ''
}

describe('AdminHistogram', () => {
  it('桶的区间用全值写出来，零桶也在（位置不能错位）', () => {
    const { container } = mount()
    const ranges = Array.from(container.querySelectorAll('.ahist__range')).map((el) => el.textContent?.trim())
    expect(ranges).toEqual(['0–1,200', '1,200–2,400', '2,400–3,600'])
  })

  it('条长按桶内条数相对最大桶算，非零桶有 2% 的下限', () => {
    const { container } = mount()
    expect(barWidth(container, '0–1,200')).toBe('100%')
    // 最大桶的三分之一，远在 2% 之上。
    expect(barWidth(container, '2,400–3,600')).toBe('33.33333333333333%')
    // 零桶是真的 0（不是下限）：它和「桶里有数」必须分得开。
    expect(barWidth(container, '1,200–2,400')).toBe('0%')
  })

  it('极小值看得见：一条的窗口里那一条是满条，窗口放大到一百条时仍有 2%', () => {
    const many = [
      { from: 0, to: 1, count: 1 },
      { from: 1, to: 2, count: 99 },
    ]
    const { container } = mount({ rows: many })
    expect(barWidth(container, '0–1')).toBe('2%')
    expect(barWidth(container, '1–2')).toBe('100%')
  })

  it('条数按千分位分组，单位跟在后面', () => {
    const { container } = mount({ unit: '条', rows: [{ from: 0, to: 10, count: 1234 }] })
    expect(container.querySelector('.ahist__count')?.textContent?.trim()).toBe('1,234条')
  })

  it('没有桶时画破折号，不画一张空图', () => {
    const { container } = mount({ rows: [] })
    expect(container.querySelector('.ahist__none')?.textContent?.trim()).toBe('—')
    expect(container.querySelectorAll('.ahist__row')).toHaveLength(0)
  })

  it('加载中画骨架，不画零桶（两者都会被读成「这一格是 0」）', () => {
    const { container } = mount({ loading: true })
    expect(container.querySelectorAll('.ahist__bone').length).toBeGreaterThan(0)
    expect(container.querySelectorAll('.ahist__row')).toHaveLength(0)
    expect(container.querySelector('.ahist__none')).toBeNull()
  })
})
