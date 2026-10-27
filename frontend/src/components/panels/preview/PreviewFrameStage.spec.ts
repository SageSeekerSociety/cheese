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

import { ref } from 'vue'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import PreviewFrameStage from './PreviewFrameStage.vue'

import { setLocale } from '@/i18n'

// 这一格的「窄」是按它自己的宽度判的（`useElementSize`）。happy-dom 没有布局，量出来
// 是 0，默认就按宽处画；窄面板那几条用例自己把宽度压下去。
const panelWidth = ref(1200)
vi.mock('@vueuse/core', async (orig) => ({
  ...(await orig<typeof import('@vueuse/core')>()),
  useElementSize: () => ({ width: panelWidth, height: ref(800), stop: () => {} }),
}))

// 菜单只验「窄的时候那几件东西还在不在菜单里」，脚本里那几样原样摆出来即可：
// 真 v-menu 要布局才开得了，而这里问的不是它开不开。
const menuStubs = {
  VMenu: { template: '<div><slot name="activator" :props="{}" /><slot /></div>' },
  VList: { template: '<div><slot /></div>' },
  VListItem: { template: '<button type="button" v-bind="$attrs"><slot /></button>' },
  VListItemTitle: { template: '<span><slot /></span>' },
}

beforeEach(() => {
  setLocale('zh-CN')
  panelWidth.value = 1200
})

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

function staticFrame(): PreviewFrame {
  return { ...frame(), label: 'index.html', mime: 'text/html', live: false }
}

/** 一帧正常的、指定文件的网页：条上该有「在新标签页打开」「下载」「刷新」。 */
function staticFileMount() {
  const shown = staticFrame()
  return render(PreviewFrameStage, {
    props: {
      topicId: 'topic-1',
      path: 'index.html',
      frameName: 'frame',
      frames: [shown],
      displayedFrame: shown,
      navigation: 'loaded',
      previewFile: {
        path: 'index.html',
        content: '<h1>Hello</h1>',
        version: 'v1',
        bytes: 14,
        binary: false,
        too_large: false,
      },
      previewMime: 'text/html',
      previewUrl: 'https://example.test/index.html',
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
        VSpacer: true,
        VBtn: { template: '<button v-bind="$attrs"><slot /></button>' },
        VChip: { template: '<span><slot /></span>' },
        ...menuStubs,
      },
    },
  })
}

it('正常状态不摆运行状态块：资源和实例标识收进 chip 的 title 里', () => {
  const okay = {
    ...frame(),
    resourceId: 'resource-abcdef123456',
    instance: 'instance-abcdef123456',
    runtime: 'ready' as const,
    connection: 'online' as const,
  }
  const ui = render(PreviewFrameStage, {
    props: {
      topicId: 'topic-1',
      frameName: 'frame',
      frames: [okay],
      displayedFrame: okay,
      navigation: 'loaded',
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
    global: { stubs: { VIcon: true, VChip: { template: '<span :title="$attrs.title"><slot /></span>' } } },
  })
  expect(ui.queryByTestId('preview-runtime-alert')).toBeNull()
  expect(ui.container.querySelector('.preview-runtime-status')).toBeNull()
  expect(ui.container.textContent).not.toContain('应用已确认就绪')
  // 标识没丢：它挂在 mime 那颗 chip 的 title 上。
  const titles = [...ui.container.querySelectorAll('[title]')].map((el) => el.getAttribute('title') ?? '')
  expect(titles.some((title) => title.includes('资源 resource-abc'))).toBe(true)
})

it('应用报错时条上亮一颗小圆点，点开才看得到状态文字', async () => {
  const bad = {
    ...frame(),
    runtime: 'failed' as const,
    connection: 'disconnected' as const,
    runtimeError: '端口没起来',
  }
  const ui = render(PreviewFrameStage, {
    props: {
      topicId: 'topic-1',
      frameName: 'frame',
      frames: [bad],
      displayedFrame: bad,
      navigation: 'loaded',
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
  const dot = ui.getByTestId('preview-runtime-alert')
  expect(dot.getAttribute('aria-expanded')).toBe('false')
  // 没点开之前那几行字不在。
  expect(ui.container.textContent).not.toContain('应用报告失败')

  await fireEvent.click(dot)
  expect(dot.getAttribute('aria-expanded')).toBe('true')
  expect(ui.container.textContent).toContain('应用报告失败')
  expect(ui.container.textContent).toContain('端口没起来')
})

it('宽面板平铺：视口三键在，「…」菜单不在，刷新是一颗条上的图标按钮', () => {
  const ui = staticFileMount()
  expect(ui.queryByTestId('preview-more')).toBeNull()
  expect(ui.queryByTestId('viewport-desktop')).not.toBeNull()
  expect(ui.queryByTestId('refresh-preview')).not.toBeNull()
  // 指定了文件的那一格：条上不再重复文件名（页签上已经写着）。
  expect(ui.container.querySelector('.preview-bar__name')).toBeNull()
})

it('窄面板把「在新标签页打开」「下载」「刷新」收进「…」菜单，条上只留一颗', async () => {
  panelWidth.value = 390
  const ui = staticFileMount()
  // 视口三键在窄面板上藏起来。
  expect(ui.queryByTestId('viewport-desktop')).toBeNull()
  const more = ui.getByTestId('preview-more')
  expect(ui.container.textContent).toContain('下载')
  expect(ui.container.textContent).toContain('刷新')
  await fireEvent.click(more)
  expect(ui.container.textContent).toContain('在新标签页打开')
})

it('窄面板上视口三档进了「…」菜单，照样能切', async () => {
  panelWidth.value = 390
  const ui = staticFileMount()
  const text = ui.container.textContent ?? ''
  expect(text).toContain('桌面')
  expect(text).toContain('手机')
  const items = ui.getAllByRole('menuitemradio')
  const mobile = items.find((el) => (el.textContent ?? '').includes('手机'))!
  await fireEvent.click(mobile)
  expect(ui.container.querySelector('[data-viewport="mobile"]')).not.toBeNull()
})

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

it('支持在桌面、平板、手机视口宽度之间切换，换文件时恢复为桌面视口', async () => {
  const staticFrame = { ...frame(), label: 'index.html', mime: 'text/html', live: false }
  const ui = render(PreviewFrameStage, {
    props: {
      topicId: 'topic-1',
      frameName: 'frame',
      frames: [staticFrame],
      displayedFrame: staticFrame,
      navigation: 'loaded',
      previewFile: {
        path: 'index.html',
        content: '<h1>Hello</h1>',
        version: 'v1',
        bytes: 14,
        binary: false,
        too_large: false,
      },
      previewMime: 'text/html',
      previewUrl: 'https://example.test/index.html',
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

  const stage = ui.container.querySelector('.preview-frames') as HTMLElement
  expect(stage.getAttribute('data-viewport')).toBe('desktop')

  await fireEvent.click(ui.getByTestId('viewport-tablet'))
  expect(stage.getAttribute('data-viewport')).toBe('tablet')
  expect(stage.classList.contains('preview-frames--tablet')).toBe(true)

  await fireEvent.click(ui.getByTestId('viewport-mobile'))
  expect(stage.getAttribute('data-viewport')).toBe('mobile')
  expect(stage.classList.contains('preview-frames--mobile')).toBe(true)

  await ui.rerender({
    previewFile: {
      path: 'other.html',
      content: '<h1>Other</h1>',
      version: 'v2',
      bytes: 14,
      binary: false,
      too_large: false,
    },
  })
  expect(stage.getAttribute('data-viewport')).toBe('desktop')
})
