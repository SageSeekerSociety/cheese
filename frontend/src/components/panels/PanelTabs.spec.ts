// 一条栏放不下的时候（手机上六格页签），最后一个可以整个在屏幕外，而滚动没有可见的
// 把手。这一份钉的是：真溢出时才长出那一侧的方向标，点击它就把栏往那一侧滚——和搜索
// 页那条带 › 的栏目同一个意思。
//
// jsdom 不做布局，量不出 scrollWidth / clientWidth，所以这两个值在测试里按盒子各自
// 定义，再补一次 scroll 事件让组件重量一遍。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

import PanelTabs, { type PanelTab } from './PanelTabs.vue'

beforeEach(() => setLocale('zh-CN'))

const TABS: PanelTab[] = [
  { key: 'overview', label: '总览', icon: 'mdi-view-dashboard-outline' },
  { key: 'site', label: '现场', icon: 'mdi-progress-clock' },
  { key: 'changes', label: '改动', icon: 'mdi-file-diff' },
  { key: 'preview', label: '预览', icon: 'mdi-eye-outline' },
  { key: 'routines', label: '定时与触发', icon: 'mdi-timer-outline' },
]

let vuetify: ReturnType<typeof createVuetify>
beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

function measure(bar: Element, opts: { scrollLeft: number; clientWidth: number; scrollWidth: number }) {
  Object.defineProperty(bar, 'scrollLeft', { value: opts.scrollLeft, configurable: true, writable: true })
  Object.defineProperty(bar, 'clientWidth', { value: opts.clientWidth, configurable: true })
  Object.defineProperty(bar, 'scrollWidth', { value: opts.scrollWidth, configurable: true })
}

function mount() {
  return render(PanelTabs, {
    props: { tabs: TABS, active: 'overview', phone: true },
    global: { plugins: [vuetify, i18n] },
  })
}

describe('页签栏的溢出方向标', () => {
  it('放得下时：两侧都没有方向标', async () => {
    const { container, queryByLabelText } = mount()
    const bar = container.querySelector('.tabbar')!
    measure(bar, { scrollLeft: 0, clientWidth: 500, scrollWidth: 500 })
    await fireEvent.scroll(bar)
    expect(queryByLabelText('向右滚动页签')).toBeNull()
    expect(queryByLabelText('向左滚动页签')).toBeNull()
  })

  it('右边还有时：出右方向标，点一下把栏往右滚', async () => {
    const { container, findByLabelText } = mount()
    const bar = container.querySelector('.tabbar') as HTMLElement
    measure(bar, { scrollLeft: 0, clientWidth: 390, scrollWidth: 498 })
    const scrollBy = vi.fn()
    bar.scrollBy = scrollBy
    await fireEvent.scroll(bar)
    const arrow = await findByLabelText('向右滚动页签')
    await fireEvent.click(arrow)
    expect(scrollBy).toHaveBeenCalledWith(expect.objectContaining({ left: expect.any(Number) }))
  })

  it('滚到头：左方向标出现、右方向标退回去', async () => {
    const { container, findByLabelText, queryByLabelText } = mount()
    const bar = container.querySelector('.tabbar')!
    measure(bar, { scrollLeft: 108, clientWidth: 390, scrollWidth: 498 })
    await fireEvent.scroll(bar)
    expect(await findByLabelText('向左滚动页签')).toBeTruthy()
    expect(queryByLabelText('向右滚动页签')).toBeNull()
  })
})
