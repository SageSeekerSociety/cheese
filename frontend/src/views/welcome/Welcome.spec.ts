// 新手引导：回答变成第一个项目，项目在点「建好项目」时才建，建好后进它的「综合」，
// 第一句请求已经写在输入框里（带 @芝士，直接回车芝士不会接）。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

const finishOnboarding = vi.fn()
const uploadAttachment = vi.fn()
vi.mock('@/api', () => ({
  finishOnboarding: (a: unknown) => finishOnboarding(a),
  uploadAttachment: (...a: unknown[]) => uploadAttachment(...a),
}))
const saveComposerDraft = vi.fn()
vi.mock('@/lib/composerDrafts', () => ({ saveComposerDraft: (...a: unknown[]) => saveComposerDraft(...a) }))
vi.mock('@/query/projects', () => ({ refreshProjects: vi.fn().mockResolvedValue(undefined) }))
vi.mock('@/lib/desktopApp', () => ({ inDesktopApp: () => false }))
vi.mock('@/lib/desktop', () => ({ desktopBridge: () => null, startConnecting: vi.fn() }))
vi.mock('vuetify-sonner', () => ({ toast: { warning: vi.fn() } }))
const replace = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ replace }),
  RouterLink: { template: '<a><slot /></a>' },
}))
const account = vi.hoisted(() => ({
  user: {
    id: 1,
    username: 'lin',
    nickname: '林小满',
    onboarded_at: null,
    work_role: null,
    organization: null,
  } as Record<string, unknown>,
}))
vi.mock('@/services/account', () => ({ default: account }))

import Welcome from './Welcome.vue'

function mount(again = false) {
  return render(Welcome, {
    props: { again },
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      stubs: { 'router-link': { template: '<a><slot /></a>' } },
    },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
  account.user = { id: 1, username: 'lin', nickname: '林小满', onboarded_at: null, work_role: null, organization: null }
  finishOnboarding.mockResolvedValue({ project_id: 'p1', root_topic_id: 'general', not_invited: [], on_device: false })
})

async function next(view: ReturnType<typeof mount>) {
  await fireEvent.click(view.getByRole('button', { name: '继续' }))
}

describe('新手引导', () => {
  it('turns the answers into the project and opens its 综合 with the first request written', async () => {
    const view = mount()
    await next(view)
    await fireEvent.click(view.getByRole('button', { name: /上课、做课程项目/ }))
    await next(view)
    await fireEvent.click(view.getByRole('button', { name: /写课程报告或论文初稿/ }))
    await next(view)
    await next(view)
    expect(finishOnboarding).not.toHaveBeenCalled()

    await fireEvent.click(view.getByRole('button', { name: '建好项目' }))
    await vi.waitFor(() => expect(replace).toHaveBeenCalled())

    const sent = finishOnboarding.mock.calls[0][0]
    expect(sent).toMatchObject({
      project_name: '课程报告',
      intent: '写课程报告或论文初稿',
      nickname: '林小满',
      work_role: 'student',
      solo: true,
      invitees: [],
    })
    const [room, draft] = saveComposerDraft.mock.calls[0]
    expect(room).toBe('general')
    expect(draft.draft.startsWith('@芝士 ')).toBe(true)
    expect(draft.draft).toContain('先给我看提纲')
    expect(replace).toHaveBeenCalledWith({ name: 'workspace-topic', params: { projectId: 'p1', topicId: 'general' } })
    expect(account.user.onboarded_at).toBeTruthy()
  })

  it('suggests first tasks for the role picked', async () => {
    const view = mount()
    await next(view)
    await fireEvent.click(view.getByRole('button', { name: /教课、带学生/ }))
    await next(view)
    expect(view.getByRole('button', { name: /出一道作业题/ })).toBeTruthy()
    expect(view.queryByRole('button', { name: /写课程报告或论文初稿/ })).toBeNull()
  })

  it('invites the people named when the work is shared', async () => {
    const view = mount()
    await next(view)
    await next(view)
    await next(view)
    await fireEvent.click(view.getByRole('button', { name: /和同学或同事一起/ }))
    await fireEvent.update(view.getByLabelText('用户名或邮箱，多个用空格隔开'), 'zhangsan wangwu@ruc.edu.cn')
    await next(view)
    await fireEvent.click(view.getByRole('button', { name: '建好项目' }))
    await vi.waitFor(() => expect(finishOnboarding).toHaveBeenCalled())
    expect(finishOnboarding.mock.calls[0][0]).toMatchObject({
      solo: false,
      invitees: ['zhangsan', 'wangwu@ruc.edu.cn'],
    })
  })

  it('skipping the guide still makes a project and opens it, with nothing written for the person', async () => {
    const view = mount()
    await fireEvent.click(view.getByRole('button', { name: '跳过引导' }))
    await vi.waitFor(() => expect(replace).toHaveBeenCalled())
    expect(finishOnboarding.mock.calls[0][0]).toMatchObject({ project_name: '林小满的项目', intent: '', solo: true })
    expect(finishOnboarding.mock.calls[0][0].work_role).toBeUndefined()
    expect(saveComposerDraft).not.toHaveBeenCalled()
  })

  it('opened again from the help menu, starts at the role and keeps the earlier answers', async () => {
    account.user = { ...account.user, onboarded_at: '2026-10-11T00:00:00Z', work_role: 'teacher' }
    const view = mount(true)
    expect(view.getByRole('button', { name: /教课、带学生/ }).getAttribute('aria-pressed')).toBe('true')
    expect(view.queryByRole('button', { name: '跳过引导' })).toBeNull()
  })

  it('says why the project was not made and stays on the page', async () => {
    finishOnboarding.mockRejectedValue(new Error('代码仓库服务响应超时'))
    const view = mount()
    await fireEvent.click(view.getByRole('button', { name: '跳过引导' }))
    expect(await view.findByRole('alert')).toBeTruthy()
    expect(view.getByRole('alert').textContent).toContain('代码仓库服务响应超时')
    expect(replace).not.toHaveBeenCalled()
  })
})
