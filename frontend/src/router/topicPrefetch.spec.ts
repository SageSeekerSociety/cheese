// Opening a topic waits on two things: the topic page's code and the topic's
// newest messages. The rule here is that the second does not wait for the
// first — the messages are already being fetched while the page code is still
// on its way. The page modules below never finish loading, which is exactly the
// moment the rule is about.
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { signedIn, never } = vi.hoisted(() => ({
  signedIn: { id: '7' },
  never: () => new Promise<never>(() => {}),
}))
vi.mock('@/me', () => ({ myId: () => signedIn.id, myHandle: () => (signedIn.id ? 'alice' : '') }))
vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  listBlocks: vi.fn().mockResolvedValue({ data: [], has_more: false }),
}))
vi.mock('@/views/workspace/ProjectShell.vue', never)
vi.mock('@/views/workspace/ProjectSidebar.vue', never)
vi.mock('@/views/workspace/TopicView.vue', never)

import { listBlocks } from '@/api'
import { blockCache, setCachedWindow } from '@/lib/blockCache'
import router from '@/router'

const PROJECT = '3f1a7c62-9d4e-4b8a-8f21-0c5d6e7a9b10'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.mocked(listBlocks).mockClear()
  blockCache.clear()
  signedIn.id = '7'
})

describe('opening a topic', () => {
  it('fetches its newest messages while the page code is still loading', async () => {
    const topic = '9b81c0de-1f22-4a33-9c44-5d6e7f8a9b01'
    void router.push(`/projects/${PROJECT}/topics/${topic}`)
    await vi.waitFor(() => expect(listBlocks).toHaveBeenCalledWith(topic, expect.anything()))
  })

  it('fetches nothing for a topic whose messages are already on hand', async () => {
    const topic = '9b81c0de-1f22-4a33-9c44-5d6e7f8a9b02'
    setCachedWindow(topic, { blocks: [], hasMore: false })
    void router.push(`/projects/${PROJECT}/topics/${topic}`)
    await new Promise((r) => setTimeout(r, 20))
    expect(listBlocks).not.toHaveBeenCalled()
  })

  it('fetches nothing when nobody is signed in', async () => {
    signedIn.id = ''
    const topic = '9b81c0de-1f22-4a33-9c44-5d6e7f8a9b03'
    void router.push(`/projects/${PROJECT}/topics/${topic}`)
    await new Promise((r) => setTimeout(r, 20))
    expect(listBlocks).not.toHaveBeenCalled()
  })
})
