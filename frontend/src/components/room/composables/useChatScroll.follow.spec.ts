// 停在底部的人：时间线一变高，同一帧就跟到底——不管长了多少。
import { defineComponent, h, nextTick } from 'vue'
import { render } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useChatScroll } from './useChatScroll'

let fire: () => void = () => {}

class FakeResizeObserver {
  constructor(callback: () => void) {
    fire = callback
  }
  observe() {}
  disconnect() {}
}

/** 一个高 400、内容高 `content` 的滚动框，scrollTop 照真的一样能读写。 */
function pane(content: number) {
  const el = document.createElement('div')
  let top = 0
  Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => 400 })
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => content })
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get: () => top,
    set: (v: number) => (top = Math.max(0, Math.min(v, content - 400))),
  })
  return {
    el,
    grow(by: number) {
      content += by
    },
  }
}

afterEach(() => vi.unstubAllGlobals())

async function mounted(content: number) {
  vi.stubGlobal('ResizeObserver', FakeResizeObserver)
  const box = pane(content)
  let scroll!: ReturnType<typeof useChatScroll>
  render(
    defineComponent({
      setup() {
        scroll = useChatScroll()
        return () => h('div')
      },
    })
  )
  // 和 ChatPanel 里一样，滚动框和输入框那一块是同一层里的兄弟。
  document.createElement('div').append(box.el, document.createElement('div'))
  scroll.scrollRef.value = box.el
  scroll.contentRef.value = document.createElement('div')
  await nextTick()
  box.el.scrollTop = content
  scroll.rememberScroll('room') // 那一下滚动的 scroll 事件
  return { box, scroll }
}

describe('停在底部时跟着内容走', () => {
  it('内容只长了几像素，也在这一帧跟到底', async () => {
    const { box } = await mounted(1000)
    box.grow(10)
    fire()
    expect(box.el.scrollTop).toBe(610)
  })

  it('人已经往上翻走了，内容变高不拽他回来', async () => {
    const { box, scroll } = await mounted(1000)
    box.el.scrollTop = 100
    scroll.atBottom.value = false
    box.grow(10)
    fire()
    expect(box.el.scrollTop).toBe(100)
  })
})

// 刚打开的话题停在底部：底下那几行第一次画出来，比占位的估计高度高出一大截。浏览器
// 先用滚动锚定把 scrollTop 挪一点、发一个 scroll 事件，ResizeObserver 晚一步才看到
// 变高——线上实测每次整页打开都停在离最新一条 1152px 的地方。
describe('打开话题，底下的行画出来变高了', () => {
  it('浏览器自己挪的那一下不算人往上翻：最后仍停在最新一条', async () => {
    const { box, scroll } = await mounted(1000)
    expect(box.el.scrollTop).toBe(600)
    box.grow(1800)
    box.el.scrollTop = 600 + 900 // 锚定只补回了一半，离底部还差 900px
    scroll.rememberScroll('t-open') // 那个 scroll 事件
    fire() // 然后才是 ResizeObserver
    expect(box.el.scrollTop).toBe(2400)
    expect(scroll.atBottom.value).toBe(true)
    expect(scroll.restoresToBottom('t-open')).toBe(true)
  })

  // 反方向：上面那几行收短（离屏的消息行从估计高度换成真实高度、一条长回复被折叠成
  // 阅读高度），下面同时长出来。浏览器照样自己把 scrollTop 往上挪、发一个 scroll 事件，
  // ResizeObserver 还是晚一步——和上面那一组是同一件事的两个方向。
  it('浏览器自己往上挪的那一下也不算人往上翻：最后仍停在最新一条', async () => {
    const { box, scroll } = await mounted(2000)
    expect(box.el.scrollTop).toBe(1600)
    box.grow(-400) // 上面收短了 400
    box.grow(300) // 下面同时长出来 300，正文还在变长
    box.el.scrollTop = 1600 - 400 // 锚定把位置往上挪 400，离底部更远了
    scroll.rememberScroll('t-shrink') // 那个 scroll 事件
    fire() // 然后才是 ResizeObserver
    expect(box.el.scrollTop).toBe(1500)
    expect(scroll.atBottom.value).toBe(true)
    expect(scroll.restoresToBottom('t-shrink')).toBe(true)
  })

  it('人真的往上翻了：不跟、不拽回去，下次打开回到他停的地方', async () => {
    const { box, scroll } = await mounted(1000)
    box.el.scrollTop = 300
    scroll.rememberScroll('t-read')
    box.grow(500)
    fire()
    expect(box.el.scrollTop).toBe(300)
    expect(scroll.atBottom.value).toBe(false)
    expect(scroll.restoresToBottom('t-read')).toBe(false)
  })
})

