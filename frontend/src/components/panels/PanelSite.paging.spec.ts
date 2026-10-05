// 切一个队友的视角 = 换一条时间线，不是把手里那一窗就地滤一遍。
//
// 现场的记录和聊天一样是分页的：一次只读最近一页，往上翻才要更早的。按队友看时
// 这条规矩同样成立 —— 请求里带上这个队友，读的仍然只有一页，落点仍然是最新那一
// 条。原来的做法是把手上的时间线在客户端一滤：切过去看到的是**已经加载的全部**，
// 而且停在原地，人就落在「这一次往前翻了多远」的位置上。
//
// happy-dom 不做排版：scrollHeight / clientHeight / scrollTop 恒为 0，所以这里按
// 用例的意思把它们钉住。「停在底部」和「加载更早的一页之后视口不动」都是滚动位置
// 上的事，读的是面板自己写上去的那个数。
import type { Component } from 'vue'
import type { Block, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getTranscript = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getTranscript: (...a: unknown[]) => getTranscript(...a),
    getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false, tasks: {} }),
  }
})

import { SITE_PAGE_SIZE } from '../../api'

import PanelSite from './PanelSite.vue'

import { setLocale } from '@/i18n'

const Site = PanelSite as unknown as Component

const topic = {
  id: 't1',
  project_id: 'p1',
  parent_id: null,
  title: '话题',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
} as Topic

const NAMES = { 'cheese-a1': '芝士', 'cheese-b2': '小苔' }

function step(id: string, author: string, turn: string, at: string, arg: string): Block {
  return {
    id,
    project_id: 'p1',
    topic_id: 't1',
    kind: 'event',
    author_type: 'participant',
    author,
    content: arg,
    reply_to: null,
    refs: [],
    turn_id: turn,
    meta: { tool: 'Bash', arg },
    created_at: at,
  } as unknown as Block
}

/** 「全部」最近的一页：两个队友各一步，于是顶上有那一排 tab。 */
const ALL = [
  step('b1', 'cheese-b2', 'turn-b', '2026-09-25T10:00:00Z', 'make docs'),
  step('a1', 'cheese-a1', 'turn-a', '2026-09-25T10:01:00Z', 'pytest -q'),
]
/** 芝士最近的一页（后端按 author 截的），比「全部」多一条更早的。 */
const PAGE_A = [
  step('a0', 'cheese-a1', 'turn-a', '2026-09-25T10:00:30Z', 'ls docs'),
  step('a1', 'cheese-a1', 'turn-a', '2026-09-25T10:01:00Z', 'pytest -q'),
]
/** 再往前一页。 */
const OLDER_A = [step('z9', 'cheese-a1', 'turn-z', '2026-09-25T09:00:00Z', 'git log')]

function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => (resolve = r))
  return { promise, resolve }
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  setLocale('zh-CN')
  getTranscript.mockReset()
})

type Metrics = { scrollHeight: number; clientHeight: number; scrollTop: number }

/** happy-dom 的元素没有布局：这三个数是浏览器算出来的，这里按用例的意思钉住。 */
function fakeMetrics(el: HTMLElement, initial: Metrics): Metrics {
  const m = { ...initial }
  for (const key of ['scrollHeight', 'clientHeight', 'scrollTop'] as const) {
    Object.defineProperty(el, key, {
      configurable: true,
      get: () => m[key],
      set: (v: number) => {
        m[key] = v
      },
    })
  }
  return m
}

async function flush(times = 8) {
  for (let i = 0; i < times; i += 1) await new Promise((resolve) => setTimeout(resolve, 0))
}

/** 面板把滚动位置推到底是一帧一帧来的（见 siteLog.shouldKeepPinning），等它收工。 */
async function settle() {
  await flush()
  await new Promise((resolve) => setTimeout(resolve, 60))
  await flush()
}

function args(pane: Element): string[] {
  return Array.from(pane.querySelectorAll('[data-testid="site-act-arg"]')).map((e) => e.textContent?.trim() ?? '')
}

function optsOf(call: number): Record<string, unknown> {
  return (getTranscript.mock.calls[call]?.[1] ?? {}) as Record<string, unknown>
}

async function openSite() {
  const view = render(Site, {
    props: { topicId: topic.id, active: true, memberNames: NAMES },
    global: { plugins: [vuetify] },
  })
  const pane = view.container.querySelector<HTMLElement>('.panel-site')
  expect(pane, '找不到现场的滚动容器').toBeTruthy()
  const m = fakeMetrics(pane!, { scrollHeight: 2000, clientHeight: 500, scrollTop: 0 })
  await settle()
  return { view, pane: pane!, m }
}

