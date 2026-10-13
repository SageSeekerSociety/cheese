// 任务页上的那件任务，以最后落下的改动为准：一次在改动之前发出、之后才回来的读，
// 带回来的是改之前的样子，不能把刚做完的事盖回去。
import type { RoomTask } from '@/cx_types'

import { beforeEach, expect, it, vi } from 'vitest'

vi.mock('@/me', () => ({ myHandle: () => 'alice' }))
vi.mock('@/api/tasks', async () => ({
  ...(await vi.importActual<typeof import('@/api/tasks')>('@/api/tasks')),
  getTask: vi.fn(),
  closeTask: vi.fn(),
}))

import { useTaskPage } from './useTaskPage'

import { closeTask, getTask } from '@/api/tasks'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'

const task = (status: string): RoomTask => ({ id: 'k1', status, owner_handle: 'alice' }) as unknown as RoomTask
// 改动的回答写进缓存、晚回来的读落地，都在当前这一轮之后：等它们都落定再看。
const settle = () => new Promise((resolve) => setTimeout(resolve, 0))

beforeEach(() => {
  vi.mocked(getTask).mockReset()
  vi.mocked(closeTask).mockReset()
})

it('关闭之前发出的读、关闭之后才回来：任务仍是已关闭', async () => {
  // 打开任务页就读一次这件任务。
  vi.mocked(getTask).mockResolvedValueOnce(task('open'))
  const page = useTaskPage({ taskId: () => 'k1', people: () => [] })
  await page.load()
  expect(page.task.value?.status).toBe('open')

  let answer!: (t: RoomTask) => void
  vi.mocked(getTask).mockReturnValueOnce(new Promise((res) => (answer = res)))
  const reread = page.load(true)
  vi.mocked(closeTask).mockResolvedValueOnce(task('closed'))
  await page.close('做完了')
  answer(task('open'))
  await reread
  await settle()

  expect(page.task.value?.status).toBe('closed')
})

it('改动之后才发出的读照常算数', async () => {
  // 打开任务页就读一次这件任务。
  vi.mocked(getTask).mockResolvedValueOnce(task('open'))
  const page = useTaskPage({ taskId: () => 'k1', people: () => [] })
  await page.load()
  expect(page.task.value?.status).toBe('open')
  vi.mocked(closeTask).mockResolvedValueOnce(task('closed'))
  await page.close('做完了')
  await settle()
  expect(page.task.value?.status).toBe('closed')

  vi.mocked(getTask).mockResolvedValueOnce(task('open'))
  await page.load(true)
  await settle()

  expect(page.task.value?.status).toBe('open')
})

it('第一次读还没回来就关闭（页面先照着清单里那一行画的）：关闭留在屏幕上，晚回来的读不撤销它', async () => {
  queryClient.setQueryData(keys.projectOpenTasks('p1'), { data: [task('open')], total: 1 })
  let answer!: (t: RoomTask) => void
  vi.mocked(getTask).mockReturnValueOnce(new Promise((res) => (answer = res)))
  const page = useTaskPage({ taskId: () => 'k1', people: () => [] })
  await settle()
  expect(page.task.value?.status).toBe('open')

  vi.mocked(closeTask).mockResolvedValueOnce(task('closed'))
  expect(await page.close('做完了')).toBe(true)
  expect(page.task.value?.status).toBe('closed')
  answer(task('open'))
  await settle()

  expect(page.task.value?.status).toBe('closed')
})
