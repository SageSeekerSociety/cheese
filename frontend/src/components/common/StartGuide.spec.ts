/** 手把手引导那一层浮层：圈套在哪颗按钮上、气泡写哪一句、什么时候整块不出现。
 *
 *  尺寸全部靠假造 `getBoundingClientRect` 喂进去：jsdom 把每个元素都量成 0×0，而
 *  这一层的全部行为都长在「目标在哪、多大」上面。
 *
 *  浮层本身 `Teleport` 到 `<body>`（换页时 `.app-content` 会演一个 translateX 的动
 *  画，fixed 的定位基准会被祖先的 transform 换掉），所以下面从 `document.body` 上
 *  找它，不从 `container` 里找——这正是它该在的地方。
 */
import type { StartStepKey } from '@/lib/startGuide'

import { defineComponent, h, nextTick, ref, withDirectives } from 'vue'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { __resetStartGuideForTests, vGuideAnchor } from '@/composables/useStartGuide'

import StartGuide from './StartGuide.vue'

import { setLocale } from '@/i18n'

interface Box {
  left: number
  top: number
  width: number
  height: number
}

/** 目标各自量出来多少，按 `data-anchor` 认。没登记的就是 0×0。 */
const rects = new Map<string, Box>()

function asRect(b: Box): DOMRect {
  return { ...b, right: b.left + b.width, bottom: b.top + b.height, x: b.left, y: b.top } as DOMRect
}

/**
 * 挂一层浮层，外加若干颗带锚点的按钮，然后等一拍。
 *
 * 那一拍是必须的：指令的 `mounted` 排在 post-flush 队列里，比浮层自己的第一次量尺
 * 寸还晚，登记表涨一格之后浮层才会再量一次。真跑起来这一拍在浏览器上屏之前就过去
 * 了，人看不到中间态。
 */
async function mountGuide(
  opts: {
    step?: StartStepKey
    anchors?: string[]
    agent?: string
    /** 这些名字的按钮真的挂出来；没挂的名字就是「不在页面上」。 */
    present?: string[]
    skip?: () => void
  } = {}
) {
  const names = opts.present ?? ['rail-add']
  const Host = defineComponent(() => () => [
    ...names.map((name) =>
      withDirectives(h('button', { 'data-anchor': name, type: 'button' }), [[vGuideAnchor, name]])
    ),
    h(StartGuide, {
      step: opts.step ?? 'project',
      anchors: opts.anchors ?? ['rail-add'],
      agent: opts.agent,
      onSkip: opts.skip,
    }),
  ])
  const view = render(Host)
  await nextTick()
  return view
}

const overlay = () => document.body.querySelector<HTMLElement>('.sg')
const ring = () => document.body.querySelector<HTMLElement>('.sg__ring')
const bubble = () => document.body.querySelector<HTMLElement>('.sg__bubble')
const text = () => document.body.querySelector<HTMLElement>('.sg__text')?.textContent?.trim()

beforeEach(() => {
  setLocale('zh-CN')
  rects.clear()
  __resetStartGuideForTests()
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    const name = (this as HTMLElement).dataset?.anchor
    return asRect((name && rects.get(name)) || { left: 0, top: 0, width: 0, height: 0 })
  })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  __resetStartGuideForTests()
})

describe('一圈高亮指着目标', () => {
  it('圈套在按钮四周，留出一圈空隙', async () => {
    rects.set('rail-add', { left: 20, top: 300, width: 48, height: 48 })
    await mountGuide()

    expect(ring()!.style.left).toBe('14px')
    expect(ring()!.style.top).toBe('294px')
    expect(ring()!.style.width).toBe('60px')
    expect(ring()!.style.height).toBe('60px')
  })

  it('圈照抄目标自己的圆角，圆按钮不会被套上方的框', async () => {
    rects.set('rail-add', { left: 20, top: 300, width: 48, height: 48 })
    await mountGuide()
    // jsdom 量不出圆角时不写这个内联样式，留给样式表里那条 --radius-lg。
    expect(ring()!.style.borderRadius).toBe('')
  })

  it('几个候选里取第一个真的在页面上的那个', async () => {
    rects.set('new-project', { left: 100, top: 300, width: 80, height: 36 })
    // rail-add 在这台机器上没有（手机上左侧 rail 不画），落在待办页那颗按钮上。
    await mountGuide({ present: ['new-project'], anchors: ['rail-add', 'new-project'] })
    expect(ring()!.style.left).toBe('94px')
  })

  it('横向夹进窗口里：贴着屏幕左边的格子不会把气泡推出去', async () => {
    rects.set('rail-add', { left: 0, top: 300, width: 48, height: 48 })
    await mountGuide()
    expect(bubble()!.style.left).toBe('12px')
  })
})

