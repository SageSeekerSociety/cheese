// 和芝士的那一条对话，对着服务端真实的回答形状（信封 + server-sent events）：问一句，
// 答案留在话题里；被拒的问题说出服务端给的原因，什么也不留；「新话题」把之前的留在上面。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createAssistant } from './agentThread'

import i18n, { setLocale } from '@/i18n'

const CONVERSATION = { id: 'c1', title: '', lastActiveAt: '2026-10-01T08:00:00Z', questions: 0 }

function envelope(data: unknown, status = 200): Response {
  return new Response(JSON.stringify({ code: 200, message: 'ok', data }), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function events(...frames: [string, object][]): Response {
  const body = frames.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join('')
  return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } })
}

/** The server: a person with no topics yet, and ``ask`` answering as given. */
function serve(ask: () => Response) {
  const asked: string[] = []
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = (init?.method ?? 'GET').toUpperCase()
    if (url.endsWith('/assistant/conversations') && method === 'GET') {
      return envelope({ conversations: asked.length ? [{ ...CONVERSATION, title: asked[0] }] : [] })
    }
    if (url.endsWith('/assistant/conversations') && method === 'POST') {
      return envelope(CONVERSATION, 201)
    }
    if (url.endsWith('/assistant/conversations/c1/ask')) {
      asked.push(JSON.parse(String(init?.body)).question)
      return ask()
    }
    return new Response('not found', { status: 404 })
  })
  return asked
}

describe('和芝士的那一条对话', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => {
    vi.unstubAllGlobals()
    setLocale('zh-CN')
  })

  it('sends the question and keeps the streamed answer in the conversation', async () => {
    const asked = serve(() => events(['delta', { text: '先会' }], ['delta', { text: ' gdb。' }], ['done', {}]))
    const a = createAssistant()
    await a.load()

    await a.ask('要先会什么？', '答不上来')

    expect(asked).toEqual(['要先会什么？'])
    expect(a.messages.value.map((m) => [m.role, m.text])).toEqual([
      ['user', '要先会什么？'],
      ['assistant', '先会 gdb。'],
    ])
    expect(a.notice.value).toBeNull()
    expect(a.busy.value).toBe(false)
  })

  it('words said mid-answer that the answer ended without hearing are asked on their own, not written twice', async () => {
    const bodies: Record<string, unknown>[] = []
    const answers = [
      events(
        ['delta', { text: '先读一遍。' }],
        ['unheard', { said: [{ text: '只看第 3 份', files: [{ path: '报告/3.pdf', name: '3.pdf' }] }] }],
        ['done', {}]
      ),
      events(['delta', { text: '好，只看第 3 份。' }], ['done', {}]),
    ]
    serve(() => answers.shift() as Response)
    const fetched = globalThis.fetch
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      if (url.endsWith('/ask')) bodies.push(JSON.parse(String(init?.body)))
      return fetched(url, init)
    })
    const a = createAssistant()
    await a.load()

    await a.ask('把报告打分', '答不上来')

    expect(bodies.map((b) => [b.question, b.kept ?? false, b.files ?? []])).toEqual([
      ['把报告打分', false, []],
      ['只看第 3 份', true, [{ path: '报告/3.pdf', name: '3.pdf' }]],
    ])
    expect(a.messages.value.map((m) => [m.role, m.text])).toEqual([
      ['user', '把报告打分'],
      ['assistant', '先读一遍。'],
      ['assistant', '好，只看第 3 份。'],
    ])
    expect(a.busy.value).toBe(false)
  })

  it('words said mid-answer are not asked again when the person stopped the answer', async () => {
    const answers = [
      events(
        ['delta', { text: '先读一遍。' }],
        ['unheard', { said: [{ text: '只看第 3 份' }] }],
        ['done', { stopped: true }]
      ),
    ]
    const asked = serve(() => answers.shift() ?? new Response('', { status: 500 }))
    const a = createAssistant()
    await a.load()

    await a.ask('把报告打分', '答不上来')

    expect(asked).toEqual(['把报告打分'])
  })

  it("says the server's reason for a refused question and keeps nothing of it", async () => {
    serve(
      () =>
        new Response(JSON.stringify({ code: 429, message: '本月的芝士额度已用完，11月1日重置。' }), {
          status: 429,
          headers: { 'content-type': 'application/json' },
        })
    )
    const a = createAssistant()
    await a.load()

    await a.ask('要先会什么？', '答不上来')

    // The server's own reason, as it said it: when the credits come back.
    expect(a.notice.value).toContain('11月1日')
    expect(a.messages.value).toEqual([])
    expect(a.busy.value).toBe(false)
    expect(a.creditRefused.value).toBe(false)
  })

  it('in English a failed answer says so in English, from the key on the error event', async () => {
    setLocale('en')
    serve(() =>
      events(
        ['error', { message: '芝士暂时答不上来，稍后再试。', i18n: { key: 'assistantFailed', params: {} } }],
        ['done', {}]
      )
    )
    const a = createAssistant()
    await a.load()

    await a.ask('要先会什么？', 'fallback')

    // The sentence the key names, in English: not the server's Chinese.
    expect(a.notice.value).toBe(i18n.global.t('apiError.assistantFailed'))
    expect(a.notice.value).not.toMatch(/[\u4e00-\u9fff]/)
  })

  it('an error event with an unknown key shows the server words', async () => {
    serve(() =>
      events(['error', { message: '服务端的原话', i18n: { key: 'noSuchSentence', params: {} } }], ['done', {}])
    )
    const a = createAssistant()
    await a.load()

    await a.ask('要先会什么？', 'fallback')

    expect(a.notice.value).toBe('服务端的原话')
  })

  it('a refusal answered as a stream frame is said in the reader language', async () => {
    setLocale('en')
    serve(
      () =>
        new Response(
          // 帧里的 data 就是同一支 JSON 会答的错误体（app/core/errors.py `_event_error`）。
          `event: error\ndata: ${JSON.stringify({
            code: 401,
            message: '请先登录',
            error: {
              name: 'UnauthorizedError',
              message: '请先登录',
              i18n: { key: 'signInFirst', params: {} },
            },
          })}\n\n`,
          { status: 401, headers: { 'content-type': 'text/event-stream' } }
        )
    )
    const a = createAssistant()
    await a.load()

    await a.ask('要先会什么？', 'fallback')

    expect(a.notice.value).toBe('Sign in first')
    expect(a.messages.value).toEqual([])
  })

  it('a refusal for credits is flagged so the panel can point to the usage page', async () => {
    serve(
      () =>
        new Response(
          JSON.stringify({
            code: 429,
            message: '本月额度已用完，11月1日重置。',
            error: {
              message: '本月额度已用完，11月1日重置。',
              i18n: { key: 'creditsMonthSpent', params: { month: 11, day: 1 } },
            },
          }),
          { status: 429, headers: { 'content-type': 'application/json' } }
        )
    )
    const a = createAssistant()
    await a.load()

    await a.ask('要先会什么？', '答不上来')

    expect(a.creditRefused.value).toBe(true)
    expect(a.notice.value).toBeTruthy()

    // The next question that is not refused for credits drops the pointer.
    serve(() => events(['delta', { text: '好。' }], ['done', {}]))
    await a.ask('再问一次', '答不上来')
    expect(a.creditRefused.value).toBe(false)
  })
})

