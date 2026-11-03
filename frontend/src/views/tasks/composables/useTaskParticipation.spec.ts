import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useTaskParticipation } from './useTaskParticipation'

import { ApiError } from '@/api/http'
import i18n from '@/i18n'

const mocks = vi.hoisted(() => ({
  join: vi.fn(),
  push: vi.fn(),
  emit: vi.fn(),
  handlers: {} as Record<string, (value: number) => void>,
}))
vi.mock('@/api/taskBoard', () => ({ TaskBoardApi: { join: mocks.join } }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: mocks.push }) }))
vi.mock('@/plugins/dialog', () => ({ useDialog: () => ({}) }))
vi.mock('@/services/account', () => ({ default: { user: { id: 11 } } }))
const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('vuetify-sonner', () => ({ toast: toasts }))
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
  mocks.join.mockResolvedValue({ project: { id: 'shared-project' } })
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

  it.each([['USER'], ['TEAM']] as const)(
    'an application to a %s task that waits for approval stays on the task and says it was sent',
    async (submitterType) => {
      // 申请制: the project opens on approval, so the claim answers with none.
      mocks.join.mockResolvedValueOnce({ participant: { id: 3 }, project: null })
      const flow = setup(submitterType)
      if (submitterType === 'TEAM') mocks.handlers['select-team'](42)
      await flow.handleVerifyInfoSubmit({})
      expect(mocks.push).not.toHaveBeenCalled()
      expect(toasts.success).toHaveBeenCalledWith(i18n.global.t('tasks.participation.applied'))
    }
  )

  it('stays on the task when registration fails', async () => {
    mocks.join.mockRejectedValueOnce(new Error('Registration closed'))
    await setup('USER').handleVerifyInfoSubmit({})
    expect(mocks.push).not.toHaveBeenCalled()
  })

  it.each([['USER'], ['TEAM']] as const)(
    'says what the server said when the %s claim is being opened by another request',
    async (submitterType) => {
      // The server's sentence for `claimInProgress`, as the API client hands it on.
      const said = 'The project for this claim is being created. Try again in a moment'
      mocks.join.mockRejectedValueOnce(new ApiError(409, said, 'ClaimInProgressError', undefined, true))
      const flow = setup(submitterType)
      if (submitterType === 'TEAM') mocks.handlers['select-team'](42)
      await flow.handleVerifyInfoSubmit({})
      expect(toasts.error).toHaveBeenCalledWith(said)
      expect(mocks.push).not.toHaveBeenCalled()
    }
  )
})
