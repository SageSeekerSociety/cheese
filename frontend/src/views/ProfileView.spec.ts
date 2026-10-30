// The personal page: what choosing a week on the activity chart lists, what a
// refused delete leaves behind, and what only the person themselves is shown.
import type { ProfileActivityDay, ProfileTopic, UserProfile } from '@/cx_types'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ProfileView from './ProfileView.vue'

import {
  deleteUnderstanding,
  getMemberSummary,
  getUserProfile,
  getUserTopics,
  listProjectAgents,
  setUserRemark,
} from '@/api'
import i18n, { setLocale } from '@/i18n'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'

const me = vi.hoisted(() => ({ handle: 'lin' }))

vi.mock('@/api', () => ({
  getUserProfile: vi.fn(),
  getUserTopics: vi.fn(),
  getMemberSummary: vi.fn(),
  listProjectAgents: vi.fn(),
  deleteUnderstanding: vi.fn(),
  setUserRemark: vi.fn(),
}))
vi.mock('@/me', () => ({ myHandle: () => me.handle }))
vi.mock('@/api/avatars', () => ({
  getDefaultAvatarId: vi.fn().mockResolvedValue({ avatarId: 1 }),
}))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

/** 365 UTC days ending on Thursday 2026-09-24, with a few busy ones. */
function year(): ProfileActivityDay[] {
  const end = Date.UTC(2026, 8, 24)
  return Array.from({ length: 365 }, (_, i) => {
    const date = new Date(end - (364 - i) * 86_400_000).toISOString().slice(0, 10)
    return { date, count: date === '2026-09-16' ? 5 : date === '2026-08-03' ? 2 : 0 }
  })
}

function profileOf(handle: string, overrides: Partial<UserProfile> = {}): UserProfile {
  return {
    handle,
    name: '林知远',
    bio: '做后端和数据管道',
    avatar_id: null,
    joined_at: '2026-08-01T00:00:00+00:00',
    teams: [],
    activity: { days: year(), total: 7 },
    projects: [],
    understanding: [],
    ...overrides,
  }
}

function topic(id: string, title: string): ProfileTopic {
  return {
    id,
    title,
    status: 'active',
    project_id: 'p1',
    project_name: '数据看板',
    contributions: 3,
    last_participated_at: '2026-09-16T08:00:00+00:00',
  }
}

