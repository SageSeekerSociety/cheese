/** 悬停条上那颗「复制链接」。
 *
 * 一条消息要能在站内被指出来（贴给别人、自己留个书签），地址沿用搜索那一页的
 * 形状：`?block=<id>`，活卡里的对话再带上 `tab`/`card`。地址由 `useMessageLink`
 * 拼（组件不能 import vue-router），所以这里装真的路由来喂它。
 *
 * 还有一条同样重要：宿主**没有**路由时（演示页、一整类单测），不给链接就不画这
 * 一颗——画一颗点了没反应的按钮，比少画一颗更糟。
 */
import type { Block } from '../../cx_types'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import RoomHoverBar from './RoomHoverBar.vue'

import { setLocale } from '@/i18n'

const writeText = vi.fn().mockResolvedValue(undefined)

function block(id: string, conversation = 'tp1'): Block {
  return {
    id,
    conversation_id: conversation,
    kind: 'message',
    author_type: 'participant',
    author: '张衡',
    content: '这一条',
    created_at: '2026-09-20T00:00:00Z',
  }
}

async function routerStoppedOnTopic() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ name: 'workspace-topic', path: '/p/:projectId/t/:topicId', component: { template: '<div />' } }],
  })
  await router.push('/p/pr1/t/tp1')
  await router.isReady()
  return router
}

/** 停在频道 tp1 的支线 th1 上：桌面上频道主线在左，支线在右，两边的消息都有悬停条。 */
async function routerStoppedOnThread() {
  const page = { template: '<div />' }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { name: 'workspace-topic', path: '/p/:projectId/t/:topicId', component: page },
      { name: 'workspace-thread', path: '/p/:projectId/t/:topicId/threads/:threadId', component: page },
    ],
  })
  await router.push('/p/pr1/t/tp1/threads/th1')
  await router.isReady()
  return router
}

async function copiedFrom(b: Block, router: ReturnType<typeof createRouter>) {
  const { container } = renderBar(b, router)
  await fireEvent.click(linkButton(container)!)
  await flush()
  const url = new URL(writeText.mock.calls.at(-1)![0] as string)
  return router.resolve(url.pathname + url.search)
}

function renderBar(b: Block, router?: ReturnType<typeof createRouter>, extra: Record<string, unknown> = {}) {
  const vuetify = createVuetify({ components, directives })
  const plugins = router ? [vuetify, router] : [vuetify]
  return render(RoomHoverBar, {
    props: {
      block: b,
      shown: true,
      top: 0,
      jump: false,
      isAgent: false,
      pickerOpen: false,
      editable: false,
      ...extra,
    },
    global: { plugins },
  })
}

function linkButton(container: Element): HTMLButtonElement | null {
  return container.querySelector<HTMLButtonElement>('button[title="复制链接"], button[title="已复制链接"]')
}

async function flush() {
  for (let i = 0; i < 4; i += 1) await new Promise((r) => setTimeout(r, 0))
}

beforeEach(() => {
  // These assertions read the Chinese copy.
  setLocale('zh-CN')
  writeText.mockClear()
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
})
afterEach(() => vi.restoreAllMocks())

describe('消息的悬停条：复制链接', () => {
  it('有路由时给得出这一条的链接，点了写进剪贴板', async () => {
    const router = await routerStoppedOnTopic()
    const { container } = renderBar(block('b1'), router)

    const button = linkButton(container)
    expect(button, '有路由，却画不出复制链接那一颗').toBeTruthy()

    await fireEvent.click(button!)
    await flush()

    expect(writeText).toHaveBeenCalledTimes(1)
    const url = new URL(writeText.mock.calls[0][0] as string)
    expect(url.origin).toBe(window.location.origin)
    expect(url.pathname).toBe('/p/pr1/t/tp1')
    expect(url.searchParams.get('block')).toBe('b1')
  })

  it('复制完原地说一声「已复制链接」', async () => {
    const router = await routerStoppedOnTopic()
    const { container } = renderBar(block('b1'), router)

    await fireEvent.click(linkButton(container)!)
    await flush()

    expect(container.querySelector('button[title="已复制链接"]')).toBeTruthy()
  })

  it('支线里一条回复的链接打开那条支线，落在这条回复上', async () => {
    const router = await routerStoppedOnThread()

    const opened = await copiedFrom(block('r1', 'th1'), router)

    expect(opened.name).toBe('workspace-thread')
    expect(opened.params).toMatchObject({ topicId: 'tp1', threadId: 'th1' })
    expect(opened.query.block).toBe('r1')
  })

  it('支线页上，频道主线里那一条的链接仍是频道的', async () => {
    const router = await routerStoppedOnThread()

    const opened = await copiedFrom(block('m1', 'tp1'), router)

    expect(opened.name).toBe('workspace-topic')
    expect(opened.query.block).toBe('m1')
  })

  it('宿主没有路由就不画这一颗，而不是画一颗点了没反应的', async () => {
    const { container } = renderBar(block('b1'))
    expect(linkButton(container)).toBeNull()
    // 其余的按钮照旧在（复制的正文那颗还在）。
    expect(container.querySelector('button[title="复制"]')).toBeTruthy()
  })
})

