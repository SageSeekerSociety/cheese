// 输入框里的东西属于它被打出来的那个话题。
//
// 切话题时输入框的状态整个留在原地，其中 replyTarget 指向的是**上一个话题**的
// 块——屏幕上看不出异常（本话题找不到父块就不画引用条），落库的 reply_to 却已经
// 跨话题了，而会话树是记忆和摘要重建的依据之一。
import type { Component } from 'vue'
import type { Block, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

import { loadComposerDraft, saveComposerDraft } from '@/lib/composerDrafts'

const Panel = ChatPanel as unknown as Component

function topicOf(id: string): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: null,
    title: id,
    kind: 'topic',
    status: 'active',
    created_by: 'u',
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
  } as Topic
}

function msg(id: string, author: string, at: Date, content = id): Block {
  return {
    id,
    project_id: 'p1',
    topic_id: 't1',
    kind: 'message',
    author_type: author === 'cheese' ? 'ai' : 'human',
    author,
    content,
    reply_to: null,
    refs: [],
    created_at: at.toISOString(),
  } as unknown as Block
}

const daysAgo = (n: number, h = 9) => {
  const d = new Date()
  d.setDate(d.getDate() - n)
  d.setHours(h, 30, 0, 0)
  return d
}

let vuetify: ReturnType<typeof createVuetify>
let history: Block[] = []

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  history = []
  vi.stubGlobal(
    'WebSocket',
    class {
      close() {}
      send() {}
      addEventListener() {}
      removeEventListener() {}
    }
  )
  vi.stubGlobal('fetch', async (url: string) => ({
    ok: true,
    status: 200,
    json: async () =>
      String(url).includes('/progress')
        ? { code: 200, data: { items: [], updated_at: null } }
        : // 房间的支线：面板打开时会顺手拉一次，用来画「已派出」标记。不区分的话
          // 这个替身会把消息当成支线，时间线上多出一串标题为空的标记。
          String(url).includes('/tasks')
          ? { code: 200, data: { data: [], total: 0 } }
          : { code: 200, data: { data: history, total: history.length, has_more: false } },
  }))
})

const settle = () => new Promise((r) => setTimeout(r, 0))

describe('输入框的内容属于它被打出来的那个话题', () => {
  it('切走再回来，草稿还在；切到别的话题，输入框是空的', async () => {
    const { container, rerender } = render(Panel, {
      props: { topic: topicOf('draft-a'), showComposer: true },
      global: { plugins: [vuetify] },
    })
    await settle()

    const textarea = container.querySelector('textarea') as HTMLTextAreaElement
    await fireEvent.update(textarea, '这句话是说给 t1 的')

    await rerender({ topic: topicOf('draft-b'), showComposer: true })
    await settle()
    expect((container.querySelector('textarea') as HTMLTextAreaElement).value).toBe('')

    await rerender({ topic: topicOf('draft-a'), showComposer: true })
    await settle()
    expect((container.querySelector('textarea') as HTMLTextAreaElement).value).toBe('这句话是说给 t1 的')
  })

  it('回复目标不跟着你换话题——它指的是上一个话题里的块', async () => {
    history = [msg('m1', 'other', daysAgo(0), '这条被回复')]
    const { container, rerender } = render(Panel, {
      props: { topic: topicOf('t1'), showComposer: true },
      global: { plugins: [vuetify] },
    })
    await settle()

    const replyBtn = Array.from(container.querySelectorAll('.im-act')).find(
      (b) => b.getAttribute('title') === '回复'
    ) as HTMLButtonElement
    await fireEvent.click(replyBtn)
    expect(container.querySelector('.reply-bar')).toBeTruthy()

    await rerender({ topic: topicOf('t2'), showComposer: true })
    await settle()
    // 留着的话，下一条发到 t2 的消息会带上 t1 的 reply_to。
    expect(container.querySelector('.reply-bar')).toBeNull()
  })
})

