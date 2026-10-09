import type { RouteRecordRaw } from 'vue-router'
import type { Project } from '@/cx_types'

import { createMemoryHistory, createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import { VBtn, VIcon } from 'vuetify/components'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { topBarBack } from '../topBarBack'

import ParentBackButton from './ParentBackButton.vue'

import { setLocale } from '@/i18n'
import HomeRoutes from '@/router/home'
import { legacyProjectRedirects } from '@/router/legacyProjectPaths'
import SpacesRoutes from '@/router/spaces'
import TeamsRoutes from '@/router/teams'
import UserRoutes from '@/router/user'
import { workspaceRoutes } from '@/router/workspaceRoutes'
import { useWorkspaceStore } from '@/stores/workspace'
import { seedProject, seedProjects } from '@/test/seedQueries'

// Keep the production route hierarchy and redirects, without mounting page data loaders.
function withoutViews(record: RouteRecordRaw): RouteRecordRaw {
  return {
    ...record,
    components: { default: { template: '<div />' } },
    // 管理那一段的 `beforeEnter`（`router/spaces.ts` 的 `spaceManageGuard`）也是
    // 一个数据加载器：它先去取这个空间的管理员名单。这一份里没有真接口，取不到
    // 就等于「不是管理员」，人会被领到 `SpaceManageDenied`，父级自然也不是这一条
    // 声明的那个了。这一份钉的是「← 去哪儿」，取数留给钉守卫的那一份。
    beforeEnter: undefined,
    children: record.children?.map(withoutViews),
  } as RouteRecordRaw
}

afterEach(cleanup)

/** Vuetify 的断点读的是 window.innerWidth；桌面和手机上项目的「根」不是同一条路由。 */
const DESKTOP = 1280
const PHONE = 390

function widthIs(px: number) {
  Object.defineProperty(window, 'innerWidth', { value: px, writable: true, configurable: true })
}

let pinia: ReturnType<typeof createPinia>

beforeEach(() => {
  setLocale('zh-CN')
  widthIs(DESKTOP)
  pinia = createPinia()
  setActivePinia(pinia)
})

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      ...legacyProjectRedirects,
      ...[HomeRoutes, SpacesRoutes, TeamsRoutes, UserRoutes, workspaceRoutes].map(withoutViews),
    ],
  })
}

async function mount(router: ReturnType<typeof makeRouter>) {
  await router.isReady()
  return render(ParentBackButton, {
    global: { plugins: [router, pinia, createVuetify({ components: { VBtn, VIcon } })] },
  })
}

async function open(path: string) {
  const router = makeRouter()
  await router.push(path)
  const view = await mount(router)
  return { router, ...view }
}

const back = (q: { queryByRole: (r: string, o: { name: RegExp }) => HTMLElement | null }) =>
  q.queryByRole('link', { name: /^返回/ })

describe('返回上一级', () => {
  it.each([
    ['/projects/project-a/channels/7', '/projects/project-a'],
    ['/projects/project-a/settings', '/projects/project-a'],
    ['/projects/project-a/agents', '/projects/project-a'],
    ['/projects/project-a/docs/weeklies', '/projects/project-a'],
    ['/spaces/42/tasks/7', '/spaces/42/tasks'],
    ['/spaces/42/tasks/7/submit', '/spaces/42/tasks/7'],
    ['/spaces/42/tasks/7/edit', '/spaces/42/tasks/7'],
    ['/spaces/42/tasks/publish', '/spaces/42/tasks'],
    ['/spaces/42/manage/settings/templates/create', '/spaces/42/manage/settings/templates'],
    ['/spaces/42/manage/settings/templates/2/edit', '/spaces/42/manage/settings/templates'],
    ['/spaces/42/tasks', '/spaces'],
    ['/teams/12/members', '/home'],
  ])('direct entry to %s returns to %s without browser history', async (path, parent) => {
    const { router, getByRole } = await open(path)
    const link = getByRole('link', { name: '返回上一级' })
    expect(link.getAttribute('href')).toBe(parent)
    expect(link.textContent).not.toContain('返回上一级')
    expect(link.getAttribute('title')).toBe('返回上一级')
    await fireEvent.click(link)
    await waitFor(() => expect(router.currentRoute.value.path).toBe(parent))
  })

  it.each(['/spaces', '/teams/mine', '/projects/project-a'])(
    'does not offer a return to nowhere on %s',
    async (path) => {
      const { queryByRole } = await open(path)
      expect(queryByRole('link', { name: '返回上一级' })).toBeNull()
    }
  )

  // 它指向的是**父**地址，而 vue-router 的非精确匹配认为「站在子路由上时父链接
  // 是激活的」，于是 Vuetify 一直给它盖一层 12% 的实底遮罩——顶栏左上角一个永远
  // 按下去的灰方块。返回是「离开这一层」，不是「你在这儿」。
  it.each(['/projects/project-a/channels/7', '/projects/project-a/settings', '/spaces/42/tasks/7'])(
    'does not sit in a pressed state on %s',
    async (path) => {
      const { getByRole } = await open(path)
      expect(getByRole('link', { name: '返回上一级' }).className).not.toContain('v-btn--active')
    }
  )

  it('updates the parent when navigating to another project', async () => {
    const { router, getByRole } = await open('/projects/project-a/channels/7')
    await router.push('/projects/project-c/settings')
    expect(getByRole('link', { name: '返回上一级' }).getAttribute('href')).toBe('/projects/project-c')
  })
})

