import type { RouteRecordRaw } from 'vue-router'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import { VBtn, VIcon } from 'vuetify/components'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import ParentBackButton from './ParentBackButton.vue'

import { recordEntry } from '@/lib/projectEntry'
import HomeRoutes from '@/router/home'
import { legacyProjectRedirects } from '@/router/legacyProjectPaths'
import SpacesRoutes from '@/router/spaces'
import TeamsRoutes from '@/router/teams'
import UserRoutes from '@/router/user'
import { workspaceRoutes } from '@/router/workspaceRoutes'
import { useWorkspaceStore } from '@/stores/workspace'

// Keep the production route hierarchy and redirects, without mounting page data loaders.
function withoutViews(record: RouteRecordRaw): RouteRecordRaw {
  return {
    ...record,
    components: { default: { template: '<div />' } },
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
  sessionStorage.clear()
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

/** 走一条真实的路线，每一跳都过一遍「记来路」那条规则。 */
async function walk(...path: string[]) {
  const router = makeRouter()
  router.afterEach((to, from) => {
    recordEntry(to, from, (r) => {
      for (const record of [...r.matched].reverse()) if (record.meta?.title) return String(record.meta.title)
      return ''
    })
  })
  for (const step of path) await router.push(step)
  const view = await mount(router)
  return { router, ...view }
}

const back = (q: { queryByRole: (r: string, o: { name: RegExp }) => HTMLElement | null }) =>
  q.queryByRole('link', { name: /^返回/ })

describe('返回上一级', () => {
  it.each([
    ['/projects/project-a/topics/topic-b', '/projects/project-a'],
    ['/projects/project-a/settings', '/projects/project-a'],
    ['/projects/project-a/agents', '/projects/project-a'],
    ['/projects/project-a/docs/decisions', '/projects/project-a'],
    ['/spaces/42/tasks/7', '/spaces/42/tasks'],
    ['/spaces/42/tasks/7/submit', '/spaces/42/tasks/7'],
    ['/spaces/42/tasks/7/edit', '/spaces/42/tasks/7'],
    ['/spaces/42/tasks/publish', '/spaces/42/tasks'],
    ['/spaces/42/discussions/9', '/spaces/42/discussions'],
    ['/spaces/42/discussions/create', '/spaces/42/discussions'],
    ['/spaces/42/templates/create', '/spaces/42/templates'],
    ['/spaces/42/templates/2/edit', '/spaces/42/templates'],
    ['/spaces/42/tasks', '/spaces'],
    ['/teams/12/members', '/teams/mine'],
    ['/users/privacy-center/access-logs', '/users/privacy-center'],
  ])('direct entry to %s returns to %s without browser history', async (path, parent) => {
    const { router, getByRole } = await open(path)
    const link = getByRole('link', { name: '返回上一级' })
    expect(link.getAttribute('href')).toBe(parent)
    expect(link.textContent).not.toContain('返回上一级')
    expect(link.getAttribute('title')).toBe('返回上一级')
    await fireEvent.click(link)
    await waitFor(() => expect(router.currentRoute.value.path).toBe(parent))
  })

  it.each(['/spaces', '/teams/mine', '/projects/project-a', '/users/privacy-center'])(
    'does not offer a return to nowhere on %s',
    async (path) => {
      const { queryByRole } = await open(path)
      expect(queryByRole('link', { name: '返回上一级' })).toBeNull()
    }
  )

  // 它指向的是**父**地址，而 vue-router 的非精确匹配认为「站在子路由上时父链接
  // 是激活的」，于是 Vuetify 一直给它盖一层 12% 的实底遮罩——顶栏左上角一个永远
  // 按下去的灰方块。返回是「离开这一层」，不是「你在这儿」。
  it.each(['/projects/project-a/topics/topic-b', '/projects/project-a/settings', '/spaces/42/tasks/7'])(
    'does not sit in a pressed state on %s',
    async (path) => {
      const { getByRole } = await open(path)
      expect(getByRole('link', { name: '返回上一级' }).className).not.toContain('v-btn--active')
    }
  )

  it('updates the parent when navigating to another project', async () => {
    const { router, getByRole } = await open('/projects/project-a/topics/topic-b')
    await router.push('/projects/project-c/settings')
    expect(getByRole('link', { name: '返回上一级' }).getAttribute('href')).toBe('/projects/project-c')
  })
})

// 顶栏那颗 ← 原本只认路由自己声明的父级，而 `/projects/:projectId` 一个都没声明——
// 从小队点进一个项目之后，那颗按钮根本不出现，人只能靠底栏或左栏绕回去。写死的父级
// 描述的是一棵树，可项目是图上的一个点：小队、空间、左边的项目栏、别人贴的链接，
// 每一个都是合法入口，没有哪一个能当"那个"父级。所以入口是走进来的时候记下来的。
describe('走进一个项目之后，← 回得去', () => {
  const TEAM = '/teams/12'
  const PROJECT = '/projects/project-a'

  it('从小队走进项目，← 回小队', async () => {
    const view = await walk(TEAM, PROJECT + '/running')
    expect(back(view)?.getAttribute('href')).toBe(TEAM)
  })

  it('手机上项目的根是话题列表，← 同样回小队', async () => {
    widthIs(PHONE)
    const view = await walk(TEAM, PROJECT)
    expect(back(view)?.getAttribute('href')).toBe(TEAM)
  })

  // 在项目里翻一圈——看板、话题、再按 ← 回到根——每一跳都会经过记来路那条规则。
  // 任何一跳覆盖了入口，人就再也出不去这个项目。
  it('在项目里翻一圈，来路不被覆盖', async () => {
    const view = await walk(
      TEAM,
      PROJECT + '/running',
      PROJECT + '/topics/t1',
      PROJECT + '/settings',
      PROJECT + '/running'
    )
    expect(back(view)?.getAttribute('href')).toBe(TEAM)
  })

  it('点下去真能到小队', async () => {
    const { router, ...view } = await walk(TEAM, PROJECT + '/running')
    await fireEvent.click(back(view)!)
    await waitFor(() => expect(router.currentRoute.value.path).toBe(TEAM))
  })

  // 一个光秃秃的箭头说不出它去哪儿。名字是**离开小队那一刻**抓下来存的——等按 ←
  // 的时候再去取，那一页早就卸载了。
  it('说得出自己去哪儿', async () => {
    const view = await walk(TEAM, PROJECT + '/running')
    expect(back(view)?.getAttribute('title')).toBe('返回小队')
  })

  // 左栏切项目是同一层上的平移。少了这一条，B 项目的 ← 会指向 A 项目。
  it('从别的项目横切过来，不算走进来', async () => {
    const view = await walk(TEAM, PROJECT + '/running', '/projects/project-b/running')
    expect(back(view)).toBeNull()
  })

  it('每个项目各记各的来路', async () => {
    await walk(TEAM, PROJECT + '/running')
    cleanup() // 两次 render 都挂在 document.body 上，不清掉就会查到上一颗按钮
    const view = await open('/projects/project-b/running')
    expect(back(view)).toBeNull()
  })
})

// 贴链接直接打开、或者刷新之后，历史里没有"上一层"。这正是当初不敢用
// history.back() 的那件事——它会把人踢出整个应用。于是退到数据里的归属关系。
describe('没有来路时，退到项目所属的小队', () => {
  function ownedBy(team: number | null) {
    useWorkspaceStore().projects = [{ id: 'project-a', name: 'A', created_at: '', team_id: team }]
  }

  it('← 指向项目所属的小队', async () => {
    ownedBy(12)
    const view = await open('/projects/project-a/running')
    expect(back(view)?.getAttribute('href')).toBe('/teams/12')
  })

  // 历史遗留的行没有小队（新建项目一律会落到创建者的个人小队）。这种情况诚实的
  // 答案是没有上一层——不假装用户来过某个地方。
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

  // 记下来的来路可能已经失效（小队被删、路由改名）。存的是路由名+参数、跳之前
  // resolve 一次，就是为了此时能落回兜底，而不是把人送进一个 404。
  it('来路失效时落回所属小队', async () => {
    ownedBy(12)
    sessionStorage.setItem(
      'cheese:project-entry:project-a',
      JSON.stringify({ name: 'RouteThatNoLongerExists', params: {}, label: '哪儿' })
    )
    const view = await open('/projects/project-a/running')
    expect(back(view)?.getAttribute('href')).toBe('/teams/12')
  })
})

// 小队页上那个项目链接指的是 `/project/<id>`（单数），靠一条重定向落到
// `/projects/<id>`。「记来路」跑在重定向**之后**，所以它看见的 from 仍是小队——
// 要是它看见的是重定向的中间态，← 就会指回项目自己。这是从小队进项目的真实路径。
describe('走的是小队页上那条真实链接（带重定向）', () => {
  it('重定向不吃掉来路', async () => {
    widthIs(PHONE)
    const view = await walk('/teams/12', '/project/project-a')
    expect(back(view)?.getAttribute('href')).toBe('/teams/12')
  })
})
