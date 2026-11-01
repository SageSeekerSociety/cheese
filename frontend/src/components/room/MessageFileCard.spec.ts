/**
 * 对话里的文件卡片：进了视野才去取字节、转格式；浏览器没有 IntersectionObserver 时
 * 直接取。卡片说这份文件现在第几版、这条消息之后改没改过；表格还说改了几格。
 * 底下那排按钮各做各的事。
 */
import { createVuetify } from 'vuetify'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import MessageFileCard from './MessageFileCard.vue'

import { setLocale } from '@/i18n'
import { ATTACHMENT_SOURCE } from '@/lib/attachmentSource'
import { FILE_FACTS_SOURCE, type FileVersion } from '@/lib/fileFactsSource'

const documentPdf = vi.fn()
const fileBytes = vi.fn()
const source = {
  imageUrl: () => Promise.reject(new Error('x')),
  mediaUrl: () => Promise.reject(new Error('x')),
  documentPdf,
  fileBytes,
}
const versions = vi.fn()
const versionBytes = vi.fn()
const facts = { versions, versionBytes, livePreview: () => Promise.resolve(null) }

let observers: { cb: IntersectionObserverCallback; el: Element | null }[] = []
class FakeObserver {
  cb: IntersectionObserverCallback
  constructor(cb: IntersectionObserverCallback) {
    this.cb = cb
    observers.push({ cb, el: null })
  }
  observe(el: Element) {
    observers[observers.length - 1].el = el
  }
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return []
  }
}

const POSTED = '2026-10-27T13:26:49Z'
const v = (seq: number, at: string): FileVersion => ({ id: `v${seq}`, seq, size: 37_000, created_at: at })

beforeEach(() => {
  setLocale('zh-CN')
  observers = []
  documentPdf.mockRejectedValue(new Error('not converted'))
  fileBytes.mockRejectedValue(new Error('no bytes'))
  versions.mockResolvedValue([])
  versionBytes.mockRejectedValue(new Error('no bytes'))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function mount(path = 'output/方案.docx', name = '方案.docx') {
  return render(MessageFileCard, {
    props: { topicId: 'room', path, name, kind: '文档', postedAt: POSTED, downloadable: true },
    global: {
      plugins: [createVuetify()],
      provide: { [ATTACHMENT_SOURCE as symbol]: source, [FILE_FACTS_SOURCE as symbol]: facts },
    },
  })
}

async function seeIt() {
  await waitFor(() => expect(observers.length).toBeGreaterThan(0))
  observers.at(-1)!.cb([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver)
}

it('进视野之前不去转格式、不问版本，进了视野才取', async () => {
  vi.stubGlobal('IntersectionObserver', FakeObserver)
  mount()
  await waitFor(() => expect(observers.length).toBeGreaterThan(0))
  expect(documentPdf).not.toHaveBeenCalled()
  expect(versions).not.toHaveBeenCalled()

  await seeIt()
  await waitFor(() => expect(documentPdf).toHaveBeenCalledWith('room', 'output/方案.docx'))
  expect(versions).toHaveBeenCalledWith('room', 'output/方案.docx')
})

it('浏览器没有 IntersectionObserver：直接取，不让卡片一直空着', async () => {
  // 老内核里这个名字整个不存在（不是存在但为空），按那个样子摘掉。
  const win = window as unknown as Record<string, unknown>
  const saved = win.IntersectionObserver
  delete win.IntersectionObserver
  try {
    mount('output/结题报告.pdf', '结题报告.pdf')
    await waitFor(() => expect(fileBytes).toHaveBeenCalledWith('room', 'output/结题报告.pdf'))
  } finally {
    win.IntersectionObserver = saved
  }
})

it('这条消息之后文件又存过：标出已更新到第几版', async () => {
  vi.stubGlobal('IntersectionObserver', FakeObserver)
  // 新的在前：第 1 版是这条消息发的（存版比消息晚几百毫秒也算），第 2 版是之后改的。
  versions.mockResolvedValue([v(2, '2026-10-27T13:27:12Z'), v(1, '2026-10-27T13:26:49.6Z')])
  mount()
  await seeIt()
  expect(await screen.findByText('已更新到第 2 版')).toBeTruthy()
})

it('之后没再改过：只写现在第几版，不挂「已更新」', async () => {
  vi.stubGlobal('IntersectionObserver', FakeObserver)
  versions.mockResolvedValue([v(1, '2026-10-27T13:26:49.6Z')])
  mount()
  await seeIt()
  await waitFor(() => expect(screen.getByText(/第 1 版/)).toBeTruthy())
  expect(screen.queryByTestId('file-card-badge')).toBeNull()
})

it('点卡片、点「在右侧打开」都交出去开；点「下载」交出去下', async () => {
  vi.stubGlobal('IntersectionObserver', FakeObserver)
  const ui = mount()
  await fireEvent.click(screen.getByText('方案.docx'))
  await fireEvent.click(screen.getByText('在右侧打开'))
  await fireEvent.click(screen.getByText('下载'))
  expect(ui.emitted().open).toHaveLength(2)
  expect(ui.emitted().download).toHaveLength(1)
  // 每种文件卡底下都只有这两颗：会话内展开是文档自己的，摆在正文下面。
  const row = ui.container.querySelector('.fcard__actions') as HTMLElement
  expect([...row.querySelectorAll('button')].map((b) => b.textContent?.trim())).toEqual(['在右侧打开', '下载'])
})

async function sheet(orders: number): Promise<ArrayBuffer> {
  const { default: ExcelJS } = await import('exceljs')
  const wb = new ExcelJS.Workbook()
  const ws = wb.addWorksheet('汇总')
  ws.addRow(['月份', '订单数'])
  ws.addRow(['9 月', orders])
  const out = await wb.xlsx.writeBuffer()
  // exceljs 声明的是它自己的 Buffer（extends ArrayBuffer），运行时给的是 Node 的
  // Buffer（Uint8Array 的一种）：两种都收，后者的字节会被拷进新的 ArrayBuffer。
  return new Uint8Array(out as ArrayBuffer).buffer
}

it('表格在这条消息之后改过：数出改了几格（拿消息那一版和现在这份比）', async () => {
  vi.stubGlobal('IntersectionObserver', FakeObserver)
  versions.mockResolvedValue([v(2, '2026-10-27T13:27:12Z'), v(1, '2026-10-27T13:26:49.6Z')])
  versionBytes.mockImplementation(async (_topic: string, id: string) => (id === 'v1' ? sheet(1204) : sheet(990)))
  fileBytes.mockImplementation(() => sheet(990))
  mount('output/订单.xlsx', '订单.xlsx')
  await seeIt()
  expect(await screen.findByText('改了 1 格')).toBeTruthy()
  expect(versionBytes).toHaveBeenCalledWith('room', 'v1')
})
