/** 「文件」那一格的保存到资料库：私密频道和私聊里先问，取消就不保存；公开频道直接保存。 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  listRoomAttachments: vi.fn(),
  listRoomDocuments: vi.fn(),
  listRoomFiles: vi.fn(),
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

describe('文件夹', () => {
  it('定时任务按日期放的结果在文件夹里，点进去才看得到，面包屑回到最上层', async () => {
    api.listRoomFiles.mockResolvedValue({
      data: [
        {
          path: 'runs/2026-09-14/weekly-report.md',
          bytes: 10,
          modified_at: '2026-09-14T02:10:00Z',
          author: null,
          author_kind: null,
          source: null,
          in_library: false,
        },
      ],
      total: 1,
      truncated: false,
    })
    mount(false)
    expect(await screen.findByText('合作协议.docx')).toBeTruthy()
    expect(screen.queryByText('weekly-report.md')).toBeNull()

    await fireEvent.click(await screen.findByText('runs'))
    await fireEvent.click(await screen.findByText('2026-09-14'))
    expect(await screen.findByText('weekly-report.md')).toBeTruthy()
    expect(screen.queryByText('合作协议.docx')).toBeNull()

    await fireEvent.click(screen.getByRole('button', { name: '全部文件' }))
    expect(await screen.findByText('合作协议.docx')).toBeTruthy()
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
