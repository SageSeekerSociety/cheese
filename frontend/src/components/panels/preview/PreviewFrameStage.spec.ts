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

import { nextTick, ref } from 'vue'
import { cleanup, fireEvent, render, within } from '@testing-library/vue'
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
  VBtn: { template: '<button v-bind="$attrs"><slot /></button>' },
  VSpacer: true,
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
      previewAppUp: true,
      previewError: null,
      previewReadError: null,
      pickTarget: null,
      pickOn: false,
      agentName: '芝士',
    },
    global: { stubs: { VIcon: true, VChip: { template: '<span><slot /></span>' }, ...menuStubs } },
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
        head: '<h1>Hello</h1>',
        version: 'v1',
        bytes: 14,
        binary: false,
        too_large: false,
      },
      previewMime: 'text/html',
      previewUrl: 'https://example.test/index.html',
      previewAppNote: '',
      previewTunnelUp: true,
      previewAppUp: true,
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
        ...menuStubs,
      },
    },
  })
}

it('正常状态不另占一行：连通链三段都是绿勾，资源和实例标识收在链点开之后', () => {
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
      previewAppNote: 'npm run dev',
      previewTunnelUp: true,
      previewAppUp: true,
      previewError: null,
      previewReadError: null,
      pickTarget: null,
      pickOn: false,
      agentName: '芝士',
    },
    global: { stubs: { VIcon: true, ...menuStubs } },
  })
  expect(ui.queryByTestId('preview-runtime-alert')).toBeNull()
  const chain = ui.getByTestId('preview-chain')
  expect(chain.textContent?.replace(/\s+/g, '')).toBe('平台✓通道✓应用✓')
  // 标识没丢：链点开之后最下面那一行。
  const detail = ui.getByTestId('preview-chain-detail')
  expect(detail.textContent).toContain('资源 resource-abc')
  expect(detail.textContent).toContain('应用已确认就绪')
})

it('页面报错时链上「应用」那段变红，点开看得到原因', () => {
  const bad = {
    ...frame(),
    runtime: 'failed' as const,
    connection: 'online' as const,
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
      previewAppNote: 'npm run dev',
      previewTunnelUp: true,
      previewAppUp: true,
      previewError: null,
      previewReadError: null,
      pickTarget: null,
      pickOn: false,
      agentName: '芝士',
    },
    global: { stubs: { VIcon: true, ...menuStubs } },
  })
  const alert = ui.getByTestId('preview-runtime-alert')
  expect(alert.querySelector('[data-state="down"]')?.textContent).toContain('应用')
  const detail = ui.getByTestId('preview-chain-detail')
  // 四种断法都是同一套句子（`chain.json`）：这一段说的是「页面自己报了错」。
  expect(detail.textContent).toContain('页面自己报了错：端口没起来')
  expect(detail.textContent).toContain('应用报告失败')
})

it('宽面板平铺：视口三键在，「…」菜单不在，刷新是一颗条上的图标按钮', () => {
  const ui = staticFileMount()
  expect(ui.queryByTestId('preview-more')).toBeNull()
  expect(ui.queryByTestId('viewport-desktop')).not.toBeNull()
  expect(ui.queryByTestId('refresh-preview')).not.toBeNull()
  // 指定了文件的那一格：条上不再重复文件名（页签上已经写着）。
  expect(ui.container.querySelector('.preview-bar__name')).toBeNull()
})

it('窄面板把「在新标签页打开」「下载」收进「…」菜单；刷新留在地址栏旁边', async () => {
  panelWidth.value = 390
  const ui = staticFileMount()
  // 视口三键不跟着进菜单：它说的是「现在按哪种宽度排」，窄的时候更要看得见（下一条）。
  expect(ui.queryByTestId('viewport-desktop')).not.toBeNull()
  const more = ui.getByTestId('preview-more')
  expect(ui.container.textContent).toContain('下载')
  expect(ui.getByTestId('refresh-preview')).not.toBeNull()
  // 连通链收成一颗点。
  expect(ui.container.querySelector('.browser-chain--dot')).not.toBeNull()
  await fireEvent.click(more)
  expect(ui.container.textContent).toContain('在新标签页打开')
})

