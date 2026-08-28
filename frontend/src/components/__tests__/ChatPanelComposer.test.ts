/** 输入栏归聊天栏 (规则 5)。
 *
 * 工作台以前在对话栏和工作面板底下横跨着自己的一份输入栏——和 ChatPanel 自己那份
 * 几乎逐行重复。横跨读起来像「对整个话题说话」，而它发出去的 99% 是只有左边这一
 * 栏显示的聊天消息；重复则意味着两份实现要靠人记着同步。
 *
 * 这里钉的是并完之后仍然成立的三件事：输入栏在对话栏里、话题自己的 chips 能从外面
 * 交进来、以及**这条消息 @ 没 @ 芝士**决定它会不会被叫起来。
 */
import type { Topic } from '../../cx_types'

import { h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    listBlocks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getProgress: vi.fn().mockResolvedValue({ items: [], updated_at: null }),
    // 芝士的座位在**话题**名册上，一个话题一个分身。项目名册上没有它——这正是
    // 「线上 @ 不出芝士」那次的成因，所以这里照真实形状摆：分身 handle 带话题
    // 后缀，而项目名册里只有人。
    listTopicMembers: vi.fn().mockResolvedValue({
      data: [
        { id: 'm1', member_handle: 'alice', name: 'Alice', role: 'owner', agent: false },
        { id: 'm2', member_handle: 'cheese-topica', name: '芝士', role: 'member', agent: true },
      ],
      total: 2,
    }),
    chatWsUrl: () => 'ws://test/ws',
    attachmentRawUrl: () => '',
  }
})

import ChatPanel from '../ChatPanel.vue'

const sent: { payload: string }[] = []

function topic(id = 'topic-A'): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: null,
    title: '做一件事',
    kind: 'task',
    status: 'active',
    created_at: '2026-08-10T00:00:00Z',
  }
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

// 项目名册：只有人。房间里那位芝士来自话题名册（见上面的 mock）。
const members = [
  { user_handle: 'alice', name: 'Alice', role: 'lead' },
  { user_handle: 'bobby', name: '波比', role: 'member' },
  // 老项目名册上可能还坐着一行共用的芝士。房间自己有分身的时候它不进 @ 名单
  // （两行都叫「芝士」，@芝士 展开成哪一个纯看顺序）。
  { user_handle: 'cheese', name: '共用芝士', role: 'member', agent: true },
]

// 每话题草稿是模块级的（跨挂载留着，这正是它的用途），所以每条用例用自己的
// 话题——共用一个的话，上一条留在发件箱里的消息会在下一次挂载时重发。
function mountPanel(slots: Record<string, () => unknown> = {}, topicId?: string) {
  const vuetify = createVuetify({ components, directives })
  return render(ChatPanel, {
    props: { topic: topic(topicId), showComposer: true, hideHeader: true, members },
    slots,
    global: { plugins: [vuetify] },
  })
}

function composerBox(container: Element): HTMLTextAreaElement | null {
  return container.querySelector<HTMLTextAreaElement>('.composer textarea')
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  // 一个会「连上」的假 socket，这样输入栏不是 disabled 状态，而且发出去的东西
  // 能被读到——这条用例问的正是「发出去的那条消息带没带 @芝士」。
  ;(globalThis as unknown as { WebSocket: unknown }).WebSocket = class {
    static OPEN = 1
    readyState = 1
    onopen: (() => void) | null = null
    constructor() {
      setTimeout(() => this.onopen?.(), 0)
    }
    close() {}
    send(payload: string) {
      sent.push({ payload })
    }
  }
})

beforeEach(() => {
  vi.clearAllMocks()
  sent.length = 0
})

