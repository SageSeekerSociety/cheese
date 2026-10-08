// 一个请求在流开始之前被拒绝时，答的不是 JSON，是一帧 `event: error`。帧里的
// data 就是同一支 JSON 会答的那个错误体：调用方按 `error.name` 判别、按
// `error.i18n` 用读者的语言说那句话，和收到 JSON 时一样。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { postEventStream, StreamRefused } from './eventStream'

import { setLocale } from '@/i18n'
import { refusalWords } from '@/lib/noticeText'

const refusal = {
  code: 403,
  message: '只有被邀请的人能答复这张邀请',
  error: {
    name: 'ForbiddenError',
    message: '只有被邀请的人能答复这张邀请',
    data: null,
    retryable: false,
    i18n: { key: 'inviteNotYours', params: {} },
  },
}

function answered(status: number, body: string, type: string): Response {
  return new Response(body, { status, headers: { 'content-type': type } })
}

function refusedBy(respond: () => Promise<Response>): Promise<StreamRefused> {
  vi.stubGlobal('fetch', respond)
  return postEventStream('/x', {}, () => {}).then(
    () => {
      throw new Error('expected a refusal')
    },
    (e: unknown) => e as StreamRefused
  )
}

const framed = () =>
  refusedBy(async () => answered(403, `event: error\ndata: ${JSON.stringify(refusal)}\n\n`, 'text/event-stream'))

describe('流开始前被拒绝', () => {
  beforeEach(() => setLocale('zh-CN'))
  afterEach(() => {
    vi.unstubAllGlobals()
    setLocale('zh-CN')
  })

  it('carries the whole error body, not just the sentence', async () => {
    const e = await framed()

    expect(e).toBeInstanceOf(StreamRefused)
    expect(e.status).toBe(403)
    expect(e.body).toEqual(refusal)
    expect((e.body.error as { name?: string }).name).toBe('ForbiddenError')
  })

  it('words a streamed refusal in the reader’s language, like a JSON one', async () => {
    const e = await framed()

    expect(refusalWords(e.body)).toBe('只有被邀请的人能答复这张邀请')
    setLocale('en')
    expect(refusalWords(e.body)).toBe('Only the invited person can answer this invitation')
  })

  it('takes a JSON refusal as it is', async () => {
    const e = await refusedBy(async () => answered(403, JSON.stringify(refusal), 'application/json'))

    expect(e.body).toEqual(refusal)
  })
})