it('视口三键在窄面板上也平铺在条上，窄的时候照样能换一种宽度看', async () => {
  panelWidth.value = 390
  const ui = staticFileMount()
  expect(ui.queryByTestId('viewport-tablet')).not.toBeNull()
  const mobile = ui.getByTestId('viewport-mobile')
  await fireEvent.click(mobile)
  expect(ui.container.querySelector('[data-viewport="mobile"]')).not.toBeNull()
})

it('窄面板默认按手机档打开；宽面板默认桌面档', () => {
  panelWidth.value = 390
  const narrow = staticFileMount()
  expect(narrow.container.querySelector('[data-viewport="mobile"]')).not.toBeNull()

  cleanup()
  panelWidth.value = 1200
  const wide = staticFileMount()
  expect(wide.container.querySelector('[data-viewport="desktop"]')).not.toBeNull()
})

it('固定档按那一档的原宽排：面板比它宽时就是 768 / 375', async () => {
  panelWidth.value = 1200
  const ui = staticFileMount()
  const surface = () => ui.container.querySelector('.preview-frames') as HTMLElement
  // 桌面档没有固定宽度：铺满面板。
  expect(surface().style.width).toBe('')
  await fireEvent.click(ui.getByTestId('viewport-tablet'))
  expect(surface().style.width).toBe('768px')
  expect(surface().style.transform).toBe('')
  await fireEvent.click(ui.getByTestId('viewport-mobile'))
  expect(surface().style.width).toBe('375px')
})

it('面板装不下那一档时整块等比缩小，而不是压成面板宽再管它叫那一档', async () => {
  panelWidth.value = 600
  const ui = staticFileMount()
  await fireEvent.click(ui.getByTestId('viewport-tablet'))
  const surface = ui.container.querySelector('.preview-frames') as HTMLElement
  // 宽度还是 768（排版按平板走），画出来按 600/768 缩。
  expect(surface.style.width).toBe('768px')
  expect(surface.style.transform).toBe('scale(0.78125)')
  expect(surface.getAttribute('data-scale')).toBe('0.78125')
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
        head: '<svg viewBox="0 0 10 10"><circle r="4" /></svg>',
        version: 'v1',
        bytes: 47,
        binary: false,
        too_large: false,
      },
      previewMime: 'image/svg+xml',
      previewUrl: 'https://example.test/diagram.svg',
      previewAppNote: '',
      previewTunnelUp: true,
      previewAppUp: true,
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
        ...menuStubs,
      },
    },
  })

  expect(ui.queryByTestId('frame-source')).toBeNull()
  await fireEvent.click(ui.getByTestId('toggle-frame-source'))
  expect(ui.getByTestId('frame-source').textContent).toContain('<circle r="4" />')
  // 网页源码折行（原来是 `<pre>` 的 `pre-wrap`）：生成的 HTML 常常整页一两行，
  // 横着滚等于看不见。代码文件那一档才默认横滚。
  expect(ui.getByTestId('frame-source').querySelector('.code-view__scroll--wrap')).toBeTruthy()

  await ui.rerender({
    previewFile: {
      path: 'other.svg',
      content: '<svg viewBox="0 0 20 20"><rect width="10" height="10" /></svg>',
      head: '<svg viewBox="0 0 20 20"><rect width="10" height="10" /></svg>',
      version: 'v2',
      bytes: 62,
      binary: false,
      too_large: false,
    },
  })
  expect(ui.queryByTestId('frame-source')).toBeNull()
})

it('支持在桌面、平板、手机视口宽度之间切换，换文件时回到默认档', async () => {
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
        head: '<h1>Hello</h1>',
        version: 'v1',
        bytes: 14,
        binary: false,
        too_large: false,
      },
      previewMime: 'text/html',
      previewUrl: 'https://example.test/index.html',
      previewAppNote: '',
      previewTunnelUp: true,
      previewAppUp: true,
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
        ...menuStubs,
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
      head: '<h1>Other</h1>',
      version: 'v2',
      bytes: 14,
      binary: false,
      too_large: false,
    },
  })
  expect(stage.getAttribute('data-viewport')).toBe('desktop')
})