// 右键一条消息：时间线把鼠标位置交给悬停条，悬停条在那一点打开这一条的 ⋯。
describe('右键一条消息', () => {
  it('在鼠标那一点打开这一条的操作', async () => {
    vi.stubGlobal('visualViewport', {
      width: 1280,
      height: 800,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
    })
    vi.stubGlobal('devicePixelRatio', 1)
    // 菜单定位时 Vuetify 要问指针下是谁，happy-dom 没有这个 API。
    const elementFromPoint = document.elementFromPoint
    document.elementFromPoint = () => null
    ;(window as unknown as { innerWidth: number }).innerWidth = 1280
    const { rerender, unmount } = renderBar(block('m1'))
    await rerender({ menuAt: { x: 30, y: 60 } })
    await flush()
    const items = Array.from(document.querySelectorAll('.v-overlay .v-list-item-title')).map((el) =>
      el.textContent?.trim()
    )
    expect(items).toContain('引用回复')
    expect(items).toContain('复制')
    unmount()
    await flush()
    document.elementFromPoint = elementFromPoint
    vi.unstubAllGlobals()
  })
})

describe('在支线中回复', () => {
  it('频道主线上的消息有这一颗，点它就是要这一条的支线', async () => {
    const { container, emitted } = renderBar(block('b1'), undefined, { threadable: true })
    const button = container.querySelector<HTMLButtonElement>('[data-testid="reply-in-thread"]')
    expect(button).not.toBeNull()
    await fireEvent.click(button!)
    expect((emitted().thread as Block[][])[0][0].id).toBe('b1')
  })

  it('私聊、任务和支线里的消息没有支线，不给这一颗', () => {
    const { container } = renderBar(block('b1'))
    expect(container.querySelector('[data-testid="reply-in-thread"]')).toBeNull()
  })
})

// 置顶在消息上原地做：只有能在频道主线上说话的人（加入了频道）才有这一项；已经置顶的
// 那一条，这一项换成「取消置顶」。
describe('置顶到频道', () => {
  async function menuItems(extra: Record<string, unknown>) {
    vi.stubGlobal('visualViewport', {
      width: 1280,
      height: 800,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
    })
    vi.stubGlobal('devicePixelRatio', 1)
    const elementFromPoint = document.elementFromPoint
    document.elementFromPoint = () => null
    ;(window as unknown as { innerWidth: number }).innerWidth = 1280
    const { rerender, unmount } = renderBar(block('m1'), undefined, extra)
    await rerender({ menuAt: { x: 30, y: 60 } })
    await flush()
    const items = Array.from(document.querySelectorAll('.v-overlay .v-list-item-title')).map((el) =>
      el.textContent?.trim()
    )
    unmount()
    await flush()
    document.elementFromPoint = elementFromPoint
    vi.unstubAllGlobals()
    return items
  }

  it('没加入频道的人看不到这一项', async () => {
    const items = await menuItems({ pinnable: false })
    expect(items).not.toContain('置顶到频道')
    expect(items).not.toContain('取消置顶')
  })

  it('能在主线说话的人能置顶；已经置顶的那一条是取消置顶', async () => {
    expect(await menuItems({ pinnable: true })).toContain('置顶到频道')
    const pinned = await menuItems({ pinnable: true, pinnedIds: new Set(['m1']) })
    expect(pinned).toContain('取消置顶')
    expect(pinned).not.toContain('置顶到频道')
  })
})

describe('支线里 AI 队友的回复：查看它那一轮的过程', () => {
  const reply = { ...block('r1'), author: 'cheese', turn_id: 'turn-7' }
  const processButton = (container: Element) =>
    container.querySelector<HTMLButtonElement>('[data-testid="message-process"]')

  it('点一下，交出的是那一轮', async () => {
    const view = renderBar(reply, undefined, { isAgent: true, processable: true })
    await fireEvent.click(processButton(view.container)!)
    expect(view.emitted('process')).toEqual([['turn-7']])
  })

  it('人说的话没有过程可看', () => {
    const view = renderBar({ ...block('m1'), turn_id: 'turn-7' }, undefined, { processable: true })
    expect(processButton(view.container)).toBeNull()
  })

  it('支线以外不给（主线和任务里，现场就在旁边那一格）', () => {
    const view = renderBar(reply, undefined, { isAgent: true })
    expect(processButton(view.container)).toBeNull()
  })
})
