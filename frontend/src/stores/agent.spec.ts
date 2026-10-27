// 交给芝士的文件：先传上去，传完那一行不再转圈，下一个问题带上它。普通页面交的文件
// 进这个人自己的资料库；发布、改题目页上交的是题目的材料，存成附件。
import { effectScope, nextTick, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

const upload = vi.fn()
const keep = vi.fn()
vi.mock('@/lib/libraryApi', () => ({ uploadLibraryFile: (...a: unknown[]) => keep(...a) }))
vi.mock('@/api/teams', () => ({ TeamsApi: { detailByHandle: async () => ({ team: { id: 9 } }) } }))
vi.mock('@/me', () => ({ myHandle: () => 'lin' }))
const post = vi.fn()
const openTask = vi.fn()
const settle = vi.fn()
vi.mock('@/api/attachments', () => ({ AttachmentsApi: { upload: (...a: unknown[]) => upload(...a) } }))
vi.mock('@/api/messages', () => ({ postChatMessage: (...a: unknown[]) => post(...a) }))
vi.mock('@/api', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  createRoomTask: (...a: unknown[]) => openTask(...a),
  AgentProposalsApi: { settle: (...a: unknown[]) => settle(...a) },
}))
vi.mock('vuetify-sonner', () => ({ toast: { error: vi.fn() } }))

import type { AssistantProposal } from './agentThread'

import { toast } from 'vuetify-sonner'

import {
  agentFiles,
  agentPageOff,
  agentSelection,
  agentThread,
  askAgent,
  attachToAgent,
  pickForAgent,
  removeAgentFile,
  resetAgent,
  selectForAgent,
  sendProposal,
  useAgentContext,
} from './agent'

import { ApiError } from '@/api'

describe('交给芝士的文件', () => {
  afterEach(() => {
    resetAgent()
    upload.mockReset()
    keep.mockReset()
  })

  it('传完的那一行带上它在资料库里的名字，不再是上传中', async () => {
    keep.mockResolvedValue({ path: '实验3-缓存实验.pdf', bytes: 1 })

    await attachToAgent([new File(['x'], '实验3-缓存实验.pdf', { type: 'application/pdf' })])

    expect(keep.mock.calls[0][0]).toEqual({ team: 9 })
    expect(agentFiles.value).toEqual([
      { id: null, path: '实验3-缓存实验.pdf', name: '实验3-缓存实验.pdf', mime: 'application/pdf', uploading: false },
    ])
  })

  it('题目页上交的是题目的材料：存成附件', async () => {
    const scope = effectScope()
    scope.run(() => useAgentContext(() => ({ attachAsMaterials: true })))
    upload.mockResolvedValue({ id: 7 })

    await attachToAgent([new File(['x'], '实验说明.pdf', { type: 'application/pdf' })])

    scope.stop()
    expect(keep).not.toHaveBeenCalled()
    expect(agentFiles.value).toEqual([
      { id: 7, path: null, name: '实验说明.pdf', mime: 'application/pdf', uploading: false },
    ])
  })

  it('传不上去的那一行拿掉', async () => {
    keep.mockRejectedValue(new Error('offline'))

    await attachToAgent([new File(['x'], 'a.pdf', { type: 'application/pdf' })])

    expect(agentFiles.value).toEqual([])
  })

  it('放不进资料库时说服务端给的原因', async () => {
    keep.mockRejectedValue(new ApiError(422, '资料库已满（上限 1024 MB），删掉一些文件后再试'))

    await attachToAgent([new File(['x'], 'a.pdf', { type: 'application/pdf' })])

    expect(toast.error).toHaveBeenCalledWith('资料库已满（上限 1024 MB），删掉一些文件后再试')
  })

  it('点掉一个文件', async () => {
    keep.mockResolvedValueOnce({ path: 'a.pdf' }).mockResolvedValueOnce({ path: 'b.pdf' })
    await attachToAgent([new File(['x'], 'a.pdf'), new File(['y'], 'b.pdf')])

    removeAgentFile(0)

    expect(agentFiles.value.map((f) => f.path)).toEqual(['b.pdf'])
  })
})

describe('芝士起草、人确认的事', () => {
  afterEach(() => {
    resetAgent()
    post.mockReset()
    openTask.mockReset()
    settle.mockReset()
  })

  const drafted = (p: Partial<AssistantProposal>): AssistantProposal => ({
    id: 5,
    kind: 'message',
    state: 'pending',
    request: 'req-5',
    project: 'p-1',
    ...p,
  })

  it('a message is posted as the person with the request it was drafted with, then marked sent', async () => {
    agentThread.proposals.value = [drafted({ topic: 'room-1', topicName: '综合', text: '原稿' })]
    post.mockResolvedValue({ id: 'block-1' })
    settle.mockResolvedValue({})

    await sendProposal(5, { text: '改过的' })

    expect(post).toHaveBeenCalledWith('room-1', { content: '改过的', request_id: 'req-5' })
    expect(settle).toHaveBeenCalledWith(5, 'sent', { topic: 'room-1', message: 'block-1' })
    expect(agentThread.proposals.value[0].state).toBe('sent')
  })

  it('a task is opened in its channel and its brief said in it, once even if sending is tried again', async () => {
    agentThread.proposals.value = [drafted({ kind: 'task', channel: 'room-1', title: '排查', brief: '复现' })]
    openTask.mockResolvedValue({ id: 'task-1' })
    post.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ id: 'block-2' })
    settle.mockResolvedValue({})

    await sendProposal(5, { title: '排查超时', text: '复现并修好' })
    expect(agentThread.proposals.value[0].state).toBe('pending')
    await sendProposal(5, { title: '排查超时', text: '复现并修好' })

    expect(openTask).toHaveBeenCalledTimes(1)
    expect(openTask).toHaveBeenCalledWith('room-1', '排查超时')
    expect(post).toHaveBeenLastCalledWith('task-1', { content: '复现并修好', request_id: 'req-5' })
    expect(settle).toHaveBeenCalledWith(5, 'sent', { task: 'task-1', topic: 'task-1', message: 'block-2' })
  })
})

