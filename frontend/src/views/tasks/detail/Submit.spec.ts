import type { Task } from '@/types'

import { createVuetify } from 'vuetify'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  getParticipants: vi.fn(),
  createSubmission: vi.fn().mockResolvedValue({}),
  listSubmissions: vi.fn().mockResolvedValue({ data: { submissions: [] } }),
  push: vi.fn(),
  limits: vi.fn(),
}))
vi.mock('@/network/api/tasks', () => ({ TasksApi: mocks }))
vi.mock('@/network/api/attachments', () => ({ AttachmentsApi: { upload: vi.fn(), limits: mocks.limits } }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: mocks.push }) }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import Submit from './Submit.vue'

import i18n, { setLocale } from '@/i18n'

beforeEach(() => {
  setLocale('zh-CN')
  mocks.limits.mockReset()
  mocks.limits.mockResolvedValue({ data: { maxFileBytes: 100 * 1024 * 1024 } })
})

function mountForm() {
  return render(Submit, {
    props: {
      taskData: {
        id: 14,
        space: { id: 4 },
        submittable: true,
        resubmittable: true,
        submissionSchema: [{ type: 'FILE', prompt: '源代码' }],
      } as Task,
      participationInfo: {
        hasParticipation: true,
        identities: [{ id: 6, type: 'USER', memberId: 3, canSubmit: true, approved: 'APPROVED', deadline: null }],
      },
    },
    global: { plugins: [createVuetify(), i18n], stubs: { VDialog: true, CountdownTimer: true } },
  })
}

describe('提交须知 on file size', () => {
  it('states the limit the upload is refused at, as the server reports it', async () => {
    mocks.limits.mockResolvedValue({ data: { maxFileBytes: 200 * 1024 * 1024 } })
    const view = mountForm()

    await waitFor(() => expect(view.container.textContent).toContain('200'))
    expect(view.container.textContent).toContain('单个文件不超过 200')
    expect(view.container.textContent).not.toContain('50MB')
    view.unmount()
  })

  it('says nothing about size when the limit cannot be read, rather than guessing one', async () => {
    mocks.limits.mockRejectedValue(new Error('offline'))
    const view = mountForm()

    await waitFor(() => expect(mocks.limits).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(view.container.textContent).toContain('提交须知')
    expect(view.container.textContent).not.toContain('单个文件不超过')
    expect(view.container.textContent).not.toMatch(/\d+\s*MB/)
    view.unmount()
  })
})

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
        plugins: [createVuetify(), i18n],
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

  it('tells a one-shot task that has been handed in that it is done, instead of offering the form again', async () => {
    mocks.listSubmissions.mockResolvedValueOnce({ data: { submissions: [{ id: 1, version: 1 }] } })
    const view = render(Submit, {
      props: {
        taskData: {
          id: 13,
          space: { id: 4 },
          submittable: true,
          resubmittable: false,
          submissionSchema: [{ type: 'TEXT', prompt: '成果说明' }],
        } as Task,
        participationInfo: {
          hasParticipation: true,
          identities: [
            {
              id: 5,
              type: 'USER',
              memberId: 3,
              canSubmit: true,
              approved: 'APPROVED',
              deadline: Date.now() + 86400000,
            },
          ],
        },
      },
      global: { plugins: [createVuetify(), i18n], stubs: { VDialog: true, CountdownTimer: true, RouterLink: true } },
    })
    await waitFor(() => expect(view.getByText('已达到提交次数上限')).toBeTruthy())
    expect(mocks.listSubmissions).toHaveBeenCalledWith(13, 5, expect.objectContaining({ pageSize: 1 }))
    expect(view.queryByRole('textbox', { name: '成果说明' })).toBeNull()
    view.unmount()
  })
})
