import type { DocSelectionSnapshot } from '../lib/docAiSelection'

import { createApp } from 'vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { useDocAi } from './useDocAi'

const api = vi.hoisted(() => ({ source: vi.fn(), create: vi.fn(), validate: vi.fn() }))
vi.mock('../api/docAi', () => ({
  getDocAiSource: api.source,
  createDocAiRequest: api.create,
  listDocAiRequests: async () => ({ requests: [] }),
  getDocAiRequest: vi.fn(),
  getDocAiProposal: vi.fn(),
  acceptDocAiProposal: vi.fn(),
  cancelDocAiRequest: vi.fn(),
}))
// This isolates asynchronous preparation ordering, not PM/source provenance.
vi.mock('../lib/docAiSelection', () => ({ validateDocSelection: api.validate }))
const apps: ReturnType<typeof createApp>[] = []
const canonical = { document_id: 'doc', source: 'text', base_version: 4, offset_unit: 'utf8-bytes', nodes: [] }
beforeEach(() => {
  vi.resetAllMocks()
  localStorage.clear()
  localStorage.setItem('user', JSON.stringify({ id: 'human' }))
  api.create.mockResolvedValue({ request_id: 'r', state: 'pending' })
})
afterEach(() => apps.splice(0).forEach((app) => app.unmount()))
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { resolve, promise }
}
function setup() {
  let ai!: ReturnType<typeof useDocAi>
  const app = createApp({
    setup() {
      ai = useDocAi({
        topic: () => 'room',
        raw: () => 'text',
        prefix: () => '',
        version: () => 4,
        blocked: () => false,
        reload: async () => {},
      })
      return () => null
    },
  })
  app.mount(document.createElement('div'))
  apps.push(app)
  return ai
}
it.each(['source', 'validation'] as const)(
  'discards older preparation after late %s without overwriting the latest selection',
  async (phase) => {
    const ai = setup()
    const a = deferred<typeof canonical>()
    const hashA = deferred<{ node_id: string; start: number; end: number; exact_hash: string }>()
    api.source
      .mockImplementationOnce(() => (phase === 'source' ? a.promise : Promise.resolve(canonical)))
      .mockResolvedValue(canonical)
    const spanB = { node_id: 'b', start: 1, end: 4, exact_hash: 'b'.repeat(64) }
    api.validate.mockImplementation((snapshot) => (snapshot.from === 0 ? hashA.promise : Promise.resolve(spanB)))
    const old = ai.prepare({ from: 0 } as DocSelectionSnapshot)
    if (phase === 'validation') await vi.waitFor(() => expect(api.validate).toHaveBeenCalledTimes(1))
    await ai.prepare({ from: 1 } as DocSelectionSnapshot)
    a.resolve(canonical)
    hashA.resolve({ node_id: 'a', start: 0, end: 1, exact_hash: 'a'.repeat(64) })
    await old
    expect(ai.selection.value).toEqual(spanB)
    ai.question.value = 'only B'
    await ai.submit('propose')
    expect(api.create.mock.calls[0][1].selection).toEqual(spanB)
  }
)

it('keeps the latest prepared quote when an earlier quote hash finishes last', async () => {
  const ai = setup()
  api.source.mockResolvedValue(canonical)
  api.validate.mockImplementation((snapshot) =>
    Promise.resolve(
      snapshot.from === 0
        ? {
            node_id: 'a',
            start: 0,
            end: 4,
            exact_hash: '982d9e3eb996f559e633f4d194def3761d909f5a3b647d1a851fead67c32c9d1',
          }
        : {
            node_id: 'b',
            start: 1,
            end: 4,
            exact_hash: 'a7e7e2f59b128bdb0aa60f56f5211efefdf83b92994b8f4a5d2e18126a0a14de',
          }
    )
  )
  const digest = crypto.subtle.digest.bind(crypto.subtle)
  const gate = deferred<void>()
  let hashing = false
  const spy = vi.spyOn(crypto.subtle, 'digest').mockImplementationOnce(async (...args) => {
    hashing = true
    await gate.promise
    return digest(...args)
  })
  try {
    const old = ai.prepare({ from: 0 } as DocSelectionSnapshot)
    await vi.waitFor(() => expect(hashing).toBe(true))
    await ai.prepare({ from: 1 } as DocSelectionSnapshot)
    expect(ai.preparedContext.value).toEqual({ state: 'verified', original: 'ext', scope: 'selection', baseVersion: 4 })
    gate.resolve()
    await old
    expect(ai.preparedContext.value).toMatchObject({ state: 'verified', original: 'ext' })
    ai.question.value = 'only B'
    await ai.submit('propose')
    expect(api.create.mock.calls[0][1].selection.node_id).toBe('b')
  } finally {
    gate.resolve()
    spy.mockRestore()
  }
})
