// 芝士问的题是对话里的一条普通消息，下面带快捷回复。
//
// 点一个快捷回复，就是把那几个字作为对这道题的回复发出去 —— 和在输入框里打字是
// 同一条消息；输入框始终在，从不被题占用。
//
// 答过之后还能收回来：一道题只收一个回答，点错一下就没有别的路可走。撤回的是这道
// 题上的那条答复，不是他说过的那句话 —— 消息留着，题回到没人答。
//
// 被问的人也可以不答：✕ 跳过这道题，房间里不出现任何消息，题上写着他跳过了；撤销
// 之后题重新等他回答。
import type { Component } from 'vue'
import type { Block, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, within } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale } from '@/i18n'

// 确认框（Vuetify 的 v-dialog）算位置时要问 `window.visualViewport`，happy-dom 没有
// 这一件。补一个够用的空壳 —— 浏览器里本来就有它。
vi.stubGlobal('visualViewport', new EventTarget())

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

/** 一道题。`by` 是谁答的，null 是还没人答 —— 撤回的入口只长在答过的那一位旁边。
 *  `asked` 是它在等谁；`skipped` 为真时 `by` 那一条是一次跳过而不是答案。 */
function question(topic: string, by: string | null = null, asked = 'me', skipped = false): Block {
  return {
    id: `q-${topic}`,
    project_id: 'p1',
    topic_id: topic,
    kind: 'message',
    author_type: 'participant',
    author: 'agent',
    content: '预算按哪个口径统计？',
    reply_to: null,
    refs: [],
    created_at: '2026-08-01T00:00:00Z',
    meta: {
      asked,
      options: [{ text: '按部门', explain: '和去年的报表对得上' }, { text: '按项目' }],
      answer_log: !by
        ? []
        : skipped
          ? [{ kind: 'skipped', option: null, note: null, by, at: '2026-08-01T00:01:00Z', reply_id: null }]
          : [{ kind: 'option', option: '按部门', note: null, by, at: '2026-08-01T00:01:00Z', reply_id: 'r1' }],
    },
  } as unknown as Block
}

let vuetify: ReturnType<typeof createVuetify>
let history: Block[] = []
let sent: Array<{ url: string; body: Record<string, unknown> }> = []
/** 撤回那一次请求（`DELETE …/answer`）：落在这儿的就只有它。 */
let takenBack: string[] = []
/** 跳过那一次请求（`POST …/skip`）。 */
let skippedIds: string[] = []

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  setLocale('zh-CN')
  history = []
  sent = []
  takenBack = []
  skippedIds = []
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: 'me' }))
  vi.stubGlobal(
    'WebSocket',
    class {
      close() {}
      send() {}
      addEventListener() {}
      removeEventListener() {}
    }
  )
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    // 撤回：题目上那条答复没了，题回到还没人答 —— 服务端回来的就是这一块。
    if (String(url).endsWith('/answer') && init?.method === 'DELETE') {
      const id = String(url).split('/blocks/')[1].replace('/answer', '')
      takenBack.push(id)
      const open = question('withdrawn')
      return {
        ok: true,
        status: 200,
        json: async () => ({ code: 200, data: { ...open, id, meta: { ...open.meta, answer_log: [] } } }),
      }
    }
    // 跳过：题上记下「我跳过了」—— 服务端回来的就是这一块。
    if (String(url).endsWith('/skip') && init?.method === 'POST') {
      const id = String(url).split('/blocks/')[1].replace('/skip', '')
      skippedIds.push(id)
      return {
        ok: true,
        status: 200,
        json: async () => ({ code: 200, data: { ...question('skipped', 'me', 'me', true), id } }),
      }
    }
    if (String(url).includes('/messages') && init?.method === 'POST') {
      const body = JSON.parse(String(init.body))
      sent.push({ url: String(url), body })
      return {
        ok: true,
        status: 200,
        json: async () => ({
          code: 200,
          data: { ...question('sent'), id: 'reply', content: body.content, reply_to: body.reply_to, meta: {} },
        }),
      }
    }
    return {
      ok: true,
      status: 200,
      json: async () =>
        String(url).includes('/progress')
          ? { code: 200, data: { items: [], updated_at: null } }
          : String(url).includes('/tasks')
            ? { code: 200, data: { data: [], total: 0 } }
            : { code: 200, data: { data: history, total: history.length, has_more: false } },
    }
  })
})

