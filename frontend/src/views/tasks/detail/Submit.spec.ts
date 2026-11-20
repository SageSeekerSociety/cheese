import type { Task } from '@/types'

import { createVuetify } from 'vuetify'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  getParticipants: vi.fn(),
  createSubmission: vi.fn().mockResolvedValue({}),
  listSubmissions: vi.fn().mockResolvedValue({ submissions: [] }),
  push: vi.fn(),
  limits: vi.fn(),
}))
vi.mock('@/api/taskBoard', () => ({ TaskBoardApi: mocks }))
vi.mock('@/api/attachments', () => ({ AttachmentsApi: { upload: vi.fn(), limits: mocks.limits } }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: mocks.push }) }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
const judge = vi.hoisted(() => ({
  codeVersion: vi.fn().mockResolvedValue({ codeVersion: null }),
  latestSelfTest: vi.fn(),
  selfTest: vi.fn(),
}))
vi.mock('@/api/taskJudge', () => ({ TaskJudgeApi: judge }))

import Submit from './Submit.vue'

import i18n, { setLocale } from '@/i18n'

beforeEach(() => {
  setLocale('zh-CN')
  mocks.limits.mockReset()
  mocks.limits.mockResolvedValue({ maxFileBytes: 100 * 1024 * 1024 })
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
    mocks.limits.mockResolvedValue({ maxFileBytes: 200 * 1024 * 1024 })
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
    await waitFor(() => expect(mocks.createSubmission).toHaveBeenCalledWith(12, 99, [{ text: '共同完成的计划' }], null))
    expect(mocks.push).toHaveBeenCalledWith({ name: 'TasksSubmissions', params: { spaceId: 4, taskId: 12 } })
    view.unmount()
  })

  it('tells a one-shot task that has been handed in that it is done, instead of offering the form again', async () => {
    mocks.listSubmissions.mockResolvedValueOnce({ submissions: [{ id: 1, version: 1 }] })
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

describe('a task split into stages', () => {
  const staged = (stages: Task['stages']) =>
    render(Submit, {
      props: {
        taskData: {
          id: 21,
          space: { id: 4 },
          submittable: true,
          resubmittable: true,
          stages,
          submissionSchema: [
            { type: 'TEXT', prompt: '需求文档', stage: 'need' },
            { type: 'TEXT', prompt: '代码说明', stage: 'impl' },
          ],
        } as Task,
        participationInfo: {
          hasParticipation: true,
          identities: [{ id: 8, type: 'USER', memberId: 3, canSubmit: true, approved: 'APPROVED', deadline: null }],
        },
        progress: {
          stages: stages ?? [],
          mine: { participantId: 8, stages: ['PASSED', 'NOT_SUBMITTED'] },
          distribution: null,
        },
      },
      global: { plugins: [createVuetify(), i18n], stubs: { VDialog: true, CountdownTimer: true } },
    })

  it('opens on the first stage not yet passed, and hands in that stage with its own items', async () => {
    const view = staged([
      { id: 'need', name: '需求分析', description: '', dueAt: null },
      { id: 'impl', name: '实现', description: '', dueAt: null },
    ])

    expect(view.queryByRole('textbox', { name: '需求文档' })).toBeNull()
    await fireEvent.update(view.getByRole('textbox', { name: '代码说明' }), '写完了')
    await fireEvent.submit(view.container.querySelector('form')!)
    await waitFor(() => expect(mocks.createSubmission).toHaveBeenCalledWith(21, 8, [{ text: '写完了' }], 'impl'))
    view.unmount()
  })

  it('switches to another stage when it is picked on the timeline', async () => {
    const view = staged([
      { id: 'need', name: '需求分析', description: '', dueAt: null },
      { id: 'impl', name: '实现', description: '', dueAt: null },
    ])

    await fireEvent.click(view.getByRole('button', { name: /需求分析/ }))
    expect(view.getByRole('textbox', { name: '需求文档' })).toBeTruthy()
    expect(view.queryByRole('textbox', { name: '代码说明' })).toBeNull()
    view.unmount()
  })

  it('takes nothing for a stage past its due date', async () => {
    const view = staged([
      { id: 'need', name: '需求分析', description: '', dueAt: null },
      { id: 'impl', name: '实现', description: '', dueAt: Date.now() - 86400000 },
    ])

    await fireEvent.click(view.getByRole('button', { name: /实现/ }))
    expect(view.container.textContent).toContain('阶段「实现」已过截止时间，不能再提交')
    expect(view.queryByRole('textbox', { name: '代码说明' })).toBeNull()
    view.unmount()
  })
})

describe('自测样例', () => {
  const suite = (selfTestDaily: number | null) => ({
    stage: null,
    kind: 'IO',
    runtime: 'python3.12',
    codeDir: '',
    compileCmd: '',
    runCmd: 'python3 main.py',
    timeLimitMs: 1000,
    memoryMb: 256,
    compare: 'TRIM_TRAILING',
    tolerance: 0,
    showHiddenNames: true,
    selfTestDaily,
    cases: [{ name: '样例', points: 10, visibility: 'SAMPLE', input: '1', expected: '1' }],
    revision: 1,
  })
  const mountWith = (selfTestDaily: number | null) =>
    render(Submit, {
      props: {
        taskData: {
          id: 21,
          space: { id: 4 },
          submittable: true,
          resubmittable: true,
          submissionSchema: [{ type: 'TEXT', prompt: '说明' }],
          testSuite: [suite(selfTestDaily)],
        } as unknown as Task,
        participationInfo: {
          hasParticipation: true,
          identities: [{ id: 8, type: 'USER', memberId: 3, canSubmit: true, approved: 'APPROVED', deadline: null }],
        },
      },
      global: { plugins: [createVuetify(), i18n], stubs: { VDialog: true, CountdownTimer: true } },
    })

  it('开了自测：读今天的次数，点「自测样例」排一次', async () => {
    judge.latestSelfTest.mockResolvedValue({ selfTest: null, dailyLimit: 3, usedToday: 0, resetsAt: 0 })
    judge.selfTest.mockResolvedValue({
      selfTest: {
        state: 'done',
        purpose: 'self_test',
        codeSha: 'feedface00',
        suiteRevision: 1,
        passedPoints: 10,
        totalPoints: 10,
        createdAt: 0,
        startedAt: 0,
        finishedAt: 0,
        machineMs: 1,
        cases: [],
      },
      dailyLimit: 3,
      usedToday: 1,
      resetsAt: 0,
    })
    const view = mountWith(3)
    await waitFor(() => expect(judge.latestSelfTest).toHaveBeenCalledWith(21, 8, null))
    const run = await view.findByTestId('task-self-test-run')
    await waitFor(() => expect((run as HTMLButtonElement).disabled).toBe(false))
    await fireEvent.click(run)
    await waitFor(() => expect(judge.selfTest).toHaveBeenCalledWith(21, 8, null))
    await waitFor(() => expect(view.getByTestId('task-self-test-count').textContent).toContain('2'))
    view.unmount()
  })

  it('没开自测：不显示这一块', async () => {
    const view = mountWith(null)
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(view.queryByTestId('task-self-test')).toBeNull()
    view.unmount()
  })
})
