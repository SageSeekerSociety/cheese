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
const UNKNOWN_PROJECT = '2b3c4d5e-6f70-4a12-8b34-5c6d7e8f9a10'
const UNKNOWN_ROOM = '3c4d5e6f-7081-4b23-9c45-6d7e8f9a10b1'

const { api } = vi.hoisted(() => ({
  api: {
    resolveProject: vi.fn(),
    resolveNumber: vi.fn(),
    addressOf: vi.fn(),
  },
}))
vi.mock('@/api/addresses', () => api)

import { addressProps, canonicalAddress, shortRoute } from './addresses'
import { takeEarlyProjects } from './earlyProjects'

import { canonicalNavigation } from '@/router/addressHistory'
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

function deferred<T>() {
  let settle!: (v: T) => void
  const promise = new Promise<T>((res) => {
    settle = res
  })
  return { promise, settle }
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

describe('认得的项目不再问一次', () => {
  it('地址里写的是 UUID 也一样', async () => {
    await landsOn(`/projects/${PROJECT}/channels/${ROOM}`)
    api.resolveProject.mockClear()

    expect(await landsOn(`/projects/${PROJECT}/tasks/${TASK}`)).toBe('/projects/helper/tasks/318')
    expect(api.resolveProject).not.toHaveBeenCalled()
  })

  it('启动脚本已经在问项目清单时，直接用那份结果记下短名，不再打一次 /addresses/projects', async () => {
    const EARLY_PROJECT = '8f7e6d5c-4b3a-4123-9876-543210fedcba'
    localStorage.setItem('accessToken', 'tok-early')
    window.__cxEarlyProjects = Promise.resolve({
      token: 'tok-early',
      body: {
        code: 200,
        data: {
          data: [{ id: EARLY_PROJECT, slug: 'early-slug', name: 'Early', created_at: '2026-10-01T00:00:00Z' }],
          total: 1,
        },
      },
    })
    api.resolveProject.mockClear()

    try {
      expect(await landsOn(`/projects/${EARLY_PROJECT}`)).toBe('/projects/early-slug')
      expect(api.resolveProject).not.toHaveBeenCalled()
      // 并且不提前消费 __cxEarlyProjects，projectsQuery 第一次读时仍能接走它。
      const consumed = await takeEarlyProjects()
      expect(consumed?.data.map((p) => p.id)).toEqual([EARLY_PROJECT])
    } finally {
      delete window.__cxEarlyProjects
      localStorage.removeItem('accessToken')
    }
  })
})

describe('地址里那个 UUID 和项目那一问并排走', () => {
  // 这一趟本来排在项目那一问后面：串着问，冷开一个任务页要多等一个来回。
  const UNSEEN = '6d5c4b3a-2f1e-4d3c-9b8a-7f6e5d4c3b2a'

  it('项目那边还没回来，就已经去问地址里那个 UUID 是几号了', () => {
    const project = deferred<{ id: string; slug: string }>()
    api.resolveProject.mockImplementationOnce(() => project.promise)

    // 不 await：这里要看的正是「两边都已经出发」这一刻。
    void canonicalAddress(at(`/projects/old-name/tasks/${UNSEEN}`))

    expect(api.resolveProject).toHaveBeenCalledWith('old-name')
    expect(api.addressOf).toHaveBeenCalledWith('tasks', UNSEEN)
  })

  it('并排走不影响落到哪个地址', async () => {
    expect(await landsOn(`/projects/${PROJECT}/tasks/${TASK}`)).toBe('/projects/helper/tasks/318')
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

describe('改写地址不吃掉身后那一页', () => {
  // 真实的页面表会在跳转时拉各页的代码；这里只留守卫认的那几样：项目框、参数名、路由名。
  const Page = { render: () => null }
  function guarded() {
    const r = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: Page },
        {
          path: '/projects/:projectId',
          meta: { projectFrame: true },
          component: Page,
          children: [
            { path: '', name: 'workspace-project', component: Page },
            { path: 'channels/:topicId', name: 'workspace-room', component: Page },
            { path: 'tasks/:taskId', name: 'workspace-task', component: Page },
          ],
        },
      ],
    })
    r.beforeEach((to) => canonicalNavigation(to, r.options.history))
    return r
  }
  function stepHistory(r: ReturnType<typeof guarded>, delta = -1): Promise<string> {
    return new Promise((done) => {
      const stop = r.afterEach((to) => {
        stop()
        done(to.fullPath)
      })
      r.go(delta)
    })
  }

  it('拿 UUID 拼的链接点过去，返回回到点之前那一页', async () => {
    const r = guarded()
    await r.push('/')
    await r.push(`/projects/${PROJECT}/channels/${ROOM}`)
    await r.push(`/projects/${PROJECT}/tasks/${TASK}`)
    expect(r.currentRoute.value.fullPath).toBe('/projects/helper/tasks/318')

    expect(await stepHistory(r)).toBe('/projects/helper/channels/7')
    expect(await stepHistory(r)).toBe('/')
  })

  it('返回经过尚未规范化的旧地址仍能继续退到之前的页面', async () => {
    const rawRoom: ThingAddress = {
      project_id: UNKNOWN_PROJECT,
      slug: 'helper',
      kind: 'channels',
      id: UNKNOWN_ROOM,
      room_id: UNKNOWN_ROOM,
      number: 7,
    }
    api.resolveProject.mockImplementation((ref: string) =>
      ref === UNKNOWN_PROJECT
        ? Promise.reject(new Error('not ready'))
        : Promise.resolve({ id: PROJECT, slug: 'helper' })
    )
    api.addressOf.mockImplementation((kind: string, id: string) =>
      kind === 'channels' && id === UNKNOWN_ROOM ? Promise.resolve(rawRoom) : notFound()
    )

    const r = guarded()
    await r.push('/')
    // 项目映射暂未到达时，这个 UUID 地址会先留在历史里。
    await r.push(`/projects/${UNKNOWN_PROJECT}/channels/${UNKNOWN_ROOM}`)
    expect(r.currentRoute.value.fullPath).toBe(`/projects/${UNKNOWN_PROJECT}/channels/${UNKNOWN_ROOM}`)

    // 映射到达后，回退到那条旧历史；改写只能替换当前格，不能再压一格。
    api.resolveProject.mockResolvedValue({ id: UNKNOWN_PROJECT, slug: 'helper' })
    await r.push('/projects/helper/tasks/318')

    expect(await stepHistory(r)).toBe('/projects/helper/channels/7')
    expect(await stepHistory(r)).toBe('/')
  })

  it('本来就是换掉当前这一格的，改写后也只换这一格', async () => {
    const r = guarded()
    await r.push('/')
    await r.push(`/projects/${PROJECT}/channels/${ROOM}`)
    await r.replace(`/projects/${PROJECT}/tasks/${TASK}`)

    expect(await stepHistory(r)).toBe('/')
  })
})