function appMount(over: Record<string, unknown> = {}, shown: Partial<PreviewFrame> = {}) {
  const live = { ...frame(), connection: 'online' as const, ...shown }
  return render(PreviewFrameStage, {
    props: {
      topicId: 'topic-1',
      frameName: 'frame',
      frames: [live],
      displayedFrame: live,
      navigation: 'loaded',
      previewFile: null,
      previewMime: 'text/html',
      previewUrl: 'https://example.test/',
      previewAppNote: 'npm run dev',
      previewTunnelUp: true,
      previewAppUp: true,
      previewError: null,
      previewReadError: null,
      pickTarget: 'region',
      pickOn: false,
      agentName: '芝士',
      ...over,
    },
    global: { stubs: { VIcon: true, ...menuStubs } },
  })
}

it('地址栏跟着帧报来的地址走（只显示路径），回车跳到敲的那一个', async () => {
  const ui = appMount({}, { href: 'https://preview-x.example/signup?step=2', bridged: true })
  const address = ui.getByTestId('browser-address') as HTMLInputElement
  expect(address.value).toBe('/signup?step=2')
  expect(address.readOnly).toBe(false)
  await fireEvent.update(address, '/faq')
  await fireEvent.keyDown(address, { key: 'Enter' })
  expect(ui.emitted('navigate')).toEqual([['/faq']])
})

it('房间文件的地址栏剥掉平台前缀显示，回车再补回去', async () => {
  // 房间文件挂在 `/_cheese/room/` 下。读了显示的是剥过的房间路径，但桥那边
  // `new URL(path, location.href)` 是相对内容域解析的——不补回前缀，敲 `/pages/b.html`
  // 会跳到内容域的根下而不是房间里的那一份。
  const ui = appMount({}, { href: 'https://preview-x.example/_cheese/room/pages/a.html', bridged: true })
  const address = ui.getByTestId('browser-address') as HTMLInputElement
  expect(address.value).toBe('/pages/a.html')
  await fireEvent.update(address, '/pages/b.html')
  await fireEvent.keyDown(address, { key: 'Enter' })
  expect(ui.emitted('navigate')).toEqual([['/_cheese/room/pages/b.html']])
})

it('地址栏里带着房间前缀的那串原样提交，不再补一层', async () => {
  // 地址栏的提示里写的是完整地址，照抄一份下来再按回车不该跳到 `/_cheese/room/_cheese/
  // room/…` 那个 404 上。
  const ui = appMount({}, { href: 'https://preview-x.example/_cheese/room/pages/a.html', bridged: true })
  const address = ui.getByTestId('browser-address') as HTMLInputElement
  await fireEvent.update(address, '/_cheese/room/pages/b.html')
  await fireEvent.keyDown(address, { key: 'Enter' })
  expect(ui.emitted('navigate')).toEqual([['/_cheese/room/pages/b.html']])
})

it('地址里解不出正确回路的转义照原样显示原样提交', async () => {
  // `%2F` 解出来是真分隔符，解了就写不回去：显示和提交都照原样，看到什么就跳什么。
  const ui = appMount({}, { href: 'https://preview-x.example/a%2Fb?q=1', bridged: true })
  const address = ui.getByTestId('browser-address') as HTMLInputElement
  expect(address.value).toBe('/a%2Fb?q=1')
  await fireEvent.keyDown(address, { key: 'Enter' })
  expect(ui.emitted('navigate')).toBeUndefined()
})

it('输入法拼字时那一下回车不算跳转', async () => {
  const ui = appMount({}, { href: 'https://preview-x.example/signup', bridged: true })
  const address = ui.getByTestId('browser-address') as HTMLInputElement
  await fireEvent.update(address, '/成品')
  await fireEvent.keyDown(address, { key: 'Enter', isComposing: true })
  expect(ui.emitted('navigate')).toBeUndefined()
  await fireEvent.keyDown(address, { key: 'Enter' })
  expect(ui.emitted('navigate')).toEqual([['/成品']])
})

