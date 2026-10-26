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

function mount(submitQuestion?: (request: unknown) => boolean) {
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
    { onLocate, onPickMode: vi.fn(), snapshot: async () => null, clearMark: vi.fn() }
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
