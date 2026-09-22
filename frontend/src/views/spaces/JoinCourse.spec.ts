// 课程链接的落点。这一屏是学生第一次进一门课的整条路，所以钉的是四件事：
//   1. 没登录就先去登录，并且**带着回来**（`?redirect=` 是这条链接本身）
//   2. 登录着的人：加入 + 拿到自己的项目，然后落到课程第一屏
//   3. 组队只问一次，问过就记住；不组也能继续（不是拦路虎）
//   4. 链接失效时说人话，不要沉默
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getCurrentUser = vi.fn()
const spacesJoin = vi.fn()
const spacesEnroll = vi.fn()

vi.mock('@/network/api/users', () => ({
  UserApi: { getCurrentUser: (...a: unknown[]) => getCurrentUser(...a) },
}))

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    join: (...a: unknown[]) => spacesJoin(...a),
    enroll: (...a: unknown[]) => spacesEnroll(...a),
  },
}))

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import JoinCourse from './JoinCourse.vue'

const CODE = 'ABCD2345'
const SPACE_ID = 42
const PROJECT = {
  id: '1f0b9d0e-0000-4000-8000-000000000001',
  name: '程序设计基础',
  root_topic_id: '2f0b9d0e-0000-4000-8000-000000000002',
}

async function mountPage({ withCourseHome = true } = {}) {
  const vuetify = createVuetify({ components, directives })
  const routes = [
    { path: '/spaces/join/:code', name: 'SpacesJoinCourse', component: JoinCourse },
    { path: '/account/signin', name: 'SignIn', component: { template: '<div />' } },
    { path: '/spaces/:spaceId', name: 'SpacesDetail', component: { template: '<div />' } },
    ...(withCourseHome
      ? [{ path: '/spaces/:spaceId/course', name: 'SpacesCourseHome', component: { template: '<div />' } }]
      : []),
  ]
  const router = createRouter({ history: createMemoryHistory(), routes })
  // 地址要先落定再挂载：组件一上电就读 `route.params.code`，路由没 ready 时
  // 它读到的是 `/`，于是每一例都会栽在「这条链接用不了」上。
  await router.push(`/spaces/join/${CODE}`)
  await router.isReady()
  return { router, ...render(JoinCourse, { global: { plugins: [vuetify, router] } }) }
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  // Vuetify 的浮层（v-dialog）会去读 `visualViewport`，jsdom 里没有这个对象，
  // 于是对话框根本不渲染 —— 测试会以为「组队那一问没出现」。给它一个够用的壳。
  if (!('visualViewport' in window)) {
    Object.defineProperty(window, 'visualViewport', {
      configurable: true,
      value: {
        height: 800,
        width: 600,
        offsetTop: 0,
        offsetLeft: 0,
        scale: 1,
        addEventListener: () => {},
        removeEventListener: () => {},
      },
    })
  }
})

describe('opening a course link', () => {
  it('sends someone who is not signed in to sign in, and back to the link', async () => {
    getCurrentUser.mockRejectedValue(new Error('401'))
    const { router } = await mountPage()

    await waitFor(() => expect(router.currentRoute.value.name).toBe('SignIn'))
    expect(router.currentRoute.value.query.redirect).toBe(`/spaces/join/${CODE}`)
    // 没有登录就不该去碰「加入」这件事。
    expect(spacesJoin).not.toHaveBeenCalled()
  })

  it('joins, gets the project, and lands on the course first screen', async () => {
    getCurrentUser.mockResolvedValue({ data: { id: 7 } })
    spacesJoin.mockResolvedValue({ data: { space: { id: SPACE_ID, name: '程序设计基础' } } })
    spacesEnroll.mockResolvedValue({ data: { space: { id: SPACE_ID }, project: PROJECT } })

    const { router, findByText } = await mountPage()
    await findByText('spaces.joinCourse.teamTitle')

    // 组队那一问只在他真有项目时出现，答完才走。
    await fireEvent.click(await findByText('spaces.joinCourse.teamLater'))
    await waitFor(() => expect(router.currentRoute.value.name).toBe('SpacesCourseHome'))

    expect(spacesJoin).toHaveBeenCalledWith({ code: CODE })
    expect(spacesEnroll).toHaveBeenCalledWith(SPACE_ID)
    expect(localStorage.getItem(`cheese:course-team-asked:${SPACE_ID}`)).toBe('1')
  })

  it('asks about teammates only once', async () => {
    getCurrentUser.mockResolvedValue({ data: { id: 7 } })
    spacesJoin.mockResolvedValue({ data: { space: { id: SPACE_ID, name: '程序设计基础' } } })
    spacesEnroll.mockResolvedValue({ data: { space: { id: SPACE_ID }, project: PROJECT } })
    localStorage.setItem(`cheese:course-team-asked:${SPACE_ID}`, '1')

    const { router, queryByText } = await mountPage()
    await waitFor(() => expect(router.currentRoute.value.name).toBe('SpacesCourseHome'))
    expect(queryByText('spaces.joinCourse.teamTitle')).toBeNull()
  })

  it('still lets him in when the course has nothing to hang a project on', async () => {
    getCurrentUser.mockResolvedValue({ data: { id: 7 } })
    spacesJoin.mockResolvedValue({ data: { space: { id: SPACE_ID, name: '程序设计基础' } } })
    spacesEnroll.mockResolvedValue({ data: { space: { id: SPACE_ID }, project: null } })

    const { findByText } = await mountPage()
    // 他是个成员了 —— 这门课还没发布东西，不该用「出错了」把他挡在外面。
    await findByText('spaces.joinCourse.noProject')
  })

  it('says so plainly when the link cannot be used', async () => {
    getCurrentUser.mockResolvedValue({ data: { id: 7 } })
    spacesJoin.mockRejectedValue(new Error('not found'))

    const { findByText } = await mountPage()
    await findByText('spaces.joinCourse.failed')
  })
})
