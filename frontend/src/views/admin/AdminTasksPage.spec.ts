import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

/**
 * 题目审核（`/admin/tasks`）：平台管理员审所有空间里等审核的题。假接口照真接口的
 * 规矩办事 —— 通过或驳回过的题不再出现在等审的列表里 —— 所以断言看的是队列里还剩
 * 什么，不是页面发了哪个调用。
 */
const queue = vi.hoisted(() => ({
  pending: new Map<number, { id: number; name: string; space: { id: number; name: string } }>(),
  /** Set: the server refuses every review with this sentence. */
  refusal: '' as string,
}))
const promptAnswer = vi.hoisted(() => ({ value: '' }))

function task(id: number, name: string, spaceName: string) {
  return {
    id,
    name,
    intro: '',
    description: '',
    creator: { id: 1, nickname: '出题人', avatarId: null },
    createdAt: Date.now(),
    deadline: null,
    submitterType: 'USER',
    resubmittable: false,
    editable: false,
    teamLockingPolicy: 'NO_LOCK',
    requireRealName: false,
    category: null,
    topics: [],
    submissionSchema: [],
    space: { id: id * 10, name: spaceName },
  }
}

vi.mock('@/api/taskBoard', () => ({
  TaskBoardApi: {
    unreviewedEverywhere: async () => {
      const tasks = [...queue.pending.values()]
      return { tasks, page: { hasMore: false, nextStart: null, total: tasks.length } }
    },
    reviewAsPlatformAdmin: async (taskId: number, data: { approved: string; rejectReason?: string }) => {
      const { ApiError } = await import('@/api/http')
      if (queue.refusal) throw new ApiError(400, queue.refusal)
      if (data.approved === 'DISAPPROVED' && !data.rejectReason) throw new Error('a rejection needs a reason')
      queue.pending.delete(taskId)
      return { task: {} }
    },
  },
}))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/plugins/dialog', () => ({
  useDialog: () => ({ prompt: () => ({ wait: async () => promptAnswer.value }) }),
  CancelError: class extends Error {},
}))
vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import { toast as sonner } from 'vuetify-sonner'

import AdminTasksPage from './AdminTasksPage.vue'

function mountPage() {
  return render(AdminTasksPage, {
    global: { plugins: [createVuetify({ components, directives })], stubs: { TipTapViewer: true } },
  })
}

describe('题目审核', () => {
  it('列出不同空间等审的题，每道写着它在哪个空间；通过的那道离开队列', async () => {
    queue.pending = new Map([
      [1, task(1, '甲空间的题', '甲空间')],
      [2, task(2, '乙空间的题', '乙空间')],
    ])
    const view = mountPage()

    expect(await view.findByText('甲空间')).toBeTruthy()
    expect(view.getByText('乙空间')).toBeTruthy()

    await fireEvent.click(view.getByText('甲空间的题'))
    await fireEvent.click(view.getByRole('button', { name: 'spaces.detail.auditTasks.approve' }))

    await waitFor(() => {
      expect(view.queryByText('甲空间的题')).toBeNull()
      expect(view.getByText('乙空间的题')).toBeTruthy()
    })
    view.unmount()
  })

  it('驳回不写原因，题留在队列里；写了原因才退回', async () => {
    queue.pending = new Map([[3, task(3, '要退回的题', '丙空间')]])
    const view = mountPage()
    await fireEvent.click(await view.findByText('要退回的题'))

    promptAnswer.value = '   '
    await fireEvent.click(view.getByRole('button', { name: 'spaces.detail.auditTasks.reject' }))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(view.getByText('要退回的题')).toBeTruthy()

    promptAnswer.value = '说明不完整'
    await fireEvent.click(view.getByRole('button', { name: 'spaces.detail.auditTasks.reject' }))
    await waitFor(() => expect(view.queryByText('要退回的题')).toBeNull())
    view.unmount()
  })

  it('通过被服务端拒绝时，提示里是服务端说的原因，题留在队列里', async () => {
    queue.pending = new Map([[4, task(4, '后发的题', '丁空间')]])
    queue.refusal = '这位出题人还有更早的题目在等审核，先审核那些'
    const view = mountPage()
    await fireEvent.click(await view.findByText('后发的题'))
    await fireEvent.click(view.getByRole('button', { name: 'spaces.detail.auditTasks.approve' }))

    await waitFor(() => expect(sonner.error).toHaveBeenCalledWith('这位出题人还有更早的题目在等审核，先审核那些'))
    expect(view.getByText('后发的题')).toBeTruthy()
    queue.refusal = ''
    view.unmount()
  })
})
