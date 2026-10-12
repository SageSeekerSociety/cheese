// 目录页每张卡上面那一小格：真组件，但只在看得见的时候挂，离开视口就卸掉。
//
// 目录里有 273 格，全挂上就是 273 个真组件同时跑。这一份守的就是那条懒挂：进视口才
// 有东西，出视口就没了；传送到 body 的那些（对话框）永远不挂——它们会盖住整页。
import type { CatalogEntry } from './catalog'

import { nextTick } from 'vue'
import { render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CatalogThumb from './CatalogThumb.vue'

// 这一件只用到 `stateProps`，不去拉整份注册表（那一片是 catalog.spec.ts 的事）。
vi.mock('./catalog', () => ({
  stateProps: (entry: { args?: Record<string, unknown> }, state: { props?: Record<string, unknown> }) => ({
    ...entry.args,
    ...state.props,
  }),
}))

/** 看得见 / 看不见由测试来说了算。 */
class FakeObserver {
  static made: FakeObserver[] = []
  readonly targets: Element[] = []
  constructor(private readonly cb: IntersectionObserverCallback) {
    FakeObserver.made.push(this)
  }
  observe(el: Element): void {
    this.targets.push(el)
  }
  unobserve(): void {}
  disconnect(): void {
    this.targets.length = 0
  }
  /** 把这一档的观察对象按「看得见 / 看不见」报一次。 */
  async say(isIntersecting: boolean): Promise<void> {
    this.cb(
      this.targets.map((target) => ({ target, isIntersecting, intersectionRatio: isIntersecting ? 1 : 0 })) as never,
      this as never
    )
    await nextTick()
  }
}

function entry(over: Partial<CatalogEntry> = {}): CatalogEntry {
  return {
    id: 'probe',
    title: '探针',
    about: '用来量懒挂的那一件',
    file: 'src/Probe.vue',
    needs: [],
    states: [{ name: '默认', note: '就这一格', props: {} }],
    component: { template: '<i class="probe">预览</i>' },
    ...over,
  }
}

beforeEach(() => {
  FakeObserver.made = []
  vi.stubGlobal('IntersectionObserver', FakeObserver)
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    }
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('卡片上的那一小格', () => {
  it('一开始不挂：还没进视口', async () => {
    const view = render(CatalogThumb, { props: { entry: entry() } })
    await nextTick()
    expect(FakeObserver.made.length).toBe(1)
    expect(view.container.querySelector('.thumb-shot')).toBeNull()
    expect(view.container.textContent).toBe('')
  })

  it('进了视口才挂上，出了视口就卸掉', async () => {
    const view = render(CatalogThumb, { props: { entry: entry() } })
    await nextTick()
    await FakeObserver.made[0].say(true)
    expect(view.container.querySelector('.probe')).not.toBeNull()
    await FakeObserver.made[0].say(false)
    expect(view.container.querySelector('.thumb-shot')).toBeNull()
    expect(view.container.querySelector('.probe')).toBeNull()
  })

  it('挂在里面的真组件不抢 Tab：读屏软件读的是卡片上的名字和说明', async () => {
    const view = render(CatalogThumb, { props: { entry: entry() } })
    await nextTick()
    await FakeObserver.made[0].say(true)
    const host = view.container.querySelector('.thumb')!
    expect(host.hasAttribute('inert')).toBe(true)
    expect(host.getAttribute('aria-hidden')).toBe('true')
  })

  it('传送到 body 的那些不挂也不看视口：它们会盖住整页', async () => {
    const view = render(CatalogThumb, { props: { entry: entry({ teleport: true }) } })
    await nextTick()
    expect(FakeObserver.made.length).toBe(0)
    expect(view.container.querySelector('.thumb-shot')).toBeNull()
    expect(view.container.querySelector('.thumb')).not.toBeNull()
  })

  it('一格状态都没有的条目也不挂', async () => {
    const view = render(CatalogThumb, { props: { entry: entry({ states: [] }) } })
    await nextTick()
    await FakeObserver.made[0].say(true)
    expect(view.container.querySelector('.thumb-shot')).toBeNull()
  })

  it('环境里没有 IntersectionObserver 就一直挂着：宁可费一点也不能空着', async () => {
    vi.stubGlobal('IntersectionObserver', undefined)
    const view = render(CatalogThumb, { props: { entry: entry() } })
    await nextTick()
    expect(view.container.querySelector('.probe')).not.toBeNull()
  })
})
