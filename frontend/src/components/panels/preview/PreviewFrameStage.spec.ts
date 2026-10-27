/**
 * 帧那一档的两句状态文字：换一页时上一帧还摆着，「正在加载新页面」必须照样说得出口。
 *
 * 守的是这个：这一档换一页时**不**把上一帧卸掉（`usePreviewFrames`：新导航不清掉已加载
 * 的那一帧），所以「有帧可看」和「正在加载」会同时成立。把状态机当成「有内容就 ready」
 * 那种互斥枚举来用，这行字除了第一次导航就再也说不出口——一段静默的行为退步。
 *
 * 修复前（把 `shown` 优先那条判据原样套上来）这一条会红：`displayedFrame` 非空时只剩
 * 运行时状态块，加载那句不见了。
 */
import type { PreviewFrame } from '@/composables/usePreviewFrames'

import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it } from 'vitest'

import PreviewFrameStage from './PreviewFrameStage.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

afterEach(cleanup)

function frame(): PreviewFrame {
  return {
    id: 1,
    name: 'frame-1',
    url: 'https://example.test/',
    label: 'week-1.html',
    mime: 'text/html',
    version: 'v1',
    live: true,
    posted: true,
  }
}

function mount(
  navigation: 'idle' | 'authorizing' | 'navigating' | 'loaded' | 'failed',
  displayedFrame: PreviewFrame | null
) {
  return render(PreviewFrameStage, {
    props: {
      topicId: 'topic-1',
      frameName: 'frame',
      frames: [frame()],
      displayedFrame,
      navigation,
      previewFile: null,
      previewMime: 'text/html',
      previewUrl: 'https://example.test/',
      previewAppNote: '',
      previewTunnelUp: true,
      previewError: null,
      previewReadError: null,
      pickTarget: null,
      pickOn: false,
      agentName: '芝士',
    },
    global: { stubs: { VIcon: true, VChip: { template: '<span><slot /></span>' } } },
  })
}

it('换一页时上一帧还摆着：加载那一句照样说得出口', () => {
  const ui = mount('navigating', frame())
  expect(ui.container.textContent).toContain('正在加载新页面')
})

it('还没拿到授权、也还没有可看的帧：说的是获取授权', () => {
  const ui = mount('authorizing', null)
  expect(ui.container.textContent).toContain('正在获取预览授权')
})

it('没在导航时不摆任何一句「正在……」', () => {
  const ui = mount('idle', frame())
  const text = ui.container.textContent ?? ''
  expect(text).not.toContain('正在加载新页面')
  expect(text).not.toContain('正在获取预览授权')
})

it('静态 SVG 或网页支持在网页视图与看原文之间切换，换文件时恢复为渲染视图', async () => {
  const staticFrame = { ...frame(), label: 'diagram.svg', mime: 'image/svg+xml', live: false }
  const ui = render(PreviewFrameStage, {
    props: {
      topicId: 'topic-1',
      frameName: 'frame',
      frames: [staticFrame],
      displayedFrame: staticFrame,
      navigation: 'loaded',
      previewFile: {
        path: 'diagram.svg',
        content: '<svg viewBox="0 0 10 10"><circle r="4" /></svg>',
        version: 'v1',
        bytes: 47,
        binary: false,
        too_large: false,
      },
      previewMime: 'image/svg+xml',
      previewUrl: 'https://example.test/diagram.svg',
      previewAppNote: '',
      previewTunnelUp: true,
      previewError: null,
      previewReadError: null,
      pickTarget: null,
      pickOn: false,
      agentName: '芝士',
    },
    global: {
      stubs: {
        VIcon: true,
        VChip: { template: '<span><slot /></span>' },
        VBtn: { template: '<button v-bind="$attrs"><slot /></button>' },
      },
    },
  })

  expect(ui.queryByTestId('frame-source')).toBeNull()
  await fireEvent.click(ui.getByTestId('toggle-frame-source'))
  expect(ui.getByTestId('frame-source').textContent).toContain('<circle r="4" />')

  await ui.rerender({
    previewFile: {
      path: 'other.svg',
      content: '<svg viewBox="0 0 20 20"><rect width="10" height="10" /></svg>',
      version: 'v2',
      bytes: 62,
      binary: false,
      too_large: false,
    },
  })
  expect(ui.queryByTestId('frame-source')).toBeNull()
})