describe('这一句带上什么', () => {
  afterEach(() => {
    resetAgent()
    vi.unstubAllGlobals()
  })

  /** `hold`：开新话题的请求停在这里，等它放行（量这一阵里换了页）。 */
  function serveAsk(hold?: Promise<void>) {
    const bodies: Record<string, unknown>[] = []
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      const json = (data: unknown, status = 200) =>
        new Response(JSON.stringify({ code: 200, message: 'ok', data }), {
          status,
          headers: { 'content-type': 'application/json' },
        })
      if (url.endsWith('/assistant/conversations') && (init?.method ?? 'GET') === 'POST') {
        await hold
        return json({ id: 'c1', title: '', lastActiveAt: '2026-10-24T08:00:00Z' }, 201)
      }
      if (url.endsWith('/assistant/conversations')) return json({ conversations: [] })
      if (url.endsWith('/ask')) {
        bodies.push(JSON.parse(String(init?.body)))
        return new Response('event: done\ndata: {}\n\n', { headers: { 'content-type': 'text/event-stream' } })
      }
      return new Response('not found', { status: 404 })
    })
    return bodies
  }

  it('the page taken off, a selection and a pick go with the question; the selection does not go with the next', async () => {
    const bodies = serveAsk()
    const scope = effectScope()
    scope.run(() => useAgentContext(() => ({ page: { kind: 'topic', id: 'r1' }, label: '频道「后端」' })))
    await nextTick()
    agentPageOff.value = true
    selectForAgent('超时设成 600 秒')
    pickForAgent({ id: 'd1', kind: 'doc', title: '搜索接入方案', where: '知是' })

    await askAgent('比较 <#d1> 和这句')

    expect(bodies[0]).toMatchObject({ question: '比较 <#d1> 和这句', no_page: true, selection: '超时设成 600 秒' })
    expect(bodies[0]).not.toHaveProperty('page')
    expect(agentThread.messages.value[0]).toMatchObject({
      selection: '超时设成 600 秒',
      cited: [{ id: 'd1', kind: 'doc', title: '搜索接入方案' }],
    })
    expect(agentThread.messages.value[0]).not.toHaveProperty('askedOn')
    expect(agentSelection.value).toBeNull()

    agentPageOff.value = false
    await askAgent('再问一句')

    expect(bodies[1]).toMatchObject({ page: { kind: 'topic', id: 'r1' } })
    expect(bodies[1]).not.toHaveProperty('selection')
    expect(agentThread.messages.value.at(-1)).toMatchObject({ askedOn: '频道「后端」' })
    scope.stop()
  })

  it('taken off on one page, the page is read again on the next', async () => {
    const scope = effectScope()
    const page = ref('r1')
    scope.run(() => useAgentContext(() => ({ page: { kind: 'topic', id: page.value } })))
    await nextTick()
    agentPageOff.value = true

    page.value = 'r2'
    await nextTick()

    expect(agentPageOff.value).toBe(false)
    scope.stop()
  })

  it('page taken off when asking: still off if the person moves to another page before it goes out', async () => {
    let release = () => {}
    const bodies = serveAsk(new Promise((resolve) => (release = resolve)))
    const scope = effectScope()
    const page = ref<{ kind: 'topic' | 'doc'; id: string }>({ kind: 'topic', id: 'room-a' })
    scope.run(() => useAgentContext(() => ({ page: page.value })))
    await nextTick()
    agentPageOff.value = true

    const asking = askAgent('不要带页面')
    page.value = { kind: 'doc', id: 'private-doc-b' }
    await nextTick()
    release()
    await asking

    expect(bodies[0]).toMatchObject({ question: '不要带页面', no_page: true })
    expect(bodies[0]).not.toHaveProperty('page')
    scope.stop()
  })

  it('the question goes with the page it was asked on, not the one open when it goes out', async () => {
    let release = () => {}
    const bodies = serveAsk(new Promise((resolve) => (release = resolve)))
    const asked = effectScope()
    asked.run(() => useAgentContext(() => ({ page: { kind: 'topic', id: 'room-a' } })))
    await nextTick()

    const asking = askAgent('这个频道在说什么')
    asked.stop()
    const next = effectScope()
    next.run(() => useAgentContext(() => ({ page: { kind: 'doc', id: 'private-doc-b' } })))
    await nextTick()
    release()
    await asking

    expect(bodies[0]).toMatchObject({ page: { kind: 'topic', id: 'room-a' } })
    next.stop()
  })

  it('asked where the page declares nothing yet: a page it declares before the question goes out is not attached', async () => {
    let release = () => {}
    const bodies = serveAsk(new Promise((resolve) => (release = resolve)))
    const scope = effectScope()
    const page = ref<{ kind: 'project'; id: string } | null>(null)
    scope.run(() => useAgentContext(() => ({ page: page.value, label: '项目' })))
    await nextTick()

    const asking = askAgent('这里是什么')
    page.value = { kind: 'project', id: 'p-later' }
    await nextTick()
    release()
    await asking

    expect(bodies[0]).toMatchObject({ no_page: true })
    expect(bodies[0]).not.toHaveProperty('page')
    scope.stop()
  })

  it('on a page 芝士 does not read, the question says it has no page', async () => {
    const bodies = serveAsk()

    await askAgent('今天几号？')

    expect(bodies[0]).toMatchObject({ no_page: true })
  })
})
