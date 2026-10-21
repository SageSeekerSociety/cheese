// 空间资料库页：点开就预览（不算一次下载），下载、改可见范围、移除都走判权限的路由，
// 服务端说不能管就没有管理的入口。引用计数只给能管的人看，那是移除之前的依据。
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

// Vuetify 的浮层要读 visualViewport，而测试环境没有：不补上挂不起来，
// 测到的就成了「点开什么也没有」。
beforeAll(() => {
  // 浮层定位还要读 devicePixelRatio，测试环境也没有。
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
})
afterAll(() => vi.unstubAllGlobals())

const listMaterials = vi.fn()
const uploadMaterial = vi.fn()
const updateMaterialVisibility = vi.fn()
const deleteMaterial = vi.fn()
const downloadMaterial = vi.fn()

vi.mock('@/api/spaces', () => ({
  SpacesApi: {
    listMaterials: (...a: unknown[]) => listMaterials(...a),
    uploadMaterial: (...a: unknown[]) => uploadMaterial(...a),
    updateMaterialVisibility: (...a: unknown[]) => updateMaterialVisibility(...a),
    deleteMaterial: (...a: unknown[]) => deleteMaterial(...a),
    downloadMaterial: (...a: unknown[]) => downloadMaterial(...a),
  },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { toast } from 'vuetify-sonner'

import SpaceLibrary from './SpaceLibrary.vue'

import i18n, { setLocale } from '@/i18n'

const SPACE_ID = 11

function material(over: Record<string, unknown> = {}) {
  return {
    id: 7,
    name: '第三周讲义.pdf',
    type: 'file',
    visibility: 'members',
    size: 2048,
    mime: 'application/pdf',
    uploaderId: 3,
    uploaderName: '陈老师',
    createdAt: Date.UTC(2026, 9, 1),
    downloadCount: 2,
    ...over,
  }
}

function listed(over: Record<string, unknown> = {}, canManage = true) {
  return { materials: [material(over)], canManage }
}

/** 挂起来并等到某一串出现在屏幕上 —— 用例各自关心的那串不一样，
 *  钉不住「加载完了」就只在等一个超时。 */
async function mount(expectText = '第三周讲义.pdf') {
  setLocale('zh-CN')
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/spaces/:spaceId/library', component: SpaceLibrary }],
  })
  await router.push(`/spaces/${SPACE_ID}/library`)
  await router.isReady()
  const utils = render(defineComponent({ render: () => h(RouterView) }), {
    global: { plugins: [createVuetify({ components, directives }), router, i18n] },
  })
  await waitFor(() => expect(document.body.textContent).toContain(expectText))
  return { ...utils, router }
}

/** 打开某一行的 ⋯ 菜单。 */
async function openMenu(name = '第三周讲义.pdf') {
  await fireEvent.click(screen.getByLabelText(`${name} 的操作`))
}

describe('空间资料库页', () => {
  beforeEach(() => {
    listMaterials.mockImplementation(async () => listed())
    uploadMaterial.mockImplementation(async () => ({ material: material() }))
    updateMaterialVisibility.mockImplementation(async () => ({ material: material() }))
    deleteMaterial.mockImplementation(async () => ({}))
    downloadMaterial.mockImplementation(async () => new Blob([new TextEncoder().encode('讲义正文')]))
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.clearAllMocks()
  })

  it('每一行写上传人和大小', async () => {
    await mount()
    expect(document.body.textContent).toContain('陈老师')
    expect(document.body.textContent).toContain('2.0 KB')
  })

  it('点一行就在旁边预览，地址记着是哪一份，预览不算一次下载', async () => {
    downloadMaterial.mockImplementation(async () => new Blob([new TextEncoder().encode('第一章 进程')]))
    listMaterials.mockImplementation(async () => listed({ name: '讲义.txt' }))
    const { router } = await mount('讲义.txt')

    await fireEvent.click(screen.getByText('讲义.txt'))

    await waitFor(() => expect(router.currentRoute.value.query.file).toBe('7'))
    await waitFor(() => expect(document.body.textContent).toContain('第一章 进程'))
    expect(downloadMaterial).toHaveBeenCalledWith(SPACE_ID, 7, true)
  })

  it('下载走判权限的那条路由，存下来用资料自己的名字', async () => {
    const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:material')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.download).toBe('第三周讲义.pdf')
    })
    await mount()

    await openMenu()
    await fireEvent.click(await screen.findByText('下载'))

    await waitFor(() => expect(downloadMaterial).toHaveBeenCalledWith(SPACE_ID, 7))
    expect(create).toHaveBeenCalled()
    expect(click).toHaveBeenCalledTimes(1)
  })

  it('改可见范围在 ⋯ 里，改完按服务端回来的那一版重画', async () => {
    await mount()
    listMaterials.mockImplementation(async () => listed({ visibility: 'admins' }))

    await openMenu()
    await fireEvent.click(await screen.findByText('设为仅管理员可见'))

    await waitFor(() => expect(updateMaterialVisibility).toHaveBeenCalledWith(SPACE_ID, 7, 'admins'))
    await waitFor(() => expect(document.body.textContent).toContain('仅管理员'))
  })

  it('移除先问一句，答应了才发请求', async () => {
    await mount()

    await openMenu()
    await fireEvent.click(await screen.findByText('移除'))
    expect(deleteMaterial).not.toHaveBeenCalled()
    await screen.findByText('移除《第三周讲义.pdf》')

    const buttons = screen.getAllByRole('button', { name: '移除' })
    await fireEvent.click(buttons[buttons.length - 1])
    await waitFor(() => expect(deleteMaterial).toHaveBeenCalledWith(SPACE_ID, 7))
  })

  it('有辅导设置引用时，移除前说出会影响几处', async () => {
    listMaterials.mockImplementation(async () => listed({ usedByCount: 2 }))
    await mount()

    await openMenu()
    await fireEvent.click(await screen.findByText('移除'))
    await screen.findByText('2 处辅导设置引用了这份资料，移除后 AI 队友将读不到它')
  })

  it('服务端说不能管，就没有上传、改可见范围和移除', async () => {
    listMaterials.mockImplementation(async () => listed({}, false))
    await mount()

    expect(screen.queryByText('上传资料')).toBeNull()
    await openMenu()
    await screen.findByText('下载')
    expect(screen.queryByText('移除')).toBeNull()
    expect(screen.queryByText('设为仅管理员可见')).toBeNull()
  })

  it('打开一份时，能管的人看得见下载次数和引用数，成员看不到', async () => {
    listMaterials.mockImplementation(async () => listed({ usedByCount: 3 }))
    let view = await mount()
    await fireEvent.click(screen.getByText('第三周讲义.pdf'))
    await waitFor(() => expect(document.body.textContent).toContain('3 处辅导设置引用'))
    expect(document.body.textContent).toContain('2 次下载')
    view.unmount()

    listMaterials.mockImplementation(async () => listed({ usedByCount: 3 }, false))
    view = await mount()
    await fireEvent.click(screen.getByText('第三周讲义.pdf'))
    await waitFor(() => expect(view.router.currentRoute.value.query.file).toBe('7'))
    expect(document.body.textContent).not.toContain('辅导设置引用')
    expect(document.body.textContent).not.toContain('次下载')
  })

  it('搜不到时说「暂无匹配的资料」，一键清除筛选', async () => {
    await mount()

    await fireEvent.update(screen.getByLabelText('搜索名称'), '期末')
    await screen.findByText('暂无匹配的资料')
    await fireEvent.click(screen.getByText('清除筛选'))
    await screen.findByText('第三周讲义.pdf')
  })

  it('传不上去时报错，上传框还开着', async () => {
    uploadMaterial.mockRejectedValue(new Error('boom'))
    await mount()

    await fireEvent.click(screen.getByText('上传资料'))
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    await fireEvent.change(input, {
      target: { files: [new File(['x'], 'a.pdf', { type: 'application/pdf' })] },
    })
    await fireEvent.click(screen.getByRole('button', { name: '上传' }))

    await waitFor(() => expect(uploadMaterial).toHaveBeenCalledTimes(1))
    expect(toast.error).toHaveBeenCalled()
    expect(screen.getByText('可见范围')).toBeTruthy()
  })
})