describe('气泡', () => {
  it('写的是这一步那句话——建项目那句，和放材料那句是引导自己的', async () => {
    rects.set('rail-add', { left: 20, top: 300, width: 48, height: 48 })
    await mountGuide({ step: 'project' })
    expect(text()).toBe('从这里开一个项目')

    cleanup()
    rects.set('rail-add', { left: 20, top: 300, width: 48, height: 48 })
    await mountGuide({ step: 'materials' })
    expect(text()).toBe('资料库里放也行')
  })

  it('说上话、接仓库、请同事这三句和「开始清单」那一行是同一句', async () => {
    const cases: [StartStepKey, string][] = [
      ['talk', '跟芝士说第一句话'],
      ['repo', '让成果进代码仓库'],
      ['people', '把同事请进来'],
    ]
    for (const [step, line] of cases) {
      cleanup()
      rects.set('rail-add', { left: 20, top: 300, width: 48, height: 48 })
      await mountGuide({ step, agent: '芝士' })
      expect(text()).toBe(line)
    }
  })

  it('目标在屏幕下半时翻到它上面，免得气泡掉出屏幕', async () => {
    vi.stubGlobal('innerHeight', 600)

    rects.set('rail-add', { left: 20, top: 500, width: 48, height: 48 })
    await mountGuide()
    expect(bubble()!.style.transform).toBe('translateY(-100%)')

    cleanup()
    rects.set('rail-add', { left: 20, top: 100, width: 48, height: 48 })
    await mountGuide()
    expect(bubble()!.style.transform).toBe('')
  })

  it('「跳过引导」只在气泡上，点下去把这一步交回去', async () => {
    rects.set('rail-add', { left: 20, top: 300, width: 48, height: 48 })
    const skip = vi.fn()
    await mountGuide({ skip })

    const button = document.body.querySelector<HTMLElement>('.sg__skip')!
    expect(button.textContent?.trim()).toBe('跳过引导')
    await fireEvent.click(button)
    expect(skip).toHaveBeenCalledTimes(1)
  })

  it('换下一步时换一句话', async () => {
    rects.set('rail-add', { left: 20, top: 300, width: 48, height: 48 })
    const step = ref<StartStepKey>('talk')
    const Host = defineComponent(() => () => [
      withDirectives(h('button', { 'data-anchor': 'rail-add', type: 'button' }), [[vGuideAnchor, 'rail-add']]),
      h(StartGuide, { step: step.value, anchors: ['rail-add'], agent: '芝士' }),
    ])
    render(Host)
    await nextTick()
    expect(text()).toBe('跟芝士说第一句话')

    step.value = 'materials'
    await nextTick()
    expect(text()).toBe('资料库里放也行')
  })
})

describe('气泡不压住能点、能输入的东西', () => {
  /** 一只输入框：上面是打字的那一块，左下角一颗回形针，就是这一步要指的。 */
  async function mountComposer(extra: { wall?: boolean } = {}) {
    rects.set('box', { left: 0, top: 640, width: 600, height: 100 })
    rects.set('field', { left: 12, top: 650, width: 576, height: 40 })
    rects.set('composer-attach', { left: 12, top: 700, width: 28, height: 28 })
    if (extra.wall) rects.set('wall', { left: 0, top: 0, width: 1024, height: 640 })
    const Host = defineComponent(() => () => [
      extra.wall ? h('button', { 'data-anchor': 'wall', type: 'button' }) : null,
      h('div', { 'data-anchor': 'box' }, [
        h('textarea', { 'data-anchor': 'field' }),
        withDirectives(h('button', { 'data-anchor': 'composer-attach', type: 'button' }), [
          [vGuideAnchor, 'composer-attach'],
        ]),
      ]),
      h(StartGuide, { step: 'materials', anchors: ['composer-attach'] }),
    ])
    render(Host)
    await nextTick()
    await nextTick()
  }

  it('指着输入框里的按钮时，气泡让到整只输入框外面，不落在打字的那一块上', async () => {
    await mountComposer()
    expect(text()).toBe('资料库里放也行')
    // 底边停在输入框上沿再往上一点：打字那一块（650 起）整块露在外面。
    expect(bubble()!.style.transform).toBe('translateY(-100%)')
    expect(parseFloat(bubble()!.style.top)).toBeLessThanOrEqual(640)
  })

  it('哪儿都会压住东西时不放气泡，圈照样指着那颗按钮', async () => {
    await mountComposer({ wall: true })
    expect(ring()).not.toBeNull()
    expect(bubble()).toBeNull()
  })
})

describe('找不到目标就整块不出现', () => {
  it('目标不在这页上', async () => {
    await mountGuide({ present: [], anchors: ['rail-add'] })
    expect(overlay()).toBeNull()
  })

  it('目标登记着，但量出来是 0×0——还没排完版，或者被 display:none 藏起来了', async () => {
    await mountGuide({ present: ['rail-add'] })
    expect(overlay()).toBeNull()
  })

  it('目标不在，同一个位置上也没有别的东西可指', async () => {
    rects.set('composer-attach', { left: 20, top: 300, width: 28, height: 28 })
    // 第 2 步要指输入框，而这一页上只有附件按钮：宁可不画，也不指错东西。
    await mountGuide({ step: 'talk', anchors: ['composer-input'], present: ['composer-attach'] })
    expect(overlay()).toBeNull()
  })
})