// 正文被就地改写（话题标题刷新时每条消息都重画）：新插进来的块先按估计高度排，内容
// 短了一下，浏览器把 scrollTop 夹到那个更短的底部；真实高度随即回来，ResizeObserver
// 什么都没看到，scroll 事件里高度也和上次一样。桌面端每当项目里有件任务一动就这样
// 被甩上去几百像素，「回到最新」冒出来。
describe('正文被就地改写', () => {
  async function rewrite(box: ReturnType<typeof pane>, content: HTMLElement, to: number) {
    content.append(document.createElement('p'))
    await new Promise((r) => setTimeout(r)) // MutationObserver 的回调
    box.el.scrollTop = to // 浏览器夹下来的位置，高度已经回到原样
  }

  it('没人动，位置却往上挪了：仍算停在底部，回到最新一条', async () => {
    const { box, scroll } = await mounted(2000)
    await rewrite(box, scroll.contentRef.value!, 1600 - 620)
    scroll.rememberScroll('t-rewrite') // 那个 scroll 事件
    expect(scroll.atBottom.value).toBe(true)
    expect(box.el.scrollTop).toBe(1600)
    expect(scroll.restoresToBottom('t-rewrite')).toBe(true)
  })

  it('人正好在这时用滚轮往上翻：算他翻走了，不拽回去', async () => {
    const { box, scroll } = await mounted(2000)
    box.el.dispatchEvent(new WheelEvent('wheel', { deltaY: -300 }))
    await rewrite(box, scroll.contentRef.value!, 1300)
    scroll.rememberScroll('t-wheel')
    expect(scroll.atBottom.value).toBe(false)
    expect(box.el.scrollTop).toBe(1300)
  })

  // 在对话区点一下（点一条消息、点住又松手）位置一个像素都没动，那不是人在滚。
  // 曾经 `pointerdown` 一按就记「人在滚」，于是下面那条改写兜底失效：停住的人被留在
  // 半路，「回到最新」冒出来（真机 2026-10-24 反馈：拖到最下面又回弹一点）。
  it('只在面板上点一下、位置没动：仍算停在底部，回到最新一条', async () => {
    const { box, scroll } = await mounted(2000)
    box.el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
    window.dispatchEvent(new PointerEvent('pointerup'))
    await rewrite(box, scroll.contentRef.value!, 1600 - 306)
    scroll.rememberScroll('t-tap')
    expect(scroll.atBottom.value).toBe(true)
    expect(box.el.scrollTop).toBe(1600)
  })

  // 按住指针把这一栏挪动了位置（浏览器发的一整套指针事件里位置真的变了）仍算人在滚：
  // 那一下不该被拽回底部。
  it('按住拖动了位置：算他翻走了，不拽回去', async () => {
    const { box, scroll } = await mounted(2000)
    box.el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
    box.el.scrollTop = 1300
    window.dispatchEvent(new PointerEvent('pointermove'))
    window.dispatchEvent(new PointerEvent('pointerup'))
    await rewrite(box, scroll.contentRef.value!, 1300)
    scroll.rememberScroll('t-drag')
    expect(scroll.atBottom.value).toBe(false)
    expect(box.el.scrollTop).toBe(1300)
  })

  // 输入框里打字用的是同一批键：空格（输入法上屏就是它）、方向键（挪光标），它们的
  // keydown 一路冒到 window。那不是人在滚这一栏——按它记「人在滚」，打字的人就一路
  // 把下面那条改写兜底关掉了。
  it('在输入框里打空格：不算人在滚，仍回到最新一条', async () => {
    const { box, scroll } = await mounted(2000)
    const editor = document.createElement('div')
    editor.setAttribute('contenteditable', 'true')
    const caret = document.createElement('span')
    editor.append(caret)
    document.body.append(editor)
    for (const key of [' ', 'ArrowUp']) caret.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }))
    editor.remove()
    await rewrite(box, scroll.contentRef.value!, 1600 - 306)
    scroll.rememberScroll('t-typing')
    expect(scroll.atBottom.value).toBe(true)
    expect(box.el.scrollTop).toBe(1600)
  })

  // 焦点不在输入框里时，空格和方向键确实滚这一栏：那一下仍算他翻走了。
  it('焦点不在输入框里按空格：算他翻走了，不拽回去', async () => {
    const { box, scroll } = await mounted(2000)
    const row = document.createElement('div')
    document.body.append(row)
    row.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }))
    row.remove()
    await rewrite(box, scroll.contentRef.value!, 1300)
    scroll.rememberScroll('t-space')
    expect(scroll.atBottom.value).toBe(false)
    expect(box.el.scrollTop).toBe(1300)
  })

  // 面板**里面**的可编辑区不一样：光标走到可视范围外时，浏览器为了让光标可见会把这一栏
  // 往上挪；翻页键更是直接翻这一栏。这时候还得算——不算的话，人在这几行中间改自己的
  // 消息，被当成「没人动、位置却往上挪」而拽回底部（2026-10-24 审阅发现，合并基线上
  // 每个键都算，所以这是本次改动不能开的口子）。
  for (const key of ['PageUp', 'ArrowUp', ' ']) {
    it(`在面板里的就地编辑器里按 ${key}：算他翻走了，不拽回去`, async () => {
      const { box, scroll } = await mounted(2000)
      const editor = document.createElement('div')
      editor.setAttribute('contenteditable', 'true')
      box.el.append(editor)
      // 这个夹具里的滚动框平时挂在文档外，事件冒不到 window——先挂上去。
      document.body.append(box.el)
      editor.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }))
      await rewrite(box, scroll.contentRef.value!, 1300)
      scroll.rememberScroll('t-editable-in-pane')
      box.el.remove()
      expect(scroll.atBottom.value).toBe(false)
      expect(box.el.scrollTop).toBe(1300)
    })
  }

  // 焦点在发消息的输入框里（它在面板外）时，PageUp 挪的是输入框自己，不是这一栏。
  it('在发消息的输入框里按 PageUp：不算人在滚，仍回到最新一条', async () => {
    const { box, scroll } = await mounted(2000)
    const editor = document.createElement('div')
    editor.setAttribute('contenteditable', 'true')
    document.body.append(editor)
    editor.dispatchEvent(new KeyboardEvent('keydown', { key: 'PageUp', bubbles: true }))
    editor.remove()
    await rewrite(box, scroll.contentRef.value!, 1600 - 306)
    scroll.rememberScroll('t-pageup-composer')
    expect(scroll.atBottom.value).toBe(true)
    expect(box.el.scrollTop).toBe(1600)
  })
})

