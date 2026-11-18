import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useHandIn } from './handIn'

const flushPromises = () => new Promise((resolve) => setTimeout(resolve))

const api = vi.hoisted(() => ({
  getHandInStanding: vi.fn(),
  getHandInPanel: vi.fn(),
  draftHandIn: vi.fn(),
  handIn: vi.fn(),
}))
vi.mock('@/api', () => api)

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((ok, fail) => {
    resolve = ok
    reject = fail
  })
  return { promise, resolve, reject }
}

function panel() {
  return useHandIn({
    taskId: () => 'task-1',
    me: ref('alice'),
    waiting: () => false,
    mayHandIn: () => true,
    onHandedIn: vi.fn(),
    reportError: vi.fn(),
  })
}

beforeEach(() => {
  vi.resetAllMocks()
  api.getHandInStanding.mockResolvedValue({ unhanded: true, waiting: false })
  api.getHandInPanel.mockResolvedValue({
    files: [{ path: 'index.html', additions: 1, deletions: 1 }],
    again: false,
    subject: null,
    body: null,
    reviewers: ['alice'],
  })
})

describe('the hand-in panel drafts its 说明 without taking it from the person', () => {
  it('fills the title and body with the draft when nobody typed', async () => {
    api.draftHandIn.mockResolvedValue({ subject: 'Invite sign-ups', body: 'One line.', drafted: true })
    const state = panel()

    await state.openPanel()

    expect(state.subject.value).toBe('Invite sign-ups')
    expect(state.body.value).toBe('One line.')
    expect(state.drafting.value).toBe(false)
  })

  it('keeps what the person typed when the draft arrives later', async () => {
    const draft = deferred<{ subject: string; body: string; drafted: boolean }>()
    api.draftHandIn.mockReturnValue(draft.promise)
    const state = panel()

    const opening = state.openPanel()
    await flushPromises()
    state.touchSubject('My own title')
    draft.resolve({ subject: 'Drafted title', body: 'Drafted body.', drafted: true })
    await opening

    expect(state.subject.value).toBe('My own title')
    expect(state.body.value).toBe('Drafted body.')
  })

  it('leaves the fields usable when drafting fails', async () => {
    api.draftHandIn.mockRejectedValue(new Error('down'))
    const state = panel()

    await state.openPanel()
    state.touchSubject('Typed by hand')

    expect(state.drafting.value).toBe(false)
    expect(state.ready.value).toBe(true)
  })

  it('drops a draft that arrives after the panel was closed', async () => {
    const draft = deferred<{ subject: string; body: string; drafted: boolean }>()
    api.draftHandIn.mockReturnValue(draft.promise)
    const state = panel()

    const opening = state.openPanel()
    await flushPromises()
    state.closePanel()
    draft.resolve({ subject: 'Too late', body: '', drafted: true })
    await opening

    expect(state.subject.value).toBe('')
    expect(state.open.value).toBe(false)
  })
})
