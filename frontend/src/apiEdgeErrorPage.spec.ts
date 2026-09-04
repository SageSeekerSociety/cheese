// 边缘替应用答了话，那不是应用的答复。
//
// 黑客松现场实测：Cloudflare 隧道抖动的那几秒，它用一页 HTML 答 530（error
// 1033）；反向代理会拿一页文本答 502；强制门户和 SPA 自己的兜底页则用 200 答一页
// HTML。前端从没把它们当成成功，但用户看到的是 `HTTP 530 for /topics`、是
// `SyntaxError: Unexpected token '<'` —— 房间里的人于是问「服务器错误？」，以为
// 后端挂了。后端没挂，是那一秒没人替它接电话；下一秒就好了，而 GET 当时不重试。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, listProjects, markRead } from './api'

const HTML = '<!DOCTYPE html><html><body><h1>Argo Tunnel error</h1></body></html>'

function page(status: number, type = 'text/html; charset=UTF-8'): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': type }),
    json: async () => JSON.parse(HTML) as unknown,
  } as unknown as Response
}

const OK = {
  ok: true,
  status: 200,
  headers: new Headers({ 'content-type': 'application/json' }),
  json: async () => ({ code: 200, data: { data: [{ id: 'p1' }], total: 1 } }),
} as unknown as Response

describe('边缘的错误页不是应用的答复', () => {
  let calls: string[]

  beforeEach(() => {
    calls = []
    localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function serve(script: Response[]) {
    vi.stubGlobal('fetch', async (_url: string, init?: RequestInit) => {
      calls.push((init?.method ?? 'GET').toUpperCase())
      return script[calls.length - 1] ?? script[script.length - 1]
    })
  }

  it('GET：530 的页面来了两次再来 200，调用方看到的是成功', async () => {
    serve([page(530), page(530), OK])
    const started = Date.now()
    const out = await listProjects()
    expect(out.total).toBe(1)
    expect(calls).toEqual(['GET', 'GET', 'GET'])
    // 两次等待（250 + 750），不是三发连射。
    expect(Date.now() - started).toBeGreaterThanOrEqual(950)
  })

  it('GET：三次都是 530 的页面，抛的是那句人话，状态还在错误上', async () => {
    serve([page(530), page(530), page(530)])
    await expect(listProjects()).rejects.toMatchObject({
      status: 530,
      message: '服务暂时不可达，请稍后重试（HTTP 530）',
    })
    expect(calls).toHaveLength(3)
  })

  it('GET：200 却是一页 HTML，抛的是那句人话，不是 SyntaxError', async () => {
    serve([page(200)])
    const err: unknown = await listProjects().catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).not.toBeInstanceOf(SyntaxError)
    expect(err).toMatchObject({ status: 200, message: '服务暂时不可达，请稍后重试（HTTP 200）' })
    // 一页 HTML 和 530 一样是这一秒的事，所以也重试。
    expect(calls).toHaveLength(3)
  })

  it('GET：自称 JSON 却解析不了，同样不是应用的答复', async () => {
    serve([page(200, 'application/json')])
    await expect(listProjects()).rejects.toMatchObject({
      status: 200,
      message: '服务暂时不可达，请稍后重试（HTTP 200）',
    })
  })

  it('POST：502 的页面不重试，抛的是写操作那句', async () => {
    // 写操作不能由客户端重放：送没送达都不知道，再发一次可能就是两次。
    serve([page(502)])
    await expect(markRead('a1')).rejects.toMatchObject({
      status: 502,
      message: '服务暂时不可达，刚才的操作没有送达，请稍后重试（HTTP 502）',
    })
    expect(calls).toEqual(['POST'])
  })
})
