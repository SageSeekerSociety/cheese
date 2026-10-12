// iframe 里那一页：只画一格，不带站的骨架。
//
// 它和外面那一页是**两篇文档**，所以主题、动效、底都得在它自己身上落地，并且把量出来
// 的内容高度报回外面（外面拿它定框高）。
import type { CatalogEntry } from './catalog'

import { render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CatalogFramePage from './CatalogFramePage.vue'
import { FRAME_MESSAGE } from './demoCatalogStudio'

vi.mock('./catalog', () => ({
  stateProps: (entry: { args?: Record<string, unknown> }, state: { props?: Record<string, unknown> }) => ({
    ...entry.args,
    ...state.props,
  }),
}))

const ENTRY: CatalogEntry = {
  id: 'probe',
  title: '探针',
  about: '一格',
  file: 'src/Probe.vue',
  needs: [],
  states: [{ name: '默认', note: '就这一格', props: {} }],
  component: { template: '<i class="probe">预览</i>' },
}

let posted: ReturnType<typeof vi.fn>

beforeEach(() => {
  posted = vi.fn()
  vi.spyOn(window, 'parent', 'get').mockReturnValue({ postMessage: posted } as never)
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
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.documentElement.removeAttribute('style')
  delete document.documentElement.dataset.motion
})

function mount(motion: 'normal' | 'slow' | 'off' = 'normal', bg: 'surface' | 'canvas' = 'surface') {
  return render(CatalogFramePage, { props: { entry: ENTRY, state: ENTRY.states[0], motion, bg } })
}

describe('iframe 里的那一页', () => {
  it('只画这一格，站的骨架都不进来', () => {
    const view = mount()
    expect(view.container.querySelector('.probe')).not.toBeNull()
    expect(view.container.querySelector('.studio-top')).toBeNull()
    expect(view.container.querySelector('.rail')).toBeNull()
  })

  it('动效落在自己的文档根上，不是外面那一篇', () => {
    mount('off')
    expect(document.documentElement.dataset.motion).toBe('off')
  })

  it('底色落在 html 上：内容比框矮的时候，下面那截不能是浏览器默认的白', () => {
    const view = mount('normal', 'canvas')
    expect(document.documentElement.style.background).toContain('canvas')
    expect(view.container.querySelector('.frame-page')!.getAttribute('data-bg')).toBe('canvas')
  })

  it('画完把高度报回外面，外面拿它定框高', () => {
    mount()
    expect(posted).toHaveBeenCalledTimes(1)
    const [message, origin] = posted.mock.calls[0]
    expect(message.source).toBe(FRAME_MESSAGE)
    expect(typeof message.height).toBe('number')
    expect(origin).toBe(window.location.origin)
  })
})