// 滚动框底下那一块（输入框、「谁在忙」那一行）在同一个任务里没了又回来：这一下滚动框
// 变高，停在底部的位置被夹上去；它回来时滚动框高度复原，位置却留在了上面。
// ResizeObserver 看不到，时间线里也一个节点都没变。
describe('滚动框底下那一块闪了一下', () => {
  it('没人动，位置却往上挪了：仍算停在底部，回到最新一条', async () => {
    const { box, scroll } = await mounted(2000)
    box.el.parentElement!.lastElementChild!.append(document.createElement('div'))
    await new Promise((r) => setTimeout(r)) // MutationObserver 的回调
    box.el.scrollTop = 1600 - 196
    scroll.rememberScroll('t-composer')
    expect(scroll.atBottom.value).toBe(true)
    expect(box.el.scrollTop).toBe(1600)
  })
})

// 时间线自己包了一层（#4662 起的 `.timeline-zone`，书签条浮在它上面），输入区是那一层
// 的兄弟：滚动框的直接父元素看不到输入区。观察它的话，「谁在忙」那一行来了又走把停在
// 底部的人夹上去 196px，会被当成他自己往上翻——桌面端（WebKit）真用户每次任务一动都中。
describe('时间线外面还包了一层，输入区在包装层外面', () => {
  async function mountedZoned(content: number) {
    vi.stubGlobal('ResizeObserver', FakeResizeObserver)
    const box = pane(content)
    const timelineZone = document.createElement('div')
    const composerZone = document.createElement('div')
    const column = document.createElement('div')
    column.append(timelineZone, composerZone)
    timelineZone.append(box.el)
    let scroll!: ReturnType<typeof useChatScroll>
    render(
      defineComponent({
        setup() {
          scroll = useChatScroll({ column: () => column })
          return () => h('div')
        },
      })
    )
    scroll.scrollRef.value = box.el
    scroll.contentRef.value = document.createElement('div')
    await nextTick()
    box.el.scrollTop = content
    scroll.rememberScroll('room')
    return { box, scroll, composerZone }
  }

  it('没人动，输入区那一块却往上挪了：仍算停在底部，回到最新一条', async () => {
    const { box, scroll, composerZone } = await mountedZoned(2000)
    composerZone.append(document.createElement('div'))
    await new Promise((r) => setTimeout(r)) // MutationObserver 的回调
    box.el.scrollTop = 1600 - 196
    scroll.rememberScroll('t-zone')
    expect(scroll.atBottom.value).toBe(true)
    expect(box.el.scrollTop).toBe(1600)
  })
})

