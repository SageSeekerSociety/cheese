// 单组件页：三栏 + 粘顶的工具条。
//
// 要守的是工具条那四段真的改得动画面：设备那一段换的是**真 px 的 iframe**，主题和动效
// 是整篇文档一档（这里断言偏好本身，落到文档根上是 DemoCatalog 的事），底色换的是舞台
// 那一层的底。
import type { Component } from 'vue'
import type { CatalogEntry } from './catalog'
import type { StudioPrefs } from './demoCatalogStudio'

import { defineComponent, h, provide, reactive } from 'vue'
import { fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CatalogDetailPage from './CatalogDetailPage.vue'
import { defaultPrefs, STUDIO } from './demoCatalogStudio'

import { setIframePageLoading } from '@/test/happyDom'

// 同一个对象挂在两个字段下（共用，不是环）：参数表要两处都写得出来。
export const REUSED = { where: 'reused' }

vi.mock('./catalog', () => ({
  stateProps: (
    entry: { args?: Record<string, unknown> },
    state: { name?: string; props?: Record<string, unknown> }
  ) => ({
    ...entry.args,
    ...state.props,
    // 第一格额外带两个指向同一个对象的字段。
    ...(state.name === '默认' ? { left: REUSED, right: REUSED } : {}),
  }),
}))

const ENTRY: CatalogEntry = {
  id: 'probe',
  title: '探针',
  about: '三格，用来看工具条',
  file: 'src/components/Probe.vue',
  needs: ['vuetify'],
  args: { tone: 'quiet' },
  states: [
    { name: '默认', note: '第一格', props: {} },
    { name: '长名字', note: '第二格', props: { long: true } },
    { name: '没有 handle', note: '第三格', props: {} },
  ],
  component: { template: '<i class="probe">预览</i>' },
}

function mount(over: Partial<StudioPrefs> = {}): { view: ReturnType<typeof render>; studio: StudioPrefs } {
  const studio = reactive({ ...defaultPrefs(), ...over }) as StudioPrefs
  const Host = defineComponent({
    setup() {
      provide(STUDIO, studio)
      return () => h(CatalogDetailPage as Component, { entry: ENTRY })
    },
  })
  return { view: render(Host), studio }
}

/** 工具条里某一段的第 n 个按钮。 */
function button(view: ReturnType<typeof render>, group: string, label: string): HTMLElement {
  const box = view.container.querySelector(`.ptool-group[aria-label="${group}"]`)!
  const hit = Array.from(box.querySelectorAll('button')).find((b) => b.textContent?.trim() === label)
  expect(hit, `工具条上没找到「${group}」里的「${label}」`).toBeTruthy()
  return hit as HTMLElement
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
  // 框里那些地址测试里取不到；不去取，免得抛出一条没人接的错。
  setIframePageLoading(true)
})

afterEach(() => {
  setIframePageLoading(false)
  vi.unstubAllGlobals()
})

describe('单组件页', () => {
  it('每一格一小节，编号和「这里只装」都写清；右边那一栏列同样的几格', () => {
    const { view } = mount()
    const names = Array.from(view.container.querySelectorAll('.catalog-case-name')).map((el) => el.textContent)
    expect(names).toEqual(['默认', '长名字', '没有 handle'])
    expect(Array.from(view.container.querySelectorAll('.catalog-case-index')).map((el) => el.textContent)).toEqual([
      '#1',
      '#2',
      '#3',
    ])
    expect(view.container.querySelectorAll('.catalog-case').length).toBe(3)
    const toc = Array.from(view.container.querySelectorAll('.doc-toc-link'))
    expect(toc.map((a) => a.textContent)).toEqual(names)
    expect(toc.map((a) => a.getAttribute('href'))).toEqual(['#case-0', '#case-1', '#case-2'])
  })

  it('默认跟随容器：三格都是就地渲染，一个 iframe 都没有', () => {
    const { view } = mount()
    expect(view.container.querySelectorAll('iframe').length).toBe(0)
    expect(view.container.querySelectorAll('.stage-live .probe').length).toBe(3)
  })

  it('挑手机这一档，每一格都换成 390 的真 px iframe', async () => {
    const { view } = mount()
    await fireEvent.click(button(view, '设备', '手机 390'))
    const frames = Array.from(view.container.querySelectorAll('iframe'))
    expect(frames.length).toBe(3)
    expect(frames.map((f) => f.style.width)).toEqual(['390px', '390px', '390px'])
    // 第几格就取第几格，地址上带着这一档的主题、动效和底。
    expect(frames[1].getAttribute('src')).toBe('/demo/catalog/probe/frame/1?theme=light&motion=normal&bg=surface')
    expect(view.container.querySelectorAll('.stage-live').length).toBe(0)
  })

  it('双端并排是第五档，不是某一台设备：每格两台', async () => {
    const { view } = mount()
    await fireEvent.click(button(view, '设备', '双端并排'))
    const frames = Array.from(view.container.querySelectorAll('iframe'))
    expect(frames.length).toBe(6)
    expect(frames.slice(0, 2).map((f) => f.style.width)).toEqual(['1280px', '390px'])
  })

  it('换了主题，框里取的地址就跟着换（iframe 是另一篇文档）', async () => {
    const { view, studio } = mount({ device: 'desktop' })
    await fireEvent.click(button(view, '主题', '深色'))
    expect(studio.theme).toBe('dark')
    expect(view.container.querySelector('iframe')!.getAttribute('src')).toContain('theme=dark')
  })

  it('动了效，每一格的地址也跟着换', async () => {
    const { view, studio } = mount({ device: 'phone' })
    await fireEvent.click(button(view, '动效', '慢放 ×4'))
    expect(studio.motion).toBe('slow')
    expect(view.container.querySelector('iframe')!.getAttribute('src')).toContain('motion=slow')
  })

  it('底色换的是舞台那一层，框里也带上了', async () => {
    const { view, studio } = mount({ device: 'tablet' })
    await fireEvent.click(button(view, '底色', '画布'))
    expect(studio.bg).toBe('canvas')
    expect(view.container.querySelector('.stage')!.getAttribute('data-bg')).toBe('canvas')
    expect(view.container.querySelector('iframe')!.getAttribute('src')).toContain('bg=canvas')
  })

  it('工具条上按下的那一个标出来，其余不标', async () => {
    const { view } = mount()
    const phone = button(view, '设备', '手机 390')
    expect(button(view, '设备', '跟随容器').getAttribute('aria-pressed')).toBe('true')
    await fireEvent.click(phone)
    expect(phone.getAttribute('aria-pressed')).toBe('true')
    expect(button(view, '设备', '跟随容器').getAttribute('aria-pressed')).toBe('false')
  })

  it('两个字段指同一个对象时照原样写，不误报成环', () => {
    const { view } = mount()
    const body = view.container.querySelector('.case-args-body')?.textContent ?? ''
    // 这一格参数里的 `shared` 被两个字段引着：是共用，不是环，两处都该写得出来。
    expect(body.match(/reused/g)?.length).toBe(2)
    expect(body).not.toContain('循环引用')
  })

  it('每一格都写着参数，能序列化的就照原样给人看', () => {
    const { view } = mount()
    const bodies = Array.from(view.container.querySelectorAll('.case-args-body')).map((el) => el.textContent ?? '')
    expect(bodies[0]).toContain('"tone": "quiet"')
    expect(bodies[1]).toContain('"long": true')
    expect(view.container.querySelectorAll('.case-replay').length).toBe(3)
  })
})