it('没装桥的页面地址栏只读，显示入口名字；刷新退回整帧重来', async () => {
  const ui = appMount()
  const address = ui.getByTestId('browser-address') as HTMLInputElement
  expect(address.value).toBe('week-1.html')
  expect(address.readOnly).toBe(true)
  await fireEvent.click(ui.getByTestId('refresh-preview'))
  expect(ui.emitted('refresh')).toHaveLength(1)
  expect(ui.emitted('history')).toBeUndefined()
})

it('后退、前进按帧说的亮灭；有桥时刷新交给帧里那一页自己重载', async () => {
  const ui = appMount({}, { href: 'https://p.example/a', bridged: true, canBack: true, canForward: false })
  expect((ui.getByTestId('browser-forward') as HTMLButtonElement).disabled).toBe(true)
  await fireEvent.click(ui.getByTestId('browser-back'))
  await fireEvent.click(ui.getByTestId('refresh-preview'))
  expect(ui.emitted('history')).toEqual([['back'], ['reload']])
  expect(ui.emitted('refresh')).toBeUndefined()
})

it('电脑睡了：显示压暗的静态快照和唤醒卡，快照不跑脚本；按钮发唤醒', async () => {
  const ui = appMount({
    previewTunnelUp: false,
    previewAppUp: false,
    navigationError: '应用预览暂不可用',
    appSnapshot: { html: '<!DOCTYPE html><p>报名</p>', age: '4 分钟前' },
  })
  const card = ui.getByTestId('preview-app-sleep')
  expect(card.getAttribute('data-kind')).toBe('machine')
  expect(card.textContent).toContain('运行环境已休眠（当前为静态快照）')
  expect(card.textContent).toContain('4 分钟前')
  const shot = ui.getByTestId('preview-snapshot') as HTMLIFrameElement
  expect(shot.getAttribute('sandbox')).toBe('')
  expect(shot.getAttribute('srcdoc')).toContain('报名')
  // 那句报错不再另起一行：卡上已经说清了。
  expect(ui.queryByRole('alert')).toBeNull()
  // 链上通道、应用两段变灰。
  const down = [...ui.container.querySelectorAll('.browser-chain__seg')].map((el) => el.getAttribute('data-state'))
  expect(down).toEqual(['ok', 'down', 'wait'])
  // 快照盖着的时候，底下那一帧不接点击也不接键盘：读者看到的、能点的是快照，旧页面
  // 被点一下会走进错误页，从半透明的快照底下透出来。
  const oldFrame = ui.container.querySelector('iframe.preview-frame') as HTMLIFrameElement
  expect(oldFrame.hasAttribute('inert')).toBe(true)
  expect(ui.container.querySelector('.preview-frames--covered')).not.toBeNull()
  // 睡着时后退前进不亮，地址栏不能敲。
  expect((ui.getByTestId('browser-back') as HTMLButtonElement).disabled).toBe(true)
  await fireEvent.click(ui.getByTestId('preview-wake'))
  expect(ui.emitted('wake')).toHaveLength(1)
})

it('电脑在、应用没答：卡请队友重启；还没留过快照时说清楚，旧页面压暗留着', () => {
  const ui = appMount({ previewAppUp: false })
  const card = ui.getByTestId('preview-app-sleep')
  expect(card.getAttribute('data-kind')).toBe('app')
  expect(card.textContent).toContain('应用没有应答')
  expect(card.textContent).not.toContain('静态快照）')
  expect(card.textContent).toContain('还没有留下快照')
  expect(card.textContent).toContain('请芝士重启应用')
  expect(ui.container.querySelector('.preview-frames--asleep')).not.toBeNull()
})

