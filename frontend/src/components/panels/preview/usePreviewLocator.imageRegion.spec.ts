/**
 * 图片上框一块、说一句话：交出去的是带文件身份的 `image-region` 引用。
 *
 * 这一处原先只拼一句话（坐标写在文字里，受话人得自己从字里读）——它是最后一个绕过
 * 结构化出口的查看器。这一条守的是它现在走那条出口：位置按原图像素给，身份发的时候
 * 才配上；拿不到身份才退回拼话那条老路。
 *
 * 挂的是调用点（`usePreviewLocator`），不是叶子：图片那一档的取字节链路很深
 * （`usePreviewImageRegion` 要一份核过字节的快照），而这一条要证明的只是「框完之后
 * 定位器把这一块交给了 `quote` 出口」——只喂 props 的叶子测试对没接线的代码同样绿。
 */
import type { PreviewModeApi } from '@/composables/panelPreview/previewMode'
import type { RasterSelection } from './designRegion'

import { ref } from 'vue'
import { beforeEach, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'

// 图片那一档的取字节链路不是这一条要证明的事；替身报出的是读者框完一块之后真查看器
// 会留下的那一个 target，判据（`capture`）照真实现：拿不到就返回 null。
const target = ref<{ selection: RasterSelection } | null>(null)
vi.mock('./usePreviewImageRegion', () => ({
  usePreviewImageRegion: () => ({
    clear: () => (target.value = null),
    capture: (selection: RasterSelection) => ({ selection, identity: {}, snapshot: {} }),
    message: () => '图片区域：一句话',
    selectionEnabled: ref(true),
    target,
  }),
}))

const { usePreviewLocator } = await import('./usePreviewLocator')

beforeEach(() => setLocale('zh-CN'))

const identity = {
  topicId: 'room',
  path: '示意图.png',
  taskId: 'task',
  source: 'live' as const,
  version: 'v8',
}

function mount(submitQuestion?: (request: unknown) => boolean, mode?: PreviewModeApi | null) {
  const onLocate = vi.fn()
  const locator = usePreviewLocator(
    {
      topicId: 'room',
      submitQuestion,
      path: '示意图.png',
      docIdentity: identity,
      docSnapshot: { bytes: new ArrayBuffer(8), identity, sourceVersion: 'v8' },
      docBytes: new ArrayBuffer(8),
      previewFile: {
        path: '示意图.png',
        content: null,
        version: 'v8',
        bytes: 8,
        binary: true,
        too_large: false,
        source: 'live',
      },
      docLoading: false,
      docError: '',
      docRendererMissing: false,
      isImageArtifact: true,
    } as never,
    {
      mode: mode ?? null,
      onLocate,
      onAskPersonal: vi.fn(),
      onPickMode: vi.fn(),
      snapshot: async () => null,
      clearMark: vi.fn(),
    }
  )
  return { locator, onLocate }
}

const selection: RasterSelection = {
  region: { x: 12, y: 40, width: 180, height: 96 },
  identity: '{"x":1}',
  src: 'blob:fake',
  naturalWidth: 1024,
  naturalHeight: 768,
}

it('框一块之后交出去的是带身份的 image-region 引用，坐标是原图像素', () => {
  const submit = vi.fn().mockReturnValue(true)
  const { locator, onLocate } = mount(submit)
  locator.onImageRegion(selection)
  locator.locatorNote.value = '这一块改掉'
  locator.sendLocator()

  expect(submit).toHaveBeenCalledTimes(1)
  expect(submit.mock.calls[0]?.[0]).toMatchObject({
    topicId: 'room',
    intent: 'ask-agent',
    content: '这一块改掉',
    quotedContext: {
      kind: 'image-region',
      path: '示意图.png',
      source: 'live',
      version: 'v8',
      task_id: 'task',
      x: 12,
      y: 40,
      width: 180,
      height: 96,
      natural_width: 1024,
      natural_height: 768,
    },
  })
  // 走的是结构化那条路，不再额外拼一句话。
  expect(onLocate).not.toHaveBeenCalled()
})

it('拿不到身份时退回拼一句话那条老路，不白丢读者写的那句', () => {
  const { locator, onLocate } = mount(undefined)
  locator.onImageRegion(selection)
  locator.locatorNote.value = '这一块改掉'
  locator.sendLocator()

  expect(onLocate).toHaveBeenCalledTimes(1)
  // 退回去的是那一句话（替身给的那一句），不是结构化引用。
  expect(onLocate.mock.calls[0]?.[0]).toMatchObject({ message: '图片区域：一句话' })
})

it('评论栏里留一条评论时也给得出锚点，不是让它挂成「这一处已经不在原处了」', () => {
  // 评论栏已经开着时读者可以直接在里面写一条；那时面板带下去的是这一块的位置文字，
  // 锚点要是 null，`PreviewComments` 的 `placeOf` 只认带版本号的锚点，会把这条刚挂上
  // 去的评论画成「已失效」（`DocThreadCard` 的 `is-gone`）。
  const { locator } = mount(undefined)
  locator.onImageRegion(selection)

  expect(locator.quoted.anchor()).toMatchObject({
    kind: 'image-region',
    path: '示意图.png',
    version: 'v8',
    task_id: 'task',
    natural_width: 1024,
  })
})

it('「改」那一段里框选什么都不出：宿主递进来的模式真的管到了这一层', async () => {
  // 宿主是在同一个 setup 里 `provide` 的，`inject` 只往祖先找——照那样写这一层永远读到
  // null，档位形同虚设（独立评审报出的那条）。所以模式是显式递进来的，这一条守着它
  // 真的到了：切成「改」以后框一块，定位框不该出来。
  const { usePreviewMode } = await import('@/composables/panelPreview/previewMode')
  const mode = usePreviewMode(
    () => true,
    () => '示意图.png'
  )
  const { locator } = mount(undefined, mode)
  mode.set('edit')
  locator.onImageRegion(selection)
  expect(locator.locator.value).toBeNull()

  mode.set('note')
  locator.onImageRegion(selection)
  expect(locator.locator.value).not.toBeNull()
})
