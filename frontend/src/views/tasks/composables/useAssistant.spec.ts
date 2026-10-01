// 题目页上问芝士，对着服务端真实的回答形状（信封 + server-sent events）：问一句，
// 答案留在对话里；被拒的问题说出服务端给的原因，什么也不留。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAssistant } from './useAssistant'

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
  afterEach(() => vi.unstubAllGlobals())

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
  })
})
