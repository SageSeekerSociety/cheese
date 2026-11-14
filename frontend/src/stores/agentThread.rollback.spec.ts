// 发出去没被收下的那一句，面板收回的只是它自己：同一毫秒里已经在对话里的话
// （刚问的问题、读回来的历史）一句不少。时钟冻在同一毫秒，对着服务端真实的回答形状。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createAssistant } from './agentThread'

const NOW = '2026-11-08T00:00:00.000Z'
const CONVERSATION = { id: 'c1', title: '', lastActiveAt: NOW, questions: 1 }

function envelope(data: unknown, status = 200): Response {
  return new Response(JSON.stringify({ code: status >= 400 ? status : 200, message: 'ok', data }), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

/** A stream the test closes itself: the answer is still being written until ``end``. */
function pendingAnswer() {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const body = new ReadableStream<Uint8Array>({ start: (c) => void (controller = c) })
  const end = () => {
    controller.enqueue(new TextEncoder().encode(`event: done\ndata: {}\n\n`))
    controller.close()
  }
  return {
    response: new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } }),
    end,
  }
}

/** Let the pending requests settle without moving the frozen clock (``vi.waitFor`` would advance it). */
async function until(ready: () => boolean) {
  for (let i = 0; i < 50 && !ready(); i++) await new Promise((resolve) => setTimeout(resolve, 0))
  expect(ready()).toBe(true)
}

const shown = (a: ReturnType<typeof createAssistant>) =>
  a.messages.value.filter((m) => m.role === 'user').map((m) => m.text)

describe('收回没被收下的那一句', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(NOW))
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('a failed word said mid-answer takes back only itself, not the question asked in the same millisecond', async () => {
    const answer = pendingAnswer()
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.endsWith('/assistant/conversations') && method === 'GET') return envelope({ conversations: [] })
      if (url.endsWith('/assistant/conversations') && method === 'POST') return envelope(CONVERSATION, 201)
      if (url.endsWith('/assistant/conversations/c1/ask')) return answer.response
      if (url.endsWith('/assistant/conversations/c1/say')) return envelope(null, 500)
      return new Response('not found', { status: 404 })
    })
    const a = createAssistant()
    await a.load()

    const asking = a.ask('把报告打分', '答不上来')
    await until(() => a.current.value === 'c1')
    await a.say('只看第 3 份', '没说上')

    expect(shown(a)).toEqual(['把报告打分'])
    expect(a.notice.value).toBe('没说上')
    answer.end()
    await asking
    expect(shown(a)).toEqual(['把报告打分'])
  })

  it('a failed word said mid-answer leaves an earlier stored line of the same millisecond on screen', async () => {
    const answer = pendingAnswer()
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.endsWith('/assistant/conversations') && method === 'GET')
        return envelope({ conversations: [CONVERSATION] })
      // 读回来的历史：服务端存下的那一句没有面板自己的标记。
      if (url.endsWith('/assistant/conversations/c1'))
        return envelope({
          ...CONVERSATION,
          answering: 'q1',
          messages: [{ seq: 1, role: 'user', text: '旧的一句', at: NOW }],
        })
      if (url.endsWith('/assistant/conversations/c1/answers/q1')) return answer.response
      if (url.endsWith('/assistant/conversations/c1/say')) return envelope(null, 500)
      return new Response('not found', { status: 404 })
    })
    const a = createAssistant()
    await a.load()
    await until(() => a.busy.value)

    await a.say('补一句', '没说上')

    expect(shown(a)).toEqual(['旧的一句'])
    answer.end()
  })

  it('a refused question leaves an earlier stored line of the same millisecond on screen', async () => {
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.endsWith('/assistant/conversations') && method === 'GET')
        return envelope({ conversations: [CONVERSATION] })
      if (url.endsWith('/assistant/conversations/c1'))
        return envelope({
          ...CONVERSATION,
          answering: null,
          messages: [
            { seq: 1, role: 'user', text: '旧的一句', at: NOW },
            { seq: 2, role: 'assistant', text: '旧的回答', at: NOW },
          ],
        })
      if (url.endsWith('/assistant/conversations/c1/ask'))
        return new Response(JSON.stringify({ code: 429, message: '额度用完了' }), {
          status: 429,
          headers: { 'content-type': 'application/json' },
        })
      return new Response('not found', { status: 404 })
    })
    const a = createAssistant()
    await a.load()

    await a.ask('再问一句', '答不上来')

    expect(a.messages.value.map((m) => [m.role, m.text])).toEqual([
      ['user', '旧的一句'],
      ['assistant', '旧的回答'],
    ])
    expect(a.notice.value).toContain('额度用完了')
  })
})
