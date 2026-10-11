import type { RoomTask } from '@/cx_types'

import { defineComponent, h, nextTick, ref } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

vi.mock('@/me', () => ({ myHandle: () => 'alice' }))
vi.mock('@/api', async (original) => ({
  ...(await original<typeof import('@/api')>()),
  setTopicComputeChoice: vi.fn(),
}))
vi.mock('@/api/tasks', async (original) => ({
  ...(await original<typeof import('@/api/tasks')>()),
  getTask: vi.fn(),
}))

import { useTaskPage } from './useTaskPage'

import { setTopicComputeChoice } from '@/api'
import { getTask } from '@/api/tasks'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'

const choice = { name: null, profile: 'cloud' as const, device_id: null }
const task = (id: string) => ({ id, status: 'open', owner_handle: 'alice' }) as RoomTask
const settle = async () => {
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 0))
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(getTask).mockImplementation(async (id) => task(id))
  queryClient.setQueryData(keys.roomTask('machine-a'), task('machine-a'))
  queryClient.setQueryData(keys.roomTask('machine-b'), task('machine-b'))
})
afterEach(cleanup)

it.each(['error', 'proposal'])('task reset clears the previous computer %s', async (outcome) => {
  if (outcome === 'error') vi.mocked(setTopicComputeChoice).mockRejectedValueOnce(new Error('A refused'))
  else
    vi.mocked(setTopicComputeChoice).mockResolvedValueOnce({
      choice,
      proposal: { content: 'A proposal', approver: 'alice', tier: 'byo' },
    })
  const id = ref('machine-a')
  let page!: ReturnType<typeof useTaskPage>
  render(
    defineComponent({
      setup() {
        page = useTaskPage({ taskId: () => id.value, people: () => [] })
        return () => h('div')
      },
    })
  )
  await settle()
  await page.selectMachine(choice)
  expect(outcome === 'error' ? page.machinePickError.value : page.machineProposal.value).not.toBe('')
  id.value = 'machine-b'
  page.reset()
  await settle()
  expect(page.task.value?.id).toBe('machine-b')
  expect(page.machinePickError.value).toBe('')
  expect(page.machineProposal.value).toBe('')
  expect(page.machineSaving.value).toBe(false)
})

it('task reset makes an in-flight computer choice unable to affect the next task', async () => {
  let resolve!: (result: Awaited<ReturnType<typeof setTopicComputeChoice>>) => void
  vi.mocked(setTopicComputeChoice).mockReturnValueOnce(
    new Promise((yes) => {
      resolve = yes
    })
  )
  const id = ref('machine-a')
  let page!: ReturnType<typeof useTaskPage>
  render(
    defineComponent({
      setup() {
        page = useTaskPage({ taskId: () => id.value, people: () => [] })
        return () => h('div')
      },
    })
  )
  await settle()
  const pending = page.selectMachine(choice)
  expect(page.machineSaving.value).toBe(true)
  id.value = 'machine-b'
  page.reset()
  await settle()
  expect(page.machineSaving.value).toBe(false)
  resolve({ choice, proposal: { content: 'A proposal', approver: 'alice', tier: 'byo' } })
  await pending
  expect(page.machineProposal.value).toBe('')
  expect(page.machinePickError.value).toBe('')
  expect(page.machineSavedVersion.value).toBe(0)
})
