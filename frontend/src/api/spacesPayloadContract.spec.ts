// @vitest-environment jsdom
// spaces 域的命令-载荷语义契约：命令方法把返回的载荷原样交给调用方，只有真正空掉的
// 载荷才变成显式成功值。整域走新 API 源（`axiosNewApi`）。
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'

import { contractEnvironment, envelope, newer, server } from './requestContract.spec.helper'

contractEnvironment()

const values = [null, false, 0, '', { application: { id: 7 } }]
const payloadMethods = [
  { name: 'resubmit', path: '/space-applications/7/resubmit', args: [7, { name: '空间' }] },
  { name: 'review', path: '/admin/spaces/7/review', args: [7, false, '理由'] },
] as const

describe('spaces command versus payload semantics', () => {
  for (const method of payloadMethods) {
    it.each(values)(`${method.name} preserves payload %j without substituting command success`, async (payload) => {
      let count = 0
      server.use(
        http.post(`${newer}${method.path}`, () => {
          count++
          return HttpResponse.json(envelope(payload))
        })
      )
      const { SpacesApi: next } = await import('./spaces')
      const after = await (next[method.name] as (...args: unknown[]) => Promise<unknown>)(...method.args)
      expect(after).toEqual(payload)
      expect(count).toBe(1)
    })
  }

  it.each(values)('only nullish command payload %j becomes explicit success', async (payload) => {
    server.use(http.delete(`${newer}/spaces/7/members/9`, () => HttpResponse.json(envelope(payload))))
    const { SpacesApi } = await import('./spaces')
    const { default: handler } = await import('@/services/ErrorHandler')
    const result = await handler.withErrorHandling(() => SpacesApi.removeMember(7, 9), { showToast: false })
    expect(result).toEqual(payload == null ? true : payload)
    const refresh = vi.fn()
    if (result !== undefined) refresh()
    expect(refresh).toHaveBeenCalledOnce()
  })

  it.each(['omitted', '204'])('%s command completes the consumer continuation', async (kind) => {
    server.use(
      http.delete(`${newer}/spaces/7/members/9`, () =>
        kind === '204' ? new HttpResponse(null, { status: 204 }) : HttpResponse.json({ code: 200, message: 'ok' })
      )
    )
    const { SpacesApi } = await import('./spaces')
    const { default: handler } = await import('@/services/ErrorHandler')
    const result = await handler.withErrorHandling(() => SpacesApi.removeMember(7, 9), { showToast: false })
    expect(result).toBe(true)
  })
})
