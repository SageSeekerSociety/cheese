/**
 * 项目首页那一块「等你决定」。
 *
 * 它从退役的「总览」搬过来，而那一页上它只能摆着看：选项存在 payload 里从来没
 * 画出来过，答复它的接口一个调用方都没有。所以这一组钉的不是「搬过来了」，是
 * 「搬过来之后答得了」——点一个选项，这一条就不再等人。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import NeedsYou from './NeedsYou.vue'

vi.mock('@/api', () => ({
  getInbox: vi.fn(),
  markRead: vi.fn(),
  resolveAlert: vi.fn(),
  sendFeedback: vi.fn(),
}))
vi.mock('@/me', () => ({ myHandle: vi.fn(() => 'alice') }))

const { getInbox, markRead, resolveAlert, sendFeedback } = await import('@/api')
const { myHandle } = await import('@/me')

const vuetify = createVuetify({ components, directives })

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!('devicePixelRatio' in globalThis)) {
    Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })
  }
})

function item(overrides: Record<string, unknown> = {}) {
  return {
    id: 'n1',
    project_id: 'p1',
    topic_id: null,
    level: 'strong',
    kind: 'decision_request',
    target_handle: 'alice',
    title: '先做哪一个',
    body: '两条路都通，但只够做一条',
    payload: { options: ['先做导出', '先做搜索'] },
    read_at: null,
    resolved_at: null,
    feedback: null,
    created_at: '2026-09-20T10:00:00Z',
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(myHandle).mockReturnValue('alice')
  vi.mocked(getInbox).mockResolvedValue({ data: [item()], total: 1 })
  vi.mocked(resolveAlert).mockResolvedValue(item({ resolved_at: '2026-09-20T11:00:00Z' }))
  vi.mocked(markRead).mockResolvedValue(item({ read_at: '2026-09-20T11:00:00Z' }))
  vi.mocked(sendFeedback).mockResolvedValue(item({ feedback: 'up' }))
})

afterEach(cleanup)

function mount() {
  return render(NeedsYou, { props: { projectId: 'p1' }, global: { plugins: [vuetify] } })
}

function button(container: Element, label: string): HTMLElement | undefined {
  return Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.trim() === label)
}

describe('等你决定', () => {
  it('问的是什么、给了哪几个选项，都摆在行上', async () => {
    const { container } = mount()

    await waitFor(() => expect(container.textContent).toContain('先做哪一个'))
    const text = container.textContent ?? ''
    expect(text).toContain('等你决定')
    expect(text).toContain('两条路都通，但只够做一条')
    expect(button(container, '先做导出')).toBeTruthy()
    expect(button(container, '先做搜索')).toBeTruthy()
    expect(getInbox).toHaveBeenCalledWith('p1', 'alice')
  })

  it('点一个选项就是拍板，这一条不再等人', async () => {
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('先做哪一个'))
    vi.mocked(getInbox).mockResolvedValue({ data: [], total: 0 })

    await fireEvent.click(button(container, '先做搜索')!)

    await waitFor(() => expect(resolveAlert).toHaveBeenCalledWith('n1', '先做搜索'))
    // 答过之后收件箱里没有它了，整块跟着消失——空的时候首页不该多一个写着「暂无」的框。
    await waitFor(() => expect(container.querySelector('.asked')).toBeNull())
  })

  it('没给选项的那一条答不了，只能收起来', async () => {
    vi.mocked(getInbox).mockResolvedValue({ data: [item({ kind: 'accept_request', payload: {} })], total: 1 })
    const { container } = mount()

    await waitFor(() => expect(container.textContent).toContain('先做哪一个'))
    await fireEvent.click(button(container, '知道了')!)

    await waitFor(() => expect(markRead).toHaveBeenCalledWith('n1'))
    expect(resolveAlert).not.toHaveBeenCalled()
  })

  it('没人在等你的时候整块不出现', async () => {
    vi.mocked(getInbox).mockResolvedValue({ data: [], total: 0 })
    const { container } = mount()

    await waitFor(() => expect(getInbox).toHaveBeenCalled())
    expect(container.querySelector('.asked')).toBeNull()
  })

  it('读不到收件箱也只是这一块不出现，首页照常', async () => {
    vi.mocked(getInbox).mockRejectedValue(new Error('服务不可用'))
    const { container } = mount()

    await waitFor(() => expect(getInbox).toHaveBeenCalled())
    expect(container.querySelector('.asked')).toBeNull()
    expect(container.textContent).not.toContain('服务不可用')
  })

  it('没登录就不去问别人的收件箱', async () => {
    vi.mocked(myHandle).mockReturnValue('')
    const { container } = mount()

    await waitFor(() => expect(container.querySelector('.asked')).toBeNull())
    expect(getInbox).not.toHaveBeenCalled()
  })

  it('答复失败时说出原因，这一条还留着', async () => {
    vi.mocked(resolveAlert).mockRejectedValue(new Error('这条已经有人答过了'))
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('先做哪一个'))

    await fireEvent.click(button(container, '先做导出')!)

    const alert = await waitFor(() => {
      const found = container.querySelector('[role="alert"]')
      expect(found).toBeTruthy()
      return found!
    })
    expect(alert.textContent).toContain('这条已经有人答过了')
    expect(container.textContent).toContain('先做哪一个')
  })
})
