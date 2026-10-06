// 现场往上翻是**无限**的：每拉一页更早的，那 120 条就接在窗口顶上，不封顶的话，手
// 上的记录迟早涨回「一次加载全部」那份体积——和对话栏被卡死时一模一样。
//
// 这里的规矩跟对话栏的窗口同一个（见 lib/blockPaging.ts 的 capWindow / MAX_WINDOW）：
// 过上限时裁掉**最新**的那一截。读的人正往上翻，那一截在视口下方，裁它不动画面；而
// 且他是往上翻的，能继续翻的前提恰恰是最旧的那一头不动——从那一头裁，就等于把他刚
// 拉上来的页当场删掉。
//
// happy-dom 不排版：scrollHeight / clientHeight / scrollTop 恒为 0，所以这里按用例的
// 意思把它们钉住。滚动位置是面板自己写上去的那个数，读它就能验「翻页之后视口不动」。
import type { Component } from 'vue'
import type { Block, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
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
import { MAX_WINDOW } from '../../lib/blockPaging'
import PanelSite from '../work/PanelSiteHost.vue'

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

function step(id: string, turn: string, at: string, arg: string): Block {
  return {
    id,
    project_id: 'p1',
    conversation_id: 't1',
    kind: 'event',
    author_type: 'participant',
    author: 'cheese-a1',
    content: arg,
    reply_to: null,
    refs: [],
    turn_id: turn,
    meta: { tool: 'Bash', arg },
    created_at: at,
  } as unknown as Block
}

/** 每一页 120 条（SITE_PAGE_SIZE），第 g 页代表越早的一页（g 越大越早）。 */
function pageOf(g: number): Block[] {
  const base = Date.UTC(2026, 0, 1)
  const blocks: Block[] = []
  for (let i = 0; i < SITE_PAGE_SIZE; i += 1) {
    const at = new Date(base + (900_000 - g * 10_000 + i * 10)).toISOString()
    blocks.push(step(`b${g}-${i}`, `turn-${g}`, at, `arg ${g}-${i}`))
  }
  return blocks
}

let call = 0

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  setLocale('zh-CN')
  call = 0
  getTranscript.mockReset()
  getTranscript.mockImplementation(async () => {
    const g = call
    call += 1
    return { data: pageOf(g), total: SITE_PAGE_SIZE, has_more: true, turn_starts: {} }
  })
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

async function openSite() {
  const view = render(Site, {
    props: { topicId: topic.id, active: true, memberNames: {} },
    global: { plugins: [vuetify] },
  })
  const pane = view.container.querySelector<HTMLElement>('.panel-site')
  expect(pane, '找不到现场的滚动容器').toBeTruthy()
  const m = fakeMetrics(pane!, { scrollHeight: 2000, clientHeight: 500, scrollTop: 0 })
  await settle()
  return { view, pane: pane!, m }
}

/** 上滚到顶，拉一页更早的，并让内容长高一页。 */
async function pageOlder(pane: HTMLElement, m: Metrics): Promise<void> {
  const before = m.scrollHeight
  m.scrollTop = 0
  pane.dispatchEvent(new Event('scroll'))
  // loadOlder 在请求回来之后、把这一页拼进 DOM 之前量 before（见 useSiteTranscript），
  // 高度是**那一刻之后**才长出来的。排在紧接着的微任务里，好落在 before 与拼进 DOM
  // 那一下之间，跟真实浏览器「DOM 一更新就长高」一致。
  queueMicrotask(() => {
    m.scrollHeight = before + 1200
  })
  await flush()
  await flush()
}

describe('现场窗口封顶', () => {
  it('翻过 MAX_WINDOW：窗口封在 600，最旧的留着、最新的被裁掉', async () => {
    const { pane, m } = await openSite()
    await waitFor(() => expect(args(pane)).toHaveLength(SITE_PAGE_SIZE))

    // 再翻 5 页：120 * 6 = 720 > MAX_WINDOW(600)，第 6 页落进来时封顶。
    for (let p = 0; p < 5; p += 1) await pageOlder(pane, m)

    expect(args(pane)).toHaveLength(MAX_WINDOW)
    // 最旧那一页还在：从那一头裁等于把刚拉上来的删掉。
    expect(args(pane)[0]).toBe('arg 5-0')
    // 最新那一页（开屏读的）被裁在窗口下方——它下面还有更新的、接得上，见下一条。
    expect(args(pane)).not.toContain('arg 0-0')

    // 再翻一页也不会越封越高：还是 600，最旧的那一头往前走了一页。
    await pageOlder(pane, m)
    expect(args(pane)).toHaveLength(MAX_WINDOW)
    expect(args(pane)[0]).toBe('arg 6-0')
  })

  it('没到上限时不裁：翻几页就长几页', async () => {
    const { pane, m } = await openSite()
    await waitFor(() => expect(args(pane)).toHaveLength(SITE_PAGE_SIZE))

    for (let p = 0; p < 3; p += 1) await pageOlder(pane, m)
    expect(args(pane)).toHaveLength(SITE_PAGE_SIZE * 4)
  })

  it('翻一页之后视口不动：补的正是插进来那一段的高度', async () => {
    const { pane, m } = await openSite()
    await waitFor(() => expect(args(pane)).toHaveLength(SITE_PAGE_SIZE))

    const before = m.scrollHeight
    m.scrollTop = 0
    pane.dispatchEvent(new Event('scroll'))
    // 同上：loadOlder 量 before 在请求回来之后，这一页的高度要在这之后才长出来。
    queueMicrotask(() => {
      m.scrollHeight = before + 1200
    })
    await flush()

    expect(m.scrollTop, '插进去的一页不能把读的人顶走').toBe(1200)
  })
})
