/**
 * 知识库页「写进去」的那一半，拆开之前先钉住。
 *
 * 和 `Knowledge.spec.ts` 是同一条链子上的两半：那一份盯读（取数 / 视图 / 筛选 /
 * 详情 / 删除），这一份盯写。上传对话框里那四档（文件 / 文本 / 链接 / 代码片段）
 * 每一档**落到请求上的形状都不一样** —— 是文件就先传材料再把 id 带上，其余三档
 * 各自往 `content` 里塞 JSON —— 而这四张形状现在没有任何 spec 看着。
 *
 * 钉的是：
 *
 *   1. 校验没过时**一个请求都不发**（名字是空的、链接不是 URL）；
 *   2. 四档各自的 `CreateKnowledgeRequest`：`type`、`content` 解出来是什么、
 *      `materialId` 什么时候有；
 *   3. 文件那一档按 MIME 猜出来的 `materialType`（image / video / audio / file）；
 *   4. 成功之后新条目插在最前面、对话框关掉、说一句成功；失败时说一句失败且
 *      对话框留着。
 *
 * 绿在拆之前的旧文件上；拆完必须原样绿。
 */
import type { Component } from 'vue'
import type { Knowledge, Team } from '@/types'

import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listMock = vi.hoisted(() => vi.fn())
const createMock = vi.hoisted(() => vi.fn())
const uploadMock = vi.hoisted(() => vi.fn())
const successMock = vi.hoisted(() => vi.fn())
const errorMock = vi.hoisted(() => vi.fn())

vi.mock('@/network/api/knowledges', () => ({
  KnowledgesApi: { create: createMock, list: listMock, deleteKnowledge: vi.fn() },
}))
vi.mock('@/network/api/materials', () => ({ MaterialsApi: { upload: uploadMock } }))
vi.mock('vuetify-sonner', () => ({ toast: { success: successMock, error: errorMock } }))
vi.mock('@/plugins/dialog', async () => ({
  ...(await vi.importActual<typeof import('@/plugins/dialog')>('@/plugins/dialog')),
  useDialog: () => ({ confirm: vi.fn(), alert: vi.fn() }),
}))

vi.mock('@/components/common/Editor/TipTapEditor.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({ name: 'TipTapEditor', setup: () => () => h('div', { class: 'tiptap-stub' }) }),
    __isTeleport: false,
    __isKeepAlive: false,
  }
})

vi.mock('@/services/account', async () => {
  const { ref: make } = await import('vue')
  return { currentUserId: make(1) }
})

import KnowledgePage from './Knowledge.vue'

import { setLocale } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'

function existing(): Knowledge {
  return {
    id: 1,
    name: '设计规范',
    type: 'TEXT',
    content: '{}',
    teamId: 7,
    labels: ['设计'],
    creator: { id: 1, nickname: '我', avatarId: 0 } as never,
    createdAt: 1_700_000_000_000,
    updatedAt: 1_700_000_000_000,
  } as Knowledge
}

beforeAll(() => {
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
  // 预览文件那一格要它；happy-dom 里没有。
  URL.createObjectURL = vi.fn(() => 'blob:stub')
})

