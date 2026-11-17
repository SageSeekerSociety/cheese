// 上报是给人看的：进来的必须是有人能修的东西，落点必须是它真正在的那个项目。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// 地址按真实形态写：项目段是短名，频道段是项目里的编号（lib/addresses.ts）。
const ROOM = '/projects/helper/channels/7'
const TASK = '/projects/helper/tasks/318'
const OTHER_ROOM = '/projects/other/channels/9'

const PROJECT = 'de808b13-ffd2-4b8a-9d1d-fba7babe389f'
const TOPIC = '6825f21d-8e95-42fe-91ed-e88f78a484a2'
const TASK_ID = 'c5696812-f2fa-4d0a-8ed2-48b66d16a5d3'
const OTHER_PROJECT = '39c0ad4b-68e0-4529-8f4d-b31a0e336524'
const OTHER_TOPIC = '0a170d51-7d1b-4b9c-8a55-2ee5c2c4a111'

type Book = typeof import('./lib/addresses')

let sent: ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.resetModules()
  vi.useFakeTimers()
  sent = vi.fn().mockResolvedValue({ ok: true })
  vi.stubGlobal('fetch', sent)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

/**
 * 打开一页。地址表（短名/编号 → UUID）和上报器必须取同一份模块实例，所以一起动态
 * import。地址表由守卫在进项目框前填好，这里直接填。
 */
async function open(pathname: string) {
  vi.stubGlobal('location', { pathname, search: '' })
  const book = await import('./lib/addresses')
  const { reportError } = await import('./errorReporter')
  return { book, reportError }
}

function channel(book: Book, projectId: string, slug: string, topicId: string, number: number) {
  book.rememberProject(projectId, slug)
  book.rememberThing({
    project_id: projectId,
    slug,
    kind: 'channels',
    id: topicId,
    room_id: topicId,
    number,
  })
}

function task(book: Book, projectId: string, slug: string, id: string, room: string, number: number) {
  book.rememberProject(projectId, slug)
  book.rememberThing({ project_id: projectId, slug, kind: 'tasks', id, room_id: room, number })
}

type SentBody = { project_id?: string; topic_id?: string; errors: { message: string }[] }

function bodyOf(call: number): SentBody {
  return JSON.parse(String((sent.mock.calls[call][1] as RequestInit).body)) as SentBody
}

describe('前端报错上报', () => {
  it('短名项目和频道编号都换算成 UUID 再上报', async () => {
    const { book, reportError } = await open(ROOM)
    channel(book, PROJECT, 'helper', TOPIC, 7)

    reportError("Cannot read properties of undefined (reading 'id')", 'at TopicView.vue:12')
    await vi.advanceTimersByTimeAsync(20_000)

    expect(sent).toHaveBeenCalledTimes(1)
    const body = bodyOf(0)
    expect(body.project_id).toBe(PROJECT)
    expect(body.topic_id).toBe(TOPIC)
    expect(body.errors[0].message).toContain('Cannot read properties')
  })

  // 首页和 /teams 这些页面不在任何项目里，而「新建空间」「创建团队」就在那儿。从前
  // 这类失败因为归不进项目而整条丢掉，平台对最难查的两个入口一无所知。
  it('项目外面（没有项目段）照样上报，只是不带归属', async () => {
    const { reportError } = await open('/home')

    reportError('boom')
    await vi.advanceTimersByTimeAsync(20_000)

    expect(sent).toHaveBeenCalledTimes(1)
    const body = bodyOf(0)
    expect(body.project_id).toBeUndefined()
    expect(body.topic_id).toBeUndefined()
    expect(body.errors[0].message).toBe('boom')
  })

  // 不在项目里的那几条和项目里的那几条不是一批：一条运行记录只写一个归属，混在一批
  // 里出去会把这批整个记到某个项目名下。
  it('项目里和项目外的一起等着时，分两批发', async () => {
    const { book, reportError } = await open(ROOM)
    channel(book, PROJECT, 'helper', TOPIC, 7)
    reportError('inside', 'at TopicView.vue:12')

    vi.stubGlobal('location', { pathname: '/home', search: '' })
    reportError('outside')
    await vi.advanceTimersByTimeAsync(20_000)

    expect(sent).toHaveBeenCalledTimes(2)
    const bodies = sent.mock.calls.map((_, i) => bodyOf(i))
    const withProject = bodies.find((b) => b.project_id !== undefined)
    const withoutProject = bodies.find((b) => b.project_id === undefined)
    expect(withProject?.project_id).toBe(PROJECT)
    expect(withProject?.errors[0].message).toBe('inside')
    expect(withoutProject?.errors[0].message).toBe('outside')
  })

  it('短名还没换算出 UUID 时不上报，不拿短名冒充 UUID', async () => {
    const { reportError } = await open(ROOM)

    reportError('boom')
    await vi.advanceTimersByTimeAsync(20_000)

    expect(sent).not.toHaveBeenCalled()
  })

  it('ResizeObserver 通告即使在项目里也不上报', async () => {
    const { book, reportError } = await open(ROOM)
    channel(book, PROJECT, 'helper', TOPIC, 7)

    reportError('ResizeObserver loop completed with undelivered notifications.')
    await vi.advanceTimersByTimeAsync(20_000)

    expect(sent).not.toHaveBeenCalled()
  })

  it('归属在报错那一刻就定下来，之后切项目也不改', async () => {
    const { book, reportError } = await open(ROOM)
    channel(book, PROJECT, 'helper', TOPIC, 7)
    reportError('boom', 'at TopicView.vue:12')

    // 还没到发送时刻，用户切到了另一个项目。
    vi.stubGlobal('location', { pathname: OTHER_ROOM, search: '' })
    channel(book, OTHER_PROJECT, 'other', OTHER_TOPIC, 9)
    await vi.advanceTimersByTimeAsync(20_000)

    expect(sent).toHaveBeenCalledTimes(1)
    expect(bodyOf(0).project_id).toBe(PROJECT)
    expect(bodyOf(0).topic_id).toBe(TOPIC)
  })

  it('一批里两个项目各一条，分开发、各带自己的归属', async () => {
    const { book, reportError } = await open(ROOM)
    channel(book, PROJECT, 'helper', TOPIC, 7)
    reportError('first', 'at TopicView.vue:12')

    vi.stubGlobal('location', { pathname: OTHER_ROOM, search: '' })
    channel(book, OTHER_PROJECT, 'other', OTHER_TOPIC, 9)
    reportError('second', 'at TopicView.vue:13')
    await vi.advanceTimersByTimeAsync(20_000)

    expect(sent).toHaveBeenCalledTimes(2)
    const projects = [bodyOf(0).project_id, bodyOf(1).project_id]
    expect(projects.sort()).toEqual([PROJECT, OTHER_PROJECT].sort())
  })

  it('任务页上报到任务所在的频道', async () => {
    const { book, reportError } = await open(TASK)
    task(book, PROJECT, 'helper', TASK_ID, TOPIC, 318)

    reportError('boom', 'at TaskView.vue:3')
    await vi.advanceTimersByTimeAsync(20_000)

    expect(sent).toHaveBeenCalledTimes(1)
    expect(bodyOf(0).project_id).toBe(PROJECT)
    expect(bodyOf(0).topic_id).toBe(TOPIC)
  })
})
