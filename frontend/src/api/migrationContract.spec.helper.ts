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
vi.mock('vuetify-sonner', () => ({ toast: { error: vi.fn() } }))

export const primary = 'https://primary.example.test/api'
export const newer = 'https://newer.example.test/api'
export const server = setupServer()
export const envelope = (data: unknown, code = 200) => ({ code, message: 'ok', data })
export const uploadFile = () => new File(['%PDF-1.7'], 'notes.pdf', { type: 'application/pdf' })

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
    const oldHttp = await import('@/network/api')
    const newHttp = await import('./axiosHttp')
    instances = [oldHttp.default.instance, oldHttp.NewApiInstance.instance, newHttp.axiosApi, newHttp.axiosNewApi]
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
}

/** Capture the observable browser request, not axios config or wrapper source. */
export async function compareRequests(
  test: ContractCase,
  oldCall: () => Promise<unknown>,
  newCall: () => Promise<unknown>,
  base = newer
) {
  const captures: unknown[] = []
  const payload = { contract: 'payload' }
  server.use(
    http.all(`${base}${test.path}`, async ({ request }) => {
      const url = new URL(request.url)
      expect(request.method).toBe(test.method)
      expect([...url.searchParams.entries()]).toEqual(test.query ?? [])
      expect(request.headers.get('authorization')).toBe('Bearer contract-token')
      let body: unknown
      if (test.fileFields) {
        expect(request.headers.get('content-type')).toContain('multipart/form-data; boundary=')
        const form = await request.formData()
        // Keep all entries: converting to an object would hide duplicate files or fields.
        const entries = [...form.entries()]
        expect(entries.filter(([, value]) => typeof value !== 'string')).toHaveLength(1)
        expect(entries.filter(([, value]) => typeof value === 'string')).toHaveLength(
          Object.keys(test.fileFields).length
        )
        body = await Promise.all(
          entries.map(async ([key, value]) => [
            key,
            typeof value === 'string'
              ? value
              : { name: value.name, type: value.type, bytes: [...new Uint8Array(await value.arrayBuffer())] },
          ])
        )
        expect(body).toEqual([
          ['file', { name: 'notes.pdf', type: 'application/pdf', bytes: [...new TextEncoder().encode('%PDF-1.7')] }],
          ...Object.entries(test.fileFields),
        ])
      } else {
        const text = await request.text()
        body = text ? JSON.parse(text) : undefined
        expect(body).toEqual(test.body)
      }
      captures.push({
        method: request.method,
        url: request.url,
        authorization: request.headers.get('authorization'),
        contentType: test.fileFields ? 'multipart/form-data' : request.headers.get('content-type'),
        body,
      })
      return test.blob
        ? new HttpResponse('download-bytes', { headers: { 'Content-Type': 'application/pdf' } })
        : HttpResponse.json(envelope(payload, 201), { status: 201 })
    })
  )
  const old = await oldCall()
  const next = await newCall()
  if (test.blob) {
    // XHR returns the browser's Blob realm, not the multipart undici realm.
    const read = (value: unknown) =>
      typeof (value as Blob).text === 'function'
        ? (value as Blob).text()
        : new Promise((resolve) => {
            const reader = new FileReader()
            reader.onload = () => resolve(reader.result)
            reader.readAsText(value as Blob)
          })
    expect(await read(old)).toBe('download-bytes')
    expect(await read(next)).toBe('download-bytes')
  } else {
    expect((old as { data: unknown }).data).toEqual(payload)
    expect(next).toEqual(payload)
  }
  expect(captures).toHaveLength(2)
  expect(captures[1]).toEqual(captures[0])
}
