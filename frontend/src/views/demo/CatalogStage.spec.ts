// 一格的舞台：按工具条选的档画。
//
// 三档设备是**真 px 的 iframe**，跟随容器是就地渲染。这两种画法在这一份里都要看得见——
// 用缩放的 div 假扮设备宽度，产品里按窗口切的布局根本不会切。
import type { CatalogEntry } from './catalog'
import type { StudioPrefs } from './demoCatalogStudio'

import { defineComponent, h, nextTick, provide, reactive } from 'vue'
import { render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CatalogStage from './CatalogStage.vue'
import { defaultPrefs, STUDIO } from './demoCatalogStudio'

import { setIframePageLoading } from '@/test/happyDom'

vi.mock('./catalog', () => ({
  stateProps: (entry: { args?: Record<string, unknown> }, state: { props?: Record<string, unknown> }) => ({
    ...entry.args,
    ...state.props,
  }),
}))

const ENTRY: CatalogEntry = {
  id: 'probe',
  title: '探针',
  about: '一格的舞台',
  file: 'src/Probe.vue',
  needs: [],
  states: [{ name: '默认', note: '就这一格', props: {} }],
  component: { template: '<i class="probe">预览</i>' },
}

/** 偏好是工具条和舞台共用的一份，所以测试里也照真页面那样 provide 进去。 */
function mount(over: Partial<StudioPrefs> = {}, index = 0): { view: ReturnType<typeof render>; studio: StudioPrefs } {
  const studio = reactive({ ...defaultPrefs(), ...over }) as StudioPrefs
  const Host = defineComponent({
    setup() {
      provide(STUDIO, studio)
      return () => h(CatalogStage, { entry: ENTRY, state: ENTRY.states[0], index, replay: 0 })
    },
  })
  return { view: render(Host), studio }
}

beforeEach(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    }
  )
  // 框里那个地址测试里取不到；不去取，免得抛出一条没人接的错。
  setIframePageLoading(true)
})

afterEach(() => {
  setIframePageLoading(false)
  vi.unstubAllGlobals()
})

describe('舞台', () => {
  it('跟随容器：就地渲染，不套框——这一页本身也被文档嵌着看', () => {
    const { view } = mount()
    expect(view.container.querySelectorAll('iframe').length).toBe(0)
    expect(view.container.querySelector('.stage-live .probe')).not.toBeNull()
  })

  it('桌面这一档是真 px 的 iframe，地址上带着主题、动效和底', () => {
    const { view } = mount({ device: 'desktop', theme: 'dark', motion: 'slow', bg: 'canvas' })
    const frames = view.container.querySelectorAll('iframe')
    expect(frames.length).toBe(1)
    expect(frames[0].getAttribute('src')).toBe('/demo/catalog/probe/frame/0?theme=dark&motion=slow&bg=canvas')
    expect(frames[0].style.width).toBe('1280px')
    expect(view.container.textContent).toContain('桌面 1280')
  })

  it('手机这一档就是窄的真宽度', () => {
    const { view } = mount({ device: 'phone' })
    expect(view.container.querySelector('iframe')!.style.width).toBe('390px')
  })

  it('双端并排：宽窄各一台，窄的不是缩出来的', () => {
    const { view } = mount({ dual: true })
    const frames = Array.from(view.container.querySelectorAll('iframe'))
    expect(frames.map((f) => f.style.width)).toEqual(['1280px', '390px'])
  })

  it('底色跟着工具条走：产品里这两种底的地方都有', async () => {
    const { view, studio } = mount()
    expect(view.container.querySelector('.stage')!.getAttribute('data-bg')).toBe('surface')
    studio.bg = 'canvas'
    await nextTick()
    expect(view.container.querySelector('.stage')!.getAttribute('data-bg')).toBe('canvas')
  })
})
