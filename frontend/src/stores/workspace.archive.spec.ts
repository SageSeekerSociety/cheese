/** 打开一个已归档的项目：整块换成「项目已归档」；所有者取消归档之后，它像第一次打开
 *  那样回来。项目在用着的时候被归档，下一次写被拒也换成同一个状态。 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

vi.mock('@/me', () => ({ myHandle: () => 'alice' }))
vi.mock('@/api', async () => ({
  ...(await vi.importActual<typeof import('@/api')>('@/api')),
  getProject: vi.fn(),
  unarchiveProject: vi.fn(),
  createTopic: vi.fn(),
  listTopics: vi.fn().mockResolvedValue({ data: [] }),
  listProjectMembers: vi.fn().mockResolvedValue({ data: [] }),
  listProjects: vi.fn().mockResolvedValue({ data: [] }),
  getTopicNotifyLevels: vi.fn().mockResolvedValue({}),
  getTopicUnread: vi.fn().mockResolvedValue({}),
  getPrivateUnread: vi.fn().mockResolvedValue({}),
}))

import type { Project, Topic } from '@/cx_types'

import { ApiError, createTopic, getProject, listProjects, listTopics, unarchiveProject } from '@/api'
import { useWorkspaceStore } from '@/stores/workspace'

const project = (archived: boolean): Project => ({
  id: 'p',
  name: '毕业设计',
  created_at: '2026-09-01T00:00:00Z',
  owner_handle: 'alice',
  archived_at: archived ? '2026-09-28T00:00:00Z' : null,
})

const overview = (status: string) => ({ id: 'root', project_id: 'p', kind: 'root', status, title: '总览' }) as Topic

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(getProject).mockResolvedValue(project(true))
})

async function settle() {
  for (let i = 0; i < 10; i += 1) await new Promise((resolve) => setTimeout(resolve, 0))
}

async function opened(status: string) {
  vi.mocked(listTopics).mockResolvedValue({ data: [overview(status)], total: 1 })
  const store = useWorkspaceStore()
  store.openProject('p')
  await settle()
  return store
}

it('an archived project opens to the archived state, with its name', async () => {
  // 归档了的项目不在「我的项目」清单里：名字只能从项目本身读。
  vi.mocked(listProjects).mockResolvedValue({ data: [], total: 0 })
  const store = await opened('archived')
  expect(store.accessDenied).toBe('archived')
  expect(store.projectName).toBe('毕业设计')
})

it('a project in use opens normally, named from the project list', async () => {
  vi.mocked(listProjects).mockResolvedValue({ data: [project(false)], total: 1 })
  const store = await opened('active')
  expect(store.accessDenied).toBeNull()
  expect(store.projectName).toBe('毕业设计')
})

it('unarchiving it brings the project back as if opened afresh', async () => {
  const store = await opened('archived')
  vi.mocked(unarchiveProject).mockResolvedValue(project(false))
  vi.mocked(listTopics).mockResolvedValue({ data: [overview('active')], total: 1 })
  vi.mocked(listProjects).mockResolvedValue({ data: [project(false)], total: 1 })

  expect(await store.unarchiveOpenProject()).toBe(true)
  await settle()

  expect(unarchiveProject).toHaveBeenCalledWith('p')
  expect(store.accessDenied).toBeNull()
  expect(store.projects.map((p) => p.id)).toEqual(['p'])
})

it('a write refused because the project was archived meanwhile switches to that state', async () => {
  const store = await opened('active')
  vi.mocked(createTopic).mockRejectedValue(new ApiError(409, '项目已归档，取消归档后才能修改', 'ProjectArchivedError'))

  await store.create('新房间')

  expect(store.accessDenied).toBe('archived')
  expect(store.error).toBeNull()
})