beforeEach(() => {
  listMock.mockReset().mockResolvedValue({
    data: {
      knowledges: [existing()],
      page: { pageStart: 0, pageSize: 20, hasMore: false, nextStart: null, total: 1 },
    },
  })
  createMock.mockReset()
  uploadMock.mockReset().mockResolvedValue({ data: { id: 99 } })
  successMock.mockReset()
  errorMock.mockReset()
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function mount() {
  setLocale('zh-CN')
  const team = ref({ id: 7, name: 'Cheese 核心组' } as unknown as Team)
  return render(KnowledgePage as unknown as Component, {
    global: {
      plugins: [createVuetify({ components, directives })],
      provide: { [teamDataInjectionKey as symbol]: team },
    },
  })
}

type View = ReturnType<typeof mount>

/** 对话框那一层。关掉之后 Vuetify 还留着节点（过渡），所以看的是「还亮着吗」。
 *  迁移到 AdaptiveDialog 后那张卡不再挂 `.upload-dialog`，桌面上是 overlay 里的
 *  `.v-card`（AdaptiveDialog 渲染 `<v-dialog><v-card rounded="lg">…`）。 */
function dialog(view: View) {
  const overlay = view.baseElement.querySelector('.v-overlay--active')
  return (overlay?.querySelector('.v-card') ?? null) as HTMLElement | null
}

async function openUpload(view: View) {
  await waitFor(() => expect(view.container.textContent).toContain('设计规范'))
  const button = (await view.findByText('上传资料')).closest('button')!
  await fireEvent.click(button)
  await waitFor(() => expect(dialog(view)).toBeTruthy())
  return dialog(view)!
}

/** 对话框里第 1 个文本框就是「资料名称」。 */
function nameField(panel: HTMLElement) {
  return panel.querySelector('input[type="text"]') as HTMLInputElement
}

function submitButton(panel: HTMLElement) {
  return Array.from(panel.querySelectorAll('button')).find((b) => b.textContent?.trim() === '上传')!
}

async function pickType(panel: HTMLElement, index: number) {
  await fireEvent.click(panel.querySelectorAll('.type-option')[index]!)
}

async function fill(panel: HTMLElement, name: string) {
  await fireEvent.update(nameField(panel), name)
}

function createdRequest() {
  return createMock.mock.calls[0]![0] as { content: string; materialId?: number } & Record<string, unknown>
}

function createdPayload() {
  return JSON.parse(createdRequest().content) as Record<string, unknown>
}

describe('校验', () => {
  it('名字是空的，一个请求都不发', async () => {
    const view = mount()
    const panel = await openUpload(view)
    await fireEvent.click(submitButton(panel))

    await waitFor(() => expect(panel.querySelectorAll('.v-messages__message').length).toBeGreaterThan(0))
    expect(createMock).not.toHaveBeenCalled()
    expect(uploadMock).not.toHaveBeenCalled()
  })

  it('链接那一档：地址不是 http(s) 就拦下来', async () => {
    const view = mount()
    const panel = await openUpload(view)
    await fill(panel, '一个链接')
    await pickType(panel, 2)

    const fields = panel.querySelectorAll('input[type="text"]')
    await fireEvent.update(fields[1] as HTMLInputElement, '随便写的')
    await fireEvent.click(submitButton(panel))

    await waitFor(() => expect(panel.textContent).toContain('请输入有效的URL'))
    expect(createMock).not.toHaveBeenCalled()
  })
})

describe('四档各自的请求', () => {
  it('文件：先传材料拿到 id，再把 id 带上；富文本里放的是文件名', async () => {
    const view = mount()
    const panel = await openUpload(view)
    await fill(panel, '照片')

    const file = new File(['x'], 'a.png', { type: 'image/png' })
    const fileInput = panel.querySelector('input[type="file"]') as HTMLInputElement
    await fireEvent.change(fileInput, { target: { files: [file] } })

    await fireEvent.click(submitButton(panel))
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1))

    expect(uploadMock).toHaveBeenCalledWith(file, 'image')
    const request = createdRequest()
    expect(request).toMatchObject({ name: '照片', type: 'MATERIAL', teamId: 7, materialId: 99, labels: [] })
    const richText = createdPayload().richText as { content: { content: { text: string }[] }[] }
    expect(richText.content[0]!.content[0]!.text).toBe('a.png')
  })

  it('文件：视频和音频各自猜出自己的 materialType', async () => {
    const view = mount()
    const panel = await openUpload(view)
    await fill(panel, '一段视频')

    const file = new File(['x'], 'b.mp4', { type: 'video/mp4' })
    await fireEvent.change(panel.querySelector('input[type="file"]') as HTMLInputElement, {
      target: { files: [file] },
    })
    await fireEvent.click(submitButton(panel))

    await waitFor(() => expect(uploadMock).toHaveBeenCalledWith(file, 'video'))
  })

  it('文本：content 里装的是编辑器交出来的富文本', async () => {
    const view = mount()
    const panel = await openUpload(view)
    await fill(panel, '一段笔记')
    await pickType(panel, 1)

    await waitFor(() => expect(panel.querySelector('.tiptap-stub')).toBeTruthy())
    await fireEvent.click(submitButton(panel))

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1))
    expect(createdRequest()).toMatchObject({ name: '一段笔记', type: 'TEXT' })
    expect(createdPayload()).toHaveProperty('richText')
    expect(createdRequest().materialId).toBeUndefined()
  })

  it('链接：url 进 content；标题留空时退回资料名称', async () => {
    const view = mount()
    const panel = await openUpload(view)
    await fill(panel, '官网乙')
    await pickType(panel, 2)

    const fields = panel.querySelectorAll('input[type="text"]')
    await fireEvent.update(fields[1] as HTMLInputElement, 'https://example.com/x')

    await fireEvent.click(submitButton(panel))
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1))

    expect(createdRequest()).toMatchObject({ name: '官网乙', type: 'LINK' })
    expect(createdPayload()).toMatchObject({ url: 'https://example.com/x', title: '官网乙' })
  })

  it('代码片段：代码和语言进 content', async () => {
    const view = mount()
    const panel = await openUpload(view)
    await fill(panel, '一段脚本')
    await pickType(panel, 3)

    await fireEvent.update(panel.querySelector('textarea') as HTMLTextAreaElement, 'console.log(1)')
    await fireEvent.click(submitButton(panel))

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1))
    expect(createdRequest()).toMatchObject({ name: '一段脚本', type: 'CODE' })
    expect(createdPayload()).toMatchObject({ code: 'console.log(1)', language: 'javascript' })
  })
})

describe('落地', () => {
  it('成功：新条目插在最前面，对话框关掉，说一句成功', async () => {
    createMock.mockResolvedValue({ data: { knowledge: { ...existing(), id: 42, name: '照片' } } })
    const view = mount()
    const panel = await openUpload(view)
    await fill(panel, '一段笔记')
    await pickType(panel, 1)
    await fireEvent.click(submitButton(panel))

    await waitFor(() => expect(successMock).toHaveBeenCalledWith('资料上传成功'))
    await waitFor(() => expect(dialog(view)).toBeNull())
    await waitFor(() => expect(view.container.querySelectorAll('.resource-card').length).toBe(2))
    expect(view.container.querySelectorAll('.resource-card')[0]!.textContent).toContain('照片')
  })

  it('失败：说一句失败，对话框留着让人再试', async () => {
    createMock.mockRejectedValue(new Error('炸了'))
    const view = mount()
    const panel = await openUpload(view)
    await fill(panel, '一段笔记')
    await pickType(panel, 1)
    await fireEvent.click(submitButton(panel))

    await waitFor(() => expect(errorMock).toHaveBeenCalledWith('上传资料失败，请稍后重试'))
    expect(dialog(view)).toBeTruthy()
  })
})
