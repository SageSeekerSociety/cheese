import type { PreviewInfo } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const getPreview = vi.fn()
const readPreviewFile = vi.fn()
const requestPreviewSession = vi.fn()
const postPreviewSession = vi.fn()
const downloadFile = vi.fn()
const attachmentRawUrl = vi.fn()
const previewFileBytes = vi.fn()
const previewDocumentPdf = vi.fn()

vi.mock('../../api', () => ({
  getPreview: (...args: unknown[]) => getPreview(...args),
  readPreviewFile: (...args: unknown[]) => readPreviewFile(...args),
  requestPreviewSession: (...args: unknown[]) => requestPreviewSession(...args),
  downloadFile: (...args: unknown[]) => downloadFile(...args),
  attachmentRawUrl: (...args: unknown[]) => attachmentRawUrl(...args),
  previewFileBytes: (...args: unknown[]) => previewFileBytes(...args),
  previewDocumentPdf: (...args: unknown[]) => previewDocumentPdf(...args),
  PreviewRendererUnavailable: class extends Error {},
}))
// 表单投递在测试里没有意义（它是在预览域的存储分区里落 cookie 的），换成一个
// 记录调用的替身，好让「图片确实走上了 iframe 这条路」这个断言只看本组件。
vi.mock('../../lib/previewSession', () => ({
  postPreviewSession: (...args: unknown[]) => postPreviewSession(...args),
}))

import PanelPreview from './PanelPreview.vue'

import { setLocale } from '@/i18n'

const PREVIEW = 'https://preview-abc.cheeseusercontent.com/'

function artifact(path: string, mime: string): PreviewInfo {
  return { kind: 'file', path, mime, url: PREVIEW, artifact_id: `artifact-${path}`, tunnel_up: true }
}

function textFile(path: string, content: string) {
  return { path, content, version: 'v1', bytes: content.length, binary: false, too_large: false }
}

function binaryFile(path: string) {
  return { path, content: null, version: 'v1', bytes: 2048, binary: true, too_large: false }
}

function mount() {
  return render(PanelPreview, {
    props: { topicId: 'topic-a', projectId: 'project-a', active: true },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

beforeEach(() => {
  // 这一份断言的是中文界面。默认语言是 en，所以先钉住语言：面板的文字进了词表
  // 之后，屏幕上出现哪一版词不再由写死的中文决定。
  setLocale('zh-CN')
  vi.clearAllMocks()
  attachmentRawUrl.mockReturnValue('/api/topics/topic-a/attachments/raw?path=x')
  downloadFile.mockResolvedValue(undefined)
  previewDocumentPdf.mockResolvedValue(new ArrayBuffer(4096))
  previewFileBytes.mockResolvedValue(new ArrayBuffer(2048))
  requestPreviewSession.mockResolvedValue({ url: `${PREVIEW}_cheese/session`, grant: 'g' })
})
afterEach(cleanup)

it('renders a markdown deliverable as the document it describes, not as its source', async () => {
  getPreview.mockResolvedValue(artifact('output/方案.md', 'text/markdown'))
  readPreviewFile.mockResolvedValue(textFile('output/方案.md', '# 预算方案\n\n- 第一项\n- 第二项\n\n**重点。**下一句'))

  mount()

  const md = await screen.findByTestId('markdown')
  // 标题成了标题、列表成了列表——之前这块显示的是 `# 预算方案` 和两行星号。
  expect(md.querySelector('h1')?.textContent).toBe('预算方案')
  expect(md.querySelectorAll('li')).toHaveLength(2)
  // 解析器是聊天用的那一个，所以中文后面的 `**` 收得住：这一条只有装上了
  // marked-cjk-friendly 才成立。
  expect(md.querySelector('strong')?.textContent).toBe('重点。')
  expect(screen.queryByText('# 预算方案')).toBeNull()
})

it('never sends a markdown file to a viewer that does not exist', async () => {
  getPreview.mockResolvedValue(artifact('output/说明.md', 'text/markdown'))
  readPreviewFile.mockResolvedValue(textFile('output/说明.md', '# 说明'))

  const { container } = mount()

  await screen.findByTestId('markdown')
  // 既没有 iframe（预览域只会把字节原样发出来），也没有假装缺了什么转换服务。
  expect(container.querySelector('iframe')).toBeNull()
  expect(requestPreviewSession).not.toHaveBeenCalled()
  expect(previewDocumentPdf).not.toHaveBeenCalled()
  expect(screen.queryByText('文档预览未启用')).toBeNull()
  expect(screen.queryByText('无法显示这个文件')).toBeNull()
})

// 预览的正文来自一个文件，v-html 会照单全收，所以它和聊天走同一个出口
// （sanitizeRendered）。下面两条各自只放一样东西进去：happy-dom 的 DOMParser 在
// 一个 <script> 之后会漏掉属性这一层的清理（同样输入换真浏览器不会），两样放一起
// 会测出这个环境自己的毛病，而不是这里的代码。
it('drops a script a markdown file tried to put on the page', async () => {
  getPreview.mockResolvedValue(artifact('output/外来的.md', 'text/markdown'))
  readPreviewFile.mockResolvedValue(textFile('output/外来的.md', '<script>window.stolen = 1</script>\n\n正文'))

  mount()

  const md = await screen.findByTestId('markdown')
  expect(md.querySelector('script')).toBeNull()
  expect(md.textContent).toContain('正文')
})

it('drops the handler on markup a markdown file pasted in', async () => {
  getPreview.mockResolvedValue(artifact('output/外来的.md', 'text/markdown'))
  readPreviewFile.mockResolvedValue(textFile('output/外来的.md', '<img src="/x" onerror="window.stolen = 2">\n\n正文'))

  mount()

  const md = await screen.findByTestId('markdown')
  const img = md.querySelector('img')!
  expect(img.getAttribute('onerror')).toBeNull()
  expect(img.getAttribute('src')).toBe('/x')
})

it('shows a png in the frame instead of calling it unreadable', async () => {
  getPreview.mockResolvedValue(artifact('output/趋势图.png', 'image/png'))
  // 图片的字节读不成文本，content 就是 null——这不是「没法显示」。
  readPreviewFile.mockResolvedValue(binaryFile('output/趋势图.png'))

  const { container } = mount()

  await waitFor(() => expect(container.querySelector('iframe')).toBeTruthy())
  expect(requestPreviewSession).toHaveBeenCalledWith('topic-a')
  expect(postPreviewSession).toHaveBeenCalled()
  expect(screen.queryByText('这个文件不是文本')).toBeNull()
  // 面板顶上写着这条预览是什么类型的字节。
  expect(screen.getByText('image/png')).toBeTruthy()
})

it('still says a file the browser cannot draw is not text', async () => {
  getPreview.mockResolvedValue(artifact('output/模型权重.bin', 'application/octet-stream'))
  readPreviewFile.mockResolvedValue(binaryFile('output/模型权重.bin'))

  const { container } = mount()

  await waitFor(() => expect(screen.getByText('这个文件不是文本')).toBeTruthy())
  expect(container.querySelector('iframe')).toBeNull()
})
