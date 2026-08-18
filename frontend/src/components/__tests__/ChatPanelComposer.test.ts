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

// 芝士在名单里带 `agent` 标记，和 @ 补全菜单看到的是同一份名单。
const members = [
  { user_handle: 'cheese', name: '芝士', role: 'member', agent: true },
  { user_handle: 'bobby', name: '波比', role: 'member' },
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
    expect(JSON.parse(sent[1].payload).content).toBe('<@cheese> 再看看')
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
