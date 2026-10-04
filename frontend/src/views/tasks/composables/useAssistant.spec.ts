// 题目页上问芝士，对着服务端真实的回答形状（信封 + server-sent events）：问一句，
// 答案留在对话里；被拒的问题说出服务端给的原因，什么也不留。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAssistant } from './useAssistant'

import { setLocale } from '@/i18n'

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

/** The server: one task with no conversations yet, and ``ask`` answering as given. */
function serve(ask: () => Response) {
  const asked: string[] = []
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = (init?.method ?? 'GET').toUpperCase()
    if (url.endsWith('/assistant/tasks/7/conversations') && method === 'GET') {
      return envelope({ conversations: asked.length ? [{ ...CONVERSATION, title: asked[0] }] : [] })
    }
    if (url.endsWith('/assistant/tasks/7/conversations') && method === 'POST') {
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

describe('题目页上问芝士', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => {
    vi.unstubAllGlobals()
    setLocale('zh-CN')
  })

  it('sends the question and keeps the streamed answer in the conversation', async () => {
    const asked = serve(() => events(['delta', { text: '先会' }], ['delta', { text: ' gdb。' }], ['done', {}]))
    const a = useAssistant(() => 7)
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

  it("says the server's reason for a refused question and keeps nothing of it", async () => {
    serve(
      () =>
        new Response(JSON.stringify({ code: 429, message: '本月的芝士额度已用完，11月1日重置。' }), {
          status: 429,
          headers: { 'content-type': 'application/json' },
        })
    )
    const a = useAssistant(() => 7)
    await a.load()

    await a.ask('要先会什么？', '答不上来')

    expect(a.notice.value).toBe('本月的芝士额度已用完，11月1日重置。')
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
    const a = useAssistant(() => 7)
    await a.load()

    await a.ask('要先会什么？', 'fallback')

    expect(a.notice.value).toBe("Cheese can't answer right now. Try again later.")
  })

  it('an error event with an unknown key shows the server words', async () => {
    serve(() =>
      events(['error', { message: '服务端的原话', i18n: { key: 'noSuchSentence', params: {} } }], ['done', {}])
    )
    const a = useAssistant(() => 7)
    await a.load()

    await a.ask('要先会什么？', 'fallback')

    expect(a.notice.value).toBe('服务端的原话')
  })

  it('a refusal answered as a stream frame is said in the reader language', async () => {
    setLocale('en')
    serve(
      () =>
        new Response(
          `event: error\ndata: ${JSON.stringify({ message: '请先登录', i18n: { key: 'signInFirst', params: {} } })}\n\n`,
          { status: 401, headers: { 'content-type': 'text/event-stream' } }
        )
    )
    const a = useAssistant(() => 7)
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
    const a = useAssistant(() => 7)
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
      if (url.endsWith('/assistant/tasks/7/conversations') && method === 'GET')
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
    const a = useAssistant(() => 7)
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
      if (url.endsWith('/assistant/tasks/7/conversations') && method === 'GET')
        return envelope({ conversations: [CONVERSATION] })
      if (url.endsWith('/assistant/conversations/c1') && method === 'GET')
        return envelope({ ...CONVERSATION, messages: [], answering: null })
      if (url.endsWith('/assistant/conversations/c1/ask'))
        return placed(['answering', { id: 'q1' }], ['delta', { text: '先会', at: 0 }, '1-0'])
      return new Response('not found', { status: 404 })
    })
    const a = useAssistant(() => 7)
    await a.load()

    await a.ask('要先会什么？', '答不上来')

    expect(a.messages.value.map((m) => m.role)).toEqual(['user'])
    expect(a.notice.value).toBe('答不上来')
    expect(a.busy.value).toBe(false)
  })

  it('opening a conversation still being answered shows the answer as it goes on', async () => {
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.endsWith('/assistant/tasks/7/conversations') && method === 'GET')
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
    const a = useAssistant(() => 7)

    await a.load()
    await vi.waitFor(() => expect(a.busy.value).toBe(false))

    expect(a.messages.value.map((m) => [m.role, m.text])).toEqual([
      ['user', '要先会什么？'],
      ['assistant', '先会 gdb。'],
    ])
  })
})
