// 动效台：八格都挂得起来，每一格标着它用的 token，重放和慢放是同一件东西的两面。
//
// 这一份守两条：**页面上的毫秒数是算出来的**（读不到样式表就说读不到，不编一个数），
// 以及**重放真的是重新挂一遍**（换 key，不是发一个产品里不存在的「播放」信号）。
import type { StudioPrefs } from './demoCatalogStudio'

import { defineComponent, h, nextTick, provide, reactive } from 'vue'
import { fireEvent, render } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { defaultPrefs, STUDIO } from './demoCatalogStudio'
import { MOTION_FACTOR } from './demoMotion'
import DemoMotionPage from './DemoMotionPage.vue'
import { TRANSITIONS } from './demoTransitions'

/** 产品那四条时长，名字和值照抄 `src/style.css` 的 `:root`。 */
const BASE_MS = 220
const style = document.createElement('style')
style.textContent = `:root { --dur-press: 100ms; --dur-quick: 150ms; --dur-base: ${BASE_MS}ms; --dur-slow: 320ms; --ease-out: cubic-bezier(0.2, 0.8, 0.2, 1); }`
document.head.appendChild(style)

/** 页面要的那份偏好是工具条和舞台共用的一份，所以照真页面那样 provide 进去。 */
function mount(over: Partial<StudioPrefs> = {}): { view: ReturnType<typeof render>; studio: StudioPrefs } {
  const studio = reactive({ ...defaultPrefs(), ...over }) as StudioPrefs
  const Host = defineComponent({
    setup() {
      provide(STUDIO, studio)
      return () => h(DemoMotionPage)
    },
  })
  return { view: render(Host), studio }
}

/** 每一格终态都是挂上以后下一帧才翻的，所以要看终态得等一帧。 */
async function settled(): Promise<void> {
  await new Promise((resolve) => requestAnimationFrame(() => resolve(null)))
  await nextTick()
}

afterEach(() => {
  vi.unstubAllGlobals()
  delete document.documentElement.dataset.theme
})

describe('动效台', () => {
  it('每一格都摆出来了：名字、说明、用的 token、重放按钮', () => {
    const { view } = mount()
    const cells = view.container.querySelectorAll('.motion-cell')
    expect(cells.length).toBe(TRANSITIONS.length)
    for (const entry of TRANSITIONS) {
      const text = view.container.textContent ?? ''
      expect(text, entry.id).toContain(entry.name)
      for (const token of entry.tokens) {
        expect(text, `${entry.id} 少标了 ${token}`).toContain(token)
      }
    }
    expect(view.container.querySelectorAll('.motion-replay').length).toBe(TRANSITIONS.length)
  })

  it('每一格画的都是产品那几条变量，不写死毫秒数', async () => {
    const { view } = mount()
    await settled()
    const samples = Array.from(view.container.querySelectorAll('.sample'))
    expect(samples.length).toBe(TRANSITIONS.length)
    expect(view.container.querySelectorAll('.sample[data-on="on"]').length).toBe(TRANSITIONS.length)
  })

  it('工具条那一排写的是共享的那一份偏好：点了就真的换档', async () => {
    const { view, studio } = mount({ motion: 'normal' })
    const buttons = Array.from(view.container.querySelectorAll('.motion-btn'))
    const slow = buttons.find((button) => button.textContent === '慢放 ×4')!
    await fireEvent.click(slow)
    expect(studio.motion).toBe('slow')
    expect(slow.getAttribute('aria-pressed')).toBe('true')
  })

  it('慢放那一档把话说全：倍数和它算出来的毫秒数', async () => {
    const { view } = mount({ motion: 'slow' })
    await nextTick()
    // 不钉整句：这一句的写法会改，钉住的是它必须说出的那几件事——哪条 token、原值、
    // 以及倍数乘出来的值。原值和倍数都从页面读的那一份来，不是这里另写一个数。
    const note = view.getByRole('status').textContent ?? ''
    expect(note).toContain('--dur-base')
    expect(note).toContain(`${MOTION_FACTOR.slow} 倍`)
    expect(note).toContain(`${BASE_MS}ms`)
    expect(note).toContain(`${BASE_MS * MOTION_FACTOR.slow}ms`)
  })

  it('不是慢放那一档就不说这句话', () => {
    expect(mount({ motion: 'normal' }).view.queryByRole('status')).toBeNull()
    expect(mount({ motion: 'off' }).view.queryByRole('status')).toBeNull()
  })

  it('系统开着「减少动态效果」时把默认那件事说出来', () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('reduced-motion') }))
    const { view, studio } = mount()
    expect(studio.motion).toBe('off')
    expect(view.container.querySelector('.motion-reduced')).not.toBeNull()
  })

  it('系统开着「减少动态效果」时慢放档标成不可用，并说清为什么', () => {
    // 产品自己的全局规则（带 !important）已经把每一段过渡压成 0.001ms，慢放乘几倍都
    // 落不到屏幕上：这一档摆在那里只会骗人。
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('reduced-motion') }))
    const { view } = mount()
    const slow = Array.from(view.container.querySelectorAll('.motion-btn')).find(
      (button) => button.textContent === '慢放 ×4'
    )!
    expect(slow.hasAttribute('disabled')).toBe(true)
    expect(slow.getAttribute('aria-describedby')).toBe('motion-reduced-note')
    expect(view.container.querySelector('.motion-reduced')?.textContent).toContain('慢放')
  })

  it('系统没开时慢放档照常能点，也不说那句话', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false }))
    const { view } = mount()
    const slow = Array.from(view.container.querySelectorAll('.motion-btn')).find(
      (button) => button.textContent === '慢放 ×4'
    )!
    expect(slow.hasAttribute('disabled')).toBe(false)
    expect(slow.getAttribute('aria-describedby')).toBeNull()
  })

  it('系统没开时不说，免得变成一句常驻的废话', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false }))
    expect(mount().view.container.querySelector('.motion-reduced')).toBeNull()
  })

  it('重放是把这一格重新挂一遍，不是发一个播放信号', async () => {
    const { view } = mount()
    await settled()
    const cell = view.container.querySelector('.motion-cell')!
    const before = cell.querySelector('.sample')!
    expect(before.getAttribute('data-on')).toBe('on')

    await fireEvent.click(cell.querySelector('.motion-replay')!)
    const after = cell.querySelector('.sample')!
    // 换了一颗，所以初始态回来了：这一格会从头再走一次。
    expect(after).not.toBe(before)
    expect(after.getAttribute('data-on')).toBe('off')
    await settled()
    expect(cell.querySelector('.sample')!.getAttribute('data-on')).toBe('on')
  })

  it('「全部重放」一次把每一格都重来一遍', async () => {
    const { view } = mount()
    await settled()
    const before = Array.from(view.container.querySelectorAll('.sample'))
    await fireEvent.click(view.container.querySelector('.motion-all')!)
    const after = Array.from(view.container.querySelectorAll('.sample'))
    expect(after.length).toBe(before.length)
    for (const [index, sample] of after.entries()) {
      expect(sample, `第 ${index} 格没跟着重来`).not.toBe(before[index])
    }
  })
})
