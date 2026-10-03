/** ArchiveProjectDialog：把项目名打一遍才放行，归档成了就离开这个项目，被拒时理由留在弹窗里。 */
import type { Component } from 'vue'

import { ref } from 'vue'
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

// replace 而不是 push：归档完再按回退键不该落回这个项目的设置页（见组件里那句注释）。
const replace = vi.fn()
vi.mock('vue-router', async () => ({
  ...(await vi.importActual<typeof import('vue-router')>('vue-router')),
  useRouter: () => ({ replace }),
}))

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
  replace.mockReset().mockResolvedValue(undefined)
})

async function mount() {
  const Host = {
    setup: () => ({ open: ref(false) }),
    components: { Dialog },
    template: `<div><button type="button" data-testid="open" @click="open = true">打开</button><Dialog v-model="open" project-id="p1" project-name="毕业设计" /></div>`,
  }
  const utils = render(Host as unknown as Component, { global: { plugins: [vuetify] } })
  await fireEvent.click(utils.getByTestId('open'))
  return utils
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
    await mount()
    await fireEvent.update(await screen.findByLabelText('输入项目名称「毕业设计」确认'), '毕业设计')
    await fireEvent.click(archiveButton())
    await waitFor(() => expect(archiveProject).toHaveBeenCalledWith('p1'))
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/'))
    expect(refreshProjects).toHaveBeenCalled()
  })

  it('被拒时弹窗不关，理由说在弹窗里', async () => {
    archiveProject.mockRejectedValue(new Error('只有项目所有者能归档或取消归档项目'))
    await mount()
    await fireEvent.update(await screen.findByLabelText('输入项目名称「毕业设计」确认'), '毕业设计')
    await fireEvent.click(archiveButton())
    expect(await screen.findByText('只有项目所有者能归档或取消归档项目')).toBeTruthy()
    expect(replace).not.toHaveBeenCalled()
  })
})
