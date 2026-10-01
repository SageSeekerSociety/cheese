import { createApp, defineComponent, nextTick, ref } from 'vue'
const flushPromises = async () => {
  await Promise.resolve()
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
}
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useDocAi } from './useDocAi'

const api = vi.hoisted(() => ({
  acceptDocAiProposal: vi.fn(),
  cancelDocAiRequest: vi.fn(),
  createDocAiRequest: vi.fn(),
  getDocAiProposal: vi.fn(),
  getDocAiRequest: vi.fn(),
  getDocAiSource: vi.fn(),
  listDocAiRequests: vi.fn(),
}))
vi.mock('../api/docAi', () => api)
const wrappers: ReturnType<typeof createApp>[] = []
beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('user', JSON.stringify({ id: 'human', username: 'human' }))
  vi.clearAllMocks()
  api.listDocAiRequests.mockResolvedValue({ requests: [] })
  api.getDocAiSource.mockResolvedValue({
    document_id: 'doc',
    base_version: 4,
    source: 'text',
    offset_unit: 'utf8-bytes',
    nodes: [],
  })
})
afterEach(() => wrappers.splice(0).forEach((wrapper) => wrapper.unmount()))
function setup() {
  const topic = ref('room')
  const version = ref(4)
  const blocked = ref(false)
  const reload = vi.fn().mockResolvedValue(undefined)
  let ai!: ReturnType<typeof useDocAi>
  const wrapper = createApp(
    defineComponent({
      setup() {
        ai = useDocAi({
          topic: () => topic.value,
          version: () => version.value,
          blocked: () => blocked.value,
          raw: () => 'text',
          prefix: () => '',
          reload,
        })
        return () => null
      },
    })
  )
  wrapper.mount(document.createElement('div'))
  wrappers.push(wrapper)
  return { ai, topic, version, blocked, reload }
}
describe('document AI operation recovery', () => {
  it('persists operation before sending and replays unknown with exactly the same payload', async () => {
    const { ai } = setup()
    await ai.prepare(null)
    ai.question.value = 'explain'
    api.createDocAiRequest.mockImplementationOnce(async (bodyTopic, body) => {
      expect(bodyTopic).toBe('room')
      expect(JSON.parse(localStorage.getItem('cheese.doc-ai.v1:human:room:operation')!).body).toEqual(body)
      throw new TypeError('network disconnected')
    })
    await ai.submit('ask')
    expect(ai.unknown.value?.kind).toBe('request')
    expect(ai.question.value).toBe('explain')
    const first = api.createDocAiRequest.mock.calls[0][1]
    api.createDocAiRequest.mockResolvedValue({ request_id: 'request', state: 'pending' })
    ai.question.value = 'new typing'
    await ai.recover()
    expect(api.createDocAiRequest.mock.calls[1][1]).toEqual(first)
    expect(ai.question.value).toBe('new typing')
    expect(ai.unknown.value).toBeNull()
  })
  it('never installs acceptance receipt and rereads canonical through the protected reload', async () => {
    const { ai, reload, blocked } = setup()
    await flushPromises()
    ai.cards.value = [
      {
        request: {
          request_id: 'r',
          state: 'succeeded',
          kind: 'propose',
          generation: 1,
          proposal_id: 'p',
          answer: 'done',
          error: null,
        },
        proposal: {
          proposal_id: 'p',
          request_id: 'r',
          revision: 2,
          state: 'pending',
          document_id: 'doc',
          base_version: 4,
          selection: { node_id: 'n', start: 0, end: 4, exact_hash: 'a'.repeat(64) },
          replacement: 'new',
          answer: 'done',
          accepted_by: null,
          accepted_version: null,
        },
      },
    ]
    api.acceptDocAiProposal.mockImplementation(async () => {
      blocked.value = true // user starts typing while the server applies
      return { content: 'old receipt', doc_version: 5 }
    })
    await ai.accept('p')
    expect(reload).toHaveBeenCalledWith('room')
    expect(api.acceptDocAiProposal.mock.calls[0][2]).toMatchObject({ expected_version: 4, revision: 2 })
  })
  it('isolates actor/topic and restores the destination draft without overwriting it', async () => {
    const { ai, topic } = setup()
    await ai.prepare(null)
    ai.question.value = 'old actor draft'
    localStorage.setItem('cheese.doc-ai.v1:human:other:draft', 'other draft')
    topic.value = 'other'
    expect(ai.question.value).toBe('other draft')
    expect(localStorage.getItem('cheese.doc-ai.v1:human:room:draft')).toBe('old actor draft')
    localStorage.setItem('user', JSON.stringify({ id: 'different', username: 'different' }))
    ai.question.value = 'must not leak'
    await ai.submit('ask')
    expect(api.createDocAiRequest).not.toHaveBeenCalled()
  })
  it('drops late responses after topic changes and blocks dirty writes', async () => {
    const { ai, topic, blocked } = setup()
    await ai.prepare(null)
    ai.question.value = 'explain'
    blocked.value = true
    await ai.submit('ask')
    expect(api.createDocAiRequest).not.toHaveBeenCalled()
    blocked.value = false
    let resolve!: (value: unknown) => void
    api.createDocAiRequest.mockReturnValue(
      new Promise((done) => {
        resolve = done
      })
    )
    const pending = ai.submit('ask')
    topic.value = 'other'
    ai.question.value = 'other question'
    resolve({ request_id: 'old', state: 'pending' })
    await pending
    expect(ai.question.value).toBe('other question')
    expect(ai.cards.value).toEqual([])
    expect(localStorage.getItem('cheese.doc-ai.v1:human:room:operation')).not.toBeNull()
  })
})
