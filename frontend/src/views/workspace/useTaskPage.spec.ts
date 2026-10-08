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

const task = (status: string): RoomTask => ({ id: 'k1', status, owner_handle: 'alice' }) as unknown as RoomTask

beforeEach(() => {
  vi.mocked(getTask).mockReset()
  vi.mocked(closeTask).mockReset()
})

it('关闭之前发出的读、关闭之后才回来：任务仍是已关闭', async () => {
  const page = useTaskPage({ taskId: () => 'k1', people: () => [] })
  vi.mocked(getTask).mockResolvedValueOnce(task('open'))
  await page.load()

  let answer!: (t: RoomTask) => void
  vi.mocked(getTask).mockReturnValueOnce(new Promise((res) => (answer = res)))
  const reread = page.load(true)
  vi.mocked(closeTask).mockResolvedValueOnce(task('closed'))
  await page.close('做完了')
  answer(task('open'))
  await reread

  expect(page.task.value?.status).toBe('closed')
})

it('改动之后才发出的读照常算数', async () => {
  const page = useTaskPage({ taskId: () => 'k1', people: () => [] })
  vi.mocked(getTask).mockResolvedValueOnce(task('open'))
  await page.load()
  vi.mocked(closeTask).mockResolvedValueOnce(task('closed'))
  await page.close('做完了')

  vi.mocked(getTask).mockResolvedValueOnce(task('open'))
  await page.load(true)

  expect(page.task.value?.status).toBe('open')
})