describe('对话栏自己的输入栏', () => {
  it('输入栏就在对话栏里，不再横跨到工作面板底下', async () => {
    const { container } = mountPanel()
    await flush()

    const box = composerBox(container)
    expect(box, '对话栏里没有输入栏').toBeTruthy()
    expect(box!.closest('.chat'), '输入栏跑到对话栏外面去了').toBeTruthy()
  })

  it('话题自己的 chips 从外面交进来——输入栏不必认识算力池', async () => {
    const { container } = mountPanel({
      'composer-chips': () => h('span', { class: 'probe-chip' }, '已归档'),
    })
    await flush()

    const chip = container.querySelector('.probe-chip')
    expect(chip, 'composer-chips 插槽没渲染').toBeTruthy()
    expect(chip!.closest('.composer'), 'chips 没落在输入栏那一行里').toBeTruthy()
  })

  it('@ 了芝士的那条消息才召唤它', async () => {
    const { container } = mountPanel()
    await flush()

    const box = composerBox(container)!
    box.focus()
    await fireEvent.update(box, '看看这个')
    await fireEvent.keyDown(box, { key: 'Enter' })
    await flush()
    expect(sent.map((s) => JSON.parse(s.payload).summon)).toEqual([false])

    box.focus()
    await fireEvent.update(box, '@芝士 再看看')
    await fireEvent.keyDown(box, { key: 'Enter' })
    await flush()

    expect(sent.map((s) => JSON.parse(s.payload).summon)).toEqual([false, true])
    // 发出去的是规范形式，和 @ 一个人完全一样。
    expect(JSON.parse(sent[1].payload).content).toBe('<@cheese-topica> 再看看')
  })

  // 线上真实形状：项目名册里只有人，芝士只在话题名册上。名单少了它，@ 补全里
  // 就没有它，而 @ 它是叫它干活的唯一方式——整条路就断了。
  it('项目名册里没有芝士，@ 补全里照样有', async () => {
    const { container } = mountPanel({}, 'topic-C')
    await flush()

    const box = composerBox(container)!
    box.focus()
    await fireEvent.update(box, '@')
    await flush()

    const menu = container.querySelector('.mention-menu')
    expect(menu, '@ 补全没弹出来').toBeTruthy()
    expect(menu!.textContent).toContain('芝士')
    expect(menu!.textContent).toContain('AI 队友')
  })

  // 老话题的名册里可能没有芝士的座位（座位是后来才有的）。那种房间里，项目名册上
  // 那行共用的芝士得顶上，否则这个话题永远叫不动它。
  it('房间名册里没有芝士时，退回项目名册上那一行', async () => {
    const api = await import('../../api')
    vi.mocked(api.listTopicMembers).mockResolvedValueOnce({
      data: [{ id: 'm1', member_handle: 'alice', name: 'Alice', role: 'owner', agent: false }],
      total: 1,
    } as Awaited<ReturnType<typeof api.listTopicMembers>>)

    const { container } = mountPanel({}, 'topic-D')
    await flush()

    const box = composerBox(container)!
    box.focus()
    await fireEvent.update(box, '@')
    await flush()
    expect(container.querySelector('.mention-menu')!.textContent).toContain('共用芝士')
  })

  // 「打一个 @ 然后回车」是这个输入框里最短的一条路，而它当时通向 @all——把整个
  // 话题的所有人叫起来。最短的路得通向最常见的意图：交给芝士。
  it('@ 之后直接回车，选中的是芝士，不是群播', async () => {
    const { container } = mountPanel({}, 'topic-E')
    await flush()

    const box = composerBox(container)!
    box.focus()
    await fireEvent.update(box, '@')
    await flush()
    await fireEvent.keyDown(box, { key: 'Enter' })
    await flush()

    expect(box.value).toBe('@芝士 ')
    // 挑完人就是接着打字的时刻——焦点不该被那次回车带走。
    expect(document.activeElement).toBe(box)
  })

  it('@ 一个人不会把芝士叫起来', async () => {
    const { container } = mountPanel({}, 'topic-B')
    await flush()

    const box = composerBox(container)!
    box.focus()
    await fireEvent.update(box, '@波比 你看下')
    await fireEvent.keyDown(box, { key: 'Enter' })
    await flush()

    expect(sent.map((s) => JSON.parse(s.payload).summon)).toEqual([false])
  })
})
