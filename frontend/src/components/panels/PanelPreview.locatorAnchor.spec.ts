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

function mount() {
  return render(PanelPreviewView, {
    props: props(),
    global: {
      plugins: [createVuetify({ components, directives })],
      stubs: { VBtn: { template: '<button><slot /></button>' }, VIcon: true, VSpacer: true, VAlert: true },
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
