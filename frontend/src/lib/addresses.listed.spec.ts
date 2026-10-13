// 点开一件手上已经列着的任务，不等服务端答「它的编号是几」才换页。
//
// 地址守卫（`canonicalAddress`）要把任务的 UUID 换成 `/tasks/<编号>` 才落地。侧栏、频道
// 概览、全部任务、消息下面的任务卡，读回来的每一件都带着编号；没记下来的话，每点一次都
// 要先等一趟 `/addresses/of/tasks/<id>`（dev 上实测三百多毫秒），这段时间页面一动不动。
//
// 用的是真的路由记录和真的读法（`api/tasks`、`api/blocks`、频道清单那一份），只把 HTTP 换成替身。
import type { RouteLocationNormalized, RouteLocationRaw } from 'vue-router'

import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const PROJECT = '3f1a7c62-9d4e-4b8a-8f21-0c5d6e7a9b10'
const ROOM = '9b81c0de-1f22-4a33-9c44-5d6e7f8a9b01'

const { api, http } = vi.hoisted(() => ({
  api: { resolveProject: vi.fn(), resolveNumber: vi.fn(), addressOf: vi.fn() },
  http: { body: null as unknown },
}))
vi.mock('@/api/addresses', () => api)
vi.mock('@/api/http', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/http')>()),
  request: vi.fn(async () => http.body),
  readSince: vi.fn(async () => http.body),
}))
const listTopics = vi.hoisted(() => vi.fn())
vi.mock('@/api', async (importOriginal) => ({ ...(await importOriginal<typeof import('@/api')>()), listTopics }))

import { listBlocks } from '@/api/blocks'
import { getTask, listProjectTasks, listRoomTasks, pageProjectTasks } from '@/api/tasks'
import { canonicalAddress, rememberProjects } from '@/lib/addresses'
import { topicsQuery } from '@/query/project'
import { workspaceRoutes } from '@/router/workspaceRoutes'

const router = createRouter({ history: createMemoryHistory(), routes: [workspaceRoutes] })

function task(id: string, number: number) {
  return { id, project_id: PROJECT, room_id: ROOM, number, title: id, status: 'open' }
}

async function landsOn(path: string): Promise<string> {
  const next = await canonicalAddress(router.resolve(path) as unknown as RouteLocationNormalized)
  return next === true ? path : router.resolve(next as RouteLocationRaw).fullPath
}

beforeEach(() => {
  api.addressOf.mockReset().mockRejectedValue(new Error('should not be asked'))
  api.resolveProject.mockResolvedValue({ id: PROJECT, slug: 'helper' })
  // 项目清单先到：短名是那时候记下的。
  rememberProjects([{ id: PROJECT, slug: 'helper' }])
})

const uuid = (n: number) => `5c2d1e0f-1f22-4a33-9c44-${String(n).padStart(12, '0')}`

describe('列着的任务，点开就落地，不先问编号', () => {
  it.each([
    ['频道里的任务清单', 101, (t: object) => ((http.body = { data: [t] }), listRoomTasks(ROOM, { limit: 0 }))],
    ['整个项目的任务', 102, (t: object) => ((http.body = { data: [t] }), listProjectTasks(PROJECT, { open: true }))],
    [
      '「全部任务」那一页',
      103,
      (t: object) => (
        (http.body = { data: [t], has_more: false, next: null, counts: {} }),
        pageProjectTasks(PROJECT, { status: 'open', limit: 50 })
      ),
    ],
    ['单读一件', 104, (t: object) => ((http.body = t), getTask((t as { id: string }).id))],
    [
      '消息下面的任务卡',
      105,
      (t: object) => (
        (http.body = {
          data: [{ id: 'b1', tasks: [t] }],
          has_more: false,
          oldest_id: null,
          has_newer: false,
          newest_id: null,
        }),
        listBlocks(ROOM, { limit: 50 })
      ),
    ],
    [
      '侧栏频道那一行带着的',
      106,
      (t: object) => {
        listTopics.mockResolvedValue({
          data: [{ id: ROOM, project_id: PROJECT, number: 7, my_tasks: { shown: [t], open: 1 } }],
        })
        return (topicsQuery(PROJECT).queryFn as (ctx: { signal: AbortSignal }) => Promise<unknown>)({
          signal: new AbortController().signal,
        })
      },
    ],
  ])('%s', async (_where, number, read) => {
    const id = uuid(number)
    await read(task(id, number))

    expect(await landsOn(`/projects/${PROJECT}/tasks/${id}`)).toBe(`/projects/helper/tasks/${number}`)
    expect(api.addressOf).not.toHaveBeenCalled()
  })

  it('没列过的任务照旧去问', async () => {
    const id = uuid(999)
    api.addressOf.mockResolvedValue({
      project_id: PROJECT,
      slug: 'helper',
      kind: 'tasks',
      id,
      room_id: ROOM,
      number: 999,
    })

    expect(await landsOn(`/projects/${PROJECT}/tasks/${id}`)).toBe('/projects/helper/tasks/999')
    expect(api.addressOf).toHaveBeenCalledWith('tasks', id)
  })
})
