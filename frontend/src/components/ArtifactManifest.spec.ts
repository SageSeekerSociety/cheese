/**
 * 项目首页上「做出了什么」那一列的内容。
 *
 * 列的框和列头在 RunningWorkView 那边（三列任务加这一列共用 `.board-col`），所以
 * 这里只钉它里面的东西：一项一行、点得进去、空了说「暂无产物」、件数报上去，以及
 * 那三件只有人做得了的判断（改名、合并、删除）。
 */
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ArtifactManifest from './ArtifactManifest.vue'

vi.mock('../api', () => ({
  listProjectArtifacts: vi.fn(),
  renameProjectArtifact: vi.fn(),
  mergeProjectArtifacts: vi.fn(),
  deleteProjectArtifact: vi.fn(),
  // 网站钉在这一列最上面，但它是另一个组件的事（PublishedSite.spec.ts 钉它）。
  // 这里让它读不到发布信息，那一行因此不画。
  getProjectSite: vi.fn(async () => {
    throw new Error('no site in this test')
  }),
  publishProjectSite: vi.fn(),
  ApiError: class extends Error {},
}))

const { deleteProjectArtifact, listProjectArtifacts, mergeProjectArtifacts, renameProjectArtifact } = await import(
  '../api'
)

const vuetify = createVuetify({ components, directives })

// 一行产物点进去是它自己那一页，所以这里要一个真路由：没有它，那一行渲染成一个
// 解析不出来的 router-link，测到的「名字在不在」就与「点得进去」无关了。
const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/', name: 'home', component: defineComponent({ setup: () => () => h('div') }) },
    {
      path: '/projects/:projectId/artifacts/:artifactId',
      name: 'project-artifact',
      component: defineComponent({ setup: () => () => h('div') }),
    },
  ],
})

afterEach(cleanup)

// 菜单和对话框都是 VOverlay，而 happy-dom 没有 visualViewport：不补这几样，弹出
// 来的东西根本挂不上去，测到的就成了「点了没反应」。
beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!('devicePixelRatio' in globalThis)) {
    Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })
  }
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
})

const REPORT = {
  id: 'a1',
  name: '结题报告',
  about: '交给甲方的最终报告',
  version: 3,
  delivered_at: '2026-09-19T10:00:00Z',
}
const SITE = { id: 'a2', name: '项目官网', about: '', version: 0, delivered_at: null }

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listProjectArtifacts).mockResolvedValue({ data: [REPORT, SITE], total: 2 })
  vi.mocked(renameProjectArtifact).mockResolvedValue({ id: 'a1', name: '结题报告（终稿）' })
  vi.mocked(mergeProjectArtifacts).mockResolvedValue({ id: 'a1', name: '结题报告' })
  vi.mocked(deleteProjectArtifact).mockResolvedValue({ deleted: true })
})

function mount() {
  return render(ArtifactManifest, { props: { projectId: 'p1' }, global: { plugins: [vuetify, router] } })
}

function menuItem(title: string): HTMLElement | undefined {
  return Array.from(document.querySelectorAll('.v-list-item-title')).find((n) => n.textContent?.trim() === title)
    ?.parentElement as HTMLElement | undefined
}

function dialogButton(label: string): HTMLElement | undefined {
  return Array.from(document.querySelectorAll('.v-overlay button')).find((b) => b.textContent?.trim() === label) as
    | HTMLElement
    | undefined
}

async function openMenu(container: Element, name: string) {
  const activator = container.querySelector<HTMLElement>(`button[aria-label="${name} 的操作"]`)
  expect(activator).toBeTruthy()
  await fireEvent.click(activator!)
}