// 贴链接直接打开、或者刷新之后，历史里没有"上一层"。这正是当初不敢用
// history.back() 的那件事——它会把人踢出整个应用。于是退到数据里的归属关系。
describe('没有来路时，退到项目所属的小队', () => {
  // 项目外框打开了 project-a，项目清单已经读回来。
  function projectsAre(projects: Project[]) {
    seedProjects(projects)
    seedProject('project-a', { topics: [], members: [], unread: {}, privateUnread: {}, notifyLevels: {} })
    useWorkspaceStore().openProject('project-a')
  }

  function ownedBy(team: number | null) {
    projectsAre([
      { id: 'project-a', name: 'A', created_at: '', team_id: team, team_handle: team === null ? null : `crew-${team}` },
    ])
  }

  it('← 指向项目所属的小队', async () => {
    ownedBy(12)
    const view = await open('/projects/project-a/running')
    expect(back(view)?.getAttribute('href')).toBe('/teams/crew-12')
  })

  // 历史遗留的行没有小队（新建项目一律会落到创建者的个人小队）。这种情况诚实的
  // 答案是没有上一层——不假装用户来过某个地方。
  // 项目在某人名下时，所属的是只有他自己的那个团队，地址是他的用户名，也只有他本人
  // 打得开。被邀请进来的人按下去只会看到「找不到」，所以对他们没有这一层。
  function ownedByPerson(handle: string) {
    projectsAre([{ id: 'project-a', name: 'A', created_at: '', team_id: 3, team_handle: handle, owner_handle: handle }])
  }

  it('自己名下的项目，← 回你名下的项目', async () => {
    localStorage.setItem('user', JSON.stringify({ id: 1, username: 'linxia' }))
    ownedByPerson('linxia')
    const view = await open('/projects/project-a/running')
    expect(back(view)?.getAttribute('href')).toBe('/teams/linxia')
    expect(back(view)?.getAttribute('title')).toBe('返回你名下的项目')
  })

  it('别人名下的项目，← 不指向对方打不开给你的那一页', async () => {
    localStorage.setItem('user', JSON.stringify({ id: 1, username: 'linxia' }))
    ownedByPerson('alice')
    const view = await open('/projects/project-a/running')
    expect(back(view)).toBeNull()
  })

  it('项目不属于任何小队时，← 不出现', async () => {
    ownedBy(null)
    const view = await open('/projects/project-a/running')
    expect(back(view)).toBeNull()
  })

  // 桌面上 `/projects/:id` 一帧都不停，当场被换成看板。看板却声明着"我的上一层是
  // /projects/:id"，所以那颗 ← 按下去只会被弹回看板自己——一颗按了没反应的按钮。
  it('桌面看板上那颗 ← 不再指向会把人弹回来的地址', async () => {
    ownedBy(null)
    const view = await open('/projects/project-a/running')
    expect(back(view)?.getAttribute('href')).not.toBe('/projects/project-a')
  })

  it('手机上看板仍然回话题列表：那是真的上一层', async () => {
    widthIs(PHONE)
    ownedBy(12)
    const view = await open('/projects/project-a/running')
    expect(back(view)?.getAttribute('href')).toBe('/projects/project-a')
  })
})

