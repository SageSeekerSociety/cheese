/** 看不见的时候要说得出为什么。
 *
 * 非成员打开一个项目链接，在这之前看到的是空壳：后端那句话经一条 4 秒的红条闪过
 * 就没了，之后页面上再无解释——话题列表空白、项目名不显示，跟「一个刚建好、还
 * 什么都没有的项目」长得一模一样。这里断言的是屏幕上留得住的那段话，以及它给的
 * 下一步动作对得上人当下的处境。
 */
import type { Project } from '@/cx_types'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'

// 断言按中文写；测试环境默认是英文界面。
beforeEach(() => setLocale('zh-CN'))

vi.mock('@/me', () => ({ myHandle: () => 'alice' }))

import ProjectAccessNotice from './ProjectAccessNotice.vue'

import { useWorkspaceStore } from '@/stores/workspace'
import { seedProject, seedProjects } from '@/test/seedQueries'

afterEach(cleanup)
beforeEach(() => setActivePinia(createPinia()))

function show(reason: 'unauthenticated' | 'forbidden' | 'archived') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/account/signin', component: { template: '<div />' } },
    ],
  })
  return render(ProjectAccessNotice, {
    props: { reason },
    global: { plugins: [router, createVuetify({ components, directives })] },
  })
}

describe('打不开这个项目的时候', () => {
  it('没登录：说要登录，并给一条去登录的路', () => {
    const { getByText, getByRole } = show('unauthenticated')
    getByText('需要登录才能查看这个项目')
    expect(getByRole('link', { name: '登录' }).getAttribute('href')).toBe('/account/signin')
  })

  // 登录不一定就够——他可能登录完还是不是成员。先说了才不算骗人。
  it('没登录：不承诺登录就一定看得到', () => {
    const { getByText } = show('unauthenticated')
    expect(getByText(/如果你是这个项目的成员/)).toBeTruthy()
  })

  it('登录了但不是成员：说清是成员资格的问题，不是让他再登一次', () => {
    const { getByText, queryByRole } = show('forbidden')
    getByText('你不是这个项目的成员')
    expect(queryByRole('link', { name: '登录' })).toBeNull()
  })

  it('登录了但不是成员：说得出怎么拿到权限', () => {
    const { getByText } = show('forbidden')
    expect(getByText(/找项目里的人把你加进成员名单/)).toBeTruthy()
  })

  // 这个产品里没有「管理员」这个角色，写了等于让人去找一个不存在的人。
  it('不把人指向一个不存在的角色', () => {
    for (const reason of ['unauthenticated', 'forbidden'] as const) {
      const { container } = show(reason)
      expect(container.textContent).not.toContain('管理员')
      cleanup()
    }
  })
})

// 开着的是这个已归档的项目：它不在项目清单里，名字和所有者是单独读来的那一行。
async function openArchived(project: Project) {
  seedProjects([])
  seedProject(project.id, { topics: [], members: [], unread: {}, privateUnread: {}, notifyLevels: {}, project })
  const store = useWorkspaceStore()
  store.openProject(project.id)
  await vi.waitFor(() => expect(store.openedProject?.id).toBe(project.id))
  return store
}

describe('项目已归档的时候', () => {
  it('不是所有者：说它归档了，只给一条离开的路', async () => {
    await openArchived({ id: 'p', name: 'P', created_at: '', owner_handle: 'bob' })
    const { getByText, queryByRole, getByRole } = show('archived')
    getByText('项目已归档')
    expect(queryByRole('button', { name: '取消归档' })).toBeNull()
    expect(getByRole('link', { name: '回到我的项目' }).getAttribute('href')).toBe('/')
  })

  it('所有者：主操作是取消归档', async () => {
    const store = await openArchived({ id: 'p', name: 'P', created_at: '', owner_handle: 'alice' })
    const restore = vi.spyOn(store, 'unarchiveOpenProject').mockResolvedValue(true)
    const { getByRole } = show('archived')
    await fireEvent.click(getByRole('button', { name: '取消归档' }))
    expect(restore).toHaveBeenCalled()
  })
})
