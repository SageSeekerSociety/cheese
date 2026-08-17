/** 聊天面板里人类消息的作者名和头像。
 *
 * 消息里存的 `author` 是登录身份的 handle —— 后端有意固定成这个防伪造，所以
 * 「显示成昵称」只能在展示层做。修之前消息头直接吐 handle，头像是 handle 首字母
 * 的哈希色块，于是同一条消息里正文的 <@wangchangxin> 是昵称、消息头却是
 * `wangchangxin`，同一个人两个名字。
 *
 * 这里挂真实的 ChatPanel、喂真实的名册，从 DOM 上读结果 —— 要钉的是「人打开
 * 房间，眼睛看到的是谁」。
 */
import type { ProjectMemberRow, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listBlocks = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    listBlocks: (...a: unknown[]) => listBlocks(...a),
    getProgress: vi.fn().mockResolvedValue({ items: [], updated_at: null }),
    chatWsUrl: () => 'ws://test/ws',
    attachmentRawUrl: () => '',
    answerOptions: vi.fn(),
    toggleReaction: vi.fn(),
  }
})

import ChatPanel from '../ChatPanel.vue'

let seq = 0
/** 每个用例一个新房间 id —— 时间线窗口有个模块级缓存，共用 id 会串味。 */
function freshRoom(): string {
  seq += 1
  return `identity-room-${seq}`
}

function room(id: string): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: null,
    title: '对话内显示名称',
    kind: 'topic',
    status: 'active',
    created_at: '2026-08-15T00:00:00Z',
  }
}

function message(roomId: string, author: string) {
  return {
    id: 'b1',
    topic_id: roomId,
    kind: 'message',
    author_type: 'human' as const,
    author,
    content: '这条谁发的？',
    created_at: '2026-08-15T09:00:00Z',
  }
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function mountPanel(id: string, author: string, members: ProjectMemberRow[]) {
  listBlocks.mockResolvedValue({ data: [message(id, author)], has_more: false })
  const vuetify = createVuetify({ components, directives })
  return render(ChatPanel, {
    props: { topic: room(id), topicList: [room(id)], members },
    global: { plugins: [vuetify] },
  })
}

beforeAll(() => {
  // Vuetify 的 layout/overlay 摸这个 API，happy-dom 没有。
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  // 面板打开会连 WebSocket；这里只看渲染，给个不做事的替身。
  ;(globalThis as unknown as { WebSocket: unknown }).WebSocket = class {
    static OPEN = 1
    readyState = 0
    close() {}
    send() {}
  }
})

beforeEach(() => {
  vi.clearAllMocks()
})

describe('消息头上的作者名', () => {
  it('名册里有这个 handle → 显示昵称，不显示 handle', async () => {
    const id = freshRoom()
    const { container } = mountPanel(id, 'wangchangxin', [
      { user_handle: 'wangchangxin', role: 'member', name: 'fulu' },
    ])
    await flush()

    const name = container.querySelector('.im-name')!
    expect(name.textContent?.trim()).toBe('fulu')
  })

  it('名册里没有这个 handle（退出项目的人、anonymous 兜底作者）→ 原样显示 handle', async () => {
    const id = freshRoom()
    const { container } = mountPanel(id, 'ghost', [{ user_handle: 'wangchangxin', role: 'member', name: 'fulu' }])
    await flush()

    expect(container.querySelector('.im-name')!.textContent?.trim()).toBe('ghost')
  })

  // mentionNames 那张表额外塞了 all/here 两个保留键（渲染成「所有人」「在线成员」），
  // 拿它当名册用的话，一个恰好叫 all 的用户会被显示成「所有人」。
  it('handle 恰好叫 all 时不会被 @所有人 的保留键顶掉', async () => {
    const id = freshRoom()
    const { container } = mountPanel(id, 'all', [{ user_handle: 'all', role: 'member', name: '奥尔' }])
    await flush()

    expect(container.querySelector('.im-name')!.textContent?.trim()).toBe('奥尔')
  })
})

describe('消息头上的头像', () => {
  it('名册带 avatar_id → 渲染真头像 <img>', async () => {
    const id = freshRoom()
    const { container } = mountPanel(id, 'wangchangxin', [
      { user_handle: 'wangchangxin', role: 'member', name: 'fulu', avatar_id: 4242 },
    ])
    await flush()

    const img = container.querySelector('img.im-avatar') as HTMLImageElement
    expect(img).not.toBeNull()
    expect(img.getAttribute('src')).toContain('/avatars/4242')
    expect(img.getAttribute('alt')).toBe('fulu')
  })

  it('没有 avatar_id → 彩色色块，块里的字来自昵称首字而不是 handle 首字母', async () => {
    const id = freshRoom()
    const { container } = mountPanel(id, 'wangchangxin', [
      { user_handle: 'wangchangxin', role: 'member', name: '福禄' },
    ])
    await flush()

    expect(container.querySelector('img.im-avatar')).toBeNull()
    const block = container.querySelector('div.im-avatar')!
    expect(block.textContent?.trim()).toBe('福')
    // 底色的种子仍是 handle —— 换成昵称会让所有人的颜色都变一遍。
    expect(block.getAttribute('style')).toContain('background-color')
  })

  it('名册里没这个人时不去取 /avatars/default，保留可辨识的色块', async () => {
    const id = freshRoom()
    const { container } = mountPanel(id, 'ghost', [])
    await flush()

    expect(container.querySelector('img.im-avatar')).toBeNull()
    expect(container.querySelector('div.im-avatar')!.textContent?.trim()).toBe('G')
  })

  it('真头像加载失败 → 优雅退回彩色首字母，不留破图', async () => {
    const id = freshRoom()
    const { container } = mountPanel(id, 'wangchangxin', [
      { user_handle: 'wangchangxin', role: 'member', name: '福禄', avatar_id: 4242 },
    ])
    await flush()

    const img = container.querySelector('img.im-avatar')!
    img.dispatchEvent(new Event('error'))
    await flush()

    expect(container.querySelector('img.im-avatar')).toBeNull()
    expect(container.querySelector('div.im-avatar')!.textContent?.trim()).toBe('福')
  })
})
