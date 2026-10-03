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
function overlayOf(open: Ref<boolean>) {
  return defineComponent({
    setup() {
      useFocusReturn(open)
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

function mountOverlay(open: Ref<boolean>): void {
  const container = document.createElement('div')
  document.body.appendChild(container)
  app = createApp(overlayOf(open))
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
