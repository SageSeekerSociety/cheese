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
import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale } from '@/i18n'
import { loadMarkdownReader } from '@/test/markdownReader'

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
 *  `asked` 是它问了谁，`skipped` 是谁跳过了它，`all` 是问了频道里所有人。 */
function question(
  topic: string,
  by: string | null = null,
  asked: string[] = ['me'],
  skipped: string[] = [],
  all = false
): Block {
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
      asked_all: all,
      options: [{ text: '按部门', explain: '和去年的报表对得上' }, { text: '按项目' }],
      answer_log: [
        ...skipped.map((who) => ({
          kind: 'skipped',
          option: null,
          note: null,
          by: who,
          at: '2026-08-01T00:01:00Z',
          reply_id: null,
        })),
        ...(by
          ? [{ kind: 'option', option: '按部门', note: null, by, at: '2026-08-01T00:01:00Z', reply_id: 'r1' }]
          : []),
      ],
    },
  } as unknown as Block
}

/** 提问方收起的一道题：撤回（不再等人答）或过期（还能答）。 */
function closedByAsker(topic: string, kind: 'withdrawn' | 'expired'): Block {
  const open = question(topic)
  return {
    ...open,
    meta: {
      ...open.meta,
      answer_log: [{ kind, option: null, note: null, by: 'agent', at: '2026-08-02T00:00:00Z', reply_id: null }],
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
/** 拖住发消息那一次请求（不给就是不拖）：卡收起之前那一段只有靠它才看得见。 */
let holdMessages: (() => Promise<void>) | null = null

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  setLocale('zh-CN')
  history = []
  sent = []
  takenBack = []
  skippedIds = []
  holdMessages = null
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
        json: async () => ({ code: 200, data: { ...question('skipped', null, ['me'], ['me']), id } }),
      }
    }
    if (String(url).endsWith('/messages') && init?.method === 'POST') {
      const body = JSON.parse(String(init.body))
      sent.push({ url: String(url), body })
      // 停在路上的那一段：卡还没收起，题块也还没带着答案回来。
      if (holdMessages) await holdMessages()
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

beforeAll(loadMarkdownReader)

describe('芝士的提问是一条带快捷回复的消息', () => {
  it('题画在对话里，输入框照样在', async () => {
    // 每一格一间房：时间线按房间缓存在模块里，同一间会串味。
    history = [question('t-open')]
    const { container, getByRole } = await open('t-open')
    // 题目就是卡片的标题，按 markdown 画（渲染器是按需载入的）。
    // 渲染器按需载入，冷的 CI 上要几百毫秒：按时间等，不按空转的次数等。
    await waitFor(
      () => expect(container.querySelector('.ask-replies')?.textContent).toContain('预算按哪个口径统计？'),
      {
        timeout: 5_000,
      }
    )
    expect(container.querySelector('.ask-replies')?.textContent).toContain('预算按哪个口径统计？')
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

  // 卡片要等这条消息落库、题块再带着答案回来才收起；那一趟走完之前它还是「没答」的
  // 样子，手快就会多点出几条，房间里真会多出几条消息。
  it('手快连点两下，只发出一条', async () => {
    history = [question('t-double')]
    let release: () => void = () => {}
    holdMessages = () => new Promise<void>((resolve) => (release = resolve))
    const { getByRole } = await open('t-double')
    const option = getByRole('button', { name: /按部门/ })
    await fireEvent.click(option)
    await fireEvent.click(option)
    await fireEvent.click(option)
    expect(sent).toHaveLength(1)
    release()
    // 发件箱一次只送一条：没挡住的那几下会排着队，放行之后一条接一条地出去。
    await until(() => sent.length > 1, 50)
    expect(sent).toHaveLength(1)
  })

  it('任务关了、没人答过的题不再给点，说它不再等回答', async () => {
    history = [question('t-closed')]
    const { container, queryByRole } = await open('t-closed', { askClosed: true })
    // 题目就是卡片的标题，按 markdown 画（渲染器是按需载入的）。
    // 渲染器按需载入，冷的 CI 上要几百毫秒：按时间等，不按空转的次数等。
    await waitFor(
      () => expect(container.querySelector('.ask-replies')?.textContent).toContain('预算按哪个口径统计？'),
      {
        timeout: 5_000,
      }
    )
    expect(container.querySelector('.ask-replies')?.textContent).toContain('预算按哪个口径统计？')
    expect(queryByRole('button', { name: /按部门/ })).toBeNull()
    expect(container.querySelector('.ask-replies')?.textContent).toContain('已结束，不再等回答')
  })

  it('芝士把题收回去了：选项收起，说它收回了', async () => {
    history = [closedByAsker('t-taken-back', 'withdrawn')]
    const { container, queryByRole } = await open('t-taken-back')
    await waitFor(
      () => expect(container.querySelector('.ask-replies')?.textContent).toContain('预算按哪个口径统计？'),
      { timeout: 5_000 }
    )
    expect(queryByRole('button', { name: /按部门/ })).toBeNull()
    expect(container.querySelector('.ask-replies__other')).toBeNull()
    expect(container.querySelector('.ask-replies')?.textContent).toContain('芝士收回了这道题')
    // 收起了就没人答得成：点不了，题上也不会多出一条答复。
    expect(container.querySelector('.ask-replies__skip')).toBeNull()
  })

  it('题过了时效：还摆在下面，说它过期了，照样能答', async () => {
    history = [closedByAsker('t-expired', 'expired')]
    const { container, getByRole } = await open('t-expired')
    await waitFor(
      () => expect(container.querySelector('.ask-replies')?.textContent).toContain('预算按哪个口径统计？'),
      { timeout: 5_000 }
    )
    expect(container.querySelector('.ask-replies')?.textContent).toContain('这道题过期了，仍可以回答')
    await fireEvent.click(getByRole('button', { name: /按项目/ }))
    await until(() => sent.length > 0)
    expect(sent).toHaveLength(1)
    expect(sent[0].body.reply_to).toBe('q-t-expired')
  })

  it('有人答过之后，快捷回复收成谁说了什么', async () => {
    history = [question('t-done', 'me')]
    const { container, queryByRole } = await open('t-done')
    expect(queryByRole('button', { name: /按项目/ })).toBeNull()
    expect(container.querySelector('.ask-replies__answers')?.textContent).toContain('按部门')
    expect(container.querySelector('.composer')).toBeTruthy()
  })

  it('选项下面一直有个输入框，不用先点什么就能打字', async () => {
    history = [question('t-other-open')]
    const { getByRole } = await open('t-other-open')
    const field = getByRole('textbox', { name: '其他回答' }) as HTMLTextAreaElement
    expect(field.disabled).toBe(false)
    expect(sent).toHaveLength(0)
  })

  it('在末行打的字，回车就是他对这道题的答案', async () => {
    history = [question('t-other-send')]
    const { getByRole } = await open('t-other-send')
    const field = getByRole('textbox', { name: '其他回答' })
    await fireEvent.update(field, '按季度汇总')
    await fireEvent.keyDown(field, { key: 'Enter' })
    await until(() => sent.length > 0)
    expect(sent).toHaveLength(1)
    expect(sent[0].url).toContain('/topics/t-other-send/messages')
    expect(sent[0].body.content).toBe('按季度汇总')
    expect(sent[0].body.reply_to).toBe('q-t-other-send')
  })

  it('点末行的发送键也一样发出去', async () => {
    history = [question('t-other-button')]
    const { container, getByRole } = await open('t-other-button')
    await fireEvent.update(getByRole('textbox', { name: '其他回答' }), '按季度汇总')
    // 下面的输入框也有一颗「发送」：点的是题卡上那一颗。
    const card = container.querySelector('.ask-replies') as HTMLElement
    await fireEvent.click(within(card).getByRole('button', { name: '发送' }))
    await until(() => sent.length > 0)
    expect(sent).toHaveLength(1)
    expect(sent[0].body.content).toBe('按季度汇总')
    expect(sent[0].body.reply_to).toBe('q-t-other-button')
  })

  it('Shift+回车是换行，输入法选字的回车也不算：都不发', async () => {
    history = [question('t-other-hold')]
    const { getByRole } = await open('t-other-hold')
    const field = getByRole('textbox', { name: '其他回答' })
    await fireEvent.update(field, '按季度')
    await fireEvent.keyDown(field, { key: 'Enter', shiftKey: true })
    await fireEvent.keyDown(field, { key: 'Enter', isComposing: true })
    await fireEvent.keyDown(field, { key: 'Enter', keyCode: 229 })
    await settle()
    expect(sent).toHaveLength(0)
  })

  it('空着不发：没有字就没有答案', async () => {
    history = [question('t-other-empty')]
    const { getByRole } = await open('t-other-empty')
    await fireEvent.keyDown(getByRole('textbox', { name: '其他回答' }), { key: 'Enter' })
    await settle()
    expect(sent).toHaveLength(0)
  })
})

describe('卡有焦点时按数字键选选项', () => {
  it('按 2 就是点第二个选项', async () => {
    history = [question('t-key')]
    const { container } = await open('t-key')
    const card = container.querySelector('.ask-replies') as HTMLElement
    await fireEvent.keyDown(card, { key: '2' })
    await until(() => sent.length > 0)
    expect(sent).toHaveLength(1)
    expect(sent[0].body.content).toBe('按项目')
    expect(sent[0].body.reply_to).toBe('q-t-key')
  })

  it('在末行输入框里打数字是在打字，不选选项；没有那一项的数字也不算', async () => {
    history = [question('t-key-typing')]
    const { container, getByRole } = await open('t-key-typing')
    await fireEvent.keyDown(getByRole('textbox', { name: '其他回答' }), { key: '1' })
    await fireEvent.keyDown(container.querySelector('.ask-replies') as HTMLElement, { key: '9' })
    await settle()
    expect(sent).toHaveLength(0)
  })

  it('带修饰键的数字是别的快捷键，不选选项', async () => {
    history = [question('t-key-mod')]
    const { container } = await open('t-key-mod')
    await fireEvent.keyDown(container.querySelector('.ask-replies') as HTMLElement, { key: '1', metaKey: true })
    await settle()
    expect(sent).toHaveLength(0)
  })

  it('问的是别人：按数字键也不发', async () => {
    history = [question('t-key-theirs', null, ['alice'])]
    const { container } = await open('t-key-theirs')
    await fireEvent.keyDown(container.querySelector('.ask-replies') as HTMLElement, { key: '1' })
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
    history = [question('t-unskip', null, ['me'], ['me'])]
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
    history = [question('t-not-mine', null, ['alice'])]
    const { getByRole, queryByRole } = await open('t-not-mine')
    expect(getByRole('button', { name: /按项目/ })).toBeTruthy()
    expect(queryByRole('button', { name: '跳过这道题' })).toBeNull()
  })

  it('别人跳过的题，看的人没有撤销', async () => {
    history = [question('t-their-skip', null, ['alice'], ['alice'])]
    const { container, queryByRole } = await open('t-their-skip')
    expect(container.querySelector('.ask-replies__answers')?.textContent).toContain('已跳过')
    expect(queryByRole('button', { name: /撤销/ })).toBeNull()
  })
})

describe('题问了几个人', () => {
  it('卡上写着谁问了谁：问到我就是「你」，别人写名字，不画 @ 的 chip', async () => {
    history = [question('t-asks', null, ['me', 'alice'])]
    const { container } = await open('t-asks')
    const card = container.querySelector('.ask-replies') as HTMLElement
    expect(card.textContent).toContain('问你和 alice')
    expect(card.textContent).not.toContain('@')
    expect(card.querySelectorAll('.mention')).toHaveLength(0)
  })

  it('题是一串消息的第一条：名字那一行就在卡片上面，页头不再说一遍名字', async () => {
    history = [question('t-who-first')]
    const { container } = await open('t-who-first')
    const who = container.querySelector('.ask-replies__who')?.textContent?.trim()
    expect(who).toBe('问你')
  })

  it('题接在同一个人的上一条后面（上面没有名字那一行）：页头写出是谁问的', async () => {
    const before = { ...question('t-who-cont'), id: 'm-before', content: '我先看了一遍原始表。', meta: {} } as Block
    history = [before, question('t-who-cont')]
    const { container } = await open('t-who-cont')
    await until(() => container.querySelectorAll('[data-mid]').length === 2)
    const who = container.querySelector('.ask-replies__who')?.textContent?.trim()
    expect(who).toMatch(/^\S+ 问你$/)
  })

  it('问所有人的题写「所有人」，不逐个列名字', async () => {
    history = [question('t-asks-all', null, ['me', 'alice', 'bob'], [], true)]
    const { container } = await open('t-asks-all')
    const card = container.querySelector('.ask-replies') as HTMLElement
    expect(card.textContent).toContain('问所有人')
    expect(card.textContent).not.toContain('bob')
  })

  it('我跳过了，别人还在等：我这里只剩「已跳过」和撤销', async () => {
    history = [question('t-i-skipped', null, ['me', 'alice'], ['me'])]
    const { getByRole, queryByRole } = await open('t-i-skipped')
    expect(queryByRole('button', { name: /按项目/ })).toBeNull()
    expect(getByRole('button', { name: /撤销/ })).toBeTruthy()
  })

  it('别人跳过了，我还在被问：我照样看到选项和 ✕', async () => {
    history = [question('t-they-skipped', null, ['me', 'alice'], ['alice'])]
    const { getByRole, container } = await open('t-they-skipped')
    expect(getByRole('button', { name: /按项目/ })).toBeTruthy()
    expect(getByRole('button', { name: '跳过这道题' })).toBeTruthy()
    // 别人的跳过只是他自己的事，不在我这里列出来。
    expect(container.querySelector('.ask-replies__answers')).toBeNull()
  })

  it('点名的人都跳过了，谁那里都不再有选项', async () => {
    history = [question('t-all-skipped', null, ['me', 'alice'], ['me', 'alice'])]
    const { container, queryByRole } = await open('t-all-skipped')
    expect(queryByRole('button', { name: /按项目/ })).toBeNull()
    expect(container.querySelectorAll('.ask-replies__answers li')).toHaveLength(2)
  })
})

describe('只有被问到的人能点选项', () => {
  it('问的是别人：选项看得到但点不动，末行也打不了字，没有 ✕', async () => {
    history = [question('t-chip-theirs', null, ['alice'])]
    const { container, getByRole, queryByRole } = await open('t-chip-theirs')
    // 卡上那句「… 问 alice」说明了在等谁。
    expect(container.querySelector('.ask-replies')?.textContent).toContain('问 alice')
    const option = getByRole('button', { name: /按项目/ }) as HTMLButtonElement
    const other = getByRole('textbox', { name: '其他回答' }) as HTMLTextAreaElement
    // 灰掉的按钮浏览器不派发点击（happy-dom 照样派发，所以这里看的是 disabled 本身）。
    expect(option.disabled).toBe(true)
    expect(other.disabled).toBe(true)
    expect(queryByRole('button', { name: '跳过这道题' })).toBeNull()
  })

  it('问到了我，几个人里的一个也算：选项点得动', async () => {
    history = [question('t-chip-mine', null, ['alice', 'me'])]
    const { getByRole } = await open('t-chip-mine')
    await fireEvent.click(getByRole('button', { name: /按项目/ }))
    await until(() => sent.length > 0)
    expect(sent[0].body.reply_to).toBe('q-t-chip-mine')
  })

  it('问所有人：名单里没有我（后来才进频道）也点得动，也能跳过', async () => {
    history = [question('t-chip-all', null, ['alice', 'bob'], [], true)]
    const { getByRole } = await open('t-chip-all')
    expect(getByRole('button', { name: '跳过这道题' })).toBeTruthy()
    await fireEvent.click(getByRole('button', { name: /按项目/ }))
    await until(() => sent.length > 0)
    expect(sent[0].body.content).toBe('按项目')
  })

  it('没点名的题（平台发起的那一轮里问的）：谁都点得动、都能跳过，和以前一样', async () => {
    history = [question('t-chip-nobody', null, [])]
    const { getByRole } = await open('t-chip-nobody')
    expect(getByRole('button', { name: '跳过这道题' })).toBeTruthy()
    await fireEvent.click(getByRole('button', { name: /按项目/ }))
    await until(() => sent.length > 0)
    expect(sent[0].body.reply_to).toBe('q-t-chip-nobody')
  })
})
