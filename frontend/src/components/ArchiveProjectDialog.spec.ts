/** ArchiveProjectDialog：把项目名打一遍才放行，归档成了就离开这个项目，被拒时理由留在弹窗里。 */
import type { Component } from 'vue'

import { ref } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const archiveProject = vi.fn()
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return { ...actual, archiveProject: (...a: unknown[]) => archiveProject(...a) }
})

const refreshProjects = vi.fn()
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => ({ refreshProjects }) }))

const Blank = { render: () => null }

/** 归档完回首页，而且是 replace 而不是 push：再按回退键不该落回一个已经不在清单里的
 *  项目（见组件里那句注释）。组件跳转走 `composables/useNavigation`，路由从应用上拿，
 *  所以这里装的必须是真路由 —— 它读的就是这一个。 */
function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'Home', component: Blank },
      { path: '/projects/:projectId/settings', name: 'project-settings', component: Blank },
      { path: '/:any(.*)*', component: Blank },
    ],
  })
}

import ArchiveProjectDialog from './ArchiveProjectDialog.vue'

import { setLocale } from '@/i18n'

const Dialog = ArchiveProjectDialog as unknown as Component
let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.visualViewport) {
    ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = {
      width: 1024,
      height: 768,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent: () => false,
    }
  }
})

afterEach(cleanup)

beforeEach(() => {
  setLocale('zh-CN')
  archiveProject.mockReset().mockResolvedValue({})
  refreshProjects.mockReset().mockResolvedValue(undefined)
})

async function mount() {
  // 从项目设置页开始：归档成功要把这一格换成首页，从别处出发就看不出它换了。
  const router = makeRouter()
  await router.push('/projects/p1/settings')
  const Host = {
    setup: () => ({ open: ref(false) }),
    components: { Dialog },
    template: `<div><button type="button" data-testid="open" @click="open = true">打开</button><Dialog v-model="open" project-id="p1" project-name="毕业设计" /></div>`,
  }
  const utils = render(Host as unknown as Component, { global: { plugins: [vuetify, router] } })
  // 挂上之后才钉：装路由时 vue-router 自己会往初始位置走一步，那一步不是组件跳的。
  const push = vi.spyOn(router, 'push')
  const replace = vi.spyOn(router, 'replace')
  await fireEvent.click(utils.getByTestId('open'))
  return { ...utils, router, push, replace }
}

function archiveButton(): HTMLButtonElement {
  return screen.getByRole('button', { name: '归档' }) as HTMLButtonElement
}

describe('ArchiveProjectDialog', () => {
  it('项目名没打对之前不能归档', async () => {
    await mount()
    const field = await screen.findByLabelText('输入项目名称「毕业设计」确认')
    expect(archiveButton().disabled).toBe(true)
    await fireEvent.update(field, '毕业')
    expect(archiveButton().disabled).toBe(true)
    await fireEvent.click(archiveButton())
    expect(archiveProject).not.toHaveBeenCalled()
  })

  it('打对名字之后归档，刷新项目清单并回到首页', async () => {
    const { router, push, replace } = await mount()
    await fireEvent.update(await screen.findByLabelText('输入项目名称「毕业设计」确认'), '毕业设计')
    await fireEvent.click(archiveButton())
    await waitFor(() => expect(archiveProject).toHaveBeenCalledWith('p1'))
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/'))
    // 换掉这一格，不在身后压回一条项目设置页。
    expect(replace).toHaveBeenCalledWith('/')
    expect(push).not.toHaveBeenCalled()
    expect(refreshProjects).toHaveBeenCalled()
  })

  it('被拒时弹窗不关，理由说在弹窗里', async () => {
    archiveProject.mockRejectedValue(new Error('只有项目所有者能归档或取消归档项目'))
    const { replace } = await mount()
    await fireEvent.update(await screen.findByLabelText('输入项目名称「毕业设计」确认'), '毕业设计')
    await fireEvent.click(archiveButton())
    expect(await screen.findByText('只有项目所有者能归档或取消归档项目')).toBeTruthy()
    expect(replace).not.toHaveBeenCalled()
  })
})
