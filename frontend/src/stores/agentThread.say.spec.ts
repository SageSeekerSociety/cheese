// 芝士答着时人又说了一句（`say`），对着服务端真实的形状：回答是一条还开着的事件流，插话是
// 另一个请求。服务端答完到页面收到 `done` 之间有一段空档：这时插话被拒（409，「芝士现在
// 没有在回答」，`backend/app/api/routes/assistant.py` 的 `say_in`），那句话不能丢，要在这一轮
// 收尾后当作一个新问题问；别的失败不重发，免得服务端其实存下了、又多问一遍。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createAssistant } from './agentThread'

const CONVERSATION = { id: 'c1', title: '', lastActiveAt: '2026-10-01T08:00:00Z', questions: 0 }

function envelope(data: unknown, status = 200): Response {
  return new Response(JSON.stringify({ code: 200, message: 'ok', data }), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function refusal(status: number, name: string, message: string): Response {
  return new Response(
    JSON.stringify({ code: status, message, error: { name, message, data: null, retryable: false } }),
    {
      status,
      headers: { 'content-type': 'application/json' },
    }
  )
}

const frame = (event: string, data: object) =>
  new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)

/** 一条回答流：先写一段字，`end()` 时才送到 `done`，好让插话落在答完、`done` 未到的空档里。 */
function openAnswer(text: string) {
  let push!: ReadableStreamDefaultController<Uint8Array>
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      push = controller
      controller.enqueue(frame('delta', { text }))
    },
  })
  return {
    response: new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } }),
    end() {
      push.enqueue(frame('done', {}))
      push.close()
    },
  }
}

function finished(text: string): Response {
  return new Response(new Blob([frame('delta', { text }), frame('done', {})]), {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  })
}

/** 服务端：`answers` 依次回每一次 ask，`said` 回插话。记下每次 ask 的请求体、每次插话的字。 */
function serve(answers: (() => Response)[], said: (text: string) => Promise<Response>) {
  const asked: Record<string, unknown>[] = []
  const says: string[] = []
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = (init?.method ?? 'GET').toUpperCase()
    if (url.endsWith('/assistant/conversations') && method === 'GET')
      return envelope({ conversations: asked.length ? [CONVERSATION] : [] })
    if (url.endsWith('/assistant/conversations') && method === 'POST') return envelope(CONVERSATION, 201)
    if (url.endsWith('/assistant/conversations/c1/ask')) {
      asked.push(JSON.parse(String(init?.body)))
      const next = answers.shift()
      return next ? next() : new Response('', { status: 500 })
    }
    if (url.endsWith('/assistant/conversations/c1/say')) {
      const text = JSON.parse(String(init?.body)).text as string
      says.push(text)
      return said(text)
    }
    return new Response('not found', { status: 404 })
  })
  return { asked, says }
}

/** 等到 `check` 成立，最多两秒（机器忙时流和请求要多转几圈）。 */
const until = async (check: () => boolean) => {
  const deadline = Date.now() + 2000
  while (!check() && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 5))
  expect(check()).toBe(true)
}

describe('芝士答着时插的话', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => vi.unstubAllGlobals())

  it('said in the gap after the answer ended and before its done arrived, it is asked as a new question', async () => {
    const first = openAnswer('先读一遍。')
    const { asked, says } = serve([() => first.response, () => finished('好，只看第 3 份。')], async () =>
      refusal(409, 'ConflictError', '芝士现在没有在回答，直接提问即可')
    )
    const a = createAssistant()
    await a.load()

    const asking = a.ask('把报告打分', '答不上来')
    await until(() => a.streaming.value === '先读一遍。')
    await a.say('只看第 3 份', '回答失败')

    // 被拒的那一刻：话还在眼前，不报失败。
    expect(says).toEqual(['只看第 3 份'])
    expect(a.messages.value.map((m) => [m.role, m.text])).toEqual([
      ['user', '把报告打分'],
      ['user', '只看第 3 份'],
    ])
    expect(a.notice.value).toBeNull()

    first.end()
    await asking
    await until(() => !a.busy.value && a.messages.value.length === 4)

    expect(asked.map((b) => [b.question, b.kept ?? false])).toEqual([
      ['把报告打分', false],
      ['只看第 3 份', false],
    ])
    expect(a.messages.value.map((m) => [m.role, m.text])).toEqual([
      ['user', '把报告打分'],
      ['assistant', '先读一遍。'],
      ['user', '只看第 3 份'],
      ['assistant', '好，只看第 3 份。'],
    ])
    expect(a.notice.value).toBeNull()
    expect(a.busy.value).toBe(false)
  })

  it('a say that failed for another reason is not sent again, even once the answer has ended', async () => {
    const first = openAnswer('先读一遍。')
    const { asked, says } = serve([() => first.response], async () => {
      // 服务端可能已经存下了这句才出错：它回话之前这一轮先收了尾。
      first.end()
      await until(() => !a.busy.value)
      return refusal(500, 'InternalServerError', '服务器出错了')
    })
    const a = createAssistant()
    await a.load()

    const asking = a.ask('把报告打分', '答不上来')
    await until(() => a.streaming.value === '先读一遍。')
    await a.say('只看第 3 份', '回答失败')
    await asking

    expect(says).toEqual(['只看第 3 份'])
    expect(asked.map((b) => b.question)).toEqual(['把报告打分'])
    expect(a.messages.value.map((m) => [m.role, m.text])).toEqual([
      ['user', '把报告打分'],
      ['assistant', '先读一遍。'],
    ])
    expect(a.notice.value).toBe('回答失败')
    expect(a.busy.value).toBe(false)
  })
})