// 停在对话最底下，点左上角书签跳到上面某一条（真人 2026-10-26 反馈：被拽回来，到不了
// 那一条）。书签浮条挂在滚动框外面，点它不算「人在滚」；而 agent 在跑时输入框上方那行
// 「用时」每秒都在改这一栏，上面那条改写兜底正好把这次往上的移动读成浏览器锚定，当场
// 钉回底部。这一下滚动是我们自己发的，两条判据都该让开。
describe('点书签跳到上面那一条', () => {
  /** 这一栏刚被就地改写：agent 在跑时每秒都有一次。 */
  async function rewriteNow(content: HTMLElement) {
    content.append(document.createElement('p'))
    await new Promise((r) => setTimeout(r)) // MutationObserver 的回调
  }

  it('是我们自己发的滚动：停在最底下点书签，落到那一条上，不拽回底部', async () => {
    const { box, scroll } = await mounted(2000)
    await rewriteNow(scroll.contentRef.value!)
    scroll.beginOwnScroll() // scrollIntoView 之前
    box.el.scrollTop = 1600 - 30 // 平滑滚动的第一帧：离底部还不到 80
    scroll.rememberScroll('t-jump')
    // 这一帧近底部、又赶上刚被改写：那一瞬间位置和「在不在底部」都还没定，跟随由
    // 窗口自己挡着（下面长高一截，也不该被拽回底部）。
    box.grow(400)
    fire()
    expect(box.el.scrollTop).toBe(1600 - 30)
    box.el.scrollTop = 1600 - 900 // 落到了书签那一条
    scroll.rememberScroll('t-jump')
    await new Promise((r) => setTimeout(r, 200)) // 安静下来，算滚完了
    expect(scroll.atBottom.value).toBe(false)
    expect(box.el.scrollTop).toBe(700)
  })

  it('跳过去之后读的人自己滚轮：仍是他翻走了，不拽回底部', async () => {
    const { box, scroll } = await mounted(2000)
    await rewriteNow(scroll.contentRef.value!)
    scroll.beginOwnScroll()
    box.el.scrollTop = 1600 - 900
    box.el.dispatchEvent(new WheelEvent('wheel', { deltaY: 300 }))
    await rewriteNow(scroll.contentRef.value!)
    box.el.scrollTop = 700 + 300
    scroll.rememberScroll('t-jump-wheel')
    expect(scroll.atBottom.value).toBe(false)
    expect(box.el.scrollTop).toBe(1000)
  })

  it('跳的地方本来就靠底部：停下之后照常跟新消息', async () => {
    const { box, scroll } = await mounted(2000)
    scroll.beginOwnScroll()
    box.el.scrollTop = 1600 - 40 // 书签正是最后一条那一带
    scroll.rememberScroll('t-jump-bottom')
    await new Promise((r) => setTimeout(r, 200)) // 安静下来，算滚完了
    expect(scroll.atBottom.value).toBe(true)
    box.grow(10)
    fire()
    expect(box.el.scrollTop).toBe(1610)
  })
})
