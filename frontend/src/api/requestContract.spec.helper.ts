// 请求契约的公共脚手架：`src/api/*` 的每一份 `*Contract.spec.ts` 都用它。
//
// 这里曾经是「新旧两套栈对拍」的一半：同一件事分别走 `@/network` 和 `./`，比两边
// 发出的请求一模一样，好证明迁移没改行为。旧栈退役之后没有另一侧可比，用例表本身
// 就成了契约 —— 每个用例写明方法、地址、查询、正文、上传形状，`contractRequest`
// 拿 MSW 看到的那一个请求逐条对。少了「和旧栈一样」这层转述，剩下的正好是要钉的东西。
import { Blob as NodeBlob, File as NodeFile } from 'node:buffer'

import type { AxiosInstance } from 'axios'

import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, beforeEach, expect, vi } from 'vitest'

const auth = vi.hoisted(() => ({ loggedIn: true, accessToken: 'contract-token' }))
const refresh = vi.hoisted(() => vi.fn())
const replace = vi.hoisted(() => vi.fn())
export { auth, refresh, replace }
vi.mock('@/services/account', () => ({ default: auth }))
vi.mock('@/lib/session', () => ({ refreshSession: refresh }))
vi.mock('@/router', () => ({ default: { replace } }))
vi.mock('vuetify-sonner', () => ({ toast: { error: vi.fn(), warning: vi.fn() } }))

export const primary = 'https://primary.example.test/api'
export const newer = 'https://newer.example.test/api'
export const server = setupServer()
export const envelope = (data: unknown, code = 200) => ({ code, message: 'ok', data })
export const uploadFile = () => new File(['%PDF-1.7'], 'notes.pdf', { type: 'application/pdf' })

/** 两个 axios 实例（主源、新源）是前端全部取数入口。把适配器换成浏览器 XHR，
 *  测试看到的才是浏览器真会发出的那个请求，而不是 axios 的 config。 */
export function contractEnvironment() {
  let instances: AxiosInstance[]
  let adapters: AxiosInstance['defaults']['adapter'][]
  beforeAll(async () => {
    const nativeFormData = (
      await new Request('https://form-data.example.test', {
        method: 'POST',
        body: 'probe=value',
        headers: { 'content-type': 'application/x-www-form-urlencoded' },
      }).formData()
    ).constructor
    vi.stubGlobal('FormData', nativeFormData)
    vi.stubGlobal('File', NodeFile)
    vi.stubGlobal('Blob', NodeBlob)
    vi.stubEnv('VITE_API_BASE_URL', primary)
    vi.stubEnv('VITE_NEW_API_BASE_URL', newer)
    server.listen({ onUnhandledRequest: 'error' })
    const newHttp = await import('./axiosHttp')
    instances = [newHttp.axiosApi, newHttp.axiosNewApi]
    adapters = instances.map((instance) => instance.defaults.adapter)
    for (const instance of instances) instance.defaults.adapter = 'xhr'
  })
  beforeEach(() => {
    auth.loggedIn = true
    auth.accessToken = 'contract-token'
    refresh.mockReset().mockResolvedValue({ kind: 'unreachable' })
    replace.mockReset()
  })
  afterEach(() => server.resetHandlers())
  afterAll(() => {
    instances?.forEach((instance, index) => {
      instance.defaults.adapter = adapters[index]
    })
    server.close()
    vi.unstubAllGlobals()
    vi.unstubAllEnvs()
  })
}

export type ContractCase = {
  method: string
  path: string
  args: unknown[]
  query?: [string, string][]
  body?: unknown
  fileFields?: Record<string, string>
  blob?: boolean
  /** 浏览器会不会带上刷新 cookie。`withCredentials` 决定它，所以一个方法丢了这个
   *  开关仍然发同样的方法、地址和正文，只有这一个值能把它们分开。默认不带。 */
  credentials?: 'include' | 'same-origin'
}

/** 发一次请求，拿 MSW 看到的那个请求对用例表逐条比，再看返回值。 */
export async function contractRequest(test: ContractCase, newCall: () => Promise<unknown>, base = newer) {
  const payload = { contract: 'payload' }
  server.use(
    http.all(`${base}${test.path}`, async ({ request }) => {
      const url = new URL(request.url)
      expect(request.method).toBe(test.method)
      expect([...url.searchParams.entries()]).toEqual(test.query ?? [])
      expect(request.headers.get('authorization')).toBe('Bearer contract-token')
      expect(request.credentials).toBe(test.credentials ?? 'same-origin')
      if (test.fileFields) {
        expect(request.headers.get('content-type')).toContain('multipart/form-data; boundary=')
        const form = await request.formData()
        // Keep all entries: converting to an object would hide duplicate files or fields.
        const entries = [...form.entries()]
        expect(entries.filter(([, value]) => typeof value !== 'string')).toHaveLength(1)
        expect(entries.filter(([, value]) => typeof value === 'string')).toHaveLength(
          Object.keys(test.fileFields).length
        )
        expect(
          await Promise.all(
            entries.map(async ([key, value]) => [
              key,
              typeof value === 'string'
                ? value
                : { name: value.name, type: value.type, bytes: [...new Uint8Array(await value.arrayBuffer())] },
            ])
          )
        ).toEqual([
          ['file', { name: 'notes.pdf', type: 'application/pdf', bytes: [...new TextEncoder().encode('%PDF-1.7')] }],
          ...Object.entries(test.fileFields),
        ])
      } else {
        const text = await request.text()
        // A body that goes out with the wrong Content-Type still parses here, so
        // the header is asserted wherever a body is sent at all. Reads carry no
        // Content-Type and are left alone.
        if (test.body !== undefined) {
          expect(request.headers.get('content-type')).toContain('application/json')
        }
        expect(text ? JSON.parse(text) : undefined).toEqual(test.body)
      }
      return test.blob
        ? new HttpResponse('download-bytes', { headers: { 'Content-Type': 'application/pdf' } })
        : HttpResponse.json(envelope(payload, 201), { status: 201 })
    })
  )
  const next = await newCall()
  if (test.blob) {
    // XHR returns the browser's Blob realm, not the multipart undici realm.
    expect(
      await (typeof (next as Blob).text === 'function'
        ? (next as Blob).text()
        : new Promise((resolve) => {
            const reader = new FileReader()
            reader.onload = () => resolve(reader.result)
            reader.readAsText(next as Blob)
          }))
    ).toBe('download-bytes')
  } else {
    expect(next).toEqual(payload)
  }
}