it('唤醒中：卡变成分步进度，按钮收起；圈选打开时卡让开', async () => {
  const steps = [
    { key: 'machine', label: 'work.room.preview.sleep.stepMachine', state: 'done' },
    { key: 'tunnel', label: 'work.room.preview.sleep.stepTunnel', state: 'done' },
    { key: 'agent', label: 'work.room.preview.sleep.stepAgent', state: 'now' },
    { key: 'port', label: 'work.room.preview.sleep.stepPort', state: 'todo' },
  ]
  const ui = appMount({ previewTunnelUp: true, previewAppUp: false, wakeSteps: steps })
  const rows = [...ui.getByTestId('preview-wake-steps').children].map((el) => [
    el.textContent?.trim(),
    el.getAttribute('data-state'),
  ])
  expect(rows).toEqual([
    ['开机', 'done'],
    ['连上通道', 'done'],
    ['芝士重启应用', 'now'],
    ['等端口应答', 'todo'],
  ])
  expect(ui.queryByTestId('preview-wake')).toBeNull()
  await ui.rerender({ pickOn: true })
  expect((ui.container.querySelector('.app-sleep__card') as HTMLElement).style.display).toBe('none')
  expect(ui.getByTestId('preview-region-pick')).toBeTruthy()
})

it('冷启动那一段摆骨架：等的时候不是一片白', () => {
  // 第一次连上（机器还在起）能等一两分钟，原来内容区是一片白。骨架只在真没有内容可看时
  // 摆：换一页时上一帧还在屏幕上，那不是「空的」，不能盖。
  const waiting = mount('navigating', null)
  expect(waiting.queryByTestId('preview-skeleton')).not.toBeNull()

  cleanup()
  const withOldPage = mount('navigating', frame())
  expect(withOldPage.queryByTestId('preview-skeleton')).toBeNull()
})

it('看源码只摆了开头时说一句，再给一条下载整份的路', async () => {
  // 一份超过读入上限的网页：`content` 是 null，`head` 是前 256 KiB。切到「看原文」
  // 时读者看到的是开头，滑到底以为整份就这么长——原来那一页什么也不说。
  const big = { ...frame(), label: 'index.html', mime: 'text/html', live: false }
  const ui = render(PreviewFrameStage, {
    props: {
      topicId: 'topic-1',
      frameName: 'frame',
      frames: [big],
      displayedFrame: big,
      navigation: 'loaded',
      previewFile: {
        path: 'site/index.html',
        content: null,
        head: '<html><body><h1>开头</h1>',
        version: 'v1',
        bytes: 8_000_000,
        binary: false,
        too_large: true,
      },
      previewMime: 'text/html',
      previewUrl: 'https://example.test/index.html',
      previewAppNote: '',
      previewTunnelUp: true,
      previewAppUp: true,
      previewError: null,
      previewReadError: null,
      pickTarget: null,
      pickOn: false,
      agentName: '芝士',
    },
    global: { stubs: { VIcon: true, VChip: { template: '<span><slot /></span>' }, ...menuStubs } },
  })

  await fireEvent.click(ui.getByTestId('toggle-frame-source'))
  const cut = ui.getByTestId('frame-source-cut')
  expect(cut.textContent).toContain('site/index.html')
  expect(cut.textContent).toContain('已经截断')
  await fireEvent.click(within(cut).getByText('下载'))
  expect(ui.emitted('download')).toHaveLength(1)

  // 整份读得下来的网页：切到看原文时不该出现这一条。
  await ui.rerender({
    previewFile: {
      path: 'site/index.html',
      content: '<html><body><h1>整份</h1></body></html>',
      head: '<html><body><h1>整份</h1></body></html>',
      version: 'v2',
      bytes: 40,
      binary: false,
      too_large: false,
    },
  })
  expect(ui.queryByTestId('frame-source-cut')).toBeNull()
})

it('只有图标的按钮有可读名字，加载那一行不跟着秒数反复播报', async () => {
  const staticFrame = { ...frame(), label: 'index.html', mime: 'text/html', live: false }
  const ui = render(PreviewFrameStage, {
    props: {
      topicId: 'topic-1',
      path: 'site/index.html',
      frameName: 'frame',
      frames: [staticFrame],
      displayedFrame: staticFrame,
      navigation: 'loaded',
      previewFile: {
        path: 'site/index.html',
        content: '<h1>hi</h1>',
        head: '<h1>hi</h1>',
        version: 'v1',
        bytes: 12,
        binary: false,
        too_large: false,
      },
      previewMime: 'text/html',
      previewUrl: 'https://example.test/index.html',
      previewAppNote: '',
      previewTunnelUp: true,
      previewAppUp: true,
      previewError: null,
      previewReadError: null,
      pickTarget: null,
      pickOn: false,
      agentName: '芝士',
    },
    global: { stubs: { VIcon: true, VChip: { template: '<span><slot /></span>' }, ...menuStubs } },
  })
  // 平铺档那几颗只有图标：`title` 能兜出名字，但它先是悬停提示，读屏按键板时要的是
  // `aria-label`。窄档菜单里那几行本来就带字，不受影响。
  // 这三颗以前只有 `title`：按键板的人要等悬停才听得见名字，读屏顺着名字找也找不到。
  // 现在名字和提示各一份，名字说的是这一颗干什么。
  for (const label of ['在新标签页打开', '下载']) {
    const button = [...ui.container.querySelectorAll('button')].find(
      (candidate) => candidate.getAttribute('aria-label') === label
    )
    expect(button, label).toBeTruthy()
    expect(button!.getAttribute('title')).toBe(label)
  }
})

