/**
 * 项目首页那一块「等你决定」。
 *
 * 它从退役的「总览」搬过来，而那一页上它只能摆着看：选项存在 payload 里从来没
 * 画出来过，答复它的接口一个调用方都没有。所以这一组钉的不是「搬过来了」，是
 * 「搬过来之后答得了」——点一个选项，这一条就不再等人。
 *
 * 另一半钉的是它摆成一叠之后的行为：屏幕上同时只有一个问题、一次只答得了那一
 * 个，而「一共几条、这是第几条」写在标题那一行。这一页钉在视口上，板按剩下的高
 * 度分列，所以「几条问题占多高」这件事必须和条数无关。
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
    id: 1,
    project_id: 'p1',
    topic_id: null,
    level: 'strong',
    kind: 'decision_request',
    target_handle: 'alice',
    title: '先做哪一个',
    body: '两条路都通，但只够做一条',
    payload: { options: ['先做导出', '先做搜索'] },
    read: false,
    resolved_at: null,
    feedback: null,
    created_at: '2026-09-20T10:00:00Z',
    ...overrides,
  }
}

/** 三条在等你，各问各的。 */
function three() {
  return [
    item(),
    item({ id: 2, title: '要不要先冻结接口', body: '冻了就改不动了', payload: { options: ['冻结', '再等等'] } }),
    item({ id: 3, title: '这一版要不要发', body: '快检是绿的', payload: { options: ['发', '不发'] } }),
  ]
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(myHandle).mockReturnValue('alice')
  vi.mocked(getInbox).mockResolvedValue({ data: [item()], total: 1 })
  vi.mocked(resolveAlert).mockResolvedValue(item({ resolved_at: '2026-09-20T11:00:00Z' }))
  vi.mocked(markRead).mockResolvedValue(item({ read: true }))
  vi.mocked(sendFeedback).mockResolvedValue(item({ feedback: 'up' }))
})

afterEach(cleanup)

function mount() {
  return render(NeedsYou, { props: { projectId: 'p1' }, global: { plugins: [vuetify] } })
}

function buttons(container: Element, label: string): HTMLElement[] {
  return Array.from(container.querySelectorAll('button')).filter((b) => b.textContent?.trim() === label)
}

function button(container: Element, label: string): HTMLElement | undefined {
  return buttons(container, label)[0]
}

describe('等你决定', () => {
  it('问的是什么、给了哪几个选项，都摆在卡上', async () => {
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

    await waitFor(() => expect(resolveAlert).toHaveBeenCalledWith(1, '先做搜索'))
    // 答过之后收件箱里没有它了，整块跟着消失——空的时候首页不该多一个写着「暂无」的框。
    await waitFor(() => expect(container.querySelector('.asked')).toBeNull())
  })

  it('没给选项的那一条答不了，只能收起来', async () => {
    vi.mocked(getInbox).mockResolvedValue({ data: [item({ kind: 'accept_request', payload: {} })], total: 1 })
    const { container } = mount()

    await waitFor(() => expect(container.textContent).toContain('先做哪一个'))
    await fireEvent.click(button(container, '知道了')!)

    await waitFor(() => expect(markRead).toHaveBeenCalledWith(1))
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

describe('等你决定：一叠而不是一列', () => {
  it('屏幕上只有一个问题，答得了的也只有它', async () => {
    vi.mocked(getInbox).mockResolvedValue({ data: three(), total: 3 })
    const { container } = mount()

    await waitFor(() => expect(container.textContent).toContain('先做哪一个'))
    const text = container.textContent ?? ''
    // 后面那两条在叠里，只露一道边：读不到它们问了什么，也点不到它们的选项——不然
    // 同一屏上摆着三个问题的六个按钮，人得先分清哪个按钮属于哪一条。
    expect(text).not.toContain('要不要先冻结接口')
    expect(text).not.toContain('这一版要不要发')
    expect(buttons(container, '冻结')).toHaveLength(0)
    expect(buttons(container, '先做导出')).toHaveLength(1)
  })

  it('说得出一共几条、这是第几条', async () => {
    vi.mocked(getInbox).mockResolvedValue({ data: three(), total: 3 })
    const { container } = mount()

    await waitFor(() => expect(container.textContent?.replace(/\s+/g, '')).toContain('1/3'))
  })

  it('只有一条的时候不写第几条，也不摆「下一条」', async () => {
    const { container } = mount()

    await waitFor(() => expect(container.textContent).toContain('先做哪一个'))
    expect(container.textContent?.replace(/\s+/g, '')).not.toContain('1/1')
    expect(button(container, '下一条')).toBeUndefined()
  })

  it('这一条现在答不了就翻到下一条，翻到底绕回第一条', async () => {
    vi.mocked(getInbox).mockResolvedValue({ data: three(), total: 3 })
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('先做哪一个'))

    await fireEvent.click(button(container, '下一条')!)

    await waitFor(() => expect(container.textContent).toContain('要不要先冻结接口'))
    expect(container.textContent?.replace(/\s+/g, '')).toContain('2/3')
    expect(buttons(container, '冻结')).toHaveLength(1)

    await fireEvent.click(button(container, '下一条')!)
    await fireEvent.click(button(container, '下一条')!)

    // 翻到最后一条还剩一个按不动的按钮，人会以为是坏的。
    await waitFor(() => expect(container.textContent).toContain('先做哪一个'))
    expect(container.textContent?.replace(/\s+/g, '')).toContain('1/3')
  })

  it('答掉最上面那一条，紧接着那一条自己顶上来', async () => {
    vi.mocked(getInbox).mockResolvedValue({ data: three(), total: 3 })
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('先做哪一个'))
    vi.mocked(getInbox).mockResolvedValue({ data: three().slice(1), total: 2 })

    await fireEvent.click(button(container, '先做导出')!)

    await waitFor(() => expect(resolveAlert).toHaveBeenCalledWith(1, '先做导出'))
    await waitFor(() => expect(container.textContent).toContain('要不要先冻结接口'))
    // 少了一条，计数跟着变，而摆出来的仍然是这一叠的第一张。
    expect(container.textContent?.replace(/\s+/g, '')).toContain('1/2')
  })

  it('翻到第三条又答掉它，队伍变短之后不会停在一张空卡上', async () => {
    vi.mocked(getInbox).mockResolvedValue({ data: three(), total: 3 })
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('先做哪一个'))

    await fireEvent.click(button(container, '下一条')!)
    await fireEvent.click(button(container, '下一条')!)
    await waitFor(() => expect(container.textContent).toContain('这一版要不要发'))

    vi.mocked(getInbox).mockResolvedValue({ data: three().slice(0, 2), total: 2 })
    await fireEvent.click(button(container, '发')!)

    await waitFor(() => expect(resolveAlert).toHaveBeenCalledWith(3, '发'))
    // 原来停在第 3 条，现在只剩 2 条：回到第一条，而不是指着一个不存在的下标。
    await waitFor(() => expect(container.textContent).toContain('先做哪一个'))
    expect(container.textContent?.replace(/\s+/g, '')).toContain('1/2')
  })
})
