// 资料库页：看得见就下得下来，改档就地生效，撤下来点两下才是真的。
//
// 这几条钉的是「界面上看到的」与「发出去的请求」：下载走的是判权限的那条路由
// （清单里刻意没有 url），改档之后拿服务端回来的那一版重画（不是本地猜），移除
// 点两下才发请求，而且**服务端说不能管就没有那几个入口** —— 后端也挡（403），
// 但入口不该先摆在那里让人点。引用计数那一格也一样：它是「撤之前心里有数」用的，
// 只有能管的人看得见，判据是键在不在，不是数值多少。
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

vi.mock('@/network/api/spaces', () => ({
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

import SpaceMaterials from './SpaceMaterials.vue'

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
    createdAt: Date.UTC(2026, 9, 1),
    downloadCount: 2,
    ...over,
  }
}

function listed(over: Record<string, unknown> = {}, canManage = true) {
  return { data: { materials: [material(over)], canManage } }
}

/** 挂起来并等到某一串出现在屏幕上 —— 用例各自关心的那串不一样，
 *  钉不住「加载完了」就只在等一个超时。 */
async function mount(expectText = '第三周讲义.pdf') {
  setLocale('zh-CN')
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/spaces/:spaceId/manage/settings/materials', component: SpaceMaterials }],
  })
  await router.push(`/spaces/${SPACE_ID}/manage/settings/materials`)
  await router.isReady()
  const utils = render(defineComponent({ render: () => h(RouterView) }), {
    global: { plugins: [createVuetify({ components, directives }), router, i18n] },
  })
  await waitFor(() => expect(document.body.textContent).toContain(expectText))
  return utils
}

describe('资料库页', () => {
  beforeEach(() => {
    listMaterials.mockImplementation(async () => listed())
    uploadMaterial.mockImplementation(async () => ({ data: { material: material() } }))
    updateMaterialVisibility.mockImplementation(async () => ({ data: { material: material() } }))
    deleteMaterial.mockImplementation(async () => ({ data: {} }))
    downloadMaterial.mockImplementation(async () => new Blob([new Uint8Array([1, 2, 3])]))
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.clearAllMocks()
  })

  it('下载走的是判权限的那条路由，字节落成文件时用的是素材自己的名字', async () => {
    // 取字节与落盘这两步在测试环境里都要挡一下，否则「点了」与「存下来了」
    // 之间没有可断言的东西。
    const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:material')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.download).toBe('第三周讲义.pdf')
    })

    await mount()
    expect(document.body.textContent).toContain('所有成员')
    expect(document.body.textContent).toContain('2.00 KB')

    await fireEvent.click(screen.getByText('下载'))

    await waitFor(() => expect(downloadMaterial).toHaveBeenCalledWith(SPACE_ID, 7))
    expect(create).toHaveBeenCalledTimes(1)
    expect(click).toHaveBeenCalledTimes(1)
  })

  it('改可见范围之后就地把这一行重画成新档，按服务端回来的那一版', async () => {
    await mount()

    // 服务端在 PATCH 之后会把这一行换成「仅管理员」。
    listMaterials.mockImplementation(async () => listed({ visibility: 'admins' }))

    await fireEvent.click(screen.getByText('所有成员'))
    await fireEvent.click(await screen.findByText('仅管理员'))

    await waitFor(() => expect(updateMaterialVisibility).toHaveBeenCalledWith(SPACE_ID, 7, 'admins'))
    await waitFor(() => expect(listMaterials).toHaveBeenCalledTimes(2))
    expect(document.body.textContent).toContain('仅管理员')
  })

  it('撤下来要点两下，第一下只问不改', async () => {
    await mount()

    await fireEvent.click(screen.getByText('移除'))
    expect(deleteMaterial).not.toHaveBeenCalled()

    await fireEvent.click(screen.getByText('确认移除'))
    await waitFor(() => expect(deleteMaterial).toHaveBeenCalledWith(SPACE_ID, 7))
  })

  it('服务端说不能管，就不摆上传、改档、移除那几个入口', async () => {
    listMaterials.mockImplementation(async () => listed({}, false))
    await mount()

    // 清单还在（成员看得到「所有成员」那一档），下载也还在 —— 拿不到的只是管理入口。
    expect(screen.getByText('下载')).toBeTruthy()
    expect(screen.queryByText('上传资料')).toBeNull()
    expect(screen.queryByText('移除')).toBeNull()
    expect(screen.queryByText('所有成员')).toBeNull()
  })

  it('能管的人看得见「被几处引用」，撤之前心里有数', async () => {
    listMaterials.mockImplementation(async () => listed({ usedByCount: 3 }))
    await mount()

    expect(document.body.textContent).toContain('被 3 处引用')
  })

  it('一处也没列着就直说「未被引用」，不摆一个 0', async () => {
    listMaterials.mockImplementation(async () => listed({ usedByCount: 0 }))
    await mount()

    expect(document.body.textContent).toContain('未被引用')
  })

  it('成员那一侧不摆这一格，哪怕这一格被塞了过来', async () => {
    // 真实的服务端对成员不补这个键（不是补 0）。这里故意塞一个进去，钉的是**判据
    // 是 canManage，不只是「键在不在」** —— 后端哪天改了形状，界面也不该把「被几处
    // 引用」摆给一个改不了档、删不掉的人看。
    listMaterials.mockImplementation(async () => listed({ usedByCount: 3 }, false))
    await mount()

    expect(document.body.textContent).not.toContain('未被引用')
    expect(document.body.textContent).not.toContain('处引用')
  })

  it('传不上去时说清楚，界面不装作传成功了', async () => {
    uploadMaterial.mockRejectedValue(new Error('boom'))
    await mount()

    await fireEvent.click(screen.getByText('上传资料'))
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    await fireEvent.change(input, {
      target: { files: [new File(['x'], 'a.pdf', { type: 'application/pdf' })] },
    })
    await fireEvent.click(screen.getAllByText('上传资料')[1])

    await waitFor(() => expect(uploadMaterial).toHaveBeenCalledTimes(1))
    expect(toast.error).toHaveBeenCalled()
    // 还停在表单上，文件也还在：重试不用再选一次。
    expect(document.body.textContent).toContain('上传一份资料')
  })
})