/** Events with their place in the question's stream, as the server sends them
 *  once the question has an id. */
function placed(...frames: [string, object, string?][]): Response {
  const body = frames
    .map(([event, data, id]) => `${id ? `id: ${id}\n` : ''}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
    .join('')
  return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } })
}

describe('题目页上问芝士：连接断了，回答接着读', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('a stream cut before the end is read on from where it broke, and the whole answer is kept', async () => {
    const readOn: string[] = []
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.endsWith('/assistant/conversations') && method === 'GET')
        return envelope({ conversations: [CONVERSATION] })
      if (url.endsWith('/assistant/conversations/c1') && method === 'GET')
        return envelope({ ...CONVERSATION, messages: [], answering: null })
      if (url.endsWith('/assistant/conversations/c1/ask'))
        return placed(['answering', { id: 'q1' }], ['delta', { text: '先会', at: 0 }, '1-0'])
      if (url.includes('/assistant/conversations/c1/answers/q1')) {
        readOn.push(new URL(url, 'http://x').searchParams.get('after') ?? '')
        return placed(['delta', { text: '会 gdb。', at: 1 }, '2-0'], ['done', { stopped: false }, '3-0'])
      }
      return new Response('not found', { status: 404 })
    })
    const a = createAssistant()
    await a.load()

    await a.ask('要先会什么？', '答不上来')

    expect(readOn).toEqual(['1-0'])
    expect(a.messages.value.map((m) => [m.role, m.text])).toEqual([
      ['user', '要先会什么？'],
      ['assistant', '先会 gdb。'],
    ])
    expect(a.notice.value).toBeNull()
  })

  it('a stream cut that cannot be read on keeps no half answer and says it failed', async () => {
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.endsWith('/assistant/conversations') && method === 'GET')
        return envelope({ conversations: [CONVERSATION] })
      if (url.endsWith('/assistant/conversations/c1') && method === 'GET')
        return envelope({ ...CONVERSATION, messages: [], answering: null })
      if (url.endsWith('/assistant/conversations/c1/ask'))
        return placed(['answering', { id: 'q1' }], ['delta', { text: '先会', at: 0 }, '1-0'])
      return new Response('not found', { status: 404 })
    })
    const a = createAssistant()
    await a.load()

    await a.ask('要先会什么？', '答不上来')

    expect(a.messages.value.map((m) => m.role)).toEqual(['user'])
    expect(a.notice.value).toBe('答不上来')
    expect(a.busy.value).toBe(false)
  })

  it('opening a conversation still being answered shows the answer as it goes on', async () => {
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.endsWith('/assistant/conversations') && method === 'GET')
        return envelope({ conversations: [CONVERSATION] })
      if (url.endsWith('/assistant/conversations/c1') && method === 'GET')
        return envelope({
          ...CONVERSATION,
          messages: [{ role: 'user', text: '要先会什么？', at: '2026-10-01T08:00:00Z' }],
          answering: 'q1',
        })
      if (url.includes('/assistant/conversations/c1/answers/q1'))
        return placed(['delta', { text: '先会 gdb。', at: 0 }, '1-0'], ['done', { stopped: false }, '2-0'])
      return new Response('not found', { status: 404 })
    })
    const a = createAssistant()

    await a.load()
    await vi.waitFor(() => expect(a.busy.value).toBe(false))

    expect(a.messages.value.map((m) => [m.role, m.text])).toEqual([
      ['user', '要先会什么？'],
      ['assistant', '先会 gdb。'],
    ])
  })
})

describe('芝士这一轮做了什么', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('reasoning is one step that knows how long it took; a call is another', async () => {
    serve(() =>
      events(
        ['thinking', {}],
        ['tool', { name: 'cheese_task_search', call: 'c1' }],
        ['tool_args', { call: 'c1', text: '{"query":"Lab"}', at: 0 }],
        ['delta', { text: '有两道。' }],
        ['done', {}]
      )
    )
    const a = createAssistant()
    await a.load()

    await a.ask('空间里有哪些实验？', '答不上来')

    const steps = a.messages.value.at(-1)?.steps ?? []
    expect(steps.map((s) => [s.name, s.state])).toEqual([
      ['thinking', 'done'],
      ['cheese_task_search', 'done'],
    ])
    expect(typeof steps[0].ms).toBe('number')
    expect(steps[1].args).toBe('{"query":"Lab"}')
  })

  it('calls made together each end when they come back, not when the next one starts', async () => {
    serve(() =>
      events(
        ['tool', { name: 'cheese_read', call: 'c1', at: 0 }],
        ['tool', { name: 'cheese_find', call: 'c2', at: 0 }],
        ['tool_done', { call: 'c2' }],
        ['tool_done', { call: 'c1' }],
        ['delta', { text: '都读完了。', at: 0 }],
        ['done', {}]
      )
    )
    const ended: string[] = []
    const a = createAssistant({ on: { toolEnded: (call) => ended.push(call) } })
    await a.load()

    await a.ask('看看待办', '答不上来')

    expect(ended).toEqual(['c2', 'c1'])
    expect(a.messages.value.at(-1)?.steps?.map((s) => s.at)).toEqual([0, 0])
  })

  it('keeps how the subagents it started stood when the answer ended', async () => {
    const started = { id: 'branch-1', call: 'c1', description: '打分', status: 'running', steps: [] }
    serve(() =>
      events(
        ['tool', { name: 'cheese_subagent_start', call: 'c1', at: 0 }],
        ['tool_done', { call: 'c1' }],
        ['subagents', { branches: [started] }],
        ['subagents', { branches: [{ ...started, status: 'done' }] }],
        ['delta', { text: '打完了。', at: 0 }],
        ['done', {}]
      )
    )
    const a = createAssistant()
    await a.load()

    await a.ask('打分', '答不上来')

    expect(a.messages.value.at(-1)?.subagents?.map((s) => s.status)).toEqual(['done'])
  })

  it('an answer that did something and wrote nothing says so instead of standing empty', async () => {
    serve(() => events(['tool', { name: 'cheese_inbox', call: 'c1' }], ['tool_done', { call: 'c1' }], ['done', {}]))
    const a = createAssistant()
    await a.load()

    await a.ask('有什么待办？', '答不上来')

    expect(a.messages.value.at(-1)).toMatchObject({ role: 'assistant', text: '', silent: true })
  })

  it('a file 芝士 hands over stays with its answer, live and when the conversation is opened again', async () => {
    const file = { team: 9, path: '成绩分布.png', bytes: 84000 }
    serve(() => events(['tool', { call: 'g1', name: 'cheese_file_give' }], ['handed', file], ['done', {}]))
    const live = createAssistant()
    await live.load()
    await live.ask('画一张分布图', '答不上来')
    expect(live.messages.value.at(-1)?.handed).toEqual([file])

    vi.stubGlobal('fetch', async (url: string) => {
      if (url.endsWith('/assistant/conversations')) return envelope({ conversations: [CONVERSATION] })
      if (url.endsWith('/assistant/conversations/c1'))
        return envelope({
          ...CONVERSATION,
          answering: null,
          messages: [
            { role: 'user', text: '画一张分布图', at: '2026-10-20T08:00:00Z' },
            { role: 'assistant', text: '', at: '2026-10-20T08:00:09Z', parts: { steps: [], handed: [file] } },
          ],
        })
      return new Response('not found', { status: 404 })
    })
    const again = createAssistant()
    await again.load()
    expect(again.messages.value[1].handed).toEqual([file])
  })

  it('a conversation opened again shows what was kept of each answer', async () => {
    vi.stubGlobal('fetch', async (url: string) => {
      if (url.endsWith('/assistant/conversations')) return envelope({ conversations: [CONVERSATION] })
      if (url.endsWith('/assistant/conversations/c1'))
        return envelope({
          ...CONVERSATION,
          answering: null,
          messages: [
            { role: 'user', text: '改一下截止时间', at: '2026-10-20T08:00:00Z' },
            {
              role: 'assistant',
              text: '改好了。',
              at: '2026-10-20T08:00:09Z',
              parts: {
                steps: [
                  { call: 'thinking-0', name: 'thinking', args: '', ms: 3000 },
                  { call: 'c1', name: 'cheese_problem_write', args: '{"task":88}', failed: '草稿还不完整' },
                ],
                edits: [{ draft: 41, changed: { deadline: { before: 1, after: 2 } }, kept: [], notes: [] }],
                published: [],
              },
            },
          ],
        })
      return new Response('not found', { status: 404 })
    })
    const a = createAssistant()

    await a.load()

    const answer = a.messages.value[1]
    expect(answer.steps?.map((s) => [s.name, s.state, s.ms ?? null])).toEqual([
      ['thinking', 'done', 3000],
      ['cheese_problem_write', 'failed', null],
    ])
    expect(answer.edits?.[0].changed).toEqual({ deadline: { before: 1, after: 2 } })
    // Kept before the server kept writes: shown, not undone.
    expect(answer.edits?.[0].undoable).toBe(false)
  })

  it('a write is told by its kept id, and undoing it strikes what the server says is undone', async () => {
    const write = { id: 7, kind: 'draft', draft: 41, changed: { rank: { before: 2, after: 3 } }, undone: [] }
    const undone: string[] = []
    serve(() => events(['change', write], ['done', {}]))
    const answer = fetch
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      if (url.endsWith('/assistant/changes/7/undo')) {
        undone.push(JSON.parse(String(init?.body)).part)
        return envelope({ changes: [{ ...write, undone: ['difficulty'] }], draft: 41, version: 5, fields: { rank: 2 } })
      }
      return answer(url, init)
    })
    const seen: number[] = []
    const a = createAssistant({ on: { edit: (edit) => seen.push(edit.id) } })
    await a.load()

    await a.ask('难一点', '答不上来')
    expect(seen).toEqual([7])
    expect(a.messages.value[1].edits?.[0]).toMatchObject({ id: 7, undoable: true, undone: [] })

    const done = await a.undo(7, 'difficulty')

    expect(undone).toEqual(['difficulty'])
    expect(done.fields).toEqual({ rank: 2 })
    expect(a.messages.value[1].edits?.[0].undone).toEqual(['difficulty'])
  })

  it('a new topic keeps the last one above it, and earlier topics are read back on asking', async () => {
    const said = (title: string) => [
      { role: 'user', text: title, at: '2026-10-19T08:00:00Z' },
      { role: 'assistant', text: '好的。', at: '2026-10-19T08:00:05Z' },
    ]
    const TOPICS = [
      { id: 'c2', title: '这周要交什么', lastActiveAt: '2026-10-20T08:00:00Z', questions: 1 },
      { id: 'c1', title: '实验三从哪入手', lastActiveAt: '2026-10-19T08:00:00Z', questions: 1 },
    ]
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.endsWith('/assistant/conversations') && method === 'GET') return envelope({ conversations: TOPICS })
      if (url.endsWith('/assistant/conversations/c2'))
        return envelope({ ...TOPICS[0], answering: null, messages: said('这周要交什么') })
      if (url.endsWith('/assistant/conversations/c1'))
        return envelope({ ...TOPICS[1], answering: null, messages: said('实验三从哪入手') })
      return new Response('not found', { status: 404 })
    })
    const a = createAssistant()
    await a.load()

    // The thread opens on the latest topic; the one before it is not read yet.
    expect(a.current.value).toBe('c2')
    expect(a.hasOlder.value).toBe(true)

    a.startNew()
    expect(a.current.value).toBeNull()
    expect(a.messages.value).toEqual([])
    expect(a.earlier.value.map((topic) => topic.id)).toEqual(['c2'])

    await a.older()
    expect(a.earlier.value.map((topic) => topic.title)).toEqual(['实验三从哪入手', '这周要交什么'])
    expect(a.earlier.value[0].messages[0].text).toBe('实验三从哪入手')
    expect(a.hasOlder.value).toBe(false)
  })

  it('a topic that said more elsewhere is read again, keeping what was read back above it', async () => {
    const line = (seq: number, text = `第 ${seq} 句`) => ({ seq, role: 'user', text, at: '2026-10-19T08:00:00Z' })
    const TOPIC = { id: 'c3', title: '最近的', lastActiveAt: '2026-10-21T08:00:00Z', questions: 3 }
    let latest = [line(3), line(4)]
    vi.stubGlobal('fetch', async (url: string) => {
      if (url.endsWith('/assistant/conversations')) return envelope({ conversations: [TOPIC], next: null })
      if (url.endsWith('/assistant/conversations/c3'))
        return envelope({ ...TOPIC, answering: null, messages: latest, earlier: true })
      if (url.endsWith('/assistant/conversations/c3/messages?before=3'))
        return envelope({ messages: [line(1), line(2)], earlier: false })
      return new Response('not found', { status: 404 })
    })
    const a = createAssistant()
    await a.load()
    await a.older()

    latest = [line(4), line(5, '例行事务的结果')]
    await a.heard('c3')

    expect(a.messages.value.map((m) => m.seq)).toEqual([1, 2, 3, 4, 5])
    expect(a.messages.value.at(-1)?.text).toBe('例行事务的结果')
  })

  it('reading back fills the topic at the top first, then reads older topics a page at a time', async () => {
    const line = (seq: number) => ({ seq, role: 'user', text: `第 ${seq} 句`, at: '2026-10-19T08:00:00Z' })
    const LATEST = { id: 'c3', title: '最近的', lastActiveAt: '2026-10-21T08:00:00Z', questions: 3 }
    const OLDEST = { id: 'c1', title: '最早的', lastActiveAt: '2026-10-19T08:00:00Z', questions: 1 }
    const asked: string[] = []
    vi.stubGlobal('fetch', async (url: string) => {
      asked.push(url)
      if (url.endsWith('/assistant/conversations')) return envelope({ conversations: [LATEST], next: 'p2' })
      if (url.endsWith('/assistant/conversations?before=p2')) return envelope({ conversations: [OLDEST], next: null })
      if (url.endsWith('/assistant/conversations/c3'))
        return envelope({ ...LATEST, answering: null, messages: [line(3), line(4)], earlier: true })
      if (url.endsWith('/assistant/conversations/c3/messages?before=3'))
        return envelope({ messages: [line(1), line(2)], earlier: false })
      if (url.endsWith('/assistant/conversations/c1'))
        return envelope({ ...OLDEST, answering: null, messages: [line(1)], earlier: false })
      return new Response('not found', { status: 404 })
    })
    const a = createAssistant()
    await a.load()
    expect(a.currentEarlier.value).toBe(true)

    await a.older()
    expect(a.messages.value.map((m) => m.text)).toEqual(['第 1 句', '第 2 句', '第 3 句', '第 4 句'])
    expect(a.currentEarlier.value).toBe(false)
    expect(a.hasOlder.value).toBe(true)

    await a.older()
    expect(a.earlier.value.map((topic) => topic.title)).toEqual(['最早的'])
    expect(a.hasOlder.value).toBe(false)
  })
})
