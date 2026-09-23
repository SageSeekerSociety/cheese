/**
 * 项目首页「做出了什么」那一列最上面那一行：网站。
 *
 * 它从退役的「导出与发布」搬过来，那一页整页只有这一块。行上只剩地址、线上那一版
 * 和一个按钮；发布本身在一个对话框里，因为按下去之前必须看清两件事——发的是哪一版、
 * 发的是哪个入口。所以这一组钉的是：那条路上的每一步都还在（发哪一版、发哪个入口、
 * 版本变了怎么办），以及这一行该出现的时候才出现。
 */
import type { ProjectSite, ProjectSiteInfo } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import PublishedSite from './PublishedSite.vue'

import { ApiError } from '@/api'

vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  getProjectSite: vi.fn(),
  publishProjectSite: vi.fn(),
}))

const { getProjectSite, publishProjectSite } = await import('@/api')

const vuetify = createVuetify({ components, directives })

const REVISION = 'abc12345abc12345abc12345abc12345abc12345'
const NEW_REVISION = 'def67890def67890def67890def67890def67890'
const OLD_REVISION = '1111222211112222111122221111222211112222'

beforeAll(() => {
  Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.matchMedia) {
    globalThis.matchMedia = (() => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    })) as unknown as typeof globalThis.matchMedia
  }
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
})

function site(overrides: Partial<ProjectSite> = {}): ProjectSite {
  return {
    id: 'site-a',
    url: '/sites/project-a',
    source_revision: OLD_REVISION,
    directory: 'website',
    published_at: '2026-09-08T12:00:00Z',
    published_by: 'alice',
    ...overrides,
  }
}

function info(overrides: Partial<ProjectSiteInfo> = {}): ProjectSiteInfo {
  return {
    can_publish: true,
    source_revision: REVISION,
    candidates: [{ directory: 'website', entry_file: 'website/index.html' }],
    site: null,
    ...overrides,
  }
}

beforeEach(() => {
  vi.mocked(getProjectSite).mockReset().mockResolvedValue(info())
  vi.mocked(publishProjectSite).mockReset()
})
afterEach(cleanup)

function mount() {
  return render(PublishedSite, { props: { projectId: 'project-a' }, global: { plugins: [vuetify] } })
}

/** 行上那个按钮开对话框；对话框里那个「发布」才真的发。 */
async function openPublish(label: string) {
  await fireEvent.click(await screen.findByRole('button', { name: label }))
  await waitFor(() => expect(document.querySelector('.v-overlay .v-card')).toBeTruthy())
}

function dialogButton(label: string): HTMLButtonElement | undefined {
  return Array.from(document.querySelectorAll('.v-overlay button')).find((b) => b.textContent?.trim() === label) as
    | HTMLButtonElement
    | undefined
}

function dialogText(): string {
  return document.querySelector('.v-overlay .v-card')?.textContent ?? ''
}

