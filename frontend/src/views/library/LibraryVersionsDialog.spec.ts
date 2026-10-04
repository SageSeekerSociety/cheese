// @vitest-environment jsdom
/** 资料库文件的版本：列出每一版、下载某一版、把旧版恢复成当前（复制成新一版，先确认）。 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/libraryApi', () => ({
  listLibraryVersions: vi.fn(),
  restoreLibraryVersion: vi.fn(),
  libraryVersionRawUrl: (_p: string, path: string, id: string) => `raw?path=${path}&version=${id}`,
}))
vi.mock('@/api', async () => ({
  ...(await vi.importActual<typeof import('@/api')>('@/api')),
  downloadFile: vi.fn(async () => {}),
}))

import LibraryVersionsDialog from './LibraryVersionsDialog.vue'

import { downloadFile } from '@/api'
import { setLocale, t } from '@/i18n'
import { listLibraryVersions, restoreLibraryVersion } from '@/lib/libraryApi'

beforeAll(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal('visualViewport', {
    width: 1280,
    height: 800,
    offsetLeft: 0,
    offsetTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
})
beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
  vi.mocked(listLibraryVersions).mockResolvedValue([
    { id: 'r2', version: 2, bytes: 4, added_by: 'bob', added_at: null, current: true },
    { id: 'r1', version: 1, bytes: 3, added_by: 'alice', added_at: null, current: false },
  ])
})
afterEach(cleanup)

function mount() {
  return render(LibraryVersionsDialog, {
    props: { projectId: 'p1', path: '预算.xlsx', canRestore: true, fmtBytes: (n: number) => `${n} B` },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

describe('资料库文件版本', () => {
  it('列出每一版，新的在上，标出当前', async () => {
    mount()
    await screen.findByText(/第 2 版/)
    expect(screen.getByText(/第 1 版/)).toBeTruthy()
    expect(screen.getByText('当前')).toBeTruthy()
    expect(screen.getByText(/alice · 3 B/)).toBeTruthy()
  })

  it('当前那一版没有恢复，旧版能下载', async () => {
    mount()
    await screen.findByText(/第 2 版/)
    expect(screen.getAllByRole('button', { name: t('work.library.restoreVersion') })).toHaveLength(1)
    await fireEvent.click(screen.getAllByRole('button', { name: t('work.library.download') })[1])
    expect(downloadFile).toHaveBeenCalledWith('raw?path=预算.xlsx&version=r1', '预算.xlsx')
  })

  it('恢复先确认，确认后才复制成新一版', async () => {
    vi.mocked(restoreLibraryVersion).mockResolvedValue({})
    const view = mount()
    await screen.findByText(/第 2 版/)
    await fireEvent.click(screen.getByRole('button', { name: t('work.library.restoreVersion') }))
    expect(restoreLibraryVersion).not.toHaveBeenCalled()
    const confirm = await screen.findAllByRole('button', { name: t('work.library.restoreVersion') })
    await fireEvent.click(confirm[confirm.length - 1])
    await waitFor(() => expect(restoreLibraryVersion).toHaveBeenCalledWith('p1', '预算.xlsx', 'r1'))
    await waitFor(() => expect(view.emitted().restored?.[0]).toEqual(['预算.xlsx', 1]))
  })
})