describe('做出了什么', () => {
  it('一项一行，交付过的显示第几版，还没落地的说尚未交付', async () => {
    const { container } = mount()

    await waitFor(() => expect(container.textContent).toContain('结题报告'))
    const text = container.textContent ?? ''
    expect(text).toContain('第 3 版')
    expect(text).toContain('项目官网')
    expect(text).toContain('尚未交付')
  })

  it('一行点进去是这一项自己那一页', async () => {
    const { container } = mount()

    await waitFor(() => expect(container.textContent).toContain('结题报告'))
    const link = Array.from(container.querySelectorAll('a')).find((a) => a.textContent?.trim() === '结题报告')
    expect(link?.getAttribute('href')).toBe('/projects/p1/artifacts/a1')
  })

  it('一项都没有的时候这一列留着，自己说「暂无产物」', async () => {
    // 它曾经是整块隐藏，那是它还摞在板上面的时候。现在它是一列，凭空消失会让整个
    // 网格错位，而板的规矩是位置本身就是信息。
    vi.mocked(listProjectArtifacts).mockResolvedValue({ data: [], total: 0 })

    const { container } = mount()

    await waitFor(() => expect(container.textContent).toContain('暂无产物'))
    expect(container.querySelector('.made')).not.toBeNull()
  })

  it('件数报给列头 —— 列头在板那边，件数在这边', async () => {
    const { emitted } = mount()

    await waitFor(() => expect(emitted().count).toBeTruthy())
    expect(emitted().count.at(-1)).toEqual([2])
  })

  it('读不到清单时这一列显示成空的，不把首页变成一条错误', async () => {
    vi.mocked(listProjectArtifacts).mockRejectedValue(new Error('服务不可用'))

    const { container, emitted } = mount()

    await waitFor(() => expect(container.textContent).toContain('暂无产物'))
    expect(container.textContent).not.toContain('服务不可用')
    expect(emitted().count.at(-1)).toEqual([0])
  })

  it('改名改的是这一项，改完重新读一次清单', async () => {
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('结题报告'))

    await openMenu(container, '结题报告')
    await fireEvent.click(menuItem('重命名')!)
    const field = document.querySelector<HTMLInputElement>('.v-overlay input')
    expect(field?.value).toBe('结题报告')
    await fireEvent.update(field!, '结题报告（终稿）')
    await fireEvent.click(dialogButton('保存')!)

    await waitFor(() => expect(renameProjectArtifact).toHaveBeenCalledWith('p1', 'a1', '结题报告（终稿）'))
    expect(listProjectArtifacts).toHaveBeenCalledTimes(2)
  })

  it('合并的目标是清单上别的那几项，不包括它自己', async () => {
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('结题报告'))

    await openMenu(container, '结题报告')
    await fireEvent.click(menuItem('合并到…')!)

    await waitFor(() => expect(document.querySelector('.v-overlay .v-select')).toBeTruthy())
    const dialog = document.querySelector('.v-overlay .v-card')
    expect(dialog?.textContent).toContain('合并《结题报告》')
    // 选完哪一项才能按「合并」：没有目标的合并不是一个动作。
    expect(dialogButton('合并')?.hasAttribute('disabled')).toBe(true)

    const field = document.querySelector<HTMLInputElement>('.v-overlay .v-select input')
    await fireEvent.keyDown(field!, { key: 'ArrowDown' })

    await waitFor(() => expect(menuItem('项目官网')).toBeTruthy())
    // 自己不在候选里：合并到自己不是一件事。
    expect(menuItem('结题报告')).toBeUndefined()
    await fireEvent.click(menuItem('项目官网')!)
    await waitFor(() => expect(dialogButton('合并')?.hasAttribute('disabled')).toBe(false))
    await fireEvent.click(dialogButton('合并')!)

    await waitFor(() => expect(mergeProjectArtifacts).toHaveBeenCalledWith('p1', 'a1', 'a2'))
  })

  it('删除要先确认，确认之后这一项从清单上去掉', async () => {
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('项目官网'))

    await openMenu(container, '项目官网')
    await fireEvent.click(menuItem('删除')!)

    await waitFor(() => expect(document.body.textContent).toContain('删除《项目官网》'))
    expect(deleteProjectArtifact).not.toHaveBeenCalled()
    await fireEvent.click(dialogButton('删除')!)

    await waitFor(() => expect(deleteProjectArtifact).toHaveBeenCalledWith('p1', 'a2'))
  })
})
