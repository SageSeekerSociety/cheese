// 「频道文件」这一页：这个频道自己的文件一层一层看（最上层先摆文件夹，点进去，面包屑
// 回上一层），一行写着是谁什么时候放下的；点一份在右边看内容、能下载；一份都没有时说的
// 是「暂无文件」；被上限截过要说出来。
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
  return {
    path,
    bytes: 4210,
    modified_at: '2026-09-14T10:12:00Z',
    author: 'alice',
    author_kind: 'human',
    source: 'upload',
    ...rest,
  }
}

const files = [
  file('runs/2026-09-14/周报.md', { author: '', author_kind: 'agent', source: 'scheduled' }),
  file('screenshot-2026-09-12.png', { bytes: 132_400 }),
  file('文献综述.docx', { author: '', author_kind: 'unknown', source: 'baseline' }),
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

function names(view: ReturnType<typeof mount>): (string | undefined)[] {
  return rows(view).map((row) => row.querySelector('.room-files__name')?.textContent ?? undefined)
}

function crumbs(view: ReturnType<typeof mount>): HTMLElement[] {
  return Array.from(view.container.querySelectorAll('.room-files__crumb')) as HTMLElement[]
}

it('最上层先摆文件夹，再摆文件，文件夹写着里面有几份', () => {
  const view = mount()
  expect(names(view)).toEqual(['runs', 'screenshot-2026-09-12.png', '文献综述.docx'])
  expect(rows(view)[0].textContent).toContain('1 份文件')
})

it('点文件夹进去看里面，面包屑回上一层', async () => {
  const view = mount()
  await fireEvent.click(rows(view)[0])
  expect(names(view)).toEqual(['2026-09-14'])
  expect(crumbs(view).map((crumb) => crumb.textContent?.trim())).toEqual(['全部文件', 'runs'])

  await fireEvent.click(rows(view)[0])
  expect(names(view)).toEqual(['周报.md'])

  await fireEvent.click(crumbs(view)[0])
  expect(names(view)).toEqual(['runs', 'screenshot-2026-09-12.png', '文献综述.docx'])
})

it('一行上写着是谁、从哪来的、什么时候、多大', () => {
  const view = mount()
  const screenshot = rows(view)[1]
  expect(screenshot.textContent).toContain('alice')
  expect(screenshot.textContent).toContain(t('work.room.fileHistory.source.upload'))
  expect(screenshot.textContent).toContain('KB')

  // 没有保存记录的那一份说不出是谁写的：写「来源未知」，别的照写。
  expect(rows(view)[2].textContent).toContain(t('work.library.unknownSource'))
  expect(rows(view)[2].textContent).toContain(t('work.room.fileHistory.source.baseline'))
})

it('队友写的照「芝士」命名，不把座位编号画出来', () => {
  const view = mount({
    files: [file('报告.md', { author: 'cheese-1a2b3c', author_kind: 'agent', source: 'ai' })],
  })
  expect(rows(view)[0].textContent).toContain(t('work.room.defaultAgentName'))
  expect(rows(view)[0].textContent).not.toContain('cheese-1a2b3c')
})

it('点一份就在右边看内容、能下载', async () => {
  const view = mount()
  await fireEvent.click(rows(view)[1])
  expect(view.container.querySelector('.room-files__detail-name')?.textContent).toBe('screenshot-2026-09-12.png')
  expect(read).toHaveBeenCalledWith(files[1], expect.anything())

  await fireEvent.click(view.getByRole('button', { name: t('work.library.download') }))
  expect(view.emitted('download')).toEqual([[files[1]]])
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
