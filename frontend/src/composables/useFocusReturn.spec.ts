// 浮层关上时把焦点还回打开它的那个东西：还在 DOM 里才还；已经随这一次操作一起没了就不还。
import type { App, Ref } from 'vue'

import { createApp, defineComponent, h, nextTick, ref } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'

import { useFocusReturn } from './useFocusReturn'

let app: App | null = null

afterEach(() => {
  app?.unmount()
  app = null
  document.body.innerHTML = ''
})

const panel = () => document.getElementById('panel')

/** 还焦点是推迟到下一轮渲染之后的（避开 Vuetify 的焦点陷阱），等够两拍。 */
async function settle(): Promise<void> {
  await nextTick()
  await nextTick()
}

/** 浮层组件：开着的时候画一块可聚焦的面板。 */
function overlayOf(open: Ref<boolean>, fallback?: () => HTMLElement | null) {
  return defineComponent({
    setup() {
      useFocusReturn(open, fallback)
      return () => (open.value ? h('div', { id: 'panel', tabindex: -1 }, 'panel') : null)
    },
  })
}

/** 触发按钮留在 app 之外：卸载 app 不会把它一起带走。 */
function makeTrigger(): HTMLButtonElement {
  const trigger = document.createElement('button')
  trigger.id = 'trigger'
  trigger.textContent = 'open'
  document.body.appendChild(trigger)
  return trigger
}

function mountOverlay(open: Ref<boolean>, fallback?: () => HTMLElement | null): void {
  const container = document.createElement('div')
  document.body.appendChild(container)
  app = createApp(overlayOf(open, fallback))
  app.mount(container)
}

describe('useFocusReturn', () => {
  it('关上时把焦点还给打开它的那颗按钮', async () => {
    const open = ref(false)
    const trigger = makeTrigger()
    trigger.focus()
    mountOverlay(open)

    open.value = true
    await nextTick()
    panel()?.focus()
    expect(document.activeElement).toBe(panel())

    open.value = false
    await settle()
    expect(document.activeElement).toBe(trigger)
  })

  it('打开它的元素已经不在 DOM 里时不还', async () => {
    const open = ref(false)
    const trigger = makeTrigger()
    trigger.focus()
    mountOverlay(open)

    open.value = true
    await nextTick()
    panel()?.focus()

    trigger.remove()
    open.value = false
    await settle()

    expect(document.activeElement).not.toBe(trigger)
  })

  it('打开它的元素没了时退到兜底容器', async () => {
    const open = ref(false)
    const trigger = makeTrigger()
    trigger.focus()
    const fallback = document.createElement('main')
    fallback.id = 'fallback'
    fallback.tabIndex = -1
    document.body.appendChild(fallback)
    mountOverlay(open, () => document.getElementById('fallback'))

    open.value = true
    await nextTick()
    panel()?.focus()

    trigger.remove()
    open.value = false
    await settle()

    expect(document.activeElement).toBe(fallback)
  })

  it('兜底容器也不在时不硬聚焦', async () => {
    const open = ref(false)
    const trigger = makeTrigger()
    trigger.focus()
    mountOverlay(open, () => document.getElementById('missing'))

    open.value = true
    await nextTick()
    panel()?.focus()

    trigger.remove()
    open.value = false
    await settle()

    // 没有可去的地方：不掉到某个猜出来的元素上，也不抛错。
    expect(document.activeElement).not.toBe(trigger)
  })

  it('从来就没打开过的浮层在挂载时不去抓兜底容器的焦点', async () => {
    // AdaptiveDialog / ConfirmDialog 是 `v-model` 驱动的，页面一渲染它们就挂上（关着）。
    // 这时的 `restore` 不该因为「没有打开它的那一处」就把焦点抓到 `#main-content` 上——
    // 那会在挂载的一瞬间把用户手里正拿着的焦点（比如刚点进某个输入框）挪走。
    const focused = document.createElement('input')
    document.body.appendChild(focused)
    focused.focus()
    const fallback = document.createElement('main')
    fallback.id = 'fallback'
    fallback.tabIndex = -1
    document.body.appendChild(fallback)

    const open = ref(false)
    mountOverlay(open, () => document.getElementById('fallback'))
    await settle()

    expect(document.activeElement).toBe(focused)
  })

  it('从来没打开过就卸载的浮层，卸载时也不去抓兜底容器的焦点', async () => {
    const focused = document.createElement('input')
    document.body.appendChild(focused)
    focused.focus()
    const fallback = document.createElement('main')
    fallback.id = 'fallback'
    fallback.tabIndex = -1
    document.body.appendChild(fallback)

    const open = ref(false)
    mountOverlay(open, () => document.getElementById('fallback'))
    app?.unmount()
    app = null
    await settle()

    expect(document.activeElement).toBe(focused)
  })

  it('开着的时候组件被卸载，也把焦点还回去', async () => {
    const open = ref(false)
    const trigger = makeTrigger()
    trigger.focus()
    mountOverlay(open)

    open.value = true
    await nextTick()
    panel()?.focus()

    app?.unmount()
    app = null
    await settle()

    expect(document.activeElement).toBe(trigger)
  })

  it('打开时焦点本来就在 body 上：不算有「打开它的那一处」，走兜底', async () => {
    // 打开浮层的那一下没把焦点交给任何元素（比如程序化地改 open，或者点的那处没聚焦）：
    // activeElement 是 body。「还焦点给 body」是空动作，还会盖掉别处在关掉时的收尾，
    // 所以这里当没有可还的那一处，直接退到兜底容器。
    const open = ref(false)
    makeTrigger()
    const fallback = document.createElement('main')
    fallback.id = 'fallback'
    fallback.tabIndex = -1
    document.body.appendChild(fallback)
    mountOverlay(open, () => document.getElementById('fallback'))

    expect(document.activeElement).toBe(document.body)

    open.value = true
    await nextTick()
    panel()?.focus()

    open.value = false
    await settle()

    expect(document.activeElement).toBe(fallback)
  })

  it('一直挂到关掉为止的浮层（open 一开始就是 true）在卸载时还焦点', async () => {
    // 设置浮层那种：按路由挂上，open 恒为 true，`onBeforeUnmount` 是唯一的「关上」。
    const trigger = makeTrigger()
    trigger.focus()
    const open = ref(true)
    mountOverlay(open)

    panel()?.focus()
    expect(document.activeElement).toBe(panel())

    app?.unmount()
    app = null
    await settle()

    expect(document.activeElement).toBe(trigger)
  })
})