// 刷新（尤其是 service worker 更新引发的那一次自动刷新）不会走「离开这个话题」，
// 所以草稿不能只存在内存里——那是 lib/composerDrafts.ts 存在的理由，这里钉的是
// ChatPanel 那一头的接线：写下去、接回来、以及故意不写下去的那一样。
describe('刷新之后草稿还在', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('内存里那份没了（刚刷新过）就从落盘的那份接回来', async () => {
    // 直接往 localStorage 里放，等价于「上一次页面退出时存下的」。
    saveComposerDraft('from-disk', { draft: '上次没发出去的话', reply: null, atts: [] })

    const { container } = render(Panel, {
      props: { topic: topicOf('from-disk'), showComposer: true },
      global: { plugins: [vuetify] },
    })
    await settle()

    expect((container.querySelector('textarea') as HTMLTextAreaElement).value).toBe('上次没发出去的话')
  })

  it('边打边存：不必等切话题/卸载，输入本身就落盘', async () => {
    const { container } = render(Panel, {
      props: { topic: topicOf('live-save'), showComposer: true },
      global: { plugins: [vuetify] },
    })
    await settle()

    await fireEvent.update(container.querySelector('textarea') as HTMLTextAreaElement, '打了一半')
    await new Promise((r) => setTimeout(r, 900))

    expect(loadComposerDraft('live-save')?.draft).toBe('打了一半')
  })

  it('没送出去的消息**不**落盘——它会在下次打开时被自动发出去', async () => {
    const { container } = render(Panel, {
      props: { topic: topicOf('out-persist'), showComposer: true },
      global: { plugins: [vuetify] },
    })
    await settle()

    const textarea = container.querySelector('textarea') as HTMLTextAreaElement
    textarea.focus()
    await fireEvent.update(textarea, '这条还没送到')
    await fireEvent.keyDown(textarea, { key: 'Enter' })
    await new Promise((r) => setTimeout(r, 900))

    // 屏幕上那条还在（发件箱），但磁盘上没有它。
    expect(container.querySelector('.im-row--pending')?.textContent).toContain('这条还没送到')
    expect(loadComposerDraft('out-persist')).toBeNull()
  })

  it('发出去了就把落盘的那份也删掉', async () => {
    const { container } = render(Panel, {
      props: { topic: topicOf('sent-away'), showComposer: true },
      global: { plugins: [vuetify] },
    })
    await settle()

    const textarea = container.querySelector('textarea') as HTMLTextAreaElement
    await fireEvent.update(textarea, '先打一句')
    await new Promise((r) => setTimeout(r, 900))
    expect(loadComposerDraft('sent-away')?.draft).toBe('先打一句')

    textarea.focus()
    await fireEvent.keyDown(textarea, { key: 'Enter' })
    await new Promise((r) => setTimeout(r, 900))

    expect(loadComposerDraft('sent-away')).toBeNull()
  })
})

describe('发出去的消息立刻显示，没送到能重试', () => {
  it('socket 没开也能打字、也能发——消息进队列，屏幕上立刻有', async () => {
    const { container } = render(Panel, {
      props: { topic: topicOf('t1'), showComposer: true },
      global: { plugins: [vuetify] },
    })
    await settle()

    const textarea = container.querySelector('textarea') as HTMLTextAreaElement
    // 断线时输入框曾经是 disabled 的：一天二十几次部署，等于每天有相当多的
    // 时间这个框是死的。
    expect(textarea.disabled).toBe(false)

    textarea.focus()
    await fireEvent.update(textarea, '断线时说的话')
    await fireEvent.keyDown(textarea, { key: 'Enter' })
    await settle()

    const pending = container.querySelector('.im-row--pending')
    expect(pending).toBeTruthy()
    expect(pending!.textContent).toContain('断线时说的话')
    // 输入框清空了：这条已经交给发件箱了，不该还留在草稿里。
    expect((container.querySelector('textarea') as HTMLTextAreaElement).value).toBe('')
  })

  it('待发的消息属于它被打出来的那个话题，不跟着你换房间', async () => {
    const { container, rerender } = render(Panel, {
      props: { topic: topicOf('out-a'), showComposer: true },
      global: { plugins: [vuetify] },
    })
    await settle()
    const textarea = container.querySelector('textarea') as HTMLTextAreaElement
    textarea.focus()
    await fireEvent.update(textarea, '这条是发给 t1 的')
    await fireEvent.keyDown(textarea, { key: 'Enter' })
    await settle()
    expect(container.querySelector('.im-row--pending')).toBeTruthy()

    await rerender({ topic: topicOf('out-b'), showComposer: true })
    await settle()
    expect(container.querySelector('.im-row--pending')).toBeNull()

    await rerender({ topic: topicOf('out-a'), showComposer: true })
    await settle()
    expect(container.querySelector('.im-row--pending')?.textContent).toContain('这条是发给 t1 的')
  })
})
