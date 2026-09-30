import { createVuetify } from 'vuetify'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  list: vi.fn().mockResolvedValue({ data: { tasks: [], page: { page_start: null, page_size: 20, has_more: false } } }),
  update: vi.fn().mockResolvedValue({ data: {} }),
}))
/** 驳回时那个「填原因」对话框：用例里决定人填了什么。 */
const promptAnswer = vi.hoisted(() => ({ value: '' }))
vi.mock('@/network/api/tasks', () => ({ TasksApi: mocks }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

// 页头只是个壳（面包屑要真路由和导航 store）；这里只留它的标题与操作区插槽。
vi.mock('@/components/common/PageHeader.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'PageHeaderStub',
      props: { title: { type: String, default: '' } },
      setup(props, { slots }) {
        return () => h('div', { class: 'page-header-stub' }, [props.title, slots.default?.(), slots.actions?.()])
      },
    }),
  }
})
vi.mock('@/plugins/dialog', () => ({
  useDialog: () => ({ prompt: () => ({ wait: async () => promptAnswer.value }) }),
  CancelError: class extends Error {},
}))
vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import AuditTask from './AuditTask.vue'

import { useSpaceStore } from '@/stores/space'

describe('审核题目', () => {
  it('loads the waiting tasks once the space is known, even when opened before it', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useSpaceStore()
    const view = render(AuditTask, { global: { plugins: [pinia, createVuetify()], stubs: { TipTapViewer: true } } })
    expect(mocks.list).not.toHaveBeenCalled()

    store.currentSpaceId = 8
    await waitFor(() =>
      expect(mocks.list).toHaveBeenCalledWith(expect.objectContaining({ space: 8, approved: 'NONE' }))
    )
    view.unmount()
  })

  it('审核队列里看得见作者要求交什么', async () => {
    // 这个假接口照真接口的规矩回话：**不点名要就不带那张表单**。所以这条用例
    // 既验「要求显示出来了」，也验「页面确实去要了」—— 页面不点名，这里就会
    // 渲染成「无提交要求」，断言随之失败。
    mocks.list.mockImplementation(async (params: { querySubmissionSchema?: boolean }) => ({
      data: {
        tasks: [
          {
            id: 101,
            name: '带提交要求的题',
            intro: '随便一句简介',
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
            submissionSchema: params?.querySubmissionSchema ? [{ prompt: '提交文件', type: 'FILE' }] : [],
          },
        ],
        page: { page_start: null, page_size: 20, has_more: false },
      },
    }))

    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useSpaceStore()
    const view = render(AuditTask, { global: { plugins: [pinia, createVuetify()], stubs: { TipTapViewer: true } } })
    store.currentSpaceId = 8

    const title = await view.findByText('带提交要求的题')
    await fireEvent.click(title)

    expect(await view.findByText('提交文件')).toBeTruthy()
    expect(view.queryByText('spaces.detail.auditTasks.noRequirements')).toBeNull()
    view.unmount()
  })

  it('驳回必须写原因：只填空格不发请求，写了才把原因带上', async () => {
    mocks.list.mockResolvedValue({
      data: {
        tasks: [
          {
            id: 202,
            name: '待驳回的题',
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
          },
        ],
        page: { page_start: null, page_size: 20, has_more: false },
      },
    })
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useSpaceStore()
    const view = render(AuditTask, { global: { plugins: [pinia, createVuetify()], stubs: { TipTapViewer: true } } })
    store.currentSpaceId = 8
    // 点开这一行，底下才有「通过」「驳回」。
    await fireEvent.click(await view.findByText('待驳回的题'))

    promptAnswer.value = '   '
    await fireEvent.click(view.getByRole('button', { name: 'spaces.detail.auditTasks.reject' }))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(mocks.update).not.toHaveBeenCalled()

    promptAnswer.value = '题面缺少输入格式'
    await fireEvent.click(view.getByRole('button', { name: 'spaces.detail.auditTasks.reject' }))
    await waitFor(() =>
      expect(mocks.update).toHaveBeenCalledWith(202, { approved: 'DISAPPROVED', rejectReason: '题面缺少输入格式' })
    )
    view.unmount()
  })
})
