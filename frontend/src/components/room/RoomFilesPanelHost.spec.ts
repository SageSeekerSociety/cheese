/** 「文件」那一格：一行一样东西，新的在前；保存到资料库在私密频道和私聊里先问，取消就不保存，公开频道直接保存。 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  listRoomAttachments: vi.fn(),
  listRoomDocuments: vi.fn(),
  listRoomFiles: vi.fn(),
  listRoomOutputs: vi.fn(),
  saveRoomOutputToLibrary: vi.fn(),
  keepDocument: vi.fn(),
  listTopicMembers: vi.fn(),
}))
vi.mock('@/api', async () => ({ ...(await vi.importActual<typeof import('@/api')>('@/api')), ...api }))

import RoomFilesPanelHost from './RoomFilesPanelHost.vue'

import { setLocale } from '@/i18n'
import { queryClient } from '@/query/client'

const sent = {
  path: 'uploads/0123456789abcdef0123456789abcdef/合作协议.docx',
  name: '合作协议.docx',
  mime: null,
  added_by: 'alice',
  added_at: '2026-10-16T00:00:00Z',
  in_library: false,
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.stubGlobal('visualViewport', new EventTarget())
  api.listRoomAttachments.mockResolvedValue({ data: [sent], total: 1 })
  api.listRoomDocuments.mockResolvedValue({ data: [], total: 0 })
  api.listRoomFiles.mockResolvedValue({ data: [], total: 0, truncated: false })
  api.listRoomOutputs.mockResolvedValue({ data: [], total: 0 })
  api.saveRoomOutputToLibrary.mockResolvedValue({ name: '合作协议.docx' })
  api.listTopicMembers.mockResolvedValue({ data: [], total: 0 })
})
afterEach(() => {
  cleanup()
  queryClient.clear()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

function mount(seatsOnly: boolean) {
  return render(RoomFilesPanelHost, {
    props: { topicId: 'room-1', seatsOnly },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

function areaFile(path: string, modified: string, authorKind: 'human' | 'agent') {
  return {
    path,
    bytes: 10,
    modified_at: modified,
    author: authorKind === 'human' ? 'bob' : 'cheese',
    author_kind: authorKind,
    source: authorKind === 'human' ? 'upload' : 'ai',
    in_library: false,
  }
}

describe('一行一样东西', () => {
  it('列摆出来过的和人放进来的，新的在前；网页自己的文件和没摆出来的中间文件不单列', async () => {
    api.listRoomFiles.mockResolvedValue({
      data: [
        areaFile('site/index.html', '2026-10-20T00:00:00Z', 'agent'),
        areaFile('site/style.css', '2026-10-20T00:00:00Z', 'agent'),
        areaFile('work/tmp.txt', '2026-10-21T00:00:00Z', 'agent'),
        areaFile('notes/plan.md', '2026-10-18T00:00:00Z', 'human'),
        areaFile('report.docx', '2026-10-10T00:00:00Z', 'agent'),
      ],
      total: 5,
      truncated: false,
    })
    api.listRoomOutputs.mockResolvedValue({
      data: [
        { path: 'site/index.html', mime: 'text/html', kind: 'file', shown_at: '2026-10-20T00:00:00Z' },
        { path: 'report.docx', mime: 'application/msword', kind: 'file', shown_at: '2026-10-19T00:00:00Z' },
      ],
      total: 2,
    })
    mount(false)
    await screen.findByText('index.html')
    const names = [...document.querySelectorAll('.room-files-panel__name')].map((el) => el.textContent?.trim())
    expect(names).toEqual(['index.html', 'report.docx', 'plan.md', '合作协议.docx'])
    // 它在哪个文件夹是次要的一句，不是一层要点进去的文件夹。
    expect(screen.getByText('notes')).toBeTruthy()
    expect(screen.queryByText('style.css')).toBeNull()
    expect(screen.queryByText('tmp.txt')).toBeNull()
  })
})

describe('保存到资料库', () => {
  it('在私密场合先问，取消就不保存', async () => {
    mount(true)
    await fireEvent.click(await screen.findByRole('button', { name: '保存到资料库' }))
    await fireEvent.click(await screen.findByRole('button', { name: '取消' }))

    expect(api.saveRoomOutputToLibrary).not.toHaveBeenCalled()
    expect(screen.queryByText('已在资料库')).toBeNull()
  })

  it('在私密场合确认之后才保存', async () => {
    mount(true)
    await fireEvent.click(await screen.findByRole('button', { name: '保存到资料库' }))
    const dialog = await screen.findByRole('alertdialog')
    const confirm = [...dialog.querySelectorAll('button')].find((b) => b.textContent?.includes('保存到资料库'))
    await fireEvent.click(confirm!)

    await waitFor(() => expect(api.saveRoomOutputToLibrary).toHaveBeenCalledWith('room-1', sent.path))
    expect(await screen.findByText('已在资料库')).toBeTruthy()
  })

  it('在公开频道里直接保存', async () => {
    mount(false)
    await fireEvent.click(await screen.findByRole('button', { name: '保存到资料库' }))

    await waitFor(() => expect(api.saveRoomOutputToLibrary).toHaveBeenCalledWith('room-1', sent.path))
    expect(screen.queryByRole('alertdialog')).toBeNull()
  })
})
