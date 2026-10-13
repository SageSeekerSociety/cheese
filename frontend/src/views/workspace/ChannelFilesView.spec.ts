// 「频道文件」这一页：这个频道自己的文件按最近改动排，点一份在右边看内容、能下载；
// 一份都没有时说的是「暂无文件」；被上限截过要说出来。
import type { RoomFile } from '@/api/roomFiles'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import ChannelFilesView from './ChannelFilesView.vue'

import { setLocale, t } from '@/i18n'

Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })

function file(path: string, rest: Partial<RoomFile> = {}): RoomFile {
  return { path, bytes: 4210, modified_at: '2026-09-14T10:12:00Z', ...rest }
}

const files = [
  file('runs/2026-09-14/周报.md'),
  file('screenshot-2026-09-12.png', { bytes: 132_400 }),
  file('文献综述.docx'),
]

const read = vi.fn(async () => new TextEncoder().encode('这个频道的一份文件。').buffer)

beforeEach(() => {
  read.mockClear()
  vi.stubGlobal('visualViewport', {
    width: 1440,
    height: 900,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  setLocale('zh-CN')
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function mount(extra: Record<string, unknown> = {}) {
  return render(ChannelFilesView, {
    props: { files, truncated: false, loading: false, error: null, read, ...extra },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

function rows(view: ReturnType<typeof mount>): HTMLElement[] {
  return Array.from(view.container.querySelectorAll('.room-files__open')) as HTMLElement[]
}

it('一份一份列出来，按给过来的顺序，带路径和时间', () => {
  const view = mount()
  const names = rows(view).map((row) => row.querySelector('.room-files__name')?.textContent)
  expect(names).toEqual(['周报.md', 'screenshot-2026-09-12.png', '文献综述.docx'])
  expect(rows(view)[0].textContent).toContain('runs/2026-09-14')
})

it('点一份就在右边看内容、能下载', async () => {
  const view = mount()
  await fireEvent.click(rows(view)[0])
  expect(view.container.querySelector('.room-files__detail-name')?.textContent).toBe('周报.md')
  expect(read).toHaveBeenCalledWith(files[0], expect.anything())

  await fireEvent.click(view.getByRole('button', { name: t('work.library.download') }))
  expect(view.emitted('download')).toEqual([[files[0]]])
})

it('一份都没有说的是「暂无文件」', () => {
  const view = mount({ files: [] })
  expect(view.getByText(t('files.room.empty'))).toBeTruthy()
})

it('读不到的时候给一次重试', async () => {
  const view = mount({ error: '服务暂时不可达', files: [] })
  await fireEvent.click(view.getByRole('button', { name: t('global.loadError.retry') }))
  expect(view.emitted('retry')).toEqual([[]])
})

it('被上限截过要说出来，不闷着', () => {
  const view = mount({ truncated: true })
  expect(view.getByText(t('files.room.truncated', { n: files.length }))).toBeTruthy()
})