describe('现场按队友看：一次一页', () => {
  beforeEach(() => {
    getTranscript.mockImplementation(async (_tid: string, opts: Record<string, unknown> = {}) => {
      if (opts.before) return { data: OLDER_A, total: 1, has_more: false }
      if (opts.author === 'cheese-a1') return { data: PAGE_A, total: PAGE_A.length, has_more: true }
      return { data: ALL, total: ALL.length, has_more: true }
    })
  })

  it('第一次打开现场：只读最近一页，不停在更早的地方', async () => {
    const { pane, m } = await openSite()
    await waitFor(() => expect(args(pane)).toEqual(['make docs', 'pytest -q']))

    expect(getTranscript.mock.calls).toHaveLength(1)
    expect(optsOf(0)).toEqual({ limit: SITE_PAGE_SIZE, author: null })
    expect(optsOf(0).before, '打开时不带游标，就是最近那一页').toBeUndefined()
    // 时间线按时间正序渲染，最新的一条在底部。
    expect(m.scrollTop).toBe(m.scrollHeight)
  })

  it('切到某个队友：只按这个队友请求一页，并停在最新那一条', async () => {
    const { view, pane, m } = await openSite()
    await waitFor(() => expect(args(pane)).toEqual(['make docs', 'pytest -q']))
    m.scrollTop = 0 // 先站到顶上，好看出切换之后有没有回到末尾

    await fireEvent.click(view.getByRole('tab', { name: '芝士' }))
    await settle()

    // 一次请求：按 author 过滤、不带游标。切过去看到的是这个人的最近一页 —— 不是
    // 把手上所有人的行就地滤一遍（那既不请求，也把翻过的历史全带过去了）。
    expect(getTranscript.mock.calls, '切一次视角只发一个请求').toHaveLength(2)
    expect(optsOf(1)).toEqual({ limit: SITE_PAGE_SIZE, author: 'cheese-a1' })
    // 手里那两行换成了芝士的最近一页：小苔那条不在，也没有多出翻过的历史。
    expect(args(pane)).toEqual(['ls docs', 'pytest -q'])
    expect(m.scrollTop, '切过去停在最新的那条（底部）').toBe(m.scrollHeight)
  })

  it('切回来「全部」同样只读一页，并且不再带 author', async () => {
    const { view, pane } = await openSite()
    await waitFor(() => expect(args(pane)).toEqual(['make docs', 'pytest -q']))

    await fireEvent.click(view.getByRole('tab', { name: '芝士' }))
    await settle()
    await fireEvent.click(view.getByRole('tab', { name: '全部' }))
    await settle()

    expect(getTranscript.mock.calls).toHaveLength(3)
    expect(optsOf(2)).toEqual({ limit: SITE_PAGE_SIZE, author: null })
    expect(args(pane)).toEqual(['make docs', 'pytest -q'])
  })

  it('往上翻到顶才拉这个队友更早的一页，视口不动', async () => {
    const { view, pane, m } = await openSite()
    await waitFor(() => expect(args(pane)).toEqual(['make docs', 'pytest -q']))

    await fireEvent.click(view.getByRole('tab', { name: '芝士' }))
    await settle()
    expect(args(pane)).toEqual(['ls docs', 'pytest -q'])
    expect(getTranscript.mock.calls).toHaveLength(2)
    // 钉到底的那几帧收工了，下面才是「读的人自己上滚」这个动作。
    expect(m.scrollTop).toBe(m.scrollHeight)

    // 读的人上滚到顶：这一页之前还有更早的（has_more），该去要。
    const gate = deferred<{ data: Block[]; total: number; has_more: boolean }>()
    getTranscript.mockImplementationOnce((_tid: string, opts: Record<string, unknown> = {}) => {
      expect(opts).toEqual({ limit: SITE_PAGE_SIZE, before: 'a0', author: 'cheese-a1' })
      return gate.promise
    })
    m.scrollTop = 0
    pane.dispatchEvent(new Event('scroll'))
    await flush()

    expect(getTranscript.mock.calls, '上滑才拉更早的一页').toHaveLength(3)

    // 更早的一页插在视口**上面**：loadOlder 在请求回来之后、拼进 DOM 之前量 before
    //（见 useSiteTranscript），这一页的 1000 是那之后才长出来的。排在紧接着的微任务里，
    // 就落在 before 与拼进 DOM 那一下之间；滚动位置要跟着往下让这么多。
    gate.resolve({ data: OLDER_A, total: 1, has_more: false })
    queueMicrotask(() => {
      m.scrollHeight = 3000
    })
    await flush()

    expect(args(pane)).toEqual(['git log', 'ls docs', 'pytest -q'])
    expect(m.scrollTop, '插进去的一页不能把读的人顶走').toBe(1000)
  })

  it('更早的页拉完了就不再请求：别把同一个游标问第二遍', async () => {
    const { view, pane, m } = await openSite()
    await waitFor(() => expect(args(pane)).toEqual(['make docs', 'pytest -q']))
    await fireEvent.click(view.getByRole('tab', { name: '芝士' }))
    await settle()

    // 更早的那一页要回来之后，后端说没有更早的了。
    m.scrollTop = 0
    pane.dispatchEvent(new Event('scroll'))
    await flush()
    await flush()
    expect(args(pane)).toEqual(['git log', 'ls docs', 'pytest -q'])
    expect(getTranscript.mock.calls, '切换 + 一页更早的，就这两个请求').toHaveLength(3)

    // 再滚一次顶：没有更早的了，不该再问。
    pane.dispatchEvent(new Event('scroll'))
    await flush()
    expect(getTranscript.mock.calls).toHaveLength(3)
  })
})
