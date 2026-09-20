import type { ProjectSite, ProjectSiteInfo } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getProjectSite = vi.fn()
const publishProjectSite = vi.fn()
vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    getProjectSite: (...args: unknown[]) => getProjectSite(...args),
    publishProjectSite: (...args: unknown[]) => publishProjectSite(...args),
  }
})

import { ApiError } from '../api'

import ProjectDeliveryView from './ProjectDeliveryView.vue'

const PROJECT = 'project-a'
const REVISION = 'abc12345abc12345abc12345abc12345abc12345'
const NEW_REVISION = 'def67890def67890def67890def67890def67890'
const OLD_REVISION = '1111222211112222111122221111222211112222'

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

function mountPage() {
  return render(ProjectDeliveryView, {
    props: { projectId: PROJECT },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

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

beforeEach(() => {
  getProjectSite.mockReset().mockResolvedValue(info())
  publishProjectSite.mockReset()
})
afterEach(cleanup)

describe('项目网站发布', () => {
  it('发布展示的已采纳版本，完成后显示固定链接和线上版本', async () => {
    publishProjectSite.mockResolvedValue(site({ source_revision: REVISION }))
    mountPage()

    expect(await screen.findByText(REVISION.slice(0, 8))).toBeTruthy()
    expect(screen.getByText('website/index.html')).toBeTruthy()
    expect(screen.queryByRole('combobox')).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: '发布为 Site' }))

    await screen.findByText('网站已发布')
    expect(publishProjectSite).toHaveBeenCalledWith(PROJECT, {
      directory: 'website',
      expected_source_revision: REVISION,
    })
    expect(screen.getByRole('link', { name: '打开网站' }).getAttribute('href')).toBe('/sites/project-a')
    expect(screen.getByText('网站已是当前版本')).toBeTruthy()
    expect((screen.getByRole('button', { name: '发布更新' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('多个网站时，发布用户选择的入口', async () => {
    getProjectSite.mockResolvedValue(
      info({
        candidates: [
          { directory: 'website', entry_file: 'website/index.html' },
          { directory: 'docs', entry_file: 'docs/index.html' },
        ],
      })
    )
    publishProjectSite.mockResolvedValue(site({ directory: 'docs', source_revision: REVISION }))
    mountPage()

    await fireEvent.mouseDown(await screen.findByRole('combobox'))
    await fireEvent.click(await screen.findByRole('option', { name: 'docs/index.html' }))
    await fireEvent.click(screen.getByRole('button', { name: '发布为 Site' }))
    await waitFor(() => {
      expect(publishProjectSite).toHaveBeenCalledWith(PROJECT, {
        directory: 'docs',
        expected_source_revision: REVISION,
      })
    })
  })

  it('更新失败时保留原来的线上版本和访问链接', async () => {
    getProjectSite.mockResolvedValue(info({ site: site() }))
    publishProjectSite.mockRejectedValue(new ApiError(502, '发布服务暂时不可用'))
    mountPage()

    await fireEvent.click(await screen.findByRole('button', { name: '发布更新' }))
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', '发布服务暂时不可用')
    expect(screen.getByText(OLD_REVISION.slice(0, 8))).toBeTruthy()
    expect(screen.getByRole('link', { name: '打开网站' }).getAttribute('href')).toBe('/sites/project-a')
  })

  it('版本变化后展示新版本，等用户再次点击才重新发布', async () => {
    getProjectSite.mockResolvedValueOnce(info()).mockResolvedValueOnce(info({ source_revision: NEW_REVISION }))
    publishProjectSite.mockRejectedValueOnce(new ApiError(409, '项目版本已变化'))
    publishProjectSite.mockResolvedValueOnce(site({ source_revision: NEW_REVISION }))
    mountPage()

    await fireEvent.click(await screen.findByRole('button', { name: '发布为 Site' }))
    expect(await screen.findByText('项目版本已变化，请确认当前版本后重新发布')).toBeTruthy()
    expect(screen.getByText(NEW_REVISION.slice(0, 8))).toBeTruthy()
    expect(publishProjectSite).toHaveBeenCalledTimes(1)

    await fireEvent.click(screen.getByRole('button', { name: '发布为 Site' }))
    await screen.findByText('网站已发布')
    expect(publishProjectSite).toHaveBeenLastCalledWith(PROJECT, {
      directory: 'website',
      expected_source_revision: NEW_REVISION,
    })
  })

  it('普通成员可以打开已有网站，不能发布更新', async () => {
    getProjectSite.mockResolvedValue(info({ can_publish: false, site: site() }))
    mountPage()

    expect(await screen.findByRole('link', { name: '打开网站' })).toBeTruthy()
    expect(screen.getByText('由项目负责人发布网站')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '发布更新' })).toBeNull()
  })

  it('没有网站时说明先准备并验收，阻止空发布', async () => {
    getProjectSite.mockResolvedValue(info({ candidates: [] }))
    mountPage()

    expect(await screen.findByText('项目已采纳版本中暂无可发布的网站，请让芝士准备静态网站并提交验收')).toBeTruthy()
    expect((screen.getByRole('button', { name: '发布为 Site' }) as HTMLButtonElement).disabled).toBe(true)
    expect(publishProjectSite).not.toHaveBeenCalled()
  })

  it('显示环境不可发布的原因，不误报为没有网站', async () => {
    getProjectSite.mockResolvedValue(info({ candidates: [], unavailable_reason: '网站托管尚未配置' }))
    mountPage()

    expect(await screen.findByText('网站托管尚未配置')).toBeTruthy()
    expect(screen.queryByText(/请让芝士准备静态网站/)).toBeNull()
    expect((screen.getByRole('button', { name: '发布为 Site' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('刷新失败时继续显示线上网站，但不能使用旧发布信息提交', async () => {
    getProjectSite.mockResolvedValueOnce(info({ site: site() })).mockRejectedValueOnce(new ApiError(503, '服务不可用'))
    mountPage()
    await screen.findByRole('link', { name: '打开网站' })

    await fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', '服务不可用')
    expect(screen.getByRole('link', { name: '打开网站' })).toBeTruthy()
    expect((screen.getByRole('button', { name: '发布更新' }) as HTMLButtonElement).disabled).toBe(true)
  })
})