// 设备、连接这几页：手机上是从头像菜单推进来的一层，← 回首页；桌面上 rail 一直在，
// 它们不是谁的下一层，顶栏不画 ←。
describe('只在手机上是一层的页面', () => {
  function withPersonalPage() {
    const router = makeRouter()
    router.addRoute({
      name: 'my-devices',
      path: '/my/devices',
      component: { template: '<div />' },
      meta: { title: '我的设备', hideTabs: true, backTo: 'HomeHub', backOnPhoneOnly: true },
    })
    return router
  }

  it('手机上 ← 回首页', async () => {
    widthIs(PHONE)
    const router = withPersonalPage()
    await router.push('/my/devices')
    const view = await mount(router)
    expect(back(view)?.getAttribute('href')).toBe('/home')
  })

  it('桌面上不画 ←', async () => {
    const router = withPersonalPage()
    await router.push('/my/devices')
    const view = await mount(router)
    expect(back(view)).toBeNull()
  })
})

// 页面里还有一层比路由更近的「上一步」时（手机上话题里从别的页签回到对话），页面
// 接管这颗 ←；桌面上没有这一层，照旧回路由声明的上一层。
describe('页面接管 ←', () => {
  afterEach(() => (topBarBack.value = null))

  it('手机上点 ← 走页面给的那一步，不离开这一页', async () => {
    widthIs(PHONE)
    const onBack = vi.fn()
    topBarBack.value = { label: '返回对话', onBack }
    const { router, getByRole } = await open('/projects/project-a/channels/7')
    await fireEvent.click(getByRole('button', { name: '返回对话' }))
    expect(onBack).toHaveBeenCalledOnce()
    expect(router.currentRoute.value.path).toBe('/projects/project-a/channels/7')
  })

  it('桌面上照旧回上一层', async () => {
    const onBack = vi.fn()
    topBarBack.value = { label: '返回对话', onBack }
    const view = await open('/projects/project-a/channels/7')
    expect(back(view)?.getAttribute('href')).toBe('/projects/project-a')
  })
})

// 顶栏的 ← 回答「这一层上面是谁」；浏览器那一颗才回答「我刚才在哪」。声明了父级的
// 页面一律按声明走：从话题 A 跳到话题 B，← 回话题列表而不是回 A——A 和 B 是并列的
// 两层，不是上下级。只有声明不出父级的页面（下一组用例）才回退到「来路」。
describe('声明了父级时，← 走层级而不是来路', () => {
  function webRouter() {
    return createRouter({
      history: createWebHistory(),
      routes: [
        ...legacyProjectRedirects,
        ...[HomeRoutes, SpacesRoutes, TeamsRoutes, UserRoutes, workspaceRoutes].map(withoutViews),
      ],
    })
  }

  afterEach(() => vi.restoreAllMocks())

  it.each([
    [
      '从一个话题跳到另一个话题',
      '/projects/project-a/topics/topic-a',
      '/projects/project-a/channels/7',
      '/projects/project-a',
    ],
    ['从小队页进项目设置', '/teams/12/members', '/projects/project-a/settings', '/projects/project-a'],
  ])('%s：从 %s 跳到 %s 之后，← 去声明的父级 %s', async (_, from, to, parent) => {
    const router = webRouter()
    await router.push(from)
    await router.push(to)
    const go = vi.spyOn(window.history, 'go')
    const { getByRole } = await mount(router)
    const link = getByRole('link', { name: '返回上一级' })
    expect(link.getAttribute('href')).toBe(parent)
    await fireEvent.click(link)
    await waitFor(() => expect(router.currentRoute.value.path).toBe(parent))
    expect(go).not.toHaveBeenCalled()
  })

  // 项目看板在桌面上就是项目的根，根没有可声明的父级；这一组用例里也没有小队数据，
  // 于是层级给不出答案——这时才回退到「从哪来的」，按浏览器的语义退一格。
  it('层级给不出答案时，退回浏览器的一步', async () => {
    const router = webRouter()
    await router.push('/teams/12')
    await router.push('/projects/project-a/running')
    const go = vi.spyOn(window.history, 'go')
    const { getByRole, queryByRole } = await mount(router)
    expect(queryByRole('link', { name: /^返回/ })).toBeNull()
    await fireEvent.click(getByRole('button', { name: '返回' }))
    expect(go).toHaveBeenCalledWith(-1)
  })
})