async function renderPage(handle: string, projectId?: string) {
  const stub = { template: '<div />' }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/users/:handle', name: 'UserPage', component: stub },
      { path: '/users/settings/profile', name: 'UserSettingsProfile', component: stub },
      { path: '/teams/:handle', name: 'TeamsDetailDefault', component: stub },
      { path: '/projects/:projectId', name: 'workspace-project', component: stub },
      { path: '/projects/:projectId/members', name: 'project-members', component: stub },
      { path: '/projects/:projectId/topics/:topicId', name: 'workspace-topic', component: stub },
      { path: '/projects/:projectId/dm/:peer', name: 'workspace-dm', component: stub },
    ],
  })
  await router.push(`/users/${handle}`)
  await router.isReady()
  return render(ProfileView, {
    props: { handle, projectId },
    global: { plugins: [router, createPinia(), i18n, createVuetify({ components, directives })] },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  setLocale('zh-CN')
  me.handle = 'lin'
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.mocked(getMemberSummary).mockResolvedValue(null as never)
  vi.mocked(listProjectAgents).mockResolvedValue({ data: [], total: 0 } as never)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('choosing a week on the activity chart', () => {
  beforeEach(() => {
    vi.mocked(getUserProfile).mockResolvedValue(profileOf('lin'))
    vi.mocked(getUserTopics).mockImplementation(async (_handle, range = {}) => ({
      topics: range.from ? [topic('t2', '那一周的话题')] : [topic('t1', '最近的话题')],
    }))
  })

  it('lists the topics of that week, and going back lists the recent ones again', async () => {
    const view = await renderPage('lin')
    expect(await view.findByText('最近的话题')).toBeTruthy()

    // Wednesday 16 September sits in the week Monday 14 – Sunday 20.
    await fireEvent.click(view.getByRole('button', { name: '9月16日，5 条贡献' }))

    expect(getUserTopics).toHaveBeenLastCalledWith(
      'lin',
      expect.objectContaining({ from: '2026-09-14', to: '2026-09-20' })
    )
    expect(await view.findByText('那一周的话题')).toBeTruthy()
    expect(view.queryByText('最近的话题')).toBeNull()

    await fireEvent.click(view.getByRole('button', { name: '回到最近' }))

    expect(await view.findByText('最近的话题')).toBeTruthy()
    expect(view.queryByText('那一周的话题')).toBeNull()
  })

  it('choosing the same week again goes back to the recent topics', async () => {
    const view = await renderPage('lin')
    await view.findByText('最近的话题')
    const cell = view.getByRole('button', { name: '9月16日，5 条贡献' })

    await fireEvent.click(cell)
    await view.findByText('那一周的话题')
    await fireEvent.click(cell)

    expect(await view.findByText('最近的话题')).toBeTruthy()
    expect(view.queryByRole('button', { name: '回到最近' })).toBeNull()
  })
})

describe('deleting what an agent noted about you', () => {
  const notes = [
    {
      id: 'n1',
      content: '偏好先写数据库迁移再改接口',
      created_at: '2026-09-01T00:00:00+00:00',
      project_id: 'p1',
      project_name: '数据看板',
      agent_handle: 'cheese',
      agent_name: '芝士',
    },
    {
      id: 'n2',
      content: '对查询性能敏感',
      created_at: '2026-09-02T00:00:00+00:00',
      project_id: 'p1',
      project_name: '数据看板',
      agent_handle: 'cheese',
      agent_name: '芝士',
    },
  ]

  beforeEach(() => {
    vi.mocked(getUserProfile).mockResolvedValue(profileOf('lin', { understanding: structuredClone(notes) }))
    vi.mocked(getUserTopics).mockResolvedValue({ topics: [] })
  })

  it('puts a note the server refuses back where it was', async () => {
    let refuse!: (e: Error) => void
    vi.mocked(deleteUnderstanding).mockReturnValue(new Promise((_, reject) => (refuse = reject)))
    const view = await renderPage('lin')
    await view.findByText('偏好先写数据库迁移再改接口')

    await fireEvent.click(view.getAllByRole('button', { name: '删除这条' })[0])
    // Gone at once, before the server has answered.
    await waitFor(() => expect(view.queryByText('偏好先写数据库迁移再改接口')).toBeNull())
    expect(deleteUnderstanding).toHaveBeenCalledWith('n1')

    refuse(new Error('500'))

    await waitFor(() => expect(view.queryByText('偏好先写数据库迁移再改接口')).toBeTruthy())
    const order = Array.from(view.container.querySelectorAll('li')).map((li) => li.textContent)
    expect(order[0]).toContain('偏好先写数据库迁移再改接口')
    expect(order[1]).toContain('对查询性能敏感')
  })

  it('keeps a deleted note gone once the server accepts', async () => {
    vi.mocked(deleteUnderstanding).mockResolvedValue({ deleted: 'n2' })
    const view = await renderPage('lin')
    await view.findByText('对查询性能敏感')

    await fireEvent.click(view.getAllByRole('button', { name: '删除这条' })[1])

    await waitFor(() => expect(view.queryByText('对查询性能敏感')).toBeNull())
    expect(view.getByText('偏好先写数据库迁移再改接口')).toBeTruthy()
  })
})

describe('what only the person themselves sees', () => {
  beforeEach(() => {
    vi.mocked(getUserTopics).mockResolvedValue({ topics: [] })
  })

  it('shows your notes and the way to edit your profile on your own page', async () => {
    vi.mocked(getUserProfile).mockResolvedValue(profileOf('lin'))
    const view = await renderPage('lin')

    expect(await view.findByRole('heading', { name: '芝士眼中的你' })).toBeTruthy()
    expect(view.getByRole('link', { name: '编辑资料' })).toBeTruthy()
  })

  it('shows neither on someone else’s page', async () => {
    me.handle = 'chen'
    vi.mocked(getUserProfile).mockResolvedValue(profileOf('lin'))
    const view = await renderPage('lin')

    await view.findByRole('heading', { name: '林知远' })
    expect(view.queryByRole('heading', { name: '芝士眼中的你' })).toBeNull()
    expect(view.queryByRole('link', { name: '编辑资料' })).toBeNull()
  })
})

describe('opened from a project’s roster', () => {
  beforeEach(() => {
    vi.mocked(getUserTopics).mockResolvedValue({ topics: [] })
  })

  function summary(handle: string, extra: Record<string, unknown>) {
    return { handle, topics_started: [], topics_active: [], weekly_contributions: 0, waiting_on_you: [], ...extra }
  }

  it('calls an AI teammate an AI teammate and offers a private chat with it', async () => {
    vi.mocked(getUserProfile).mockResolvedValue(profileOf('cheese-742841cae293', { name: '小杉' }))
    vi.mocked(getMemberSummary).mockResolvedValue(
      summary('cheese-742841cae293', { source: 'agent', instance_handle: 'xiaoshan' }) as never
    )
    const view = await renderPage('cheese-742841cae293', 'p1')

    await view.findAllByRole('heading', { name: '小杉' })
    expect(view.getByText('AI 队友')).toBeTruthy()
    expect(view.queryByText('团队成员')).toBeNull()
    const dm = view.getByRole('link', { name: '私聊' })
    expect(dm.getAttribute('href')).toBe('/projects/p1/dm/agent:xiaoshan')
  })

  it('offers a private chat with another person in the project', async () => {
    vi.mocked(getUserProfile).mockResolvedValue(profileOf('wang', { name: '王' }))
    vi.mocked(getMemberSummary).mockResolvedValue(summary('wang', { source: 'team' }) as never)
    const view = await renderPage('wang', 'p1')

    await view.findAllByRole('heading', { name: '王' })
    expect(view.getByText('团队成员')).toBeTruthy()
    expect(view.getByRole('link', { name: '私聊' }).getAttribute('href')).toBe('/projects/p1/dm/wang')
  })

  it('offers no private chat with yourself', async () => {
    vi.mocked(getUserProfile).mockResolvedValue(profileOf('lin'))
    vi.mocked(getMemberSummary).mockResolvedValue(summary('lin', { source: 'owner' }) as never)
    const view = await renderPage('lin', 'p1')

    await view.findAllByRole('heading', { name: '林知远' })
    expect(view.queryByRole('link', { name: '私聊' })).toBeNull()
  })

  it('lets you add, edit, and clear a personal remark on someone else’s page', async () => {
    me.handle = 'chen'
    vi.mocked(getUserProfile).mockResolvedValue(profileOf('lin', { name: '林知远', remark: null }))
    vi.mocked(setUserRemark).mockResolvedValue({ handle: 'lin', remark: '后端接口-林工' })
    const view = await renderPage('lin')

    await view.findByRole('heading', { name: '林知远' })
    await fireEvent.click(view.getByTestId('profile-remark-trigger'))
    const input = view.getByTestId('profile-remark-input') as HTMLInputElement
    await fireEvent.update(input, '后端接口-林工')
    await fireEvent.click(view.getByTestId('profile-remark-save'))

    await waitFor(() => expect(setUserRemark).toHaveBeenCalledWith('lin', '后端接口-林工'))
    expect(await view.findByRole('heading', { name: '后端接口-林工' })).toBeTruthy()
    expect(view.getByText(/原名：林知远/)).toBeTruthy()

    vi.mocked(setUserRemark).mockResolvedValue({ handle: 'lin', remark: null })
    await fireEvent.click(view.getByTestId('profile-remark-trigger'))
    await fireEvent.click(view.getByTestId('profile-remark-clear'))
    await waitFor(() => expect(setUserRemark).toHaveBeenCalledWith('lin', ''))
    expect(await view.findByRole('heading', { name: '林知远' })).toBeTruthy()
  })

  it('in-project profile reuses cached agentsQuery without fetching /projects/:id/agents again', async () => {
    queryClient.setQueryData(keys.projectAgents('p-cached'), [
      {
        id: 'a1',
        project_id: 'p-cached',
        handle: 'cheese',
        seat_handle: 'cheese-seat',
        display_name: '芝士',
        name_source: 'default',
        is_default: true,
        is_active: true,
        type_name: null,
        configuration: { skills: ['cheese-docs'] },
        version: 1,
        created_at: '',
        updated_at: '',
      },
    ])
    vi.mocked(getUserProfile).mockResolvedValue(profileOf('cheese-seat'))
    vi.mocked(getUserTopics).mockResolvedValue({ topics: [] })
    vi.mocked(getMemberSummary).mockResolvedValue({
      source: 'agent',
      weekly_contributions: 1,
      waiting_on_you: [],
      topics_active: [],
    } as never)
    const view = await renderPage('cheese-seat', 'p-cached')
    expect(await view.findByText('cheese-docs')).toBeTruthy()
    expect(listProjectAgents).not.toHaveBeenCalled()
  })
})
