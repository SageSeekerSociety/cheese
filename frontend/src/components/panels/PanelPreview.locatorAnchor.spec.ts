/**
 * 定位框弹在指的那一处旁边：挂在面板这一层验接线——面板在打开那一刻记下选中的那一段，
 * 把算出来的位置交给定位框。位置怎么算（Range、点、滚动、出视野）在
 * `preview/locatorAnchor.spec.ts`；happy-dom 没有布局，这里把那一步换成一个固定的位置，
 * 只看面板有没有把选中的那一段交过去、有没有把结果递给定位框。
 *
 * 把面板里 `:anchor="locatorAnchor"` 那一句删掉，这条会红：框不再弹出。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import PanelPreviewView from './PanelPreviewView.vue'

import { setLocale } from '@/i18n'
import { PENDING_NOTES, usePendingNotes } from '@/lib/pendingNotes'
import { previewBundles } from '@/test/panelBundles'

vi.mock('./preview/RevisionList.vue', () => ({ default: { template: '<div />' } }))

const measured: { range: Range | null }[] = []
vi.mock('./preview/locatorAnchor', async (orig) => {
  const real = await orig<typeof import('./preview/locatorAnchor')>()
  return {
    ...real,
    measureAnchor: (_panel: HTMLElement, range: Range | null) => {
      measured.push({ range })
      return range ? { top: 40, bottom: 60, left: 30, panelWidth: 800, viewTop: 0, viewBottom: 600 } : null
    },
  }
})

const SOURCE = '# 配置\n\n失败以后重试 3 次。\n'

function props(path = 'output/说明.md', content = SOURCE) {
  return {
    ...previewBundles(),
    topicId: 'room',
    projectId: 'project',
    frameName: 'frame',
    loading: false,
    refreshing: false,
    previewFile: {
      path,
      content,
      version: 'v1',
      bytes: content.length,
      binary: false,
      too_large: false,
    },
    previewMime: 'text/markdown',
    previewNamed: true,
    previewUrl: null,
    previewAppNote: '',
    previewTunnelUp: false,
    previewNamedPath: path,
    previewError: null,
    previewReadError: null,
    documentSuffix: 'md',
    documentType: { label: 'Markdown', icon: 'mdi-language-markdown-outline', view: 'markdown' as const },
    documentName: '说明.md',
    isImageArtifact: false,
    downloadError: '',
    docBytes: null,
    docLoading: false,
    docError: '',
    docRendererMissing: false,
    // 这一份文件在屏幕上的身份：发出去的结构化引用要带上它（path/source/version）。
    docIdentity: { topicId: 'room', path, taskId: null, source: 'live' as const, version: 'v1' },
  }
}

beforeEach(() => {
  setLocale('zh-CN')
  measured.length = 0
})
afterEach(() => {
  cleanup()
  window.getSelection()?.removeAllRanges()
})

function mount(pending?: ReturnType<typeof usePendingNotes>) {
  return render(PanelPreviewView, {
    props: props(),
    global: {
      plugins: [createVuetify({ components, directives })],
      stubs: { VBtn: { template: '<button><slot /></button>' }, VIcon: true, VSpacer: true, VAlert: true },
      ...(pending ? { provide: { [PENDING_NOTES as symbol]: pending } } : {}),
    },
  })
}

it('选中一段以后，定位框弹在那一段旁边，而不是贴在面板底下', async () => {
  // happy-dom 没有布局：面板宽度给一个够弹出的值（窄于 440 时框贴底、不先出按钮）。
  const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800)
  const ui = mount()
  const md = ui.getByTestId('markdown')
  const node = await waitFor(() => md.querySelector('p')!.firstChild!, { timeout: 5000 })
  const range = document.createRange()
  range.setStart(node, 4)
  range.setEnd(node, 10)
  window.getSelection()!.removeAllRanges()
  window.getSelection()!.addRange(range)
  await fireEvent.mouseUp(md)

  // 选中一段字先只出一颗「添加批注」，贴在选区左下（不压选区），整张卡不出来。
  const arm = await waitFor(() => ui.getByTestId('locator-arm'))
  expect(ui.queryByPlaceholderText('输入修改要求或批注内容…')).toBeNull()
  // 交过去的是选中的那一段本身（克隆的一份），不是空的。
  expect(measured.at(-1)?.range?.toString()).toBe(range.toString())
  expect(arm.style.top).toBe('68px')
  expect(arm.style.left).toBe('30px')

  // 点了才展开成卡，弹在同一处，光标交给输入框。
  await fireEvent.click(arm)
  const card = await waitFor(() => {
    const el = ui.getByPlaceholderText('输入修改要求或批注内容…').closest('.locator') as HTMLElement
    expect(el.classList.contains('locator--floating')).toBe(true)
    return el
  })
  expect(card.style.top).toBe('68px')
  expect(card.style.left).toBe('30px')
  expect(document.activeElement).toBe(ui.getByPlaceholderText('输入修改要求或批注内容…'))

  // 取消以后不再留着位置：下一次指认重新记。
  await fireEvent.click(card.querySelector('button[aria-label="取消"]')!)
  await waitFor(() => expect(ui.queryByPlaceholderText('输入修改要求或批注内容…')).toBeNull())
  width.mockRestore()
})

async function selectFirstLine(ui: ReturnType<typeof mount>) {
  const md = ui.getByTestId('markdown')
  const node = await waitFor(() => md.querySelector('p')!.firstChild!, { timeout: 5000 })
  const range = document.createRange()
  range.setStart(node, 4)
  range.setEnd(node, 10)
  window.getSelection()!.removeAllRanges()
  window.getSelection()!.addRange(range)
  await fireEvent.mouseUp(md)
  return md
}

it('面板窄到框只能贴底时，选中一段直接出卡，不先出按钮', async () => {
  const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(400)
  const ui = mount()
  await selectFirstLine(ui)
  await waitFor(() => expect(ui.getByPlaceholderText('输入修改要求或批注内容…')).toBeTruthy())
  expect(ui.queryByTestId('locator-arm')).toBeNull()
  width.mockRestore()
})

it('在标着「指」的查看器里（如表格）拖出的选区直接出卡', async () => {
  const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800)
  const ui = mount()
  ui.getByTestId('markdown').setAttribute('data-locator-points', '')
  await selectFirstLine(ui)
  await waitFor(() => expect(ui.getByPlaceholderText('输入修改要求或批注内容…')).toBeTruthy())
  expect(ui.queryByTestId('locator-arm')).toBeNull()
  width.mockRestore()
})

it('先出了按钮后面板被拖窄：按钮无处可贴，改成直接出卡，不会两样都不见', async () => {
  let w = 800
  const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(() => w)
  const ui = mount()
  await selectFirstLine(ui)
  await waitFor(() => ui.getByTestId('locator-arm'))
  w = 400
  window.dispatchEvent(new Event('resize'))
  await waitFor(() => expect(ui.getByPlaceholderText('输入修改要求或批注内容…')).toBeTruthy())
  width.mockRestore()
})

it('「加入待发」把这一处加进话题页那一份待发篮，不是本格自己那一条', async () => {
  const send = vi.fn<(content: string) => boolean>(() => true)
  const pending = usePendingNotes(send)
  const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800)
  const ui = mount(pending)
  const md = ui.getByTestId('markdown')
  const node = await waitFor(() => md.querySelector('p')!.firstChild!, { timeout: 5000 })
  const range = document.createRange()
  range.setStart(node, 4)
  range.setEnd(node, 10)
  window.getSelection()!.removeAllRanges()
  window.getSelection()!.addRange(range)
  await fireEvent.mouseUp(md)

  const arm = await waitFor(() => ui.getByTestId('locator-arm'))
  await fireEvent.click(arm)
  const field = await waitFor(() => ui.getByPlaceholderText('输入修改要求或批注内容…'))
  await fireEvent.update(field, '这里改成三段')
  const queue = [...ui.container.querySelectorAll('button')].find((b) => b.textContent?.trim() === '加入待发')!
  expect(queue).toBeTruthy()
  await fireEvent.click(queue)
  await waitFor(() => expect(pending.notes.value).toHaveLength(1))
  const note = pending.notes.value[0]
  expect(note.where).toBeTruthy()
  expect(note.text).toBe('这里改成三段')
  expect(note.message).toContain('这里改成三段')
  // 面板底下照旧留一条计数（原型里那一条和输入框上方那一篮同时在）；那两个按钮不在
  // 这儿——同一批批注画两遍按钮，读者会以为进了两个地方。
  const strip = ui.container.querySelector('.locator__queue')
  expect(strip).not.toBeNull()
  expect(strip!.textContent).toContain('1 条待发批注')
  expect([...strip!.querySelectorAll('button')].map((b) => b.textContent?.trim())).not.toContain('全部发送（1）')
  width.mockRestore()
})

it('篮里已经有几条时，卡片主按钮那一下发的是整批，不是当前这一处', async () => {
  // 那颗键写着「全部发送（N+1）」，点下去必须整批发：按 `queuedCount` 分流。用本格
  // 自己那条 `queued` 判的话（共享篮时它永远是空的），只会发当前这一处，篮里那几条
  // 留着没发，而读者看到的是「已经整批发完了」。
  const send = vi.fn<(content: string) => boolean>(() => true)
  const pending = usePendingNotes(send)
  pending.add({
    file: '说明.md',
    path: 'output/说明.md',
    version: '',
    where: '开头',
    quote: '甲',
    text: '先攒的一条',
    audience: 'cheese',
    message: '先攒的那条消息',
  })
  const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800)
  const ui = mount(pending)
  const md = ui.getByTestId('markdown')
  const node = await waitFor(() => md.querySelector('p')!.firstChild!, { timeout: 5000 })
  const range = document.createRange()
  range.setStart(node, 4)
  range.setEnd(node, 10)
  window.getSelection()!.removeAllRanges()
  window.getSelection()!.addRange(range)
  await fireEvent.mouseUp(md)
  await fireEvent.click(await waitFor(() => ui.getByTestId('locator-arm')))
  const field = await waitFor(() => ui.getByPlaceholderText('输入修改要求或批注内容…'))
  await fireEvent.update(field, '这一处也要改')
  // 主按钮这时写的就是「全部发送（2）」。
  const sendAll = [...ui.container.querySelectorAll('button')].find((b) => b.textContent?.trim() === '全部发送（2）')!
  expect(sendAll).toBeTruthy()
  await fireEvent.click(sendAll)
  await waitFor(() => expect(send).toHaveBeenCalled())
  // 发出去的那一条同时含先攒的那句和当前这一处，说明走的是整批那条路。
  const [firstCall] = send.mock.calls
  const sent = String(firstCall?.[0] ?? '')
  expect(sent).toContain('先攒的那条消息')
  expect(sent).toContain('这一处也要改')
  expect(pending.notes.value).toHaveLength(0)
  width.mockRestore()
})

it('篮里别的文件上的那几处不在这份正文里找序号', async () => {
  // 篮是整间房一份，换文件时不清空。按字找位置只在这一份正文里找——拿着别的文件上的
  // 一句话来这儿找，要么找不到，要么把序号挂到这一份里碰巧同文的那句话上。
  const pending = usePendingNotes(() => true)
  // 同一句原文，但说的是另一份文件。
  pending.add({
    file: '别的.md',
    path: 'other/别的.md',
    version: '',
    where: '开头',
    quote: '失败以后重试 3 次。',
    text: '改一下',
    audience: 'cheese',
    message: 'm',
    anchor: { kind: 'text-range', text: '失败以后重试 3 次。', heading: null, prefix: '', suffix: '' },
  })
  const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800)
  const ui = mount(pending)
  await waitFor(() => ui.getByTestId('markdown'))
  // 等正文画出来（序号要等它渲染完才有地方挂）。
  await waitFor(() => expect(ui.getByTestId('markdown').querySelector('p')).toBeTruthy(), { timeout: 5000 })
  await new Promise((r) => setTimeout(r, 50))
  expect(ui.container.querySelector('.pending-pin')).toBeNull()
  // 而这一份文件自己那一条是挂得上去的：换一个文件名就画。
  pending.add({
    file: '说明.md',
    path: 'output/说明.md',
    version: '',
    where: '开头',
    quote: '失败以后重试 3 次。',
    text: '改一下',
    audience: 'cheese',
    message: 'm',
    anchor: { kind: 'text-range', text: '失败以后重试 3 次。', heading: null, prefix: '', suffix: '' },
  })
  await waitFor(() => expect(ui.container.querySelector('.pending-pin')).toBeTruthy())
  width.mockRestore()
})

// ---- 看 / 批注 / 改 三段（原型里那一段）----
//
// 三段说的是「现在鼠标按下去算什么」：看那一段里选中先在选区下浮一颗「添加批注」、点了
// 才展开整张卡（两步，原型读模式）；批注那一段里选中直接出卡（一步，原型批注模式）。
// 把 `locatorFromSelection` 那一句里的模式判断删掉，下面第一条会红。

it('默认是「看」：选中先在选区下浮一颗「添加批注」，不直接弹卡', async () => {
  const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800)
  const ui = mount()
  // 刚打开一份就是「看」，不用点任何东西。
  expect(ui.getByTestId('preview-mode-view').getAttribute('aria-pressed')).toBe('true')
  await selectFirstLine(ui)
  await waitFor(() => ui.getByTestId('locator-arm'))
  expect(ui.queryByPlaceholderText('输入修改要求或批注内容…')).toBeNull()
  width.mockRestore()
})

it('切到「批注」：同一段选中直接出卡，不走那颗小按钮', async () => {
  const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800)
  const ui = mount()
  await fireEvent.click(ui.getByTestId('preview-mode-note'))
  expect(ui.getByTestId('preview-mode-note').getAttribute('aria-pressed')).toBe('true')
  await selectFirstLine(ui)
  await waitFor(() => expect(ui.getByPlaceholderText('输入修改要求或批注内容…')).toBeTruthy())
  expect(ui.queryByTestId('locator-arm')).toBeNull()
  width.mockRestore()
})

it('这一份改不了时「改」是灰的，点了不切过去', async () => {
  const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800)
  // markdown 只有在协同会话连上时才可改；这里没给 liveEdit，所以「改」不给。
  const ui = mount()
  const edit = ui.getByTestId('preview-mode-edit') as HTMLButtonElement
  expect(edit.disabled).toBe(true)
  await fireEvent.click(edit)
  expect(ui.getByTestId('preview-mode-view').getAttribute('aria-pressed')).toBe('true')
  width.mockRestore()
})

it('快捷键 2 切到批注，1 切回看', async () => {
  const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800)
  const ui = mount()
  await fireEvent.keyDown(window, { key: '2' })
  expect(ui.getByTestId('preview-mode-note').getAttribute('aria-pressed')).toBe('true')
  await fireEvent.keyDown(window, { key: '1' })
  expect(ui.getByTestId('preview-mode-view').getAttribute('aria-pressed')).toBe('true')
  width.mockRestore()
})

it('在输入框里打字时，1/2/3 是那句话里的字，不切模式', async () => {
  const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800)
  const ui = mount()
  await fireEvent.click(ui.getByTestId('preview-mode-note'))
  await selectFirstLine(ui)
  const field = await waitFor(() => ui.getByPlaceholderText('输入修改要求或批注内容…'))
  await fireEvent.keyDown(field, { key: '1' })
  // 还停在「批注」：读者在写那句话，「1」是那句话里的字符。
  expect(ui.getByTestId('preview-mode-note').getAttribute('aria-pressed')).toBe('true')
  width.mockRestore()
})