describe('网站', () => {
  it('发布的是已采纳的那一版，发完摆出固定地址和线上版本', async () => {
    vi.mocked(publishProjectSite).mockResolvedValue(site({ source_revision: REVISION }))
    const { container } = mount()

    await openPublish('发布')
    // 对话框说清发的是哪一版、哪个入口，再由它里面那个「发布」真的发。
    expect(dialogText()).toContain(REVISION.slice(0, 8))
    expect(dialogText()).toContain('website/index.html')
    expect(screen.queryByRole('combobox')).toBeNull()
    await fireEvent.click(dialogButton('发布')!)

    await waitFor(() =>
      expect(publishProjectSite).toHaveBeenCalledWith('project-a', {
        directory: 'website',
        expected_source_revision: REVISION,
      })
    )
    const link = await waitFor(() => {
      const found = container.querySelector('a')
      expect(found).toBeTruthy()
      return found!
    })
    expect(link.getAttribute('href')).toBe('/sites/project-a')
    expect(container.textContent).toContain(REVISION.slice(0, 8))
    // 线上已经是这一版了，按钮因此按不下去。
    expect((screen.getByRole('button', { name: '发布更新' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('有好几个站点时，发的是人挑的那个入口', async () => {
    vi.mocked(getProjectSite).mockResolvedValue(
      info({
        candidates: [
          { directory: 'website', entry_file: 'website/index.html' },
          { directory: 'docs', entry_file: 'docs/index.html' },
        ],
      })
    )
    vi.mocked(publishProjectSite).mockResolvedValue(site({ directory: 'docs', source_revision: REVISION }))
    mount()

    await openPublish('发布')
    await fireEvent.mouseDown(await screen.findByRole('combobox'))
    await fireEvent.click(await screen.findByRole('option', { name: 'docs/index.html' }))
    await fireEvent.click(dialogButton('发布')!)

    await waitFor(() =>
      expect(publishProjectSite).toHaveBeenCalledWith('project-a', {
        directory: 'docs',
        expected_source_revision: REVISION,
      })
    )
  })

  it('已采纳的版本在这中间变了：摆出新的那一版，等人再点一次', async () => {
    vi.mocked(getProjectSite)
      .mockResolvedValueOnce(info())
      .mockResolvedValueOnce(info({ source_revision: NEW_REVISION }))
    vi.mocked(publishProjectSite)
      .mockRejectedValueOnce(new ApiError(409, '项目版本已变化'))
      .mockResolvedValueOnce(site({ source_revision: NEW_REVISION }))
    const { container } = mount()

    await openPublish('发布')
    await fireEvent.click(dialogButton('发布')!)
    await waitFor(() => expect(container.textContent).toContain('项目已采纳的版本变了，确认之后再发布'))
    expect(publishProjectSite).toHaveBeenCalledTimes(1)

    await openPublish('发布')
    expect(dialogText()).toContain(NEW_REVISION.slice(0, 8))
    await fireEvent.click(dialogButton('发布')!)
    await waitFor(() =>
      expect(publishProjectSite).toHaveBeenLastCalledWith('project-a', {
        directory: 'website',
        expected_source_revision: NEW_REVISION,
      })
    )
  })

  it('发布失败时线上那一份照旧在，地址也还在', async () => {
    vi.mocked(getProjectSite).mockResolvedValue(info({ site: site() }))
    vi.mocked(publishProjectSite).mockRejectedValue(new ApiError(502, '发布服务暂时不可用'))
    const { container } = mount()

    await openPublish('发布更新')
    await fireEvent.click(dialogButton('发布')!)

    expect(await screen.findByRole('alert')).toHaveProperty('textContent', '发布服务暂时不可用')
    expect(container.textContent).toContain(OLD_REVISION.slice(0, 8))
    expect(container.querySelector('a')?.getAttribute('href')).toBe('/sites/project-a')
  })

  it('发不了的时候说出原因，不谎称没有网站可发', async () => {
    vi.mocked(getProjectSite).mockResolvedValue(
      info({ site: site(), candidates: [], unavailable_reason: '网站托管尚未配置' })
    )
    mount()

    await openPublish('发布更新')
    expect(dialogText()).toContain('网站托管尚未配置')
    expect(dialogText()).not.toContain('项目已采纳的版本里没有可发布的网站')
    expect(dialogButton('发布')?.disabled).toBe(true)
  })

  it('不是负责人的人看得到地址，但没有发布这一下', async () => {
    vi.mocked(getProjectSite).mockResolvedValue(info({ can_publish: false, site: site() }))
    const { container } = mount()

    await waitFor(() => expect(container.querySelector('a')?.getAttribute('href')).toBe('/sites/project-a'))
    expect(screen.queryByRole('button', { name: '发布更新' })).toBeNull()
  })

  it('没发布过、也没有东西可发布时这一行不出现', async () => {
    vi.mocked(getProjectSite).mockResolvedValue(info({ candidates: [] }))
    const { container } = mount()

    await waitFor(() => expect(getProjectSite).toHaveBeenCalled())
    expect(container.querySelector('.site')).toBeNull()
  })

  it('读不到发布信息也只是这一行不出现，那一列照常', async () => {
    vi.mocked(getProjectSite).mockRejectedValue(new ApiError(503, '服务不可用'))
    const { container } = mount()

    await waitFor(() => expect(getProjectSite).toHaveBeenCalled())
    expect(container.querySelector('.site')).toBeNull()
    expect(container.textContent).not.toContain('服务不可用')
  })
})
