import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useTaskParticipation } from './useTaskParticipation'

const mocks = vi.hoisted(() => ({
  join: vi.fn(),
  push: vi.fn(),
  emit: vi.fn(),
  handlers: {} as Record<string, (value: number) => void>,
}))
vi.mock('@/network/api/tasks', () => ({ TasksApi: { join: mocks.join } }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: mocks.push }) }))
vi.mock('@/plugins/dialog', () => ({ useDialog: () => ({}) }))
vi.mock('@/services/account', () => ({ default: { user: { id: 11 } } }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('../events', () => ({
  useEvents: () => ({
    on: (name: string, handler: (value: number) => void) => {
      mocks.handlers[name] = handler
    },
    emit: mocks.emit,
  }),
}))

function setup(submitterType: 'TEAM' | 'USER') {
  return useTaskParticipation({
    taskData: ref({ id: 7, submitterType }),
    loadTaskData: vi.fn(),
  } as unknown as Parameters<typeof useTaskParticipation>[0])
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.join.mockResolvedValue({ data: { project: { id: 'shared-project' } } })
})

describe('registration enters its workspace', () => {
  it('uses the selected team and enters the project returned by registration', async () => {
    const flow = setup('TEAM')
    mocks.handlers['select-team'](42)
    await flow.handleVerifyInfoSubmit({ email: 'student@example.test' })
    expect(mocks.join).toHaveBeenCalledWith(7, expect.objectContaining({ email: 'student@example.test' }), 42)
    expect(mocks.push).toHaveBeenCalledWith('/projects/shared-project')
  })

  it('requires a team instead of silently registering the person', async () => {
    const flow = setup('TEAM')
    await flow.handleVerifyInfoSubmit({})
    expect(mocks.join).not.toHaveBeenCalled()
    expect(mocks.emit).toHaveBeenCalledWith('team-selection-dialog-open', true)
  })

  it('opens an individual registration workspace', async () => {
    await setup('USER').handleVerifyInfoSubmit({})
    expect(mocks.join).toHaveBeenCalledWith(7, expect.any(Object))
    expect(mocks.push).toHaveBeenCalledWith('/projects/shared-project')
  })

  it('stays on the task when registration fails', async () => {
    mocks.join.mockRejectedValueOnce(new Error('Registration closed'))
    await setup('USER').handleVerifyInfoSubmit({})
    expect(mocks.push).not.toHaveBeenCalled()
  })
})