const settle = () => new Promise((r) => setTimeout(r, 0))
async function until(ready: () => boolean, times = 200) {
  for (let i = 0; i < times && !ready(); i++) await settle()
}

async function open(topic: string, extra: Record<string, unknown> = {}) {
  const ui = render(Panel, {
    props: { topic: topicOf(topic), showComposer: true, ...extra },
    global: { plugins: [vuetify, i18n] },
  })
  await until(() => !!ui.container.querySelector('[data-mid]'))
  return ui
}

describe('芝士的提问是一条带快捷回复的消息', () => {
  it('题画在对话里，输入框照样在', async () => {
    // 每一格一间房：时间线按房间缓存在模块里，同一间会串味。
    history = [question('t-open')]
    const { container, getByRole } = await open('t-open')
    expect(container.textContent).toContain('预算按哪个口径统计？')
    expect(getByRole('button', { name: /按部门/ })).toBeTruthy()
    expect(container.querySelector('.composer')).toBeTruthy()
  })

  it('点一个快捷回复，就是把它作为对这道题的回复发出去', async () => {
    history = [question('t-click')]
    const { getByRole } = await open('t-click')
    await fireEvent.click(getByRole('button', { name: /按项目/ }))
    await until(() => sent.length > 0)
    expect(sent).toHaveLength(1)
    expect(sent[0].url).toContain('/topics/t-click/messages')
    expect(sent[0].body.content).toBe('按项目')
    expect(sent[0].body.reply_to).toBe('q-t-click')
  })

  it('任务关了、没人答过的题不再给点，说它不再等回答', async () => {
    history = [question('t-closed')]
    const { container, queryByRole } = await open('t-closed', { askClosed: true })
    expect(container.textContent).toContain('预算按哪个口径统计？')
    expect(queryByRole('button', { name: /按部门/ })).toBeNull()
    expect(container.querySelector('.ask-replies')?.textContent).toContain('已结束，不再等回答')
  })

  it('有人答过之后，快捷回复收成谁说了什么', async () => {
    history = [question('t-done', 'me')]
    const { container, queryByRole } = await open('t-done')
    expect(queryByRole('button', { name: /按项目/ })).toBeNull()
    expect(container.querySelector('.ask-replies__answers')?.textContent).toContain('按部门')
    expect(container.querySelector('.composer')).toBeTruthy()
  })

  it('点「其他」一个字都不发，只在原地长出输入框', async () => {
    history = [question('t-other-open')]
    const { getByRole } = await open('t-other-open')
    await fireEvent.click(getByRole('button', { name: '其他' }))
    await settle()
    // 「其他」是个入口，不是第四个答案：点它不该把「其他」当成他的答复发出去。
    expect(sent).toHaveLength(0)
    expect(getByRole('textbox', { name: '自己写一个答案' })).toBeTruthy()
  })

  it('在「其他」里打的字，就是他对这道题的答案', async () => {
    history = [question('t-other-send')]
    const { getByRole } = await open('t-other-send')
    await fireEvent.click(getByRole('button', { name: '其他' }))
    const field = getByRole('textbox', { name: '自己写一个答案' })
    await fireEvent.update(field, '按季度汇总')
    await fireEvent.keyDown(field, { key: 'Enter' })
    await until(() => sent.length > 0)
    expect(sent).toHaveLength(1)
    expect(sent[0].url).toContain('/topics/t-other-send/messages')
    expect(sent[0].body.content).toBe('按季度汇总')
    expect(sent[0].body.reply_to).toBe('q-t-other-send')
  })

  it('空着不发：没有字就没有答案', async () => {
    history = [question('t-other-empty')]
    const { getByRole } = await open('t-other-empty')
    await fireEvent.click(getByRole('button', { name: '其他' }))
    await fireEvent.keyDown(getByRole('textbox', { name: '自己写一个答案' }), { key: 'Enter' })
    await settle()
    expect(sent).toHaveLength(0)
  })
})

