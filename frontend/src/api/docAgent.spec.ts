// 文档里问 AI 队友：回答在服务端答到底，连接断了就从断的地方接着读，读的是这个框自己那段对话。
import { afterEach, describe, expect, it, vi } from 'vitest'

import { askDocAgent } from './docAgent'

function placed(...frames: [string, object, string?][]): Response {
  const body = frames
    .map(([event, data, id]) => `${id ? `id: ${id}\n` : ''}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
    .join('')
  return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } })
}

describe('文档里问 AI 队友', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('a stream cut before the end is read on in the box conversation, from where it broke', async () => {
    const readOn: string[] = []
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      if ((init?.method ?? 'GET').toUpperCase() === 'POST')
        return placed(
          ['conversation', { id: 'box1' }],
          ['working', {}],
          ['answering', { id: 'q1' }],
          ['delta', { text: '范围', at: 0 }, '1-0']
        )
      readOn.push(url)
      return placed(
        ['delta', { text: '指第二节。', at: 2 }, '2-0'],
        ['done', { answer: '范围指第二节。', edits: [], stopped: false }, '3-0']
      )
    })
    const heard: string[] = []

    await askDocAgent('room1', { text: '范围指什么？' }, (event) => heard.push(event))

    expect(readOn).toHaveLength(1)
    expect(readOn[0]).toContain('/documents/room1/agent/box1/answers/q1?after=1-0')
    expect(heard.at(-1)).toBe('done')
  })
})
