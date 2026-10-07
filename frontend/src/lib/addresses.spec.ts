// 项目框里的地址是给人看的：`/projects/<短名>/tasks/318`。规则，写在代码之前：
//
// - 拿 UUID 拼的地址、改名前的旧短名，落地时都换成现在的短名和编号；
// - 页面拿到的仍是 UUID，任务页还拿到任务所在的频道；
// - 资料库里打开文档的旧地址 `library?doc=` 落到这份文档自己的地址；
// - 查不到的（没有这个项目、看不到、还没编号）原样放行，由页面自己说找不到；
// - 复制出去的链接用已经知道的短名和编号。
//
// 用的是真的路由记录；`resolve()` 不把各页的代码拉进测试。
import type { RouteLocationNormalized, RouteLocationRaw } from 'vue-router'
import type { ThingAddress } from '@/api/addresses'

import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const PROJECT = '3f1a7c62-9d4e-4b8a-8f21-0c5d6e7a9b10'
const ROOM = '9b81c0de-1f22-4a33-9c44-5d6e7f8a9b01'
const TASK = '5c2d1e0f-1f22-4a33-9c44-5d6e7f8a9b02'
const DOC = '7e6f5a4b-1f22-4a33-9c44-5d6e7f8a9b03'
const DM = '1a2b3c4d-1f22-4a33-9c44-5d6e7f8a9b04'

const { api } = vi.hoisted(() => ({
  api: {
    resolveProject: vi.fn(),
    resolveNumber: vi.fn(),
    addressOf: vi.fn(),
  },
}))
vi.mock('@/api/addresses', () => api)

import { addressProps, canonicalAddress, shortRoute } from './addresses'

import { workspaceRoutes } from '@/router/workspaceRoutes'

const THINGS: ThingAddress[] = [
  { project_id: PROJECT, slug: 'helper', kind: 'channels', id: ROOM, room_id: ROOM, number: 7 },
  { project_id: PROJECT, slug: 'helper', kind: 'tasks', id: TASK, room_id: ROOM, number: 318 },
  { project_id: PROJECT, slug: 'helper', kind: 'docs', id: DOC, room_id: null, number: 42 },
  { project_id: PROJECT, slug: 'helper', kind: 'channels', id: DM, room_id: DM, number: null },
]

function notFound(): Promise<never> {
  return Promise.reject(new Error('not found'))
}

beforeEach(() => {
  api.resolveProject.mockImplementation((ref: string) =>
    [PROJECT, 'helper', 'old-name'].includes(ref) ? Promise.resolve({ id: PROJECT, slug: 'helper' }) : notFound()
  )
  api.addressOf.mockImplementation((kind: string, id: string) => {
    const found = THINGS.find((a) => a.kind === kind && a.id === id)
    return found ? Promise.resolve(found) : notFound()
  })
  api.resolveNumber.mockImplementation((_: string, kind: string, n: string) => {
    const found = THINGS.find((a) => a.kind === kind && String(a.number) === n)
    return found ? Promise.resolve(found) : notFound()
  })
})

const router = createRouter({ history: createMemoryHistory(), routes: [workspaceRoutes] })

function at(path: string): RouteLocationNormalized {
  return router.resolve(path) as unknown as RouteLocationNormalized
}

/** 守卫把这个地址落到哪：原样放行时就是它自己。 */
async function landsOn(path: string): Promise<string> {
  const next = await canonicalAddress(at(path))
  return next === true ? path : router.resolve(next as RouteLocationRaw).fullPath
}

describe('落地时换成短地址', () => {
  it.each([
    [`/projects/${PROJECT}`, '/projects/helper'],
    ['/projects/old-name/settings/agents', '/projects/helper/settings/agents'],
    [`/projects/${PROJECT}/channels/${ROOM}`, '/projects/helper/channels/7'],
    [`/projects/${PROJECT}/channels/${ROOM}/threads/x1`, '/projects/helper/channels/7/threads/x1'],
    [`/projects/${PROJECT}/tasks/${TASK}`, '/projects/helper/tasks/318'],
    [`/projects/${PROJECT}/docs/${DOC}`, '/projects/helper/docs/42'],
    [`/projects/${PROJECT}/library?doc=${DOC}`, '/projects/helper/docs/42'],
    ['/projects/helper/tasks/318?tab=overview', '/projects/helper/tasks/318?tab=overview'],
  ])('%s → %s', async (from, to) => {
    expect(await landsOn(from)).toBe(to)
  })

  it('没编号的（私聊）留着 UUID，项目照样换成短名', async () => {
    expect(await landsOn(`/projects/${PROJECT}/channels/${DM}`)).toBe(`/projects/helper/channels/${DM}`)
  })

  it('查不到的原样放行', async () => {
    expect(await landsOn('/projects/nobody-knows/tasks/1')).toBe('/projects/nobody-knows/tasks/1')
    expect(await landsOn('/projects/helper/tasks/999')).toBe('/projects/helper/tasks/999')
  })
})

describe('页面拿到的是 UUID', () => {
  it('任务页拿到任务和它所在的频道', async () => {
    const path = '/projects/helper/tasks/318'
    await canonicalAddress(at(path))
    expect(addressProps(at(path))).toMatchObject({ projectId: PROJECT, taskId: TASK, topicId: ROOM })
  })

  it('频道和文档也一样', async () => {
    for (const path of ['/projects/helper/channels/7', '/projects/helper/docs/42']) await canonicalAddress(at(path))
    expect(addressProps(at('/projects/helper/channels/7'))).toMatchObject({ projectId: PROJECT, topicId: ROOM })
    expect(addressProps(at('/projects/helper/docs/42'))).toMatchObject({ projectId: PROJECT, docId: DOC })
  })
})

describe('复制出去的链接', () => {
  it('用已经知道的短名和编号', async () => {
    await canonicalAddress(at(`/projects/${PROJECT}/tasks/${TASK}`))
    const to = shortRoute({ name: 'workspace-task', params: { projectId: PROJECT, topicId: ROOM, taskId: TASK } })
    expect(router.resolve(to).fullPath).toBe('/projects/helper/tasks/318')
  })
})
