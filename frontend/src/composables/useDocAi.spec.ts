import { createApp, defineComponent, nextTick, ref } from 'vue'
const flushPromises = async () => {
  await Promise.resolve()
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
}
import { Editor } from '@tiptap/core'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api'
import { captureDocSelection } from '../lib/docAiSelection'
import { docExtensions } from '../lib/docSchema'

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
  it.each(['blocked', 'version'] as const)(
    'retires an old selected quote and proposal after the document becomes %s',
    async (change) => {
      const { ai, blocked, version } = setup()
      const editor = new Editor({ extensions: docExtensions(), content: 'text', contentType: 'markdown' })
      try {
        editor.commands.setTextSelection({ from: 1, to: 5 })
        api.getDocAiSource.mockResolvedValue({
          document_id: 'doc',
          base_version: 4,
          source: 'text',
          offset_unit: 'utf8-bytes',
          nodes: [{ node_id: 'n', start: 0, end: 4 }],
        })
        await ai.prepare(captureDocSelection(editor))
        expect(ai.preparedContext.value).toMatchObject({ state: 'verified', original: 'text', scope: 'selection' })
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
              selection: ai.selection.value!,
              replacement: 'new',
              answer: 'done',
              accepted_by: null,
              accepted_version: null,
            },
          },
        ]
        ai.question.value = 'use that quote'
        if (change === 'blocked') blocked.value = true
        else version.value = 5
        expect(ai.preparedContext.value).toEqual({ state: 'unavailable' })
        await ai.submit('propose')
        await ai.accept('p')
        expect(api.createDocAiRequest).not.toHaveBeenCalled()
        expect(api.acceptDocAiProposal).not.toHaveBeenCalled()
      } finally {
        editor.destroy()
      }
    }
  )

  it('reads the original question and document from the persisted request after later edits', async () => {
    const { ai, version } = setup()
    version.value = 9
    ai.question.value = 'a different draft'
    api.listDocAiRequests.mockResolvedValue({ requests: [{ request_id: 'r' }] })
    api.getDocAiRequest.mockResolvedValue({
      request_id: 'r',
      state: 'succeeded',
      kind: 'ask',
      generation: 1,
      proposal_id: null,
      answer: 'done',
      error: null,
      frozen_context: {
        question: 'original question',
        document_id: 'doc',
        base_version: 4,
        source: 'text',
        source_hash: '982d9e3eb996f559e633f4d194def3761d909f5a3b647d1a851fead67c32c9d1',
        selection: null,
        offset_unit: 'utf8-bytes',
      },
    })
    await ai.refresh()
    expect(ai.cards.value[0].context).toEqual({
      state: 'verified',
      question: 'original question',
      original: 'text',
      scope: 'document',
      baseVersion: 4,
    })
    expect(ai.question.value).toBe('a different draft')
    expect(api.createDocAiRequest).not.toHaveBeenCalled()
    expect(api.acceptDocAiProposal).not.toHaveBeenCalled()
  })
  it.each(['room', 'actor'] as const)('discards a late frozen-source hash after changing %s', async (change) => {
    const { ai, topic } = setup()
    await flushPromises()
    api.listDocAiRequests.mockResolvedValueOnce({ requests: [{ request_id: 'r' }] }).mockResolvedValue({ requests: [] })
    api.getDocAiRequest.mockResolvedValue({
      request_id: 'r',
      state: 'succeeded',
      kind: 'ask',
      generation: 1,
      proposal_id: null,
      answer: 'done',
      error: null,
      frozen_context: {
        question: 'old question',
        document_id: 'doc',
        base_version: 4,
        source: 'text',
        source_hash: '982d9e3eb996f559e633f4d194def3761d909f5a3b647d1a851fead67c32c9d1',
        selection: null,
        offset_unit: 'utf8-bytes',
      },
    })
    const digest = crypto.subtle.digest.bind(crypto.subtle)
    let release!: () => void
    let hashing = false
    const gate = new Promise<void>((done) => {
      release = done
    })
    const spy = vi.spyOn(crypto.subtle, 'digest').mockImplementationOnce(async (...args) => {
      hashing = true
      await gate
      return digest(...args)
    })
    try {
      const pending = ai.refresh()
      await vi.waitFor(() => expect(hashing).toBe(true))
      if (change === 'room') topic.value = 'other'
      else {
        localStorage.setItem('cheese.doc-ai.v1:different:room:draft', 'new actor draft')
        localStorage.setItem('user', JSON.stringify({ id: 'different', username: 'different' }))
        await vi.waitFor(() => expect(ai.question.value).toBe('new actor draft'))
      }
      release()
      await pending
      expect(ai.cards.value).toEqual([])
      expect(ai.preparedContext.value).toEqual({ state: 'unavailable' })
    } finally {
      release()
      spy.mockRestore()
    }
  })
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
  it.each(['request', 'accept'] as const)(
    'retains unknown %s across denied recovery and only replays the original ID',
    async (kind) => {
      const { ai, reload } = setup()
      await ai.prepare(null)
      ai.question.value = 'explain'
      const accepted = {
        kind: 'accept',
        proposal: 'p',
        body: { operation_id: crypto.randomUUID(), expected_version: 4, revision: 2 },
      } as const
      if (kind === 'accept') ai.unknown.value = accepted
      const send = kind === 'request' ? api.createDocAiRequest : api.acceptDocAiProposal
      const committed = new Set<string>()
      let attempt = 0
      send.mockImplementation(async (...args: unknown[]) => {
        const body = args[kind === 'request' ? 1 : 2] as { operation_id: string }
        attempt++
        if (attempt === 2) throw new ApiError(403, 'membership revoked before receipt lookup')
        committed.add(body.operation_id)
        if (attempt === 1) throw new TypeError('response lost after server commit')
        return kind === 'request' ? { request_id: 'r', state: 'pending' } : { doc_version: 5 }
      })
      if (kind === 'request') await ai.submit('ask')
      else await ai.recover()
      const original = JSON.parse(JSON.stringify(ai.unknown.value))
      const stored = localStorage.getItem('cheese.doc-ai.v1:human:room:operation')
      await ai.recover()
      expect(ai.unknown.value).toEqual(original)
      expect(localStorage.getItem('cheese.doc-ai.v1:human:room:operation')).toBe(stored)
      expect(ai.error.value).toContain('membership revoked')
      await ai.submit('ask')
      await ai.accept('p')
      expect(send).toHaveBeenCalledTimes(2)
      await ai.recover()
      const payloads = send.mock.calls.map((args) => args[kind === 'request' ? 1 : 2])
      expect(payloads).toEqual([original.body, original.body, original.body])
      expect(committed.size).toBe(1)
      expect(ai.unknown.value).toBeNull()
      expect(localStorage.getItem('cheese.doc-ai.v1:human:room:operation')).toBeNull()
      if (kind === 'accept') expect(reload).toHaveBeenCalledWith('room')
    }
  )
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
