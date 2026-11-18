// 点「取消」是回答「不做」，不是一次失败。
//
// `confirm()` 早就把答案写好了（`onCancel: () => false`），但容器从前把它丢掉、改
// 成 reject 一个 `CancelError`：调用点写的是 `if (!confirmed) return`，于是那个没人
// 接的 reject 一路冒到场景的兜底上，整块内容换成「无法显示这块内容」。公告页点删除
// 再取消就是这么坏的。
//
// `prompt()` / `custom()` 没有 `onCancel`：它们仍然 reject，因为「取消了」和「交了
// 上来一个空值」是两回事。
import { defineComponent, h, nextTick } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import DialogContainer from './DialogContainer.vue'

import i18n, { setLocale } from '@/i18n'
import { CancelError, dialogs, useDialog } from '@/plugins/dialog'

const Page = defineComponent({ render: () => h(DialogContainer) })

const vuetify = createVuetify({ components, directives })

function mount() {
  return render(Page, { global: { plugins: [vuetify, i18n] } })
}

function button(text: string): HTMLElement | undefined {
  return Array.from(document.querySelectorAll<HTMLElement>('button')).find((b) => b.textContent?.trim() === text)
}

beforeEach(() => {
  // Vuetify 的浮层（v-dialog）会读 `visualViewport`，happy-dom 里没有。
  if (!('visualViewport' in window)) {
    Object.defineProperty(window, 'visualViewport', {
      configurable: true,
      value: {
        height: 800,
        width: 600,
        offsetTop: 0,
        offsetLeft: 0,
        scale: 1,
        addEventListener: () => {},
        removeEventListener: () => {},
      },
    })
  }
  setLocale('zh-CN')
})

afterEach(() => {
  dialogs.splice(0, dialogs.length)
  cleanup()
})

/** 先把确认框挂出来，再回来等它的两颗按钮。
 *
 *  返回值用 `{ answer }` 包一层：直接交回那个 promise 的话，`await open(…)` 会连它
 *  一起等，而它要等到点了按钮才有答案 —— 于是还没点就已经卡死了。 */
async function open(dialog: () => Promise<unknown>) {
  const answer = dialog()
  mount()
  await waitFor(() => expect(button('取消')).toBeDefined())
  return { answer }
}

describe('确认框的取消', () => {
  it('取消交回 false，不抛错', async () => {
    const dialog = useDialog()
    const { answer } = await open(() => dialog.confirm('要删掉吗？').wait())
    await fireEvent.click(button('取消')!)

    await expect(answer).resolves.toBe(false)
  })

  it('确认交回 true', async () => {
    const dialog = useDialog()
    const { answer } = await open(() => dialog.confirm('要删掉吗？').wait())
    await fireEvent.click(button('确定')!)

    await expect(answer).resolves.toBe(true)
  })

  it('提示框取消仍然抛 CancelError：「取消了」和「交了空值」分得开', async () => {
    const dialog = useDialog()
    const { answer } = await open(() => dialog.prompt('叫什么名字？').wait())
    await fireEvent.click(button('取消')!)

    await expect(answer).rejects.toBeInstanceOf(CancelError)
  })

  it('自定义弹窗取消仍然抛 CancelError', async () => {
    const dialog = useDialog()
    const { answer } = await open(() => dialog.custom('挑一个', () => h('div', '内容')).wait())
    await nextTick()
    await fireEvent.click(button('取消')!)

    await expect(answer).rejects.toBeInstanceOf(CancelError)
  })
})
