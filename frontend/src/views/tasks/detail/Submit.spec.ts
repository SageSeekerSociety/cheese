import type { Task } from '@/types'

import { createVuetify } from 'vuetify'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  getParticipants: vi.fn(),
  createSubmission: vi.fn().mockResolvedValue({}),
  push: vi.fn(),
}))
vi.mock('@/network/api/tasks', () => ({ TasksApi: mocks }))
vi.mock('@/network/api/attachments', () => ({ AttachmentsApi: { upload: vi.fn() } }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: mocks.push }) }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import Submit from './Submit.vue'

describe('participant submission', () => {
  it('uses its own deadline without requesting the administrator roster and submits as that identity', async () => {
    const deadline = Date.now() + 86400000
    const view = render(Submit, {
      props: {
        taskData: {
          id: 12,
          space: { id: 4 },
          submittable: true,
          resubmittable: true,
          submissionSchema: [{ type: 'TEXT', prompt: '成果说明' }],
        } as Task,
        participationInfo: {
          hasParticipation: true,
          identities: [{ id: 99, type: 'TEAM', memberId: 7, canSubmit: true, approved: 'APPROVED', deadline }],
        },
      },
      global: {
        plugins: [createVuetify()],
        stubs: {
          VDialog: true,
          CountdownTimer: { props: ['deadline'], template: '<span data-testid="deadline">{{ deadline }}</span>' },
        },
      },
    })
    expect(view.getByTestId('deadline').textContent).toBe(String(deadline))
    expect(mocks.getParticipants).not.toHaveBeenCalled()
    await fireEvent.update(view.getByRole('textbox', { name: '成果说明' }), '共同完成的计划')
    await fireEvent.submit(view.container.querySelector('form')!)
    await waitFor(() => expect(mocks.createSubmission).toHaveBeenCalledWith(12, 99, [{ text: '共同完成的计划' }]))
    expect(mocks.push).toHaveBeenCalledWith({ name: 'TasksSubmissions', params: { spaceId: 4, taskId: 12 } })
    view.unmount()
  })
})