it('加载时的播报是一条独立的、不随秒数变的信息', async () => {
  const waiting = { ...frame(), label: 'index.html', mime: 'text/html', live: false }
  const ui = render(PreviewFrameStage, {
    props: {
      topicId: 'topic-1',
      frameName: 'frame',
      frames: [],
      displayedFrame: null,
      navigation: 'authorizing',
      previewFile: null,
      previewMime: 'text/html',
      previewUrl: null,
      previewAppNote: '课程网站',
      previewTunnelUp: true,
      previewAppUp: true,
      previewError: null,
      previewReadError: null,
      pickTarget: null,
      pickOn: false,
      agentName: '芝士',
    },
    global: { stubs: { VIcon: true, VChip: { template: '<span><slot /></span>' }, ...menuStubs } },
  })
  void waiting
  // 可见那一行说「正在获取预览授权」；播报那一条是同一个开头，但它是独立的一个
  // `role="status"`——秒数在可见那一行里涨，播报这一条只在阶段变化时换。
  const live = ui.getByTestId('preview-step-live')
  expect(live.textContent).toContain('正在获取预览授权')
  expect(live.getAttribute('role')).toBe('status')
  // 可见那一行不再挂着 `role="status"`（挂了就会每两秒念一遍整句）。
  const visible = ui.container.querySelector('.text-caption.px-3')
  expect(visible?.getAttribute('role')).toBeNull()
})

it('断网时红条说设备断网、不给重试；网络回来按钮就回来', async () => {
  // 断网时按「重试」只会再失败一次，而且这一格会在 `online` 那一下自己重取
  // （见 `PanelPreviewView`）。所以这一条只说话，不给那颗按不动的按钮。
  const offline = vi.fn()
  Object.defineProperty(window.navigator, 'onLine', { configurable: true, get: offline })
  offline.mockReturnValue(false)
  window.dispatchEvent(new Event('offline'))
  const bad = { ...frame(), runtime: 'failed' as const, runtimeError: 'x', connection: 'online' as const }
  const ui = render(PreviewFrameStage, {
    props: {
      topicId: 't',
      frameName: 'frame',
      frames: [bad],
      displayedFrame: bad,
      navigation: 'loaded',
      previewFile: null,
      previewMime: 'text/html',
      previewUrl: 'https://example.test/',
      previewAppNote: '课程网站',
      // 通道和设备都在、页面自己报错：这一档不会变成「睡着了」那张卡，红条一直摆着，
      // 正好用它看按钮在不在。
      previewTunnelUp: true,
      previewAppUp: true,
      previewError: null,
      previewReadError: null,
      pickTarget: null,
      pickOn: false,
      agentName: '芝士',
    },
    global: { stubs: { VIcon: true, VChip: { template: '<span><slot /></span>' }, ...menuStubs } },
  })
  const bar = ui.container.querySelector('[role=alert]')!
  expect(bar.textContent).toContain('这台设备断网了')
  expect(within(bar as HTMLElement).queryByText('重试新内容')).toBeNull()

  offline.mockReturnValue(true)
  window.dispatchEvent(new Event('online'))
  await nextTick()
  await nextTick()
  expect(ui.container.textContent).not.toContain('这台设备断网了')
  expect(ui.getByText('重试新内容')).toBeTruthy()
})