describe('自己答过的题可以撤回', () => {
  it('答的那一条旁边有撤回', async () => {
    history = [question('t-own', 'me')]
    const { container } = await open('t-own')
    const line = container.querySelector('.ask-replies__answers li')!
    expect(line.textContent).toContain('按部门')
    expect(within(line as HTMLElement).getByRole('button')).toBeTruthy()
  })

  it('别人的答案没有撤回入口', async () => {
    history = [question('t-theirs', 'alice')]
    const { container, queryByRole } = await open('t-theirs')
    expect(container.querySelector('.ask-replies__answers')?.textContent).toContain('按部门')
    expect(queryByRole('button', { name: /撤回/ })).toBeNull()
  })

  it('点了先弹确认：题会重新等回答，人点掉才发出去', async () => {
    history = [question('t-confirm', 'me')]
    const { getByRole } = await open('t-confirm')
    await fireEvent.click(getByRole('button', { name: /撤回/ }))
    const dialog = await screen.findByRole('alertdialog')
    expect(dialog.textContent).toContain('撤回这条回答？')
    expect(dialog.textContent).toContain('谁都能再选一次')
    expect(takenBack).toEqual([])
    await fireEvent.click(within(dialog).getByRole('button', { name: '取消' }))
    expect(takenBack).toEqual([])
  })

  it('确认之后才请服务端撤回，那一块原地换掉：题回到没人答', async () => {
    history = [question('t-yes', 'me')]
    const { container, getByRole, queryByRole } = await open('t-yes')
    await fireEvent.click(getByRole('button', { name: /撤回/ }))
    const dialog = await screen.findByRole('alertdialog')
    await fireEvent.click(within(dialog).getByRole('button', { name: '撤回' }))
    await until(() => takenBack.length > 0)
    expect(takenBack).toEqual(['q-t-yes'])
    // 题回到还没人答：选项重新长出来，撤回的入口也跟着没了。
    await until(() => !!container.querySelector('.ask-replies__options'))
    expect(queryByRole('button', { name: /按项目/ })).toBeTruthy()
    expect(container.querySelector('.ask-replies__answers')).toBeNull()
  })
})

describe('被问的人可以跳过这道题', () => {
  it('点 ✕ 不发任何消息，题上写着他跳过了，旁边有撤销', async () => {
    history = [question('t-skip')]
    const { container, getByRole, queryByRole } = await open('t-skip')
    await fireEvent.click(getByRole('button', { name: '跳过这道题' }))
    await until(() => skippedIds.length > 0)
    expect(skippedIds).toEqual(['q-t-skip'])
    expect(sent).toHaveLength(0)
    await until(() => !!container.querySelector('.ask-replies__answers'))
    expect(container.querySelector('.ask-replies__answers')?.textContent).toContain('已跳过')
    expect(queryByRole('button', { name: /按项目/ })).toBeNull()
    expect(getByRole('button', { name: /撤销/ })).toBeTruthy()
  })

  it('撤销跳过不用确认，题重新等他回答', async () => {
    history = [question('t-unskip', 'me', 'me', true)]
    const { container, getByRole, queryByRole } = await open('t-unskip')
    await fireEvent.click(getByRole('button', { name: /撤销/ }))
    await until(() => takenBack.length > 0)
    expect(takenBack).toEqual(['q-t-unskip'])
    expect(screen.queryByRole('alertdialog')).toBeNull()
    await until(() => !!container.querySelector('.ask-replies__options'))
    expect(queryByRole('button', { name: /按项目/ })).toBeTruthy()
    expect(getByRole('button', { name: '跳过这道题' })).toBeTruthy()
  })

  it('题在等别人时没有 ✕', async () => {
    history = [question('t-not-mine', null, 'alice')]
    const { getByRole, queryByRole } = await open('t-not-mine')
    expect(getByRole('button', { name: /按项目/ })).toBeTruthy()
    expect(queryByRole('button', { name: '跳过这道题' })).toBeNull()
  })

  it('别人跳过的题，看的人没有撤销', async () => {
    history = [question('t-their-skip', 'alice', 'alice', true)]
    const { container, queryByRole } = await open('t-their-skip')
    expect(container.querySelector('.ask-replies__answers')?.textContent).toContain('已跳过')
    expect(queryByRole('button', { name: /撤销/ })).toBeNull()
  })
})
