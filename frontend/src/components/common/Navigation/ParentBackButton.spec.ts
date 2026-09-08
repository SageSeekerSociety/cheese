import type { RouteRecordRaw } from 'vue-router'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import { VBtn, VIcon } from 'vuetify/components'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it } from 'vitest'

import ParentBackButton from './ParentBackButton.vue'

import HomeRoutes from '@/router/home'
import SpacesRoutes from '@/router/spaces'
import TeamsRoutes from '@/router/teams'
import UserRoutes from '@/router/user'
import { workspaceRoutes } from '@/router/workspaceRoutes'

// Keep the production route hierarchy and redirects, without mounting page data loaders.
function withoutViews(record: RouteRecordRaw): RouteRecordRaw {
  return {
    ...record,
    components: { default: { template: '<div />' } },
    children: record.children?.map(withoutViews),
  } as RouteRecordRaw
}

afterEach(cleanup)

async function open(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [HomeRoutes, SpacesRoutes, TeamsRoutes, UserRoutes, workspaceRoutes].map(withoutViews),
  })
  await router.push(path)
  await router.isReady()
  const view = render(ParentBackButton, {
    global: { plugins: [router, createVuetify({ components: { VBtn, VIcon } })] },
  })
  return { router, ...view }
}

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
